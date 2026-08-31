# sure/runtime/memory/test_cli.py
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # sure/runtime

from memory import cli, index, paths, promote, usage  # noqa: E402

RUN1 = "20260810-120000-aaaa0001"
RUN2 = "20260812-120000-aaaa0002"
LEGACY_ID = "sure_onboard/vc_partition_name_mismatch"
KERNEL_ID = "sure_onboard/no-kernel-image"
MODIFY_ID = "sure_onboard/kernel-image-arch-list"
SUPERSEDE_ID = "sure_onboard/kernel-image-torch-index"
LEGACY_MODIFY_ID = "sure_onboard/partition-alias-fix"
FACT_ID = "_shared/vc-partition-names"
COLD_ID = "sure_eval/cold-entry"

LEGACY_README = """# Bad Case Memory Index

Bad cases are optional memory. Read this index only after a concrete failure or
known-risk trigger appears. Then read only the matching bad-case file.

## Route Table

| Trigger or symptom | Suggested memory file | Notes |
|--------------------|-----------------------|-------|
| `vc submit` fails with `partition not found` after using a shorthand queue name | `vc_partition_name_mismatch.md` | Use when a human-facing queue alias such as `3090-data` differs from the exact `vc submit -p` partition name. |
"""

LEGACY_ENTRY = """# Bad Case: VC Partition Name Mismatch

## Trigger

- `vc submit` fails with `partition not found: <name>`.

## Affected Step

- `BUILD_ENV` / remote GPU validation submission through `vc submit`.

## Minimum Evidence

1. Capture the failed `vc submit` command and exact error string.

## Known Mitigation

Use the exact partition name from `vc info -u`.

## Verification

Re-run `vc submit -p <exact-name>` and confirm the job is queued.
"""

BAD_CASE_BODY = """## Trigger

`{trigger}` in the build or import log.

## Affected Step

`sure_onboard` unit `{unit}`.

## Minimum Evidence

`artifacts/build_env.log:12`

## Known Mitigation

{mitigation}

## Verification

`python -c "import torch; print(torch.cuda.get_arch_list())"`
"""


def bad_case_text(*, title: str, trigger: str, unit: str, cause: str, run_id: str, added: str,
                  status: str = "provisional", mitigation: str = "Reinstall torch for the GPU arch.", header: bool = True) -> str:
    """entry.md as publish writes it: five provenance lines, blank line, then the body starting at the H1."""
    head = "" if not header else (
        f"Trigger: {trigger}\nCell: sure_onboard/{unit} x {cause}\nSource: {run_id} -> demo-asr\nAdded: {added}\nStatus: {status}\n\n"
    )
    return head + f"# {title}\n\n" + BAD_CASE_BODY.format(trigger=trigger, unit=unit, mitigation=mitigation)


def fact_text(*, title: str, scope: str, checked_at: str, run_id: str, added: str, status: str = "provisional") -> str:
    return (
        f"Trigger: \nCell: _shared/_ x n.a.\nSource: {run_id} -> demo-asr\nAdded: {added}\nStatus: {status}\n\n"
        f"# {title}\n\nScope: {scope}\nChecked-at: {checked_at}\nEvidence: artifacts/memory_evidence/1.txt\n\nUse the exact partition names.\n"
    )


def proposal(*, type_: str, op: str, target_skill: str, component: str, cause: str, trigger: list[str], run_id: str,
             target_entry: str | None = None, similar: dict | None = None, scope: str | None = None, checked_at: str | None = None) -> dict:
    return {
        "schema": "sure.memory.proposal.v2", "type": type_, "op": op, "target_skill": target_skill,
        "target_entry": target_entry, "applies_to": [target_skill] if type_ == "bad_case" else ["sure_onboard", "sure_eval"],
        "cell": {"component": component, "cause": cause}, "trigger": trigger, "causal": True,
        "evidence": ["artifacts/build_env.log:12"],
        "claims": [{"kind": "unit_result", "unit": component, "attempt": 2, "status": "passed"}],
        "source": {"run_id": run_id, "skill": "sure_onboard", "target": "demo-asr", "digest_sha256": "0" * 64},
        "similar": similar, "scope": scope, "checked_at": checked_at,
    }


def meta(entry_id: str, *, type_: str, target_skill: str, component: str, cause: str, trigger: list[str], run_id: str,
         created: str, entry_sha256: str, op: str = "add", target_entry: str | None = None, similar_entry: str | None = None,
         scope: str | None = None, checked_at: str | None = None, evidence_sha256: dict | None = None,
         derived_from: list[str] | None = None, status: str = "provisional") -> dict:
    """Section 6.3 meta plus the skeleton 1.7 extras Task 6 writes: schema, op, target_entry, similar_entry,
    orphan, entry_sha256 (the entry.md hash the index uses for provisional inclusion) and hook_trigger
    (publish computes it; cli must carry it through unchanged)."""
    return {
        "schema": "sure.memory.meta.v1",
        "entry_id": entry_id, "type": type_, "status": status, "target_skill": target_skill,
        "applies_to": [target_skill] if type_ == "bad_case" else ["sure_onboard", "sure_eval"],
        "component": component, "cause": cause, "trigger": trigger, "hook_trigger": list(trigger), "scope": scope,
        "injections": 0, "useful_activated": 0, "useful_unattributed": 0, "useful_runs": [], "disputed": 0, "last_hit": None,
        "created": {"run_id": run_id, "date": created}, "confirmed": None, "exported": None,
        "derived_from": derived_from or [], "fix_exercised": True, "evidence_sha256": evidence_sha256 or {},
        "superseded_by": None, "superseded_at": None, "checked_at": checked_at,
        "op": op, "target_entry": target_entry, "similar_entry": similar_entry, "orphan": False, "entry_sha256": entry_sha256,
    }


def digest(run_id: str, skill: str, units: list[tuple[str, str, int]]) -> dict:
    return {
        "schema": "sure.memory.run_digest.v1",
        "run": {"run_id": run_id, "skill": skill, "args": "model=demo-asr", "target": {"kind": "model", "id": "demo-asr"},
                "status_so_far": "running", "cutoff": 100, "memory_usage": []},
        "units": [{"id": uid, "outcome": outcome, "attempts": attempts, "repairs": [], "fix_window": [], "last_commands": []}
                  for uid, outcome, attempts in units],
        "tool_errors": 0, "prior_runs": [], "memory_index_snapshot": [], "units_registry": {},
    }


class MemoryTree:
    """A fake instance checkout: references with one legacy entry, a memory tree with six published entries,
    two runs of usage rows and digests, and one run dir with evidence files."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.memory = root / "sure" / "memory"
        self.today = paths.utc_today()

    def build(self) -> None:
        self._write_references(self.root)
        paths.ensure_memory_tree(self.memory)
        self._add_bad_case(KERNEL_ID, title="CUDA arch mismatch: no kernel image is available", trigger="no kernel image is available",
                           unit="build_env", cause="cuda_version_mismatch", run_id=RUN1, created="2026-08-10",
                           evidence_sha256={"artifacts/build_env.log": paths.sha256_text("ok log\n"),
                                            "artifacts/changed.log": "f" * 64, "artifacts/gone.log": "e" * 64})
        self._add_bad_case(MODIFY_ID, title="CUDA arch mismatch: check the arch list first", trigger="no kernel image is available",
                           unit="build_env", cause="cuda_version_mismatch", run_id=RUN2, created=self.today,
                           op="modify", target_entry=KERNEL_ID, mitigation="Print torch.cuda.get_arch_list() before reinstalling.",
                           derived_from=[KERNEL_ID])
        self._add_bad_case(SUPERSEDE_ID, title="CUDA arch mismatch: pin the torch index url", trigger="no kernel image is available",
                           unit="build_env", cause="cuda_version_mismatch", run_id=RUN2, created=self.today,
                           op="supersede", target_entry=KERNEL_ID, mitigation="Install torch from the cu128 index url.")
        self._add_bad_case(LEGACY_MODIFY_ID, title="VC partition alias must be resolved with vc info", trigger="partition not found",
                           unit="build_env", cause="infra", run_id=RUN2, created=self.today,
                           op="modify", target_entry=LEGACY_ID, mitigation="Resolve the alias with vc info -u first.")
        self._add_bad_case(COLD_ID, title="Smoke test needs the vc partition flag", trigger="unknown partition flag",
                           unit="smoke_test", cause="infra", run_id=RUN1, created="2020-01-01", target_skill="sure_eval")
        self._add_fact()
        self._write_usage()
        self._write_digests()
        self._write_run_dir()
        cli._rebuild_index(cli._load_ctx(str(self.root)))

    @staticmethod
    def _write_references(root: Path) -> None:
        bad_cases = root / "sure" / "skills" / "sure_onboard" / "references" / "memory" / "bad_cases"
        paths.atomic_write_text(bad_cases / "README.md", LEGACY_README)
        paths.atomic_write_text(bad_cases / "vc_partition_name_mismatch.md", LEGACY_ENTRY)
        paths.atomic_write_text(root / "sure" / "skills" / "sure_onboard" / "references" / "task_playbooks" / "VC.md",
                                "# VC\n\nRead `references/memory/bad_cases/vc_partition_name_mismatch.md` when vc submit fails.\n")
        paths.atomic_write_text(root / "sure" / "skills" / "sure_eval" / "references" / "memory" / "bad_cases" / "README.md",
                                "# Bad Case Memory Index\n\n## Route Table\n\n| Trigger or symptom | Suggested memory file | Notes |\n|---|---|---|\n")
        # every real clone has the shared facts tree; export refuses a --repo-root without it
        (root / "sure" / "skills" / "_shared" / "memory" / "facts").mkdir(parents=True, exist_ok=True)

    def _publish(self, entry_id: str, text: str, prop: dict, meta_row: dict) -> None:
        skill, slug = entry_id.split("/", 1)
        entry_dir = self.memory / "provisional" / skill / slug
        paths.atomic_write_text(entry_dir / "entry.md", text)
        paths.atomic_write_json(entry_dir / "proposal.json", prop)
        meta_row["entry_sha256"] = paths.sha256_file(entry_dir / "entry.md")
        paths.atomic_write_json(self.memory / "meta" / skill / f"{slug}.json", meta_row)
        # The publish row exactly as Task 6 writes it (skeleton 1.6): key `action`, via the paths helpers, under the lock.
        with paths.memory_lock(self.memory):
            paths.append_decision(self.memory, paths.decision_row(
                "publish", entry_id, "auto", run_id=meta_row["created"]["run_id"], skill="sure_onboard", target_skill=skill,
                type=meta_row["type"], op=meta_row["op"], target_entry=meta_row["target_entry"], candidate=f"01-{slug}"))

    def _add_bad_case(self, entry_id: str, *, title: str, trigger: str, unit: str, cause: str, run_id: str, created: str,
                      op: str = "add", target_entry: str | None = None, mitigation: str = "Reinstall torch for the GPU arch.",
                      evidence_sha256: dict | None = None, derived_from: list[str] | None = None, target_skill: str = "sure_onboard") -> None:
        text = bad_case_text(title=title, trigger=trigger, unit=unit, cause=cause, run_id=run_id, added=created, mitigation=mitigation)
        prop = proposal(type_="bad_case", op=op, target_skill=target_skill, component=unit, cause=cause, trigger=[trigger], run_id=run_id, target_entry=target_entry)
        row = meta(entry_id, type_="bad_case", target_skill=target_skill, component=unit, cause=cause, trigger=[trigger], run_id=run_id,
                   created=created, entry_sha256="", op=op, target_entry=target_entry, evidence_sha256=evidence_sha256, derived_from=derived_from)
        self._publish(entry_id, text, prop, row)

    def _add_fact(self) -> None:
        text = fact_text(title="VC partitions are named 3090-data and site-gpu", scope="cluster", checked_at="2020-01-01", run_id=RUN1, added="2026-08-10")
        prop = proposal(type_="fact", op="add", target_skill="_shared", component="_", cause="n.a.", trigger=[], run_id=RUN1, scope="cluster", checked_at="2020-01-01")
        row = meta(FACT_ID, type_="fact", target_skill="_shared", component="_", cause="n.a.", trigger=[], run_id=RUN1, created="2026-08-10",
                   entry_sha256="", scope="cluster", checked_at="2020-01-01")
        self._publish(FACT_ID, text, prop, row)

    def _write_usage(self) -> None:
        rows1 = [
            {"kind": "inject", "run_id": RUN1, "skill": "sure_onboard", "unit": "build_env", "attempt": 1, "events_cutoff": 40,
             "entries": [{"entry_id": KERNEL_ID, "shared": False}], "at": "2026-08-10T12:10:00Z"},
            {"kind": "settle", "run_id": RUN1, "skill": "sure_onboard", "unit": "build_env", "entry_id": KERNEL_ID,
             "outcome": "useful_activated", "at": "2026-08-10T12:20:00Z"},
        ]
        rows2 = [
            {"kind": "pre_start", "run_id": RUN2, "skill": "sure_onboard", "entries": [{"entry_id": FACT_ID, "shared": True}], "at": "2026-08-12T12:00:00Z"},
            {"kind": "inject", "run_id": RUN2, "skill": "sure_onboard", "unit": "build_env", "attempt": 1, "events_cutoff": 55,
             "entries": [{"entry_id": KERNEL_ID, "shared": False}], "at": "2026-08-12T12:10:00Z"},
            {"kind": "settle", "run_id": RUN2, "skill": "sure_onboard", "unit": "build_env", "entry_id": KERNEL_ID,
             "outcome": "useful_unattributed", "at": "2026-08-12T12:30:00Z"},
        ]
        for run_id, rows in ((RUN1, rows1), (RUN2, rows2)):
            for row in rows:
                paths.append_jsonl(self.memory / "usage" / f"{run_id}.jsonl", row, 4096)
        with (self.memory / "usage" / f"{RUN2}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write("{broken line\n")

    def _write_digests(self) -> None:
        paths.atomic_write_json(self.memory / "digests" / f"{RUN1}.json",
                                digest(RUN1, "sure_onboard", [("build_env", "passed", 2), ("validate_import", "passed", 1)]))
        paths.atomic_write_json(self.memory / "digests" / f"{RUN2}.json",
                                digest(RUN2, "sure_onboard", [("build_env", "passed", 3), ("validate_load", "passed", 2), ("verdict", "current", 0)]))

    def _write_run_dir(self) -> None:
        run_dir = self.root / ".sure" / "runs" / RUN1 / "artifacts"
        paths.atomic_write_text(run_dir / "build_env.log", "ok log\n")
        paths.atomic_write_text(run_dir / "changed.log", "something else\n")


class CliTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "instance"
        self.tree = MemoryTree(self.root)
        self.tree.build()
        self.memory = self.tree.memory

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--root", str(self.root), *argv])
        return code, out.getvalue(), err.getvalue()

    def meta_of(self, entry_id: str) -> dict:
        skill, slug = entry_id.split("/", 1)
        return json.loads((self.memory / "meta" / skill / f"{slug}.json").read_text(encoding="utf-8"))

    def decisions(self, action: str) -> list[dict]:
        """decisions.jsonl rows of one action (skeleton 1.6: the key is `action`)."""
        rows, _ = paths.read_jsonl(self.memory / "decisions.jsonl")
        return [r for r in rows if r.get("action") == action]

    def index_ids(self) -> set[str]:
        idx = json.loads((self.memory / "index.json").read_text(encoding="utf-8"))
        return {e["entry_id"] for e in idx["entries"]}

    def index_status(self, entry_id: str) -> str | None:
        """status of one index.json row, None when the entry is not indexed at all."""
        idx = json.loads((self.memory / "index.json").read_text(encoding="utf-8"))
        return next((e["status"] for e in idx["entries"] if e["entry_id"] == entry_id), None)

    def provisional_md(self, entry_id: str) -> Path:
        skill, slug = entry_id.split("/", 1)
        return self.memory / "provisional" / skill / slug / "entry.md"

    @staticmethod
    def row_ids(text: str) -> set[str]:
        """First column of every printed line (entry ids of table rows)."""
        return {line.split()[0] for line in text.splitlines() if line.split()}

    def row_of(self, text: str, entry_id: str) -> str:
        """The table line whose first column is entry_id (an id can also appear inside another row's note)."""
        for line in text.splitlines():
            if line.split() and line.split()[0] == entry_id:
                return line
        self.fail(f"no row for {entry_id!r} in:\n{text}")
        return ""


class ListTests(CliTestBase):
    def test_lists_every_entry_with_counts_and_cell(self) -> None:
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        for entry_id in (KERNEL_ID, MODIFY_ID, SUPERSEDE_ID, LEGACY_MODIFY_ID, FACT_ID, COLD_ID, LEGACY_ID):
            self.assertIn(entry_id, out)
        line = self.row_of(out, KERNEL_ID)
        self.assertIn("bad_case", line)
        self.assertIn("provisional", line)
        self.assertIn("sure_onboard/build_env x cuda_version_mismatch", line)
        self.assertIn("1/1", line)  # useful activated / unattributed
        self.assertIn("2026-08-12", line)  # last_hit from the settle row of run 2
        self.assertIn("7 entries", out)

    def test_status_filter(self) -> None:
        self.run_cli("confirm", KERNEL_ID)
        code, out, _ = self.run_cli("list", "--status", "confirmed")
        self.assertEqual(code, 0)
        self.assertIn(KERNEL_ID, self.row_ids(out))
        self.assertIn(LEGACY_ID, self.row_ids(out))  # references entries count as confirmed
        self.assertNotIn(COLD_ID, self.row_ids(out))

    def test_skill_filter(self) -> None:
        _, out, _ = self.run_cli("list", "--skill", "sure_eval")
        self.assertEqual(self.row_ids(out) & {COLD_ID, KERNEL_ID}, {COLD_ID})
        self.assertIn("1 entries", out)

    def test_cold_lists_only_never_injected_entries(self) -> None:
        _, out, _ = self.run_cli("list", "--cold")
        ids = self.row_ids(out)
        self.assertNotIn(KERNEL_ID, ids)
        self.assertIn(COLD_ID, ids)
        self.assertIn(FACT_ID, ids)  # pre_start hits are not injections, so a fact stays cold (spec 8.2)

    def test_cold_days_needs_a_creation_date_that_old(self) -> None:
        _, out, _ = self.run_cli("list", "--cold", "--days", "30")
        ids = self.row_ids(out)
        self.assertIn(COLD_ID, ids)          # created 2020-01-01
        self.assertNotIn(MODIFY_ID, ids)     # created today
        self.assertNotIn(LEGACY_ID, ids)     # no creation date: cannot be judged
        code, _, err = self.run_cli("list", "--days", "30")
        self.assertEqual(code, 1)
        self.assertIn("--cold", err)

    def test_fact_row_shows_checked_at_and_stale(self) -> None:
        _, out, _ = self.run_cli("list")
        line = self.row_of(out, FACT_ID)
        self.assertIn("checked_at=2020-01-01", line)
        self.assertIn("[stale]", line)

    def test_legacy_without_trigger_is_marked(self) -> None:
        _, out, _ = self.run_cli("list")
        self.assertIn(cli.NOTE_LEGACY_NO_TRIGGER, self.row_of(out, LEGACY_ID))

    def test_list_marks_a_bad_case_whose_hook_trigger_is_empty(self) -> None:
        m = self.meta_of(COLD_ID)
        m["hook_trigger"] = []  # publish found none of the triggers in the run digest
        paths.atomic_write_json(self.memory / "meta" / "sure_eval" / "cold-entry.json", m)
        _, out, _ = self.run_cli("list")
        self.assertIn(cli.NOTE_NO_HOOK_TRIGGER, self.row_of(out, COLD_ID))
        self.assertNotIn(cli.NOTE_NO_HOOK_TRIGGER, self.row_of(out, KERNEL_ID))

    def test_list_marks_a_bad_case_whose_component_is_underscore(self) -> None:
        m = self.meta_of(COLD_ID)
        m["component"] = "_"  # matchBadCases needs component == the failing unit; no unit is named _
        paths.atomic_write_json(self.memory / "meta" / "sure_eval" / "cold-entry.json", m)
        _, out, _ = self.run_cli("list")
        self.assertIn(cli.NOTE_NO_HOOK_TRIGGER, self.row_of(out, COLD_ID))

    def test_list_does_not_mark_a_fact_without_triggers(self) -> None:
        _, out, _ = self.run_cli("list")  # facts match on scope, so no trigger is not the same as dead
        self.assertEqual(self.meta_of(FACT_ID)["hook_trigger"], [])
        self.assertNotIn(cli.NOTE_NO_HOOK_TRIGGER, self.row_of(out, FACT_ID))

    def test_a_legacy_entry_gets_one_note_not_two(self) -> None:
        _, out, _ = self.run_cli("list")
        row = self.row_of(out, LEGACY_ID)
        self.assertIn(cli.NOTE_LEGACY_NO_TRIGGER, row)
        self.assertNotIn(cli.NOTE_NO_HOOK_TRIGGER, row)

    def test_candidates_show_their_target_and_orphan_after_reject(self) -> None:
        _, out, _ = self.run_cli("list")
        self.assertIn(f"modify->{KERNEL_ID}", self.row_of(out, MODIFY_ID))
        self.assertNotIn(cli.NOTE_ORPHAN, self.row_of(out, MODIFY_ID))
        self.run_cli("reject", KERNEL_ID, "--reason", "wrong root cause")
        _, out, _ = self.run_cli("list")
        self.assertIn(cli.NOTE_ORPHAN, self.row_of(out, MODIFY_ID))
        self.assertIn(cli.NOTE_ORPHAN, self.row_of(out, SUPERSEDE_ID))


class ShowTests(CliTestBase):
    def test_show_prints_body_meta_proposal_history_and_descendants(self) -> None:
        code, out, _ = self.run_cli("show", KERNEL_ID)
        self.assertEqual(code, 0)
        self.assertIn("# CUDA arch mismatch: no kernel image is available", out)
        self.assertIn('"entry_id": "sure_onboard/no-kernel-image"', out)          # meta
        self.assertIn('"schema": "sure.memory.proposal.v2"', out)                 # proposal
        self.assertIn(f"inject    {RUN1}  build_env  attempt 1", out)              # usage history
        self.assertIn(f"settle    {RUN2}  build_env  useful_unattributed", out)
        self.assertIn(f"{MODIFY_ID}  (derived_from)", out)                          # descendants
        self.assertIn(f"{SUPERSEDE_ID}  (supersede candidate", out)

    def test_show_marks_evidence_ok_changed_missing_without_touching_status(self) -> None:
        _, out, _ = self.run_cli("show", KERNEL_ID)
        self.assertIn("ok       artifacts/build_env.log", out)
        self.assertIn("changed  artifacts/changed.log", out)
        self.assertIn("missing  artifacts/gone.log", out)
        self.assertEqual(self.meta_of(KERNEL_ID)["status"], "provisional")

    def test_show_prints_the_meta_status_not_the_stale_file_header(self) -> None:
        # confirm stamps `Status: confirmed` into the outbox copy only, so the provisional file
        # keeps its publish-time header; show must not print that next to a meta saying confirmed.
        code, out, _ = self.run_cli("confirm", KERNEL_ID, "--reason", "seen twice on the cluster")
        self.assertEqual(code, 0, out)
        code, out, _ = self.run_cli("show", KERNEL_ID)
        self.assertEqual(code, 0)
        self.assertIn("Status: confirmed", out)
        self.assertNotIn("Status: provisional", out)
        # the fix is read-side: nothing on disk is rewritten.
        self.assertIn("Status: provisional", self.provisional_md(KERNEL_ID).read_text(encoding="utf-8"))

    def test_show_legacy_entry_without_meta(self) -> None:
        code, out, _ = self.run_cli("show", LEGACY_ID)
        self.assertEqual(code, 0)
        self.assertIn("no meta file", out)
        self.assertIn("no proposal.json", out)
        self.assertIn("never injected", out)

    def test_show_unknown_entry_exits_1(self) -> None:
        code, _, err = self.run_cli("show", "sure_onboard/does-not-exist")
        self.assertEqual(code, 1)
        self.assertIn("unknown entry", err)
        code, _, err = self.run_cli("show", "not-an-id")
        self.assertEqual(code, 1)
        self.assertIn("<target_skill>/<slug>", err)


class CompareTests(CliTestBase):
    def test_compare_modify_candidate_diffs_against_target(self) -> None:
        code, out, _ = self.run_cli("compare", MODIFY_ID)
        self.assertEqual(code, 0)
        self.assertIn(f"--- {KERNEL_ID}", out)
        self.assertIn(f"+++ {MODIFY_ID}", out)
        self.assertIn("+Print torch.cuda.get_arch_list() before reinstalling.", out)
        self.assertIn("-Reinstall torch for the GPU arch.", out)

    def test_compare_add_uses_similar_entry(self) -> None:
        prop_path = self.memory / "provisional" / "sure_eval" / "cold-entry" / "proposal.json"
        prop = json.loads(prop_path.read_text(encoding="utf-8"))
        prop["similar"] = {"entry": KERNEL_ID, "difference": "different unit"}
        paths.atomic_write_json(prop_path, prop)
        code, out, _ = self.run_cli("compare", COLD_ID)
        self.assertEqual(code, 0)
        self.assertIn(f"--- {KERNEL_ID}", out)
        self.assertIn(f"+++ {COLD_ID}", out)

    def test_compare_without_target_or_similar_exits_1(self) -> None:
        code, _, err = self.run_cli("compare", COLD_ID)
        self.assertEqual(code, 1)
        self.assertIn("names no target_entry or similar.entry", err)


class ConfirmTests(CliTestBase):
    def test_confirm_add_copies_to_outbox_and_records_decision(self) -> None:
        code, out, _ = self.run_cli("confirm", KERNEL_ID, "--reason", "seen twice on the cluster")
        self.assertEqual(code, 0, out)
        outbox = self.memory / "outbox" / "sure_onboard" / "no-kernel-image"
        self.assertTrue((outbox / "entry.md").is_file())
        self.assertTrue((outbox / "proposal.json").is_file())
        self.assertTrue((self.memory / "provisional" / "sure_onboard" / "no-kernel-image" / "entry.md").is_file())  # provisional copy stays
        m = self.meta_of(KERNEL_ID)
        self.assertEqual(m["status"], "confirmed")
        self.assertEqual(m["confirmed"], {"by": "human", "date": paths.utc_today()})
        self.assertEqual(m["hook_trigger"], ["no kernel image is available"])  # confirm never touches hook_trigger
        rows = self.decisions("confirm")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "confirm")
        self.assertEqual(rows[0]["entry_id"], KERNEL_ID)
        self.assertEqual(rows[0]["by"], "human")
        self.assertEqual(rows[0]["reason"], "seen twice on the cluster")
        self.assertEqual(rows[0]["op"], "add")
        self.assertTrue(rows[0]["at"].endswith("Z"))
        self.assertNotIn("kind", rows[0])
        self.assertIn("export", out)
        # the index was rebuilt: the entry now shows as confirmed
        idx = json.loads((self.memory / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(next(e for e in idx["entries"] if e["entry_id"] == KERNEL_ID)["status"], "confirmed")

    def test_confirm_stamps_the_outbox_copy_confirmed_like_auto_promotion_does(self) -> None:
        code, _, err = self.run_cli("confirm", KERNEL_ID)
        self.assertEqual(code, 0, err)
        staged = (self.memory / "outbox" / "sure_onboard" / "no-kernel-image" / "entry.md").read_text(encoding="utf-8")
        self.assertIn("\nStatus: confirmed\n", staged)          # promote._promote writes the same line
        self.assertNotIn("Status: provisional", staged)
        self.assertIn("Status: provisional", self.provisional_md(KERNEL_ID).read_text(encoding="utf-8"))

    def test_confirm_refuses_non_provisional(self) -> None:
        self.run_cli("confirm", KERNEL_ID)
        code, _, err = self.run_cli("confirm", KERNEL_ID)
        self.assertEqual(code, 1)
        self.assertIn("only provisional or disputed", err)
        code, _, err = self.run_cli("confirm", LEGACY_ID)
        self.assertEqual(code, 1)

    def test_confirm_disputed_is_allowed(self) -> None:
        m = self.meta_of(COLD_ID)
        m["status"] = "disputed"
        paths.atomic_write_json(self.memory / "meta" / "sure_eval" / "cold-entry.json", m)
        code, _, _ = self.run_cli("confirm", COLD_ID)
        self.assertEqual(code, 0)
        self.assertEqual(self.meta_of(COLD_ID)["status"], "confirmed")

    def test_confirm_fact_is_allowed(self) -> None:
        code, _, _ = self.run_cli("confirm", FACT_ID)
        self.assertEqual(code, 0)
        self.assertTrue((self.memory / "outbox" / "_shared" / "vc-partition-names" / "entry.md").is_file())

    def test_confirm_modify_rewrites_provisional_target_body_and_keeps_header(self) -> None:
        target_md = self.memory / "provisional" / "sure_onboard" / "no-kernel-image" / "entry.md"
        before_header = target_md.read_text(encoding="utf-8").splitlines()[:5]
        code, out, _ = self.run_cli("confirm", MODIFY_ID)
        self.assertEqual(code, 0, out)
        self.assertIn(f"body applied to {KERNEL_ID}", out)  # rewritten in place: live at once, unlike a staged one
        after = target_md.read_text(encoding="utf-8")
        self.assertEqual(after.splitlines()[:5], before_header)
        self.assertIn("Print torch.cuda.get_arch_list() before reinstalling.", after)
        self.assertNotIn("Reinstall torch for the GPU arch.", after)
        # the rewritten provisional target keeps its place in the index only because entry_sha256 was refreshed
        target_meta = self.meta_of(KERNEL_ID)
        self.assertEqual(target_meta["entry_sha256"], paths.sha256_file(target_md))
        self.assertEqual(target_meta["hook_trigger"], ["no kernel image is available"])
        candidate = self.meta_of(MODIFY_ID)
        self.assertEqual(candidate["status"], "superseded")
        self.assertEqual(candidate["superseded_by"], KERNEL_ID)
        self.assertEqual(candidate["confirmed"]["by"], "human")
        self.assertFalse((self.memory / "outbox" / "sure_onboard" / "kernel-image-arch-list").exists())
        self.assertEqual(self.decisions("confirm")[0]["applied_to"], KERNEL_ID)
        # superseded rows stay in index.json with status superseded (match.ts skips them); the target stays provisional
        self.assertEqual(self.index_status(MODIFY_ID), "superseded")
        self.assertEqual(self.index_status(KERNEL_ID), "provisional")

    def test_confirm_modify_of_tracked_target_stages_merged_file_in_outbox(self) -> None:
        code, out, _ = self.run_cli("confirm", LEGACY_MODIFY_ID)
        self.assertEqual(code, 0, out)
        staged = self.memory / "outbox" / "sure_onboard" / "vc_partition_name_mismatch" / "entry.md"
        self.assertTrue(staged.is_file())
        self.assertIn("Resolve the alias with vc info -u first.", staged.read_text(encoding="utf-8"))
        # the tracked file itself was not touched
        tracked = self.root / "sure" / "skills" / "sure_onboard" / "references" / "memory" / "bad_cases" / "vc_partition_name_mismatch.md"
        self.assertEqual(tracked.read_text(encoding="utf-8"), LEGACY_ENTRY)
        self.assertEqual(self.meta_of(LEGACY_ID)["status"], "confirmed")
        self.assertIn("export it", out)

    def test_confirm_modify_leaves_a_target_already_staged_in_outbox_confirmed(self) -> None:
        # Every copy of the target takes the new body and keeps its own header: the outbox copy the
        # earlier confirm stamped `Status: confirmed` must not read as provisional again afterwards.
        self.assertEqual(self.run_cli("confirm", KERNEL_ID)[0], 0)
        code, _out, err = self.run_cli("confirm", MODIFY_ID)
        self.assertEqual(code, 0, err)
        staged = (self.memory / "outbox" / "sure_onboard" / "no-kernel-image" / "entry.md").read_text(encoding="utf-8")
        self.assertIn("Print torch.cuda.get_arch_list() before reinstalling.", staged)
        self.assertIn("\nStatus: confirmed\n", staged)
        self.assertNotIn("Status: provisional", staged)

    def test_confirm_supersede_marks_target_superseded(self) -> None:
        code, out, _ = self.run_cli("confirm", SUPERSEDE_ID)
        self.assertEqual(code, 0, out)
        self.assertTrue((self.memory / "outbox" / "sure_onboard" / "kernel-image-torch-index" / "entry.md").is_file())
        self.assertEqual(self.meta_of(SUPERSEDE_ID)["status"], "confirmed")
        target = self.meta_of(KERNEL_ID)
        self.assertEqual(target["status"], "superseded")
        self.assertEqual(target["superseded_by"], SUPERSEDE_ID)
        self.assertIsNotNone(target["superseded_at"])
        # apply_supersede appended the header to the provisional target and refreshed its entry_sha256
        target_md = self.provisional_md(KERNEL_ID)
        self.assertIn(f"Superseded-by: {SUPERSEDE_ID} (", target_md.read_text(encoding="utf-8"))
        self.assertEqual(target["entry_sha256"], paths.sha256_file(target_md))
        rows = self.decisions("supersede")
        self.assertGreaterEqual(len(rows), 1)
        self.assertEqual(rows[-1]["entry_id"], KERNEL_ID)
        self.assertEqual(rows[-1]["by"], "human")
        self.assertEqual(self.index_status(KERNEL_ID), "superseded")
        self.assertEqual(self.index_status(SUPERSEDE_ID), "confirmed")


class ExportTests(CliTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.clone = Path(self.tmp.name) / "clone"
        MemoryTree._write_references(self.clone)

    def test_export_writes_tracked_file_reconciles_readme_prints_git_and_never_runs_git(self) -> None:
        self.run_cli("confirm", KERNEL_ID)
        code, out, err = self.run_cli("export", KERNEL_ID, "--repo-root", str(self.clone))
        self.assertEqual(code, 0, err)
        dest = self.clone / "sure" / "skills" / "sure_onboard" / "references" / "memory" / "bad_cases" / "no-kernel-image.md"
        text = dest.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("Trigger: no kernel image is available\n"))
        self.assertIn("\nStatus: confirmed\n", text)
        self.assertNotIn("Status: provisional", text)
        readme = (dest.parent / "README.md").read_text(encoding="utf-8")
        self.assertIn(LEGACY_README.splitlines()[-1], readme)   # legacy row verbatim
        self.assertIn("`no-kernel-image.md`", readme)
        self.assertIn("never runs git", out)
        self.assertIn(" add ", out)
        self.assertIn("commit -m", out)
        self.assertIn("no-kernel-image.md", out)
        self.assertFalse((self.clone / ".git").exists())
        self.assertFalse(hasattr(cli, "subprocess"))
        self.assertFalse((self.memory / "outbox" / "sure_onboard" / "no-kernel-image").exists())
        m = self.meta_of(KERNEL_ID)
        self.assertEqual(m["exported"], paths.utc_today())
        self.assertEqual(m["hook_trigger"], ["no kernel image is available"])  # export never touches hook_trigger
        self.assertEqual(self.decisions("export"), [])  # export writes no decisions row, only meta.exported
        # the instance's own references were not written (a different clone was named)
        self.assertFalse((self.root / "sure" / "skills" / "sure_onboard" / "references" / "memory" / "bad_cases" / "no-kernel-image.md").exists())

    def test_export_fact_goes_to_shared_facts(self) -> None:
        self.run_cli("confirm", FACT_ID)
        code, out, err = self.run_cli("export", FACT_ID, "--repo-root", str(self.clone))
        self.assertEqual(code, 0, err)
        dest = self.clone / "sure" / "skills" / "_shared" / "memory" / "facts" / "vc-partition-names.md"
        self.assertTrue(dest.is_file())
        self.assertIn("Scope: cluster", dest.read_text(encoding="utf-8"))
        self.assertIn("by hand", out)

    def test_export_defaults_to_the_instance_root(self) -> None:
        self.run_cli("confirm", KERNEL_ID)
        code, _, err = self.run_cli("export", KERNEL_ID)
        self.assertEqual(code, 0, err)
        self.assertTrue((self.root / "sure" / "skills" / "sure_onboard" / "references" / "memory" / "bad_cases" / "no-kernel-image.md").is_file())
        self.assertIn(KERNEL_ID, self.index_ids())

    def test_export_requires_confirmed_and_an_outbox_copy(self) -> None:
        code, _, err = self.run_cli("export", KERNEL_ID, "--repo-root", str(self.clone))
        self.assertEqual(code, 1)
        self.assertIn("only confirmed", err)
        self.run_cli("confirm", KERNEL_ID)
        self.run_cli("export", KERNEL_ID, "--repo-root", str(self.clone))
        code, _, err = self.run_cli("export", KERNEL_ID, "--repo-root", str(self.clone))
        self.assertEqual(code, 1)
        self.assertIn("nothing in outbox", err)
        self.assertIn("already exported", err)

    def test_export_refuses_a_clone_without_the_skill_dir(self) -> None:
        self.run_cli("confirm", KERNEL_ID)
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        code, _, err = self.run_cli("export", KERNEL_ID, "--repo-root", str(empty))
        self.assertEqual(code, 1)
        self.assertIn("not a directory", err)
        self.assertTrue((self.memory / "outbox" / "sure_onboard" / "no-kernel-image" / "entry.md").is_file())

    def test_export_of_a_fact_refuses_a_clone_without_the_shared_dir(self) -> None:
        self.run_cli("confirm", FACT_ID)
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        code, _, err = self.run_cli("export", FACT_ID, "--repo-root", str(empty))
        self.assertEqual(code, 1)
        self.assertIn("not a directory", err)
        self.assertEqual(list(empty.rglob("*.md")), [])                       # nothing written outside a clone
        self.assertIsNone(self.meta_of(FACT_ID)["exported"])                  # still exportable
        self.assertTrue((self.memory / "outbox" / "_shared" / "vc-partition-names" / "entry.md").is_file())

    def test_export_of_staged_superseded_entry_updates_tracked_file(self) -> None:
        self.run_cli("supersede", LEGACY_ID, "--by", KERNEL_ID)
        code, _, err = self.run_cli("export", LEGACY_ID, "--repo-root", str(self.clone))
        self.assertEqual(code, 0, err)
        dest = self.clone / "sure" / "skills" / "sure_onboard" / "references" / "memory" / "bad_cases" / "vc_partition_name_mismatch.md"
        text = dest.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(f"Superseded-by: {KERNEL_ID} ("))
        # export always marks the exported copy confirmed, even on a legacy file that only ever
        # picked up a Superseded-by line (no pre-existing Status: line for _with_status to rewrite).
        self.assertIn("\nStatus: confirmed\n", text)


class ExportLineEndingTests(CliTestBase):
    """The repo runs core.autocrlf=true with no .gitattributes, so a CRLF working tree is the normal
    case for every references entry. promote.apply_supersede keeps a tracked file's endings, but the
    copy it stages for another clone did not, and `export` then rewrote every line of the destination:
    one added header line reads as a whole-file diff, which is the fast-forward hazard on a shared
    checkout. Real CRLF content is used here (the legacy entry, twenty-odd lines), not a one-liner."""

    REL = "sure/skills/sure_onboard/references/memory/bad_cases/vc_partition_name_mismatch.md"

    def setUp(self) -> None:
        super().setUp()
        self.clone = Path(self.tmp.name) / "clone"
        MemoryTree._write_references(self.clone)
        self.tracked = self.root / self.REL
        self.clone_tracked = self.clone / self.REL
        for path in (self.tracked, self.clone_tracked):
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

    @staticmethod
    def endings(path: Path) -> tuple[int, int]:
        """(CRLF line count, total line count). Equal means every line is CRLF, 0 means every line is LF."""
        payload = path.read_bytes()
        return payload.count(b"\r\n"), payload.count(b"\n")

    def test_export_keeps_the_crlf_the_staged_and_tracked_files_had(self) -> None:
        crlf, total = self.endings(self.tracked)
        self.assertEqual(crlf, total)
        self.assertGreater(total, 20)  # a real entry, not a synthetic line
        code, _out, err = self.run_cli("supersede", LEGACY_ID, "--by", KERNEL_ID)
        self.assertEqual(code, 0, err)
        crlf, total = self.endings(self.tracked)
        self.assertEqual(crlf, total, "apply_supersede already keeps this checkout's own file")
        staged = self.memory / "outbox" / "sure_onboard" / "vc_partition_name_mismatch" / "entry.md"
        crlf, total = self.endings(staged)
        self.assertEqual(crlf, total, "the copy export carries to another clone must keep them too")
        code, _out, err = self.run_cli("export", LEGACY_ID, "--repo-root", str(self.clone))
        self.assertEqual(code, 0, err)
        crlf, total = self.endings(self.clone_tracked)
        self.assertEqual(crlf, total, f"export flattened {total - crlf} of {total} lines to LF")
        self.assertGreater(total, 20)
        text = self.clone_tracked.read_text(encoding="utf-8")  # universal newlines: content only
        self.assertTrue(text.startswith(f"Superseded-by: {KERNEL_ID} ("))
        self.assertIn("\nStatus: confirmed\n", text)
        self.assertIn("# Bad Case: VC Partition Name Mismatch", text)

    def test_export_leaves_an_lf_entry_on_lf(self) -> None:
        # Nothing is converted: the provisional entries publish writes are LF and stay LF, whatever
        # the endings of the clone they are exported into.
        self.run_cli("confirm", KERNEL_ID)
        code, _out, err = self.run_cli("export", KERNEL_ID, "--repo-root", str(self.clone))
        self.assertEqual(code, 0, err)
        dest = self.clone / "sure/skills/sure_onboard/references/memory/bad_cases/no-kernel-image.md"
        crlf, total = self.endings(dest)
        self.assertEqual(crlf, 0)
        self.assertGreater(total, 10)

    def test_modify_of_a_tracked_entry_keeps_its_crlf_through_export(self) -> None:
        # Same command, the other staging path: _apply_modify takes the tracked file's header.
        code, _out, err = self.run_cli("confirm", LEGACY_MODIFY_ID)
        self.assertEqual(code, 0, err)
        staged = self.memory / "outbox" / "sure_onboard" / "vc_partition_name_mismatch" / "entry.md"
        crlf, total = self.endings(staged)
        self.assertEqual(crlf, total)
        code, _out, err = self.run_cli("export", LEGACY_ID, "--repo-root", str(self.clone))
        self.assertEqual(code, 0, err)
        crlf, total = self.endings(self.clone_tracked)
        self.assertEqual(crlf, total, f"export flattened {total - crlf} of {total} lines to LF")


class ModifyOfAnExportedTargetTests(CliTestBase):
    """Confirming a modify whose target has already been exported.

    `export` writes the git-tracked references file and deletes the outbox copy, but the target's
    provisional/ copy stays on disk on purpose: `cli confirm` and promote._promote both re-stage
    from it after a demotion. So an exported entry has two copies, and index.py reads the tracked
    one (test_index.test_references_win_over_provisional_twin): the merge has to land there, and
    the copy it does not read must not be left holding the body the merge replaced."""

    MARKER = "Print torch.cuda.get_arch_list() before reinstalling."   # the modify candidate's mitigation
    REPLACED = "Reinstall torch for the GPU arch."                     # the target's, before the merge
    REL = "sure/skills/sure_onboard/references/memory/bad_cases/no-kernel-image.md"

    def setUp(self) -> None:
        super().setUp()
        self.config = paths.load_config()
        self.tracked = self.root / self.REL
        self.assertEqual(self.run_cli("confirm", KERNEL_ID)[0], 0)
        self.assertEqual(self.run_cli("export", KERNEL_ID)[0], 0)  # no --repo-root: into this checkout
        self.assertTrue(self.tracked.is_file())
        self.assertTrue(self.provisional_md(KERNEL_ID).is_file(), "export keeps the provisional copy")

    def indexed_text(self) -> str:
        """The entry file index.json points the hooks at for the target."""
        idx = json.loads((self.memory / "index.json").read_text(encoding="utf-8"))
        row = next(e for e in idx["entries"] if e["entry_id"] == KERNEL_ID)
        return (self.root / row["path"]).read_text(encoding="utf-8")

    def dispute(self, run_id: str, at: str) -> None:
        paths.append_jsonl(self.memory / "usage" / f"{run_id}.jsonl", {
            "kind": "settle", "run_id": run_id, "skill": "sure_onboard", "unit": "build_env",
            "entry_id": KERNEL_ID, "outcome": "disputed", "at": at}, 4096)

    def test_a_confirmed_modify_reaches_the_file_the_index_reads(self) -> None:
        code, out, err = self.run_cli("confirm", MODIFY_ID)
        self.assertEqual(code, 0, err)
        self.assertIn("export it", out)  # the operator is told the revision is staged, not yet live
        staged = self.memory / "outbox" / "sure_onboard" / "no-kernel-image" / "entry.md"
        self.assertTrue(staged.is_file(), "the merge has to be staged where export can carry it")
        self.assertIn(self.MARKER, staged.read_text(encoding="utf-8"))
        self.assertIsNone(self.meta_of(KERNEL_ID)["exported"], "export refuses a re-export otherwise")
        code, _out, err = self.run_cli("export", KERNEL_ID)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.run_cli("rebuild-index")[0], 0)
        indexed = self.indexed_text()
        self.assertIn(self.MARKER, indexed)
        self.assertNotIn(self.REPLACED, indexed)
        self.assertEqual(self.index_status(KERNEL_ID), "confirmed")
        self.assertEqual(self.meta_of(MODIFY_ID)["status"], "superseded")

    def test_the_confirm_summary_does_not_call_a_staged_body_applied(self) -> None:
        code, out, err = self.run_cli("confirm", MODIFY_ID)
        self.assertEqual(code, 0, err)
        # the tracked file the index reads is untouched until the human exports it, so the summary
        # must not read as done: this is the line that made the discarded merge look like a success
        self.assertNotIn("applied", out)
        self.assertIn("staged", out)
        self.assertIn(KERNEL_ID, out)
        self.assertEqual(self.decisions("confirm")[-1]["applied_to"], KERNEL_ID)  # the row is unchanged

    def test_the_provisional_copy_of_the_target_is_merged_too(self) -> None:
        self.assertEqual(self.run_cli("confirm", MODIFY_ID)[0], 0)
        prov = self.provisional_md(KERNEL_ID)
        text = prov.read_text(encoding="utf-8")
        self.assertIn(self.MARKER, text)
        self.assertNotIn(self.REPLACED, text)
        # a rewritten provisional entry.md needs a fresh hash or the index calls it tampered with
        self.assertEqual(self.meta_of(KERNEL_ID)["entry_sha256"], paths.sha256_file(prov))

    def test_a_demote_and_re_confirm_does_not_revert_the_merge(self) -> None:
        self.assertEqual(self.run_cli("confirm", MODIFY_ID)[0], 0)
        self.assertEqual(self.run_cli("export", KERNEL_ID)[0], 0)
        self.assertIn(self.MARKER, self.tracked.read_text(encoding="utf-8"))
        self.dispute("run-x", "2026-09-01T10:00:00Z")
        self.dispute("run-y", "2026-09-02T10:00:00Z")
        self.assertEqual([r["action"] for r in promote.promote_all(self.root, config=self.config)], ["demote"])
        self.assertEqual(self.meta_of(KERNEL_ID)["status"], "provisional")
        # cmd_confirm re-stages from the provisional copy: a copy left un-merged reverts the entry
        self.assertEqual(self.run_cli("confirm", KERNEL_ID, "--reason", "re-checked by hand")[0], 0)
        self.assertEqual(self.run_cli("export", KERNEL_ID)[0], 0)
        tracked = self.tracked.read_text(encoding="utf-8")
        self.assertIn(self.MARKER, tracked)
        self.assertNotIn(self.REPLACED, tracked)


class LifecycleInterleavingTests(CliTestBase):
    """The real cross-module sequences: a cli command and a post_finish promote pass over one tree.
    Every test here drives the actual modules; nothing is mocked except the point in time where the
    other writer runs (mock.patch on a helper cli calls before it takes the memory lock)."""

    def setUp(self) -> None:
        super().setUp()
        self.config = paths.load_config()
        self.clone = Path(self.tmp.name) / "clone"
        MemoryTree._write_references(self.clone)

    def useful(self, entry_id: str, run_id: str, at: str) -> None:
        paths.append_jsonl(self.memory / "usage" / f"{run_id}.jsonl", {
            "kind": "settle", "run_id": run_id, "skill": "sure_onboard", "unit": "build_env",
            "entry_id": entry_id, "outcome": "useful_activated", "at": at}, 4096)

    def dispute(self, entry_id: str, run_id: str, at: str) -> None:
        paths.append_jsonl(self.memory / "usage" / f"{run_id}.jsonl", {
            "kind": "settle", "run_id": run_id, "skill": "sure_onboard", "unit": "build_env",
            "entry_id": entry_id, "outcome": "disputed", "at": at}, 4096)

    def promote_pass(self) -> list[dict]:
        return promote.promote_all(self.root, config=self.config)

    def test_a_human_confirm_of_a_disputed_entry_survives_the_next_promote_passes(self) -> None:
        self.dispute(KERNEL_ID, "run-x", "2026-08-13T10:00:00Z")
        self.dispute(KERNEL_ID, "run-y", "2026-08-14T10:00:00Z")
        self.promote_pass()
        self.assertEqual(self.meta_of(KERNEL_ID)["status"], "disputed")
        code, _, err = self.run_cli("confirm", KERNEL_ID, "--reason", "checked by hand")
        self.assertEqual(code, 0, err)
        outbox = self.memory / "outbox" / "sure_onboard" / "no-kernel-image" / "entry.md"
        self.assertTrue(outbox.is_file())
        for _pass in range(3):  # no new usage rows at all
            self.assertEqual(self.promote_pass(), [])
            m = self.meta_of(KERNEL_ID)
            self.assertEqual(m["status"], "confirmed")
            self.assertEqual(m["confirmed"], {"by": "human", "date": paths.utc_today()})
            self.assertTrue(outbox.is_file())
        self.assertEqual(self.decisions("demote"), [])

    def _auto_confirm_kernel(self) -> None:
        """Drive the real auto-promotion: two useful_activated settles from two distinct runs."""
        self.useful(KERNEL_ID, "run-p", "2026-08-15T10:00:00Z")
        self.assertEqual([r["action"] for r in self.promote_pass()], ["promote"])
        self.assertEqual(self.meta_of(KERNEL_ID)["status"], "confirmed")

    def test_export_never_restores_a_status_a_promote_pass_changed_under_it(self) -> None:
        self._auto_confirm_kernel()
        self.dispute(KERNEL_ID, "run-q", "2026-08-16T10:00:00Z")
        self.dispute(KERNEL_ID, "run-r", "2026-08-17T10:00:00Z")
        real = cli._export_destination

        def demote_first(*args, **kwargs):  # a run's post_finish promote pass, mid-command
            self.assertEqual([r["action"] for r in self.promote_pass()], ["demote"])
            return real(*args, **kwargs)

        with mock.patch.object(cli, "_export_destination", demote_first):
            code, out, err = self.run_cli("export", KERNEL_ID, "--repo-root", str(self.clone))
        self.assertEqual(code, 1, out)
        self.assertIn("provisional", err)
        m = self.meta_of(KERNEL_ID)
        self.assertEqual(m["status"], "provisional")
        self.assertIsNone(m["confirmed"])
        self.assertIsNone(m["exported"])
        dest = self.clone / "sure" / "skills" / "sure_onboard" / "references" / "memory" / "bad_cases" / "no-kernel-image.md"
        self.assertFalse(dest.exists())
        # one real transition, one decision row: the invariant promote_all's docstring claims
        self.assertEqual(self.promote_pass(), [])
        self.assertEqual([r["action"] for r in self.decisions("demote")], ["demote"])

    def test_confirm_refuses_an_entry_another_operator_rejected_meanwhile(self) -> None:
        real = cli._proposal

        def reject_first(*args, **kwargs):
            if not getattr(self, "_rejected", False):
                self._rejected = True
                self.assertEqual(cli.main(["--root", str(self.root), "reject", KERNEL_ID, "--reason", "wrong"]), 0)
            return real(*args, **kwargs)

        with mock.patch.object(cli, "_proposal", reject_first), contextlib.redirect_stdout(io.StringIO()):
            code, _, err = self.run_cli("confirm", KERNEL_ID, "--reason", "looks fine")
        self.assertEqual(code, 1)
        self.assertIn("rejected", err)
        m = self.meta_of(KERNEL_ID)
        self.assertEqual(m["status"], "rejected")
        self.assertIsNone(m["confirmed"])
        self.assertFalse((self.memory / "outbox" / "sure_onboard" / "no-kernel-image").exists())

    def test_reject_keeps_counters_a_promote_pass_refreshed_under_it(self) -> None:
        real = cli._references_file

        def promote_first(*args, **kwargs):
            if not getattr(self, "_promoted", False):
                self._promoted = True
                self.useful(COLD_ID, "run-s", "2026-08-15T10:00:00Z")
                self.useful(COLD_ID, "run-t", "2026-08-16T10:00:00Z")
                self.promote_pass()
            return real(*args, **kwargs)

        with mock.patch.object(cli, "_references_file", promote_first):
            code, out, _ = self.run_cli("reject", COLD_ID, "--reason", "duplicate")
        self.assertEqual(code, 0, out)
        m = self.meta_of(COLD_ID)
        self.assertEqual(m["status"], "rejected")
        self.assertEqual(m["useful_activated"], 2)
        self.assertEqual(m["useful_runs"], ["run-s", "run-t"])


class RejectTests(CliTestBase):
    def test_reject_moves_provisional_to_rejected_and_records_reason(self) -> None:
        code, out, _ = self.run_cli("reject", COLD_ID, "--reason", "duplicate of a playbook rule")
        self.assertEqual(code, 0, out)
        self.assertFalse((self.memory / "provisional" / "sure_eval" / "cold-entry").exists())
        self.assertTrue((self.memory / "rejected" / "sure_eval" / "cold-entry" / "entry.md").is_file())
        self.assertTrue((self.memory / "rejected" / "sure_eval" / "cold-entry" / "proposal.json").is_file())
        self.assertEqual(self.meta_of(COLD_ID)["status"], "rejected")
        rows = self.decisions("reject")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "reject")
        self.assertEqual(rows[0]["by"], "human")
        self.assertEqual(rows[0]["reason"], "duplicate of a playbook rule")
        self.assertNotIn(COLD_ID, self.index_ids())  # rejected provisional entries leave index.json
        code, _, err = self.run_cli("reject", COLD_ID, "--reason", "again")
        self.assertEqual(code, 1)
        self.assertIn("already rejected", err)

    def test_reject_requires_reason(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            self.run_cli("reject", COLD_ID)
        self.assertEqual(cm.exception.code, 2)

    def test_reject_prints_descendants_without_cascading(self) -> None:
        code, out, _ = self.run_cli("reject", KERNEL_ID, "--reason", "wrong root cause")
        self.assertEqual(code, 0)
        self.assertIn("descendants (not cascaded", out)
        self.assertIn(MODIFY_ID, out)
        self.assertIn(SUPERSEDE_ID, out)
        self.assertEqual(self.meta_of(MODIFY_ID)["status"], "provisional")
        self.assertTrue((self.memory / "provisional" / "sure_onboard" / "kernel-image-torch-index" / "entry.md").is_file())

    def test_reject_legacy_entry_warns_about_playbooks_and_prints_delete_path(self) -> None:
        code, out, err = self.run_cli("reject", LEGACY_ID, "--reason", "outdated")
        self.assertEqual(code, 0, err)
        self.assertIn("warning", err)
        self.assertIn("task_playbooks/VC.md", err)
        self.assertIn("delete sure/skills/sure_onboard/references/memory/bad_cases/vc_partition_name_mismatch.md", out)
        tracked = self.root / "sure" / "skills" / "sure_onboard" / "references" / "memory" / "bad_cases" / "vc_partition_name_mismatch.md"
        self.assertTrue(tracked.is_file())  # never touches tracked files
        self.assertEqual(self.meta_of(LEGACY_ID)["status"], "rejected")
        self.assertEqual(self.decisions("reject")[0]["entry_id"], LEGACY_ID)

    def test_reject_marks_existing_children_orphan_in_their_meta(self) -> None:
        for child in (MODIFY_ID, SUPERSEDE_ID):  # both target KERNEL_ID
            self.assertFalse(self.meta_of(child)["orphan"])
        code, out, _ = self.run_cli("reject", KERNEL_ID, "--reason", "wrong lesson")
        self.assertEqual(code, 0, out)
        for child in (MODIFY_ID, SUPERSEDE_ID):
            self.assertTrue(self.meta_of(child)["orphan"], child)
        self.assertFalse(self.meta_of(LEGACY_MODIFY_ID)["orphan"])  # targets a different entry

    def test_reject_removes_outbox_copy(self) -> None:
        self.run_cli("confirm", KERNEL_ID)
        code, _, _ = self.run_cli("reject", KERNEL_ID, "--reason", "changed my mind")
        self.assertEqual(code, 0)
        self.assertFalse((self.memory / "outbox" / "sure_onboard" / "no-kernel-image").exists())
        self.assertTrue((self.memory / "rejected" / "sure_onboard" / "no-kernel-image" / "entry.md").is_file())


class SupersedeTests(CliTestBase):
    def test_supersede_marks_old_provisional_entry(self) -> None:
        code, out, err = self.run_cli("supersede", KERNEL_ID, "--by", SUPERSEDE_ID)
        self.assertEqual(code, 0, err)
        old = self.meta_of(KERNEL_ID)
        self.assertEqual(old["status"], "superseded")
        self.assertEqual(old["superseded_by"], SUPERSEDE_ID)
        old_md = self.provisional_md(KERNEL_ID)
        self.assertIn(f"Superseded-by: {SUPERSEDE_ID} (", old_md.read_text(encoding="utf-8"))
        self.assertEqual(old["entry_sha256"], paths.sha256_file(old_md))  # refreshed after the header rewrite
        rows = self.decisions("supersede")
        self.assertGreaterEqual(len(rows), 1)
        self.assertEqual(rows[-1]["by"], "human")
        self.assertEqual(self.index_status(KERNEL_ID), "superseded")  # stays in index.json, out of index.md / matching
        self.assertIn(f"superseded {KERNEL_ID} by {SUPERSEDE_ID}", out)

    def test_supersede_tracked_entry_creates_meta_and_stages_outbox_copy(self) -> None:
        code, out, err = self.run_cli("supersede", LEGACY_ID, "--by", KERNEL_ID)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.meta_of(LEGACY_ID)["superseded_by"], KERNEL_ID)
        staged = (self.memory / "outbox" / "sure_onboard" / "vc_partition_name_mismatch" / "entry.md").read_text(encoding="utf-8")
        self.assertTrue(staged.startswith(f"Superseded-by: {KERNEL_ID} ("))
        self.assertIn("# Bad Case: VC Partition Name Mismatch", staged)
        self.assertIn("export it", out)

    def test_supersede_says_it_rewrote_the_tracked_file(self) -> None:
        tracked = self.root / "sure" / "skills" / "sure_onboard" / "references" / "memory" / "bad_cases" / "vc_partition_name_mismatch.md"
        code, out, err = self.run_cli("supersede", LEGACY_ID, "--by", KERNEL_ID)
        self.assertEqual(code, 0, err)
        # the tracked file really was rewritten, so the message must say so (it is a git diff now)
        self.assertTrue(tracked.read_text(encoding="utf-8").startswith(f"Superseded-by: {KERNEL_ID} ("))
        self.assertIn("sure/skills/sure_onboard/references/memory/bad_cases/vc_partition_name_mismatch.md", out)
        self.assertIn("rewrote", out)
        self.assertIn("commit", out)

    def test_supersede_keeps_a_crlf_tracked_file_crlf(self) -> None:
        tracked = self.root / "sure" / "skills" / "sure_onboard" / "references" / "memory" / "bad_cases" / "vc_partition_name_mismatch.md"
        tracked.write_bytes(LEGACY_ENTRY.replace("\n", "\r\n").encode("utf-8"))
        before = tracked.read_bytes().count(b"\r\n")
        code, _, err = self.run_cli("supersede", LEGACY_ID, "--by", KERNEL_ID)
        self.assertEqual(code, 0, err)
        after = tracked.read_bytes()
        self.assertEqual(after.count(b"\r\n"), before + 2)   # the header line plus its blank separator
        self.assertEqual(after.count(b"\n"), after.count(b"\r\n"))  # not one bare LF: the diff stays one line
        self.assertTrue(after.startswith(f"Superseded-by: {KERNEL_ID} (".encode("utf-8")))

    def test_supersede_rejects_self_and_unknown_ids(self) -> None:
        code, _, err = self.run_cli("supersede", KERNEL_ID, "--by", KERNEL_ID)
        self.assertEqual(code, 1)
        self.assertIn("cannot supersede itself", err)
        code, _, err = self.run_cli("supersede", KERNEL_ID, "--by", "sure_onboard/nope")
        self.assertEqual(code, 1)
        self.assertIn("unknown entry", err)


class StatsTests(CliTestBase):
    def test_stats_prints_every_section(self) -> None:
        code, out, err = self.run_cli("stats")
        self.assertEqual(code, 0, err)
        line = self.row_of(out, KERNEL_ID)
        for column in ("2", "1", "1", "0"):
            self.assertIn(column, line)
        self.assertIn(cli.NOTE_LEGACY_NO_TRIGGER, self.row_of(out, LEGACY_ID))
        self.assertIn("1 bad usage line(s) skipped", out)
        self.assertIn(f"sure_onboard/build_env: {RUN1}=2+ {RUN2}=3+", out)
        self.assertIn(f"sure_onboard/validate_load: {RUN2}=2", out)
        self.assertNotIn("verdict", out)  # current units have no attempts yet
        self.assertIn("injected+read 1/1 (100%)  injected-not-read 0/1 (0%)  no-injection 1/1 (100%)", out)
        self.assertIn("cold ratio: 6/7", out)
        self.assertIn("sure_onboard/build_env x cuda_version_mismatch: 3 (occupant provisional, modify candidate pending)", out)
        self.assertIn("provisional: 6", out)
        self.assertIn("confirmed: 1", out)
        self.assertIn("legacy: 1", out)
        self.assertIn(f"limit {cli.paths.load_config()['index_md_max_lines']} lines / {cli.paths.load_config()['index_md_max_bytes']} bytes", out)

    def test_stats_refreshes_meta_counts_from_usage(self) -> None:
        self.assertEqual(self.meta_of(KERNEL_ID)["injections"], 0)
        self.run_cli("stats")
        m = self.meta_of(KERNEL_ID)
        self.assertEqual(m["injections"], 2)
        self.assertEqual(m["useful_activated"], 1)
        self.assertEqual(m["useful_unattributed"], 1)
        self.assertEqual(m["useful_runs"], [RUN1])
        self.assertIsNotNone(m["last_hit"])

    def test_stats_since_narrows_rows_and_runs(self) -> None:
        code, out, _ = self.run_cli("stats", "--since", "2026-08-11")
        self.assertEqual(code, 0)
        self.assertNotIn(f"{RUN1}=2", out)
        self.assertIn(f"{RUN2}=3+", out)
        self.assertIn("injected+read 0/0  injected-not-read 0/1 (0%)", out)
        code, _, err = self.run_cli("stats", "--since", "yesterday")
        self.assertEqual(code, 1)
        self.assertIn("YYYY-MM-DD", err)

    def test_stats_skill_filter(self) -> None:
        _, out, _ = self.run_cli("stats", "--skill", "sure_eval")
        self.assertIn(COLD_ID, self.row_ids(out))
        self.assertNotIn(KERNEL_ID, self.row_ids(out))
        self.assertIn("no digests", out)

    def test_stats_skill_shared_keeps_hits_from_other_skills_runs(self) -> None:
        # a _shared fact is only ever used by runs of some other skill, so filtering the usage rows by run skill zeroed it
        _, out, _ = self.run_cli("stats", "--skill", "_shared")
        self.assertEqual(self.row_of(out, FACT_ID).split()[3], "1")  # columns: entry_id, status, inject, pre_start, ...

    def write_cold_archive(self) -> None:
        """usage/_archive.json as prune_usage leaves it: COLD_ID's run files are gone, its counts are not."""
        paths.atomic_write_json(self.memory / "usage" / "_archive.json", {
            "schema": usage.ARCHIVE_SCHEMA, "folded_runs": 1, "through": "2026-08-01T10:00:00Z", "pending": {},
            "entries": {COLD_ID: {"injections": 3, "useful_activated": 1, "useful_unattributed": 0,
                                  "useful_runs": ["run-old"], "disputed": 0, "last_hit": "2026-08-01T10:00:00Z"}}})

    def test_stats_counts_include_the_usage_archive(self) -> None:
        self.write_cold_archive()
        _, out, _ = self.run_cli("stats")
        row = self.row_of(out, COLD_ID).split()  # columns: entry_id, status, inject, pre_start, useful_act, ...
        self.assertEqual((row[2], row[4]), ("3", "1"))
        self.assertIn("cold ratio: 5/7", out)  # COLD_ID has a settle now, so it is not cold

    def test_stats_since_excludes_the_usage_archive(self) -> None:
        # deliberate: the archive keeps totals, not per-row dates, so no window can say what falls inside it
        self.write_cold_archive()
        _, out, _ = self.run_cli("stats", "--since", "2026-08-11")
        self.assertEqual(self.row_of(out, COLD_ID).split()[2:5], ["0", "0", "0"])
        self.assertIn("cold ratio: 6/7", out)


class RebuildAndPermsTests(CliTestBase):
    def test_rebuild_index_rewrites_index_and_reconciles_readme(self) -> None:
        (self.memory / "index.json").unlink()
        readme = self.root / "sure" / "skills" / "sure_onboard" / "references" / "memory" / "bad_cases" / "README.md"
        readme.write_text(LEGACY_README + "| stale row | `gone.md` | file no longer exists |\n", encoding="utf-8")
        code, out, err = self.run_cli("rebuild-index")
        self.assertEqual(code, 0, err)
        self.assertTrue((self.memory / "index.json").is_file())
        self.assertTrue((self.memory / "index.md").is_file())
        self.assertIn("index rebuilt: 7 entries", out)
        self.assertIn("index.md:", out)
        text = readme.read_text(encoding="utf-8")
        self.assertNotIn("`gone.md`", text)
        self.assertIn(LEGACY_README.splitlines()[-1], text)
        self.assertIn("README reconciled: sure/skills/sure_onboard/references/memory/bad_cases/README.md", out)

    def test_rebuild_index_names_a_hand_edited_provisional_entry(self) -> None:
        md = self.provisional_md(KERNEL_ID)
        md.write_text(md.read_text(encoding="utf-8").replace("no kernel image", "no kernel image (typo fixed)", 1), encoding="utf-8")
        code, out, err = self.run_cli("rebuild-index")
        self.assertEqual(code, index.EXIT_HASH_MISMATCH, err)  # a dropped entry has to reach the exit status too
        self.assertIn("1 provisional entries dropped: hash mismatch", out)
        self.assertIn(KERNEL_ID, out)
        self.assertNotIn(KERNEL_ID, self.index_ids())
        _code, out, _ = self.run_cli("rebuild-index")  # still reported until the operator fixes it
        self.assertIn("hash mismatch", out)

    def test_list_rebuilds_a_missing_or_corrupt_index(self) -> None:
        (self.memory / "index.json").write_text("{not json", encoding="utf-8")
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn(KERNEL_ID, out)
        self.assertEqual(json.loads((self.memory / "index.json").read_text(encoding="utf-8"))["schema"], "sure.memory.index.v1")

    def test_promote_runs_the_rules_and_rebuilds_the_index(self) -> None:
        for run, at in (("run-p", "2026-08-15T10:00:00Z"), ("run-q", "2026-08-16T10:00:00Z")):
            paths.append_jsonl(self.memory / "usage" / f"{run}.jsonl", {
                "kind": "settle", "run_id": run, "skill": "sure_onboard", "unit": "build_env",
                "entry_id": KERNEL_ID, "outcome": "useful_activated", "at": at}, 4096)
        code, out, err = self.run_cli("promote")
        self.assertEqual(code, 0, err)
        self.assertIn(f"promote: {KERNEL_ID}", out)
        self.assertIn("1 decision(s)", out)
        self.assertEqual(self.meta_of(KERNEL_ID)["status"], "confirmed")
        self.assertEqual(self.index_status(KERNEL_ID), "confirmed")
        self.assertEqual([r["by"] for r in self.decisions("promote")], ["auto"])
        code, out, _ = self.run_cli("promote")  # idempotent: nothing left to do
        self.assertEqual((code, "0 decision(s)" in out), (0, True))

    def test_fix_perms_runs_and_reports(self) -> None:
        code, out, _ = self.run_cli("fix-perms")
        self.assertIn(code, (0, 1))
        self.assertTrue(out.strip())


class MainTests(unittest.TestCase):
    def test_no_command_is_a_usage_error(self) -> None:
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(io.StringIO()):
            cli.main([])
        self.assertEqual(cm.exception.code, 2)

    def test_default_root_is_the_checkout_that_owns_cli(self) -> None:
        with mock.patch.object(paths, "ensure_memory_tree"):
            ctx = cli._load_ctx(None)
        self.assertEqual(ctx.repo_root, paths.LIB_DIR.parents[2])
        self.assertEqual(ctx.memory_root, paths.LIB_DIR.parents[2] / "sure" / "memory")

    def test_cli_imports_only_stdlib_and_the_memory_package(self) -> None:
        # python3 -s on the cluster has no third-party packages, and export must never shell out to git.
        imported = {name for name, value in vars(cli).items() if isinstance(value, types.ModuleType)}
        self.assertTrue(imported <= {"argparse", "difflib", "json", "os", "shlex", "shutil", "sys", "index_mod", "paths", "promote", "usage"}, imported)
        self.assertNotIn("subprocess", imported)


class HeaderHelperTests(unittest.TestCase):
    TEXT = bad_case_text(title="CUDA arch mismatch", trigger="no kernel image is available", unit="build_env",
                         cause="cuda_version_mismatch", run_id=RUN1, added="2026-08-10")

    def test_split_header_skips_leading_blank_lines(self) -> None:
        # index.parse_header skips them, so cli must too, or a hand-edited file silently loses its
        # Trigger and Cell the moment _with_superseded_by or _with_body_of rewrites it.
        header, body = cli._split_header("\n\n" + self.TEXT)
        self.assertEqual(len(header), 5)
        self.assertTrue(body.startswith("# CUDA arch mismatch"))
        merged = cli._with_superseded_by("\n" + self.TEXT, KERNEL_ID, "2026-08-19")
        self.assertEqual(merged.count("Superseded-by:"), 1)
        self.assertTrue(merged.startswith("Trigger: no kernel image is available\n"))

    def test_split_header_leaves_a_headerless_file_whole(self) -> None:
        header, body = cli._split_header("\n" + LEGACY_ENTRY)
        self.assertEqual(header, [])
        self.assertTrue(body.startswith("# Bad Case: VC Partition Name Mismatch"))


class MetaStubShapeTests(unittest.TestCase):
    def test_meta_stub_key_set_matches_promote_legacy_meta(self) -> None:
        """cli._meta_stub reconstructs a legacy meta the same way promote.legacy_meta does
        (skeleton 1.7): state fields only, no candidate-derived keys (component, cause, trigger,
        scope, op, target_entry, similar_entry, derived_from, fix_exercised, evidence_sha256).
        promote._entry_op reads meta.get("op") directly, so a stray "op" key here would let a
        genuinely op-less legacy entry qualify for auto-promotion once it is demoted to
        provisional; any future drift in either function's key set must fail this test."""
        view = {
            "entry_id": LEGACY_ID, "type": "bad_case", "status": "confirmed", "target_skill": "sure_onboard",
            "component": "build_env", "cause": "infra", "trigger": ["partition not found"], "scope": None,
            "checked_at": None, "op": "modify", "target_entry": KERNEL_ID, "similar_entry": None,
            "superseded_by": None,
        }
        stub_keys = sorted(cli._meta_stub(view))
        legacy_keys = sorted(promote.legacy_meta(LEGACY_ID))
        self.assertEqual(stub_keys, legacy_keys)


if __name__ == "__main__":
    unittest.main()
