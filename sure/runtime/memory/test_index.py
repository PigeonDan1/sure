# sure/runtime/memory/test_index.py
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # sure/runtime

from memory import index, paths  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN = paths.LIB_DIR / "fixtures" / "golden_index.json"
CONFIG = paths.load_config()
UNITS = paths.load_units()

# --- fixture builders -----------------------------------------------------------------
# Every file is written through paths.atomic_write_text so the bytes are LF on Windows and Linux
# alike: sources_sha256 in the committed golden index depends on the exact bytes.


def header(trigger: str, cell: str, source: str, added: str, status: str, superseded_by: str | None = None) -> str:
    lines = [f"Trigger: {trigger}", f"Cell: {cell}", f"Source: {source}", f"Added: {added}", f"Status: {status}"]
    if superseded_by:
        lines.append(f"Superseded-by: {superseded_by}")
    return "\n".join(lines) + "\n\n"


def bad_case_body(title: str, trigger_line: str) -> str:
    return (
        f"# {title}\n\n## Trigger\n\n- `{trigger_line}`\n\n## Affected Step\n\n- build_env\n\n"
        "## Minimum Evidence\n\n- artifacts/build_env.log\n\n## Known Mitigation\n\n- Rebuild with the matching wheel.\n\n"
        "## Verification\n\n- python -c \"import torch\"\n"
    )


def fact_body(title: str, scope: str, checked_at: str) -> str:
    return f"# {title}\n\nScope: {scope}\nChecked-at: {checked_at}\nEvidence: artifacts/memory_evidence/1.txt\n"


def write(path: Path, text: str) -> None:
    paths.atomic_write_text(path, text)


def make_repo(tmp: Path) -> Path:
    """A fake repo root with the skills tree, three reference dirs and a sure/memory/ instance tree.
    No .sure/runs/ directory exists: the index never needs one."""
    repo = tmp / "repo"
    onboard = repo / "sure" / "skills" / "sure_onboard" / "references" / "memory" / "bad_cases"
    evald = repo / "sure" / "skills" / "sure_eval" / "references" / "memory" / "bad_cases"
    facts = repo / "sure" / "skills" / "_shared" / "memory" / "facts"
    root = repo / "sure" / "memory"

    # references: legacy headerless, legacy with header, confirmed with header + meta, superseded
    write(onboard / "legacy_headerless.md", bad_case_body("Legacy Headerless Case", "no kernel image is available"))
    write(onboard / "no-kernel-image.md",
          header("no kernel image is available; CUDA error: no kernel image", "sure_onboard/build_env x cuda_version_mismatch",
                 "legacy", "2026-08-18", "confirmed")   # `Source: legacy` without an arrow: the form Task 16 writes
          + bad_case_body("CUDA arch mismatch: no kernel image", "no kernel image is available"))
    write(onboard / "partition-not-found.md",
          header("partition not found", "sure_onboard/validate_env_compat x infra",
                 "run-20260801-aaaa → qwen-audio", "2026-08-01", "confirmed")
          + bad_case_body("vc submit rejects the queue alias", "partition not found"))
    write(onboard / "old-partition-name.md",
          header("partition not found: 3090-data", "sure_onboard/BUILD_ENV x infra",
                 "run-20260701-zzzz → qwen-audio", "2026-07-01", "confirmed",
                 superseded_by="sure_onboard/partition-not-found (2026-08-17)")
          + bad_case_body("Old partition alias case", "partition not found: 3090-data"))
    write(onboard / "README.md", index.README_BOOTSTRAP
          + "| Legacy Headerless Case | `legacy_headerless.md` | keep me verbatim |\n"
          + "| a row for a file that no longer exists | `ghost.md` | should be dropped |\n")
    write(evald / "job-log-missing.md",
          header("job.log: No such file", "sure_eval/execute_wait x infra",
                 "run-20260805-eeee → qwen-audio", "2026-08-05", "confirmed")
          + bad_case_body("vc job log never appeared", "job.log: No such file"))
    write(facts / "vc-partition-names.md",
          header("", "_shared/_ x n.a.", "run-20260810-bbbb → cluster", "2026-08-10", "confirmed")
          + fact_body("The 3090 partition is named site-gpu", "cluster", "2020-01-01"))
    write(facts / "qwen-audio-needs-cu121.md",
          header("torch was compiled with cuda 12.1", "_shared/_ x n.a.", "run-20260811-cccc → qwen-audio", "2026-08-11", "confirmed")
          + fact_body("qwen-audio wheels need a cu121 torch build", "model_family:qwen-audio", "2099-01-01"))

    # meta for a reference entry: a confirmed file demoted to disputed in meta
    write_meta(root, "sure_eval/job-log-missing", status="disputed", injections=2, disputed=1, entry_sha256="unused-for-references")

    # provisional entries
    add_provisional(root, "sure_onboard", "pip-index-timeout", "Read timed out on the pip index", "ReadTimeoutError: HTTPSConnectionPool",
                    "sure_onboard/build_env x python_dependency_missing", "run-20260818-dddd → qwen-audio", "2026-08-18",
                    op="add", status="provisional", meta_ok=True, publish=True)
    add_provisional(root, "sure_onboard", "pip-index-timeout-v2", "Read timed out on the pip index (mirror fix)", "ReadTimeoutError: HTTPSConnectionPool",
                    "sure_onboard/build_env x python_dependency_missing", "run-20260819-eeee → qwen-audio", "2026-08-19",
                    op="modify", target_entry="sure_onboard/pip-index-timeout", status="provisional", meta_ok=True, publish=True)
    add_provisional(root, "sure_onboard", "pip-index-timeout-old", "First sighting of the pip index timeout", "ReadTimeoutError: HTTPSConnectionPool",
                    "sure_onboard/build_env x python_dependency_missing", "run-20260817-cccc → qwen-audio", "2026-08-17",
                    op="add", status="provisional", meta_ok=True, publish=True)
    # two triggers in the header, but publish (Task 6) only found the first one in the run digest,
    # so meta.hook_trigger is a strict subset: the only entry in the golden index where they differ
    add_provisional(root, "sure_eval", "smoke-oom", "Smoke test dies with CUDA out of memory",
                    "CUDA out of memory; evidence-only: dmesg oom-killer",
                    "sure_eval/smoke_test x resource_limit", "run-20260812-ffff → qwen-audio", "2026-08-12",
                    op="add", status="disputed", similar="sure_eval/job-log-missing", meta_ok=True, publish=True, disputed=1,
                    hook_trigger=["CUDA out of memory"])
    add_provisional(root, "sure_onboard", "no-meta", "No meta at all", "some trigger text here",
                    "sure_onboard/build_env x infra", "run-20260813-0001 → m", "2026-08-13", meta_ok=None, publish=True)
    add_provisional(root, "sure_onboard", "sha-mismatch", "Meta sha does not match", "another trigger text",
                    "sure_onboard/build_env x infra", "run-20260813-0002 → m", "2026-08-13", meta_ok=False, publish=True)
    add_provisional(root, "sure_onboard", "no-publish-row", "Never published", "third trigger text",
                    "sure_onboard/build_env x infra", "run-20260813-0003 → m", "2026-08-13", meta_ok=True, publish=False)
    add_provisional(root, "sure_onboard", "partition-not-found", "Provisional twin of a references entry", "partition not found",
                    "sure_onboard/validate_env_compat x infra", "run-20260813-0004 → m", "2026-08-13", meta_ok=True, publish=True)
    add_provisional(root, "sure_onboard", "rejected-one", "Rejected but still on disk", "rejected trigger text",
                    "sure_onboard/build_env x infra", "run-20260813-0005 → m", "2026-08-13", status="rejected", meta_ok=True, publish=True)
    # the twin shares the meta file with the references entry: valid sha for the twin, counts for both
    twin = root / "provisional" / "sure_onboard" / "partition-not-found" / "entry.md"
    write_meta(root, "sure_onboard/partition-not-found", status="confirmed", injections=5, useful_activated=3,
               component="validate_env_compat", trigger=["partition not found"], entry_sha256=paths.sha256_file(twin))
    # outbox copy is never scanned
    shutil.copytree(root / "provisional" / "sure_onboard" / "pip-index-timeout", root / "outbox" / "sure_onboard" / "pip-index-timeout")
    # a torn decisions line must not break anything
    with open(root / "decisions.jsonl", "ab") as handle:
        handle.write(b'{"action": "publish", "entry_id": "sure_onboard/tor')
    return repo


def write_meta(root: Path, entry_id: str, **fields) -> None:
    """A meta file shaped like Task 6 writes it. `hook_trigger` is deliberately absent from the base
    dict: a meta without that key must make the index fall back to the entry's own trigger list."""
    skill, slug = entry_id.split("/", 1)
    meta = {
        "schema": "sure.memory.meta.v1", "entry_id": entry_id, "type": "bad_case", "status": "provisional", "target_skill": skill,
        "applies_to": [skill], "component": "_", "cause": "infra", "trigger": [], "scope": None,
        "injections": 0, "useful_activated": 0, "useful_unattributed": 0, "useful_runs": [], "disputed": 0,
        "last_hit": None, "created": {"run_id": "run-x", "date": "2026-08-13"}, "confirmed": None, "exported": None,
        "derived_from": [], "fix_exercised": False, "evidence_sha256": {}, "superseded_by": None, "superseded_at": None,
        "checked_at": None, "entry_sha256": None, "op": "add", "target_entry": None, "similar_entry": None, "orphan": False,
    }
    meta.update(fields)
    paths.atomic_write_json(root / "meta" / skill / f"{slug}.json", meta)


def add_provisional(root: Path, skill: str, slug: str, title: str, trigger: str, cell: str, source: str, added: str, *,
                    op: str = "add", target_entry: str | None = None, similar: str | None = None, status: str = "provisional",
                    meta_ok: bool | None, publish: bool, disputed: int = 0, hook_trigger: list[str] | None = None) -> None:
    """One provisional entry: entry.md + proposal.json, optionally meta (meta_ok=None: no meta file;
    False: meta whose entry_sha256 does not match) and a publish row. `trigger` is the header text
    (`;`-separated); meta.hook_trigger defaults to the full list, pass `hook_trigger` for a subset."""
    entry_dir = root / "provisional" / skill / slug
    text = header(trigger, cell, source, added, "provisional") + bad_case_body(title, trigger)
    write(entry_dir / "entry.md", text)
    component, cause = cell.split("/", 1)[1].split(" x ")
    triggers = [t.strip() for t in trigger.split(";") if t.strip()]
    proposal = {
        "schema": "sure.memory.proposal.v2", "type": "bad_case", "op": op, "target_skill": skill, "target_entry": target_entry,
        "applies_to": [skill], "cell": {"component": component, "cause": cause}, "trigger": triggers, "causal": False,
        "evidence": ["artifacts/build_env.log"], "claims": [], "source": {"run_id": source.split(" ")[0], "skill": skill, "target": "qwen-audio", "digest_sha256": "0" * 64},
        "similar": {"entry": similar, "difference": "narrower"} if similar else None, "scope": None, "checked_at": None,
    }
    paths.atomic_write_json(entry_dir / "proposal.json", proposal)
    run_id = source.split(" ")[0]
    if meta_ok is not None:
        sha = paths.sha256_file(entry_dir / "entry.md") if meta_ok else "0" * 64
        write_meta(root, f"{skill}/{slug}", status=status, component=component, cause=cause, trigger=triggers,
                   hook_trigger=list(triggers) if hook_trigger is None else list(hook_trigger),
                   created={"run_id": run_id, "date": added}, entry_sha256=sha, disputed=disputed)
    if publish:
        # the same row shape publish.py writes (§1.6); `at` is pinned so the golden sources_sha256 is stable
        paths.append_decision(root, paths.decision_row("publish", f"{skill}/{slug}", "auto", run_id=run_id, at=f"{added}T00:00:00Z"))


def entry_map(idx: dict) -> dict[str, dict]:
    return {e["entry_id"]: e for e in idx["entries"]}


class IndexFixtureCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self.tmp.name))
        self.root = self.repo / "sure" / "memory"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def build(self) -> dict:
        return index.build_index(self.repo, config=CONFIG, units=UNITS)


class ParseEntryFileTests(IndexFixtureCase):
    def test_five_line_header_is_parsed(self) -> None:
        rec = index.parse_entry_file(self.repo / "sure/skills/sure_onboard/references/memory/bad_cases/partition-not-found.md",
                                     target_skill="sure_onboard", legacy_dir=True)
        self.assertEqual(rec.entry_id, "sure_onboard/partition-not-found")
        self.assertEqual(rec.trigger, ["partition not found"])
        self.assertEqual((rec.component, rec.cause), ("validate_env_compat", "infra"))
        self.assertEqual(rec.status, "confirmed")
        self.assertEqual(rec.created, {"run_id": "run-20260801-aaaa", "date": "2026-08-01"})
        self.assertEqual(rec.title, "vc submit rejects the queue alias")
        self.assertFalse(rec.legacy)
        self.assertEqual(rec.type, "bad_case")
        self.assertEqual(rec.applies_to, ["sure_onboard"])
        self.assertEqual(rec.hook_trigger, rec.trigger)  # parser only copies; meta may narrow it later

    def test_headerless_reference_is_legacy_with_empty_trigger(self) -> None:
        rec = index.parse_entry_file(self.repo / "sure/skills/sure_onboard/references/memory/bad_cases/legacy_headerless.md",
                                     target_skill="sure_onboard", legacy_dir=True)
        self.assertTrue(rec.legacy)
        self.assertEqual(rec.trigger, [])
        self.assertEqual(rec.component, "_")
        self.assertEqual(rec.status, "confirmed")
        self.assertEqual(rec.created, "legacy")
        self.assertEqual(rec.title, "Legacy Headerless Case")

    def test_source_legacy_header_is_legacy_but_keeps_triggers(self) -> None:
        rec = index.parse_entry_file(self.repo / "sure/skills/sure_onboard/references/memory/bad_cases/no-kernel-image.md",
                                     target_skill="sure_onboard", legacy_dir=True)
        self.assertTrue(rec.legacy)
        self.assertEqual(rec.trigger, ["no kernel image is available", "CUDA error: no kernel image"])
        self.assertEqual(rec.component, "build_env")
        self.assertEqual(rec.created, "legacy")

    def test_source_accepts_legacy_without_arrow_and_both_arrows(self) -> None:
        # Task 16 writes `Source: legacy` (no arrow); publish writes `<run_id> → <target>`; `->` is tolerated
        cases = {
            "legacy": (True, "legacy"),
            "legacy → legacy": (True, "legacy"),
            "run-20260901-abcd -> qwen-audio": (False, {"run_id": "run-20260901-abcd", "date": "2026-09-01"}),
            "run-20260901-abcd → qwen-audio": (False, {"run_id": "run-20260901-abcd", "date": "2026-09-01"}),
        }
        for n, (source, (legacy, created)) in enumerate(cases.items()):
            path = self.repo / f"src-{n}.md"
            write(path, header("partition not found", "sure_onboard/build_env x infra", source, "2026-09-01", "confirmed")
                  + bad_case_body("Source form case", "partition not found"))
            rec = index.parse_entry_file(path, target_skill="sure_onboard", legacy_dir=True)
            self.assertEqual((rec.legacy, rec.created), (legacy, created), source)
            self.assertEqual(rec.trigger, ["partition not found"], source)  # a legacy header keeps its triggers

    def test_superseded_by_sixth_line(self) -> None:
        rec = index.parse_entry_file(self.repo / "sure/skills/sure_onboard/references/memory/bad_cases/old-partition-name.md",
                                     target_skill="sure_onboard", legacy_dir=True)
        self.assertEqual(rec.status, "superseded")
        self.assertEqual(rec.superseded_by, "sure_onboard/partition-not-found")

    def test_fact_file_reads_scope_and_checked_at(self) -> None:
        rec = index.parse_entry_file(self.repo / "sure/skills/_shared/memory/facts/qwen-audio-needs-cu121.md",
                                     target_skill="_shared", legacy_dir=True)
        self.assertEqual(rec.type, "fact")
        self.assertEqual(rec.scope, "model_family:qwen-audio")
        self.assertEqual(rec.checked_at, "2099-01-01")
        self.assertEqual(rec.applies_to, ["_shared"])
        self.assertEqual(rec.trigger, ["torch was compiled with cuda 12.1"])

    def test_provisional_without_header_raises(self) -> None:
        bad = self.root / "provisional" / "sure_onboard" / "bare" / "entry.md"
        write(bad, "# Bare\n\nbody\n")
        with self.assertRaises(ValueError):
            index.parse_entry_file(bad, target_skill="sure_onboard", legacy_dir=False)

    def test_crlf_file_parses_the_same(self) -> None:
        src = self.repo / "sure/skills/sure_onboard/references/memory/bad_cases/partition-not-found.md"
        crlf = self.repo / "crlf.md"
        crlf.write_bytes(src.read_bytes().replace(b"\n", b"\r\n"))
        rec = index.parse_entry_file(crlf, target_skill="sure_onboard", legacy_dir=True)
        self.assertEqual(rec.trigger, ["partition not found"])
        self.assertEqual(rec.title, "vc submit rejects the queue alias")


class BuildIndexTests(IndexFixtureCase):
    def test_reference_entries_always_included_and_readme_skipped(self) -> None:
        ids = set(entry_map(self.build()))
        for expected in ("sure_onboard/legacy_headerless", "sure_onboard/no-kernel-image", "sure_onboard/partition-not-found",
                         "sure_onboard/old-partition-name", "sure_eval/job-log-missing", "_shared/vc-partition-names",
                         "_shared/qwen-audio-needs-cu121"):
            self.assertIn(expected, ids)
        self.assertNotIn("sure_onboard/README", ids)
        self.assertFalse((self.repo / ".sure").exists())  # the index never needs .sure/runs/

    def test_provisional_inclusion_rules(self) -> None:
        ids = set(entry_map(self.build()))
        self.assertIn("sure_onboard/pip-index-timeout", ids)          # meta + sha + publish row
        self.assertIn("sure_onboard/pip-index-timeout-v2", ids)
        self.assertIn("sure_eval/smoke-oom", ids)
        self.assertNotIn("sure_onboard/no-meta", ids)                 # no meta
        self.assertNotIn("sure_onboard/sha-mismatch", ids)            # meta sha differs
        self.assertNotIn("sure_onboard/no-publish-row", ids)          # no publish row in decisions.jsonl
        self.assertNotIn("sure_onboard/rejected-one", ids)            # meta says rejected

    def test_a_sha_mismatch_is_counted_and_reported_not_silently_dropped(self) -> None:
        idx = self.build()
        self.assertNotIn("sure_onboard/sha-mismatch", entry_map(idx))
        self.assertEqual(idx["hash_mismatch"], ["sure_onboard/sha-mismatch"])
        text, _omitted = index.render_index_md(idx, CONFIG)
        report = index.index_report(idx, text, CONFIG)
        self.assertIn("1 provisional entries dropped: hash mismatch", report)
        self.assertIn("sure_onboard/sha-mismatch", report)

    def test_no_mismatch_leaves_the_report_line_unchanged(self) -> None:
        shutil.rmtree(self.root / "provisional" / "sure_onboard" / "sha-mismatch")
        idx = self.build()
        self.assertEqual(idx["hash_mismatch"], [])
        text, _omitted = index.render_index_md(idx, CONFIG)
        self.assertNotIn("hash mismatch", index.index_report(idx, text, CONFIG))

    def test_publish_row_needs_action_key(self) -> None:
        # §1.6: the key is `action`; a row written with the old `kind` key is not a publish row
        paths.append_jsonl(self.root / "decisions.jsonl",
                           {"kind": "publish", "entry_id": "sure_onboard/no-publish-row", "by": "auto", "at": "2026-08-13T00:00:00Z"}, 4096)
        self.assertNotIn("sure_onboard/no-publish-row", entry_map(self.build()))
        paths.append_decision(self.root, paths.decision_row("publish", "sure_onboard/no-publish-row", "auto", run_id="run-20260813-0003"))
        self.assertIn("sure_onboard/no-publish-row", entry_map(self.build()))

    def test_hook_trigger_from_meta_else_trigger(self) -> None:
        entries = entry_map(self.build())
        # meta.hook_trigger present (Task 6 wrote it): the index copies it, even when it is a strict subset
        self.assertEqual(entries["sure_eval/smoke-oom"]["trigger"], ["CUDA out of memory", "evidence-only: dmesg oom-killer"])
        self.assertEqual(entries["sure_eval/smoke-oom"]["hook_trigger"], ["CUDA out of memory"])
        self.assertEqual(entries["sure_onboard/pip-index-timeout"]["hook_trigger"], ["ReadTimeoutError: HTTPSConnectionPool"])
        # no meta / meta without the key / legacy / headerless / fact: hook_trigger == trigger
        for entry_id in ("sure_onboard/no-kernel-image", "sure_onboard/legacy_headerless", "sure_onboard/partition-not-found",
                         "sure_eval/job-log-missing", "_shared/qwen-audio-needs-cu121", "_shared/vc-partition-names"):
            self.assertEqual(entries[entry_id]["hook_trigger"], entries[entry_id]["trigger"], entry_id)
        self.assertEqual(entries["sure_onboard/no-kernel-image"]["hook_trigger"], ["no kernel image is available", "CUDA error: no kernel image"])
        self.assertEqual(entries["sure_onboard/legacy_headerless"]["hook_trigger"], [])

    def test_references_win_over_provisional_twin(self) -> None:
        entry = entry_map(self.build())["sure_onboard/partition-not-found"]
        self.assertEqual(entry["path"], "sure/skills/sure_onboard/references/memory/bad_cases/partition-not-found.md")
        self.assertEqual(entry["title"], "vc submit rejects the queue alias")
        self.assertEqual(sum(1 for e in self.build()["entries"] if e["entry_id"] == "sure_onboard/partition-not-found"), 1)

    def test_meta_overlays_counts_and_status(self) -> None:
        entries = entry_map(self.build())
        self.assertEqual(entries["sure_onboard/partition-not-found"]["useful_activated"], 3)
        self.assertEqual(entries["sure_onboard/partition-not-found"]["injections"], 5)
        self.assertEqual(entries["sure_eval/job-log-missing"]["status"], "disputed")
        self.assertEqual(entries["sure_eval/smoke-oom"]["status"], "disputed")
        self.assertEqual(entries["sure_eval/smoke-oom"]["similar_entry"], "sure_eval/job-log-missing")

    def test_proposal_overlays_op_and_target(self) -> None:
        entry = entry_map(self.build())["sure_onboard/pip-index-timeout-v2"]
        self.assertEqual(entry["op"], "modify")
        self.assertEqual(entry["target_entry"], "sure_onboard/pip-index-timeout")
        self.assertEqual(entry["path"], "sure/memory/provisional/sure_onboard/pip-index-timeout-v2/entry.md")
        self.assertEqual(entry["created"], {"run_id": "run-20260819-eeee", "date": "2026-08-19"})

    def test_unknown_component_becomes_underscore(self) -> None:
        # "Cell: sure_onboard/BUILD_ENV x infra": BUILD_ENV is not a unit id of sure_onboard
        self.assertEqual(entry_map(self.build())["sure_onboard/old-partition-name"]["component"], "_")

    def test_fact_stale_flag(self) -> None:
        entries = entry_map(self.build())
        self.assertTrue(entries["_shared/vc-partition-names"]["stale"])       # checked 2020, cluster limit 90 days
        self.assertFalse(entries["_shared/qwen-audio-needs-cu121"]["stale"])  # checked 2099
        self.assertEqual(entries["_shared/vc-partition-names"]["scope"], "cluster")
        self.assertEqual(entries["_shared/vc-partition-names"]["trigger"], [])

    def test_ordering_confirmed_then_provisional_newest_first_then_disputed_then_superseded(self) -> None:
        ids = [e["entry_id"] for e in self.build()["entries"]]
        self.assertEqual(ids, [
            "_shared/qwen-audio-needs-cu121", "_shared/vc-partition-names", "sure_onboard/legacy_headerless",
            "sure_onboard/no-kernel-image", "sure_onboard/partition-not-found",
            "sure_onboard/pip-index-timeout-v2", "sure_onboard/pip-index-timeout", "sure_onboard/pip-index-timeout-old",
            "sure_eval/job-log-missing", "sure_eval/smoke-oom",
            "sure_onboard/old-partition-name",
        ])

    def test_top_level_shape(self) -> None:
        idx = self.build()
        self.assertEqual(idx["schema"], "sure.memory.index.v1")
        self.assertEqual(set(idx), {"schema", "built_at", "sources_sha256", "entries", "omitted_provisional", "hash_mismatch"})
        self.assertEqual(len(idx["sources_sha256"]), 64)
        expected_keys = {"entry_id", "type", "status", "target_skill", "applies_to", "component", "cause", "trigger", "hook_trigger", "scope",
                         "title", "path", "legacy", "op", "target_entry", "similar_entry", "useful_activated",
                         "useful_unattributed", "injections", "disputed", "created", "checked_at", "stale", "superseded_by"}
        for entry in idx["entries"]:
            self.assertEqual(set(entry), expected_keys, entry["entry_id"])

    def test_empty_repo_builds_empty_index(self) -> None:
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        idx = index.build_index(empty, config=CONFIG, units=UNITS)
        self.assertEqual(idx["entries"], [])
        self.assertEqual(len(idx["sources_sha256"]), 64)


class ConcurrentPublishTests(IndexFixtureCase):
    """A publish lands while build_index is scanning: sources_sha256 must never end up describing a
    tree the scan did not see, or --check reports "up to date" and the entry is lost for ever."""

    LATE = "sure_onboard/late-entry"

    def publish_late_entry(self) -> None:
        with open(self.root / "decisions.jsonl", "ab") as handle:
            handle.write(b"\n")  # close make_repo's deliberately torn last line so the new row parses
        add_provisional(self.root, "sure_onboard", "late-entry", "Late entry", "pip index read timed out again",
                        "sure_onboard/build_env x infra", "run-20260819-late", "2026-08-19", meta_ok=True, publish=True)

    def build_with_publish_at(self, nth: int) -> dict:
        """Run a real build with a real publish landing just before the nth sources_sha256 call."""
        calls: list[int] = []
        real = index.sources_sha256

        def wrapper(repo_root):
            calls.append(1)
            if len(calls) == nth:
                self.publish_late_entry()
            return real(repo_root)

        with mock.patch.object(index, "sources_sha256", wrapper):
            idx = index.build_index(self.repo, config=CONFIG, units=UNITS)
        index.write_index(self.repo, idx, CONFIG)
        return idx

    def test_an_entry_published_during_the_scan_is_not_lost(self) -> None:
        idx = self.build_with_publish_at(1)
        if self.LATE not in entry_map(idx):  # not seen by this build: the next check must rebuild
            self.assertTrue(index.check_index(self.repo, config=CONFIG, units=UNITS))
        self.assertIn(self.LATE, entry_map(index.read_index(self.repo) or {"entries": []}))

    def test_a_moved_tree_is_rescanned_instead_of_recorded_stale(self) -> None:
        idx = self.build_with_publish_at(2)
        self.assertIn(self.LATE, entry_map(idx))
        self.assertEqual(idx["sources_sha256"], index.sources_sha256(self.repo))
        self.assertFalse(index.check_index(self.repo, config=CONFIG, units=UNITS))

    def test_a_quiet_tree_records_its_own_hash(self) -> None:
        idx = self.build()
        self.assertEqual(idx["sources_sha256"], index.sources_sha256(self.repo))


class SourcesShaAndCheckTests(IndexFixtureCase):
    def test_sources_sha_ignores_mtime_but_sees_content(self) -> None:
        before = index.sources_sha256(self.repo)
        target = self.repo / "sure/skills/sure_onboard/references/memory/bad_cases/no-kernel-image.md"
        stamp = target.stat().st_mtime + 3600
        os.utime(target, (stamp, stamp))
        self.assertEqual(index.sources_sha256(self.repo), before)
        target.write_bytes(target.read_bytes() + b"\nOne more line.\n")
        self.assertNotEqual(index.sources_sha256(self.repo), before)

    def test_sources_sha_sees_meta_and_decisions(self) -> None:
        before = index.sources_sha256(self.repo)
        write_meta(self.root, "sure_onboard/partition-not-found", status="confirmed", useful_activated=4, entry_sha256="x")
        middle = index.sources_sha256(self.repo)
        self.assertNotEqual(middle, before)
        paths.append_jsonl(self.root / "decisions.jsonl", {"action": "confirm", "entry_id": "sure_onboard/pip-index-timeout"}, 4096)
        self.assertNotEqual(index.sources_sha256(self.repo), middle)

    def test_sources_sha_sees_proposal_json_alone(self) -> None:
        # entry.md, meta.json and decisions.jsonl are untouched: only proposal.json changes.
        before = index.sources_sha256(self.repo)
        proposal_path = self.root / "provisional" / "sure_onboard" / "pip-index-timeout" / "proposal.json"
        proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
        proposal["causal"] = not proposal["causal"]
        paths.atomic_write_json(proposal_path, proposal)
        self.assertNotEqual(index.sources_sha256(self.repo), before)

    def test_check_builds_once_then_only_on_content_change(self) -> None:
        self.assertTrue(index.check_index(self.repo, config=CONFIG, units=UNITS))
        self.assertTrue((self.root / "index.json").is_file())
        self.assertTrue((self.root / "index.md").is_file())
        self.assertFalse(index.check_index(self.repo, config=CONFIG, units=UNITS))
        target = self.repo / "sure/skills/sure_eval/references/memory/bad_cases/job-log-missing.md"
        stamp = target.stat().st_mtime + 3600
        os.utime(target, (stamp, stamp))
        self.assertFalse(index.check_index(self.repo, config=CONFIG, units=UNITS))
        target.write_bytes(target.read_bytes() + b"\nChanged.\n")
        self.assertTrue(index.check_index(self.repo, config=CONFIG, units=UNITS))
        self.assertFalse(index.check_index(self.repo, config=CONFIG, units=UNITS))

    def test_check_rebuilds_when_index_json_is_broken_or_wrong_schema(self) -> None:
        index.check_index(self.repo, config=CONFIG, units=UNITS)
        (self.root / "index.json").write_text("{not json", encoding="utf-8")
        self.assertTrue(index.check_index(self.repo, config=CONFIG, units=UNITS))
        broken = json.loads((self.root / "index.json").read_text(encoding="utf-8"))
        broken["schema"] = "sure.memory.index.v0"
        (self.root / "index.json").write_text(json.dumps(broken), encoding="utf-8")
        self.assertTrue(index.check_index(self.repo, config=CONFIG, units=UNITS))
        (self.root / "index.md").unlink()
        self.assertTrue(index.check_index(self.repo, config=CONFIG, units=UNITS))
        self.assertIsNotNone(index.read_index(self.repo))

    def test_write_index_leaves_no_temp_files_and_records_omitted(self) -> None:
        idx = self.build()
        index.write_index(self.repo, idx, CONFIG)
        leftovers = [p.name for p in self.root.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])
        on_disk = json.loads((self.root / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["omitted_provisional"], 0)
        self.assertEqual(on_disk["entries"], idx["entries"])


class RenderIndexMdTests(IndexFixtureCase):
    def test_bullet_format_tags_and_order(self) -> None:
        text, omitted = index.render_index_md(self.build(), CONFIG)
        self.assertEqual(omitted, 0)
        lines = [line for line in text.splitlines() if line.startswith("- ")]
        self.assertEqual(lines[0].split(index.MD_SEP)[0], "- [confirmed] _shared/qwen-audio-needs-cu121")
        self.assertIn("- [confirmed] [stale] _shared/vc-partition-names — The 3090 partition is named site-gpu — triggers: (none; prompt-level only) — sure/skills/_shared/memory/facts/vc-partition-names.md", lines)
        self.assertIn("- [confirmed] [legacy] [no hook trigger] sure_onboard/legacy_headerless — Legacy Headerless Case — triggers: (none; prompt-level only) — sure/skills/sure_onboard/references/memory/bad_cases/legacy_headerless.md", lines)
        self.assertIn("- [confirmed] [legacy] sure_onboard/no-kernel-image — CUDA arch mismatch: no kernel image — triggers: no kernel image is available; CUDA error: no kernel image — sure/skills/sure_onboard/references/memory/bad_cases/no-kernel-image.md", lines)
        self.assertIn("- [provisional] sure_onboard/pip-index-timeout-v2 — Read timed out on the pip index (mirror fix) — triggers: ReadTimeoutError: HTTPSConnectionPool — sure/memory/provisional/sure_onboard/pip-index-timeout-v2/entry.md", lines)
        self.assertTrue(lines[-1].startswith("- [disputed] sure_eval/smoke-oom"))
        self.assertNotIn("old-partition-name", text)  # superseded never appears
        statuses = [line.split("]")[0][3:] for line in lines]
        self.assertEqual(statuses, ["confirmed"] * 5 + ["provisional"] * 3 + ["disputed"] * 2)

    def test_line_budget_drops_oldest_provisional_and_writes_notice(self) -> None:
        idx = self.build()
        # full text: 4 head lines + 5 confirmed + 3 provisional + 2 disputed = 14 lines; the notice counts too
        config = dict(CONFIG, index_md_max_lines=13)
        text, omitted = index.render_index_md(idx, config)
        self.assertEqual(omitted, 2)
        self.assertIn("pip-index-timeout-v2", text)                    # newest provisional kept
        self.assertNotIn("sure_onboard/pip-index-timeout —", text)      # older ones dropped
        self.assertNotIn("pip-index-timeout-old", text)
        self.assertTrue(text.rstrip("\n").endswith("`python3 -s sure/runtime/memory/cli.py list --status provisional` to see them)"))
        self.assertIn("- (omitted 2 older provisional entries;", text)
        self.assertEqual(text.count("\n"), 13)
        for must_keep in ("legacy_headerless", "smoke-oom", "job-log-missing"):
            self.assertIn(must_keep, text)

    def test_byte_budget_uses_utf8_bytes(self) -> None:
        idx = self.build()
        full, _ = index.render_index_md(idx, CONFIG)
        config = dict(CONFIG, index_md_max_bytes=len(full.encode("utf-8")) - 1)
        text, omitted = index.render_index_md(idx, config)
        self.assertEqual(omitted, 1)
        self.assertLessEqual(len(text.encode("utf-8")), config["index_md_max_bytes"])

    def test_budget_never_drops_confirmed_or_disputed(self) -> None:
        config = dict(CONFIG, index_md_max_lines=1)
        text, omitted = index.render_index_md(self.build(), config)
        self.assertEqual(omitted, 3)
        for kept in ("legacy_headerless", "no-kernel-image", "smoke-oom"):
            self.assertIn(kept, text)


class NoHookTriggerTagTests(IndexFixtureCase):
    """§6.4 index.md tags: a bad_case with no hook trigger, or with component '_', is listed with
    everything a routable entry has but can never be selected by matchBadCases. The tag is the only
    place a human sees it."""

    def md_line(self, entry_id: str) -> str:
        text, _omitted = index.render_index_md(self.build(), CONFIG)
        return next(line for line in text.splitlines() if f" {entry_id}{index.MD_SEP}" in line)

    def test_a_headerless_legacy_bad_case_is_tagged(self) -> None:
        line = self.md_line("sure_onboard/legacy_headerless")
        self.assertIn("[no hook trigger]", line)
        self.assertIn("[legacy]", line)

    def test_a_routable_bad_case_is_not_tagged(self) -> None:
        self.assertNotIn("[no hook trigger]", self.md_line("sure_onboard/partition-not-found"))

    def test_a_fact_without_triggers_is_not_tagged(self) -> None:
        self.assertNotIn("[no hook trigger]", self.md_line("_shared/vc-partition-names"))

    def test_a_bad_case_whose_cell_names_no_unit_is_tagged(self) -> None:
        # `Cell: sure_onboard/BUILD_ENV x infra` is not a unit id, so _finish forces component '_'
        write(self.repo / "sure/skills/sure_onboard/references/memory/bad_cases/dead-cell.md",
              header("partition not found: 3090-data", "sure_onboard/BUILD_ENV x infra",
                     "run-20260701-zzzz → qwen-audio", "2026-07-01", "confirmed")
              + bad_case_body("Dead cell case", "partition not found: 3090-data"))
        line = self.md_line("sure_onboard/dead-cell")
        self.assertIn("[no hook trigger]", line)
        self.assertIn("triggers: partition not found: 3090-data", line)


    def test_never_injected_holds_for_a_confirmed_entry_on_a_real_unit_with_no_trigger(self) -> None:
        # proposals.rule_7_dedup reads this predicate for cell occupancy, and the cells it frees
        # have this exact shape: confirmed, filed on a real unit, no trigger for matchBadCases.
        entry = {"entry_id": "sure_onboard/no-trigger", "type": "bad_case", "status": "confirmed",
                 "component": "build_env", "cause": "metric_bypass", "trigger": [], "hook_trigger": []}
        self.assertTrue(index.never_injected(entry))
        self.assertFalse(index.never_injected(dict(entry, hook_trigger=["no kernel image is available"])))


class ReadmeReconcileTests(IndexFixtureCase):
    def readme(self) -> Path:
        return self.repo / "sure/skills/sure_onboard/references/memory/bad_cases/README.md"

    def test_adds_missing_rows_drops_ghost_rows_keeps_existing_verbatim(self) -> None:
        records = index.records_from_index(self.build())
        self.assertTrue(index.reconcile_readme(self.readme(), records))
        text = self.readme().read_text(encoding="utf-8")
        self.assertIn("| Legacy Headerless Case | `legacy_headerless.md` | keep me verbatim |", text)
        self.assertNotIn("`ghost.md`", text)
        self.assertIn("| partition not found | `partition-not-found.md` | vc submit rejects the queue alias |", text)
        self.assertIn("| no kernel image is available; CUDA error: no kernel image | `no-kernel-image.md` | CUDA arch mismatch: no kernel image |", text)
        self.assertNotIn("`old-partition-name.md`", text)  # superseded: no new row
        rows = [line for line in text.splitlines() if line.startswith("| ") and "`" in line]
        self.assertEqual(len(rows), 3)

    def test_second_run_is_a_no_op(self) -> None:
        records = index.records_from_index(self.build())
        index.reconcile_readme(self.readme(), records)
        before = self.readme().read_bytes()
        stamp = self.readme().stat().st_mtime
        self.assertFalse(index.reconcile_readme(self.readme(), records))
        self.assertEqual(self.readme().read_bytes(), before)
        self.assertEqual(self.readme().stat().st_mtime, stamp)

    def test_crlf_readme_keeps_crlf(self) -> None:
        self.readme().write_bytes(self.readme().read_bytes().replace(b"\n", b"\r\n"))
        records = index.records_from_index(self.build())
        self.assertTrue(index.reconcile_readme(self.readme(), records))
        raw = self.readme().read_bytes()
        self.assertNotIn(b"\r\r\n", raw)
        self.assertEqual(raw.count(b"\r\n"), raw.count(b"\n"))
        self.assertIn(b"| partition not found | `partition-not-found.md` |", raw)
        self.assertFalse(index.reconcile_readme(self.readme(), records))

    def test_missing_readme_is_bootstrapped(self) -> None:
        readme = self.repo / "sure/skills/sure_eval/references/memory/bad_cases/README.md"
        self.assertFalse(readme.exists())
        records = index.records_from_index(self.build())
        self.assertTrue(index.reconcile_readme(readme, records))
        text = readme.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Bad Case Memory Index"))
        # job-log-missing is disputed in meta, so it gets no row; the table exists and is empty
        self.assertIn(index.ROUTE_TABLE_HEADER[1], text)
        self.assertNotIn("`job-log-missing.md`", text)

    def test_two_skills_with_the_same_file_name_keep_their_own_rows(self) -> None:
        # both skills may hold a bad case called partition-not-found.md; each README gets its own row
        twin = self.repo / "sure/skills/sure_eval/references/memory/bad_cases/partition-not-found.md"
        write(twin, header("partition not found on the eval queue", "sure_eval/execute_wait x infra",
                           "run-20260816-aaaa → qwen-audio", "2026-08-16", "confirmed")
              + bad_case_body("vc eval submit rejects the queue alias", "partition not found on the eval queue"))
        records = index.records_from_index(self.build())
        eval_readme = twin.parent / "README.md"
        self.assertTrue(index.reconcile_readme(eval_readme, records))
        self.assertIn("| partition not found on the eval queue | `partition-not-found.md` | vc eval submit rejects the queue alias |",
                      eval_readme.read_text(encoding="utf-8"))
        self.assertTrue(index.reconcile_readme(self.readme(), records))
        self.assertIn("| partition not found | `partition-not-found.md` | vc submit rejects the queue alias |",
                      self.readme().read_text(encoding="utf-8"))
        self.assertNotIn("on the eval queue", self.readme().read_text(encoding="utf-8"))

    def test_real_onboard_readme_is_unchanged(self) -> None:
        # The 17 legacy rows in the real README stay byte for byte, whether the files have headers yet or not.
        real_dir = REPO_ROOT / "sure" / "skills" / "sure_onboard" / "references" / "memory" / "bad_cases"
        repo = Path(self.tmp.name) / "real"
        target = repo / "sure" / "skills" / "sure_onboard" / "references" / "memory" / "bad_cases"
        shutil.copytree(real_dir, target)
        idx = index.build_index(repo, config=CONFIG, units=UNITS)
        onboard = [e for e in idx["entries"] if e["target_skill"] == "sure_onboard"]
        self.assertEqual(len(onboard), 17)
        self.assertTrue(all(e["legacy"] for e in onboard))
        self.assertTrue(all(e["status"] == "confirmed" for e in onboard))
        before = (target / "README.md").read_bytes()
        self.assertFalse(index.reconcile_readme(target / "README.md", index.records_from_index(idx)))
        self.assertEqual((target / "README.md").read_bytes(), before)


class MainTests(IndexFixtureCase):
    def run_main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = index.main([*argv])
        return code, out.getvalue(), err.getvalue()

    def test_check_prints_rebuilt_then_up_to_date(self) -> None:
        # A tree with nothing dropped: exit 0 both times. The fixture ships one diverged entry, so
        # it goes first; test_check_exits_non_zero_on_a_hash_mismatch covers the tree with it.
        shutil.rmtree(self.root / "provisional" / "sure_onboard" / "sha-mismatch")
        code, out, _ = self.run_main("--repo-root", str(self.repo), "--check")
        self.assertEqual((code, out.strip()), (0, "index: rebuilt"))
        code, out, _ = self.run_main("--repo-root", str(self.repo), "--check")
        self.assertEqual((code, out.strip()), (0, "index: up to date"))
        # --check never touches the git-tracked README
        self.assertIn("`ghost.md`", self.repo.joinpath("sure/skills/sure_onboard/references/memory/bad_cases/README.md").read_text(encoding="utf-8"))

    def test_check_keeps_reporting_a_hash_mismatch_on_stderr(self) -> None:
        code, _out, err = self.run_main("--repo-root", str(self.repo), "--check")
        self.assertEqual(code, index.EXIT_HASH_MISMATCH)
        self.assertIn("hash mismatch", err)
        self.assertIn("sure_onboard/sha-mismatch", err)
        code, out, err = self.run_main("--repo-root", str(self.repo), "--check")
        self.assertEqual(out.strip(), "index: up to date")  # nothing rebuilt, still visible
        self.assertEqual(code, index.EXIT_HASH_MISMATCH)
        self.assertIn("hash mismatch", err)

    def test_check_exits_non_zero_on_a_hash_mismatch(self) -> None:
        """A dropped entry has to reach the exit status, not just stderr.

        preStartMemory is the only runtime reader of this command and it judges the run by the exit
        status alone (`runMemoryScript` returns `ok: r.status === 0`, and preStartMemory raises its
        `memory index check failed` warning from `!check.ok`). Exiting 0 with the line on stderr
        means that diagnostic can never fire and a broken index is found only when a human happens
        to run `cli rebuild-index`. Exit 1 stays reserved for "the check itself could not run"."""
        self.assertNotEqual(index.EXIT_HASH_MISMATCH, 0)
        self.assertNotEqual(index.EXIT_HASH_MISMATCH, 1)  # 1 is the "check crashed" code below
        code, out, err = self.run_main("--repo-root", str(self.repo), "--check")
        self.assertEqual(code, index.EXIT_HASH_MISMATCH)
        self.assertEqual(out.strip(), "index: rebuilt")  # the rebuild happened; the entry is still out
        self.assertIn("sure_onboard/sha-mismatch", err)
        # ...and the same tree with the diverged entry gone exits 0, so the code tracks the mismatch
        shutil.rmtree(self.root / "provisional" / "sure_onboard" / "sha-mismatch")
        code, out, err = self.run_main("--repo-root", str(self.repo), "--check")
        self.assertEqual((code, out.strip(), err), (0, "index: rebuilt", ""))

    def test_rebuild_exits_non_zero_on_a_hash_mismatch(self) -> None:
        """--rebuild drops the same entries as --check, so it owes the same exit status.

        The report line goes to stdout and nobody reads stdout in a `&&` chain or a cron step;
        exiting 0 there means an unattended rebuild swallows a dropped entry that --check would
        have flagged. Exit 1 stays reserved for "the rebuild itself failed"."""
        code, out, _ = self.run_main("--repo-root", str(self.repo), "--rebuild")
        self.assertEqual(code, index.EXIT_HASH_MISMATCH)
        self.assertIn("provisional entries dropped: hash mismatch", out)
        self.assertIn("sure_onboard/sha-mismatch", out)
        shutil.rmtree(self.root / "provisional" / "sure_onboard" / "sha-mismatch")
        code, out, _ = self.run_main("--repo-root", str(self.repo), "--rebuild")
        self.assertEqual(code, 0)
        self.assertNotIn("hash mismatch", out)

    def test_rebuild_reports_and_reconciles(self) -> None:
        # EXIT_HASH_MISMATCH, not 0: the fixture ships the diverged sha-mismatch entry. The report
        # and the README reconciliation are what this test is about, and both still happen.
        code, out, _ = self.run_main("--repo-root", str(self.repo), "--rebuild")
        self.assertEqual(code, index.EXIT_HASH_MISMATCH)
        self.assertIn("index: 11 entries (5 confirmed, 3 provisional, 2 disputed, 1 superseded)", out)
        self.assertIn(f"(limits {CONFIG['index_md_max_lines']} / {CONFIG['index_md_max_bytes']})", out)
        self.assertIn("readme updated: sure/skills/sure_onboard/references/memory/bad_cases/README.md", out)
        self.assertNotIn("`ghost.md`", self.repo.joinpath("sure/skills/sure_onboard/references/memory/bad_cases/README.md").read_text(encoding="utf-8"))
        code, out, _ = self.run_main("--repo-root", str(self.repo), "--rebuild")
        self.assertEqual(code, index.EXIT_HASH_MISMATCH)
        self.assertNotIn("readme updated", out)

    def test_rebuild_leaves_the_shared_facts_index_alone(self) -> None:
        # Task 15 writes sure/skills/_shared/memory/facts/README.md by hand; no machine reconciliation
        facts_readme = self.repo / "sure/skills/_shared/memory/facts/README.md"
        code, out, _ = self.run_main("--repo-root", str(self.repo), "--rebuild")
        self.assertEqual(code, index.EXIT_HASH_MISMATCH)  # the fixture's diverged entry, not a failure here
        self.assertFalse(facts_readme.exists())
        self.assertNotIn("facts/README.md", out)

    def test_requires_a_mode(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            index.main(["--repo-root", str(self.repo)])


class GoldenIndexTests(IndexFixtureCase):
    """fixtures/golden_index.json is the index built from make_repo(); vitest (Task 9) parses it.
    Regenerate on purpose only: SURE_MEMORY_WRITE_GOLDEN=1 python -m unittest sure/runtime/memory/test_index.py -k golden"""

    FIXED_BUILT_AT = "2026-08-18T00:00:00Z"

    def fresh(self) -> dict:
        idx = self.build()
        idx["built_at"] = self.FIXED_BUILT_AT
        return idx

    def load_golden(self) -> dict:
        # Both tests go through here, so the very first run with the env var set creates the fixture
        # whichever test unittest happens to run first (it sorts them by name).
        if os.environ.get("SURE_MEMORY_WRITE_GOLDEN") == "1":
            paths.atomic_write_json(GOLDEN, self.fresh())
        self.assertTrue(GOLDEN.is_file(), "run once with SURE_MEMORY_WRITE_GOLDEN=1 to create the fixture")
        return json.loads(GOLDEN.read_text(encoding="utf-8"))

    def test_matches_committed_golden(self) -> None:
        self.assertEqual(self.fresh(), self.load_golden())

    def test_golden_covers_the_shapes_vitest_needs(self) -> None:
        golden = self.load_golden()
        by_id = {e["entry_id"]: e for e in golden["entries"]}
        self.assertEqual(golden["schema"], "sure.memory.index.v1")
        self.assertEqual(by_id["sure_onboard/legacy_headerless"]["trigger"], [])           # not matchable
        self.assertEqual(by_id["sure_onboard/pip-index-timeout-v2"]["op"], "modify")        # pending revision
        self.assertEqual(by_id["sure_eval/smoke-oom"]["similar_entry"], "sure_eval/job-log-missing")
        self.assertEqual(by_id["sure_onboard/old-partition-name"]["status"], "superseded")
        self.assertEqual(by_id["_shared/vc-partition-names"]["scope"], "cluster")
        self.assertEqual(by_id["_shared/qwen-audio-needs-cu121"]["scope"], "model_family:qwen-audio")
        self.assertEqual({e["status"] for e in golden["entries"]}, {"confirmed", "provisional", "disputed", "superseded"})
        # hook_trigger: the only entry where it differs from trigger is smoke-oom (evidence-only trigger left out);
        # every bad_case with a trigger keeps trigger[0] in hook_trigger, so Task 9's "each entry matches itself" holds
        self.assertEqual(by_id["sure_eval/smoke-oom"]["hook_trigger"], ["CUDA out of memory"])
        self.assertNotEqual(by_id["sure_eval/smoke-oom"]["hook_trigger"], by_id["sure_eval/smoke-oom"]["trigger"])
        for entry in golden["entries"]:
            self.assertIn("hook_trigger", entry, entry["entry_id"])
            if entry["entry_id"] != "sure_eval/smoke-oom":
                self.assertEqual(entry["hook_trigger"], entry["trigger"], entry["entry_id"])
            if entry["type"] == "bad_case" and entry["trigger"]:
                self.assertIn(entry["trigger"][0], entry["hook_trigger"], entry["entry_id"])


if __name__ == "__main__":
    unittest.main()
