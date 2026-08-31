from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # sure/runtime

from memory import paths, usage  # noqa: E402

X = "sure_onboard/no-kernel-image"
Y = "sure_onboard/cuda-arch-list"
F = "_shared/vc-partition-names"


def inject(run_id: str, unit: str, at: str, *entry_ids: str, attempt: int = 1) -> dict:
    """An inject row shaped like the ones match.ts appends (skeleton 1.6)."""
    return {
        "kind": "inject", "run_id": run_id, "skill": "sure_onboard", "unit": unit, "attempt": attempt,
        "events_cutoff": 812, "at": at,
        "entries": [{"entry_id": e, "shared": e.startswith("_shared/")} for e in entry_ids],
    }


def pre_start(run_id: str, at: str, *entry_ids: str) -> dict:
    return {
        "kind": "pre_start", "run_id": run_id, "skill": "sure_eval", "at": at,
        "entries": [{"entry_id": e, "shared": e.startswith("_shared/")} for e in entry_ids],
    }


def settle(run_id: str, unit: str, entry_id: str, outcome: str, at: str) -> dict:
    return {"kind": "settle", "run_id": run_id, "skill": "sure_onboard", "unit": unit,
            "entry_id": entry_id, "outcome": outcome, "at": at}


def write_usage(root: Path, run_id: str, rows: list[dict]) -> Path:
    path = root / "usage" / f"{run_id}.jsonl"
    for row in rows:
        paths.append_jsonl(path, row, 4096)
    return path


class UsageReplayBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "sure" / "memory"
        paths.ensure_memory_tree(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()


class ReplayCountsTests(UsageReplayBase):
    def test_inject_row_counts_one_injection_per_listed_entry(self) -> None:
        write_usage(self.root, "run-a", [inject("run-a", "build_env", "2026-08-18T10:00:00Z", X, Y)])
        counts = usage.replay_usage(self.root)
        self.assertEqual(counts[X].injections, 1)
        self.assertEqual(counts[Y].injections, 1)
        self.assertEqual(counts[X].useful_activated, 0)
        self.assertEqual(counts[X].last_hit, "2026-08-18T10:00:00Z")

    def test_settle_rows_map_to_the_three_outcomes(self) -> None:
        write_usage(self.root, "run-a", [
            inject("run-a", "build_env", "2026-08-18T10:00:00Z", X, Y),
            settle("run-a", "build_env", X, "useful_activated", "2026-08-18T10:05:00Z"),
            settle("run-a", "build_env", Y, "useful_unattributed", "2026-08-18T10:05:00Z"),
        ])
        write_usage(self.root, "run-b", [
            inject("run-b", "build_env", "2026-08-18T11:00:00Z", X),
            settle("run-b", "build_env", X, "disputed", "2026-08-18T11:30:00Z"),
        ])
        counts = usage.replay_usage(self.root)
        self.assertEqual((counts[X].useful_activated, counts[X].useful_unattributed, counts[X].disputed), (1, 0, 1))
        self.assertEqual((counts[Y].useful_activated, counts[Y].useful_unattributed, counts[Y].disputed), (0, 1, 0))
        self.assertEqual(counts[X].injections, 2)

    def test_an_abandoned_settle_changes_no_counter(self) -> None:
        # "abandoned" is deliberately absent from OUTCOMES: _fold skips the row whole, so giving up
        # on a unit can neither block auto-promotion nor demote a confirmed entry.
        write_usage(self.root, "run-a", [
            inject("run-a", "build_env", "2026-08-18T10:00:00Z", X),
            settle("run-a", "build_env", X, "abandoned", "2026-08-18T10:05:00Z"),
        ])
        counts = usage.replay_usage(self.root)
        self.assertEqual(counts[X].injections, 1)
        self.assertEqual(counts[X].useful_activated, 0)
        self.assertEqual(counts[X].useful_unattributed, 0)
        self.assertEqual(counts[X].disputed, 0)
        self.assertEqual(counts[X].disputed_since_last_useful, 0)
        self.assertEqual(counts[X].useful_runs, [])

    def test_useful_runs_are_distinct_run_ids_of_activated_settles(self) -> None:
        write_usage(self.root, "run-a", [
            inject("run-a", "build_env", "2026-08-18T10:00:00Z", X),
            settle("run-a", "build_env", X, "useful_activated", "2026-08-18T10:05:00Z"),
            inject("run-a", "validate_infer", "2026-08-18T10:10:00Z", X),
            settle("run-a", "validate_infer", X, "useful_activated", "2026-08-18T10:15:00Z"),
        ])
        write_usage(self.root, "run-b", [
            inject("run-b", "build_env", "2026-08-18T11:00:00Z", X),
            settle("run-b", "build_env", X, "useful_unattributed", "2026-08-18T11:05:00Z"),
        ])
        counts = usage.replay_usage(self.root)
        self.assertEqual(counts[X].useful_activated, 2)
        self.assertEqual(counts[X].useful_runs, ["run-a"])  # run-b only had an unattributed pass
        write_usage(self.root, "run-c", [
            inject("run-c", "build_env", "2026-08-18T12:00:00Z", X),
            settle("run-c", "build_env", X, "useful_activated", "2026-08-18T12:05:00Z"),
        ])
        self.assertEqual(usage.replay_usage(self.root)[X].useful_runs, ["run-a", "run-c"])

    def test_last_hit_is_the_latest_timestamp_across_kinds(self) -> None:
        write_usage(self.root, "run-a", [
            inject("run-a", "build_env", "2026-08-18T10:00:00Z", X),
            settle("run-a", "build_env", X, "useful_activated", "2026-08-18T10:05:00Z"),
        ])
        write_usage(self.root, "run-b", [pre_start("run-b", "2026-08-19T09:00:00Z", X, F)])
        counts = usage.replay_usage(self.root)
        self.assertEqual(counts[X].last_hit, "2026-08-19T09:00:00Z")
        self.assertEqual(counts[F].last_hit, "2026-08-19T09:00:00Z")

    def test_pre_start_rows_do_not_count_as_injections(self) -> None:
        write_usage(self.root, "run-b", [pre_start("run-b", "2026-08-19T09:00:00Z", F)])
        counts = usage.replay_usage(self.root)
        self.assertEqual(counts[F].injections, 0)
        self.assertEqual(counts[F].last_hit, "2026-08-19T09:00:00Z")

    def test_disputed_streak_follows_time_order_not_file_order(self) -> None:
        # File "run-a" (sorted first) holds the LATER events; ordering must come from "at".
        write_usage(self.root, "run-a", [
            inject("run-a", "build_env", "2026-08-20T10:00:00Z", X),
            settle("run-a", "build_env", X, "disputed", "2026-08-20T10:30:00Z"),
            inject("run-a", "validate_infer", "2026-08-20T11:00:00Z", X),
            settle("run-a", "validate_infer", X, "disputed", "2026-08-20T11:30:00Z"),
        ])
        write_usage(self.root, "run-b", [
            inject("run-b", "build_env", "2026-08-18T10:00:00Z", X),
            settle("run-b", "build_env", X, "disputed", "2026-08-18T10:30:00Z"),
            inject("run-b", "validate_infer", "2026-08-18T11:00:00Z", X),
            settle("run-b", "validate_infer", X, "useful_activated", "2026-08-18T11:30:00Z"),
        ])
        counts = usage.replay_usage(self.root)
        self.assertEqual(counts[X].disputed, 3)
        self.assertEqual(counts[X].disputed_since_last_useful, 2)

    def test_useful_unattributed_does_not_reset_the_disputed_streak(self) -> None:
        write_usage(self.root, "run-a", [
            settle("run-a", "build_env", X, "disputed", "2026-08-18T10:00:00Z"),
            settle("run-a", "validate_infer", X, "useful_unattributed", "2026-08-18T11:00:00Z"),
            settle("run-a", "validate_load", X, "disputed", "2026-08-18T12:00:00Z"),
        ])
        self.assertEqual(usage.replay_usage(self.root)[X].disputed_since_last_useful, 2)

    def test_settle_without_run_id_uses_the_file_name(self) -> None:
        row = settle("ignored", "build_env", X, "useful_activated", "2026-08-18T10:05:00Z")
        del row["run_id"]
        write_usage(self.root, "run-a", [row])
        self.assertEqual(usage.replay_usage(self.root)[X].useful_runs, ["run-a"])

    def test_bad_lines_and_malformed_rows_are_skipped(self) -> None:
        path = write_usage(self.root, "run-a", [
            inject("run-a", "build_env", "2026-08-18T10:00:00Z", X),
            {"kind": "settle", "run_id": "run-a", "entry_id": X, "outcome": "maybe", "at": "2026-08-18T10:01:00Z"},
            {"kind": "settle", "run_id": "run-a", "entry_id": 42, "outcome": "disputed", "at": "2026-08-18T10:02:00Z"},
            {"kind": "inject", "run_id": "run-a", "entries": "not-a-list", "at": "2026-08-18T10:03:00Z"},
            {"kind": "inject", "run_id": "run-a", "entries": [{"entry_id": None}, "x"], "at": "2026-08-18T10:03:30Z"},
            {"kind": "unknown", "run_id": "run-a", "entry_id": X, "at": "2026-08-18T10:04:00Z"},
            {"run_id": "run-a", "entry_id": X, "outcome": "disputed", "at": "2026-08-18T10:04:30Z"},
            settle("run-a", "build_env", X, "useful_activated", "2026-08-18T10:05:00Z"),
        ])
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"kind": "settle", "entry_id": "' + X + '", "outcome": "disputed", "at": "2026-08-18T10:06:00Z"\n')
            handle.write("[1, 2, 3]\n")
        counts = usage.replay_usage(self.root)
        self.assertEqual(counts[X].injections, 1)
        self.assertEqual((counts[X].useful_activated, counts[X].disputed), (1, 0))
        rows, bad = usage.load_all_usage(self.root)
        self.assertEqual(bad, 2)
        self.assertEqual(len(rows), 8)

    def test_empty_or_missing_usage_dir_replays_to_nothing(self) -> None:
        self.assertEqual(usage.replay_usage(self.root), {})
        self.assertEqual(usage.replay_usage(self.root / "nope"), {})
        self.assertEqual(usage.load_all_usage(self.root / "nope"), ([], 0))

    def test_meta_fields_drop_the_streak(self) -> None:
        counts = usage.Counts(injections=3, useful_activated=2, useful_runs=["a", "b"], disputed=1,
                              last_hit="2026-08-18T10:00:00Z", disputed_since_last_useful=1)
        self.assertEqual(counts.meta_fields(), {
            "injections": 3, "useful_activated": 2, "useful_unattributed": 0, "useful_runs": ["a", "b"],
            "disputed": 1, "last_hit": "2026-08-18T10:00:00Z",
        })


class ScenarioTests(UsageReplayBase):
    """The 8.1 story replayed end to end: what the hooks log, what the counts must say."""

    def test_spec_8_1_story(self) -> None:
        # run A: build_env blocked once, both entries injected, agent read only X, unit passed.
        write_usage(self.root, "run-a", [
            inject("run-a", "build_env", "2026-08-18T10:00:00Z", X, Y),
            settle("run-a", "build_env", X, "useful_activated", "2026-08-18T10:20:00Z"),
            settle("run-a", "build_env", Y, "useful_unattributed", "2026-08-18T10:20:00Z"),
        ])
        # run B: blocked three times on build_env; X injected once (dedup), pending twice, then passed.
        write_usage(self.root, "run-b", [
            inject("run-b", "build_env", "2026-08-19T10:00:00Z", X),
            settle("run-b", "build_env", X, "useful_activated", "2026-08-19T10:40:00Z"),
        ])
        # run C: X injected, retries exhausted with the trigger still in the repair -> disputed.
        write_usage(self.root, "run-c", [
            inject("run-c", "build_env", "2026-08-20T10:00:00Z", X),
            settle("run-c", "build_env", X, "disputed", "2026-08-20T10:30:00Z"),
        ])
        # run D: agent only cat'ed the log, no gate result -> nothing logged for that unit.
        write_usage(self.root, "run-d", [pre_start("run-d", "2026-08-21T09:00:00Z", F)])
        counts = usage.replay_usage(self.root)
        self.assertEqual(counts[X].injections, 3)
        self.assertEqual(counts[X].useful_activated, 2)
        self.assertEqual(counts[X].useful_runs, ["run-a", "run-b"])
        self.assertEqual(counts[X].disputed, 1)
        self.assertEqual(counts[X].disputed_since_last_useful, 1)
        self.assertEqual(counts[X].last_hit, "2026-08-20T10:30:00Z")
        self.assertEqual(counts[Y].injections, 1)
        self.assertEqual(counts[Y].useful_unattributed, 1)
        self.assertEqual(counts[Y].useful_runs, [])
        self.assertEqual(counts[F].injections, 0)
        self.assertNotIn("run-d", counts[X].useful_runs)


class LoadRunUsageTests(UsageReplayBase):
    def test_returns_rows_of_that_run_in_file_order(self) -> None:
        write_usage(self.root, "run-a", [
            inject("run-a", "build_env", "2026-08-18T10:00:00Z", X),
            settle("run-a", "build_env", X, "useful_activated", "2026-08-18T10:05:00Z"),
        ])
        write_usage(self.root, "run-b", [inject("run-b", "build_env", "2026-08-18T11:00:00Z", Y)])
        rows = usage.load_run_usage(self.root, "run-a")
        self.assertEqual([r["kind"] for r in rows], ["inject", "settle"])
        self.assertEqual(rows[0]["entries"][0]["entry_id"], X)

    def test_missing_run_file_is_empty(self) -> None:
        self.assertEqual(usage.load_run_usage(self.root, "run-zzz"), [])

    def test_unsafe_run_ids_are_refused(self) -> None:
        for bad in ("../decisions", "a/b", "..", "", "run a"):
            with self.subTest(bad=bad):
                self.assertEqual(usage.load_run_usage(self.root, bad), [])

    def test_row_without_run_id_gets_the_requested_one(self) -> None:
        row = inject("x", "build_env", "2026-08-18T10:00:00Z", X)
        del row["run_id"]
        write_usage(self.root, "run-a", [row])
        self.assertEqual(usage.load_run_usage(self.root, "run-a")[0]["run_id"], "run-a")


def archive_entry(**over) -> dict:
    """One usage/_archive.json entries[] value (the asdict() of a Counts)."""
    row = {
        "injections": 0, "useful_activated": 0, "useful_unattributed": 0, "useful_runs": [],
        "disputed": 0, "last_hit": None, "disputed_since_last_useful": 0, "disputed_streak_at": [],
    }
    row.update(over)
    return row


def write_archive(root: Path, entries: dict, *, pending: dict | None = None, through: str = "") -> None:
    paths.atomic_write_json(usage.archive_path(root), {
        "schema": usage.ARCHIVE_SCHEMA,
        "folded_runs": len(entries),
        "through": through,
        "pending": pending or {},
        "entries": entries,
    })


class ArchiveReplayTests(UsageReplayBase):
    """usage/_archive.json carries the counts of runs whose raw rows are gone (retention).

    It is folded before the rows still on disk, so a pruned run keeps contributing exactly
    what its rows contributed: promote.py reads nothing but replay_usage."""

    def test_archived_counts_are_added_to_the_rows_still_on_disk(self) -> None:
        write_archive(self.root, {X: archive_entry(
            injections=4, useful_activated=2, useful_unattributed=1,
            useful_runs=["run-old-1", "run-old-2"], last_hit="2026-08-01T10:00:00Z",
        )}, through="2026-08-01T10:00:00Z")
        write_usage(self.root, "run-new", [
            inject("run-new", "build_env", "2026-08-18T10:00:00Z", X),
            settle("run-new", "build_env", X, "useful_activated", "2026-08-18T10:05:00Z"),
        ])
        counts = usage.replay_usage(self.root)
        self.assertEqual(counts[X].injections, 5)
        self.assertEqual(counts[X].useful_activated, 3)
        self.assertEqual(counts[X].useful_unattributed, 1)
        self.assertEqual(counts[X].useful_runs, ["run-old-1", "run-old-2", "run-new"])
        self.assertEqual(counts[X].last_hit, "2026-08-18T10:05:00Z")

    def test_an_archived_dispute_streak_continues_into_the_rows_on_disk(self) -> None:
        write_archive(self.root, {X: archive_entry(
            disputed=1, disputed_since_last_useful=1, disputed_streak_at=["2026-08-01T10:00:00Z"],
            last_hit="2026-08-01T10:00:00Z",
        )})
        write_usage(self.root, "run-new", [settle("run-new", "build_env", X, "disputed", "2026-08-18T10:00:00Z")])
        counts = usage.replay_usage(self.root)
        self.assertEqual(counts[X].disputed, 2)
        self.assertEqual(counts[X].disputed_since_last_useful, 2)  # demote_disputed_streak still sees both

    def test_a_useful_activated_on_disk_clears_the_archived_streak(self) -> None:
        write_archive(self.root, {X: archive_entry(
            disputed=2, disputed_since_last_useful=2,
            disputed_streak_at=["2026-08-01T10:00:00Z", "2026-08-02T10:00:00Z"],
        )})
        write_usage(self.root, "run-new", [settle("run-new", "build_env", X, "useful_activated", "2026-08-18T10:00:00Z")])
        counts = usage.replay_usage(self.root)
        self.assertEqual(counts[X].disputed, 2)
        self.assertEqual(counts[X].disputed_since_last_useful, 0)
        self.assertEqual(counts[X].disputed_streak_at, [])

    def test_a_missing_or_unreadable_archive_replays_the_files_alone(self) -> None:
        usage.archive_path(self.root).write_text("{ not json", encoding="utf-8")
        write_usage(self.root, "run-a", [inject("run-a", "build_env", "2026-08-18T10:00:00Z", X)])
        self.assertEqual(usage.replay_usage(self.root)[X].injections, 1)

    def test_the_archive_file_is_not_replayed_as_a_run(self) -> None:
        write_archive(self.root, {X: archive_entry(injections=2)})
        rows, bad = usage.load_all_usage(self.root)
        self.assertEqual((rows, bad), ([], 0))


class ArchiveCrashWindowTests(UsageReplayBase):
    """prune_usage writes the archive first and deletes the run files second. Between the two
    the rows exist twice on disk, and replay_usage must still count them exactly once."""

    def seed(self, count: int = 6) -> None:
        for i in range(count):
            run = f"run-{i:02d}"
            write_usage(self.root, run, [
                inject(run, "build_env", f"2026-08-{i + 1:02d}T10:00:00Z", X),
                settle(run, "build_env", X, "useful_activated", f"2026-08-{i + 1:02d}T10:05:00Z"),
            ])

    def prune_without_deleting(self, *, retain: int) -> None:
        """Run a real prune whose unlink() always fails: the archive lands, the files stay."""
        with mock.patch.object(Path, "unlink", side_effect=PermissionError(13, "Permission denied")):
            usage.prune_usage(self.root, retain=retain)

    def test_a_folded_run_file_that_is_still_on_disk_is_not_counted_twice(self) -> None:
        self.seed()
        before = usage.replay_usage(self.root)
        self.prune_without_deleting(retain=2)
        self.assertEqual(len(list((self.root / "usage").glob("*.jsonl"))), 6)  # nothing was deleted
        self.assertEqual(usage.replay_usage(self.root), before)

    def test_rows_appended_after_the_fold_are_still_counted(self) -> None:
        self.seed()
        self.prune_without_deleting(retain=2)
        before = usage.replay_usage(self.root)
        write_usage(self.root, "run-00", [settle("run-00", "validate_import", X, "disputed", "2026-09-01T10:00:00Z")])
        counts = usage.replay_usage(self.root)
        self.assertEqual(counts[X].disputed, before[X].disputed + 1)
        self.assertEqual(counts[X].injections, before[X].injections)          # the folded prefix, once
        self.assertEqual(counts[X].useful_activated, before[X].useful_activated)

    def test_a_run_file_whose_name_is_not_a_safe_run_id_is_never_folded(self) -> None:
        # load_archive drops a pending key that is not a safe run id, because prune_usage joins
        # that key back onto usage/ to see whether the file is still there. So folding such a
        # file writes a marker that the next read throws away: the rows are in the archive, the
        # file is still on disk with nothing to skip it, and replay counts them a second time.
        # Two disputes out of one is enough to demote a confirmed entry on its own.
        self.seed()
        write_usage(self.root, "run 06", [settle("run 06", "build_env", X, "disputed", "2026-08-01T09:00:00Z")])
        before = usage.replay_usage(self.root)
        self.prune_without_deleting(retain=2)
        self.assertEqual(usage.load_archive(self.root).pending.keys() - {"run 06"},
                         usage.load_archive(self.root).pending.keys())
        self.assertEqual(usage.replay_usage(self.root), before)

    def test_a_run_file_rewritten_under_the_same_name_is_counted_whole(self) -> None:
        # The marker names the exact bytes that were folded, not just how many. A file that
        # reappears under the same run id is a different file, so none of it may be treated as
        # already archived -- not even when the new file is the longer of the two, where a
        # length-only marker would silently eat its first rows.
        self.seed()
        path = self.root / "usage" / "run-00.jsonl"
        folded_bytes = len(path.read_bytes())
        self.prune_without_deleting(retain=2)
        path.write_text("", encoding="utf-8")
        write_usage(self.root, "run-00", [
            inject("run-00", "build_env", f"2026-09-0{i + 1}T10:00:00Z", Y) for i in range(3)
        ])
        self.assertGreater(len(path.read_bytes()), folded_bytes)  # the length check alone would pass it
        self.assertEqual(usage.replay_usage(self.root)[Y].injections, 3)

    def test_a_row_that_lands_between_the_fold_and_the_delete_is_not_deleted_with_it(self) -> None:
        # match.ts appends usage rows with plain appendFileSync and takes no lock, so a run that
        # is still writing can extend a file after prune_usage has read and folded it. Only the
        # bytes the archive names are safe; deleting the file would take the rest with it, and
        # those rows exist nowhere else.
        self.seed()
        landed: list[bool] = []
        real_write = paths.atomic_write_json

        def land_a_row_after_the_fold(path: Path, obj: object) -> None:
            real_write(path, obj)
            if not landed:  # the archive write is the commit point; the deletes come after it
                landed.append(True)
                write_usage(self.root, "run-00", [
                    settle("run-00", "validate_import", Y, "useful_activated", "2026-09-01T10:00:00Z"),
                ])

        with mock.patch.object(paths, "atomic_write_json", side_effect=land_a_row_after_the_fold):
            report = usage.prune_usage(self.root, retain=2)
        self.assertTrue(landed, "the archive was never written, so the window was never opened")
        self.assertNotIn("run-00", report.pruned)
        self.assertTrue((self.root / "usage" / "run-00.jsonl").exists())
        counts = usage.replay_usage(self.root)
        self.assertEqual(counts[Y].useful_activated, 1)   # the late row survived
        self.assertEqual(counts[Y].useful_runs, ["run-00"])
        self.assertEqual(counts[X].useful_activated, 6)   # and the folded rows still count once


class PruneUsageTests(UsageReplayBase):
    def seed(self, count: int, start: int = 0) -> None:
        for i in range(start, start + count):
            run = f"run-{i:02d}"
            write_usage(self.root, run, [
                inject(run, "build_env", f"2026-08-{i + 1:02d}T10:00:00Z", X),
                settle(run, "build_env", X, "useful_activated", f"2026-08-{i + 1:02d}T10:05:00Z"),
            ])

    def files(self) -> list[str]:
        return sorted(p.name for p in (self.root / "usage").glob("*.jsonl"))

    def runs_root(self) -> Path:
        return Path(self.tmp.name) / ".sure" / "runs"

    def write_run(self, run_id: str, status: str) -> None:
        """The .sure/runs/<run_id>/run.json the harness keeps; only "status" matters here."""
        paths.atomic_write_json(self.runs_root() / run_id / "run.json", {"status": status})

    def test_a_run_still_running_is_not_pruned_however_old_its_rows_are(self) -> None:
        # Its file is the only place the inject row's events_cutoff lives, and settleOnPass in
        # hooks.ts needs that to record anything useful the entry did. Delete it and the archive
        # keeps the injection and can never gain the benefit.
        self.seed(12)
        before = usage.replay_usage(self.root)
        self.write_run("run-00", "running")
        report = usage.prune_usage(self.root, retain=4, runs_root=self.runs_root())
        # Its own file is spared and nothing else is: the oldest file in the window is also the
        # one every other candidate is judged against, so sparing it any harder than this would
        # stop the whole store from folding.
        self.assertEqual(report.pruned, [f"run-{i:02d}" for i in range(1, 8)])
        self.assertIn("run-00.jsonl", self.files())
        self.assertEqual(usage.replay_usage(self.root), before)

    def test_an_old_live_run_does_not_stop_the_rest_of_the_store_from_shrinking(self) -> None:
        # A killed run stays at "running" for ever (extension.ts only stamps staleSince), so once
        # such a file has drifted past the retained window it is in the candidate window to stay.
        # If it held the boundary as well, one tombstone would fold nothing ever again and every
        # post_finish would read the whole growing store for nothing, which is the cost retention
        # exists to remove.
        self.seed(20)
        self.write_run("run-00", "running")
        first = usage.prune_usage(self.root, retain=4, runs_root=self.runs_root())
        self.assertEqual(len(first.pruned), 15)
        self.seed(6, start=20)
        before = usage.replay_usage(self.root)
        second = usage.prune_usage(self.root, retain=4, runs_root=self.runs_root())
        self.assertEqual(len(second.pruned), 6)
        self.assertEqual(self.files(), ["run-00.jsonl", *(f"run-{i}.jsonl" for i in range(22, 26))])
        self.assertEqual(usage.replay_usage(self.root), before)

    def test_a_run_that_reached_a_terminal_status_is_still_pruned(self) -> None:
        self.seed(12)
        self.write_run("run-00", "success")
        report = usage.prune_usage(self.root, retain=4, runs_root=self.runs_root())
        self.assertIn("run-00", report.pruned)
        self.assertEqual(len(report.pruned), 8)
        self.assertNotIn("run-00.jsonl", self.files())

    def test_a_run_with_no_run_directory_is_still_pruned(self) -> None:
        # Unknown means not in flight. .sure/runs gets cleared by hand and a run started from
        # another cwd records itself elsewhere, so reading "no record" as alive would stop the
        # store shrinking for ever -- exactly the cost retention exists to remove.
        self.seed(12)
        report = usage.prune_usage(self.root, retain=4, runs_root=self.runs_root())
        self.assertEqual(len(report.pruned), 8)
        self.assertEqual(len(self.files()), 4)

    def test_a_live_run_joins_the_retained_window_without_blocking_older_ones(self) -> None:
        # The live file is retained, not merely skipped, so _prefix_cut judges the rest against
        # it as well and everything older than it still folds.
        self.seed(12)
        before = usage.replay_usage(self.root)
        self.write_run("run-07", "running")  # the newest file in the candidate window
        report = usage.prune_usage(self.root, retain=4, runs_root=self.runs_root())
        self.assertEqual(report.pruned, [f"run-{i:02d}" for i in range(7)])
        self.assertEqual(self.files(), ["run-07.jsonl", "run-08.jsonl", "run-09.jsonl", "run-10.jsonl", "run-11.jsonl"])
        self.assertEqual(report.kept, 5)
        self.assertEqual(usage.replay_usage(self.root), before)

    def test_store_is_bounded_and_every_replayed_count_is_unchanged(self) -> None:
        self.seed(12)
        before = usage.replay_usage(self.root)
        report = usage.prune_usage(self.root, retain=4)
        self.assertEqual(len(self.files()), 4)
        self.assertEqual(self.files(), ["run-08.jsonl", "run-09.jsonl", "run-10.jsonl", "run-11.jsonl"])
        self.assertEqual(len(report.pruned), 8)
        self.assertEqual(report.errors, [])
        self.assertEqual(usage.replay_usage(self.root), before)

    def test_a_second_pass_prunes_nothing_and_keeps_the_counts(self) -> None:
        self.seed(12)
        usage.prune_usage(self.root, retain=4)
        before = usage.replay_usage(self.root)
        report = usage.prune_usage(self.root, retain=4)
        self.assertEqual(report.pruned, [])
        self.assertEqual(self.files(), ["run-08.jsonl", "run-09.jsonl", "run-10.jsonl", "run-11.jsonl"])
        self.assertEqual(usage.replay_usage(self.root), before)

    def test_a_store_inside_the_retention_count_is_left_alone(self) -> None:
        self.seed(3)
        report = usage.prune_usage(self.root, retain=4)
        self.assertEqual(report.pruned, [])
        self.assertFalse(usage.archive_path(self.root).exists())
        self.assertEqual(len(self.files()), 3)

    def test_a_store_only_just_over_the_retention_count_costs_one_glob(self) -> None:
        # A pass has to read every remaining file to find its boundary, so pruning one file per
        # run would cost about what the replay it is meant to cheapen costs. Below the slack the
        # pass must not open a single run file.
        self.seed(11)
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("opened a run file")):
            self.assertEqual(usage.prune_usage(self.root, retain=10).pruned, [])
        self.assertEqual(len(self.files()), 11)
        self.assertFalse(usage.archive_path(self.root).exists())

    def test_a_store_past_the_slack_is_taken_back_down_to_the_retention_count(self) -> None:
        self.seed(12)
        before = usage.replay_usage(self.root)
        report = usage.prune_usage(self.root, retain=10)
        self.assertEqual(len(report.pruned), 2)
        self.assertEqual(len(self.files()), 10)
        self.assertEqual(usage.replay_usage(self.root), before)

    def test_a_run_whose_rows_reach_into_the_retained_window_is_kept(self) -> None:
        # Folding is only exact while every archived row sorts before every surviving row. A long
        # run that overlaps the newest ones must stay on disk rather than be folded out of order.
        write_usage(self.root, "run-long", [
            settle("run-long", "build_env", X, "disputed", "2026-08-01T10:00:00Z"),
            settle("run-long", "validate_import", X, "disputed", "2026-08-09T10:00:00Z"),
        ])
        for i in range(4):
            run = f"run-{i:02d}"
            write_usage(self.root, run, [settle(run, "build_env", X, "disputed", f"2026-08-0{i + 5}T10:00:00Z")])
        before = usage.replay_usage(self.root)
        report = usage.prune_usage(self.root, retain=2)
        self.assertNotIn("run-long", report.pruned)
        self.assertIn("run-long.jsonl", self.files())
        self.assertEqual(usage.replay_usage(self.root), before)

    def test_two_old_runs_that_overlap_only_each_other_are_folded_together(self) -> None:
        # A pass folds all of its candidates in one globally sorted pass, so two old runs that
        # overlap each other but not the retained window still reach the archive in true order.
        # Asking each candidate to sort clear of the candidates after it as well walls the store
        # off for good: an overlapping pair blocks itself for ever, so nothing older than it is
        # ever folded either, the store grows without bound, and every post_finish past the slack
        # pays a full read of it -- worse than never pruning at all.
        write_usage(self.root, "run-long", [
            settle("run-long", "build_env", X, "disputed", "2026-08-01T10:00:00Z"),
            settle("run-long", "validate_import", X, "disputed", "2026-08-04T10:00:00Z"),
        ])
        write_usage(self.root, "run-mid", [settle("run-mid", "build_env", X, "disputed", "2026-08-02T10:00:00Z")])
        self.seed(4, start=5)  # run-05 .. run-08, every row after run-long's last one
        before = usage.replay_usage(self.root)
        report = usage.prune_usage(self.root, retain=2)
        self.assertEqual(sorted(report.pruned), ["run-05", "run-06", "run-long", "run-mid"])
        self.assertEqual(self.files(), ["run-07.jsonl", "run-08.jsonl"])
        self.assertEqual(usage.replay_usage(self.root), before)

    def test_an_unreadable_archive_stops_pruning_instead_of_losing_the_folded_counts(self) -> None:
        self.seed(12)
        usage.archive_path(self.root).write_text("{ half written", encoding="utf-8")
        report = usage.prune_usage(self.root, retain=4)
        self.assertEqual(report.pruned, [])
        self.assertEqual(len(self.files()), 12)
        self.assertEqual(len(report.errors), 1)
        self.assertNotIn(str(self.root), report.errors[0])

    def test_a_failed_delete_is_reported_without_a_host_path(self) -> None:
        self.seed(6)
        with mock.patch.object(Path, "unlink", side_effect=PermissionError(13, "Permission denied")):
            report = usage.prune_usage(self.root, retain=2)
        self.assertEqual(report.pruned, [])
        self.assertEqual(len(report.errors), 4)
        for line in report.errors:
            self.assertNotIn(str(self.root), line)
            self.assertNotIn(str(self.tmp.name), line)

    def test_a_pass_that_folds_nothing_is_not_repeated_on_the_very_next_run(self) -> None:
        # Runs that are always overlapping leave no cut where a whole file can be folded, so a
        # pass can come back with nothing. It still had to open every file to find that out, and
        # repeating that on every post_finish costs a full read of a store that is not shrinking
        # -- worse than never pruning at all. The next pass waits for the store to drift by the
        # slack again, so the read is amortised over that many runs however long the stall lasts.
        self.seed(24)
        write_usage(self.root, "run-spanning", [  # newest last row, oldest first row: no cut anywhere
            settle("run-spanning", "build_env", X, "disputed", "2026-08-01T00:00:00Z"),
            settle("run-spanning", "validate_import", X, "disputed", "2026-09-01T10:00:00Z"),
        ])
        self.assertEqual(usage.prune_usage(self.root, retain=20).pruned, [])  # read everything, folded nothing
        self.seed(1, start=30)
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("opened a run file")):
            self.assertEqual(usage.prune_usage(self.root, retain=20).pruned, [])
        self.seed(1, start=31)
        with self.assertRaises(AssertionError):  # drifted by the slack: the next pass does look again
            with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("opened a run file")):
                usage.prune_usage(self.root, retain=20)

    def test_a_pass_never_folds_more_than_its_own_cap(self) -> None:
        self.seed(9)
        with mock.patch.object(usage, "_MAX_PRUNE_PER_PASS", 3):
            report = usage.prune_usage(self.root, retain=2)
        self.assertEqual(len(report.pruned), 3)
        self.assertEqual(len(self.files()), 6)

    def test_a_backlog_keeps_draining_on_the_next_run_not_the_next_drift(self) -> None:
        # The cap cuts a store that grew before retention existed into batches. A pass that folded
        # something has to be repeatable at once, or a backlog drains one batch per drift window
        # and the store retention is meant to shrink stays slow for the whole of it.
        self.seed(9)
        with mock.patch.object(usage, "_MAX_PRUNE_PER_PASS", 3):
            first = usage.prune_usage(self.root, retain=2)
            second = usage.prune_usage(self.root, retain=2)  # no new run in between
        self.assertEqual(len(first.pruned), 3)
        self.assertEqual(len(second.pruned), 3)
        self.assertEqual(len(self.files()), 3)


if __name__ == "__main__":
    unittest.main()
