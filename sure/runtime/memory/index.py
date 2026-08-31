#!/usr/bin/env python3
"""Merged memory index for the SURE memory system (design spec §6.4).

Two sources feed one index:
- git-tracked entries under sure/skills/<skill>/references/memory/bad_cases/*.md and
  sure/skills/_shared/memory/facts/*.md (confirmed and legacy; always included);
- instance entries under sure/memory/provisional/<skill>/<slug>/entry.md, included only when
  meta/<skill>/<slug>.json exists, its entry_sha256 matches the file and decisions.jsonl has a
  publish row for the entry (files placed by hand never reach the index).

Outputs: sure/memory/index.json (machine readable, match.ts reads it) and sure/memory/index.md
(agent facing bullet list with a line / byte budget). `--check` compares the recorded
sources_sha256 with the current one and rebuilds only when they differ (never mtime); it and
`--rebuild` exit EXIT_HASH_MISMATCH when an entry was dropped for a hash mismatch, 1 when the
command itself failed.
README route tables under references/ are reconciled by `--rebuild` and by `cli export`,
never by `--check`, so a hook never dirties a git-tracked file.

The entry file layout this module parses (publish.py writes it, legacy files get it by hand):

    Trigger: <t0>; <t1>
    Cell: <target_skill>/<component> x <cause>
    Source: <run_id> → <target>          (`Source: legacy` for the old entries; `legacy → legacy` and `->` accepted)
    Added: <YYYY-MM-DD>
    Status: provisional | confirmed
    Superseded-by: <entry_id> (<date>)   (optional sixth line, appended by promote / cli)

    # <H1 title>
    ...body...                            (facts: `Scope:` / `Checked-at:` lines right after the H1)

A references file without that header is a legacy entry: trigger empty, component `_`.

`hook_trigger` (the trigger subset hooks match on) is copied from meta when publish.py wrote it
there; every other entry (references, legacy, headerless, meta without the key) gets its full
`trigger` list. The indexer never computes it. index.md and the README route tables use the full
`trigger`. decisions.jsonl rows are recognised by their `action` key only (`paths.decision_row`).

index.md budget truncation adapted (direction reversed) from OpenHands software-agent-sdk
openhands/sdk/context/memory.py (_truncate_top; MIT, Copyright (c) 2026 OpenHands contributors).
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

if __package__ in (None, ""):  # run as `python sure/runtime/memory/index.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory import paths  # noqa: E402

INDEX_SCHEMA = "sure.memory.index.v1"
HEADER_KEYS = ("Trigger", "Cell", "Source", "Added", "Status", "Superseded-by")
STATUSES = ("provisional", "confirmed", "disputed", "superseded", "rejected")
# index.md line shape from spec §6.4: "- [status] <entry_id> — <H1> — triggers: a; b"; the path is
# appended as a fourth field so the agent can open the file without guessing where it lives.
MD_SEP = " — "
MD_TITLE = "# SURE memory index"
MD_INTRO = (
    "Confirmed entries first, then provisional (newest first), then disputed. Read only the entries "
    "whose triggers match your failure text; the last field is the file path relative to the repo root."
)
NO_TRIGGER_TEXT = "(none; prompt-level only)"
ROUTE_TABLE_HEADING = "## Route Table"
ROUTE_TABLE_HEADER = (
    "| Trigger or symptom | Suggested memory file | Notes |",
    "|--------------------|-----------------------|-------|",
)
# Bootstrap text copied from the onboard bad_cases/README.md (old adopt_memory.py README_BOOTSTRAP).
README_BOOTSTRAP = """# Bad Case Memory Index

Bad cases are optional memory. Read this index only after a concrete failure or
known-risk trigger appears. Then read only the matching bad-case file.

Do not pre-load every historical story into default context.

## Route Table

| Trigger or symptom | Suggested memory file | Notes |
|--------------------|-----------------------|-------|
"""
_BUILD_ATTEMPTS = 3  # re-scans allowed when the tree changed under a build (see build_index)
_SEPARATOR_ROW_RE = re.compile(r"^\|[\s:|-]+\|?$")
_FILE_CELL_RE = re.compile(r"^`([^`]+)`$")
_TIER = {"confirmed": 0, "provisional": 1, "disputed": 2, "superseded": 3}


@dataclass
class EntryRecord:
    """One index.json row. Field names are the JSON keys (spec skeleton §1.7)."""

    entry_id: str
    type: str = "bad_case"
    status: str = "confirmed"
    target_skill: str = ""
    applies_to: list[str] = field(default_factory=list)
    component: str = "_"
    cause: str = "n.a."
    trigger: list[str] = field(default_factory=list)
    hook_trigger: list[str] = field(default_factory=list)  # match.ts matches on this; == trigger unless meta narrows it
    scope: str | None = None
    title: str = ""
    path: str = ""
    legacy: bool = False
    op: str = "add"
    target_entry: str | None = None
    similar_entry: str | None = None
    useful_activated: int = 0
    useful_unattributed: int = 0
    injections: int = 0
    disputed: int = 0
    created: Any = "legacy"
    checked_at: str | None = None
    stale: bool = False
    superseded_by: str | None = None


# --- entry file parsing ----------------------------------------------------------

def _read_lines(path: Path) -> list[str]:
    # bytes + splitlines: identical result for LF and CRLF files (Windows checkouts are CRLF)
    return Path(path).read_bytes().decode("utf-8", errors="replace").splitlines()


def parse_header(lines: list[str]) -> tuple[dict[str, str], int]:
    """Leading provenance block -> ({key: value}, index of the first line after it).
    Leading blank lines are skipped; the block ends at the first line without a known prefix."""
    header: dict[str, str] = {}
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    while i < len(lines):
        stripped = lines[i].strip()
        key, sep, value = stripped.partition(":")
        if not sep or key not in HEADER_KEYS:
            break
        header[key] = value.strip()
        i += 1
    return header, i


def _parse_cell(value: str) -> tuple[str, str]:
    """'sure_onboard/build_env x infra' -> ('build_env', 'infra'); tolerates the old 'build_env x infra'."""
    left, _, cause = value.partition(" x ")
    left = left.strip()
    component = left.split("/", 1)[1] if "/" in left else left
    return component or "_", cause.strip() or "n.a."


def _parse_source(value: str) -> str:
    """'run-123 → qwen' -> 'run-123' (also accepts the ASCII '->' arrow); no arrow: the whole value
    is the run_id, so `Source: legacy` (the form Task 16 writes) yields 'legacy'."""
    for arrow in ("→", "->"):
        if arrow in value:
            return value.split(arrow, 1)[0].strip()
    return value.strip()


def _first_body_lines(lines: list[str], start: int, limit: int = 8) -> list[str]:
    out: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        if line.strip():
            out.append(line.strip())
        if len(out) >= limit:
            break
    return out


def parse_entry_file(path: Path, *, target_skill: str, legacy_dir: bool) -> EntryRecord:
    """Parse one entry file (references or provisional). `legacy_dir=True` means the file is a
    git-tracked references entry: a missing header makes it a legacy entry instead of an error.
    Counts / meta overlays are the caller's job; `path` is filled with the absolute posix path."""
    path = Path(path)
    lines = _read_lines(path)
    header, body_start = parse_header(lines)
    if not header and not legacy_dir:
        raise ValueError(f"{path}: provisional entry has no provenance header")
    slug = path.parent.name if path.name == "entry.md" else path.stem
    h1_idx = next((i for i in range(body_start, len(lines)) if lines[i].startswith("# ")), None)
    title = lines[h1_idx][2:].strip() if h1_idx is not None else slug
    after_h1 = _first_body_lines(lines, h1_idx + 1 if h1_idx is not None else body_start)
    is_fact = path.parent.name == "facts" or any(line.startswith("Scope:") for line in after_h1)

    rec = EntryRecord(entry_id=f"{target_skill}/{slug}", target_skill=target_skill, title=title, path=path.as_posix())
    rec.type = "fact" if is_fact else "bad_case"
    rec.applies_to = ["_shared"] if is_fact else [target_skill]
    if is_fact:
        for line in after_h1:
            if line.startswith("Scope:"):
                rec.scope = line[len("Scope:"):].strip() or None
            elif line.startswith("Checked-at:"):
                rec.checked_at = line[len("Checked-at:"):].strip() or None
    run_id = _parse_source(header.get("Source", "legacy")) if header else "legacy"
    rec.legacy = legacy_dir and (not header or run_id == "legacy")
    if header:
        rec.trigger = [t.strip() for t in header.get("Trigger", "").split(";") if t.strip()]
        rec.component, rec.cause = _parse_cell(header.get("Cell", ""))
        status = header.get("Status", "")
        rec.status = status if status in STATUSES else ("confirmed" if legacy_dir else "provisional")
        rec.created = "legacy" if run_id == "legacy" else {"run_id": run_id, "date": header.get("Added", "")}
        superseded = header.get("Superseded-by", "").split(" ", 1)[0].strip()
        if superseded:
            rec.superseded_by = superseded
            rec.status = "superseded"
    rec.hook_trigger = list(rec.trigger)  # default; _apply_meta narrows it when meta carries hook_trigger
    return rec


# --- source enumeration --------------------------------------------------------------

def _reference_dirs(repo_root: Path) -> list[tuple[str, Path]]:
    """(target_skill, directory) for every git-tracked entry directory that exists."""
    skills_dir = Path(repo_root) / "sure" / "skills"
    found: list[tuple[str, Path]] = []
    if not skills_dir.is_dir():
        return found
    for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        candidate = skill_dir / "references" / "memory" / "bad_cases"
        if skill_dir.name == "_shared":
            candidate = skill_dir / "memory" / "facts"
        if candidate.is_dir():
            found.append((skill_dir.name, candidate))
    return found


def _entry_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("*.md") if p.is_file() and p.name.lower() != "readme.md")


def _provisional_entries(root: Path) -> list[tuple[str, Path]]:
    """(target_skill, entry.md) under sure/memory/provisional/<skill>/<slug>/, sorted."""
    base = Path(root) / "provisional"
    found: list[tuple[str, Path]] = []
    if not base.is_dir():
        return found
    for skill_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        for slug_dir in sorted(p for p in skill_dir.iterdir() if p.is_dir()):
            entry = slug_dir / "entry.md"
            if entry.is_file():
                found.append((skill_dir.name, entry))
    return found


def _source_files(repo_root: Path) -> list[Path]:
    """Every file whose content decides the index: references entries, provisional entry.md +
    proposal.json, meta files, decisions.jsonl. Sorted by repo-relative posix path."""
    repo_root = Path(repo_root)
    root = paths.memory_root(repo_root)
    files: list[Path] = []
    for _skill, directory in _reference_dirs(repo_root):
        files.extend(_entry_files(directory))
    for _skill, entry in _provisional_entries(root):
        files.append(entry)
        proposal = entry.parent / "proposal.json"
        if proposal.is_file():
            files.append(proposal)
    meta_dir = root / "meta"
    if meta_dir.is_dir():
        files.extend(p for p in meta_dir.glob("*/*.json") if p.is_file())
    decisions = root / "decisions.jsonl"
    if decisions.is_file():
        files.append(decisions)
    return sorted(files, key=lambda p: _rel(p, repo_root))


def _rel(path: Path, repo_root: Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(repo_root).resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def sources_sha256(repo_root: Path) -> str:
    """sha256 over "<relpath> <sha256(content)>\\n" of every source file, sorted by path.
    Content only: touching a file without changing it does not change this value."""
    parts: list[str] = []
    for path in _source_files(repo_root):
        try:
            parts.append(f"{_rel(path, repo_root)} {paths.sha256_file(path)}\n")
        except OSError:
            continue  # vanished between listing and hashing: the next check sees the difference
    return paths.sha256_text("".join(parts))


# --- meta / proposal / decisions overlays -----------------------------------------------

def _load_meta(root: Path, entry_id: str) -> dict | None:
    parts = paths.split_entry_id(entry_id)
    if parts is None:
        return None
    meta_path = Path(root) / "meta" / parts[0] / f"{parts[1]}.json"
    if not meta_path.is_file():
        return None
    try:
        meta = paths.load_json(meta_path)
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def _published_entry_ids(root: Path) -> set[str]:
    """entry_ids with a publish row. The row key is `action` (§1.6, written by paths.decision_row);
    rows shaped any other way are not publish rows."""
    rows, _bad = paths.read_jsonl(Path(root) / "decisions.jsonl")
    return {
        str(row.get("entry_id"))
        for row in rows
        if row.get("action") == "publish" and isinstance(row.get("entry_id"), str)
    }


def _apply_meta(rec: EntryRecord, meta: dict | None) -> None:
    """meta is the state authority: status (a confirmed references file may be disputed or demoted
    in meta), counts, superseded_by, checked_at, created, applies_to, hook_trigger."""
    if not meta:
        return
    if isinstance(meta.get("hook_trigger"), list):
        # publish.py computed the subset of triggers seen verbatim in the run digest; copy it as is
        rec.hook_trigger = [t for t in meta["hook_trigger"] if isinstance(t, str)]
    for name in ("injections", "useful_activated", "useful_unattributed", "disputed"):
        value = meta.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            setattr(rec, name, value)
    status = meta.get("status")
    if status in STATUSES:
        rec.status = status
    if isinstance(meta.get("superseded_by"), str) and meta["superseded_by"]:
        rec.superseded_by = meta["superseded_by"]
        rec.status = "superseded"
    if isinstance(meta.get("checked_at"), str) and meta["checked_at"]:
        rec.checked_at = meta["checked_at"]
    if isinstance(meta.get("created"), dict):
        rec.created = meta["created"]
    if isinstance(meta.get("applies_to"), list) and meta["applies_to"]:
        rec.applies_to = [str(s) for s in meta["applies_to"]]


def _apply_proposal(rec: EntryRecord, proposal_path: Path) -> None:
    """proposal.json carries what the header does not: op, target_entry, similar.entry, applies_to,
    scope / checked_at for facts, type."""
    try:
        proposal = paths.load_json(proposal_path)
    except (OSError, ValueError):
        return
    if not isinstance(proposal, dict):
        return
    if proposal.get("type") in ("bad_case", "fact"):
        rec.type = proposal["type"]
    if proposal.get("op") in ("add", "modify", "supersede"):
        rec.op = proposal["op"]
    if isinstance(proposal.get("target_entry"), str) and proposal["target_entry"]:
        rec.target_entry = proposal["target_entry"]
    similar = proposal.get("similar")
    if isinstance(similar, dict) and isinstance(similar.get("entry"), str) and similar["entry"]:
        rec.similar_entry = similar["entry"]
    if isinstance(proposal.get("applies_to"), list) and proposal["applies_to"]:
        rec.applies_to = [str(s) for s in proposal["applies_to"]]
    if isinstance(proposal.get("scope"), str) and proposal["scope"]:
        rec.scope = proposal["scope"]
    if isinstance(proposal.get("checked_at"), str) and proposal["checked_at"]:
        rec.checked_at = proposal["checked_at"]


def _parse_date(text: str | None) -> date | None:
    if not isinstance(text, str):
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _is_stale(rec: EntryRecord, config: dict, today: date) -> bool:
    """fact only: checked_at older than stale_after_days[<scope kind>] days. Unknown date: not stale."""
    if rec.type != "fact":
        return False
    checked = _parse_date(rec.checked_at)
    if checked is None:
        return False
    kind = (rec.scope or "cluster").split(":", 1)[0]
    limit = config.get("stale_after_days", {}).get(kind)
    if not isinstance(limit, int):
        return False
    return (today - checked).days > limit


def _finish(rec: EntryRecord, repo_root: Path, config: dict, units: dict, today: date) -> None:
    rec.path = _rel(Path(rec.path), repo_root)
    known_units = units.get("skills", {}).get(rec.target_skill, [])
    if rec.type == "bad_case" and rec.component != "_" and rec.component not in known_units:
        rec.component = "_"  # a Cell that names no unit of that skill occupies no cell (spec §6.4 legacy rule)
    if rec.type == "fact":
        rec.component = "_"
    rec.stale = _is_stale(rec, config, today)


# --- ordering ------------------------------------------------------------------------

def _created_date(entry: dict) -> str:
    created = entry.get("created")
    if isinstance(created, dict):
        return str(created.get("date") or "")
    return ""


def ordered_entries(entries: list[dict]) -> list[dict]:
    """confirmed (by entry_id) -> provisional (newest first) -> disputed -> superseded; anything else last."""
    tiers: dict[int, list[dict]] = {}
    for entry in entries:
        tiers.setdefault(_TIER.get(str(entry.get("status")), 9), []).append(entry)
    out: list[dict] = []
    for tier in sorted(tiers):
        rows = tiers[tier]
        if tier == 1:
            rows = sorted(rows, key=lambda e: (_created_date(e), str(e.get("entry_id"))), reverse=True)
        else:
            rows = sorted(rows, key=lambda e: str(e.get("entry_id")))
        out.extend(rows)
    return out


# --- build / render / write ------------------------------------------------------------

def build_index(repo_root: Path, *, config: dict, units: dict) -> dict:
    """Read every source and produce the index dict (not written). Never needs .sure/runs/.

    sources_sha256 is taken before the scan and verified after it. Hashing last would record a tree
    the scan never read: a publish that lands mid-scan is then missing from index.json while the
    recorded hash already covers it, so `--check` reports "up to date" for ever and the entry stays
    out of injection until some unrelated source file changes. When the tree moved, the scan is
    repeated; after _BUILD_ATTEMPTS the pre-scan hash is kept, which is stale on purpose so the next
    --check rebuilds. build_index does not hold the memory lock (write_index takes it, and locks do
    not nest), so this is what keeps a concurrent writer honest."""
    repo_root = Path(repo_root).resolve()
    sources = sources_sha256(repo_root)
    index: dict = {}
    for _attempt in range(_BUILD_ATTEMPTS):
        index = _build_once(repo_root, config=config, units=units, sources=sources)
        after = sources_sha256(repo_root)
        if after == sources:
            return index
        sources = after  # the verification hash of this pass is the pre-scan hash of the next
    return index


def _build_once(repo_root: Path, *, config: dict, units: dict, sources: str) -> dict:
    repo_root = Path(repo_root).resolve()
    root = paths.memory_root(repo_root)
    today = date.fromisoformat(paths.utc_today())
    records: list[EntryRecord] = []
    seen: set[str] = set()
    for skill, directory in _reference_dirs(repo_root):
        for path in _entry_files(directory):
            try:
                rec = parse_entry_file(path, target_skill=skill, legacy_dir=True)
            except OSError:
                continue
            _apply_meta(rec, _load_meta(root, rec.entry_id))
            _finish(rec, repo_root, config, units, today)
            records.append(rec)
            seen.add(rec.entry_id)
    published = _published_entry_ids(root)
    mismatched: list[str] = []
    for skill, entry_md in _provisional_entries(root):
        entry_id = f"{skill}/{entry_md.parent.name}"
        if entry_id in seen:
            continue  # references wins over the provisional / outbox copy
        meta = _load_meta(root, entry_id)
        if meta is None or entry_id not in published:
            continue
        try:
            if meta.get("entry_sha256") != paths.sha256_file(entry_md):
                # The entry.md no longer matches the hash meta recorded (a hand edit, or a kill
                # between an in-place rewrite and the meta refresh). Dropping it silently takes it
                # out of injection for good while cli list still shows it, so it is named here and
                # reported by index_report / cli rebuild-index until someone fixes it.
                mismatched.append(entry_id)
                continue
            rec = parse_entry_file(entry_md, target_skill=skill, legacy_dir=False)
        except (OSError, ValueError):
            continue
        _apply_proposal(rec, entry_md.parent / "proposal.json")
        _apply_meta(rec, meta)
        if rec.status == "rejected":
            continue
        _finish(rec, repo_root, config, units, today)
        records.append(rec)
        seen.add(entry_id)
    return {
        "schema": INDEX_SCHEMA,
        "built_at": paths.utc_now(),
        "sources_sha256": sources,
        "entries": ordered_entries([asdict(r) for r in records]),
        "omitted_provisional": 0,
        "hash_mismatch": sorted(mismatched),
    }


def never_injected(entry: dict) -> bool:
    """True when the hooks can never select this bad_case, however routable its row looks.

    match.ts requires a non-empty hook_trigger and, for a bad_case, `component === unit`; no unit is
    named `_`, so such an entry is listed with its triggers and never fires. Facts are excluded:
    matchFacts also accepts a scope hit, so an empty hook_trigger does not silence them."""
    if entry.get("type") != "bad_case":
        return False
    hook = entry.get("hook_trigger")
    if not isinstance(hook, list):
        hook = entry.get("trigger") or []  # index.json written before hook_trigger existed
    return not hook or (entry.get("component") or "_") == "_"


def _md_line(entry: dict) -> str:
    # index.md lists the full trigger list (prompt-level routing); hook_trigger is for match.ts only
    tags = ""
    if entry.get("legacy"):
        tags += " [legacy]"
    if entry.get("stale"):
        tags += " [stale]"
    if never_injected(entry):
        tags += " [no hook trigger]"
    triggers = "; ".join(entry.get("trigger") or []) or NO_TRIGGER_TEXT
    return (
        f"- [{entry.get('status')}]{tags} {entry.get('entry_id')}{MD_SEP}{entry.get('title')}"
        f"{MD_SEP}triggers: {triggers}{MD_SEP}{entry.get('path')}"
    )


def _fits(lines: list[str], config: dict) -> bool:
    text = "\n".join(lines) + "\n"
    return len(lines) <= config["index_md_max_lines"] and len(text.encode("utf-8")) <= config["index_md_max_bytes"]


def render_index_md(index: dict, config: dict) -> tuple[str, int]:
    """(index.md text, omitted provisional count). superseded / rejected never appear. When the
    budget is exceeded the oldest provisional lines are dropped one by one (the notice line counts
    toward the budget, whole lines only). Confirmed / disputed lines are never dropped."""
    head = [MD_TITLE, "", f"Built {index.get('built_at', '')}. {MD_INTRO}", ""]
    visible = [e for e in ordered_entries(list(index.get("entries", []))) if e.get("status") in ("confirmed", "provisional", "disputed")]
    confirmed = [_md_line(e) for e in visible if e["status"] == "confirmed"]
    provisional = [_md_line(e) for e in visible if e["status"] == "provisional"]
    disputed = [_md_line(e) for e in visible if e["status"] == "disputed"]
    omitted = 0
    lines = head + confirmed + provisional + disputed
    while not _fits(lines, config) and provisional:
        provisional.pop()  # provisional is newest first, so pop drops the oldest line
        omitted += 1
        notice = (
            f"- (omitted {omitted} older provisional entries; run "
            f"`python3 -s sure/runtime/memory/cli.py list --status provisional` to see them)"
        )
        lines = head + confirmed + provisional + disputed + [notice]
    return "\n".join(lines) + "\n", omitted


def write_index(repo_root: Path, index: dict, config: dict) -> None:
    """Render index.md, fill omitted_provisional, write both files atomically under the memory lock.
    Takes the lock itself: callers must not hold paths.memory_lock when calling this."""
    root = paths.memory_root(Path(repo_root))
    with paths.memory_lock(root):
        paths.ensure_memory_tree(root)
        text, omitted = render_index_md(index, config)
        index["omitted_provisional"] = omitted
        paths.atomic_write_json(root / "index.json", index)
        paths.atomic_write_text(root / "index.md", text)


def check_index(repo_root: Path, *, config: dict, units: dict) -> bool:
    """Rebuild when index.json is missing / unreadable / wrong schema, when index.md is missing, or
    when sources_sha256 differs from the recorded one. Returns True when it rebuilt."""
    root = paths.memory_root(Path(repo_root))
    current = sources_sha256(repo_root)
    up_to_date = False
    try:
        existing = paths.load_json(root / "index.json")
        up_to_date = (
            isinstance(existing, dict)
            and existing.get("schema") == INDEX_SCHEMA
            and existing.get("sources_sha256") == current
            and (root / "index.md").is_file()
        )
    except (OSError, ValueError):
        up_to_date = False
    if up_to_date:
        return False
    write_index(repo_root, build_index(repo_root, config=config, units=units), config)
    return True


def read_index(repo_root: Path) -> dict | None:
    """index.json as a dict, or None when missing / broken / wrong schema (python callers only)."""
    try:
        index = paths.load_json(paths.memory_root(Path(repo_root)) / "index.json")
    except (OSError, ValueError):
        return None
    if not isinstance(index, dict) or index.get("schema") != INDEX_SCHEMA or not isinstance(index.get("entries"), list):
        return None
    return index


def records_from_index(index: dict) -> list[EntryRecord]:
    names = set(EntryRecord.__dataclass_fields__)
    records: list[EntryRecord] = []
    for entry in index.get("entries", []):
        if not isinstance(entry, dict) or "entry_id" not in entry:
            continue
        rec = EntryRecord(**{k: v for k, v in entry.items() if k in names})
        if "hook_trigger" not in entry:
            rec.hook_trigger = list(rec.trigger)  # index.json written by an older build: same fallback as match.ts
        records.append(rec)
    return records


# --- README route table reconciliation ----------------------------------------------------

def _row_file(row: str) -> str | None:
    """The bare file name in a route row's second cell, or None when the row is not shaped like that."""
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    if len(cells) < 2:
        return None
    match = _FILE_CELL_RE.match(cells[1])
    return match.group(1) if match else None


def _escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _route_row(rec: EntryRecord, file_name: str) -> str:
    if rec.trigger:
        return f"| {_escape_cell('; '.join(rec.trigger))} | `{file_name}` | {_escape_cell(rec.title)} |"
    return f"| {_escape_cell(rec.title)} | `{file_name}` | No trigger header; prompt-level routing only. |"


def _locate_table(lines: list[str]) -> tuple[int, int, int] | None:
    """(index of first table line, index of first data row, index after the last row) or None."""
    heading = next((i for i, line in enumerate(lines) if line.strip() == ROUTE_TABLE_HEADING), None)
    if heading is None:
        return None
    i = heading + 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or not lines[i].lstrip().startswith("|"):
        return None
    start = i
    end = start
    while end < len(lines) and lines[end].lstrip().startswith("|"):
        end += 1
    rows_start = start + 1
    if rows_start < end and _SEPARATOR_ROW_RE.match(lines[rows_start].strip()):
        rows_start += 1
    return start, rows_start, end


def _dir_owner_skill(directory: Path) -> str | None:
    """The skill that owns an entry directory: `.../sure/skills/<skill>/references/memory/bad_cases`
    or `.../sure/skills/_shared/memory/facts`. None when the path has neither shape."""
    parts = Path(directory).parts
    if len(parts) >= 4 and parts[-3:] == ("references", "memory", "bad_cases"):
        return parts[-4]
    if len(parts) >= 3 and parts[-2:] == ("memory", "facts"):
        return parts[-3]
    return None


def reconcile_readme(readme_path: Path, entries: list[EntryRecord]) -> bool:
    """Idempotent route-table sync for one references directory (spec §9): rows whose file vanished
    are dropped, every confirmed entry of that directory's own skill without a row gets one, every
    other row is kept byte for byte, nothing is written when nothing changed. The caller holds
    paths.memory_lock (cli export / --rebuild); returns True when the file was written."""
    readme_path = Path(readme_path)
    directory = readme_path.parent
    owner = _dir_owner_skill(directory)
    present = {p.name for p in _entry_files(directory)}
    if readme_path.is_file():
        raw = readme_path.read_bytes().decode("utf-8", errors="replace")
    else:
        raw = README_BOOTSTRAP
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.splitlines()
    located = _locate_table(lines)
    if located is None:
        lines = [*lines, "", ROUTE_TABLE_HEADING, "", *ROUTE_TABLE_HEADER]
        located = (len(lines) - 2, len(lines), len(lines))
    start, rows_start, end = located
    kept: list[str] = []
    listed: set[str] = set()
    for row in lines[rows_start:end]:
        name = _row_file(row)
        if name is not None and name not in present:
            continue  # the file it points to is gone
        if name is not None:
            listed.add(name)
        kept.append(row)
    added: list[str] = []
    for rec in entries:
        entry_path = PurePosixPath(rec.path)
        if rec.status != "confirmed" or entry_path.parent.name != directory.name:
            continue
        if owner is not None and rec.target_skill != owner:
            continue  # two skills can hold a bad case with the same file name; each README keeps its own row
        if entry_path.name in present and entry_path.name not in listed:
            added.append(_route_row(rec, entry_path.name))
            listed.add(entry_path.name)
    new_lines = lines[:rows_start] + kept + added + lines[end:]
    new_raw = newline.join(new_lines) + newline
    if readme_path.is_file() and new_raw == raw:
        return False
    paths.atomic_write_bytes(readme_path, new_raw.encode("utf-8"))
    return True


def reconcile_all_readmes(repo_root: Path, entries: list[EntryRecord]) -> list[Path]:
    """Every references bad_cases/ directory that exists gets its README route table reconciled.
    facts/ is skipped: sure/skills/_shared/memory/facts/README.md is hand written (Task 15) and is
    never machine reconciled, the same boundary cli.py rebuild-index draws. Caller holds the lock."""
    written: list[Path] = []
    for _skill, directory in _reference_dirs(Path(repo_root)):
        if directory.name != "bad_cases":
            continue  # the _shared facts index is hand written
        readme = directory / "README.md"
        if reconcile_readme(readme, entries):
            written.append(readme)
    return written


# --- cli -------------------------------------------------------------------------------

EXIT_HASH_MISMATCH = 2
"""`--check`'s and `--rebuild`'s exit status when at least one entry was dropped for a hash
mismatch (0 = clean, 1 = the command itself could not run). Non-zero because that status is the
whole report: hooks.ts builds its `memory index check failed` warning from `ok: r.status === 0`,
never from stderr, and a `--rebuild` chained behind `&&` never has its stdout read at all."""


def hash_mismatch_line(index: dict) -> str | None:
    """The one line that makes a dropped entry visible, or None when nothing was dropped.
    An entry.md that no longer matches meta.entry_sha256 is left out of the index for ever
    (spec 6.4) while cli list still shows it, so every reporting path prints this."""
    dropped = [str(entry_id) for entry_id in index.get("hash_mismatch") or []]
    if not dropped:
        return None
    return f"{len(dropped)} provisional entries dropped: hash mismatch ({', '.join(sorted(dropped))})"


def index_report(index: dict, text: str, config: dict) -> str:
    counts: dict[str, int] = {}
    for entry in index.get("entries", []):
        counts[str(entry.get("status"))] = counts.get(str(entry.get("status")), 0) + 1
    by_status = ", ".join(f"{counts.get(s, 0)} {s}" for s in ("confirmed", "provisional", "disputed", "superseded"))
    dropped = hash_mismatch_line(index)
    return (
        f"index: {len(index.get('entries', []))} entries ({by_status}); "
        f"index.md {text.count(chr(10))} lines / {len(text.encode('utf-8'))} bytes "
        f"(limits {config['index_md_max_lines']} / {config['index_md_max_bytes']}); "
        f"omitted provisional: {index.get('omitted_provisional', 0)}"
        + (f"; {dropped}" if dropped else "")
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="index.py", description="Build or check the merged SURE memory index.")
    parser.add_argument("--repo-root", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="rebuild only when the source content hash changed")
    mode.add_argument("--rebuild", action="store_true", help="always rebuild, then reconcile the README route tables")
    args = parser.parse_args(argv)
    config = paths.load_config()
    units = paths.load_units()
    repo_root = args.repo_root.resolve()
    try:
        if args.check:
            rebuilt = check_index(repo_root, config=config, units=units)
            print("index: rebuilt" if rebuilt else "index: up to date")
            # Reported on every check, rebuilt or not: the sha of a diverged entry never comes back
            # on its own, so "up to date" alone would hide the entry for good. The exit status is
            # what actually carries it: hooks.ts's runMemoryScript returns `ok: r.status === 0` and
            # preStartMemory raises its warning from `!check.ok`, so a mismatch reported only on
            # stderr would be invisible at runtime. Verify by grepping hooks.ts for `r.status === 0`.
            dropped = hash_mismatch_line(read_index(repo_root) or {})
            if dropped:
                print(f"index: {dropped}", file=sys.stderr)
                return EXIT_HASH_MISMATCH
            return 0
        index = build_index(repo_root, config=config, units=units)
        write_index(repo_root, index, config)
        text = (paths.memory_root(repo_root) / "index.md").read_text(encoding="utf-8")
        with paths.memory_lock(paths.memory_root(repo_root)):
            written = reconcile_all_readmes(repo_root, records_from_index(index))
        print(index_report(index, text, config))
        for readme in written:
            print(f"readme updated: {_rel(readme, repo_root)}")
        # index_report already named the dropped entries, but nothing chained behind this command
        # reads stdout; the same status --check uses is the only part an unattended caller sees.
        return EXIT_HASH_MISMATCH if hash_mismatch_line(index) else 0
    except (OSError, ValueError) as exc:  # hooks surface this line as a diagnostic
        print(f"index: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
