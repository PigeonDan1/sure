from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # sure/runtime

from memory import paths, promote, usage  # noqa: E402

CONFIG = {
    "promote_useful_activated": 2,
    "promote_min_distinct_runs": 2,
    "demote_disputed_streak": 2,
    "usage_max_line_bytes": 4096,
}
X = "sure_onboard/no-kernel-image"
NEW = "sure_onboard/no-kernel-image-v2"
FACT = "_shared/vc-partition-names"

BODY = """# CUDA arch mismatch: no kernel image

## Trigger
`no kernel image is available for execution on the device`

## Affected Step
sure_onboard / build_env

## Minimum Evidence
artifacts/build_env.log:42

## Known Mitigation
Set TORCH_CUDA_ARCH_LIST to the target GPU before building.

## Verification
python -c "import torch; print(torch.cuda.get_arch_list())"
"""


def header(status: str = "provisional", entry_id: str = X) -> str:
    skill, _slug = paths.split_entry_id(entry_id)
    return (
        "Trigger: no kernel image is available\n"
        f"Cell: {skill}/build_env x cuda_version_mismatch\n"
        "Source: run-0 → qwen2-audio\n"
        "Added: 2026-08-18\n"
        f"Status: {status}\n"
    )


def entry_text(status: str = "provisional", entry_id: str = X) -> str:
    return header(status, entry_id) + "\n" + BODY


def base_meta(entry_id: str, *, status: str, entry_type: str = "bad_case", op: str | None = "add",
              entry_sha256: str | None = None) -> dict:
    """A meta file shaped like spec 6.3 plus the skeleton 1.7 extras (as publish.py writes it), counts still zero."""
    skill, _slug = paths.split_entry_id(entry_id)
    meta = {
        "schema": "sure.memory.meta.v1",
        "entry_id": entry_id, "type": entry_type, "status": status, "target_skill": skill, "applies_to": [skill],
        "component": "build_env" if entry_type == "bad_case" else "_",
        "cause": "cuda_version_mismatch" if entry_type == "bad_case" else "n.a.",
        "trigger": ["no kernel image is available"], "hook_trigger": ["no kernel image is available"],
        "scope": None if entry_type == "bad_case" else "cluster",
        "injections": 0, "useful_activated": 0, "useful_unattributed": 0, "useful_runs": [], "disputed": 0,
        "last_hit": None, "created": {"run_id": "run-0", "date": "2026-08-18"},
        "confirmed": {"by": "auto", "date": "2026-08-18"} if status == "confirmed" else None,
        "exported": None, "derived_from": [], "fix_exercised": False, "evidence_sha256": {},
        "superseded_by": None, "superseded_at": None, "checked_at": None,
        "target_entry": None, "similar_entry": None, "orphan": False, "entry_sha256": entry_sha256,
    }
    if op is not None:
        meta["op"] = op
    return meta


class PromoteBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.root = self.repo / "sure" / "memory"
        paths.ensure_memory_tree(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # --- fixture builders ---------------------------------------------------------

    def write_provisional(self, entry_id: str = X, *, status: str = "provisional", entry_type: str = "bad_case",
                          op: str | None = "add", proposal_op: str = "add", meta_op: bool = True) -> Path:
        """provisional/<skill>/<slug>/{entry.md,proposal.json} + meta/<skill>/<slug>.json
        (meta.entry_sha256 = sha of the entry.md just written, as publish.py records it)."""
        entry = promote.provisional_entry(self.root, entry_id)
        paths.atomic_write_text(entry, entry_text("provisional", entry_id))
        paths.atomic_write_json(entry.parent / "proposal.json", {
            "schema": "sure.memory.proposal.v2", "type": entry_type, "op": proposal_op,
            "target_skill": paths.split_entry_id(entry_id)[0], "target_entry": None,
        })
        meta = base_meta(entry_id, status=status, entry_type=entry_type, op=op if meta_op else None,
                         entry_sha256=paths.sha256_file(entry))
        paths.atomic_write_json(promote.meta_path(self.root, entry_id), meta)
        return entry

    def write_references(self, entry_id: str = X, *, text: str | None = None, entry_type: str = "bad_case") -> Path:
        path = promote.references_entry(self.repo, entry_id, entry_type)
        paths.atomic_write_text(path, entry_text("confirmed", entry_id) if text is None else text)
        return path

    def write_meta(self, entry_id: str, **overrides) -> Path:
        meta = base_meta(entry_id, status=overrides.pop("status", "confirmed"), **overrides)
        path = promote.meta_path(self.root, entry_id)
        paths.atomic_write_json(path, meta)
        return path

    def usage_row(self, run_id: str, row: dict) -> None:
        paths.append_jsonl(self.root / "usage" / f"{run_id}.jsonl", row, 4096)

    def useful(self, entry_id: str, run_id: str, at: str, unit: str = "build_env") -> None:
        self.usage_row(run_id, {"kind": "inject", "run_id": run_id, "skill": "sure_onboard", "unit": unit, "attempt": 1,
                                "events_cutoff": 10, "entries": [{"entry_id": entry_id, "shared": False}], "at": at})
        self.usage_row(run_id, {"kind": "settle", "run_id": run_id, "skill": "sure_onboard", "unit": unit,
                                "entry_id": entry_id, "outcome": "useful_activated", "at": at})

    def disputed(self, entry_id: str, run_id: str, at: str, unit: str = "build_env") -> None:
        self.usage_row(run_id, {"kind": "inject", "run_id": run_id, "skill": "sure_onboard", "unit": unit, "attempt": 1,
                                "events_cutoff": 10, "entries": [{"entry_id": entry_id, "shared": False}], "at": at})
        self.usage_row(run_id, {"kind": "settle", "run_id": run_id, "skill": "sure_onboard", "unit": unit,
                                "entry_id": entry_id, "outcome": "disputed", "at": at})

    def abandoned(self, entry_id: str, run_id: str, at: str, unit: str = "build_env") -> None:
        self.usage_row(run_id, {"kind": "inject", "run_id": run_id, "skill": "sure_onboard", "unit": unit, "attempt": 1,
                                "events_cutoff": 10, "entries": [{"entry_id": entry_id, "shared": False}], "at": at})
        self.usage_row(run_id, {"kind": "settle", "run_id": run_id, "skill": "sure_onboard", "unit": unit,
                                "entry_id": entry_id, "outcome": "abandoned", "at": at})

    def meta(self, entry_id: str = X) -> dict:
        return json.loads(promote.meta_path(self.root, entry_id).read_text(encoding="utf-8"))

    def decisions(self) -> list[dict]:
        rows, bad = paths.read_jsonl(self.root / "decisions.jsonl")
        self.assertEqual(bad, 0)
        return rows

    def references_files(self) -> list[Path]:
        skills = self.repo / "sure" / "skills"
        return sorted(skills.rglob("*.md")) if skills.exists() else []


class PromotionTests(PromoteBase):
    def test_two_activated_from_two_runs_promotes_into_outbox_only(self) -> None:
        entry = self.write_provisional()
        before = entry.read_bytes()
        self.useful(X, "run-a", "2026-08-18T10:00:00Z")
        self.useful(X, "run-b", "2026-08-19T10:00:00Z")
        rows = promote.promote_all(self.repo, config=CONFIG)
        self.assertEqual([r["action"] for r in rows], ["promote"])
        meta = self.meta()
        self.assertEqual(meta["status"], "confirmed")
        self.assertEqual(meta["confirmed"]["by"], "auto")
        self.assertEqual(meta["useful_activated"], 2)
        self.assertEqual(meta["useful_runs"], ["run-a", "run-b"])
        outbox = promote.outbox_entry(self.root, X)
        self.assertTrue(outbox.is_file())
        text = outbox.read_text(encoding="utf-8")
        self.assertIn("Status: confirmed\n", text)
        self.assertNotIn("Status: provisional", text)
        self.assertTrue(text.endswith(BODY))
        self.assertEqual(entry.read_bytes(), before)          # provisional entry.md untouched (sha stays valid)
        self.assertEqual(meta["entry_sha256"], paths.sha256_file(entry))
        self.assertEqual(meta["hook_trigger"], ["no kernel image is available"])  # promote never edits it
        self.assertEqual(self.references_files(), [])         # never writes the git-tracked tree
        decisions = self.decisions()
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["action"], "promote")
        self.assertEqual(decisions[0]["entry_id"], X)
        self.assertEqual(decisions[0]["by"], "auto")
        self.assertEqual(decisions[0]["useful_runs"], ["run-a", "run-b"])

    def test_an_abandoned_settle_does_not_block_auto_promotion(self) -> None:
        # A run that gave up on an unrelated unit must not cost a good entry its promotion.
        self.write_provisional()
        self.useful(X, "run-a", "2026-08-18T10:00:00Z")
        self.useful(X, "run-b", "2026-08-19T10:00:00Z")
        self.abandoned(X, "run-c", "2026-08-20T10:00:00Z")
        self.assertEqual([r["action"] for r in promote.promote_all(self.repo, config=CONFIG)], ["promote"])
        self.assertEqual(self.meta()["status"], "confirmed")
        self.assertEqual(self.meta()["confirmed"]["by"], "auto")

    def test_two_activated_from_the_same_run_is_not_enough(self) -> None:
        self.write_provisional()
        self.useful(X, "run-a", "2026-08-18T10:00:00Z", unit="build_env")
        self.useful(X, "run-a", "2026-08-18T11:00:00Z", unit="validate_infer")
        self.assertEqual(promote.promote_all(self.repo, config=CONFIG), [])
        self.assertEqual(self.meta()["status"], "provisional")
        self.assertEqual(self.meta()["useful_activated"], 2)
        self.assertFalse(promote.outbox_entry(self.root, X).exists())

    def test_one_activation_is_below_k(self) -> None:
        self.write_provisional()
        self.useful(X, "run-a", "2026-08-18T10:00:00Z")
        self.assertEqual(promote.promote_all(self.repo, config=CONFIG), [])
        self.assertEqual(self.meta()["status"], "provisional")

    def test_any_dispute_freezes_a_provisional_entry(self) -> None:
        self.write_provisional()
        self.useful(X, "run-a", "2026-08-18T10:00:00Z")
        self.useful(X, "run-b", "2026-08-19T10:00:00Z")
        self.disputed(X, "run-c", "2026-08-20T10:00:00Z")
        self.assertEqual(promote.promote_all(self.repo, config=CONFIG), [])
        meta = self.meta()
        self.assertEqual(meta["status"], "disputed")
        self.assertEqual(meta["disputed"], 1)
        self.assertFalse(promote.outbox_entry(self.root, X).exists())
        self.assertEqual(self.decisions(), [])
        # more useful hits later never bring it back automatically
        self.useful(X, "run-d", "2026-08-21T10:00:00Z")
        self.assertEqual(promote.promote_all(self.repo, config=CONFIG), [])
        self.assertEqual(self.meta()["status"], "disputed")

    def test_modify_and_supersede_candidates_never_auto_promote(self) -> None:
        for op in ("modify", "supersede"):
            with self.subTest(op=op):
                entry_id = f"sure_onboard/{op}-cand"
                self.write_provisional(entry_id, op=op, proposal_op=op)
                self.useful(entry_id, "run-a", "2026-08-18T10:00:00Z")
                self.useful(entry_id, "run-b", "2026-08-19T10:00:00Z")
                self.assertEqual(promote.promote_all(self.repo, config=CONFIG), [])
                self.assertEqual(self.meta(entry_id)["status"], "provisional")

    def test_op_falls_back_to_the_provisional_proposal(self) -> None:
        self.write_provisional(meta_op=False, proposal_op="add")
        self.write_provisional("sure_onboard/other", meta_op=False, proposal_op="modify")
        for entry_id in (X, "sure_onboard/other"):
            self.useful(entry_id, "run-a", "2026-08-18T10:00:00Z")
            self.useful(entry_id, "run-b", "2026-08-19T10:00:00Z")
        rows = promote.promote_all(self.repo, config=CONFIG)
        self.assertEqual([r["entry_id"] for r in rows], [X])
        self.assertEqual(self.meta("sure_onboard/other")["status"], "provisional")

    def test_facts_never_move(self) -> None:
        self.write_provisional(FACT, entry_type="fact")
        self.useful(FACT, "run-a", "2026-08-18T10:00:00Z")
        self.useful(FACT, "run-b", "2026-08-19T10:00:00Z")
        self.disputed(FACT, "run-c", "2026-08-20T10:00:00Z")
        self.assertEqual(promote.promote_all(self.repo, config=CONFIG), [])
        meta = self.meta(FACT)
        self.assertEqual(meta["status"], "provisional")
        self.assertEqual((meta["useful_activated"], meta["disputed"]), (2, 1))  # counts still refreshed

    def test_promotion_needs_the_provisional_entry_file(self) -> None:
        entry = self.write_provisional()
        entry.unlink()
        self.useful(X, "run-a", "2026-08-18T10:00:00Z")
        self.useful(X, "run-b", "2026-08-19T10:00:00Z")
        self.assertEqual(promote.promote_all(self.repo, config=CONFIG), [])
        self.assertEqual(self.meta()["status"], "provisional")


class DemotionTests(PromoteBase):
    def test_two_disputes_in_a_row_demote_a_confirmed_entry(self) -> None:
        self.write_provisional(status="confirmed")
        paths.atomic_write_text(promote.outbox_entry(self.root, X), entry_text("confirmed"))
        self.useful(X, "run-a", "2026-08-18T10:00:00Z")
        self.disputed(X, "run-b", "2026-08-19T10:00:00Z")
        self.disputed(X, "run-c", "2026-08-20T10:00:00Z")
        rows = promote.promote_all(self.repo, config=CONFIG)
        self.assertEqual([r["action"] for r in rows], ["demote"])
        meta = self.meta()
        self.assertEqual(meta["status"], "provisional")
        self.assertIsNone(meta["confirmed"])
        self.assertEqual(meta["disputed"], 2)
        self.assertEqual(meta["hook_trigger"], ["no kernel image is available"])  # demote never edits it
        self.assertFalse(promote.outbox_entry(self.root, X).parent.exists())
        self.assertEqual(self.decisions()[0]["previous_confirmed"], {"by": "auto", "date": "2026-08-18"})
        self.assertEqual(self.decisions()[0]["to_status"], "provisional")

    def test_two_abandons_do_not_demote_a_confirmed_entry(self) -> None:
        # The pair of test_two_disputes_in_a_row_demote_a_confirmed_entry: two runs that gave up
        # look nothing like two runs that hit the same wall again.
        self.write_provisional(status="confirmed")
        paths.atomic_write_text(promote.outbox_entry(self.root, X), entry_text("confirmed"))
        self.abandoned(X, "run-b", "2026-08-19T10:00:00Z")
        self.abandoned(X, "run-c", "2026-08-20T10:00:00Z")
        self.assertEqual(promote.promote_all(self.repo, config=CONFIG), [])
        self.assertEqual(self.meta()["status"], "confirmed")
        self.assertEqual(self.meta()["disputed"], 0)
        self.assertEqual([r["action"] for r in self.decisions()], [])

    def test_a_useful_hit_between_disputes_blocks_demotion(self) -> None:
        self.write_provisional(status="confirmed")
        self.disputed(X, "run-a", "2026-08-18T10:00:00Z")
        self.useful(X, "run-b", "2026-08-19T10:00:00Z")
        self.disputed(X, "run-c", "2026-08-20T10:00:00Z")
        self.assertEqual(promote.promote_all(self.repo, config=CONFIG), [])
        self.assertEqual(self.meta()["status"], "confirmed")
        self.assertEqual(self.meta()["disputed"], 2)

    def test_legacy_confirmed_entry_is_demoted_in_meta_only(self) -> None:
        ref = self.write_references(X)
        before = ref.read_bytes()
        self.write_meta(X, status="confirmed", op=None)
        self.disputed(X, "run-a", "2026-08-18T10:00:00Z")
        self.disputed(X, "run-b", "2026-08-19T10:00:00Z")
        rows = promote.promote_all(self.repo, config=CONFIG)
        self.assertEqual([r["action"] for r in rows], ["demote"])
        self.assertEqual(self.meta()["status"], "provisional")
        self.assertEqual(ref.read_bytes(), before)

    def test_demoted_entry_is_frozen_as_disputed_on_the_next_pass(self) -> None:
        self.write_provisional(status="confirmed")
        self.disputed(X, "run-a", "2026-08-18T10:00:00Z")
        self.disputed(X, "run-b", "2026-08-19T10:00:00Z")
        promote.promote_all(self.repo, config=CONFIG)
        self.assertEqual(self.meta()["status"], "provisional")
        self.assertEqual(promote.promote_all(self.repo, config=CONFIG), [])
        self.assertEqual(self.meta()["status"], "disputed")

    def _human_confirm(self, entry_id: str = X, *, date: str) -> None:
        """What cli confirm writes: status confirmed, confirmed.by human, outbox copy staged."""
        meta = self.meta(entry_id)
        meta["status"] = "confirmed"
        meta["confirmed"] = {"by": "human", "date": date}
        paths.atomic_write_json(promote.meta_path(self.root, entry_id), meta)
        paths.atomic_write_text(promote.outbox_entry(self.root, entry_id), entry_text("provisional", entry_id))

    def test_a_human_confirm_is_not_undone_by_the_disputes_it_adjudicated(self) -> None:
        self.write_provisional()
        self.disputed(X, "run-a", "2026-08-18T10:00:00Z")
        self.disputed(X, "run-b", "2026-08-19T10:00:00Z")
        promote.promote_all(self.repo, config=CONFIG)
        self.assertEqual(self.meta()["status"], "disputed")
        self._human_confirm(date="2026-08-20")
        for _pass in range(3):  # no new usage rows at all
            self.assertEqual(promote.promote_all(self.repo, config=CONFIG), [])
            meta = self.meta()
            self.assertEqual(meta["status"], "confirmed")
            self.assertEqual(meta["confirmed"], {"by": "human", "date": "2026-08-20"})
            self.assertTrue(promote.outbox_entry(self.root, X).is_file())

    def test_disputes_after_a_human_confirm_still_demote(self) -> None:
        self.write_provisional()
        self.disputed(X, "run-a", "2026-08-18T10:00:00Z")
        self.disputed(X, "run-b", "2026-08-19T10:00:00Z")
        promote.promote_all(self.repo, config=CONFIG)
        self._human_confirm(date="2026-08-20")
        self.assertEqual(promote.promote_all(self.repo, config=CONFIG), [])
        self.disputed(X, "run-c", "2026-08-21T10:00:00Z")
        self.disputed(X, "run-d", "2026-08-22T10:00:00Z")
        rows = promote.promote_all(self.repo, config=CONFIG)
        self.assertEqual([r["action"] for r in rows], ["demote"])
        self.assertEqual(self.meta()["status"], "provisional")
        self.assertFalse(promote.outbox_entry(self.root, X).parent.exists())

    def test_one_dispute_after_a_human_confirm_is_below_the_streak(self) -> None:
        self.write_provisional()
        self.disputed(X, "run-a", "2026-08-18T10:00:00Z")
        self.disputed(X, "run-b", "2026-08-19T10:00:00Z")
        promote.promote_all(self.repo, config=CONFIG)
        self._human_confirm(date="2026-08-20")
        self.disputed(X, "run-c", "2026-08-21T10:00:00Z")
        self.assertEqual(promote.promote_all(self.repo, config=CONFIG), [])
        self.assertEqual(self.meta()["status"], "confirmed")


class BookkeepingTests(PromoteBase):
    def test_counts_are_refreshed_in_every_meta(self) -> None:
        self.write_provisional(X)
        self.write_provisional("sure_onboard/quiet", status="confirmed")
        self.write_provisional("sure_onboard/gone", status="rejected")
        self.useful(X, "run-a", "2026-08-18T10:00:00Z")
        self.disputed("sure_onboard/gone", "run-a", "2026-08-18T11:00:00Z")
        self.usage_row("run-b", {"kind": "pre_start", "run_id": "run-b", "skill": "sure_eval",
                                 "entries": [{"entry_id": "sure_onboard/quiet", "shared": False}], "at": "2026-08-19T09:00:00Z"})
        promote.promote_all(self.repo, config=CONFIG)
        self.assertEqual(self.meta(X)["injections"], 1)
        self.assertEqual(self.meta(X)["last_hit"], "2026-08-18T10:00:00Z")
        quiet = self.meta("sure_onboard/quiet")
        self.assertEqual((quiet["injections"], quiet["last_hit"]), (0, "2026-08-19T09:00:00Z"))
        gone = self.meta("sure_onboard/gone")
        self.assertEqual((gone["status"], gone["disputed"]), ("rejected", 1))  # counts yes, transition no

    def test_second_pass_changes_nothing(self) -> None:
        self.write_provisional()
        self.useful(X, "run-a", "2026-08-18T10:00:00Z")
        self.useful(X, "run-b", "2026-08-19T10:00:00Z")
        self.assertEqual(len(promote.promote_all(self.repo, config=CONFIG)), 1)
        meta_bytes = promote.meta_path(self.root, X).read_bytes()
        outbox_bytes = promote.outbox_entry(self.root, X).read_bytes()
        self.assertEqual(promote.promote_all(self.repo, config=CONFIG), [])
        self.assertEqual(len(self.decisions()), 1)
        self.assertEqual(promote.meta_path(self.root, X).read_bytes(), meta_bytes)
        self.assertEqual(promote.outbox_entry(self.root, X).read_bytes(), outbox_bytes)

    def test_meta_is_rewritten_under_the_memory_lock(self) -> None:
        self.write_provisional()
        self.useful(X, "run-a", "2026-08-18T10:00:00Z")
        with mock.patch.object(promote.paths, "memory_lock", wraps=paths.memory_lock) as lock:
            promote.promote_all(self.repo, config=CONFIG)
        lock.assert_called_once_with(self.root)
        self.assertEqual(self.meta()["useful_activated"], 1)

    def test_a_crash_between_meta_write_and_decision_append_never_duplicates_the_row(self) -> None:
        # meta is written before its decision row is appended (matching apply_supersede): a
        # process death in that window, still holding the lock, leaves meta already showing the
        # new status. The real append_decision runs for real on this call's first invocation
        # (proving the write-then-log order) and only raises once that write has landed.
        self.write_provisional()
        self.useful(X, "run-a", "2026-08-18T10:00:00Z")
        self.useful(X, "run-b", "2026-08-19T10:00:00Z")
        real_append = paths.append_decision
        calls = {"n": 0}

        def flaky(root, row):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("simulated crash after the meta write landed")
            return real_append(root, row)

        with mock.patch.object(promote.paths, "append_decision", side_effect=flaky):
            with self.assertRaises(RuntimeError):
                promote.promote_all(self.repo, config=CONFIG)
        # the meta write already committed before the simulated crash
        self.assertEqual(self.meta()["status"], "confirmed")
        self.assertFalse((self.root / "decisions.jsonl").exists())
        # retrying finds a confirmed (not provisional) entry, so it does not re-fire the same
        # transition -- the crash can drop the audit row for it, but never duplicate it
        self.assertEqual(promote.promote_all(self.repo, config=CONFIG), [])
        promote_rows = [r for r in self.decisions() if r["entry_id"] == X and r["action"] == "promote"]
        # zero, not one: the meta write already landed before the append failed, so the
        # transition is complete and unrepeatable -- the row for it is dropped, not duplicated
        self.assertEqual(len(promote_rows), 0)

    def test_broken_or_stray_meta_files_are_skipped(self) -> None:
        self.write_provisional()
        (self.root / "meta" / "sure_onboard" / "broken.json").write_text("{not json", encoding="utf-8")
        (self.root / "meta" / "sure_onboard" / "list.json").write_text("[1, 2]", encoding="utf-8")
        (self.root / "meta" / "notes.json").write_text("{}", encoding="utf-8")
        self.useful(X, "run-a", "2026-08-18T10:00:00Z")
        promote.promote_all(self.repo, config=CONFIG)  # must not raise
        self.assertEqual(self.meta()["useful_activated"], 1)

    def test_fresh_tree_is_a_no_op(self) -> None:
        empty = Path(self.tmp.name) / "fresh"
        self.assertEqual(promote.promote_all(empty, config=CONFIG), [])
        self.assertTrue((empty / "sure" / "memory" / "meta").is_dir())


class LegacyMetaTests(PromoteBase):
    """Spec 6.3: confirmed and legacy entries carry meta too. Entries that only live under
    references never went through publish, so promote has to create their meta before the counts
    replayed from usage have anywhere to land."""

    def test_a_references_only_entry_gets_a_meta_and_its_counts(self) -> None:
        self.write_references(X)
        self.useful(X, "run-a", "2026-08-18T10:00:00Z")
        self.assertEqual(promote.promote_all(self.repo, config=CONFIG), [])
        meta = self.meta()
        self.assertEqual((meta["status"], meta["type"], meta["created"]), ("confirmed", "bad_case", "legacy"))
        self.assertEqual((meta["injections"], meta["useful_activated"], meta["useful_runs"]), (1, 1, ["run-a"]))
        self.assertEqual(meta["last_hit"], "2026-08-18T10:00:00Z")
        self.assertEqual(meta["schema"], "sure.memory.meta.v1")
        self.assertEqual(meta["confirmed"], {"by": "human", "date": None})
        self.assertIsNone(meta["entry_sha256"])  # the index only hashes provisional entry.md files
        # the index reads triggers off the file; an empty hook_trigger here would mute the entry
        self.assertNotIn("hook_trigger", meta)
        self.assertNotIn("trigger", meta)

    def test_one_undecodable_references_file_does_not_stop_the_rest(self) -> None:
        """A references file that is not valid UTF-8 made read_text raise UnicodeDecodeError, which
        is a ValueError and so escaped the `except OSError: continue`. That exception left
        _create_missing_meta before it wrote anything, so one bad file on the shared checkout
        silently disabled promotion, demotion and the count refresh for the whole store."""
        self.write_references(X)
        self.useful(X, "run-a", "2026-08-18T10:00:00Z")
        bad = promote.references_entry(self.repo, "sure_onboard/not-utf8", "bad_case")
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_bytes(b"Trigger: \xff\xfe not utf-8\n\n# broken\n")

        self.assertEqual(promote.promote_all(self.repo, config=CONFIG), [])

        meta = self.meta()
        self.assertEqual(meta["status"], "confirmed")
        self.assertEqual((meta["injections"], meta["useful_activated"]), (1, 1))
        self.assertFalse(promote.meta_path(self.root, "sure_onboard/not-utf8").exists())

    def test_a_legacy_entry_can_now_be_demoted_by_two_disputes(self) -> None:
        ref = self.write_references(X)
        before = ref.read_bytes()
        self.disputed(X, "run-a", "2026-08-18T10:00:00Z")
        self.disputed(X, "run-b", "2026-08-19T10:00:00Z")
        rows = promote.promote_all(self.repo, config=CONFIG)
        self.assertEqual([r["action"] for r in rows], ["demote"])
        self.assertEqual(rows[0]["previous_confirmed"], {"by": "human", "date": None})
        self.assertEqual(self.meta()["status"], "provisional")
        self.assertEqual(ref.read_bytes(), before)  # the git-tracked file is never rewritten

    def test_shared_facts_are_typed_fact(self) -> None:
        self.write_references(FACT, entry_type="fact")
        promote.promote_all(self.repo, config=CONFIG)
        meta = self.meta(FACT)
        self.assertEqual((meta["type"], meta["target_skill"], meta["applies_to"]), ("fact", "_shared", ["_shared"]))
        self.assertEqual(meta["status"], "confirmed")

    def test_a_superseded_header_is_honoured_and_the_stub_is_written_once(self) -> None:
        gone = "sure_onboard/old-and-gone"
        self.write_references(gone, text=f"Superseded-by: {NEW} (2026-08-01)\n\n# Gone\n")
        promote.promote_all(self.repo, config=CONFIG)
        meta = self.meta(gone)
        self.assertEqual((meta["status"], meta["superseded_by"], meta["confirmed"]), ("superseded", NEW, None))
        stub_bytes = promote.meta_path(self.root, gone).read_bytes()
        self.assertEqual(promote.promote_all(self.repo, config=CONFIG), [])
        self.assertEqual(promote.meta_path(self.root, gone).read_bytes(), stub_bytes)

    def test_readme_and_unusable_file_names_are_not_entries(self) -> None:
        directory = self.repo / "sure" / "skills" / "sure_onboard" / "references" / "memory" / "bad_cases"
        paths.atomic_write_text(directory / "README.md", "| entry | file |\n")
        paths.atomic_write_text(directory / "not a slug.md", "# spaces in the name\n")
        promote.promote_all(self.repo, config=CONFIG)
        self.assertEqual(list((self.root / "meta").rglob("*.json")), [])

    def test_an_existing_meta_is_never_overwritten_by_a_stub(self) -> None:
        self.write_references(X)
        self.write_meta(X, status="disputed", op=None)
        before = promote.meta_path(self.root, X).read_bytes()
        promote.promote_all(self.repo, config=CONFIG)
        self.assertEqual(promote.meta_path(self.root, X).read_bytes(), before)
        self.assertEqual(self.meta()["status"], "disputed")


class SupersedeTests(PromoteBase):
    def test_header_line_lands_on_every_copy_and_meta_is_updated(self) -> None:
        provisional = self.write_provisional(status="confirmed")
        sha_before = self.meta()["entry_sha256"]
        outbox = promote.outbox_entry(self.root, X)
        paths.atomic_write_text(outbox, entry_text("confirmed"))
        ref = self.write_references(X)
        promote.apply_supersede(self.repo, X, NEW, by="human")
        for path in (ref, provisional, outbox):
            with self.subTest(path=path.name):
                lines = path.read_text(encoding="utf-8").splitlines()
                self.assertEqual(lines[:5], header("confirmed" if path != provisional else "provisional").splitlines())
                self.assertTrue(lines[5].startswith(f"Superseded-by: {NEW} ("), lines[5])
                self.assertEqual(lines[6], "")
                self.assertEqual(lines[7], "# CUDA arch mismatch: no kernel image")
        meta = self.meta()
        self.assertEqual(meta["status"], "superseded")
        self.assertEqual(meta["superseded_by"], NEW)
        self.assertEqual(meta["superseded_at"], paths.utc_today())
        # the provisional entry.md was rewritten, so the index's inclusion hash must follow it (skeleton 1.7)
        self.assertNotEqual(meta["entry_sha256"], sha_before)
        self.assertEqual(meta["entry_sha256"], paths.sha256_file(provisional))
        self.assertEqual(meta["hook_trigger"], ["no kernel image is available"])  # supersede never edits it
        row = self.decisions()[-1]
        self.assertEqual((row["action"], row["entry_id"], row["by"], row["superseded_by"]), ("supersede", X, "human", NEW))
        self.assertEqual(len(row["files"]), 3)
        self.assertTrue(all("\\" not in f for f in row["files"]))

    def test_legacy_file_without_header_gets_a_one_line_header(self) -> None:
        legacy = "sure_onboard/cuda-runtime-mismatch"
        ref = self.write_references(legacy, text="# CUDA runtime mismatch\n\n## Trigger\n`libcudart.so.11.0: cannot open`\n")
        promote.apply_supersede(self.repo, legacy, NEW, by="human")
        text = ref.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(f"Superseded-by: {NEW} ({paths.utc_today()})\n\n# CUDA runtime mismatch\n"), text)
        meta = self.meta(legacy)  # created on the spot so the index can see the status
        self.assertEqual((meta["status"], meta["type"], meta["target_skill"], meta["created"]), ("superseded", "bad_case", "sure_onboard", "legacy"))
        self.assertEqual(meta["superseded_by"], NEW)
        self.assertEqual(meta["schema"], "sure.memory.meta.v1")
        self.assertIsNone(meta["entry_sha256"])  # no provisional entry.md, nothing for the index to hash

    def test_a_crlf_tracked_file_keeps_its_line_endings(self) -> None:
        ref = promote.references_entry(self.repo, X, "bad_case")
        paths.atomic_write_bytes(ref, entry_text("confirmed").replace("\n", "\r\n").encode("utf-8"))
        before = ref.read_bytes().count(b"\r\n")
        promote.apply_supersede(self.repo, X, NEW, by="human")
        after = ref.read_bytes()
        self.assertEqual(after.count(b"\n"), after.count(b"\r\n"))  # one changed line, not a whole-file diff
        self.assertEqual(after.count(b"\r\n"), before + 1)          # only the added header line
        self.assertIn(f"Superseded-by: {NEW} (".encode("utf-8"), after)

    def test_supersede_is_idempotent(self) -> None:
        self.write_provisional()
        promote.apply_supersede(self.repo, X, NEW, by="human")
        promote.apply_supersede(self.repo, X, "sure_onboard/no-kernel-image-v3", by="human")
        text = promote.provisional_entry(self.root, X).read_text(encoding="utf-8")
        self.assertEqual(text.count("Superseded-by:"), 1)
        self.assertIn("Superseded-by: sure_onboard/no-kernel-image-v3 (", text)
        self.assertEqual(self.meta()["superseded_by"], "sure_onboard/no-kernel-image-v3")
        self.assertEqual(self.meta()["entry_sha256"], paths.sha256_text(text))

    def test_bad_ids_and_unknown_entries_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            promote.apply_supersede(self.repo, "nope", NEW, by="human")
        with self.assertRaises(ValueError):
            promote.apply_supersede(self.repo, X, "../x/y", by="human")
        with self.assertRaises(ValueError):
            promote.apply_supersede(self.repo, X, X, by="human")
        with self.assertRaises(FileNotFoundError):
            promote.apply_supersede(self.repo, "sure_onboard/never-existed", NEW, by="human")
        self.assertFalse((self.root / "decisions.jsonl").exists())

    def test_superseded_entries_are_left_alone_by_promote(self) -> None:
        self.write_provisional()
        promote.apply_supersede(self.repo, X, NEW, by="human")
        self.useful(X, "run-a", "2026-08-18T10:00:00Z")
        self.useful(X, "run-b", "2026-08-19T10:00:00Z")
        self.assertEqual(promote.promote_all(self.repo, config=CONFIG), [])
        self.assertEqual(self.meta()["status"], "superseded")
        self.assertEqual(self.meta()["useful_activated"], 2)


class HeaderHelperTests(unittest.TestCase):
    def test_split_and_join_round_trip(self) -> None:
        text = entry_text("provisional")
        head, body = promote.split_header(text)
        self.assertEqual(len(head), 5)
        self.assertEqual(body[0], "# CUDA arch mismatch: no kernel image")
        self.assertEqual(promote.join_header(head, body), text)

    def test_legacy_text_has_no_header(self) -> None:
        self.assertEqual(promote.split_header(BODY), ([], BODY.splitlines()))
        self.assertEqual(promote.with_status(BODY, "confirmed"), BODY)

    def test_a_leading_blank_line_does_not_hide_the_header(self) -> None:
        # index.parse_header skips leading blanks; split_header used to stop at line 0, so a
        # hand-edited file lost its Trigger and Cell as soon as anything rewrote the header.
        text = "\n" + entry_text("provisional")
        head, body = promote.split_header(text)
        self.assertEqual(len(head), 5)
        self.assertEqual(body[0], "# CUDA arch mismatch: no kernel image")
        self.assertEqual(promote.with_status(text, "confirmed"), entry_text("confirmed"))
        superseded = promote.with_superseded_by(text, NEW, "2026-08-19")
        self.assertEqual(superseded.count("Superseded-by:"), 1)
        self.assertIn("Trigger: no kernel image is available\n", superseded)
        self.assertIn("Cell: sure_onboard/build_env x cuda_version_mismatch\n", superseded)

    def test_with_status_replaces_only_the_status_line(self) -> None:
        out = promote.with_status(entry_text("provisional"), "confirmed")
        self.assertEqual(out, entry_text("confirmed"))

    def test_decision_helpers_are_the_paths_ones(self) -> None:
        # Skeleton 1.6: every decisions row goes through paths; promote only re-exports the names.
        self.assertIs(promote.decision_row, paths.decision_row)
        self.assertIs(promote.append_decision, paths.append_decision)
        self.assertEqual(promote.DECISION_ACTIONS, paths.DECISION_ACTIONS)
        with self.assertRaises(ValueError):
            promote.decision_row("adopt", X, "human", reason="x")
        row = promote.decision_row("confirm", X, "human", reason="looks right")
        self.assertEqual(sorted(row), ["action", "at", "by", "entry_id", "reason"])


class MainTests(PromoteBase):
    def test_main_promotes_then_rebuilds_the_index(self) -> None:
        self.write_provisional()
        self.useful(X, "run-a", "2026-08-18T10:00:00Z")
        self.useful(X, "run-b", "2026-08-19T10:00:00Z")
        out = io.StringIO()
        with mock.patch.object(promote, "_rebuild_index") as rebuild, contextlib.redirect_stdout(out):
            code = promote.main(["--repo-root", str(self.repo)])
        self.assertEqual(code, 0)
        rebuild.assert_called_once()
        self.assertEqual(rebuild.call_args.args[0], self.repo.resolve())
        self.assertIn(f"promote: {X}", out.getvalue())
        self.assertIn("promote: 1 decision(s)", out.getvalue())
        self.assertIn("index rebuilt", out.getvalue())
        self.assertEqual(self.meta()["status"], "confirmed")

    def test_the_documented_standalone_invocation_runs(self) -> None:
        # promote.py's own docstring advertises `python -s .../promote.py --repo-root <repo>`;
        # without the sys.path guard every sibling has, it dies with ModuleNotFoundError: memory.
        script = Path(__file__).resolve().parent / "promote.py"
        proc = subprocess.run([sys.executable, "-s", str(script), "--repo-root", str(self.repo), "--no-rebuild-index"],
                              capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("promote: 0 decision(s)", proc.stdout)
        self.assertNotIn("ModuleNotFoundError", proc.stderr)

    def test_main_can_skip_the_rebuild(self) -> None:
        out = io.StringIO()
        with mock.patch.object(promote, "_rebuild_index") as rebuild, contextlib.redirect_stdout(out):
            code = promote.main(["--repo-root", str(self.repo), "--no-rebuild-index"])
        self.assertEqual(code, 0)
        rebuild.assert_not_called()
        self.assertEqual(out.getvalue(), "promote: 0 decision(s)\n")


if __name__ == "__main__":
    unittest.main()
