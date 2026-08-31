#!/usr/bin/env python3
"""Human tools for the SURE memory system (design spec section 9).

    python3 -s sure/runtime/memory/cli.py <command> ...

Every command works on ONE instance's sure/memory/ tree: by default the checkout this
file lives in, or the checkout named by --root. Only `export` writes into another checkout
(the clone named by its --repo-root, default --root). No command ever runs git: they print
the git command for the human to run.

Reads: index.json (rebuilt through index.py when missing or unreadable), meta/, usage/,
digests/, decisions.jsonl. Writes: meta/ and decisions.jsonl under paths.memory_lock
(decisions rows only through paths.decision_row / paths.append_decision, key "action");
moves under outbox/ and rejected/. Two commands write git-tracked references files:
`export`, and `supersede` (also `confirm` of a supersede candidate), which rewrites the
`Superseded-by:` header line of the old entry in every copy it has, the committed one
included, and prints the path to commit. meta.hook_trigger is never read or changed here
(publish computes it, the indexer copies it).

Locking rule: memory_lock does not nest, so this module holds the lock only around its
own meta / decisions writes and calls index.py / promote.py with the lock released
(they take it themselves). Every command re-reads the meta it is about to change inside
that lock hold (_locked_meta) and sets only its own fields: the catalogue at the top of a
command is read unlocked, so writing that copy back would revert a promote pass or another
operator's decision and log a second row for one real transition.

Only the standard library is imported, so `python3 -s` on the cluster works.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import shlex
import shutil
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # sure/runtime, so `python3 -s cli.py` finds `memory`

from memory import index as index_mod  # noqa: E402
from memory import paths, promote, usage  # noqa: E402

HEADER_PREFIXES = ("Trigger:", "Cell:", "Source:", "Added:", "Status:", "Superseded-by:")
STATUSES = ("provisional", "confirmed", "disputed", "superseded", "rejected")
INDEX_SCHEMA = "sure.memory.index.v1"
NOTE_LEGACY_NO_TRIGGER = "[legacy: no trigger, not injected]"
NOTE_NO_HOOK_TRIGGER = "[no hook trigger, not injected]"
NOTE_ORPHAN = "[orphan]"
NOTE_STALE = "[stale]"


class CliError(Exception):
    """Refusal with a human-readable message; main() prints it to stderr and exits 1."""


@dataclass
class Ctx:
    repo_root: Path
    memory_root: Path
    config: dict
    units: dict


# --- context, index, catalogue --------------------------------------------------

def _load_ctx(root: str | None) -> Ctx:
    repo_root = Path(root).resolve() if root else paths.LIB_DIR.parents[2]
    memory_root = paths.memory_root(repo_root)
    paths.ensure_memory_tree(memory_root)
    return Ctx(repo_root=repo_root, memory_root=memory_root, config=paths.load_config(), units=paths.load_units())


def _rebuild_index(ctx: Ctx) -> dict:
    """index.py owns index.json / index.md and takes the lock itself: call it with our lock released."""
    idx = index_mod.build_index(ctx.repo_root, config=ctx.config, units=ctx.units)
    index_mod.write_index(ctx.repo_root, idx, ctx.config)
    return idx


def _load_index(ctx: Ctx) -> dict:
    try:
        idx = paths.load_json(ctx.memory_root / "index.json")
    except (OSError, ValueError):
        return _rebuild_index(ctx)
    if not isinstance(idx, dict) or idx.get("schema") != INDEX_SCHEMA or not isinstance(idx.get("entries"), list):
        return _rebuild_index(ctx)
    return idx


def _index_rows(ctx: Ctx) -> dict[str, dict]:
    return {row["entry_id"]: row for row in _load_index(ctx)["entries"] if isinstance(row, dict) and isinstance(row.get("entry_id"), str)}


def _split_id(entry_id: str) -> tuple[str, str]:
    parts = paths.split_entry_id(entry_id)
    if parts is None:
        raise CliError(f"entry id must look like <target_skill>/<slug>, got {entry_id!r}")
    return parts


def _meta_path(ctx: Ctx, entry_id: str) -> Path:
    skill, slug = _split_id(entry_id)
    return ctx.memory_root / "meta" / skill / f"{slug}.json"


def _all_metas(ctx: Ctx) -> dict[str, dict]:
    out: dict[str, dict] = {}
    meta_dir = ctx.memory_root / "meta"
    for path in sorted(meta_dir.rglob("*.json")) if meta_dir.is_dir() else []:
        try:
            meta = paths.load_json(path)
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict) and isinstance(meta.get("entry_id"), str):
            out[meta["entry_id"]] = meta
    return out


def _meta_stub(view: dict) -> dict:
    """Meta for an entry that so far only exists in references (legacy / hand-committed): the same
    state-only shape as promote.legacy_meta (Task 7), which normally creates these at post_finish.

    created is the string "legacy" (this meta was rebuilt from a git-tracked file, not written by
    publish); entry_sha256 is None (nothing for the index to hash); hook_trigger and every
    candidate-derived key (component, cause, trigger, scope, op, target_entry, similar_entry,
    derived_from, fix_exercised, evidence_sha256) are deliberately absent (skeleton 1.7): the
    index parses those off the entry file itself, and promote._entry_op reads meta["op"] directly,
    so writing "op": "add" here would let a genuinely op-less legacy entry qualify for
    auto-promotion once it is later demoted to provisional. Key set must match
    promote.legacy_meta exactly (see test_meta_stub_key_set_matches_promote_legacy_meta)."""
    status = view.get("status") or "confirmed"
    return {
        "schema": "sure.memory.meta.v1",
        "entry_id": view["entry_id"], "type": view.get("type") or "bad_case", "status": status,
        "target_skill": view.get("target_skill"), "applies_to": [view.get("target_skill")],
        "created": "legacy",
        "confirmed": {"by": "human", "date": None} if status == "confirmed" else None,
        "exported": None, "superseded_by": view.get("superseded_by"), "superseded_at": None,
        "checked_at": view.get("checked_at"), "entry_sha256": None,
        "injections": 0, "useful_activated": 0, "useful_unattributed": 0, "useful_runs": [], "disputed": 0, "last_hit": None,
    }


def _write_meta(ctx: Ctx, meta: dict) -> None:
    """Caller holds the memory lock, and `meta` was read inside that hold (see _locked_meta)."""
    paths.atomic_write_json(_meta_path(ctx, meta["entry_id"]), meta)


def _read_meta(ctx: Ctx, entry_id: str) -> dict | None:
    """The entry's meta as it is on disk right now, or None when it is missing or unreadable."""
    try:
        meta = paths.load_json(_meta_path(ctx, entry_id))
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def _locked_meta(ctx: Ctx, view: dict, *, allowed: tuple[str, ...] | None = None, stub: bool = False) -> dict:
    """Caller holds the memory lock: re-read the meta and check the status inside that hold.

    _require_view / _catalogue read every meta without the lock at the top of a command, so by the
    time the command takes the lock a post_finish promote pass or another operator may have moved
    the entry. Writing that stale dict back reverts their transition and produces a second decisions
    row for one real change; commands therefore start from this fresh copy and set only their own
    fields. `allowed` is the status set the command needs; `stub` allows an entry that has no meta
    file yet (a legacy references entry)."""
    entry_id = view["entry_id"]
    meta = _read_meta(ctx, entry_id)
    if meta is None:
        if not stub:
            raise CliError(f"{entry_id} has no meta file; publish writes it, a hand-placed entry cannot be changed")
        meta = _meta_stub(view)
    status = meta.get("status") if isinstance(meta.get("status"), str) else view["status"]
    if allowed is not None and status not in allowed:
        raise CliError(f"{entry_id} is now {status}: another writer changed it while this command was running; re-read it and try again")
    return meta


def _append_decision(ctx: Ctx, action: str, entry_id: str, **extra: Any) -> None:
    """Caller holds the memory lock. Every human decision goes through paths.decision_row /
    paths.append_decision (skeleton 1.6): {"action", "entry_id", "by": "human", "at", ...extra}."""
    paths.append_decision(ctx.memory_root, paths.decision_row(action, entry_id, "human", **extra))


def _view(entry_id: str, row: dict | None, meta: dict | None) -> dict:
    """One merged read model per entry: meta is the state store, the index row adds title / path / legacy."""
    row = row or {}
    meta = meta or {}
    created = meta.get("created") if isinstance(meta.get("created"), dict) else {}

    def pick(key: str, default: Any = None) -> Any:
        if key in meta:
            return meta[key]
        return row.get(key, default)

    return {
        "entry_id": entry_id,
        "type": pick("type") or "bad_case",
        "status": pick("status") or "confirmed",
        "target_skill": pick("target_skill") or entry_id.split("/", 1)[0],
        "component": pick("component"),
        "cause": pick("cause"),
        "trigger": pick("trigger") or [],
        "hook_trigger": pick("hook_trigger"),  # None when neither meta nor the index row carries it
        "scope": pick("scope"),
        "checked_at": pick("checked_at"),
        "legacy": bool(row.get("legacy")),
        "title": row.get("title") or "",
        "path": row.get("path") or "",
        "op": pick("op") or "add",
        "target_entry": pick("target_entry"),
        "similar_entry": pick("similar_entry"),
        "created_date": created.get("date"),
        "created_run": created.get("run_id"),
        "exported": meta.get("exported"),
        "derived_from": meta.get("derived_from") or [],
        "superseded_by": pick("superseded_by"),
        "meta": meta or None,
        "row": row or None,
    }


def _catalogue(ctx: Ctx) -> dict[str, dict]:
    """All entries the human may name: every meta (any status) plus index rows without meta (legacy)."""
    rows = _index_rows(ctx)
    metas = _all_metas(ctx)
    views: dict[str, dict] = {}
    for entry_id in sorted(set(rows) | set(metas)):
        views[entry_id] = _view(entry_id, rows.get(entry_id), metas.get(entry_id))
    return views


def _require_view(ctx: Ctx, entry_id: str) -> dict:
    _split_id(entry_id)
    view = _catalogue(ctx).get(entry_id)
    if view is None:
        raise CliError(f"unknown entry {entry_id} (not in index.json and no meta file)")
    return view


def _cell(view: dict) -> str:
    return f"{view['target_skill']}/{view.get('component') or '_'} x {view.get('cause') or '-'}"


def _stale(view: dict, config: dict) -> bool | None:
    """Tri-state like projectmem: True stale, False fresh, None cannot judge (not a fact / no date / unknown scope)."""
    if view["type"] != "fact":
        return None
    checked = view.get("checked_at")
    if not isinstance(checked, str):
        return None
    scope = view.get("scope") or "cluster"
    kind = scope.split(":", 1)[0]
    limit = config["stale_after_days"].get(kind)
    if limit is None:
        return None
    try:
        checked_day = date.fromisoformat(checked[:10])
    except ValueError:
        return None
    return (date.fromisoformat(paths.utc_today()) - checked_day).days > limit


def _orphan(view: dict, views: dict[str, dict]) -> bool:
    """A modify / supersede candidate whose target is gone or rejected (section 6.2)."""
    if view["op"] not in ("modify", "supersede") or not view.get("target_entry"):
        return False
    target = views.get(view["target_entry"])
    return target is None or target["status"] == "rejected"


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


# --- entry files ---------------------------------------------------------------

def _entry_dir(ctx: Ctx, layer: str, entry_id: str) -> Path:
    skill, slug = _split_id(entry_id)
    return ctx.memory_root / layer / skill / slug


def _entry_file(ctx: Ctx, view: dict) -> Path:
    """First existing copy: index path (references or provisional), then provisional / outbox / rejected."""
    candidates: list[Path] = []
    if view.get("path"):
        candidates.append(ctx.repo_root / view["path"])
    for layer in ("provisional", "outbox", "rejected"):
        candidates.append(_entry_dir(ctx, layer, view["entry_id"]) / "entry.md")
    for path in candidates:
        if path.is_file():
            return path
    raise CliError(f"no entry file found for {view['entry_id']}")


def _read_text(path: Path) -> str:
    """For reading only (show, compare): read_text applies universal newlines, so a CRLF checkout
    and an LF one produce the same string and a diff of the two is about content, not endings."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise CliError(f"cannot read {path}: not valid UTF-8 ({exc})") from exc


def _read_source_text(path: Path) -> str:
    """For text that is rewritten and written back: the bytes decoded as UTF-8, newlines untouched.
    promote.keep_line_endings can only restore an ending it was shown, so every rewrite path reads
    through here; _read_text would flatten a CRLF working tree before any header edit happened."""
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CliError(f"cannot read {path}: not valid UTF-8 ({exc})") from exc


def _proposal(ctx: Ctx, entry_id: str) -> tuple[Path, dict] | None:
    for layer in ("provisional", "outbox", "rejected"):
        path = _entry_dir(ctx, layer, entry_id) / "proposal.json"
        if path.is_file():
            try:
                obj = paths.load_json(path)
            except (OSError, ValueError) as exc:
                raise CliError(f"proposal.json for {entry_id} is unreadable: {exc}") from exc
            if isinstance(obj, dict):
                return path, obj
    return None


def _references_file(ctx: Ctx, view: dict) -> Path | None:
    """The git-tracked copy under sure/skills/, when the index says the entry lives there."""
    path = view.get("path") or ""
    if path.startswith("sure/skills/") and (ctx.repo_root / path).is_file():
        return ctx.repo_root / path
    return None


def _split_header(text: str) -> tuple[list[str], str]:
    """Leading provenance lines (section 5.1) and the rest of the file (starts at the blank line or H1).
    Blank lines before the block are skipped, as index.parse_header and promote.split_header do."""
    lines = text.splitlines()
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    n = start
    while n < len(lines) and lines[n].startswith(HEADER_PREFIXES):
        n += 1
    if n == start:
        return [], "\n".join(lines).lstrip("\n")  # headerless: the whole file is the body
    return lines[start:n], "\n".join(lines[n:]).lstrip("\n")


def _join(header: list[str], body: str) -> str:
    return ("\n".join(header) + "\n\n" if header else "") + body.rstrip("\n") + "\n"


def _with_status(text: str, status: str) -> str:
    """Delegates to promote.with_status: one parser for the Status: header line, not two. cli's own
    line-replace-only version silently dropped the line on a header that has no existing Status:
    line (e.g. a legacy entry that so far only picked up a Superseded-by: line)."""
    return promote.with_status(text, status)


def _with_superseded_by(text: str, new_id: str, today: str) -> str:
    """Append the sixth header line once; a headerless legacy file gets it as its first line.
    Line endings follow the input, as promote.with_superseded_by does: this text is staged in
    outbox and `export` writes it into another clone, where a flattened file is a whole-file diff."""
    header, body = _split_header(text)
    if any(line.startswith("Superseded-by:") and new_id in line for line in header):
        return text
    header.append(f"Superseded-by: {new_id} ({today})")
    return promote.keep_line_endings(text, _join(header, body))


def _with_body_of(target_text: str, candidate_text: str) -> str:
    """modify: keep the target's provenance header, take the candidate's body (its H1 included).
    The target's line endings are part of what is kept (promote.keep_line_endings)."""
    header, _ = _split_header(target_text)
    _, body = _split_header(candidate_text)
    return promote.keep_line_endings(target_text, _join(header, body))


# --- usage / digests -----------------------------------------------------------

def _usage_rows(ctx: Ctx) -> tuple[list[dict], int]:
    # load_all_usage, not a glob of our own: it skips the byte prefix the archive already
    # folded, so a run pruned mid-flight is counted once here as it is in replay_usage.
    got, skipped = usage.load_all_usage(ctx.memory_root)
    rows = [r for r in got if isinstance(r.get("kind"), str)]
    rows.sort(key=lambda r: str(r.get("at") or ""))
    return rows, skipped


def _row_entry_ids(row: dict) -> list[str]:
    if row.get("kind") in ("inject", "pre_start"):
        return [e.get("entry_id") for e in row.get("entries") or [] if isinstance(e, dict) and isinstance(e.get("entry_id"), str)]
    if row.get("kind") == "settle" and isinstance(row.get("entry_id"), str):
        return [row["entry_id"]]
    return []


def _digests(ctx: Ctx) -> list[dict]:
    out: list[dict] = []
    digests_dir = ctx.memory_root / "digests"
    for path in sorted(digests_dir.glob("*.json")) if digests_dir.is_dir() else []:
        try:
            obj = paths.load_json(path)
        except (OSError, ValueError):
            continue
        if isinstance(obj, dict) and isinstance(obj.get("units"), list) and isinstance(obj.get("run"), dict):
            out.append(obj)
    out.sort(key=lambda d: str(d["run"].get("run_id") or ""))
    return out


def _refresh_meta_counts(ctx: Ctx) -> int:
    """Section 6.3: meta counts are replayed from usage by promote (post_finish) and by cli stats / rebuild-index."""
    counts = usage.replay_usage(ctx.memory_root)
    changed = 0
    with paths.memory_lock(ctx.memory_root):
        for entry_id, meta in _all_metas(ctx).items():
            c = counts.get(entry_id)
            fresh = {
                "injections": c.injections if c else 0,
                "useful_activated": c.useful_activated if c else 0,
                "useful_unattributed": c.useful_unattributed if c else 0,
                "useful_runs": list(c.useful_runs) if c else [],
                "disputed": c.disputed if c else 0,
                "last_hit": c.last_hit if c else None,
            }
            if any(meta.get(k) != v for k, v in fresh.items()):
                meta.update(fresh)
                _write_meta(ctx, meta)
                changed += 1
    return changed


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    fmt = "  ".join("{:<%d}" % w for w in widths)
    print(fmt.format(*headers).rstrip())
    for row in rows:
        print(fmt.format(*row).rstrip())


# --- list ----------------------------------------------------------------------

def cmd_list(ctx: Ctx, args: argparse.Namespace) -> int:
    if args.days is not None and not args.cold:
        raise CliError("--days only makes sense together with --cold")
    views = _catalogue(ctx)
    counts = usage.replay_usage(ctx.memory_root)
    wanted_status = {s for arg in (args.status or []) for s in arg.split(",") if s}
    wanted_skill = {s for arg in (args.skill or []) for s in arg.split(",") if s}
    today = date.fromisoformat(paths.utc_today())
    rows: list[list[str]] = []
    for entry_id, view in views.items():
        c = counts.get(entry_id)
        if wanted_status and view["status"] not in wanted_status:
            continue
        if wanted_skill and view["target_skill"] not in wanted_skill:
            continue
        if args.cold:
            if c and c.injections:
                continue
            if args.days is not None:
                created = view.get("created_date")
                if not isinstance(created, str):
                    continue  # legacy / unknown creation date cannot be judged
                try:
                    age = (today - date.fromisoformat(created[:10])).days
                except ValueError:
                    continue
                if age < args.days:
                    continue
        notes: list[str] = []
        if view["type"] == "fact":
            stale = _stale(view, ctx.config)
            notes.append(f"checked_at={view.get('checked_at') or '-'}")
            if stale:
                notes.append(NOTE_STALE)
        if view["legacy"] and not view["trigger"]:
            notes.append(NOTE_LEGACY_NO_TRIGGER)
        elif index_mod.never_injected(view):
            # The only signal that an entry which looks healthy here is never handed to a gate:
            # an empty hook_trigger, or a bad_case cell whose component is `_` (no unit is named `_`).
            notes.append(NOTE_NO_HOOK_TRIGGER)
        if _orphan(view, views):
            notes.append(NOTE_ORPHAN)
        if view["op"] in ("modify", "supersede") and view.get("target_entry"):
            notes.append(f"{view['op']}->{view['target_entry']}")
        rows.append([
            entry_id, view["type"], view["status"], _cell(view),
            f"{c.useful_activated if c else 0}/{c.useful_unattributed if c else 0}",
            str(c.disputed if c else 0), str(c.injections if c else 0),
            (c.last_hit or "-") if c else "-",
            view.get("created_date") or ("legacy" if view["legacy"] else "-"),
            " ".join(notes),
        ])
    _print_table(["entry_id", "type", "status", "cell", "useful(act/unattr)", "disputed", "inject", "last_hit", "added", "note"], rows)
    print(f"{len(rows)} entries")
    return 0


# --- show ----------------------------------------------------------------------

def cmd_show(ctx: Ctx, args: argparse.Namespace) -> int:
    view = _require_view(ctx, args.entry_id)
    entry_path = _entry_file(ctx, view)
    print(f"== entry {view['entry_id']} ({_rel(entry_path, ctx.repo_root)}) ==")
    text = _read_text(entry_path)
    if view["meta"] is not None:
        # meta is the state authority (index._apply_meta), and promote / confirm stamp the Status:
        # header into the outbox copy only, so the file here keeps its publish-time line. Shown as
        # is it would contradict the meta printed right below; the file on disk is not touched.
        text = _with_status(text, view["status"])
    print(text.rstrip("\n"))
    print("== meta ==")
    if view["meta"] is None:
        print("no meta file (legacy entry: counts start when it is first injected)")
    else:
        print(json.dumps(view["meta"], indent=2, ensure_ascii=False))
    print("== proposal ==")
    found = _proposal(ctx, view["entry_id"])
    if found is None:
        print("no proposal.json (legacy or exported entry)")
    else:
        print(json.dumps(found[1], indent=2, ensure_ascii=False))
    print("== usage ==")
    rows, _ = _usage_rows(ctx)
    hits = [r for r in rows if view["entry_id"] in _row_entry_ids(r)]
    if not hits:
        print("never injected")
    for r in hits:
        if r["kind"] == "settle":
            print(f"settle    {r.get('run_id')}  {r.get('unit')}  {r.get('outcome')}  {r.get('at')}")
        elif r["kind"] == "inject":
            print(f"inject    {r.get('run_id')}  {r.get('unit')}  attempt {r.get('attempt')}  {r.get('at')}")
        else:
            print(f"pre_start {r.get('run_id')}  {r.get('at')}")
    print("== descendants ==")
    descendants = _descendants(ctx, view["entry_id"])
    if not descendants:
        print("none")
    for line in descendants:
        print(line)
    print("== evidence ==")
    evidence = (view["meta"] or {}).get("evidence_sha256") or {}
    if not evidence:
        print("none recorded")
    run_dir = ctx.repo_root / ".sure" / "runs" / str(view.get("created_run") or "")
    for rel, sha in sorted(evidence.items()):
        path = run_dir / rel
        if not path.is_file():
            state = "missing"
        elif paths.sha256_file(path) != sha:
            state = "changed"
        else:
            state = "ok"
        print(f"{state:<8} {rel}")
    return 0


def _descendants(ctx: Ctx, entry_id: str) -> list[str]:
    """derived_from back-references plus modify / supersede candidates that target this entry."""
    out: list[str] = []
    for other_id, view in _catalogue(ctx).items():
        if other_id == entry_id:
            continue
        if entry_id in view["derived_from"]:
            out.append(f"{other_id}  (derived_from)")
        if view.get("target_entry") == entry_id and view["op"] in ("modify", "supersede"):
            out.append(f"{other_id}  ({view['op']} candidate, status {view['status']})")
    return out


# --- compare -------------------------------------------------------------------

def cmd_compare(ctx: Ctx, args: argparse.Namespace) -> int:
    view = _require_view(ctx, args.entry_id)
    found = _proposal(ctx, view["entry_id"])
    proposal = found[1] if found else {}
    other_id: str | None = None
    if view["op"] in ("modify", "supersede"):
        other_id = view.get("target_entry") or proposal.get("target_entry")
    else:
        similar = proposal.get("similar") if isinstance(proposal.get("similar"), dict) else {}
        other_id = view.get("similar_entry") or similar.get("entry")
    if not isinstance(other_id, str) or not other_id:
        raise CliError(f"{view['entry_id']} names no target_entry or similar.entry to compare with")
    other = _require_view(ctx, other_id)
    other_text = _read_text(_entry_file(ctx, other))
    my_text = _read_text(_entry_file(ctx, view))
    print(f"comparing {other_id} (existing) with {view['entry_id']} ({view['op']} candidate)")
    # difflib.unified_diff, as the old adopt_memory.cmd_compare did.
    sys.stdout.writelines(difflib.unified_diff(
        other_text.splitlines(keepends=True), my_text.splitlines(keepends=True),
        fromfile=other_id, tofile=view["entry_id"],
    ))
    if other_text == my_text:
        print("(identical)")
    return 0


# --- confirm -------------------------------------------------------------------

def _copy_to_outbox(ctx: Ctx, entry_id: str, text: str, proposal_path: Path | None) -> Path:
    outbox = _entry_dir(ctx, "outbox", entry_id)
    paths.atomic_write_text(outbox / "entry.md", text)
    if proposal_path is not None and proposal_path.is_file():
        paths.atomic_write_bytes(outbox / "proposal.json", proposal_path.read_bytes())
    return outbox


def cmd_confirm(ctx: Ctx, args: argparse.Namespace) -> int:
    view = _require_view(ctx, args.entry_id)
    entry_id = view["entry_id"]
    if view["status"] not in ("provisional", "disputed"):
        raise CliError(f"{entry_id} is {view['status']}; only provisional or disputed entries can be confirmed")
    prov_md = _entry_dir(ctx, "provisional", entry_id) / "entry.md"
    if not prov_md.is_file():
        raise CliError(f"{entry_id} has no provisional/ copy ({_rel(prov_md, ctx.repo_root)})")
    if view["meta"] is None:
        raise CliError(f"{entry_id} has no meta file; publish writes it, a hand-placed entry cannot be confirmed")
    text = _read_source_text(prov_md)
    found = _proposal(ctx, entry_id)
    proposal_path = found[0] if found else None
    today = paths.utc_today()
    op = view["op"]
    target_id = view.get("target_entry")
    target = None
    if op in ("modify", "supersede"):
        if not isinstance(target_id, str):
            raise CliError(f"{entry_id} is a {op} candidate without target_entry")
        target = _require_view(ctx, target_id)
        if target["status"] in ("rejected", "superseded"):
            raise CliError(f"target {target_id} is {target['status']}; reject or re-target this candidate")

    with paths.memory_lock(ctx.memory_root):
        meta = _locked_meta(ctx, view, allowed=("provisional", "disputed"))
        meta["confirmed"] = {"by": "human", "date": today}
        extra: dict[str, Any] = {"reason": args.reason, "op": op}
        if op == "modify":
            assert target is not None
            # Says where the body went, which is not always the target itself: a tracked target is
            # only staged, so calling that "applied" is the wording that made a discarded merge read
            # as a success. The decisions row still records applied_to either way.
            landed = _apply_modify(ctx, target, text)
            # The candidate's content now lives in the target: keep the candidate out of matching.
            meta["status"] = "superseded"
            meta["superseded_by"] = target_id
            meta["superseded_at"] = today
            extra["applied_to"] = target_id
        else:
            meta["status"] = "confirmed"
            # Status: confirmed in the staged copy, the same line auto-promotion writes
            # (promote._promote); a human reading outbox/ must not see a confirmed entry as provisional.
            outbox = _copy_to_outbox(ctx, entry_id, _with_status(text, "confirmed"), proposal_path)
            landed = _rel(outbox, ctx.repo_root)
        _write_meta(ctx, meta)  # hook_trigger and every other publish-time key ride along unchanged
        _append_decision(ctx, "confirm", entry_id, **extra)

    if op == "supersede":
        assert target is not None and isinstance(target_id, str)
        _supersede(ctx, target, entry_id, today)
    _rebuild_index(ctx)
    print(f"confirmed {entry_id} ({op}) -> {landed}")
    if op != "modify":
        print(f"next: python3 -s sure/runtime/memory/cli.py export {entry_id} --repo-root <your clone>")
    return 0


def _apply_modify(ctx: Ctx, target: dict, candidate_text: str) -> str:
    """Caller holds the lock; returns the line cmd_confirm prints for where the body went.

    The merge lands in the copy the indexer reads, which is what decides the precedence here:
    index._build_once takes the git-tracked references file whenever the target has one and only
    falls back to provisional/<skill>/<slug>/entry.md when it has none. An exported entry has both
    (export deletes the outbox copy, not the provisional one), so a merge written to the provisional
    copy of such a target is written where nothing will ever read it.

    Tracked target: stage the merged file in outbox, never rewrite the tracked file in place - only
    `export` and `supersede` write git-tracked files (module docstring), so the revision goes live
    when the human exports it. Provisional target: rewrite in place, which is live immediately.

    Every other copy of the target on disk takes the new body too, as promote.apply_supersede does
    with its header line, because the copy the index passes over is not dead weight: cmd_confirm and
    promote._promote both re-stage an entry from its provisional copy after a demotion, and export
    then writes that text over the tracked file. A copy left holding the pre-merge body therefore
    does not just sit there, it reverts the entry the next time the entry is confirmed. Each copy
    keeps its own header (_with_body_of takes the body only), so the outbox copy stays confirmed."""
    target_id = target["entry_id"]
    live = ("provisional", "confirmed", "disputed")  # the pre-lock check refused rejected / superseded
    tracked = _references_file(ctx, target)
    prov_md = _entry_dir(ctx, "provisional", target_id) / "entry.md"
    outbox_md = _entry_dir(ctx, "outbox", target_id) / "entry.md"
    if tracked is None and not prov_md.is_file():
        raise CliError(f"target {target_id} has neither a provisional copy nor a references file")

    def merge(path: Path) -> str:
        return _with_body_of(_read_source_text(path), candidate_text)

    target_meta = _locked_meta(ctx, target, allowed=live, stub=True)
    if tracked is not None:
        _copy_to_outbox(ctx, target_id, merge(tracked), None)  # overwrites any outbox copy of its own
        target_meta["exported"] = None  # an already-exported target: export must not refuse the re-export
    elif outbox_md.is_file():
        paths.atomic_write_text(outbox_md, merge(outbox_md))
    if prov_md.is_file():
        paths.atomic_write_text(prov_md, merge(prov_md))
        # A rewritten provisional entry.md must carry a fresh meta.entry_sha256, or the indexer
        # (Task 5) treats the mismatch as tampering and drops the target from index.json.
        target_meta["entry_sha256"] = paths.sha256_file(prov_md)
    _write_meta(ctx, target_meta)
    if tracked is None:
        return f"body applied to {target_id}"
    print(f"staged modified {target_id} in outbox; export it to update the tracked file")
    return f"body staged in outbox for {target_id}"


def _supersede(ctx: Ctx, old: dict, new_id: str, today: str) -> None:
    """Lock released here: promote.apply_supersede takes it. It appends the Superseded-by header to every
    copy of the old entry it finds (references / provisional / outbox), refreshes meta.entry_sha256 when the
    provisional copy was rewritten, and appends the decisions "supersede" row itself (by="human" here).

    The references copy is git-tracked, so this checkout now has an uncommitted change: the header has to
    reach the committed file or a clone keeps routing to a superseded entry. The path is printed for the
    human to commit (this module never runs git), and the same text is staged in outbox so
    `export --repo-root <clone>` can carry it to another checkout."""
    old_id = old["entry_id"]
    if old["meta"] is None:
        with paths.memory_lock(ctx.memory_root):
            if _read_meta(ctx, old_id) is None:  # still none inside the lock: nobody wrote one meanwhile
                _write_meta(ctx, _meta_stub(old))
    promote.apply_supersede(ctx.repo_root, old_id, new_id, by="human")
    tracked = _references_file(ctx, old)
    if tracked is not None:
        staged = _with_superseded_by(_read_source_text(tracked), new_id, today)
        _copy_to_outbox(ctx, old_id, staged, None)
        print(f"rewrote the tracked file {_rel(tracked, ctx.repo_root)} with its Superseded-by header; commit it in this checkout")
        _print_git_suggestion(ctx.repo_root, [tracked], f"memory({old['target_skill']}): supersede {old_id.split('/', 1)[1]}")
        print(f"the same text is staged in outbox; export it to carry the header to another clone")


# --- export --------------------------------------------------------------------

def _contained(dest: Path, root: Path) -> bool:
    """resolve() + startswith(root + sep), following anthropic-sdk-python _validate_path (MIT)."""
    resolved_dest = dest.resolve()
    resolved_root = root.resolve()
    return resolved_dest != resolved_root and str(resolved_dest).startswith(str(resolved_root) + os.sep)


def _export_destination(ctx: Ctx, clone: Path, view: dict) -> Path:
    skill, slug = _split_id(view["entry_id"])
    skills_root = clone / "sure" / "skills"
    if view["type"] == "fact":
        # Same clone check as the bad_case branch: a mistyped --repo-root would otherwise get a
        # facts/ tree created under it, and cmd_export would then delete the outbox copy.
        if not (skills_root / "_shared").is_dir():
            raise CliError(f"{_rel(skills_root / '_shared', clone)} is not a directory in {clone}; is --repo-root a SURE clone?")
        dest = skills_root / "_shared" / "memory" / "facts" / f"{slug}.md"
    else:
        if skill not in ctx.config["target_skills"] or skill == "_shared":
            raise CliError(f"bad_case with target_skill {skill!r} has no references destination")
        if not (skills_root / skill).is_dir():
            raise CliError(f"{_rel(skills_root / skill, clone)} is not a directory in {clone}; is --repo-root a SURE clone?")
        dest = skills_root / skill / "references" / "memory" / "bad_cases" / f"{slug}.md"
    if not _contained(dest, skills_root):
        raise CliError(f"destination {dest} escapes {skills_root}")
    return dest


def _records_in(bad_cases_dir: Path, skill: str, repo_root: Path) -> list:
    """EntryRecords for every entry file in a references bad_cases/ dir (README excluded), for reconcile_readme."""
    records = []
    for p in sorted(bad_cases_dir.glob("*.md")):
        if p.name == "README.md":
            continue
        record = index_mod.parse_entry_file(p, target_skill=skill, legacy_dir=True)
        if not getattr(record, "path", None):
            record.path = _rel(p, repo_root)
        records.append(record)
    return records


def cmd_export(ctx: Ctx, args: argparse.Namespace) -> int:
    view = _require_view(ctx, args.entry_id)
    entry_id = view["entry_id"]
    clone = Path(args.repo_root).resolve() if args.repo_root else ctx.repo_root
    if view["status"] not in ("confirmed", "superseded"):
        raise CliError(f"{entry_id} is {view['status']}; only confirmed entries (or superseded ones staged for re-export) can be exported")
    outbox_md = _entry_dir(ctx, "outbox", entry_id) / "entry.md"
    if not outbox_md.is_file():
        exported = view.get("exported")
        hint = f" (already exported on {exported})" if exported else ""
        raise CliError(f"nothing in outbox for {entry_id}{hint}")
    dest = _export_destination(ctx, clone, view)
    written = [dest]
    with paths.memory_lock(ctx.memory_root):
        # The status check above ran on the unlocked catalogue copy: re-read it here, or an entry
        # another operator rejected (or a promote pass demoted) meanwhile is written into the clone
        # as confirmed and its meta reverted to match.
        meta = _locked_meta(ctx, view, allowed=("confirmed", "superseded"), stub=True)
        if not outbox_md.is_file():
            raise CliError(f"nothing in outbox for {entry_id} any more; another writer removed it")
        text = _with_status(_read_source_text(outbox_md), "confirmed")
        paths.atomic_write_text(dest, text)
        # reconcile_readme does not lock by itself (Task 5): README edits and the meta write share one lock hold.
        if view["type"] == "bad_case":
            readme = dest.parent / "README.md"
            if index_mod.reconcile_readme(readme, _records_in(dest.parent, view["target_skill"], clone)):
                written.append(readme)
        meta["exported"] = paths.utc_today()  # no decisions row for export (not in DECISION_ACTIONS); meta is the audit trail
        _write_meta(ctx, meta)
    if view["type"] == "fact":
        print(f"fact exported; add a line for it to {_rel(dest.parent / 'README.md', clone)} by hand (that index is not machine-reconciled)")
    shutil.rmtree(outbox_md.parent, ignore_errors=True)
    _rebuild_index(ctx)
    for path in written:
        print(f"wrote {_rel(path, clone)}")
    _print_git_suggestion(clone, written, f"memory({view['target_skill']}): export {entry_id.split('/', 1)[1]}")
    return 0


def _print_git_suggestion(clone: Path, files: list[Path], message: str) -> None:
    # Adapted from the old adopt_memory._print_git_suggestion: every word shell-quoted because entry
    # names are agent-authored; the command is printed, never executed (this module does not import subprocess).
    rels = " ".join(shlex.quote(_rel(p, clone)) for p in files)
    git = f"git -C {shlex.quote(str(clone))}"
    print("suggested commit (run it yourself; this tool never runs git):")
    print(f"  {git} add {rels} && {git} commit -m {shlex.quote(message)}")


# --- reject --------------------------------------------------------------------

def _playbook_referrers(ctx: Ctx, view: dict, entry_file: Path) -> list[str]:
    """Other reference docs of the skill that mention this entry's file name (README route table excluded)."""
    skill = view["target_skill"]
    refs = ctx.repo_root / "sure" / "skills" / skill / "references"
    if not refs.is_dir():
        return []
    needle = entry_file.name
    hits: list[str] = []
    for path in sorted(refs.rglob("*.md")):
        if path == entry_file or (path.name == "README.md" and path.parent.name == "bad_cases"):
            continue
        try:
            if needle in path.read_text(encoding="utf-8", errors="replace"):
                hits.append(_rel(path, ctx.repo_root))
        except OSError:
            continue
    return hits


def _mark_orphans(ctx: Ctx, rejected_id: str) -> list[str]:
    """meta.orphan of every modify / supersede candidate that pointed at the entry just rejected
    (section 6.2). publish.mark_orphans takes the memory lock itself, so this runs with ours
    released; publish.py is imported here and not at module level because it pulls proposals.py in,
    which no other cli command needs."""
    from memory import publish  # noqa: PLC0415

    return publish.mark_orphans(ctx.repo_root, rejected_id)


def cmd_reject(ctx: Ctx, args: argparse.Namespace) -> int:
    view = _require_view(ctx, args.entry_id)
    entry_id = view["entry_id"]
    if view["status"] == "rejected":
        raise CliError(f"{entry_id} is already rejected")
    tracked = _references_file(ctx, view)
    if tracked is not None:
        referrers = _playbook_referrers(ctx, view, tracked)
        if referrers:
            print(f"warning: {entry_id} is referenced by other reference docs: {', '.join(referrers)}; fix those docs by hand", file=sys.stderr)
    prov = _entry_dir(ctx, "provisional", entry_id)
    outbox = _entry_dir(ctx, "outbox", entry_id)
    dest = _entry_dir(ctx, "rejected", entry_id)
    if prov.is_dir() and dest.exists():
        raise CliError(f"rejected destination already exists: {_rel(dest, ctx.repo_root)}")
    with paths.memory_lock(ctx.memory_root):
        meta = _locked_meta(ctx, view, allowed=("provisional", "confirmed", "disputed", "superseded"), stub=True)
        if prov.is_dir():
            paths.ensure_dir(dest.parent)
            paths.atomic_replace(prov, dest)
            paths.group_writable(dest)
        if outbox.is_dir():
            shutil.rmtree(outbox, ignore_errors=True)
        meta["status"] = "rejected"
        _write_meta(ctx, meta)
        _append_decision(ctx, "reject", entry_id, reason=args.reason)
    orphaned = _mark_orphans(ctx, entry_id)  # lock released: mark_orphans takes it itself
    _rebuild_index(ctx)
    print(f"rejected {entry_id}" + (f" -> {_rel(dest, ctx.repo_root)}" if dest.is_dir() else " (meta only)"))
    if orphaned:
        print(f"marked orphan: {', '.join(orphaned)}")
    if tracked is not None:
        print(f"already exported: delete {_rel(tracked, ctx.repo_root)} in your clone and commit it (this tool never touches tracked files)")
    descendants = _descendants(ctx, entry_id)
    if descendants:
        print("descendants (not cascaded, decide each one yourself):")
        for line in descendants:
            print(f"  {line}")
    return 0


# --- supersede -----------------------------------------------------------------

def cmd_supersede(ctx: Ctx, args: argparse.Namespace) -> int:
    old = _require_view(ctx, args.old_id)
    new = _require_view(ctx, args.new_id)
    if old["entry_id"] == new["entry_id"]:
        raise CliError("an entry cannot supersede itself")
    if old["status"] in ("rejected", "superseded"):
        raise CliError(f"{old['entry_id']} is already {old['status']}")
    _supersede(ctx, old, new["entry_id"], paths.utc_today())
    _rebuild_index(ctx)
    print(f"superseded {old['entry_id']} by {new['entry_id']}")
    return 0


# --- stats ---------------------------------------------------------------------

def _count_rows(rows: list[dict], seed: dict[str, dict] | None = None) -> dict[str, dict]:
    """Per-entry counts over an already filtered row list (stats --since needs its own window).
    `seed` starts the totals off the usage archive; the rows on disk add on top of it."""
    out: dict[str, dict] = seed or {}
    for r in rows:
        for entry_id in _row_entry_ids(r):
            c = out.setdefault(entry_id, {"inject": 0, "pre_start": 0, "useful_activated": 0, "useful_unattributed": 0, "disputed": 0, "abandoned": 0, "settles": 0, "last_hit": None})
            if r["kind"] == "inject":
                c["inject"] += 1
            elif r["kind"] == "pre_start":
                c["pre_start"] += 1
            elif r["kind"] == "settle":
                c["settles"] += 1
                if r.get("outcome") in ("useful_activated", "useful_unattributed", "disputed", "abandoned"):
                    c[r["outcome"]] += 1
            at = r.get("at")
            if isinstance(at, str) and (c["last_hit"] is None or at > c["last_hit"]):
                c["last_hit"] = at
    return out


def cmd_stats(ctx: Ctx, args: argparse.Namespace) -> int:
    since = args.since
    if since is not None:
        try:
            date.fromisoformat(since)
        except ValueError as exc:
            raise CliError(f"--since must be YYYY-MM-DD, got {since!r}") from exc
    refreshed = _refresh_meta_counts(ctx)
    views = _catalogue(ctx)
    if args.skill:
        views = {k: v for k, v in views.items() if v["target_skill"] == args.skill}
    rows, skipped = _usage_rows(ctx)
    digests = _digests(ctx)
    if since is not None:
        rows = [r for r in rows if str(r.get("at") or "")[:10] >= since]
        digests = [d for d in digests if str(d["run"].get("run_id") or "")[:8] >= since.replace("-", "")]
    if args.skill:
        # rows stay unfiltered: they are keyed by entry id, and the views filter above already scopes the table
        digests = [d for d in digests if d["run"].get("skill") == args.skill]

    print(f"meta refreshed from usage: {refreshed} file(s) updated" + (f"; {skipped} bad usage line(s) skipped" if skipped else ""))
    print("== entries ==")
    # A window cannot include the archive: folded counts are totals, with no per-row dates left to filter on.
    counts = _count_rows(rows, None if since is not None else {
        entry_id: {"inject": c.injections, "pre_start": 0, "useful_activated": c.useful_activated,
                   "useful_unattributed": c.useful_unattributed, "disputed": c.disputed, "abandoned": 0,
                   "settles": c.useful_activated + c.useful_unattributed + c.disputed, "last_hit": c.last_hit}
        for entry_id, c in usage.load_archive(ctx.memory_root).entries.items()})
    table: list[list[str]] = []
    for entry_id, view in views.items():
        c = counts.get(entry_id, {"inject": 0, "pre_start": 0, "useful_activated": 0, "useful_unattributed": 0, "disputed": 0, "abandoned": 0, "settles": 0, "last_hit": None})
        note = NOTE_LEGACY_NO_TRIGGER if view["legacy"] and not view["trigger"] else ""
        table.append([entry_id, view["status"], str(c["inject"]), str(c["pre_start"]), str(c["useful_activated"]),
                      str(c["useful_unattributed"]), str(c["disputed"]), str(c["abandoned"]), c["last_hit"] or "-", note])
    _print_table(["entry_id", "status", "inject", "pre_start", "useful_act", "useful_unattr", "disputed", "abandoned", "last_hit", "note"], table)

    print("== retry trend (attempts per run, + = an entry was injected at that unit) ==")
    injected_units = {(r.get("run_id"), r.get("unit")) for r in rows if r.get("kind") == "inject"}
    trend: dict[tuple[str, str], list[str]] = {}
    for d in digests:
        run_id = str(d["run"].get("run_id") or "?")
        skill = str(d["run"].get("skill") or "?")
        for unit in d["units"]:
            if not isinstance(unit, dict) or unit.get("outcome") not in ("passed", "failed"):
                continue
            mark = "+" if (run_id, unit.get("id")) in injected_units else ""
            trend.setdefault((skill, str(unit.get("id"))), []).append(f"{run_id}={unit.get('attempts', 0)}{mark}")
    if not trend:
        print("no digests")
    for (skill, unit), points in sorted(trend.items()):
        print(f"{skill}/{unit}: {' '.join(points)}")

    print("== next-attempt pass rate ==")
    buckets = {"injected+read": [0, 0], "injected-not-read": [0, 0], "no-injection": [0, 0]}
    activated = {(r.get("run_id"), r.get("unit")) for r in rows if r.get("kind") == "settle" and r.get("outcome") == "useful_activated"}
    first_inject: dict[tuple[str, str], int] = {}
    for r in rows:
        if r.get("kind") == "inject" and isinstance(r.get("attempt"), int):
            key = (r.get("run_id"), r.get("unit"))
            first_inject[key] = min(first_inject.get(key, r["attempt"]), r["attempt"])
    for d in digests:
        run_id = d["run"].get("run_id")
        for unit in d["units"]:
            if not isinstance(unit, dict) or unit.get("outcome") not in ("passed", "failed"):
                continue
            key = (run_id, unit.get("id"))
            attempts = unit.get("attempts") if isinstance(unit.get("attempts"), int) else 0
            if key in first_inject:
                bucket = "injected+read" if key in activated else "injected-not-read"
                blocked_at = first_inject[key]
            elif attempts >= 2:
                bucket, blocked_at = "no-injection", 1
            else:
                continue
            buckets[bucket][1] += 1
            if unit.get("outcome") == "passed" and attempts == blocked_at + 1:
                buckets[bucket][0] += 1
    print("  ".join(f"{name} {hit}/{total}" + (f" ({100 * hit // total}%)" if total else "") for name, (hit, total) in buckets.items()))

    print("== cold ratio ==")
    settled = {entry_id for entry_id, c in counts.items() if c["settles"]}
    cold = [entry_id for entry_id in views if entry_id not in settled]
    print(f"cold ratio: {len(cold)}/{len(views)} entries have no settle row" + (f" ({100 * len(cold) // len(views)}%)" if views else ""))

    print("== cells ==")
    live = {k: v for k, v in views.items() if v["status"] not in ("rejected", "superseded")}
    cells: dict[str, list[dict]] = {}
    for view in live.values():
        cells.setdefault(_cell(view), []).append(view)
    for cell, members in sorted(cells.items()):
        flags: list[str] = []
        occupants = [m for m in members if m["op"] == "add" or m["op"] is None]
        if occupants and all(m["status"] == "provisional" for m in occupants):
            member_ids = {m["entry_id"] for m in occupants}
            if any(v["op"] == "modify" and v.get("target_entry") in member_ids for v in live.values()):
                flags.append("occupant provisional, modify candidate pending")
        print(f"{cell}: {len(members)}" + (f" ({'; '.join(flags)})" if flags else ""))

    print("== layers ==")
    layer_counts = {status: 0 for status in STATUSES}
    legacy = 0
    for view in views.values():
        layer_counts[view["status"]] = layer_counts.get(view["status"], 0) + 1
        if view["legacy"]:
            legacy += 1
    print("  ".join(f"{status}: {n}" for status, n in layer_counts.items()) + f"  legacy: {legacy}")

    print("== index.md ==")
    _print_index_usage(ctx)
    return 0


def _print_index_usage(ctx: Ctx) -> None:
    md = ctx.memory_root / "index.md"
    idx = _load_index(ctx)
    if md.is_file():
        raw = md.read_bytes()
        lines = raw.count(b"\n")
        print(f"index.md: {lines} lines / {len(raw)} bytes (limit {ctx.config['index_md_max_lines']} lines / {ctx.config['index_md_max_bytes']} bytes), "
              f"omitted provisional: {idx.get('omitted_provisional', 0)}")
    else:
        print("index.md: missing")


# --- rebuild-index / fix-perms -------------------------------------------------

def cmd_rebuild_index(ctx: Ctx, args: argparse.Namespace) -> int:
    refreshed = _refresh_meta_counts(ctx)
    idx = _rebuild_index(ctx)
    reconciled: list[str] = []
    skills_root = ctx.repo_root / "sure" / "skills"
    for skill_dir in sorted(skills_root.iterdir()) if skills_root.is_dir() else []:
        bad_cases = skill_dir / "references" / "memory" / "bad_cases"
        readme = bad_cases / "README.md"
        if not readme.is_file():
            continue
        with paths.memory_lock(ctx.memory_root):
            if index_mod.reconcile_readme(readme, _records_in(bad_cases, skill_dir.name, ctx.repo_root)):
                reconciled.append(_rel(readme, ctx.repo_root))
    print(f"index rebuilt: {len(idx.get('entries', []))} entries; meta refreshed: {refreshed}; README reconciled: {', '.join(reconciled) or 'none'}")
    dropped = index_mod.hash_mismatch_line(idx)
    if dropped:
        # `cli list` reads meta and still shows these entries; only this line says they are not indexed.
        print(dropped)
        print("fix: restore the entry.md text, or reject the entry and publish it again")
    _print_index_usage(ctx)
    # Same status index.py --rebuild uses: a runbook step chained behind this one reads the exit
    # code, not these two lines. 1 stays "the rebuild itself failed".
    return index_mod.EXIT_HASH_MISMATCH if dropped else 0


def cmd_promote(ctx: Ctx, args: argparse.Namespace) -> int:
    """Run the section 8.2 rules by hand. post_finish does this after every run through
    publish_memory.py; this is the supported way to answer "why has my entry not promoted yet"
    on a clone whose hooks never got that far. promote_all takes the lock itself."""
    rows = promote.promote_all(ctx.repo_root, config=ctx.config)
    for row in rows:
        print(f"{row['action']}: {row['entry_id']} ({row['reason']})")
    print(f"promote: {len(rows)} decision(s)")
    idx = _rebuild_index(ctx)
    print(f"index rebuilt: {len(idx.get('entries', []))} entries")
    return 0


def cmd_fix_perms(ctx: Ctx, args: argparse.Namespace) -> int:
    failed = paths.fix_perms(ctx.memory_root)
    if failed:
        print(f"{len(failed)} path(s) are still not group-writable (ask their owner to chmod g+w):")
        for path in failed:
            print(f"  {path}")
        return 1
    print(f"ok: {_rel(ctx.memory_root, ctx.repo_root)} is group-writable")
    return 0


# --- main ----------------------------------------------------------------------

COMMANDS = {
    "list": cmd_list, "show": cmd_show, "compare": cmd_compare, "confirm": cmd_confirm, "export": cmd_export,
    "reject": cmd_reject, "supersede": cmd_supersede, "stats": cmd_stats, "rebuild-index": cmd_rebuild_index,
    "promote": cmd_promote, "fix-perms": cmd_fix_perms,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli.py", description="Human tools for the SURE memory system (sure/memory/). Never runs git.")
    parser.add_argument("--root", default=None, help="checkout whose sure/memory/ to operate on (default: the checkout this file lives in)")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("list", help="list entries")
    p.add_argument("--status", action="append", help="filter by status (repeatable or comma-separated)")
    p.add_argument("--skill", action="append", help="filter by target_skill (repeatable or comma-separated)")
    p.add_argument("--cold", action="store_true", help="only entries that were never injected")
    p.add_argument("--days", type=int, default=None, help="with --cold: only entries created at least N days ago")
    p = sub.add_parser("show", help="entry body, meta, proposal, usage history, descendants, evidence check")
    p.add_argument("entry_id")
    p = sub.add_parser("compare", help="unified diff against target_entry (modify/supersede) or similar.entry")
    p.add_argument("entry_id")
    p = sub.add_parser("confirm", help="mark confirmed and stage in outbox (modify/supersede take effect here)")
    p.add_argument("entry_id")
    p.add_argument("--reason", default=None)
    p = sub.add_parser("export", help="move an outbox entry into a clone's references/ and print the git command")
    p.add_argument("entry_id")
    p.add_argument("--repo-root", default=None, help="clone to write into (default: --root)")
    p = sub.add_parser("reject", help="move to rejected/ (never cascades, never touches tracked files)")
    p.add_argument("entry_id")
    p.add_argument("--reason", required=True)
    p = sub.add_parser("supersede", help="mark <old> superseded by <new>")
    p.add_argument("old_id")
    p.add_argument("--by", dest="new_id", required=True)
    p = sub.add_parser("stats", help="usage counts, retry trend, pass rates, cold ratio, cells, layers, index.md budget")
    p.add_argument("--skill", default=None)
    p.add_argument("--since", default=None, help="YYYY-MM-DD; usage rows and runs on or after this day")
    sub.add_parser("rebuild-index", help="rebuild index.json / index.md and reconcile bad_cases README route tables")
    sub.add_parser("promote", help="replay usage and apply the promote / demote rules now (post_finish does this per run)")
    sub.add_parser("fix-perms", help="make sure/memory/ group-writable again")
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    try:
        ctx = _load_ctx(args.root)
        return COMMANDS[args.command](ctx, args)
    except CliError as exc:
        print(f"cli.py {args.command}: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"cli.py {args.command}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
