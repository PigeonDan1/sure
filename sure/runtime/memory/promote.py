#!/usr/bin/env python3
"""Promotion, demotion and supersede bookkeeping for memory entries (spec 8.2, 6.3).

Runs at post_finish (through publish.main), standalone (`python3 -s promote.py --repo-root <repo>`)
and from `python3 -s cli.py promote`, the supported way to force a pass by hand.
The automatic pass (promote_all) never touches sure/skills/**/references (git-tracked; a
shared checkout must stay fast-forwardable), never deletes an entry, and never edits a
provisional entry.md (the index checks that file's sha against meta). The one writer of a
tracked file is apply_supersede, called only by `cli supersede` / `cli confirm` of a
supersede candidate: a superseded entry must carry its header in every copy, including the
committed one, so that a clone stops routing to it. It rewrites that one header line and
keeps the file's existing line endings; cli prints the path so the human can commit it.
Everything it decides is derived
from usage rows replayed by usage.py, so a clean, uninterrupted run twice changes nothing.
Meta is written before its decision row is appended, so a process death between the two
can drop that row but can never duplicate it (see promote_all's docstring).

Per entry, at most one status transition per pass, evaluated in this order:
  1. provisional, op=add, type=bad_case, useful_activated >= K from >= N distinct runs,
     disputed == 0  -> confirmed (by auto): outbox copy + "promote" decision row;
  2. provisional, disputed >= 1                    -> disputed (no auto-return, no row);
  3. confirmed, disputed streak since last useful_activated >= demote_disputed_streak
                                                    -> provisional: "demote" decision row,
                                                       outbox copy removed if still there.
     After a human `cli confirm` only the disputes dated after that confirm count towards the
     streak: the human already adjudicated the older ones (see _demote_streak).
Facts never move (spec 8.2 "fact 不按命中升降"); superseded / rejected are left alone.
Counts in every meta are refreshed on every pass regardless of status. Nothing here
touches meta.trigger / meta.hook_trigger (publish computes them once).

An entry that only exists under sure/skills/**/references (the 17 old bad cases, anything
hand-committed) never went through publish, so nothing has written its meta. Spec 6.3 says
those carry meta too, because that is where their counts live, so every pass first creates
the missing stubs and then counts them like any other entry.

The header block of an entry file is the leading run of "Key: value" lines
(Trigger / Cell / Source / Added / Status / Superseded-by) up to the first blank
line; the body (H1 first) follows. Legacy files have no header block.

decisions.jsonl rows are built and appended with paths.decision_row / paths.append_decision
(skeleton 1.6); the names are re-exported below only so older callers keep working.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

if __package__ in (None, ""):  # run as `python -s sure/runtime/memory/promote.py`, as the docstring says
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory import paths, usage  # noqa: E402

HEADER_PREFIXES = ("Trigger:", "Cell:", "Source:", "Added:", "Status:", "Superseded-by:")
_STATUSES = ("provisional", "confirmed", "disputed", "superseded", "rejected")

# Re-exports: the one decisions.jsonl writer lives in paths.py. Callers hold paths.memory_lock.
DECISION_ACTIONS = paths.DECISION_ACTIONS
decision_row = paths.decision_row
append_decision = paths.append_decision


# --- locations -----------------------------------------------------------------

def meta_path(memory_root: Path, entry_id: str) -> Path:
    skill, slug = _split(entry_id)
    return Path(memory_root) / "meta" / skill / f"{slug}.json"


def provisional_entry(memory_root: Path, entry_id: str) -> Path:
    skill, slug = _split(entry_id)
    return Path(memory_root) / "provisional" / skill / slug / "entry.md"


def outbox_entry(memory_root: Path, entry_id: str) -> Path:
    skill, slug = _split(entry_id)
    return Path(memory_root) / "outbox" / skill / slug / "entry.md"


def references_entry(repo_root: Path, entry_id: str, entry_type: str | None) -> Path:
    """Where `cli export` puts the entry: bad_cases of the target skill, or the shared facts dir.
    entry_type None means "unknown": _shared ids are facts, everything else a bad_case."""
    skill, slug = _split(entry_id)
    if entry_type == "fact" or (entry_type is None and skill == "_shared"):
        return Path(repo_root) / "sure" / "skills" / "_shared" / "memory" / "facts" / f"{slug}.md"
    return Path(repo_root) / "sure" / "skills" / skill / "references" / "memory" / "bad_cases" / f"{slug}.md"


def entry_files(repo_root: Path, entry_id: str, entry_type: str | None) -> list[Path]:
    """Every copy of the entry that exists on disk (references, provisional, outbox), in that order."""
    root = paths.memory_root(repo_root)
    candidates = [
        references_entry(repo_root, entry_id, entry_type),
        provisional_entry(root, entry_id),
        outbox_entry(root, entry_id),
    ]
    return [p for p in candidates if p.is_file()]


def reference_entry_files(repo_root: Path) -> list[tuple[str, Path]]:
    """(entry_id, path) for every git-tracked entry file: <skill>/references/memory/bad_cases/*.md
    plus _shared/memory/facts/*.md (spec 6.1). README.md is the routing table, not an entry, and a
    file name that cannot be a slug is skipped rather than turned into a malformed entry id."""
    skills_dir = Path(repo_root) / "sure" / "skills"
    found: list[tuple[str, Path]] = []
    if not skills_dir.is_dir():
        return found
    for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        if skill_dir.name == "_shared":
            directory = skill_dir / "memory" / "facts"
        else:
            directory = skill_dir / "references" / "memory" / "bad_cases"
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            if not path.is_file() or path.name.lower() == "readme.md":
                continue
            entry_id = f"{skill_dir.name}/{path.stem}"
            if paths.split_entry_id(entry_id) is not None:
                found.append((entry_id, path))
    return found


def _split(entry_id: str) -> tuple[str, str]:
    parts = paths.split_entry_id(entry_id)
    if parts is None:
        raise ValueError(f"malformed entry id: {entry_id!r}")
    return parts


# --- header block helpers --------------------------------------------------------

def split_header(text: str) -> tuple[list[str], list[str]]:
    """(header_lines, body_lines). Header = leading lines that start with a known prefix,
    stopped by the first blank line or non-header line. Legacy files return ([], all).
    Blank lines before the block are skipped, as index.parse_header does: a hand-edited file
    with one leading blank must not read as headerless here and headed there."""
    lines = text.splitlines()
    header: list[str] = []
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    while i < len(lines) and lines[i].startswith(HEADER_PREFIXES):
        header.append(lines[i])
        i += 1
    if not header:
        return [], lines
    body = lines[i:]
    while body and not body[0].strip():  # drop the separator blank line(s); joined back on write
        body.pop(0)
    return header, body


def join_header(header: list[str], body: list[str]) -> str:
    if not header:
        return "\n".join(body) + "\n"
    return "\n".join([*header, "", *body]) + "\n"


def keep_line_endings(source: str, rewritten: str) -> str:
    """Give `rewritten` (join_header always emits LF) the line ending `source` was written with.

    The repo runs git core.autocrlf with no .gitattributes, so a references entry in a working tree
    is normally CRLF. Without this, one added or rewritten header line reads as a diff of the whole
    file, which is exactly the fast-forward hazard a shared checkout must not get from a cli command.
    Every rewrite that lands back on disk goes through here; the readers that feed them read bytes
    and decode rather than read_text(), whose universal newlines would erase the evidence first."""
    return rewritten.replace("\n", "\r\n") if "\r\n" in source else rewritten


def with_status(text: str, status: str) -> str:
    """Rewrite (or add) the Status: header line. Used for the outbox copy only."""
    header, body = split_header(text)
    if not header:
        return text
    kept = [line for line in header if not line.startswith("Status:")]
    kept.append(f"Status: {status}")
    return keep_line_endings(text, join_header(kept, body))


def with_superseded_by(text: str, new_id: str, date: str) -> str:
    """Add or replace the Superseded-by: line. Legacy files (no header) get a one-line header."""
    header, body = split_header(text)
    kept = [line for line in header if not line.startswith("Superseded-by:")]
    kept.append(f"Superseded-by: {new_id} ({date})")
    return keep_line_endings(text, join_header(kept, body))


# --- meta helpers ----------------------------------------------------------------

def _load_meta(path: Path) -> dict | None:
    try:
        meta = paths.load_json(path)
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def _iter_meta(memory_root: Path) -> list[tuple[str, Path, dict]]:
    """(entry_id, meta_path, meta) for every readable meta file, sorted by path."""
    found: list[tuple[str, Path, dict]] = []
    meta_dir = Path(memory_root) / "meta"
    if not meta_dir.is_dir():
        return found
    for path in sorted(meta_dir.glob("*/*.json")):
        meta = _load_meta(path)
        if meta is None:
            continue
        entry_id = meta.get("entry_id")
        if not isinstance(entry_id, str) or paths.split_entry_id(entry_id) is None:
            entry_id = f"{path.parent.name}/{path.stem}"
        if paths.split_entry_id(entry_id) is None:
            continue  # a stray file under meta/ that is not <skill>/<slug>.json
        found.append((entry_id, path, meta))
    return found


def _header_state(text: str) -> tuple[str, str | None]:
    """(status, superseded_by) read off an entry file's header block. A references file with no
    header, or with no Status: line, is a confirmed legacy entry (spec 6.4)."""
    status = "confirmed"
    superseded_by: str | None = None
    for line in split_header(text)[0]:
        if line.startswith("Status:"):
            value = line[len("Status:"):].strip()
            if value in _STATUSES:
                status = value
        elif line.startswith("Superseded-by:"):
            # "Superseded-by: <entry_id> (<date>)" -> the id
            superseded_by = line[len("Superseded-by:"):].strip().split(" ", 1)[0] or None
    return ("superseded" if superseded_by else status), superseded_by


def legacy_meta(entry_id: str, *, entry_type: str | None = None, status: str = "confirmed",
                superseded_by: str | None = None) -> dict:
    """A zero-count meta for an entry that never had one (spec 6.3: confirmed and legacy entries
    carry meta too, because that is where their counts live). created is the string "legacy":
    this meta was reconstructed from the git-tracked file, not written by publish.py.

    Deliberately carries no trigger / hook_trigger / component / cause keys: the index parses those
    off the entry file itself, and an empty hook_trigger here would take the entry out of hook
    matching for good (skeleton 1.7)."""
    skill, _slug = _split(entry_id)
    return {
        "schema": "sure.memory.meta.v1", "entry_id": entry_id,
        "type": entry_type or ("fact" if skill == "_shared" else "bad_case"),
        "status": status, "target_skill": skill, "applies_to": [skill],
        "created": "legacy",
        "confirmed": {"by": "human", "date": None} if status == "confirmed" else None,
        "exported": None, "superseded_by": superseded_by, "superseded_at": None,
        "checked_at": None, "entry_sha256": None,
        **usage.Counts().meta_fields(),
    }


def _create_missing_meta(repo_root: Path, memory_root: Path) -> int:
    """Give every references-only entry a meta stub so the replayed counts have somewhere to land.
    Without it the 17 old bad cases (and anything hand-committed) stay at zero in index.json for
    ever and rule 3 of spec 8.2 can never demote them. The status is read off the file header once,
    at creation; from then on meta is the authority, so editing the header back to "confirmed" does
    not undo a demotion. Returns how many were created. Callers hold the memory lock."""
    created = 0
    for entry_id, path in reference_entry_files(repo_root):
        destination = meta_path(memory_root, entry_id)
        if destination.exists():
            continue
        try:
            status, superseded_by = _header_state(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # UnicodeDecodeError is a ValueError, so a references file that is not valid UTF-8 used
            # to escape this handler and abort promote_all before it wrote anything -- one bad file
            # on a shared checkout silently disabled promotion, demotion and the count refresh for
            # the whole store. Skip it like an unreadable file: no meta, so the index leaves it at
            # zero, which is visible, rather than stopping every other entry.
            continue
        paths.atomic_write_json(destination, legacy_meta(entry_id, status=status, superseded_by=superseded_by))
        created += 1
    return created


def _entry_op(memory_root: Path, entry_id: str, meta: dict) -> str | None:
    """op comes from meta when publish recorded it, else from the provisional proposal.json."""
    op = meta.get("op")
    if isinstance(op, str):
        return op
    proposal = _load_meta(provisional_entry(memory_root, entry_id).parent / "proposal.json")
    if proposal is not None and isinstance(proposal.get("op"), str):
        return proposal["op"]
    return None


# --- the rules -------------------------------------------------------------------

def _qualifies_for_promotion(meta: dict, counts: usage.Counts, op: str | None, config: dict) -> bool:
    return (
        meta.get("status") == "provisional"
        and meta.get("type") == "bad_case"
        and op == "add"
        and counts.disputed == 0
        and counts.useful_activated >= int(config["promote_useful_activated"])
        and len(counts.useful_runs) >= int(config["promote_min_distinct_runs"])
    )


def _promote(memory_root: Path, entry_id: str, meta: dict, counts: usage.Counts) -> dict | None:
    source = provisional_entry(memory_root, entry_id)
    if not source.is_file():
        return None  # nothing to hand to cli export; leave it provisional
    today = paths.utc_today()
    # bytes + decode, not read_text: with_status can only keep the endings it is shown (keep_line_endings).
    staged = with_status(source.read_bytes().decode("utf-8"), "confirmed")
    paths.atomic_write_text(outbox_entry(memory_root, entry_id), staged)
    meta["status"] = "confirmed"
    meta["confirmed"] = {"by": "auto", "date": today}
    reason = f"useful_activated={counts.useful_activated} from runs {','.join(counts.useful_runs)}; disputed=0"
    return paths.decision_row("promote", entry_id, "auto", reason=reason,
                              from_status="provisional", to_status="confirmed", useful_runs=list(counts.useful_runs))


def _demote_streak(meta: dict, counts: usage.Counts) -> int:
    """How many disputes of the current streak may still demote this entry.

    For an auto-confirmed entry that is the whole streak: promotion and demotion are both machine
    decisions read off the same log. A human `cli confirm`, though, deliberately accepts an entry
    that is already disputed (cli.cmd_confirm), so the disputes it adjudicated must not demote it
    straight back: only disputes dated after the confirm count. Without this the very next pass
    demotes the entry with no new usage at all, deletes the outbox copy the human just staged, and
    repeating the confirm loops for ever."""
    confirmed = meta.get("confirmed")
    if not isinstance(confirmed, dict) or confirmed.get("by") != "human":
        return counts.disputed_since_last_useful
    date = confirmed.get("date")
    if not isinstance(date, str) or not date:
        # legacy_meta's stub for a git-tracked entry: by="human" only means a human committed the
        # file, no dispute was ever adjudicated, so rule 3 applies to it in full (spec 8.2).
        return counts.disputed_since_last_useful
    return sum(1 for at in counts.disputed_streak_at if at[:10] > date[:10])


def _demote(memory_root: Path, entry_id: str, meta: dict, streak: int) -> dict:
    previous = meta.get("confirmed")
    meta["status"] = "provisional"
    meta["confirmed"] = None
    shutil.rmtree(outbox_entry(memory_root, entry_id).parent, ignore_errors=True)
    if isinstance(previous, dict) and previous.get("by") == "human" and previous.get("date"):
        reason = f"disputed {streak} times since the human confirm on {previous['date']}"
    else:
        reason = f"disputed {streak} times since the last useful_activated"
    return paths.decision_row("demote", entry_id, "auto", reason=reason,
                              from_status="confirmed", to_status="provisional", previous_confirmed=previous)


def _apply_rules(memory_root: Path, entry_id: str, meta: dict, counts: usage.Counts, config: dict) -> dict | None:
    """One transition at most. Returns the decisions row for promote / demote, else None."""
    if meta.get("type") == "fact" or meta.get("status") not in ("provisional", "confirmed"):
        return None
    if meta.get("status") == "provisional":
        if _qualifies_for_promotion(meta, counts, _entry_op(memory_root, entry_id, meta), config):
            return _promote(memory_root, entry_id, meta, counts)
        if counts.disputed >= 1:
            meta["status"] = "disputed"
        return None
    streak = _demote_streak(meta, counts)
    if streak >= int(config["demote_disputed_streak"]):
        return _demote(memory_root, entry_id, meta, streak)
    return None


def promote_all(repo_root: Path, *, config: dict) -> list[dict]:
    """Refresh counts in every meta and apply the 8.2 rules. Returns the decisions rows appended
    this pass (promote / demote only). Takes the memory lock itself: callers must not hold it.

    Meta is written before the decision row is appended (matching apply_supersede): if the
    process dies between the two while still holding the lock, meta.json already carries the
    new status, so the next pass reads a non-provisional / non-confirmed-with-streak state and
    will not re-apply the same transition. A crash in that exact window can drop the audit row
    for one transition, but it can never duplicate one -- unlike the reverse order, where a
    crash after the decision row lands and before meta is written leaves the old status on
    disk, so the next pass recomputes the same qualifying counts and appends a second row for
    what was really one transition."""
    root = paths.memory_root(repo_root)
    paths.ensure_memory_tree(root)
    replayed = usage.replay_usage(root)
    appended: list[dict] = []
    with paths.memory_lock(root):
        _create_missing_meta(repo_root, root)  # references-only entries first, so this pass counts them
        for entry_id, path, meta in _iter_meta(root):
            before = dict(meta)
            counts = replayed.get(entry_id, usage.Counts())
            meta.update(counts.meta_fields())
            row = _apply_rules(root, entry_id, meta, counts, config)
            if meta != before:
                paths.atomic_write_json(path, meta)
            if row is not None:
                paths.append_decision(root, row)
                appended.append(row)
    return appended


# --- supersede -----------------------------------------------------------------

def apply_supersede(repo_root: Path, old_id: str, new_id: str, *, by: str) -> None:
    """Mark old_id as superseded by new_id: header line on every copy of the old entry
    (references, provisional, outbox), meta.status/superseded_by/superseded_at, decisions row.
    The references copy is git-tracked: this is the one place in promote.py that rewrites it, and
    the caller has to tell the human to commit it (cli.cmd_supersede prints the path).
    When the provisional entry.md is one of the rewritten copies, meta.entry_sha256 is refreshed
    to the new text (the index drops a provisional entry whose file no longer matches that hash).
    Never deletes. Raises ValueError on bad ids, FileNotFoundError when old_id has no file and no meta.
    Callers rebuild the index afterwards and must not hold the memory lock."""
    _split(new_id)
    if old_id == new_id:
        raise ValueError("an entry cannot supersede itself")
    root = paths.memory_root(repo_root)
    paths.ensure_memory_tree(root)
    date = paths.utc_today()
    with paths.memory_lock(root):
        path = meta_path(root, old_id)
        meta = _load_meta(path)
        entry_type = meta.get("type") if meta is not None and isinstance(meta.get("type"), str) else None
        files = entry_files(repo_root, old_id, entry_type)
        if not files and meta is None:
            raise FileNotFoundError(f"no entry file or meta for {old_id}")
        provisional = provisional_entry(root, old_id)
        provisional_sha: str | None = None
        for file in files:
            # bytes + decode, not read_text: universal newlines would turn a CRLF checkout's file
            # into LF before with_superseded_by ever sees it (see keep_line_endings).
            text = with_superseded_by(file.read_bytes().decode("utf-8"), new_id, date)
            paths.atomic_write_text(file, text)
            if file == provisional:
                provisional_sha = paths.sha256_text(text)  # equals sha256_file after the utf-8 write
        if meta is None:
            meta = legacy_meta(old_id, entry_type=entry_type, status="superseded", superseded_by=new_id)
        meta["status"] = "superseded"
        meta["superseded_by"] = new_id
        meta["superseded_at"] = date
        if provisional_sha is not None:
            meta["entry_sha256"] = provisional_sha
        paths.atomic_write_json(path, meta)
        paths.append_decision(root, paths.decision_row(
            "supersede", old_id, by, reason=f"superseded by {new_id}", superseded_by=new_id,
            files=[str(f.relative_to(repo_root)).replace("\\", "/") for f in files]))


# --- cli -----------------------------------------------------------------------

def _rebuild_index(repo_root: Path, config: dict) -> None:
    # Local import: promote must stay importable (and testable) without index.py on the path.
    from memory import index as index_mod

    built = index_mod.build_index(repo_root, config=config, units=paths.load_units())
    index_mod.write_index(repo_root, built, config)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Replay usage into meta and apply the promote/demote rules.")
    parser.add_argument("--repo-root", required=True, help="checkout root that holds sure/memory/")
    parser.add_argument("--no-rebuild-index", action="store_true", help="skip the index rebuild (tests)")
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    config = paths.load_config()
    try:
        rows = promote_all(repo_root, config=config)
        for row in rows:
            print(f"{row['action']}: {row['entry_id']} ({row['reason']})")
        print(f"promote: {len(rows)} decision(s)")
        if not args.no_rebuild_index:
            _rebuild_index(repo_root, config)
            print("index rebuilt")
    except OSError as exc:
        print(f"promote failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
