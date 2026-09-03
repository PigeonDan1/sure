# sure/runtime/memory/test_docs.py
from __future__ import annotations

import itertools
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # sure/runtime

from memory import paths, proposals  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS = REPO_ROOT / "sure" / "skills"
EXTRACTION_MD = paths.LIB_DIR / "EXTRACTION.md"
ONBOARD_SKILL = SKILLS / "sure_onboard" / "SKILL.md"
INFER_SKILL = SKILLS / "sure_infer" / "SKILL.md"
EVAL_SKILL = SKILLS / "sure_eval" / "SKILL.md"
TRANS_SKILL = SKILLS / "sure_trans" / "SKILL.md"
ONBOARD_ROUTING = SKILLS / "sure_onboard" / "references" / "memory" / "ROUTING.md"
ONBOARD_COMMON = SKILLS / "sure_onboard" / "references" / "memory" / "COMMON.md"
ONBOARD_BAD_CASES_README = SKILLS / "sure_onboard" / "references" / "memory" / "bad_cases" / "README.md"
INFER_ROUTING = SKILLS / "sure_infer" / "references" / "memory" / "ROUTING.md"
INFER_BAD_CASES_README = SKILLS / "sure_infer" / "references" / "memory" / "bad_cases" / "README.md"
TRANS_ROUTING = SKILLS / "sure_trans" / "references" / "memory" / "ROUTING.md"
TRANS_BAD_CASES_README = SKILLS / "sure_trans" / "references" / "memory" / "bad_cases" / "README.md"
FEED_SKILL = SKILLS / "sure_feed" / "SKILL.md"
FEED_ROUTING = SKILLS / "sure_feed" / "references" / "memory" / "ROUTING.md"
FEED_BAD_CASES_README = SKILLS / "sure_feed" / "references" / "memory" / "bad_cases" / "README.md"
SHARED_FACTS_README = SKILLS / "_shared" / "memory" / "facts" / "README.md"
CONTEXT_SELECTION_SCHEMA = SKILLS / "sure_onboard" / "schemas" / "context_selection.schema.json"

# "| 22 | `extract_lessons` | **gate** | `extraction_declaration.json` | `scripts/check_memory_extraction.py` |"
UNIT_ROW = re.compile(r"^\| (\d+) \| `([a-z_]+)` \| (.+?) \| (.+?) \| (.+?) \|$")
# "| `max_candidates_per_run` | 5 | ... |"  (the Limits table at the end of EXTRACTION.md)
LIMIT_ROW = re.compile(r"^\| `([a-z_]+)` \| (\d+) \|", re.M)
JSON_BLOCK = re.compile(r"```json\n(.*?)\n```", re.S)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def section(text: str, heading_prefix: str) -> str:
    """Text from the heading that starts with heading_prefix up to the next '## ' heading."""
    start = text.index(heading_prefix)
    nxt = text.find("\n## ", start + len(heading_prefix))
    return text[start : nxt if nxt != -1 else len(text)]


def unit_table(skill_md: str) -> list[tuple[int, str, str, str, str]]:
    """Rows of the '## State Machine' table as (number, unit id, kind, produces, gate script)."""
    body = section(skill_md, "## State Machine")
    rows: list[tuple[int, str, str, str, str]] = []
    for line in body.splitlines():
        m = UNIT_ROW.match(line)
        if m:
            rows.append((int(m.group(1)), m.group(2), m.group(3), m.group(4), m.group(5)))
    return rows


def contract_line(skill_md: str, unit: str) -> str:
    """The per-unit contract: onboard and eval write it as one '- **<unit>**: ...' bullet,
    sure_feed as a '### <n>. <unit> (<kind>)' block of Inputs/Output/... bullets. Same content,
    so the block is flattened to one string and every assertion below reads either shape."""
    for line in skill_md.splitlines():
        if line.startswith(f"- **{unit}**:"):
            return line
    lines = skill_md.splitlines()
    for i, line in enumerate(lines):
        if re.match(rf"^### \d+\. {re.escape(unit)} \(", line):
            body = itertools.takewhile(lambda text: not text.startswith("### "), lines[i + 1 :])
            return " ".join(body)
    raise AssertionError(f"no per-unit contract line for {unit}")


class ExtractionDocTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = read(EXTRACTION_MD)
        cls.config = paths.load_config()

    def test_limits_table_matches_config(self) -> None:
        rows = {key: int(value) for key, value in LIMIT_ROW.findall(self.text)}
        self.assertEqual(
            set(rows),
            {
                "max_candidates_per_run", "max_triggers_per_candidate", "bad_case_max_words", "fact_max_words",
                "trigger_min_chars", "extraction_gate_max_failures", "finish_extraction_max_attempts",
                "inject_max_entries",
            },
        )
        for key, value in rows.items():
            self.assertEqual(value, self.config[key], key)

    def test_declaration_fields_and_schema_ids_are_named(self) -> None:
        # Schema ids and required-field names come from proposals.py itself, not a third literal
        # copy: renaming a field or bumping a schema id there must turn this test red.
        for token in (
            proposals.DECLARATION_SCHEMA, proposals.PROPOSAL_SCHEMA,
            *(f for f in proposals.DECLARATION_REQUIRED if f != "schema"),
            "run_digest.json", "extraction_declaration.json", "artifacts/candidates/", "artifacts/memory_evidence/",
            "proposal.json", "proposal.md",
        ):
            self.assertIn(token, self.text, token)

    def test_proposal_vocabulary_matches_proposals_py(self) -> None:
        # Same reasoning for proposal.json's own required fields and its enums, but bounded: a bare
        # assertIn("op", text) is satisfied by "proposal.json" / "proposal.md" and would stay green
        # even if the `op` bullet were deleted, so every token is checked backticked (how the
        # document actually writes them) except the one that is never backtick-wrapped on its own
        # ("cell", only ever seen as `cell.component` / `cell.cause` / the bare JSON key), which
        # gets a word-boundary regex instead.
        not_backticked = {"cell"}
        for token in (
            *(f for f in proposals.PROPOSAL_REQUIRED if f != "schema"),
            *proposals.ENTRY_TYPES, *proposals.OPS, *proposals.CLAIM_KINDS,
        ):
            if token in not_backticked:
                self.assertRegex(self.text, rf"\b{re.escape(token)}\b", token)
            else:
                self.assertIn(f"`{token}`", self.text, token)

    def test_enums_are_listed(self) -> None:
        for value in self.config["cause_enum"] + self.config["target_skills"]:
            self.assertIn(f"`{value}`", self.text, value)
        for value in self.config["fact_scopes"]:
            self.assertIn(f"`{value}", self.text, value)  # written as `cluster`, `model_family:<name>`, `dataset:<name>`

    def test_stopwords_are_listed(self) -> None:
        for word in self.config["trigger_stopwords"]:
            self.assertIn(f"`{word}`", self.text, word)

    def test_bad_case_sections_and_banned_header_lines_are_listed(self) -> None:
        # Section list and provenance prefixes come from proposals.py, not a restated tuple:
        # editing BAD_CASE_REQUIRED_SECTIONS or PROVENANCE_PREFIXES there must turn this test red.
        for name in proposals.BAD_CASE_REQUIRED_SECTIONS + proposals.BAD_CASE_OPTIONAL_SECTIONS:
            self.assertIn(f"## {name}", self.text, name)
        for header in proposals.PROVENANCE_PREFIXES:
            self.assertIn(f"`{header}`", self.text, header)

    def test_gate_section_has_ten_numbered_checks(self) -> None:
        gate = section(self.text, "## 8. ")
        numbers = [int(n) for n in re.findall(r"^(\d+)\. ", gate, flags=re.M)]
        self.assertEqual(numbers, list(range(1, 11)))

    def test_key_instructions_are_present(self) -> None:
        for phrase in (
            "no_new_lessons: true", "--out", "run_digest.preview.json", "write tool", "heredoc", "sha256sum",
            "`re:`", "Do not rebuild", "sure_update_state", "sure_finish",
            # evidence-only triggers never drive hook injection; the publisher computes hook_trigger from the digest
            "`hook_trigger`", "not used for hook injection",
        ):
            self.assertIn(phrase, self.text, phrase)

    def test_output_dir_is_never_mentioned(self) -> None:
        self.assertNotIn("output_dir", self.text)


class SkillDocTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.units = paths.load_units()["skills"]
        cls.onboard = read(ONBOARD_SKILL)
        cls.infer = read(INFER_SKILL)
        cls.eval = read(EVAL_SKILL)
        cls.trans = read(TRANS_SKILL)
        cls.feed = read(FEED_SKILL)

    def test_onboard_unit_table_matches_units_json(self) -> None:
        rows = unit_table(self.onboard)
        self.assertEqual([r[0] for r in rows], list(range(1, len(self.units["sure_onboard"]) + 1)))
        self.assertEqual([r[1] for r in rows], self.units["sure_onboard"])

    def test_infer_unit_table_matches_units_json(self) -> None:
        rows = unit_table(self.infer)
        self.assertEqual([r[0] for r in rows], list(range(1, len(self.units["sure_infer"]) + 1)))
        self.assertEqual([r[1] for r in rows], self.units["sure_infer"])

    def test_eval_unit_table_matches_units_json(self) -> None:
        rows = unit_table(self.eval)
        self.assertEqual([r[0] for r in rows], list(range(1, len(self.units["sure_eval"]) + 1)))
        self.assertEqual([r[1] for r in rows], self.units["sure_eval"])

    def test_trans_unit_table_matches_units_json(self) -> None:
        rows = unit_table(self.trans)
        self.assertEqual([r[0] for r in rows], list(range(1, len(self.units["sure_trans"]) + 1)))
        self.assertEqual([r[1] for r in rows], self.units["sure_trans"])

    def test_feed_unit_table_matches_units_json(self) -> None:
        rows = unit_table(self.feed)
        self.assertEqual([r[0] for r in rows], list(range(1, len(self.units["sure_feed"]) + 1)))
        self.assertEqual([r[1] for r in rows], self.units["sure_feed"])

    def test_extract_lessons_rows(self) -> None:
        for text in (self.onboard, self.infer, self.eval, self.trans, self.feed):
            row = next(r for r in unit_table(text) if r[1] == "extract_lessons")
            self.assertEqual(row[2], "**gate**")
            self.assertEqual(row[3], "`extraction_declaration.json`")
            self.assertEqual(row[4], "`scripts/check_memory_extraction.py`")

    def test_extraction_skills_point_to_the_contract_and_the_context_file(self) -> None:
        for text in (self.onboard, self.infer, self.eval, self.trans, self.feed):
            for token in (
                "sure/runtime/memory/EXTRACTION.md", "artifacts/memory_context.json", "sure/memory/index.md",
                "references/memory/ROUTING.md",
            ):
                self.assertIn(token, text, token)
            line = contract_line(text, "extract_lessons")
            self.assertIn("EXTRACTION.md", line)
            self.assertIn("build_run_digest.py", line)
            self.assertIn("no_new_lessons: true", line)

    def test_inject_header_in_skill_docs_matches_config(self) -> None:
        header = paths.load_config()["inject_header"]
        for text in (self.onboard, self.infer, self.eval, self.trans, self.feed):
            self.assertIn(header, text)

    def test_onboard_context_selection_line(self) -> None:
        line = contract_line(self.onboard, "context_selection")
        self.assertIn("memory_context.json", line)
        self.assertIn("selected_references.memory", line)

    def test_infer_dataset_scope_line(self) -> None:
        line = contract_line(self.infer, "dataset_scope")
        self.assertIn("memory_context.json", line)
        self.assertIn("do not add", line)

    def test_memory_context_shape_is_quoted(self) -> None:
        # The shape Task 12's preStartMemory writes (skeleton 1.13). The agent reads the file by this
        # description alone, so both contract lines quote every key and say the file exists when empty.
        for text, unit in (
            (self.onboard, "context_selection"), (self.infer, "dataset_scope"), (self.feed, "scan_modelscope"),
        ):
            line = contract_line(text, unit)
            for token in (
                "sure.memory.context.v1", "skill", "target_id", "facts", "entry_id", "title", "path",
                "scope", "checked_at", "stale", "status", "omitted_provisional", "facts: []",
            ):
                self.assertIn(token, line, f"{unit}: {token}")


class RoutingDocTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = json.loads(read(CONTEXT_SELECTION_SCHEMA))

    def assert_context_selection_shape(self, block: dict, where: str) -> None:
        # Hand-rolled check of the two additionalProperties:false levels of context_selection.schema.json.
        top = self.schema["properties"]
        self.assertTrue(set(block) <= set(top), f"{where}: unknown top-level keys {sorted(set(block) - set(top))}")
        self.assertTrue(set(self.schema["required"]) <= set(block), f"{where}: missing required top-level keys")
        selected = block["selected_references"]
        allowed = set(top["selected_references"]["properties"])
        self.assertTrue(set(selected) <= allowed, f"{where}: unknown selected_references keys {sorted(set(selected) - allowed)}")
        self.assertTrue(set(top["selected_references"]["required"]) <= set(selected), f"{where}: missing required selected_references keys")
        self.assertIsInstance(selected.get("memory"), list, where)
        self.assertTrue(selected["memory"] and all(isinstance(x, str) for x in selected["memory"]), where)

    def test_onboard_routing_points_to_the_merged_index(self) -> None:
        text = read(ONBOARD_ROUTING)
        row = next(line for line in text.splitlines() if line.startswith("| Error resembles a known bad case"))
        self.assertIn("sure/memory/index.md", row)
        self.assertIn("artifacts/memory_context.json", text)
        self.assertIn("selected_references.memory", text)

    def test_onboard_examples_are_schema_shaped(self) -> None:
        for path in (ONBOARD_ROUTING, ONBOARD_COMMON):
            blocks = JSON_BLOCK.findall(read(path))
            self.assertEqual(len(blocks), 1, path.name)
            self.assert_context_selection_shape(json.loads(blocks[0]), path.name)

    def test_legacy_audit_keys_are_gone(self) -> None:
        for path in (ONBOARD_ROUTING, ONBOARD_COMMON):
            text = read(path)
            for key in ("memory_files_read", "memory_files_skipped"):
                self.assertNotIn(key, text, f"{path.name} still has {key}")

    def test_infer_routing_exists_and_routes_through_the_index(self) -> None:
        text = read(INFER_ROUTING)
        for token in (
            "sure/memory/index.md", "artifacts/memory_context.json", "failure_taxonomy.md",
            "retry_and_escalation.md", "memory/bad_cases/README.md", "dataset_decision.json",
        ):
            self.assertIn(token, text, token)

    def test_trans_routing_exists_and_routes_through_the_index(self) -> None:
        text = read(TRANS_ROUTING)
        for token in (
            "sure/memory/index.md", "artifacts/memory_context.json", "references/image_packaging.md",
            "memory/bad_cases/README.md", "Failure Rules",
        ):
            self.assertIn(token, text)

    def test_feed_routing_exists_and_routes_through_the_index(self) -> None:
        text = read(FEED_ROUTING)
        for token in (
            "sure/memory/index.md", "artifacts/memory_context.json", "references/agents.md",
            "memory/bad_cases/README.md", "scan_result.json",
        ):
            self.assertIn(token, text, token)

    def test_bad_cases_readmes_share_the_route_table_header(self) -> None:
        onboard_lines = read(ONBOARD_BAD_CASES_README).splitlines()
        o = onboard_lines[onboard_lines.index("## Route Table") :]
        for readme in (INFER_BAD_CASES_README, TRANS_BAD_CASES_README, FEED_BAD_CASES_README):
            with self.subTest(skill=readme.parents[3].name):
                text = read(readme)
                lines = text.splitlines()
                rows = lines[lines.index("## Route Table") :]
                self.assertEqual(rows[:4], o[:4])  # heading, blank line, header row, separator row
                # Rows added later by `cli export` must point at files that exist next to the README.
                for row in (line for line in rows[4:] if line.startswith("|")):
                    name = re.search(r"\| `([^`]+\.md)` \|", row)
                    self.assertIsNotNone(name, row)
                    self.assertTrue((readme.parent / name.group(1)).exists(), row)
                self.assertIn("cli.py export", text)

    def test_shared_facts_readme(self) -> None:
        text = read(SHARED_FACTS_README)
        self.assertFalse((SKILLS / "_shared" / "sure.skill.json").exists())
        for token in ("Scope:", "Checked-at:", "Evidence:", "cli.py export", "sure/memory/index.md", "sure.skill.json"):
            self.assertIn(token, text, token)


if __name__ == "__main__":
    unittest.main()
