# sure/runtime/memory/test_publish.py
from __future__ import annotations

import contextlib
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # sure/runtime

from memory import digest, paths, proposals, publish, usage  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
RUN_ID = "20260818-120000-a1b2c3d4"
SLUG = "cuda-arch-mismatch-no-kernel-image"
ENTRY_ID = f"sure_onboard/{SLUG}"
MODEL_ID = "openai/whisper-large-v3"
MODEL_NAME = "openai__whisper-large-v3"

DIGEST = {
    "schema": "sure.memory.run_digest.v1",
    "run": {
        "run_id": RUN_ID,
        "skill": "sure_onboard",
        "args": "model_input=sure/handoff/whisper/model_input.yaml",
        "target": {"kind": "model", "id": MODEL_ID},
        "status_so_far": "running",
        "cutoff": 812,
        "memory_usage": [],
    },
    "units": [
        {"id": "build_env", "outcome": "passed", "attempts": 2,
         "repairs": [{"attempt": 1, "text": "RuntimeError: no kernel image is available for execution on the device"}],
         "fix_window": [{"tool": "bash", "command": "pip install torch==2.4.0 --index-url https://download.pytorch.org/whl/cu121"}],
         "last_commands": [], "log_tail": None},
        {"id": "validate_import", "outcome": "passed", "attempts": 1, "repairs": [], "fix_window": [], "last_commands": [], "log_tail": None},
        {"id": "verdict", "outcome": "passed", "attempts": 1, "repairs": [], "fix_window": [], "last_commands": [], "log_tail": None},
        {"id": "extract_lessons", "outcome": "current", "attempts": 0, "repairs": [], "fix_window": [], "last_commands": [], "log_tail": None},
    ],
    "tool_errors": 0,
    "prior_runs": [],
    "memory_index_snapshot": [],
    "units_registry": {"sure_onboard": ["build_env", "validate_import", "verdict", "extract_lessons"]},
}

BAD_CASE_BODY = """## Trigger
`no kernel image is available` right after `pip install torch` in build_env.

## Affected Step
sure_onboard / build_env

## Minimum Evidence
artifacts/build_env.log:2

## Known Mitigation
Install the cu121 torch wheel; the default wheel targets a newer arch than the 2080ti.

## Verification
python -c "import torch; print(torch.cuda.is_available())"
"""


def candidate(**over) -> dict:
    """One candidate dir's contents: dir name, proposal.json object, proposal.md text."""
    spec = {
        "dir": "01-no-kernel-image",
        "h1": "CUDA arch mismatch: no kernel image",
        "body": BAD_CASE_BODY,
        "type": "bad_case",
        "op": "add",
        "target_skill": "sure_onboard",
        "target_entry": None,
        "applies_to": ["sure_onboard"],
        "component": "build_env",
        "cause": "cuda_version_mismatch",
        "trigger": ["no kernel image is available"],
        "causal": True,
        "evidence": ["artifacts/build_env.log:2", "artifacts/validation.log"],
        "claims": [
            {"kind": "gate_repair", "unit": "build_env", "attempt": 1, "status": "failed"},
            {"kind": "unit_result", "unit": "build_env", "attempt": 2, "status": "passed"},
        ],
        "similar": None,
        "scope": None,
        "checked_at": None,
    }
    spec.update(over)
    proposal = {
        "schema": "sure.memory.proposal.v2",
        "type": spec["type"],
        "op": spec["op"],
        "target_skill": spec["target_skill"],
        "target_entry": spec["target_entry"],
        "applies_to": spec["applies_to"],
        "cell": {"component": spec["component"], "cause": spec["cause"]},
        "trigger": spec["trigger"],
        "causal": spec["causal"],
        "evidence": spec["evidence"],
        "claims": spec["claims"],
        "source": {"run_id": RUN_ID, "skill": "sure_onboard", "target": MODEL_ID, "digest_sha256": "deadbeef"},
        "similar": spec["similar"],
        "scope": spec["scope"],
        "checked_at": spec["checked_at"],
    }
    return {"dir": spec["dir"], "proposal": proposal, "md": f"# {spec['h1']}\n\n{spec['body']}"}


def fact_candidate() -> dict:
    return candidate(
        dir="02-site-gpu-partition",
        h1="Long vc jobs go to partition site-gpu; openbench kills them at 350 seconds",
        body="Scope: cluster\nChecked-at: 2026-08-18\nEvidence: artifacts/memory_evidence/1.txt\n\nSeen on every eval since 8-12.\n",
        type="fact", target_skill="_shared", applies_to=["sure_onboard", "sure_eval"], component="_", cause="n.a.",
        trigger=[], causal=False, evidence=["artifacts/memory_evidence/1.txt"], claims=[], scope="cluster", checked_at="2026-08-18",
    )


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class PublishFixture(unittest.TestCase):
    """A fake repo with the skills tree, one model dir, one run and an empty memory root."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        for skill in ("sure_onboard", "sure_eval", "sure_feed", "sure_reval"):
            (self.repo / "sure" / "skills" / skill / "references" / "memory" / "bad_cases").mkdir(parents=True)
        (self.repo / "sure" / "skills" / "_shared" / "memory" / "facts").mkdir(parents=True)
        self.model_dir = self.repo / "sure" / "models" / MODEL_NAME
        (self.model_dir / "artifacts").mkdir(parents=True)
        (self.model_dir / "artifacts" / "validation.log").write_text("import ok\nload ok\n", encoding="utf-8")
        self.root = self.repo / "sure" / "memory"
        self.run_dir = self.repo / ".sure" / "runs" / RUN_ID
        self.art = self.run_dir / "artifacts"
        self.config = paths.load_config()
        self.units = paths.load_units()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def build_run(self, candidates=None, *, no_new_lessons=False, digest=True, declaration=True) -> None:
        self.art.mkdir(parents=True, exist_ok=True)
        (self.art / "build_env.log").write_text(
            "Collecting torch\nRuntimeError: no kernel image is available for execution on the device\n", encoding="utf-8"
        )
        (self.art / "memory_evidence").mkdir(exist_ok=True)
        (self.art / "memory_evidence" / "1.txt").write_text("PARTITION site-gpu up\n", encoding="utf-8")
        write_json(self.art / "model_input_resolved.json", {
            "model_id": MODEL_ID, "model_name": MODEL_NAME, "model_dir": str(self.model_dir),
            "repo_url": "https://x", "task_type": "asr", "deployment_type": "local", "package_profile": "none",
        })
        if digest:
            write_json(self.art / "run_digest.json", DIGEST)
        candidates = candidates if candidates is not None else [candidate()]
        for cand in candidates:
            cdir = self.art / "candidates" / cand["dir"]
            write_json(cdir / "proposal.json", cand["proposal"])
            (cdir / "proposal.md").write_text(cand["md"], encoding="utf-8")
        if declaration:
            write_json(self.art / "extraction_declaration.json", {
                "schema": "sure.memory.extraction.v2",
                "no_new_lessons": no_new_lessons,
                "no_lessons_reason": "clean run" if no_new_lessons else None,
                "covered_by": [],
                "candidates": [] if no_new_lessons else [c["dir"] for c in candidates],
                "infra_noise": False,
                "infra_evidence": [],
            })

    def write_usage(self, rows) -> None:
        for row in rows:
            paths.append_jsonl(self.root / "usage" / f"{RUN_ID}.jsonl", row, 4096)

    def seed_usage_runs(self, entry_id: str, *, count: int, useful: tuple = ()) -> None:
        """`count` earlier runs, oldest first, each injecting entry_id; the runs named in
        `useful` also settle it useful_activated. This run's own usage file is not touched."""
        for i in range(count):
            run = f"run-{i:02d}"
            day = f"2026-08-{i + 1:02d}"
            rows = [{"kind": "inject", "run_id": run, "skill": "sure_onboard", "unit": "build_env",
                     "attempt": 1, "events_cutoff": 812, "at": f"{day}T10:00:00Z",
                     "entries": [{"entry_id": entry_id, "shared": False}]}]
            if run in useful:
                rows.append({"kind": "settle", "run_id": run, "skill": "sure_onboard", "unit": "build_env",
                             "entry_id": entry_id, "outcome": "useful_activated", "at": f"{day}T10:05:00Z"})
            for row in rows:
                paths.append_jsonl(self.root / "usage" / f"{run}.jsonl", row, 4096)

    def seed_digests(self, count: int) -> None:
        """`count` digests/<run_id>.json, run ids in the YYYYMMDD- shape the hooks produce."""
        for i in range(count):
            run = f"2026-08-{i + 1:02d}".replace("-", "") + f"-100000-run{i:02d}"
            write_json(self.root / "digests" / f"{run}.json",
                       {"schema": "sure.memory.run_digest.v1", "run": {"run_id": run, "skill": "sure_onboard"}, "units": []})

    def publish(self) -> publish.PublishReport:
        return publish.publish_run(self.run_dir, self.repo, config=self.config, units=self.units)

    def entry_dir(self, entry_id: str) -> Path:
        skill, slug = entry_id.split("/", 1)
        return self.root / "provisional" / skill / slug

    def meta(self, entry_id: str) -> dict:
        skill, slug = entry_id.split("/", 1)
        return json.loads((self.root / "meta" / skill / f"{slug}.json").read_text(encoding="utf-8"))

    def decisions(self) -> list[dict]:
        rows, bad = paths.read_jsonl(self.root / "decisions.jsonl")
        self.assertEqual(bad, 0)
        return rows


class PublishAddTests(PublishFixture):
    def test_writes_entry_with_five_header_lines_then_h1_and_body(self) -> None:
        self.build_run()
        report = self.publish()
        self.assertEqual(report.errors, [])
        self.assertEqual(report.published, ["sure_onboard/cuda-arch-mismatch-no-kernel-image"])
        entry = self.entry_dir(report.published[0]) / "entry.md"
        lines = entry.read_text(encoding="utf-8").splitlines()
        # §5.1: header block first (index.parse_header / promote.split_header read from line 0).
        self.assertEqual(lines[0], "Trigger: no kernel image is available")
        self.assertEqual(lines[1], "Cell: sure_onboard/build_env x cuda_version_mismatch")
        self.assertEqual(lines[2], f"Source: {RUN_ID} → {MODEL_ID}")
        self.assertRegex(lines[3], r"^Added: \d{4}-\d{2}-\d{2}$")
        self.assertEqual(lines[4], "Status: provisional")
        self.assertEqual(lines[5], "")
        self.assertEqual(lines[6], "# CUDA arch mismatch: no kernel image")
        self.assertEqual(lines[7], "")
        self.assertEqual(lines[8], "## Trigger")
        self.assertEqual(lines[-1], 'python -c "import torch; print(torch.cuda.is_available())"')
        self.assertTrue((self.entry_dir(report.published[0]) / "proposal.json").is_file())

    def test_chinese_h1_falls_back_to_run_suffix_and_candidate_number(self) -> None:
        self.build_run([candidate(h1="显卡架构不匹配")])
        report = self.publish()
        self.assertEqual(report.published, ["sure_onboard/a1b2c3d4-01"])
        self.assertTrue((self.entry_dir("sure_onboard/a1b2c3d4-01") / "entry.md").is_file())

    def test_slug_collision_appends_dash_two_then_dash_three(self) -> None:
        self.build_run()
        for slug in (SLUG, f"{SLUG}-2"):
            # With its meta, as a published entry always has: a provisional dir without one is a
            # killed publish's litter, which _reclaim_orphans sweeps before the slug is claimed.
            (self.root / "provisional" / "sure_onboard" / slug).mkdir(parents=True, exist_ok=True)
            write_json(self.root / "meta" / "sure_onboard" / f"{slug}.json", {"entry_id": f"sure_onboard/{slug}"})
        report = self.publish()
        self.assertEqual(report.errors, [])
        self.assertEqual(report.published, ["sure_onboard/cuda-arch-mismatch-no-kernel-image-3"])

    def test_existing_reference_file_counts_as_taken(self) -> None:
        self.build_run()
        ref = self.repo / "sure" / "skills" / "sure_onboard" / "references" / "memory" / "bad_cases" / "cuda-arch-mismatch-no-kernel-image.md"
        ref.write_text("# old\n", encoding="utf-8")
        report = self.publish()
        self.assertEqual(report.published, ["sure_onboard/cuda-arch-mismatch-no-kernel-image-2"])
        self.assertEqual(ref.read_text(encoding="utf-8"), "# old\n")

    def test_meta_has_zero_counters_and_created_run(self) -> None:
        self.build_run()
        entry_id = self.publish().published[0]
        meta = self.meta(entry_id)
        self.assertEqual(meta["entry_id"], entry_id)
        self.assertEqual(meta["type"], "bad_case")
        self.assertEqual(meta["status"], "provisional")
        self.assertEqual(meta["target_skill"], "sure_onboard")
        self.assertEqual(meta["applies_to"], ["sure_onboard"])
        self.assertEqual((meta["component"], meta["cause"]), ("build_env", "cuda_version_mismatch"))
        self.assertEqual(meta["trigger"], ["no kernel image is available"])
        for key in ("injections", "useful_activated", "useful_unattributed", "disputed"):
            self.assertEqual(meta[key], 0, key)
        self.assertEqual(meta["useful_runs"], [])
        self.assertIsNone(meta["last_hit"])
        self.assertEqual(meta["created"]["run_id"], RUN_ID)
        self.assertRegex(meta["created"]["date"], r"^\d{4}-\d{2}-\d{2}$")
        for key in ("confirmed", "exported", "superseded_by", "superseded_at", "checked_at", "scope", "target_entry", "similar_entry"):
            self.assertIsNone(meta[key], key)
        self.assertEqual(meta["op"], "add")
        self.assertFalse(meta["orphan"])
        self.assertEqual(meta["hook_trigger"], ["no kernel image is available"])
        entry_text = (self.entry_dir(entry_id) / "entry.md").read_text(encoding="utf-8")
        self.assertEqual(meta["entry_sha256"], paths.sha256_text(entry_text))

    def test_hook_trigger_keeps_only_triggers_observed_in_digest(self) -> None:
        # First trigger is in the build_env repair text, second only in the log tail (different case,
        # trigger_hits is case-insensitive), third only in an evidence file the agent read: the third
        # stays in `trigger` for index.md / prompt routing but never drives hook injection.
        triggers = ["no kernel image is available", "unsupported gpu architecture", "sm_75 kernels missing"]
        digest = copy.deepcopy(DIGEST)
        digest["units"][0]["log_tail"] = {
            "path": "{run_dir}/artifacts/build_env.log",
            "lines": ["nvcc fatal: Unsupported GPU architecture 'compute_75'"],
        }
        self.build_run([candidate(trigger=triggers)])
        write_json(self.art / "run_digest.json", digest)
        entry_id = self.publish().published[0]
        meta = self.meta(entry_id)
        self.assertEqual(meta["trigger"], triggers)
        self.assertEqual(meta["hook_trigger"], ["no kernel image is available", "unsupported gpu architecture"])
        texts = proposals.trigger_texts(self.run_dir, digest, self.config)
        self.assertEqual(publish.hook_trigger("bad_case", triggers, texts), meta["hook_trigger"])

    def test_hook_trigger_sees_the_unclipped_repair_the_gate_checked(self) -> None:
        # Rule 4 checks a trigger against the UNCLIPPED repair (re-read from events.jsonl);
        # hook_trigger must read the same texts, or a trigger that lives only in the part the
        # clip dropped passes the gate and then publishes an entry that can never fire.
        limits = self.config["digest_limits"]
        head, tail = limits["repair_head_chars"], limits["repair_tail_chars"]
        buried = "unsupported gpu architecture 'compute_120'"
        full = "H" * head + "\n" + buried + "\n" + "M" * 2000 + "\n" + "T" * tail
        clipped = digest.clip_head_tail(full, head, tail)
        self.assertNotIn(buried, clipped)  # the clip really did drop it
        events = [
            {"type": "created", "data": {"skillName": "sure_onboard"}},
            {"type": "tool_result_repair", "data": {"state_patch": {"diagnostics": [{"repair": full}]}}},
        ]
        d = copy.deepcopy(DIGEST)
        d["run"]["cutoff"] = len(events)
        d["units"][0]["repairs"] = [{"attempt": 1, "text": clipped}]
        self.build_run([candidate(trigger=[buried])])
        write_json(self.art / "run_digest.json", d)
        (self.run_dir / "events.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8"
        )
        entry_id = self.publish().published[0]
        self.assertEqual(self.meta(entry_id)["hook_trigger"], [buried])

    def test_hook_trigger_for_fact_is_every_trigger_even_when_unobserved(self) -> None:
        fact = fact_candidate()
        fact["proposal"]["trigger"] = ["openbench kills them at 350 seconds"]
        self.build_run([fact])
        entry_id = self.publish().published[0]
        self.assertEqual(self.meta(entry_id)["hook_trigger"], ["openbench kills them at 350 seconds"])

    def test_hook_trigger_is_empty_for_bad_case_when_digest_is_missing(self) -> None:
        # The gate never passes a bad_case without a digest (rule 4 needs an observed trigger), so this
        # only documents the fallback: no digest, nothing observed, hook_trigger [] (index/prompt only).
        self.build_run(digest=False)
        entry_id = self.publish().published[0]
        meta = self.meta(entry_id)
        self.assertEqual(meta["trigger"], ["no kernel image is available"])
        self.assertEqual(meta["hook_trigger"], [])

    def test_digest_copied_byte_identical(self) -> None:
        self.build_run()
        self.publish()
        copy = self.root / "digests" / f"{RUN_ID}.json"
        self.assertEqual(copy.read_bytes(), (self.art / "run_digest.json").read_bytes())

    def test_evidence_sha256_recorded_in_proposal_copy_and_meta(self) -> None:
        self.build_run([candidate(evidence=["artifacts/build_env.log:2", "artifacts/validation.log", "artifacts/missing.log", "/etc/passwd"])])
        entry_id = self.publish().published[0]
        expected = {
            "artifacts/build_env.log": paths.sha256_file(self.art / "build_env.log"),
            "artifacts/validation.log": paths.sha256_file(self.model_dir / "artifacts" / "validation.log"),
            "artifacts/missing.log": None,
            "/etc/passwd": None,
        }
        copy = json.loads((self.entry_dir(entry_id) / "proposal.json").read_text(encoding="utf-8"))
        self.assertEqual(copy["evidence_sha256"], expected)
        self.assertEqual(copy["evidence"], ["artifacts/build_env.log:2", "artifacts/validation.log", "artifacts/missing.log", "/etc/passwd"])
        self.assertEqual(self.meta(entry_id)["evidence_sha256"], expected)

    def test_derived_from_uses_inject_rows_whose_unit_is_in_claims(self) -> None:
        self.build_run()
        self.write_usage([
            {"kind": "inject", "run_id": RUN_ID, "skill": "sure_onboard", "unit": "build_env", "attempt": 1, "events_cutoff": 400,
             "entries": [{"entry_id": "sure_onboard/cuda-runtime-mismatch", "shared": False}], "at": "2026-08-18T11:00:00Z"},
            {"kind": "inject", "run_id": RUN_ID, "skill": "sure_onboard", "unit": "validate_load", "attempt": 1, "events_cutoff": 700,
             "entries": [{"entry_id": "sure_onboard/wrong-entrypoint", "shared": False}], "at": "2026-08-18T11:20:00Z"},
            {"kind": "pre_start", "run_id": RUN_ID, "skill": "sure_onboard",
             "entries": [{"entry_id": "_shared/vc-partition-names", "shared": True}], "at": "2026-08-18T10:00:00Z"},
            {"kind": "settle", "run_id": RUN_ID, "skill": "sure_onboard", "unit": "build_env",
             "entry_id": "sure_onboard/cuda-runtime-mismatch", "outcome": "useful_activated", "at": "2026-08-18T11:05:00Z"},
        ])
        entry_id = self.publish().published[0]
        self.assertEqual(self.meta(entry_id)["derived_from"], ["sure_onboard/cuda-runtime-mismatch"])

    def test_fix_exercised_true_only_when_component_passed_after_retry(self) -> None:
        for component, expected in (("build_env", True), ("validate_import", False)):
            with self.subTest(component=component):
                self.tearDown()
                self.setUp()
                self.build_run([candidate(component=component)])
                entry_id = self.publish().published[0]
                self.assertIs(self.meta(entry_id)["fix_exercised"], expected)

    def test_decisions_gets_one_publish_row(self) -> None:
        self.build_run()
        entry_id = self.publish().published[0]
        rows = self.decisions()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["action"], "publish")
        self.assertEqual(row["entry_id"], entry_id)
        self.assertEqual(row["run_id"], RUN_ID)
        self.assertEqual(row["candidate"], "01-no-kernel-image")
        self.assertEqual((row["skill"], row["target_skill"], row["type"], row["op"]), ("sure_onboard", "sure_onboard", "bad_case", "add"))
        self.assertEqual(row["by"], "auto")
        self.assertRegex(row["at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertIsNone(row["target_entry"])
        # Same key set as paths.decision_row would build (§1.6): no "kind", no extra keys.
        self.assertEqual(
            sorted(row), sorted(["action", "entry_id", "by", "at", "run_id", "skill", "target_skill", "type", "op", "target_entry", "candidate"])
        )

    def test_second_publish_of_same_run_is_a_noop(self) -> None:
        self.build_run()
        first = self.publish()
        second = self.publish()
        self.assertEqual(second.published, [])
        self.assertEqual(second.skipped_reason, "already_published")
        self.assertEqual(second.errors, [])
        self.assertEqual(len(self.decisions()), 1)
        self.assertEqual(sorted(p.name for p in (self.root / "provisional" / "sure_onboard").iterdir()), [first.published[0].split("/")[1]])

    def test_fact_candidate_header_uses_underscore_cell_and_empty_trigger(self) -> None:
        self.build_run([fact_candidate()])
        report = self.publish()
        self.assertEqual(report.errors, [])
        self.assertEqual(report.published, ["_shared/long-vc-jobs-go-to-partition-site-gpu-openbench-kills-them-a"])
        lines = (self.entry_dir(report.published[0]) / "entry.md").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "Trigger:")
        self.assertEqual(lines[1], "Cell: _shared/_ x n.a.")
        self.assertIn("Scope: cluster", lines)
        meta = self.meta(report.published[0])
        self.assertEqual((meta["type"], meta["scope"], meta["checked_at"]), ("fact", "cluster", "2026-08-18"))
        self.assertEqual(meta["applies_to"], ["sure_onboard", "sure_eval"])
        self.assertIs(meta["fix_exercised"], False)
        self.assertEqual(meta["hook_trigger"], [])

    def test_the_fact_shared_pairing_is_re_checked_here(self) -> None:
        """Gate rule 1: a fact lives in _shared, a bad_case never does. proposal.json sits on disk
        between the gate and post_finish, and publish is what turns target_skill into an entry id
        while cli export routes on `type`, so an edited pairing yields one lesson under two ids."""
        fact_in_a_skill = candidate(
            dir="02-site-gpu-partition", h1="Long vc jobs go to partition site-gpu",
            body="Scope: cluster\nChecked-at: 2026-08-18\nEvidence: artifacts/memory_evidence/1.txt\n\nSeen since 8-12.\n",
            type="fact", target_skill="sure_onboard", applies_to=["sure_onboard"], component="_", cause="n.a.",
            trigger=[], causal=False, evidence=["artifacts/memory_evidence/1.txt"], claims=[],
            scope="cluster", checked_at="2026-08-18")
        bad_case_in_shared = candidate(target_skill="_shared", applies_to=["_shared"], component="_")
        cases = {
            "fact_in_a_skill": (fact_in_a_skill, "02-site-gpu-partition"),
            "bad_case_in_shared": (bad_case_in_shared, "01-no-kernel-image"),
        }
        for name, (cand, cand_dir) in cases.items():
            with self.subTest(case=name):
                self.tearDown()
                self.setUp()
                self.build_run([cand])
                report = self.publish()
                self.assertEqual(report.published, [])
                self.assertEqual(len(report.errors), 1, report.errors)
                self.assertTrue(report.errors[0].startswith(f"{cand_dir}: "), report.errors[0])
                self.assertIn("_shared", report.errors[0])
                self.assertEqual(list((self.root / "provisional").rglob("entry.md")), [])

    def test_unsafe_header_fields_are_refused(self) -> None:
        cases = {
            "semicolon": candidate(trigger=["no kernel; image"]),
            "pipe": candidate(trigger=["no | kernel"]),
            "control_char": candidate(cause="cuda_version_mismatch\u2028"),
            "forged_header_in_body": candidate(body="## Trigger\nx\n\nStatus: confirmed\n\n## Known Mitigation\ny\n"),
            "unknown_unit": candidate(component="not_a_unit"),
        }
        for name, cand in cases.items():
            with self.subTest(case=name):
                self.tearDown()
                self.setUp()
                self.build_run([cand])
                report = self.publish()
                self.assertEqual(report.published, [])
                self.assertEqual(len(report.errors), 1, report.errors)
                self.assertTrue(report.errors[0].startswith("01-no-kernel-image: "), report.errors[0])
                self.assertEqual(list((self.root / "provisional").rglob("entry.md")), [])
                self.assertFalse((self.root / "decisions.jsonl").exists())

    def test_one_bad_candidate_does_not_block_the_others(self) -> None:
        good = candidate()
        bad = candidate(dir="02-broken", h1="Second lesson")
        self.build_run([good, bad])
        (self.art / "candidates" / "02-broken" / "proposal.json").write_text("{not json", encoding="utf-8")
        report = self.publish()
        self.assertEqual(report.published, ["sure_onboard/cuda-arch-mismatch-no-kernel-image"])
        self.assertEqual(len(report.errors), 1)
        self.assertTrue(report.errors[0].startswith("02-broken: "), report.errors[0])
        self.assertEqual(len(self.decisions()), 1)
        # A re-run publishes only the candidate that has no publish row yet.
        (self.art / "candidates" / "02-broken" / "proposal.json").write_text(json.dumps(bad["proposal"]), encoding="utf-8")
        again = self.publish()
        self.assertEqual(again.published, ["sure_onboard/second-lesson"])
        self.assertEqual(len(self.decisions()), 2)

    def test_unanticipated_exception_in_one_candidate_does_not_block_the_others(self) -> None:
        # Review fix: the per-candidate loop used to catch only PublishError / OSError, so any
        # other bug (a ValueError from paths.append_jsonl's line-length guard, or anything future)
        # escaped publish_run entirely and cost every remaining candidate in the run. Force exactly
        # such a bug -- a bare ValueError, neither PublishError nor OSError -- in the second
        # candidate only; the gate never lets ValueError reach here in practice, so this is
        # deliberately synthetic (mirrors the "any future bug" case named in the finding).
        good = candidate()
        bad = candidate(dir="02-broken", h1="Second lesson")
        self.build_run([good, bad])
        original = publish.split_proposal_md

        def boom(md_text: str):
            if "Second lesson" in md_text:
                raise ValueError("synthetic failure for the test")
            return original(md_text)

        with mock.patch.object(publish, "split_proposal_md", side_effect=boom):
            report = self.publish()
        self.assertEqual(report.published, ["sure_onboard/cuda-arch-mismatch-no-kernel-image"])
        self.assertEqual(report.errors, ["02-broken: ValueError"])
        self.assertEqual(len(self.decisions()), 1)
        self.assertEqual(
            sorted(p.name for p in (self.root / "provisional" / "sure_onboard").iterdir()),
            ["cuda-arch-mismatch-no-kernel-image"],
        )

    def test_digest_copy_failure_does_not_leak_a_host_path(self) -> None:
        # Review fix: _copy_digest's OSError handler used to interpolate {exc}, and
        # atomic_write_bytes's os.replace embeds both the source and destination paths in an
        # OSError's default message when the destination is not a plain file (on Windows this
        # error carries two filenames, so OSError.__str__ renders them with repr() -- backslashes
        # doubled -- which defeats a plain `str(self.repo) in message` substring check; assert the
        # exact redacted shape instead of absence-of-substring, so the test cannot be fooled by
        # that formatting quirk on either platform). Force the failure by pre-creating the
        # digest's destination as a directory.
        self.build_run()
        (self.root / "digests").mkdir(parents=True)
        (self.root / "digests" / f"{RUN_ID}.json").mkdir()
        report = self.publish()
        self.assertEqual(report.published, ["sure_onboard/cuda-arch-mismatch-no-kernel-image"])
        self.assertEqual(len(report.errors), 1)
        self.assertRegex(report.errors[0], r"^digest copy: [A-Za-z]+$")

    def test_unreadable_candidate_file_names_the_bare_filename_without_a_host_path(self) -> None:
        # Review fix: _load_candidate's raise used to interpolate {exc}, and FileNotFoundError's
        # default message embeds the full absolute path of the file it could not open. Delete each
        # candidate file in turn (the directory otherwise stays intact) and assert the resulting
        # PublishError names the bare filename and the exception class only, never the host path.
        for filename in ("proposal.json", "proposal.md"):
            with self.subTest(file=filename):
                self.tearDown()
                self.setUp()
                self.build_run()
                target = self.art / "candidates" / "01-no-kernel-image" / filename
                target.unlink()
                report = self.publish()
                self.assertEqual(report.published, [])
                self.assertEqual(len(report.errors), 1)
                self.assertTrue(
                    report.errors[0].startswith(f"01-no-kernel-image: {filename} is unreadable: "),
                    report.errors[0],
                )
                self.assertNotIn(str(self.repo), report.errors[0])
                self.assertNotIn(str(target), report.errors[0])

    def test_write_failure_in_one_candidate_does_not_leak_a_host_path(self) -> None:
        # Review fix (site 6): the per-candidate loop's bare OSError branch used to interpolate
        # {exc} too -- the most-travelled leak path in the file. _claim_entry_dir's os.mkdir only
        # catches FileExistsError, and any OSError from writing entry.md / proposal.json / meta /
        # the decision row inside _publish_candidate's own try/except only cleans up and
        # re-raises without redacting, so a PermissionError's default message (which embeds the
        # absolute path it could not write) used to reach this branch intact. Permission failures
        # are realistic here -- paths.fix_perms / group_writable exist precisely because they
        # happen on a shared checkout. Force it by making the entry.md write raise exactly such a
        # PermissionError, and assert the path never reaches report.errors.
        self.build_run()
        secret = str(self.repo / "definitely-not-under-dot-sure-runs" / "entry.md")
        with mock.patch.object(paths, "atomic_write_text", side_effect=PermissionError(13, "denied", secret)):
            report = self.publish()
        self.assertEqual(report.published, [])
        self.assertEqual(report.errors, ["01-no-kernel-image: PermissionError"])
        self.assertNotIn(secret, json.dumps(report.errors))
        self.assertEqual(list((self.root / "provisional").rglob("entry.md")), [])


class KilledPublishTests(PublishFixture):
    """A process death between the meta write and the publish row (SIGKILL, a timeout kill): the
    entry looks live to cli list and to cli confirm, the index never sees it, and a re-publish of
    the same run would otherwise store the same lesson a second time under <slug>-2. A death one
    step earlier, before the meta write, leaves a directory no meta and no row records at all."""

    SLUG = "cuda-arch-mismatch-no-kernel-image"
    ENTRY_ID = f"sure_onboard/{SLUG}"

    def kill_at_the_decision_row(self) -> None:
        def die(root, row):
            raise SystemExit(1)  # BaseException: _publish_candidate's `except Exception` never sees it

        self.build_run()
        with mock.patch.object(paths, "append_decision", die), self.assertRaises(SystemExit):
            self.publish()
        self.assertTrue((self.entry_dir(self.ENTRY_ID) / "entry.md").is_file())
        self.assertEqual(self.meta(self.ENTRY_ID)["status"], "provisional")  # live-looking leftovers
        self.assertFalse((self.root / "decisions.jsonl").exists())

    def kill_before_the_meta_write(self) -> None:
        """The step earlier: entry.md and proposal.json are on disk, meta never happened."""
        meta_path = self.root / "meta" / "sure_onboard" / f"{self.SLUG}.json"
        real = paths.atomic_write_json

        def die(path, obj):
            if path == meta_path:
                raise SystemExit(1)  # BaseException, like the kill above
            real(path, obj)

        self.build_run()
        with mock.patch.object(paths, "atomic_write_json", die), self.assertRaises(SystemExit):
            self.publish()
        self.assertTrue((self.entry_dir(self.ENTRY_ID) / "entry.md").is_file())
        self.assertEqual(list((self.root / "meta").rglob("*.json")), [])  # nothing names the slug

    def slugs(self) -> list[str]:
        return sorted(p.name for p in (self.root / "provisional" / "sure_onboard").iterdir())

    def test_the_next_publish_of_the_same_run_reclaims_the_orphan(self) -> None:
        self.kill_at_the_decision_row()
        report = self.publish()
        self.assertEqual(report.errors, [])
        self.assertEqual(report.published, [self.ENTRY_ID])
        self.assertEqual(self.slugs(), [self.SLUG])  # the same slug, not <slug>-2
        publish_rows = [r for r in self.decisions() if r["action"] == "publish"]
        self.assertEqual([r["entry_id"] for r in publish_rows], [self.ENTRY_ID])
        self.assertEqual(self.meta(self.ENTRY_ID)["entry_sha256"],
                         paths.sha256_file(self.entry_dir(self.ENTRY_ID) / "entry.md"))

    def test_an_entry_a_human_already_acted_on_is_left_alone(self) -> None:
        self.kill_at_the_decision_row()
        meta = self.meta(self.ENTRY_ID)  # what cli confirm would have written
        meta["status"] = "confirmed"
        meta["confirmed"] = {"by": "human", "date": "2026-08-19"}
        paths.atomic_write_json(self.root / "meta" / "sure_onboard" / f"{self.SLUG}.json", meta)
        paths.atomic_write_text(self.root / "outbox" / "sure_onboard" / self.SLUG / "entry.md", "# staged\n")
        report = self.publish()
        self.assertEqual(report.published, [f"{self.ENTRY_ID}-2"])
        self.assertEqual(self.slugs(), [self.SLUG, f"{self.SLUG}-2"])
        self.assertEqual(self.meta(self.ENTRY_ID)["status"], "confirmed")

    def test_an_entry_of_another_run_is_never_reclaimed(self) -> None:
        self.kill_at_the_decision_row()
        meta = self.meta(self.ENTRY_ID)
        meta["created"] = {"run_id": "20260101-000000-other000", "date": "2026-01-01"}
        paths.atomic_write_json(self.root / "meta" / "sure_onboard" / f"{self.SLUG}.json", meta)
        report = self.publish()
        self.assertEqual(report.published, [f"{self.ENTRY_ID}-2"])
        self.assertTrue((self.root / "meta" / "sure_onboard" / f"{self.SLUG}.json").is_file())

    def test_a_headless_entry_dir_is_swept_no_matter_which_run_left_it(self) -> None:
        # Without a meta there is nothing to scope by run: the meta-driven reclaim above cannot
        # see the directory at all, while _entry_taken can, so every later publish of this slug --
        # from any run, forever -- used to land on <slug>-2 and the litter stayed.
        self.kill_before_the_meta_write()
        self.run_dir = self.run_dir.parent / "20260101-000000-other000"
        self.art = self.run_dir / "artifacts"
        self.build_run()
        report = self.publish()
        self.assertEqual(report.errors, [])
        self.assertEqual(report.published, [self.ENTRY_ID])  # the slug itself, not <slug>-2
        self.assertEqual(self.slugs(), [self.SLUG])
        self.assertEqual(self.meta(self.ENTRY_ID)["entry_sha256"],
                         paths.sha256_file(self.entry_dir(self.ENTRY_ID) / "entry.md"))


class ModifySupersedeTests(PublishFixture):
    def target_file(self) -> Path:
        return self.repo / "sure" / "skills" / "sure_onboard" / "references" / "memory" / "bad_cases" / "no-kernel-image.md"

    def test_modify_and_supersede_land_in_provisional_without_touching_target(self) -> None:
        for op in ("modify", "supersede"):
            with self.subTest(op=op):
                self.tearDown()
                self.setUp()
                self.target_file().write_text("# Old entry\n\nTrigger: no kernel image\nStatus: confirmed\n\n## Trigger\nold\n", encoding="utf-8")
                before = self.target_file().read_bytes()
                self.build_run([candidate(h1=f"Better fix ({op})", op=op, target_entry="sure_onboard/no-kernel-image")])
                report = self.publish()
                self.assertEqual(report.errors, [])
                self.assertEqual(report.published, [f"sure_onboard/better-fix-{op}"])
                self.assertEqual(self.target_file().read_bytes(), before)
                self.assertEqual(sorted(p.name for p in self.target_file().parent.iterdir()), ["no-kernel-image.md"])
                self.assertEqual(list((self.root / "outbox").iterdir()), [])
                meta = self.meta(report.published[0])
                self.assertEqual((meta["op"], meta["target_entry"], meta["status"]), (op, "sure_onboard/no-kernel-image", "provisional"))
                self.assertFalse(meta["orphan"])

    def test_orphan_flag_when_target_already_rejected(self) -> None:
        (self.root / "rejected" / "sure_onboard" / "no-kernel-image").mkdir(parents=True)
        self.build_run([candidate(h1="Better fix", op="modify", target_entry="sure_onboard/no-kernel-image")])
        report = self.publish()
        self.assertEqual(report.published, ["sure_onboard/better-fix"])
        self.assertTrue(self.meta("sure_onboard/better-fix")["orphan"])

    def test_mark_orphans_flags_children_of_rejected_target(self) -> None:
        self.build_run([
            candidate(h1="Better fix", op="modify", target_entry="sure_onboard/no-kernel-image"),
            candidate(dir="02-unrelated", h1="Unrelated add"),
        ])
        report = self.publish()
        self.assertEqual(report.errors, [])
        self.assertFalse(self.meta("sure_onboard/better-fix")["orphan"])
        self.assertEqual(publish.mark_orphans(self.repo, "sure_onboard/no-kernel-image"), ["sure_onboard/better-fix"])
        self.assertTrue(self.meta("sure_onboard/better-fix")["orphan"])
        self.assertFalse(self.meta("sure_onboard/unrelated-add")["orphan"])
        self.assertEqual(publish.mark_orphans(self.repo, "sure_onboard/no-kernel-image"), [])


class SkipTests(PublishFixture):
    def test_skips_when_declaration_missing_but_still_copies_digest(self) -> None:
        self.build_run(declaration=False)
        report = self.publish()
        self.assertEqual((report.published, report.skipped_reason, report.errors), ([], "no_declaration", []))
        self.assertTrue((self.root / "digests" / f"{RUN_ID}.json").is_file())
        self.assertEqual(list((self.root / "provisional").iterdir()), [])
        self.assertEqual(list((self.root / "meta").iterdir()), [])
        self.assertFalse((self.root / "decisions.jsonl").exists())

    def test_skips_when_no_new_lessons(self) -> None:
        self.build_run([], no_new_lessons=True)
        report = self.publish()
        self.assertEqual((report.published, report.skipped_reason, report.errors), ([], "no_new_lessons", []))
        self.assertTrue((self.root / "digests" / f"{RUN_ID}.json").is_file())
        self.assertEqual(list((self.root / "provisional").iterdir()), [])
        self.assertFalse((self.root / "decisions.jsonl").exists())

    def test_missing_run_dir_is_an_error(self) -> None:
        report = self.publish()
        self.assertEqual(report.published, [])
        self.assertEqual(report.skipped_reason, "no_run_dir")
        self.assertEqual(len(report.errors), 1)
        self.assertFalse(self.root.exists())
        # Review fix: the message used to embed the resolved host path; only the run id (the
        # useful part, already in the summary's run_id field) may appear.
        self.assertIn(RUN_ID, report.errors[0])
        self.assertNotIn(str(self.repo), report.errors[0])
        self.assertNotIn(str(self.tmp.name), report.errors[0])


class MainTests(PublishFixture):
    def run_main(self, *extra: str) -> tuple[int, dict, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = publish.main(["--run-dir", str(self.run_dir), "--repo-root", str(self.repo), *extra])
        return code, json.loads(out.getvalue()), err.getvalue()

    def test_main_no_promote_publishes_rebuilds_index_and_prints_summary(self) -> None:
        self.build_run()
        code, summary, err = self.run_main("--no-promote")
        self.assertEqual(code, 0, err)
        self.assertEqual(summary["schema"], "sure.memory.publish_summary.v1")
        self.assertEqual(summary["run_id"], RUN_ID)
        self.assertEqual(summary["published"], ["sure_onboard/cuda-arch-mismatch-no-kernel-image"])
        self.assertIsNone(summary["skipped_reason"])
        self.assertEqual(summary["errors"], [])
        self.assertEqual(summary["promoted"], 0)
        self.assertTrue(summary["index_rebuilt"])
        self.assertTrue((self.root / "index.json").is_file())
        self.assertEqual(err, "")

    def test_main_exit_1_and_stderr_when_a_candidate_fails(self) -> None:
        self.build_run([candidate(trigger=["bad;trigger"])])
        code, summary, err = self.run_main("--no-promote")
        self.assertEqual(code, 1)
        self.assertEqual(summary["published"], [])
        self.assertEqual(len(summary["errors"]), 1)
        self.assertIn("publish_memory: 01-no-kernel-image: ", err)

    def test_crash_summary_never_leaks_the_exception_message(self) -> None:
        # Review fix: main()'s top-level handler used to interpolate {exc} into the summary's
        # single error line. An OSError (or anything else) raised out of publish_run can carry an
        # absolute host path in its message; force exactly that and assert it never reaches the
        # JSON stdout reads or stderr, only the exception's class name.
        self.build_run()
        paths.ensure_memory_tree(self.root)
        secret = str(self.repo / "definitely-not-under-dot-sure-runs")
        with mock.patch.object(publish, "publish_run", side_effect=RuntimeError(f"boom at {secret}")):
            code, summary, err = self.run_main("--no-promote")
        self.assertEqual(code, 1)
        self.assertEqual(summary["skipped_reason"], "publish_crashed")
        self.assertEqual(summary["errors"][0], "publish: RuntimeError")
        self.assertNotIn(secret, json.dumps(summary))
        self.assertNotIn(secret, err)

    def test_promote_crash_does_not_leak_a_host_path(self) -> None:
        # Review fix: main()'s promote: crash handler used to interpolate {exc} too. Imported
        # locally so a problem in promote.py (owned by a parallel task) cannot break every other
        # test in this file at module load time -- only this one test depends on it importing.
        self.build_run()
        from memory import promote as promote_module

        secret = str(self.repo / "definitely-not-under-dot-sure-runs")
        with mock.patch.object(promote_module, "promote_all", side_effect=RuntimeError(f"boom at {secret}")):
            code, summary, err = self.run_main()
        self.assertIn("promote: RuntimeError", summary["errors"])
        self.assertNotIn(secret, json.dumps(summary))
        self.assertNotIn(secret, err)

    def test_index_crash_does_not_leak_a_host_path(self) -> None:
        # Review fix: main()'s index: crash handler used to interpolate {exc} too.
        self.build_run()
        from memory import index as index_module

        secret = str(self.repo / "definitely-not-under-dot-sure-runs")
        with mock.patch.object(index_module, "build_index", side_effect=RuntimeError(f"boom at {secret}")):
            code, summary, err = self.run_main("--no-promote")
        self.assertIn("index: RuntimeError", summary["errors"])
        self.assertNotIn(secret, json.dumps(summary))
        self.assertNotIn(secret, err)

    def test_main_prunes_both_stores_and_reports_what_it_dropped(self) -> None:
        self.build_run()
        self.seed_usage_runs(ENTRY_ID, count=10)
        self.seed_digests(10)
        config = dict(self.config)
        config["usage_retain_runs"] = 4
        config["digest_retain_runs"] = 2
        with mock.patch.object(publish.paths, "load_config", return_value=config):
            code, summary, err = self.run_main("--no-promote")
        self.assertEqual(code, 0, err)
        self.assertEqual(summary["pruned"], {"usage": 6, "digests": 9, "errors": []})
        self.assertEqual(len(list((self.root / "usage").glob("*.jsonl"))), 4)
        # 10 seeded plus this run's own copy, kept down to the digest retention count.
        self.assertEqual(len(list((self.root / "digests").glob("*.json"))), 2)
        self.assertTrue((self.root / "digests" / f"{RUN_ID}.json").is_file())

    def test_a_prune_failure_is_reported_without_failing_the_run_or_leaking_a_path(self) -> None:
        # Retention is housekeeping. A store it could not trim is worth saying, but the run's
        # candidates published fine and post_finish must not read that as a publish failure.
        self.build_run()
        secret = str(self.repo / "definitely-not-under-dot-sure-runs")
        with mock.patch.object(publish.usage, "prune_usage", side_effect=RuntimeError(f"boom at {secret}")):
            code, summary, err = self.run_main("--no-promote")
        self.assertEqual(code, 0, err)
        self.assertEqual(summary["errors"], [])
        self.assertEqual(summary["pruned"]["errors"], ["usage: RuntimeError"])
        self.assertNotIn(secret, json.dumps(summary))
        self.assertNotIn(secret, err)

    def test_wrapper_scripts_forward_to_publish_main(self) -> None:
        for skill in ("sure_onboard", "sure_eval"):
            with self.subTest(skill=skill):
                wrapper = REPO_ROOT / "sure" / "skills" / skill / "scripts" / "publish_memory.py"
                proc = subprocess.run([sys.executable, str(wrapper), "--help"], capture_output=True, text=True, check=False)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn("--no-promote", proc.stdout)
                self.assertIn("--run-dir", proc.stdout)


class PruneDigestsTests(PublishFixture):
    """digests/<run_id>.json is per-run scratch nothing replays, so it is pruned harder
    than usage/ and simply deleted: there is no count in it to carry forward."""

    def digests(self) -> list[str]:
        return sorted(p.name for p in (self.root / "digests").glob("*.json"))

    def test_only_the_newest_digests_survive(self) -> None:
        paths.ensure_memory_tree(self.root)
        self.seed_digests(12)
        report = publish.prune_digests(self.root, retain=3)
        self.assertEqual(self.digests(), [
            "20260810-100000-run09.json", "20260811-100000-run10.json", "20260812-100000-run11.json",
        ])
        self.assertEqual(len(report.pruned), 9)
        self.assertEqual(report.errors, [])

    def test_a_store_inside_the_retention_count_is_left_alone(self) -> None:
        paths.ensure_memory_tree(self.root)
        self.seed_digests(3)
        self.assertEqual(publish.prune_digests(self.root, retain=5).pruned, [])
        self.assertEqual(len(self.digests()), 3)

    def test_digests_are_kept_for_fewer_runs_than_usage_by_default(self) -> None:
        self.assertLess(publish.DEFAULT_DIGEST_RETAIN_RUNS, usage.DEFAULT_USAGE_RETAIN_RUNS)

    def test_a_digest_that_cannot_be_deleted_is_reported_without_a_host_path(self) -> None:
        paths.ensure_memory_tree(self.root)
        self.seed_digests(6)
        with mock.patch.object(Path, "unlink", side_effect=PermissionError(13, "Permission denied")):
            report = publish.prune_digests(self.root, retain=2)
        self.assertEqual(report.pruned, [])
        self.assertEqual(len(report.errors), 4)
        for line in report.errors:
            self.assertNotIn(str(self.root), line)
            self.assertNotIn(str(self.tmp.name), line)


class PromotionSurvivesRetentionTests(PublishFixture):
    """Retention is only allowed if it cannot lose a promotion. Spec 8.2 promotes on
    useful_activated from distinct runs, and those runs are exactly what pruning drops."""

    def promote_now(self) -> list[dict]:
        # Imported locally for the same reason the crash tests above do it: promote.py belongs
        # to a parallel task and must not be able to break this file at module load time.
        from memory import promote as promote_module

        return promote_module.promote_all(self.repo, config=self.config)

    def earn_a_promotion(self) -> None:
        self.build_run()
        self.publish()
        self.seed_usage_runs(ENTRY_ID, count=10, useful=("run-00", "run-01"))

    def test_promotion_still_fires_after_the_runs_that_earned_it_are_pruned(self) -> None:
        self.earn_a_promotion()
        report = usage.prune_usage(self.root, retain=4)
        self.assertIn("run-00", report.pruned)
        self.assertIn("run-01", report.pruned)
        self.assertFalse((self.root / "usage" / "run-00.jsonl").exists())
        rows = self.promote_now()
        self.assertEqual([row["action"] for row in rows], ["promote"])
        meta = self.meta(ENTRY_ID)
        self.assertEqual(meta["status"], "confirmed")
        self.assertEqual(meta["useful_activated"], 2)
        self.assertEqual(meta["useful_runs"], ["run-00", "run-01"])
        self.assertTrue((self.root / "outbox" / "sure_onboard" / SLUG / "entry.md").is_file())

    def test_a_useful_row_landing_between_the_fold_and_the_delete_still_promotes(self) -> None:
        # The one window in which a prune could still cost a promotion: match.ts appends usage
        # rows without the memory lock, so the run that earns the second distinct useful_activated
        # can extend its file after prune_usage has folded it and before the delete.
        self.build_run()
        self.publish()
        self.seed_usage_runs(ENTRY_ID, count=10, useful=("run-00",))  # one distinct useful run so far
        landed: list[bool] = []
        real_write = paths.atomic_write_json

        def land_the_second_useful_run(path: Path, obj: object) -> None:
            real_write(path, obj)
            if not landed:
                landed.append(True)
                paths.append_jsonl(self.root / "usage" / "run-01.jsonl", {
                    "kind": "settle", "run_id": "run-01", "skill": "sure_onboard", "unit": "build_env",
                    "entry_id": ENTRY_ID, "outcome": "useful_activated", "at": "2026-09-01T10:05:00Z",
                }, 4096)

        with mock.patch.object(paths, "atomic_write_json", side_effect=land_the_second_useful_run):
            report = usage.prune_usage(self.root, retain=4)
        self.assertTrue(landed, "the archive was never written, so the window was never opened")
        self.assertEqual([row["action"] for row in self.promote_now()], ["promote"])
        meta = self.meta(ENTRY_ID)
        self.assertEqual(meta["useful_activated"], 2)
        self.assertEqual(meta["useful_runs"], ["run-00", "run-01"])
        self.assertNotIn("run-01", report.pruned)  # left on disk, with its folded prefix marked

    def test_the_same_promotion_is_lost_when_the_archive_is_thrown_away(self) -> None:
        # The control: the rows that earned it really are gone from usage/, so the test above
        # passes because of what the archive carries and nothing else.
        self.earn_a_promotion()
        usage.prune_usage(self.root, retain=4)
        usage.archive_path(self.root).unlink()
        self.assertEqual(self.promote_now(), [])
        self.assertEqual(self.meta(ENTRY_ID)["status"], "provisional")
        self.assertEqual(self.meta(ENTRY_ID)["useful_activated"], 0)


if __name__ == "__main__":
    unittest.main()


class FencedH1Tests(unittest.TestCase):
    """The gate's parser skips fenced lines before looking for the H1; publish's did not, so a
    proposal that opens with a fenced sample containing a '# ' line passed the gate with the real
    title and was then published under the sample line. The entry id, the index title and the one
    line the hooks inject all come from that, and the id is fixed at publish, so the wrong title is
    permanent."""

    FENCED = (
        "```text\n"
        "# uv sync\n"
        "```\n"
        "\n"
        "# uv sync fails behind the proxy\n"
        "\n"
        "## Trigger\n"
        "certificate verify failed\n"
    )

    def test_the_h1_inside_a_fence_is_not_the_title(self) -> None:
        title, body = publish.split_proposal_md(self.FENCED)
        self.assertEqual(title, "uv sync fails behind the proxy")
        self.assertNotIn("```", body[0])

    def test_publish_and_the_gate_agree_on_the_title(self) -> None:
        self.assertEqual(publish.split_proposal_md(self.FENCED)[0], proposals.parse_bad_case(self.FENCED).title)

    def test_a_fence_after_the_h1_still_reaches_the_body(self) -> None:
        title, body = publish.split_proposal_md("# real title\n\n```text\n# not a title\n```\n")
        self.assertEqual(title, "real title")
        self.assertIn("# not a title", body)
