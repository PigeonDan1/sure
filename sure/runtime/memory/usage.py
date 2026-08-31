#!/usr/bin/env python3
"""Read the per-run usage logs and replay them into per-entry counts (spec 8.1 / 6.3).

sure/memory/usage/<run_id>.jsonl is written by the TS hooks (one file per run,
one writer). Python never edits those files and never keeps running counters:
every number in meta is recomputed from scratch here, so a torn line, a deleted
run file or a replayed run can never leave a counter out of step with the log.

Row shapes (skeleton 1.6):
  {"kind":"inject","run_id":..,"skill":..,"unit":..,"attempt":1,"events_cutoff":812,
   "entries":[{"entry_id":"sure_onboard/x","shared":false}],"at":"2026-08-18T12:00:00Z"}
  {"kind":"pre_start","run_id":..,"skill":..,"entries":[...],"at":..}
  {"kind":"settle","run_id":..,"skill":..,"unit":..,"entry_id":..,"outcome":..,"at":..}

Only "inject" rows count as injections; "pre_start" rows only move last_hit
(spec 8.2: entries that help before any gate fails do not accumulate useful).
Settle outcomes are decided by the hooks (spec 8.1); this module only sums them.

Retention: usage/ gets one file per run for ever, and replay_usage opens all of them at
every post_finish. prune_usage folds the oldest run files into usage/_archive.json and
deletes them; replay_usage folds that archive first and the surviving files on top, so a
pruned run keeps contributing exactly what its rows contributed. Nothing else reads the
archive: promote.py's promote / demote rules see only replay_usage's output, which is why
retention can be proved not to change a decision (see prune_usage).

The tolerant jsonl reader lives in paths.read_jsonl (adapted from hermes-agent
tools/skill_ledger.py, MIT, Copyright (c) 2025 Nous Research; see THIRD_PARTY.md);
_parse_jsonl_bytes is the same reader over bytes, which retention needs so it can skip a
byte prefix a previous pass already folded.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from memory import paths

OUTCOMES = ("useful_activated", "useful_unattributed", "disputed")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

ARCHIVE_NAME = "_archive.json"
ARCHIVE_SCHEMA = "sure.memory.usage_archive.v1"
# config.json key "usage_retain_runs"; publish.main passes it in. Absent -> this.
DEFAULT_USAGE_RETAIN_RUNS = 500
# Ceiling on one pass, so the first prune of a store that grew unbounded before retention
# existed drains over several runs instead of one long post_finish.
_MAX_PRUNE_PER_PASS = 500


@dataclass
class Counts:
    """Per-entry totals replayed from every usage file. Field names match meta (spec 6.3), except
    disputed_since_last_useful and disputed_streak_at, which only promote.py reads (spec 8.2 demotion)."""

    injections: int = 0
    useful_activated: int = 0
    useful_unattributed: int = 0
    useful_runs: list[str] = field(default_factory=list)
    disputed: int = 0
    last_hit: str | None = None
    disputed_since_last_useful: int = 0
    # "at" of every dispute in the current streak, in replay order (== len(disputed_since_last_useful)).
    # promote.py needs the timestamps, not just the count: a human `cli confirm` adjudicates the
    # disputes that already happened, so only the ones after that confirm may demote the entry again.
    disputed_streak_at: list[str] = field(default_factory=list)

    def meta_fields(self) -> dict:
        """The subset promote.py copies into meta (the two streak fields stay out)."""
        data = asdict(self)
        data.pop("disputed_since_last_useful")
        data.pop("disputed_streak_at")
        return data

    def copy(self) -> Counts:
        """A private copy, lists included: replay_usage seeds from the archive and must not
        fold this run's rows into the object it read off disk."""
        return Counts(
            injections=self.injections,
            useful_activated=self.useful_activated,
            useful_unattributed=self.useful_unattributed,
            useful_runs=list(self.useful_runs),
            disputed=self.disputed,
            last_hit=self.last_hit,
            disputed_since_last_useful=self.disputed_since_last_useful,
            disputed_streak_at=list(self.disputed_streak_at),
        )


@dataclass
class Archive:
    """usage/_archive.json: the folded counts of runs whose raw rows prune_usage dropped.

    pending maps a run id to the exact bytes of its file that are already folded
    ({"bytes": n, "sha256": hex}). It is empty in the steady state and only carries a run
    between the archive write and the delete that follows it, so a kill in that window
    cannot make replay_usage count those rows twice.

    scanned is how many run files the last pass that opened the store left behind. prune_usage
    reads it to decide whether opening the store again is worth it yet."""

    entries: dict[str, Counts] = field(default_factory=dict)
    pending: dict[str, dict] = field(default_factory=dict)
    folded_runs: int = 0
    through: str = ""
    scanned: int = 0


def usage_dir(memory_root: Path) -> Path:
    return Path(memory_root) / "usage"


def archive_path(memory_root: Path) -> Path:
    # ".json", not ".jsonl": the run-file glob below never picks it up.
    return usage_dir(memory_root) / ARCHIVE_NAME


def _nonneg_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _str_list(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _counts_from_json(value: dict) -> Counts:
    last_hit = value.get("last_hit")
    return Counts(
        injections=_nonneg_int(value.get("injections")),
        useful_activated=_nonneg_int(value.get("useful_activated")),
        useful_unattributed=_nonneg_int(value.get("useful_unattributed")),
        useful_runs=_str_list(value.get("useful_runs")),
        disputed=_nonneg_int(value.get("disputed")),
        last_hit=last_hit if isinstance(last_hit, str) and last_hit else None,
        disputed_since_last_useful=_nonneg_int(value.get("disputed_since_last_useful")),
        disputed_streak_at=_str_list(value.get("disputed_streak_at")),
    )


def _read_archive_json(path: Path) -> dict | None:
    """The archive object, or None when it is absent, unparsable or not this schema."""
    try:
        obj = paths.load_json(path)
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) and obj.get("schema") == ARCHIVE_SCHEMA else None


def load_archive(memory_root: Path) -> Archive:
    """The folded counts, or an empty Archive when there is nothing (or nothing readable) there.

    Replay must not raise on bad data, so an unreadable archive replays as no archive at all.
    prune_usage checks for that case itself and refuses to prune, rather than folding a second
    batch of runs into a file whose earlier counts can no longer be read."""
    obj = _read_archive_json(archive_path(memory_root))
    if obj is None:
        return Archive()
    entries: dict[str, Counts] = {}
    raw_entries = obj.get("entries")
    if isinstance(raw_entries, dict):
        for entry_id, value in raw_entries.items():
            if isinstance(entry_id, str) and entry_id and isinstance(value, dict):
                entries[entry_id] = _counts_from_json(value)
    pending: dict[str, dict] = {}
    raw_pending = obj.get("pending")
    if isinstance(raw_pending, dict):
        for run_id, marker in raw_pending.items():
            if isinstance(run_id, str) and _safe_run_id(run_id) and isinstance(marker, dict):
                pending[run_id] = marker
    through = obj.get("through")
    return Archive(
        entries=entries, pending=pending,
        folded_runs=_nonneg_int(obj.get("folded_runs")),
        through=through if isinstance(through, str) else "",
        scanned=_nonneg_int(obj.get("scanned")),
    )


def _parse_jsonl_bytes(raw: bytes) -> tuple[list[dict], int]:
    """(rows, skipped) with paths.read_jsonl's tolerance, over bytes rather than a path:
    a file whose folded prefix is skipped is read from the middle, not from disk again.

    Same reader as paths.read_jsonl, so same attribution: hermes-agent tools/skill_ledger.py,
    MIT, Copyright (c) 2025 Nous Research (see THIRD_PARTY.md)."""
    rows: list[dict] = []
    skipped = 0
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if isinstance(obj, dict):
            rows.append(obj)
        else:
            skipped += 1
    return rows, skipped


def _folded_prefix(raw: bytes, marker: dict | None) -> int:
    """How many leading bytes of `raw` a previous prune already folded into the archive.

    The marker names the length AND the sha256 of what was folded, so a run file that was
    deleted and then written again under the same run id (a resumed run, a replayed run) is
    recognised as a different file and replayed whole instead of losing its first rows."""
    if not isinstance(marker, dict):
        return 0
    size, digest = marker.get("bytes"), marker.get("sha256")
    if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= len(raw):
        return 0
    if not isinstance(digest, str) or hashlib.sha256(raw[:size]).hexdigest() != digest:
        return 0
    return size


@dataclass
class _RunFile:
    """One usage/<run_id>.jsonl as retention sees it: the rows that are NOT folded yet,
    plus the byte identity of the whole file so a fold can be marked precisely.

    min_at / max_at are the "at" bounds of those rows in load_all_usage's sort order; a row
    with no usable "at" sorts first there, so it makes min_at the empty string."""

    path: Path
    rows: list[dict]
    bad: int
    size: int
    sha256: str
    min_at: str
    max_at: str


def _run_file(path: Path, marker: dict | None) -> _RunFile | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    rows, bad = _parse_jsonl_bytes(raw[_folded_prefix(raw, marker):])
    stamps: list[str] = []
    undated = False
    for row in rows:
        if not isinstance(row.get("run_id"), str):
            row["run_id"] = path.stem
        at = row.get("at")
        if isinstance(at, str) and at:
            stamps.append(at)
        else:
            undated = True
    return _RunFile(
        path=path, rows=rows, bad=bad, size=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
        min_at="" if undated or not stamps else min(stamps),
        max_at=max(stamps) if stamps else "",
    )


def _safe_run_id(run_id: str) -> bool:
    # A run id is a directory basename; anything with separators or dots-only must not
    # be joined onto the usage dir (cli takes run ids from users, hooks from ctx.runDir).
    return bool(run_id) and _RUN_ID_RE.match(run_id) is not None and run_id not in (".", "..")


def load_run_usage(memory_root: Path, run_id: str) -> list[dict]:
    """Rows of one run's usage file in file order; [] when the file is missing or the id is unsafe."""
    if not isinstance(run_id, str) or not _safe_run_id(run_id):
        return []
    rows, _bad = paths.read_jsonl(usage_dir(memory_root) / f"{run_id}.jsonl")
    for row in rows:
        row.setdefault("run_id", run_id)
    return rows


def _ordered_rows(files: list[_RunFile]) -> list[dict]:
    """Rows of the given files in replay order: by "at", ties by file name then line.
    Rows without a usable "at" sort first so they still count but never disturb the streak."""
    keyed: list[tuple[str, int, dict]] = []
    seq = 0
    for info in sorted(files, key=lambda item: item.path.name):
        for row in info.rows:
            at = row.get("at")
            keyed.append((at if isinstance(at, str) else "", seq, row))
            seq += 1
    keyed.sort(key=lambda item: (item[0], item[1]))
    return [row for _at, _seq, row in keyed]


def load_all_usage(memory_root: Path, *, skip: dict[str, dict] | None = None) -> tuple[list[dict], int]:
    """(rows, bad_lines): every not-yet-archived row of every usage file, in time order.

    A row without run_id gets the file's stem. `skip` is the archive's pending map; the
    default reads it, so a caller that does not know about retention still never sees a row
    the archive has already folded."""
    directory = usage_dir(memory_root)
    if not directory.is_dir():
        return [], 0
    if skip is None:
        skip = load_archive(memory_root).pending
    files: list[_RunFile] = []
    bad_total = 0
    for path in sorted(directory.glob("*.jsonl")):
        info = _run_file(path, skip.get(path.stem))
        if info is None:
            continue
        files.append(info)
        bad_total += info.bad
    return _ordered_rows(files), bad_total


def _entry_ids(row: dict) -> list[str]:
    entries = row.get("entries")
    if not isinstance(entries, list):
        return []
    ids: list[str] = []
    for item in entries:
        if isinstance(item, dict) and isinstance(item.get("entry_id"), str) and item["entry_id"]:
            ids.append(item["entry_id"])
    return ids


def _touch(counts: Counts, at: object) -> None:
    if isinstance(at, str) and at and (counts.last_hit is None or at > counts.last_hit):
        counts.last_hit = at


def replay_usage(memory_root: Path) -> dict[str, Counts]:
    """Fold the usage archive and then every usage row into per-entry Counts. Unknown kinds,
    malformed rows and unknown outcomes are skipped; nothing here raises on bad data."""
    archive = load_archive(memory_root)
    rows, _bad = load_all_usage(memory_root, skip=archive.pending)
    result = {entry_id: counts.copy() for entry_id, counts in archive.entries.items()}
    _fold(result, rows)
    return result


def _fold(result: dict[str, Counts], rows: list[dict]) -> None:
    """Fold rows, already in replay order, into `result` in place."""
    for row in rows:
        kind = row.get("kind")
        if kind == "inject":
            for entry_id in _entry_ids(row):
                counts = result.setdefault(entry_id, Counts())
                counts.injections += 1
                _touch(counts, row.get("at"))
        elif kind == "pre_start":
            for entry_id in _entry_ids(row):
                _touch(result.setdefault(entry_id, Counts()), row.get("at"))
        elif kind == "settle":
            entry_id = row.get("entry_id")
            outcome = row.get("outcome")
            if not isinstance(entry_id, str) or not entry_id or outcome not in OUTCOMES:
                continue
            counts = result.setdefault(entry_id, Counts())
            _touch(counts, row.get("at"))
            if outcome == "useful_activated":
                counts.useful_activated += 1
                counts.disputed_since_last_useful = 0
                counts.disputed_streak_at.clear()
                run_id = row.get("run_id")
                if isinstance(run_id, str) and run_id not in counts.useful_runs:
                    counts.useful_runs.append(run_id)
            elif outcome == "useful_unattributed":
                counts.useful_unattributed += 1
            else:
                counts.disputed += 1
                counts.disputed_since_last_useful += 1
                at = row.get("at")
                counts.disputed_streak_at.append(at if isinstance(at, str) else "")


# --- retention ------------------------------------------------------------------


@dataclass
class PruneReport:
    """What one prune_usage pass did. errors never carry a host path (the hooks read them)."""

    pruned: list[str] = field(default_factory=list)  # run ids whose file was folded and removed
    kept: int = 0                                    # run files still on disk afterwards
    errors: list[str] = field(default_factory=list)


def _prefix_cut(candidates: list[_RunFile], retained: list[_RunFile]) -> int:
    """How many of `candidates` (oldest first) may be folded into the archive.

    replay_usage folds the archive as one block and then the surviving rows on top, so the
    fold is only exact while every archived row sorts before every row still on disk. Every
    count except the dispute streak is additive and would not care, but the streak does:
    a dispute folded after a useful_activated that really came later would survive a reset
    it should not have survived, and demote an entry the log does not demote.

    That is a condition on the boundary and on nothing else. A pass folds its candidates with
    one _ordered_rows call, the same sort load_all_usage uses, so candidates that overlap each
    other reach the archive in true order anyway; only rows crossing the boundary can swap. So
    the largest k whose newest candidate row is strictly older than the oldest row left behind
    -- candidates[k:] and retained alike -- is exact, and this returns it. Judging each
    candidate against the ones after it as well would wall the store off: an overlapping pair
    blocks itself for ever, so nothing older than it is ever folded either.

    What still cannot be folded is a run that reaches into the window that stays: a long run
    overlapping the newest ones, and (because a row with no usable "at" replays first, i.e. as
    the empty string) any pass where a surviving file holds an undated row. Both resolve
    themselves once those files age out of the retained window."""
    # prefix_max[k] is the newest row in candidates[:k]. A file with no rows does not move it,
    # and is tracked apart from it: a file whose rows are all undated has a max of "" as well,
    # and must still be judged against the boundary, because "" is where an undated row sorts.
    prefix_max = [""] * (len(candidates) + 1)
    prefix_rows = [False] * (len(candidates) + 1)
    for i, info in enumerate(candidates):
        prefix_max[i + 1] = max(prefix_max[i], info.max_at) if info.rows else prefix_max[i]
        prefix_rows[i + 1] = prefix_rows[i] or bool(info.rows)
    suffix_min = min((info.min_at for info in retained if info.rows), default=None)
    for k in range(len(candidates), 0, -1):
        if suffix_min is None or not prefix_rows[k] or prefix_max[k] < suffix_min:
            return k
        info = candidates[k - 1]
        if info.rows:
            suffix_min = min(suffix_min, info.min_at)
    return 0


def _archive_payload(entries: dict[str, Counts], pending: dict[str, dict], folded_runs: int,
                     through: str, scanned: int) -> dict:
    """The usage/_archive.json object. scanned is how many run files the pass writing it leaves
    behind, which is what the next pass measures its drift against."""
    return {
        "schema": ARCHIVE_SCHEMA,
        "folded_runs": folded_runs,
        "through": through,
        "scanned": scanned,
        "pending": pending,
        "entries": {entry_id: asdict(counts) for entry_id, counts in sorted(entries.items())},
    }


# run.json statuses after which no usage row can arrive any more (types.ts SureRunStatus).
_TERMINAL_RUN_STATUSES = frozenset({"success", "failed", "incomplete", "cancelled"})


def _in_flight(runs_root: Path | None, run_id: str) -> bool:
    """True while .sure/runs/<run_id>/run.json says the run has not reached a terminal status.

    Not knowing counts as not in flight: no runs_root, no run directory, an unreadable run.json.
    .sure/runs is cleared by hand and a run started from another cwd records itself elsewhere, so
    reading "no record" as alive would stop the store shrinking for ever, which is the cost
    retention exists to remove. A killed run stays at "running" and keeps its file, and that is
    the safe direction to be wrong in.

    Two known ceilings. sure/memory is one store per checkout while .sure/runs follows
    record.cwd, so a run in flight from another cwd is invisible here. And a failed run counts as
    terminal but can be resumed under the same run id, which brings the old problem back for as
    long as it then runs."""
    if runs_root is None:
        return False
    try:
        record = paths.load_json(Path(runs_root) / run_id / "run.json")
    except (OSError, ValueError):
        return False
    return isinstance(record, dict) and record.get("status") not in _TERMINAL_RUN_STATUSES


def prune_usage(memory_root: Path, *, retain: int, runs_root: Path | None = None) -> PruneReport:
    """Fold everything past the newest `retain` run files into usage/_archive.json and delete it.

    A pass only starts once the store has drifted a tenth of `retain` past the line, so the
    store is bounded by retain + that slack rather than by retain exactly.

    Why this cannot change a promotion or a demotion. promote.py reads nothing but
    replay_usage's Counts, and replay_usage returns the archive folded first with the
    surviving rows folded on top. Every count is either additive over rows (injections,
    useful_activated, useful_unattributed, disputed), a set union in first-seen order
    (useful_runs -- the distinct runs promotion counts), a maximum (last_hit), or the
    dispute streak, which is order-sensitive and is why _prefix_cut only folds runs that
    sort strictly before everything left on disk. So replay_usage returns the same Counts
    before and after a pass, and the rules read the same numbers.

    Two conditions can still lose a count. One is an unreadable usage/_archive.json: a pass
    that finds one stops instead of folding a second batch into it. The other is a row
    appended to a candidate file after this pass read it -- match.ts appends without the
    memory lock -- which the delete below guards by unlinking only a file whose size still
    matches what was folded. That stat and that unlink are two steps, so a writer landing
    between them loses its row: the stat saw the old size and the unlink takes the file as it
    is now. Only the run that owns a file appends to it, which is why `runs_root` matters --
    given it, the delete skips a run that is still in flight and no writer is left to race.
    Without it the window is exactly as wide as it was before, and the size check is all
    that guards it.

    Crash safety follows publish.py's _reclaim_orphans: the durable record is written
    first, the cleanup second, and the reader is told to ignore what the record already
    covers. The archive write is the commit point; it names, per run, the exact bytes it
    folded, so between it and the delete replay_usage skips those bytes and counts each row
    once. A kill anywhere leaves either "not folded, files present" or "folded, files
    present but marked" -- never "folded and forgotten". Nothing here writes meta/, entry
    files or index.json, so build_index is not affected at all.

    Takes the memory lock itself: callers must not hold it."""
    report = PruneReport()
    directory = usage_dir(memory_root)
    if not directory.is_dir():
        return report
    retain = max(0, int(retain))
    with paths.memory_lock(memory_root):
        archive_file = archive_path(memory_root)
        if archive_file.exists() and _read_archive_json(archive_file) is None:
            report.kept = len(list(directory.glob("*.jsonl")))
            report.errors.append(
                f"usage/{ARCHIVE_NAME} is unreadable; pruning stopped so the counts it holds are not lost"
            )
            return report
        archive = load_archive(memory_root)
        present = sorted(directory.glob("*.jsonl"))
        report.kept = len(present)
        # Only a file whose stem is a safe run id may be folded. load_archive drops any other
        # key from pending, because prune_usage joins that key back onto usage/ to check whether
        # the file is still there, so folding one writes a marker the next read throws away: the
        # rows land in the archive, the file is still on disk with nothing to skip its bytes, and
        # replay counts them twice. Left alone it keeps replaying exactly once, as it does now.
        # It is excluded from the drift check too, so a file that can never be folded cannot hold
        # the store over the line and make every post_finish read the whole thing.
        files = [path for path in present if _safe_run_id(path.stem)]
        # Hysteresis. A pass has to open every remaining file to find the boundary
        # _prefix_cut needs, so trimming one file per run would cost about what the replay it is
        # meant to cheapen costs. Wait until the store has drifted a tenth of the retention count
        # past it and then take the whole batch: below that line the pass is one glob, the store
        # stays bounded by retain + slack, and the read is amortised over slack runs.
        slack = max(1, retain // 10)
        if len(files) <= retain + slack:
            return report
        # Same argument for a pass that opened the store and folded nothing, which is what
        # _prefix_cut returns while a run that is still on disk spans every cut point. That store
        # is not shrinking, so the first test above stays true and every post_finish would read
        # all of it for nothing -- the cost retention exists to remove, now paid for ever. Wait
        # for the same drift measured from what the last pass to look actually left behind.
        if archive.scanned and len(files) < archive.scanned + slack:
            return report
        infos = [info for info in (_run_file(path, archive.pending.get(path.stem)) for path in files) if info is not None]
        ordered = sorted(infos, key=lambda info: (info.max_at, info.path.name))
        candidates = ordered[: max(0, len(ordered) - retain)][:_MAX_PRUNE_PER_PASS]
        retained = ordered[len(candidates):]
        candidates = candidates[: _prefix_cut(candidates, retained)]
        pending = {run: marker for run, marker in archive.pending.items() if (directory / f"{run}.jsonl").exists()}
        if not candidates:
            # Nothing could be folded, but the whole store was read to find that out. Record what
            # it saw, so the next post_finish can skip the read instead of repeating it, and leave
            # every count exactly as it was.
            paths.atomic_write_json(archive_file, _archive_payload(
                archive.entries, pending, archive.folded_runs, archive.through, len(files)))
            return report

        entries = {entry_id: counts.copy() for entry_id, counts in archive.entries.items()}
        _fold(entries, _ordered_rows(candidates))
        for info in candidates:
            pending[info.path.stem] = {"bytes": info.size, "sha256": info.sha256}
        payload = _archive_payload(
            entries, pending,
            archive.folded_runs + len(candidates),
            max([archive.through, *(info.max_at for info in candidates)]),
            0,  # a pass that folded is worth repeating at once: _MAX_PRUNE_PER_PASS caps a
                # backlog into batches, and waiting out the drift between them would leave a
                # store that grew before retention existed slow for slack runs per batch.
        )
        paths.atomic_write_json(archive_file, payload)  # commit point: after this the rows are folded

        for info in candidates:
            try:
                # Delete the file only while it is still the one that was folded, and never while
                # its run is in flight. hooks.ts settleOnPass reads the inject row's events_cutoff
                # out of that file (injectCutoffs) and records nothing at all without it, while
                # settleOnTerminalFailure needs no cutoff and lands its dispute either way, so a
                # live file that goes takes only the benefit with it. The run is left out of the
                # delete and not out of the fold on purpose: an unfoldable file at the old end of
                # the window would raise the boundary every other candidate has to clear, and one
                # run stuck at "running" would stop the whole store from ever shrinking again.
                # match.ts appends usage rows with appendFileSync and takes no lock, so a run that
                # is still writing can extend a file between the read above and here; the archive
                # covers the bytes the marker names and nothing past them, so unlink would destroy
                # rows that exist in no archive. Either file is left where it is instead: replay
                # skips its folded prefix and counts the tail, and a later pass folds the tail and
                # deletes it. Not an error -- nothing failed and nothing was lost.
                if _in_flight(runs_root, info.path.stem) or info.path.stat().st_size != info.size:
                    continue
                info.path.unlink()
            except OSError as exc:
                # The run id is not a host path (match.ts names usage files the same way).
                report.errors.append(f"usage/{info.path.name}: {exc.__class__.__name__}")
                continue
            report.pruned.append(info.path.stem)
        left = {run: marker for run, marker in pending.items() if (directory / f"{run}.jsonl").exists()}
        if left != pending:
            payload["pending"] = left
            paths.atomic_write_json(archive_file, payload)
        report.kept = len(list(directory.glob("*.jsonl")))
    return report
