# sure/runtime/memory/test_proposals.py
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tracemalloc
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # sure/runtime

from memory import digest, paths, proposals  # noqa: E402

RUN_ID = "run-20260818-abc123"
TARGET_ID = "qwen2-audio-7b"
REPAIR_TEXT = (
    "Gate script scripts/check_env_build.py exited 1. build_env_result.json for qwen2-audio-7b says success=false. "
    "RuntimeError: CUDA error: no kernel image is available for execution on the device "
    "(torch 2.4.0+cu118 on sm_86)."
)
LOG_LINES = [
    "Collecting torch==2.4.0+cu118",
    "ERROR: No matching distribution found for torch==2.4.0+cu118",
    "error: subprocess-exited-with-error",
]
EVIDENCE_TXT = "[Partition]\nsite-gpu-data   idle   8 nodes\nsite-gpu        busy   4 nodes\n"


# --- fixture builders ----------------------------------------------------------

def make_digest(run_id: str = RUN_ID, *, error: str | None = None) -> dict:
    """A run_digest.json shaped like spec 4.3 (build_env failed once, then passed)."""
    if error is not None:
        return {"schema": proposals.DIGEST_SCHEMA, "error": error}
    onboard_units = paths.load_units()["skills"]["sure_onboard"]
    return {
        "schema": proposals.DIGEST_SCHEMA,
        "run": {
            "run_id": run_id,
            "skill": "sure_onboard",
            "args": "model_input_path=sure/models/qwen2-audio-7b/model_input.yaml",
            "target": {"kind": "model", "id": TARGET_ID},
            "status_so_far": "running",
            "cutoff": 812,
            "memory_usage": [],
        },
        "units": [
            {"id": "load_model_input", "outcome": "passed", "attempts": 1, "repairs": [], "fix_window": [],
             "last_commands": [], "log_tail": None},
            {"id": "build_env", "outcome": "passed", "attempts": 2,
             "repairs": [{"attempt": 1, "text": REPAIR_TEXT}],
             "fix_window": [{"tool": "bash", "command": "uv pip install torch==2.4.0 --index-url https://download.pytorch.org/whl/cu121"}],
             "last_commands": [],
             "log_tail": {"path": "{run_dir}/artifacts/build_env.log", "lines": list(LOG_LINES)}},
            {"id": "fetch_weights", "outcome": "current", "attempts": 1, "repairs": [], "fix_window": [],
             "last_commands": [], "log_tail": None},
        ],
        "tool_errors": 1,
        "prior_runs": [],
        "memory_index_snapshot": [],
        "units_registry": {"sure_onboard": onboard_units},
    }


def _fixture_digest_sha() -> str:
    """sha256 of run_digest.json exactly as GateFixture.write_json writes it (text mode, so newline translation
    is included). Part B's rule 9 needs checkpoint sha == disk sha == source.digest_sha256 for the default fixture."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "run_digest.json"
        path.write_text(json.dumps(make_digest(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return paths.sha256_file(path)


SHA = _fixture_digest_sha()


def bad_case_md(title: str = "CUDA arch mismatch: cu118 torch wheel on an sm_86 node", *, extra_words: int = 0) -> str:
    padding = ("word " * extra_words).strip()
    return f"""# {title}

## Trigger

`no kernel image is available for execution on the device` right after `import torch`
in build_env or validate_import.

## Affected Step

sure_onboard / build_env

## Minimum Evidence

- artifacts/build_env.log:2 (the pip resolve line shows the cu118 wheel)
- the build_env gate repair text of attempt 1

## Known Mitigation

Reinstall torch from the cu121 index that matches the cluster driver. {padding}

```bash
uv pip install torch==2.4.0 --index-url https://download.pytorch.org/whl/cu121
```

## Verification

```bash
python -c "import torch; print(torch.cuda.get_arch_list())"
```

The printed list must contain sm_86.
"""


def fact_md(title: str = "The 3090 data queue submits as site-gpu-data, not 3090-data", *,
            scope: str = "cluster", checked_at: str = "2026-08-18",
            evidence: str = "artifacts/memory_evidence/1.txt", notes: str | None = None) -> str:
    body = notes if notes is not None else (
        "vc info -u lists the partition as site-gpu-data; the shorthand 3090-data is rejected by vc submit."
    )
    return f"# {title}\n\nScope: {scope}\nChecked-at: {checked_at}\nEvidence: {evidence}\n\n{body}\n"


def bad_case_proposal(**overrides) -> dict:
    p = {
        "schema": proposals.PROPOSAL_SCHEMA,
        "type": "bad_case",
        "op": "add",
        "target_skill": "sure_onboard",
        "target_entry": None,
        "applies_to": ["sure_onboard"],
        "cell": {"component": "build_env", "cause": "cuda_version_mismatch"},
        "trigger": ["no kernel image is available"],
        "causal": True,
        "evidence": ["artifacts/build_env.log:2"],
        "claims": [{"kind": "gate_repair", "unit": "build_env", "attempt": 1, "status": "failed"}],
        "source": {"run_id": RUN_ID, "skill": "sure_onboard", "target": TARGET_ID, "digest_sha256": SHA},
        "similar": None,
        "scope": None,
        "checked_at": None,
    }
    p.update(overrides)
    return p


def fact_proposal(**overrides) -> dict:
    p = {
        "schema": proposals.PROPOSAL_SCHEMA,
        "type": "fact",
        "op": "add",
        "target_skill": "_shared",
        "target_entry": None,
        "applies_to": ["sure_onboard", "sure_eval"],
        "cell": {"component": "_", "cause": "n.a."},
        "trigger": ["site-gpu-data"],
        "causal": False,
        "evidence": ["artifacts/memory_evidence/1.txt"],
        "claims": [],
        "source": {"run_id": RUN_ID, "skill": "sure_onboard", "target": TARGET_ID, "digest_sha256": SHA},
        "similar": None,
        "scope": "cluster",
        "checked_at": "2026-08-18",
    }
    p.update(overrides)
    return p


def declaration(**overrides) -> dict:
    d = {
        "schema": proposals.DECLARATION_SCHEMA,
        "no_new_lessons": False,
        "no_lessons_reason": None,
        "covered_by": [],
        "candidates": [],
        "infra_noise": False,
        "infra_evidence": [],
    }
    d.update(overrides)
    return d


class GateFixture:
    """Fake .sure/runs/<run_id>/ plus the real config / units files."""

    def __init__(self, tmp: Path, run_id: str = RUN_ID) -> None:
        self.repo_root = tmp / "repo"
        self.run_dir = self.repo_root / ".sure" / "runs" / run_id
        self.artifacts = self.run_dir / "artifacts"
        (self.artifacts / "candidates").mkdir(parents=True)
        (self.artifacts / "memory_evidence").mkdir()
        self.config = paths.load_config()
        self.units = paths.load_units()
        self.candidate_ids: list[str] = []
        self.write_digest(make_digest(run_id))
        self.write_text("artifacts/build_env.log", "\n".join(LOG_LINES) + "\n")
        self.write_text("artifacts/memory_evidence/1.txt", EVIDENCE_TXT)

    def write_text(self, rel: str, text: str) -> Path:
        path = self.run_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_json(self, rel: str, obj) -> Path:
        return self.write_text(rel, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")

    def write_digest(self, digest: dict) -> None:
        self.write_json("artifacts/run_digest.json", digest)

    def write_events(self, events: list[dict]) -> None:
        """events.jsonl exactly as the harness appends it: one newline-terminated JSON object per line."""
        self.write_text("events.jsonl", "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events))

    def add_candidate(self, cid: str, proposal: dict, md: str) -> None:
        self.write_json(f"artifacts/candidates/{cid}/proposal.json", proposal)
        self.write_text(f"artifacts/candidates/{cid}/proposal.md", md)
        self.candidate_ids.append(cid)

    def write_declaration(self, **overrides) -> None:
        d = declaration(candidates=list(self.candidate_ids), **overrides)
        self.write_json("artifacts/extraction_declaration.json", d)

    def run(self, *, index: dict | None = None, sha: str | None = SHA) -> list[proposals.GateFailure]:
        return proposals.check_extraction(
            self.run_dir, self.repo_root, config=self.config, units=self.units, index=index,
            checkpoint_digest_sha=sha,
        )

    def context(self) -> proposals.GateContext:
        decl = json.loads((self.artifacts / "extraction_declaration.json").read_text(encoding="utf-8"))
        return proposals.build_context(
            self.run_dir, self.repo_root, config=self.config, units=self.units, index=None,
            checkpoint_digest_sha=SHA, declaration=decl,
        )


class GateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.fx = GateFixture(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def assertFailure(self, failures, rule: int, needle: str) -> None:
        hits = [f for f in failures if f.rule == rule and needle in f.message]
        dump = "\n".join(f"[{f.rule}] {f.message}" for f in failures) or "(no failures)"
        self.assertTrue(hits, f"no rule {rule} failure containing {needle!r}; got:\n{dump}")

    def assertNoRule(self, failures, rule: int) -> None:
        hits = [f for f in failures if f.rule == rule]
        dump = "\n".join(f"[{f.rule}] {f.message}" for f in hits)
        self.assertFalse(hits, f"unexpected rule {rule} failure(s):\n{dump}")

    def assertClean(self, failures) -> None:
        dump = "\n".join(f"[{f.rule}] {f.message}" for f in failures)
        self.assertEqual(failures, [], f"expected no failures, got:\n{dump}")


# --- trigger predicate (shared with match.ts) ------------------------------------

class TriggerHitsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vectors = json.loads((paths.LIB_DIR / "fixtures" / "match_vectors.json").read_text(encoding="utf-8"))

    def test_vectors_file_shape(self) -> None:
        self.assertEqual(self.vectors["schema"], "sure.memory.match_vectors.v1")
        rows = self.vectors["vectors"]
        self.assertGreaterEqual(len(rows), 12)
        names = [r["name"] for r in rows]
        self.assertEqual(len(names), len(set(names)))
        for row in rows:
            self.assertEqual(set(row), {"name", "trigger", "text", "hit"}, row)
            self.assertIsInstance(row["hit"], bool)
        for needed in ("trigger-upper-text-lower", "inner-double-space-not-folded",
                       "substring-inside-longer-word", "empty-trigger-never-hits", "unicode-cjk-substring"):
            self.assertIn(needed, names)

    def test_every_vector(self) -> None:
        for row in self.vectors["vectors"]:
            with self.subTest(name=row["name"]):
                self.assertEqual(proposals.trigger_hits(row["trigger"], row["text"]), row["hit"])

    def test_predicate_is_plain_lowercased_substring(self) -> None:
        self.assertTrue(proposals.trigger_hits("No Kernel", "cuda: no kernel image"))
        self.assertFalse(proposals.trigger_hits("no  kernel", "cuda: no kernel image"))
        self.assertFalse(proposals.trigger_hits("", "anything"))
        self.assertTrue(proposals.observed_in("kernel image", ["nothing", "no kernel image here"]))
        self.assertFalse(proposals.observed_in("kernel image", []))


# --- field hygiene, word count, provenance -------------------------------------

class InterpolationTests(unittest.TestCase):
    def test_accepts_plain_and_unicode_text(self) -> None:
        self.assertIsNone(proposals.interpolation_problem("no kernel image is available"))
        self.assertIsNone(proposals.interpolation_problem("无法初始化 NVML"))
        self.assertIsNone(proposals.interpolation_problem("tp_plan='auto' (device_map)"))

    def test_rejects_pipe_semicolon_and_nonprintable(self) -> None:
        self.assertIn("'|'", proposals.interpolation_problem("a | b"))
        self.assertIn("';'", proposals.interpolation_problem("a; b"))
        self.assertIn("U+2028", proposals.interpolation_problem("a\u2028b"))
        self.assertIn("U+000A", proposals.interpolation_problem("a\nb"))


class BodyWordsTests(unittest.TestCase):
    def test_count_excludes_headings_fences_and_provenance_lines(self) -> None:
        md = "# Title words ignored\n\n## Section\n\none two three\n\n```bash\necho not counted\n```\n\nTrigger: not counted either\nfour five\n"
        self.assertEqual(proposals.count_body_words(md), 5)

    def test_provenance_line_detected_and_fence_exempt(self) -> None:
        self.assertEqual(proposals.provenance_line_in_body("# T\n\nStatus: confirmed\n"), "Status: confirmed")
        self.assertEqual(proposals.provenance_line_in_body("# T\n\n  Superseded-by: x\n"), "Superseded-by: x")
        self.assertIsNone(proposals.provenance_line_in_body("# T\n\n```\nTrigger: inside fence\n```\n"))
        self.assertIsNone(proposals.provenance_line_in_body("# T\n\nScope: cluster\nChecked-at: 2026-08-18\n"))


class ParseBadCaseTests(unittest.TestCase):
    def test_valid_body(self) -> None:
        body = proposals.parse_bad_case(bad_case_md())
        self.assertEqual(body.errors, [])
        self.assertEqual(body.title, "CUDA arch mismatch: cu118 torch wheel on an sm_86 node")
        self.assertEqual(set(body.sections), set(proposals.BAD_CASE_REQUIRED_SECTIONS))
        self.assertIn("build_env", body.sections["Affected Step"])
        self.assertIn("```bash", body.sections["Known Mitigation"])
        self.assertLess(body.word_count, 100)

    def test_missing_required_section(self) -> None:
        md = bad_case_md().replace("## Verification", "## Notes")
        body = proposals.parse_bad_case(md)
        self.assertTrue(any("missing section '## Verification'" in e for e in body.errors), body.errors)
        self.assertTrue(any("unknown section '## Notes'" in e for e in body.errors), body.errors)

    def test_duplicate_and_empty_sections(self) -> None:
        md = bad_case_md() + "\n## Trigger\n\nagain\n\n## Example Artifacts\n\n"
        body = proposals.parse_bad_case(md)
        self.assertTrue(any("duplicate section '## Trigger'" in e for e in body.errors), body.errors)
        md2 = bad_case_md().replace("sure_onboard / build_env\n", "\n")
        body2 = proposals.parse_bad_case(md2)
        self.assertTrue(any("section '## Affected Step' is empty" in e for e in body2.errors), body2.errors)

    def test_missing_h1_and_preamble(self) -> None:
        body = proposals.parse_bad_case("## Trigger\n\nx\n")
        self.assertTrue(any("must start with an H1" in e for e in body.errors), body.errors)
        self.assertTrue(any("missing H1" in e for e in body.errors), body.errors)
        body2 = proposals.parse_bad_case("intro text\n" + bad_case_md())
        self.assertTrue(any("must start with an H1" in e for e in body2.errors), body2.errors)
        self.assertEqual(body2.title, "CUDA arch mismatch: cu118 torch wheel on an sm_86 node")

    def test_text_between_h1_and_first_section_is_an_error(self) -> None:
        # An orphan line here is invisible to every section check yet ships verbatim in the
        # published entry, and index.py reads a body carrying `Scope:` back as a fact.
        md = bad_case_md().replace("\n## Trigger", "\nScope: cluster\n\n## Trigger", 1)
        body = proposals.parse_bad_case(md)
        self.assertTrue(any("between the H1 title and the first section" in e for e in body.errors), body.errors)

    def test_fenced_text_between_h1_and_first_section_is_an_error(self) -> None:
        md = bad_case_md().replace("\n## Trigger", "\n```text\nScope: cluster\n```\n\n## Trigger", 1)
        body = proposals.parse_bad_case(md)
        self.assertTrue(any("between the H1 title and the first section" in e for e in body.errors), body.errors)

    def test_provenance_line_in_body_is_an_error(self) -> None:
        md = bad_case_md().replace("sure_onboard / build_env", "Cell: sure_onboard/build_env x infra")
        body = proposals.parse_bad_case(md)
        self.assertTrue(any("provenance prefix" in e for e in body.errors), body.errors)

    def test_sections_are_built_in_linear_time(self) -> None:
        """A candidate that pastes a job log into a fenced block used to grow each section with
        `+=` per line, which CPython cannot do in place for a dict value: 9.1 MB took over two
        minutes and the gate timed out. Comparing two sizes keeps the check machine-independent --
        four times the input costs about four times the time when the parse is linear, sixteen
        when it is quadratic."""
        def timed(megabytes: float) -> float:
            filler = "\n".join(f"[{i:06d}] pip install line with some text to pad it out"
                               for i in range(int(megabytes * 1024 * 1024 / 55)))
            md = bad_case_md() + "\n## Example Artifacts\n\n```text\n" + filler + "\n```\n"
            start = time.perf_counter()
            body = proposals.parse_bad_case(md)
            elapsed = time.perf_counter() - start
            self.assertEqual(body.errors, [])
            self.assertIn("[000000] pip install line", body.sections["Example Artifacts"])
            return elapsed

        small = timed(2)
        big = timed(8)
        self.assertLess(big, small * 10, f"2 MB took {small:.3f}s, 8 MB took {big:.3f}s")

    def test_fence_content_ignored_for_headings_and_words(self) -> None:
        md = bad_case_md().replace(
            "```bash\nuv pip install",
            "```bash\n# not a title\n## Not A Section\nTrigger: not provenance\nuv pip install",
        )
        body = proposals.parse_bad_case(md)
        self.assertEqual(body.errors, [])
        self.assertEqual(body.word_count, proposals.parse_bad_case(bad_case_md()).word_count)


class ParseFactTests(unittest.TestCase):
    def test_valid_fact(self) -> None:
        body = proposals.parse_fact(fact_md())
        self.assertEqual(body.errors, [])
        self.assertEqual(body.title, "The 3090 data queue submits as site-gpu-data, not 3090-data")
        self.assertEqual(body.sections["Scope"], "cluster")
        self.assertEqual(body.sections["Checked-at"], "2026-08-18")
        self.assertEqual(body.sections["Evidence"], "artifacts/memory_evidence/1.txt")
        self.assertTrue(body.sections["Notes"].startswith("vc info -u lists"))
        self.assertEqual(body.word_count, 16)

    def test_missing_header_lines(self) -> None:
        body = proposals.parse_fact("# A fact\n\nSome notes only.\n")
        for key in proposals.FACT_HEADER_KEYS:
            self.assertTrue(any(f"missing '{key}:' line" in e for e in body.errors), body.errors)
        body2 = proposals.parse_fact("# A fact\n\nScope:\nChecked-at: 2026-08-18\nEvidence: a.txt\n")
        self.assertTrue(any("'Scope:' line is empty" in e for e in body2.errors), body2.errors)

    def test_word_count_excludes_headers_and_fences(self) -> None:
        md = fact_md(notes="one two\n\n```\nnot counted at all\n```\n\nthree")
        body = proposals.parse_fact(md)
        self.assertEqual(body.errors, [])
        self.assertEqual(body.word_count, 3)

    def test_duplicate_scope_line_and_missing_h1(self) -> None:
        body = proposals.parse_fact(fact_md() + "Scope: dataset:x\n")
        self.assertTrue(any("duplicate 'Scope:' line" in e for e in body.errors), body.errors)
        body2 = proposals.parse_fact("Scope: cluster\nChecked-at: 2026-08-18\nEvidence: a\n")
        self.assertTrue(any("missing H1" in e for e in body2.errors), body2.errors)


# --- trigger discipline helpers ----------------------------------------------------

class TriggerDisciplineUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = paths.load_config()

    def test_min_chars(self) -> None:
        self.assertIn("shorter than", proposals.trigger_problem("  short ", self.cfg))
        self.assertIsNone(proposals.trigger_problem("no kernel image", self.cfg))

    def test_stopword_is_case_insensitive_and_exact(self) -> None:
        self.assertIn("stop word", proposals.trigger_problem("Not Found", self.cfg))
        self.assertIn("stop word", proposals.trigger_problem("  exception  ", self.cfg))
        self.assertIsNone(proposals.trigger_problem("partition not found", self.cfg))

    def test_template_phrases_alone_are_rejected(self) -> None:
        for phrase in self.cfg["trigger_template_phrases"]:
            with self.subTest(phrase=phrase):
                self.assertIn("template", proposals.trigger_problem(phrase, self.cfg))
        self.assertIn("template", proposals.trigger_problem("Blocked because: Full expected shape:", self.cfg))

    def test_template_phrase_plus_real_content_is_accepted(self) -> None:
        self.assertIsNone(proposals.trigger_problem("Blocked because: gate script exited 1", self.cfg))
        self.assertEqual(proposals.strip_template_phrases("Gate script scripts/x.py exited", ["Gate script scripts/"]),
                         "x.py exited")

    def test_regex_prefix_rejected(self) -> None:
        self.assertIn("re:", proposals.trigger_problem("re:kernel.*image", self.cfg))
        self.assertIn("re:", proposals.trigger_problem("RE: something long", self.cfg))

    def test_generic_trigger_rejects_run_specific_strings(self) -> None:
        kw = {"run_id": RUN_ID, "target_id": TARGET_ID}
        self.assertFalse(proposals.is_generic_trigger(f"{RUN_ID} failed", **kw))
        self.assertFalse(proposals.is_generic_trigger("Qwen2-Audio-7B tokenizer", **kw))
        self.assertFalse(proposals.is_generic_trigger(".sure/runs/x/artifacts/a.log", **kw))
        self.assertFalse(proposals.is_generic_trigger("123456789", **kw))
        self.assertFalse(proposals.is_generic_trigger("dead-beef-cafe", **kw))
        self.assertFalse(proposals.is_generic_trigger("at 2026-08-18T12:00:03Z the job died", **kw))
        self.assertFalse(proposals.is_generic_trigger("!!! ... ---", **kw))
        self.assertFalse(proposals.is_generic_trigger("   ", **kw))

    def test_generic_trigger_accepts_error_strings(self) -> None:
        kw = {"run_id": RUN_ID, "target_id": TARGET_ID}
        self.assertTrue(proposals.is_generic_trigger("no kernel image is available", **kw))
        self.assertTrue(proposals.is_generic_trigger("partition not found: 3090-data", **kw))
        self.assertTrue(proposals.is_generic_trigger("no kernel image", run_id="", target_id=""))

    def test_digest_texts_collects_repairs_and_log_tail(self) -> None:
        texts = proposals.digest_texts(make_digest())
        self.assertEqual(texts, [REPAIR_TEXT, *LOG_LINES])
        self.assertEqual(proposals.digest_texts(None), [])
        self.assertEqual(proposals.digest_texts(make_digest(error="events unreadable")), [])
        self.assertEqual(proposals.digest_texts({"units": "nonsense"}), [])


# --- evidence helpers (run root only in part A) --------------------------------------

class EvidenceHelperTests(GateTestCase):
    def test_parse_evidence_ref(self) -> None:
        self.assertEqual(proposals.parse_evidence_ref("artifacts/a.log:12"), ("artifacts/a.log", 12))
        self.assertEqual(proposals.parse_evidence_ref("artifacts/a.log"), ("artifacts/a.log", None))
        self.assertEqual(proposals.parse_evidence_ref("artifacts/a.log:0"), ("artifacts/a.log:0", None))
        self.assertEqual(proposals.parse_evidence_ref("C:\\x\\a.log"), ("C:\\x\\a.log", None))

    def test_unsafe_paths_and_single_names(self) -> None:
        for bad in ("/etc/passwd", "C:\\x", "C:x", "\\\\srv\\share", "../x", "a/../b"):
            with self.subTest(bad=bad):
                self.assertTrue(proposals.is_unsafe_evidence_path(bad))
        self.assertFalse(proposals.is_unsafe_evidence_path("artifacts/build_env.log"))
        self.assertTrue(proposals.is_single_name("01-cuda-arch-mismatch"))
        for bad in ("", ".", "..", "a/b", "a\\b", "C:", "name.", "name "):
            with self.subTest(bad=bad):
                self.assertFalse(proposals.is_single_name(bad))

    def test_resolve_evidence_stays_inside_run_dir(self) -> None:
        self.fx.write_declaration()
        ctx = self.fx.context()
        self.assertEqual(proposals.evidence_bases(ctx), [(self.fx.run_dir, "resolve")])
        found = proposals.resolve_evidence(ctx, "artifacts/build_env.log")
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "build_env.log")
        self.assertIsNone(proposals.resolve_evidence(ctx, "artifacts/nope.log"))
        self.assertIsNone(proposals.resolve_evidence(ctx, "../other/x.log"))
        outside = self.fx.repo_root / "outside.txt"
        outside.write_text("x", encoding="utf-8")
        self.assertIsNone(proposals.resolve_evidence(ctx, str(outside)))
        self.assertIsNone(proposals.resolve_evidence(ctx, ""))
        self.assertIsNone(proposals.resolve_evidence(ctx, "artifacts"))  # a directory is not evidence

    def test_evidence_problem_checks_existence_and_line_range(self) -> None:
        self.fx.write_declaration()
        ctx = self.fx.context()
        self.assertIsNone(proposals.evidence_problem(ctx, "artifacts/build_env.log"))
        self.assertIsNone(proposals.evidence_problem(ctx, "artifacts/build_env.log:3"))
        self.assertIn("beyond the end", proposals.evidence_problem(ctx, "artifacts/build_env.log:4"))
        self.assertIn("does not resolve", proposals.evidence_problem(ctx, "artifacts/missing.log:1"))
        self.assertIn("absolute", proposals.evidence_problem(ctx, "/tmp/x.log"))
        self.assertIn("non-empty string", proposals.evidence_problem(ctx, "   "))
        self.assertIn("non-empty string", proposals.evidence_problem(ctx, 12))


class EvidenceStreamingTests(GateTestCase):
    """The gate runs on a shared login node next to multi-GB job logs, so no evidence read may
    hold the file in memory. Peaks are measured with tracemalloc against the file size."""

    MB = 1024 * 1024
    SIZE_MB = 8
    LINE = b"[2026-08-18 12:00:00] step 1234 loss 0.123 lr 1e-4 samples/s 12.3\n"

    def big_log(self, rel: str = "vc_logs/job.log", tail: bytes = b"") -> int:
        path = self.fx.run_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        block = self.LINE * (self.MB // len(self.LINE))
        with path.open("wb") as handle:
            for _ in range(self.SIZE_MB):
                handle.write(block)
            handle.write(tail)
        return path.stat().st_size

    def progress_log(self, rel: str = "vc_logs/shards.log", frames: int = 300) -> Path:
        """A shard-loading log the shape vc_logs/ actually holds: a header line, `frames` tqdm
        redraws that end in a bare \\r, then the traceback lines. Written as bytes -- write_text
        would translate the \\n on Windows and change the very thing under test."""
        redraws = "".join(
            "Loading checkpoint shards: {:3d}%|{}| {}/{} [00:{:02d}<00:00, 9.87it/s]\r".format(
                i * 100 // frames, ("#" * (i * 10 // frames)).ljust(10), i, frames, i // 10)
            for i in range(1, frames + 1))
        text = ("[2026-08-18 12:00:00] loading model qwen2-audio-7b\n"
                + redraws
                + "\n[2026-08-18 12:00:31] RuntimeError: CUDA error: no kernel image is available\n")
        path = self.fx.run_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path

    @staticmethod
    def peak_of(call) -> tuple[object, int]:
        tracemalloc.start()
        try:
            result = call()
            return result, tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()

    def test_line_reference_check_never_materialises_the_file(self) -> None:
        size = self.big_log()
        self.fx.write_declaration()
        ctx = self.fx.context()
        problem, peak = self.peak_of(lambda: proposals.evidence_problem(ctx, "vc_logs/job.log:212"))
        self.assertIsNone(problem)
        self.assertLess(peak, 2 * self.MB, f"peak {peak} on a {size} byte file")

    def test_line_range_is_still_reported_for_a_small_file(self) -> None:
        self.fx.write_declaration()
        ctx = self.fx.context()
        self.assertIsNone(proposals.evidence_problem(ctx, "artifacts/build_env.log:3"))
        self.assertIn("(3 lines)", proposals.evidence_problem(ctx, "artifacts/build_env.log:4"))
        self.fx.write_text("artifacts/no_final_newline.log", "one\ntwo")
        self.assertIsNone(proposals.evidence_problem(ctx, "artifacts/no_final_newline.log:2"))
        self.assertIn("(2 lines)", proposals.evidence_problem(ctx, "artifacts/no_final_newline.log:3"))
        self.fx.write_text("artifacts/empty.log", "")
        self.assertIn("(0 lines)", proposals.evidence_problem(ctx, "artifacts/empty.log:1"))

    def test_line_count_splits_on_a_bare_carriage_return_like_the_digest(self) -> None:
        # A progress bar redraws with a bare \r, which is the ordinary shape of a cluster job log,
        # not a corner case. Counting \n alone collapses a 302-line shard log to 3 lines, so the
        # gate refuses path:line citations that digest.read_log_tail shows without complaint.
        path = self.progress_log()
        shown = len(digest.read_log_tail(path, 100000, 4000, 1 << 24))
        self.assertEqual(shown, 302)
        self.assertEqual(proposals.evidence_line_count(path, 10 ** 9, 1 << 24), shown)
        self.fx.write_declaration()
        ctx = self.fx.context()
        self.assertIsNone(proposals.evidence_problem(ctx, "vc_logs/shards.log:302"))
        self.assertIn("(302 lines)", proposals.evidence_problem(ctx, "vc_logs/shards.log:303"))

    def test_line_count_is_the_same_whatever_the_chunk_size(self) -> None:
        # \r\n straddling two read()s is one break, not two.
        path = self.progress_log(frames=40)
        self.assertEqual(proposals.evidence_line_count(path, 10 ** 9, 1 << 24), 42)
        for chunk in (1, 2, 3, 7, 64, 4096):
            with self.subTest(chunk=chunk):
                self.assertEqual(proposals.evidence_line_count(path, 10 ** 9, 1 << 24, chunk_bytes=chunk), 42)

    def test_line_beyond_the_byte_cap_is_not_called_out_of_range(self) -> None:
        # The count stops at evidence_max_bytes, so the gate has no basis to call a line in the
        # unread remainder of a huge log a mistake; it must not invent a failure either.
        self.big_log()
        self.fx.config = dict(self.fx.config, evidence_max_bytes=4096)
        self.fx.write_declaration()
        ctx = self.fx.context()
        self.assertIsNone(proposals.evidence_problem(ctx, "vc_logs/job.log:900000"))

    def test_fact_trigger_scan_is_bounded(self) -> None:
        needle = "no kernel image is available"
        self.big_log(tail=needle.encode("utf-8") + b"\n")
        self.fx.write_declaration()
        ctx = self.fx.context()
        failures, peak = self.peak_of(
            lambda: proposals._fact_trigger_failures(ctx, "01-x", [needle], ["vc_logs/job.log"]))
        self.assertEqual(failures, [])
        self.assertLess(peak, 2 * self.MB)

    def test_trigger_across_a_chunk_boundary_is_still_found(self) -> None:
        path = self.fx.write_text("artifacts/split.log", "a" * 100 + "no kernel image" + "b" * 100)
        for chunk in (8, 16, 64, 4096):
            with self.subTest(chunk=chunk):
                found, truncated = proposals.evidence_triggers_found(path, ["No Kernel Image"], 1 << 20, chunk_bytes=chunk)
                self.assertEqual((found, truncated), ({"No Kernel Image"}, False))

    def test_trigger_scan_says_when_it_stopped_at_the_byte_cap(self) -> None:
        # A complete scan and a scan that ran out of budget are different answers: "not found" only
        # means absent when the whole file was read.
        path = self.fx.write_text("artifacts/late.log", "x" * 5000 + "no kernel image")
        self.assertEqual(proposals.evidence_triggers_found(path, ["no kernel image"], 1 << 20), ({"no kernel image"}, False))
        self.assertEqual(proposals.evidence_triggers_found(path, ["no kernel image"], 1024), (set(), True))
        self.assertEqual(proposals.evidence_triggers_found(path, [], 1024), (set(), False))

    def test_trigger_past_the_byte_cap_is_not_reported_as_missing(self) -> None:
        # Same contract as the line count above: the scan stopped at evidence_max_bytes, so a
        # trigger that sits in the unread remainder of a 158 MB job log must not be announced as
        # absent from it. Silence, not a fabricated failure.
        needle = "no kernel image is available"
        self.big_log(tail=needle.encode("utf-8") + b"\n")
        self.fx.config = dict(self.fx.config, evidence_max_bytes=4096)
        self.fx.write_declaration()
        ctx = self.fx.context()
        self.assertEqual(proposals._fact_trigger_failures(ctx, "01-x", [needle], ["vc_logs/job.log"]), [])

    def test_trigger_absent_from_a_fully_read_file_is_still_reported(self) -> None:
        # The cap must not become a blanket amnesty: a small evidence file is read to the end, so
        # the gate does know the trigger is not there.
        self.fx.write_declaration()
        ctx = self.fx.context()
        failures = proposals._fact_trigger_failures(ctx, "01-x", ["no kernel image"], ["artifacts/memory_evidence/1.txt"])
        self.assertEqual(len(failures), 1, failures)
        self.assertIn("does not appear verbatim", failures[0].message)

    def test_streaming_helpers_report_an_unreadable_file(self) -> None:
        missing = self.fx.run_dir / "artifacts" / "gone.log"
        with self.assertRaises(OSError):
            proposals.evidence_line_count(missing, 1, 1 << 20)
        with self.assertRaises(OSError):
            proposals.evidence_triggers_found(missing, ["anything"], 1 << 20)

    def test_scan_separates_a_trigger_it_could_not_check_from_one_it_ruled_out(self) -> None:
        # The gate is silent about both, so nothing downstream can tell them apart unless the scan
        # says which is which: "read to the end, not there" vs "ran out of budget, never looked".
        needle = "no kernel image is available"
        self.big_log(tail=needle.encode("utf-8") + b"\n")
        big = self.fx.run_dir / "vc_logs" / "job.log"
        small = self.fx.run_dir / "artifacts" / "memory_evidence" / "1.txt"

        capped = proposals.scan_evidence_triggers([big], [needle], 4096)
        self.assertEqual((capped.found, capped.missing, capped.unverified), ([], [], [needle]))
        self.assertEqual(capped.readable, 1)

        ruled_out = proposals.scan_evidence_triggers([small], [needle], 1 << 20)
        self.assertEqual((ruled_out.found, ruled_out.missing, ruled_out.unverified), ([], [needle], []))

        seen = proposals.scan_evidence_triggers([small], ["site-gpu-data"], 1 << 20)
        self.assertEqual((seen.found, seen.missing, seen.unverified), (["site-gpu-data"], [], []))

    def test_scan_reports_nothing_readable_when_every_file_fails_to_open(self) -> None:
        scan = proposals.scan_evidence_triggers([self.fx.run_dir / "artifacts" / "gone.log"], ["no kernel image"], 1 << 20)
        self.assertEqual(scan.readable, 0)
        self.assertEqual(scan.missing, ["no kernel image"])  # nothing was read, so nothing was capped
        self.assertEqual(scan.unverified, [])

    def test_scan_keeps_trigger_order_and_only_one_bucket_per_trigger(self) -> None:
        needle = "no kernel image is available"
        self.big_log(tail=needle.encode("utf-8") + b"\n")
        big = self.fx.run_dir / "vc_logs" / "job.log"
        small = self.fx.run_dir / "artifacts" / "memory_evidence" / "1.txt"
        scan = proposals.scan_evidence_triggers([small, big], ["site-gpu-data", needle, "site-gpu"], 4096)
        self.assertEqual(scan.found, ["site-gpu-data", "site-gpu"])
        self.assertEqual(scan.unverified, [needle])
        self.assertEqual(scan.missing, [])


# --- declaration scaffold and candidate loading -----------------------------------------

class DeclarationScaffoldTests(GateTestCase):
    def test_unreadable_declaration_is_a_single_rule_1_failure(self) -> None:
        failures = self.fx.run()
        self.assertEqual([f.rule for f in failures], [1])
        self.assertIn("cannot read extraction_declaration.json", failures[0].message)
        self.fx.write_text("artifacts/extraction_declaration.json", "{not json")
        failures = self.fx.run()
        self.assertEqual([f.rule for f in failures], [1])

    def test_declaration_must_be_an_object(self) -> None:
        self.fx.write_json("artifacts/extraction_declaration.json", ["list"])
        failures = self.fx.run()
        self.assertEqual([f.rule for f in failures], [1])
        self.assertIn("must be a JSON object", failures[0].message)

    def test_declaration_shape_errors(self) -> None:
        self.fx.write_json("artifacts/extraction_declaration.json", {
            "schema": "sure.check.extraction.v1", "no_new_lessons": "yes", "no_lessons_reason": 5,
            "covered_by": "x", "candidates": [1], "infra_evidence": None,
        })
        failures = self.fx.run()
        self.assertFailure(failures, 1, "missing required field(s) ['infra_noise']")
        self.assertFailure(failures, 1, "schema must be 'sure.memory.extraction.v2'")
        self.assertFailure(failures, 1, "no_new_lessons must be a boolean")
        self.assertFailure(failures, 1, "no_lessons_reason must be a string or null")
        self.assertFailure(failures, 1, "covered_by must be a list of strings")
        self.assertFailure(failures, 1, "candidates must be a list of strings")
        self.assertFailure(failures, 1, "infra_evidence must be a list of strings")

    def test_no_new_lessons_without_candidates_is_clean(self) -> None:
        self.fx.write_declaration(no_new_lessons=True, no_lessons_reason="clean success run")
        self.assertClean(self.fx.run())

    def test_candidate_dir_and_files_missing_are_rule_10(self) -> None:
        self.fx.candidate_ids.append("01-ghost")
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 10, "candidate 01-ghost: directory artifacts/candidates/01-ghost does not exist")
        (self.fx.artifacts / "candidates" / "02-half").mkdir()
        self.fx.write_json("artifacts/candidates/02-half/proposal.json", bad_case_proposal())
        self.fx.candidate_ids.append("02-half")
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 10, "candidate 02-half: missing proposal.md")

    def test_candidate_id_with_separator_is_not_joined(self) -> None:
        self.fx.add_candidate("01-real", bad_case_proposal(), bad_case_md())
        self.fx.candidate_ids.append("../01-real")
        self.fx.candidate_ids.append("nested/01-real")
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 10, "candidate id '../01-real' must be a single directory name")
        self.assertFailure(failures, 10, "candidate id 'nested/01-real' must be a single directory name")
        self.assertNoRule(failures, 1)

    def test_oversized_proposal_md_is_refused_before_it_is_parsed(self) -> None:
        # bad_case_max_words counts nothing inside a fence, so a pasted job log passes every rule;
        # the byte cap is the only thing between the gate and a multi-megabyte candidate.
        limit = int(self.fx.config["proposal_md_max_bytes"])
        md = bad_case_md() + "\n## Example Artifacts\n\n```text\n" + "log line\n" * limit + "```\n"
        self.fx.add_candidate("01-x", bad_case_proposal(), md)
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 10, f"candidate 01-x: proposal.md is ")
        self.assertFailure(failures, 10, f"bytes (max {limit})")
        self.assertNoRule(failures, 1)  # the body was never parsed, so no word-count noise
        self.assertLess(proposals.count_body_words(md), int(self.fx.config["bad_case_max_words"]))

    def test_proposal_md_at_the_cap_still_loads(self) -> None:
        limit = int(self.fx.config["proposal_md_max_bytes"])
        md = bad_case_md()
        md += "\n```text\n" + "x" * (limit - len(md.encode("utf-8")) - 1024) + "\n```\n"
        self.fx.add_candidate("01-x", bad_case_proposal(), md)
        size = (self.fx.artifacts / "candidates" / "01-x" / "proposal.md").stat().st_size
        self.assertLessEqual(size, limit)
        self.assertGreater(size, limit - 2048)
        self.fx.write_declaration()
        self.assertNoRule(self.fx.run(), 10)

    def test_bad_proposal_json_is_rule_10(self) -> None:
        self.fx.add_candidate("01-x", bad_case_proposal(), bad_case_md())
        self.fx.write_text("artifacts/candidates/01-x/proposal.json", "{oops")
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 10, "candidate 01-x: proposal.json unreadable")
        self.fx.write_json("artifacts/candidates/01-x/proposal.json", [1, 2])
        failures = self.fx.run()
        self.assertFailure(failures, 10, "candidate 01-x: proposal.json is not a JSON object")

    def test_digest_error_only_allows_no_new_lessons(self) -> None:
        self.fx.write_digest(make_digest(error="events.jsonl unreadable"))
        self.fx.add_candidate("01-x", bad_case_proposal(), bad_case_md())
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 10, "run_digest.json could not be built (events.jsonl unreadable)")
        self.fx.candidate_ids.clear()
        stash = self.fx.artifacts / "stash-01-x"   # part B refuses undeclared candidate dirs: move it aside
        (self.fx.artifacts / "candidates" / "01-x").rename(stash)
        self.fx.write_declaration(no_new_lessons=True, no_lessons_reason="digest error: events.jsonl unreadable")
        self.assertClean(self.fx.run())
        stash.rename(self.fx.artifacts / "candidates" / "01-x")
        (self.fx.artifacts / "run_digest.json").unlink()
        self.fx.candidate_ids.append("01-x")
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 10, "run_digest.json is missing or unreadable")

    def test_no_new_lessons_passes_without_digest_or_checkpoint(self) -> None:
        # Skeleton 1.13: an empty declaration passes even when the hook never built a digest
        # and the checkpoint has no memory object (test fixtures and spec 4.2 rely on this).
        (self.fx.artifacts / "run_digest.json").unlink()
        self.fx.write_declaration(no_new_lessons=True, no_lessons_reason="clean success run")
        self.assertClean(self.fx.run(sha=None))
        # The opposite pair (no_new_lessons false, no candidates) is part B's rule 10; not asserted here.

    def test_failures_sorted_by_rule_and_registry_is_consulted(self) -> None:
        self.fx.add_candidate("01-x", bad_case_proposal(trigger=["no kernel image is available", "x"], causal=True,
                                                       evidence=["artifacts/build_env.log"]), bad_case_md())
        self.fx.write_declaration()
        marker = proposals.GateFailure(rule=99, message="marker")
        proposals.RULES.append(lambda ctx: [marker])
        try:
            failures = self.fx.run()
        finally:
            proposals.RULES.pop()
        rules = [f.rule for f in failures]
        self.assertEqual(rules, sorted(rules))
        self.assertIn(marker, failures)
        self.assertEqual(rules[-1], 99)


# --- rule 1: schema, enums, required, body ---------------------------------------------

class Rule1Tests(GateTestCase):
    def test_valid_bad_case_passes_every_part_a_rule(self) -> None:
        self.fx.add_candidate("01-cuda-arch-mismatch", bad_case_proposal(), bad_case_md())
        self.fx.write_declaration()
        self.assertClean(self.fx.run())

    def test_valid_fact_passes(self) -> None:
        self.fx.add_candidate("01-queue-name", fact_proposal(), fact_md())
        self.fx.write_declaration()
        self.assertClean(self.fx.run())

    def test_schema_and_missing_fields(self) -> None:
        p = bad_case_proposal(schema="sure.check.memory_proposal.v1")
        del p["similar"]
        del p["checked_at"]
        self.fx.add_candidate("01-x", p, bad_case_md())
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 1, "candidate 01-x: proposal.json is missing required field(s) ['similar', 'checked_at']")
        self.assertFailure(failures, 1, "schema must be 'sure.memory.proposal.v2'")

    def test_type_and_op_enums(self) -> None:
        self.fx.add_candidate("01-x", bad_case_proposal(type="recipe", op="edit"), bad_case_md())
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 1, "type must be one of ('bad_case', 'fact'), got 'recipe'")
        self.assertFailure(failures, 1, "op must be one of ('add', 'modify', 'supersede'), got 'edit'")

    def test_target_skill_and_applies_to(self) -> None:
        self.fx.add_candidate("01-x", bad_case_proposal(target_skill="sure_check", applies_to=[]), bad_case_md())
        self.fx.add_candidate("02-x", bad_case_proposal(applies_to=["sure_onboard", "nope", "sure_onboard"]), bad_case_md())
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 1, "candidate 01-x: target_skill must be one of")
        self.assertFailure(failures, 1, "candidate 01-x: applies_to must be a non-empty list of skills")
        self.assertFailure(failures, 1, "candidate 02-x: applies_to contains unknown skill 'nope'")
        self.assertFailure(failures, 1, "candidate 02-x: applies_to has duplicates")

    def test_type_and_target_skill_must_agree(self) -> None:
        # skeleton 1.4 fixes the layout: facts live under _shared, bad_cases under their skill
        self.fx.add_candidate("01-x", fact_proposal(target_skill="sure_onboard"), fact_md())
        self.fx.add_candidate("02-x", bad_case_proposal(target_skill="_shared", applies_to=["_shared"]), bad_case_md())
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 1, "candidate 01-x: a fact must use target_skill '_shared'")
        self.assertFailure(failures, 1, "candidate 02-x: target_skill '_shared' is for facts")

    def test_target_entry_by_op(self) -> None:
        self.fx.add_candidate("01-x", bad_case_proposal(target_entry="sure_onboard/old"), bad_case_md())
        self.fx.add_candidate("02-x", bad_case_proposal(op="modify"), bad_case_md())
        self.fx.add_candidate("03-x", bad_case_proposal(op="supersede", target_entry="not an id"), bad_case_md())
        self.fx.add_candidate("04-x", bad_case_proposal(op="modify", target_entry="sure_onboard/old-entry"), bad_case_md())
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 1, "candidate 01-x: target_entry must be null for op=add")
        self.assertFailure(failures, 1, "candidate 02-x: target_entry must be an entry id for op=modify")
        self.assertFailure(failures, 1, "candidate 03-x: target_entry must be an entry id for op=supersede")
        # only rule 1 is under test: part B's rule 8 also reports that 04-x's target_entry is not in the index
        self.assertFalse([f for f in failures if f.rule == 1 and f.message.startswith("candidate 04-x") and "target_entry" in f.message])

    def test_cell_component_depends_on_skill_and_type(self) -> None:
        p1 = bad_case_proposal()
        p1["cell"]["component"] = "dataset_scope"  # an infer unit, not an onboard one
        p2 = bad_case_proposal(target_skill="sure_eval", applies_to=["sure_eval"])  # a target skill with no units.json row
        p2["cell"]["component"] = "build_env"
        p3 = fact_proposal()
        p3["cell"]["component"] = "build_env"
        p4 = bad_case_proposal(target_skill="sure_eval", applies_to=["sure_eval"])
        p4["cell"]["component"] = "_"
        p5 = bad_case_proposal()
        p5["cell"] = "build_env x infra"
        for cid, p, md in (("01-x", p1, bad_case_md()), ("02-x", p2, bad_case_md()), ("03-x", p3, fact_md()),
                           ("04-x", p4, bad_case_md()), ("05-x", p5, bad_case_md())):
            self.fx.add_candidate(cid, p, md)
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 1, "candidate 01-x: cell.component 'dataset_scope' must be a sure_onboard unit id")
        self.assertFailure(failures, 1, "candidate 02-x: cell.component 'build_env' must be '_' (sure_eval has no state machine)")
        self.assertFailure(failures, 1, "candidate 03-x: cell.component must be '_' for a fact")
        self.assertFalse([f for f in failures if f.message.startswith("candidate 04-x") and "component" in f.message])
        self.assertFailure(failures, 1, "candidate 05-x: cell must be an object with component and cause")

    def test_cell_component_must_be_named_by_a_claim(self) -> None:
        # match.ts filters bad_cases on component === unit with no fallback, so a component the
        # candidate's own claims never mention is published onto a cell it can never fire from.
        p1 = bad_case_proposal()
        p1["cell"]["component"] = "fetch_weights"  # a unit this run walked, but no claim says so
        p2 = bad_case_proposal(claims=[])          # component build_env, nothing anchoring it
        p3 = bad_case_proposal(target_skill="sure_infer", applies_to=["sure_infer"])
        p3["cell"]["component"] = "dataset_scope"  # another skill's unit: not this run's to bind
        p4 = bad_case_proposal(claims=[])
        p4["cell"]["component"] = "validate_import"  # an onboard unit this run never reached
        for cid, p in (("01-x", p1), ("02-x", p2), ("03-x", p3), ("04-x", p4)):
            self.fx.add_candidate(cid, p, bad_case_md())
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 1, "candidate 01-x: cell.component 'fetch_weights' is not named by any claim")
        self.assertFailure(failures, 1, "candidate 02-x: cell.component 'build_env' is not named by any claim")
        for cid in ("03-x", "04-x"):
            self.assertFalse([f for f in failures if f.message.startswith(f"candidate {cid}") and "named by any claim" in f.message])

    def test_cell_cause_enum_and_fact_rules(self) -> None:
        p1 = bad_case_proposal()
        p1["cell"]["cause"] = "environment"
        p2 = bad_case_proposal()
        p2["cell"]["cause"] = "n.a."
        p3 = fact_proposal()
        p3["cell"]["cause"] = "infra"
        self.fx.add_candidate("01-x", p1, bad_case_md())
        self.fx.add_candidate("02-x", p2, bad_case_md())
        self.fx.add_candidate("03-x", p3, fact_md())
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 1, "candidate 01-x: cell.cause 'environment' must be one of")
        self.assertFailure(failures, 1, "candidate 02-x: cell.cause 'n.a.' is reserved for facts")
        self.assertFailure(failures, 1, "candidate 03-x: cell.cause must be 'n.a.' for a fact")

    def test_trigger_list_hygiene(self) -> None:
        many = [f"no kernel image is available {i}" for i in range(6)]
        self.fx.add_candidate("01-x", bad_case_proposal(trigger=many), bad_case_md())
        self.fx.add_candidate("02-x", bad_case_proposal(trigger=["a | b long enough", "c; d long enough", "bad\u2028line here", 7]), bad_case_md())
        self.fx.add_candidate("03-x", bad_case_proposal(trigger="not a list"), bad_case_md())
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 1, "candidate 01-x: trigger has 6 entries (max 5)")
        self.assertFailure(failures, 1, "candidate 02-x: trigger[0] contains a '|'")
        self.assertFailure(failures, 1, "candidate 02-x: trigger[1] contains a ';'")
        self.assertFailure(failures, 1, "candidate 02-x: trigger[2] contains the non-printable character")
        self.assertFailure(failures, 1, "candidate 02-x: trigger[3] must be a string")
        self.assertFailure(failures, 1, "candidate 03-x: trigger must be a list of strings")

    def test_causal_evidence_and_claims_shapes(self) -> None:
        self.fx.add_candidate("01-x", bad_case_proposal(causal="yes", evidence=[], claims="x"), bad_case_md())
        self.fx.add_candidate("02-x", bad_case_proposal(evidence=["", 3], claims=[
            {"kind": "verification_item", "unit": "build_env", "attempt": 1, "status": "failed"},
            {"kind": "unit_result", "unit": "", "attempt": 0, "status": ""},
            {"kind": "gate_repair", "unit": "build_env", "attempt": True, "status": "failed"},
            "not an object",
        ]), bad_case_md())
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 1, "candidate 01-x: causal must be a boolean")
        self.assertFailure(failures, 1, "candidate 01-x: evidence must be a non-empty list of strings")
        self.assertFailure(failures, 1, "candidate 01-x: claims must be a list")
        self.assertFailure(failures, 1, "candidate 02-x: evidence[0] must be a non-empty string")
        self.assertFailure(failures, 1, "candidate 02-x: evidence[1] must be a non-empty string")
        self.assertFailure(failures, 1, "candidate 02-x: claims[0].kind must be one of")
        self.assertFailure(failures, 1, "candidate 02-x: claims[1].unit must be a non-empty string")
        self.assertFailure(failures, 1, "candidate 02-x: claims[1].attempt must be an integer >= 1")
        self.assertFailure(failures, 1, "candidate 02-x: claims[1].status must be a non-empty string")
        self.assertFailure(failures, 1, "candidate 02-x: claims[2].attempt must be an integer >= 1")
        self.assertFailure(failures, 1, "candidate 02-x: claims[3] must be an object")

    def test_source_shape(self) -> None:
        self.fx.add_candidate("01-x", bad_case_proposal(source={"run_id": "run;1", "skill": "sure_check", "target": "", "digest_sha256": "abc"}), bad_case_md())
        self.fx.add_candidate("02-x", bad_case_proposal(source="nope"), bad_case_md())
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 1, "candidate 01-x: source.run_id contains a ';'")
        self.assertFailure(failures, 1, "candidate 01-x: source.skill must be one of")
        self.assertFailure(failures, 1, "candidate 01-x: source.target must be a non-empty string")
        self.assertFailure(failures, 1, "candidate 01-x: source.digest_sha256 must be a 64-character lowercase hex sha256")
        self.assertFailure(failures, 1, "candidate 02-x: source must be an object")

    def test_similar_shape(self) -> None:
        self.fx.add_candidate("01-x", bad_case_proposal(similar={"entry": "bad id", "difference": ""}), bad_case_md())
        self.fx.add_candidate("02-x", bad_case_proposal(similar="sure_onboard/x"), bad_case_md())
        self.fx.add_candidate("03-x", bad_case_proposal(similar={"entry": "sure_onboard/no-kernel-image", "difference": "covers cu118 wheels, not driver age"}), bad_case_md())
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 1, "candidate 01-x: similar.entry must be an entry id")
        self.assertFailure(failures, 1, "candidate 01-x: similar.difference must be a non-empty string")
        self.assertFailure(failures, 1, "candidate 02-x: similar must be null or an object")
        # only rule 1 is under test: part B's rule 7 also reports that 03-x's similar.entry is not in the index
        self.assertFalse([f for f in failures if f.rule == 1 and f.message.startswith("candidate 03-x")])

    def test_scope_and_checked_at_depend_on_type(self) -> None:
        self.fx.add_candidate("01-x", bad_case_proposal(scope="cluster", checked_at="2026-08-18"), bad_case_md())
        self.fx.add_candidate("02-x", fact_proposal(scope="node:gpu-3", checked_at="18/08/2026"), fact_md(scope="node:gpu-3", checked_at="18/08/2026"))
        self.fx.add_candidate("03-x", fact_proposal(scope="model_family:", checked_at="2026-02-30"), fact_md(scope="model_family:", checked_at="2026-02-30"))
        self.fx.add_candidate("04-x", fact_proposal(scope="model_family:qwen2-audio"), fact_md(scope="model_family:qwen2-audio"))
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 1, "candidate 01-x: scope must be null for a bad_case")
        self.assertFailure(failures, 1, "candidate 01-x: checked_at must be null for a bad_case")
        self.assertFailure(failures, 1, "candidate 02-x: scope must be 'cluster', 'model_family:<name>' or 'dataset:<name>'")
        self.assertFailure(failures, 1, "candidate 02-x: checked_at must be a YYYY-MM-DD date")
        self.assertFailure(failures, 1, "candidate 03-x: scope must be 'cluster', 'model_family:<name>' or 'dataset:<name>'")
        self.assertFailure(failures, 1, "candidate 03-x: checked_at is not a real calendar date")
        self.assertFalse([f for f in failures if f.message.startswith("candidate 04-x")])

    def test_bad_case_body_errors_and_word_limit(self) -> None:
        self.fx.add_candidate("01-x", bad_case_proposal(), bad_case_md(extra_words=190))
        self.fx.add_candidate("02-x", bad_case_proposal(), bad_case_md().replace("## Verification", "## Verify"))
        self.fx.add_candidate("03-x", bad_case_proposal(), bad_case_md().replace("sure_onboard / build_env", "Trigger: forged"))
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 1, "candidate 01-x: proposal.md body is")
        self.assertFailure(failures, 1, "words (max 200")
        self.assertFailure(failures, 1, "candidate 02-x: proposal.md: missing section '## Verification'")
        self.assertFailure(failures, 1, "candidate 03-x: proposal.md: line 'Trigger: forged' starts with a provenance prefix")

    def test_fact_body_word_limit_and_consistency_with_json(self) -> None:
        long_notes = " ".join(["word"] * 61)
        self.fx.add_candidate("01-x", fact_proposal(), fact_md(notes=long_notes))
        self.fx.add_candidate("02-x", fact_proposal(), fact_md(scope="dataset:x", checked_at="2026-08-17", evidence="artifacts/other.txt"))
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 1, "candidate 01-x: proposal.md notes are 61 words (max 60")
        self.assertFailure(failures, 1, "candidate 02-x: proposal.md Scope: 'dataset:x' does not equal proposal.json scope 'cluster'")
        self.assertFailure(failures, 1, "candidate 02-x: proposal.md Checked-at: '2026-08-17' does not equal proposal.json checked_at '2026-08-18'")
        self.assertFailure(failures, 1, "candidate 02-x: proposal.md Evidence: 'artifacts/other.txt' is not listed in proposal.json evidence")


# --- rule 4: trigger discipline ---------------------------------------------------------

class Rule4Tests(GateTestCase):
    def test_bad_case_needs_at_least_one_trigger(self) -> None:
        self.fx.add_candidate("01-x", bad_case_proposal(trigger=[]), bad_case_md())
        self.fx.write_declaration()
        self.assertFailure(self.fx.run(), 4, "candidate 01-x: a bad_case needs at least one trigger")

    def test_all_triggers_run_specific(self) -> None:
        # first trigger carries the run id, second one the target id (and is even observed in the repair)
        self.fx.add_candidate("01-x", bad_case_proposal(trigger=[f"{RUN_ID} build_env failed", "build_env_result.json for qwen2-audio-7b says success=false"]), bad_case_md())
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 4, "candidate 01-x: no reusable trigger")
        self.assertFailure(failures, 4, "run-specific")

    def test_no_trigger_observed_in_digest(self) -> None:
        self.fx.add_candidate("01-x", bad_case_proposal(trigger=["libcudnn.so.8 not loaded"]), bad_case_md())
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 4, "candidate 01-x: no reusable trigger")
        self.assertFailure(failures, 4, "appears verbatim")

    def test_trigger_observed_only_in_log_tail_passes(self) -> None:
        self.fx.add_candidate("01-x", bad_case_proposal(trigger=["No matching distribution found for torch"]), bad_case_md())
        self.fx.write_declaration()
        self.assertNoRule(self.fx.run(), 4)

    def test_observation_is_case_insensitive(self) -> None:
        self.fx.add_candidate("01-x", bad_case_proposal(trigger=["NO KERNEL IMAGE IS AVAILABLE"]), bad_case_md())
        self.fx.write_declaration()
        self.assertNoRule(self.fx.run(), 4)

    def test_generic_and_observed_must_be_the_same_trigger(self) -> None:
        # one generic-but-unobserved trigger plus one observed-but-target-specific trigger: still no reusable one
        self.fx.add_candidate("01-x", bad_case_proposal(trigger=["libcudnn.so.8 not loaded", "build_env_result.json for qwen2-audio-7b"]), bad_case_md())
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 4, "candidate 01-x: no reusable trigger")
        self.assertFailure(failures, 4, "same trigger")

    def test_extra_unobserved_triggers_are_allowed_next_to_a_reusable_one(self) -> None:
        self.fx.add_candidate("01-x", bad_case_proposal(trigger=["no kernel image is available", "libcudnn.so.8 not loaded"]), bad_case_md())
        self.fx.write_declaration()
        self.assertNoRule(self.fx.run(), 4)

    def test_short_stopword_template_and_regex_triggers_are_reported(self) -> None:
        self.fx.add_candidate("01-x", bad_case_proposal(trigger=[
            "no kernel image is available", "cuda", "Not Found", "Blocked because:", "re:no kernel.*", "short",
        ]), bad_case_md())
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 4, "candidate 01-x: trigger 'cuda' is shorter than 8")
        self.assertFailure(failures, 4, "candidate 01-x: trigger 'Not Found' is a stop word")
        self.assertFailure(failures, 4, "candidate 01-x: trigger 'Blocked because:' is only harness template text")
        self.assertFailure(failures, 4, "candidate 01-x: trigger 're:no kernel.*' starts with 're:'")
        self.assertFailure(failures, 4, "candidate 01-x: trigger 'short' is shorter than 8")
        self.assertFailure(failures, 1, "trigger has 6 entries (max 5)")

    def test_fact_needs_at_least_one_trigger(self) -> None:
        # A scope-only fact is not inert: matchFacts selects on scope OR a trigger hit, so
        # scope 'cluster' with no trigger is injected into every run at hitLength 0.
        self.fx.add_candidate("01-x", fact_proposal(trigger=[]), fact_md())
        self.fx.write_declaration()
        self.assertFailure(self.fx.run(), 4, "candidate 01-x: a fact needs at least one trigger")

    def test_blank_triggers_do_not_count_for_either_type(self) -> None:
        self.fx.add_candidate("01-x", fact_proposal(trigger=["   "]), fact_md())
        self.fx.add_candidate("02-x", bad_case_proposal(trigger=["", " "]), bad_case_md())
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 4, "candidate 01-x: a fact needs at least one trigger")
        self.assertFailure(failures, 4, "candidate 02-x: a bad_case needs at least one trigger")

    def test_fact_trigger_must_appear_in_an_evidence_file(self) -> None:
        self.fx.add_candidate("01-x", fact_proposal(trigger=["site-gpu-alt-minijob"]), fact_md())
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 4, "candidate 01-x: fact trigger 'site-gpu-alt-minijob' does not appear verbatim in any evidence file")
        self.fx.add_candidate("02-x", fact_proposal(trigger=["SITE-GPU-DATA"], evidence=["artifacts/memory_evidence/none.txt"]), fact_md(evidence="artifacts/memory_evidence/none.txt"))
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 4, "candidate 02-x: fact trigger 'SITE-GPU-DATA' does not appear verbatim in any evidence file (no evidence file could be read)")

    def test_fact_trigger_found_through_path_line_evidence(self) -> None:
        self.fx.add_candidate("01-x", fact_proposal(trigger=["SITE-GPU-DATA"], evidence=["artifacts/memory_evidence/1.txt:2"]),
                              fact_md(evidence="artifacts/memory_evidence/1.txt:2"))
        self.fx.write_declaration()
        self.assertClean(self.fx.run())


# --- rule 4 against the unclipped repair (events.jsonl, not the digest) -------------------

BURIED_TRIGGER = "unsupported gpu architecture 'compute_120'"


class Rule4FullRepairTests(GateTestCase):
    """The digest clips repairs[].text head+tail; rule 4 must still see what the clip elided."""

    def setUp(self) -> None:
        super().setUp()
        limits = self.fx.config["digest_limits"]
        self.head, self.tail = limits["repair_head_chars"], limits["repair_tail_chars"]
        self.full_repair = (
            "H" * self.head + "\n" + BURIED_TRIGGER + "\n" + "M" * 2000 + "\n" + "T" * self.tail
        )

    def repair_event(self, text: str) -> dict:
        return {"type": "tool_result_repair", "data": {"state_patch": {"diagnostics": [{"repair": text}]}}}

    def write_run(self, events: list[dict], *, cutoff: int, repair_text: str) -> None:
        self.fx.write_events(events)
        d = make_digest()
        d["run"]["cutoff"] = cutoff
        d["units"][1]["repairs"] = [{"attempt": 1, "text": repair_text}]
        self.fx.write_digest(d)

    def test_trigger_in_the_elided_middle_of_a_repair_counts_as_observed(self) -> None:
        clipped = digest.clip_head_tail(self.full_repair, self.head, self.tail)
        self.assertNotIn(BURIED_TRIGGER, clipped)  # the clip really did drop it
        events = [{"type": "created", "data": {"skillName": "sure_onboard"}}, self.repair_event(self.full_repair)]
        self.write_run(events, cutoff=len(events), repair_text=clipped)
        self.fx.add_candidate("01-x", bad_case_proposal(trigger=[BURIED_TRIGGER]), bad_case_md())
        self.fx.write_declaration()
        self.assertNoRule(self.fx.run(), 4)

    def test_repairs_300_clip_does_not_hide_the_trigger_either(self) -> None:
        once = digest.clip_head_tail(self.full_repair, self.head, self.tail)
        twice = digest.reclip_head_tail(once, self.head // 2, self.tail // 2)
        self.assertNotIn(BURIED_TRIGGER, twice)
        events = [self.repair_event(self.full_repair)]
        self.write_run(events, cutoff=len(events), repair_text=twice)
        self.fx.add_candidate("01-x", bad_case_proposal(trigger=[BURIED_TRIGGER]), bad_case_md())
        self.fx.write_declaration()
        self.assertNoRule(self.fx.run(), 4)

    def test_events_past_the_digest_cutoff_are_not_read(self) -> None:
        # The repair carrying the trigger was written after the digest was built, so it is this
        # gate's own output quoted back at the agent, not something the run observed.
        events = [{"type": "created", "data": {"skillName": "sure_onboard"}}, self.repair_event(self.full_repair)]
        self.write_run(events, cutoff=1, repair_text=digest.clip_head_tail(self.full_repair, self.head, self.tail))
        self.fx.add_candidate("01-x", bad_case_proposal(trigger=[BURIED_TRIGGER]), bad_case_md())
        self.fx.write_declaration()
        self.assertFailure(self.fx.run(), 4, "candidate 01-x: no reusable trigger")

    def test_injected_memory_block_in_a_repair_is_not_observation(self) -> None:
        header = self.fx.config["inject_header"]
        seen = f"prefix\n\n{header}\n- {BURIED_TRIGGER}: do the thing\n\nsuffix"
        events = [{"type": "finish_repair", "data": {"repair": seen}}]
        self.write_run(events, cutoff=len(events), repair_text="")
        self.fx.add_candidate("01-x", bad_case_proposal(trigger=[BURIED_TRIGGER]), bad_case_md())
        self.fx.write_declaration()
        self.assertFailure(self.fx.run(), 4, "candidate 01-x: no reusable trigger")

    def test_no_events_file_falls_back_to_the_digest_texts(self) -> None:
        texts = proposals.trigger_texts(self.fx.run_dir, make_digest(), self.fx.config)
        self.assertEqual(texts, [REPAIR_TEXT, *LOG_LINES])
        self.assertIsNone(proposals.repair_texts_from_events(self.fx.run_dir, make_digest(), self.fx.config))

    def test_a_digest_without_a_cutoff_falls_back_to_the_digest_texts(self) -> None:
        events = [self.repair_event(self.full_repair)]
        self.fx.write_events(events)
        d = make_digest()
        d["run"].pop("cutoff")
        self.assertIsNone(proposals.repair_texts_from_events(self.fx.run_dir, d, self.fx.config))
        self.assertEqual(proposals.trigger_texts(self.fx.run_dir, d, self.fx.config), [REPAIR_TEXT, *LOG_LINES])

    def test_events_that_hold_no_recognisable_repair_do_not_drop_the_digest_ones(self) -> None:
        # A digest built by another checkout's digest.py, whose repair events this one no longer
        # recognises: trusting the empty result would fail triggers the check accepted before.
        self.fx.write_events([{"type": "tool_call", "data": {"toolName": "bash", "input": {"command": "ls"}}}])
        d = make_digest()
        d["run"]["cutoff"] = 1
        self.assertEqual(proposals.repair_texts_from_events(self.fx.run_dir, d, self.fx.config), [])
        self.assertEqual(proposals.trigger_texts(self.fx.run_dir, d, self.fx.config), [REPAIR_TEXT, *LOG_LINES])

    def test_a_run_with_no_repair_at_all_keeps_only_the_log_tails(self) -> None:
        self.fx.write_events([{"type": "tool_call", "data": {"toolName": "bash", "input": {"command": "ls"}}}])
        d = make_digest()
        d["run"]["cutoff"] = 1
        d["units"][1]["repairs"] = []
        self.assertEqual(proposals.trigger_texts(self.fx.run_dir, d, self.fx.config), list(LOG_LINES))

    def test_a_truncated_events_file_falls_back_to_the_digest_texts(self) -> None:
        self.fx.write_events([self.repair_event(self.full_repair)])
        d = make_digest()
        d["run"]["cutoff"] = 9  # the file no longer reaches the line count the digest was built from
        self.assertIsNone(proposals.repair_texts_from_events(self.fx.run_dir, d, self.fx.config))


# --- rule 4 against a prior run's gate repair (the late-unit path) -------------------------

PRIOR_RUN_ID = "run-20260817-def456"
PRIOR_GATE_REPAIR = (
    "RUN_REPORT_UNIT completed-run execution gate failed:\n"
    '  - successful run report conflicts with execution_result.json job_status "FAILED"'
)
PRIOR_AGENT_SUMMARY = "stopped early, the queue was busy and the weights never landed"
LATE_TRIGGER = "successful run report conflicts with execution_result.json job_status"


def prior_run_rows(gate_repair: str = PRIOR_GATE_REPAIR) -> list[dict]:
    """Two prior runs as digest.py writes them: one gate repair, one agent-written errorSummary."""
    return [
        {"run_id": PRIOR_RUN_ID, "status": "success", "failed_unit": None, "finished_at": None,
         "last_repair": gate_repair, "last_repair_source": "gate", "candidates": []},
        {"run_id": "run-20260816-aaa111", "status": "failed", "failed_unit": "build_env", "finished_at": None,
         "last_repair": PRIOR_AGENT_SUMMARY, "last_repair_source": "agent", "candidates": []},
    ]


def eval_digest_with_prior_gate_repair() -> dict:
    """A sure_infer run sitting in extract_lessons. run_report comes after extract_lessons, so it is
    not in units[] and never can be; the previous run of the same target was blocked there."""
    eval_units = paths.load_units()["skills"]["sure_infer"]
    walked = eval_units[: eval_units.index("extract_lessons")]
    return {
        "schema": proposals.DIGEST_SCHEMA,
        "run": {"run_id": RUN_ID, "skill": "sure_infer", "args": "model=qwen2-audio-7b",
                "target": {"kind": "eval", "id": TARGET_ID}, "status_so_far": "running",
                "cutoff": 0, "memory_usage": []},
        "units": [{"id": u, "outcome": "passed", "attempts": 1, "repairs": [], "fix_window": [],
                   "last_commands": [], "log_tail": None} for u in walked]
        + [{"id": "extract_lessons", "outcome": "current", "attempts": 0, "repairs": [], "fix_window": [],
            "last_commands": [], "log_tail": None}],
        "tool_errors": 0,
        "prior_runs": prior_run_rows(),
        "memory_index_snapshot": [],
        "units_registry": {"sure_infer": eval_units},
    }


class PriorRunTriggerTests(GateTestCase):
    """prior_runs[].last_repair counts as observation only when a gate wrote it. Source "agent" is
    the previous agent's own errorSummary, which is not something a gate ever said."""

    def onboard_digest(self, gate_repair: str = PRIOR_GATE_REPAIR) -> dict:
        d = make_digest()
        d["prior_runs"] = prior_run_rows(gate_repair)
        return d

    def test_a_prior_gate_repair_is_observed_when_events_are_readable(self) -> None:
        events = [{"type": "created", "data": {"skillName": "sure_onboard"}},
                  {"type": "tool_result_repair", "data": {"state_patch": {"diagnostics": [{"repair": REPAIR_TEXT}]}}}]
        self.fx.write_events(events)
        d = self.onboard_digest()
        d["run"]["cutoff"] = len(events)
        self.assertEqual(proposals.trigger_texts(self.fx.run_dir, d, self.fx.config),
                         [REPAIR_TEXT, *LOG_LINES, PRIOR_GATE_REPAIR])

    def test_a_prior_gate_repair_is_observed_when_events_are_not_readable(self) -> None:
        d = self.onboard_digest()  # the fixture writes no events.jsonl
        self.assertIsNone(proposals.repair_texts_from_events(self.fx.run_dir, d, self.fx.config))
        self.assertEqual(proposals.trigger_texts(self.fx.run_dir, d, self.fx.config),
                         [REPAIR_TEXT, *LOG_LINES, PRIOR_GATE_REPAIR])

    def test_a_trigger_only_in_an_agent_written_prior_summary_is_rejected(self) -> None:
        self.fx.write_digest(self.onboard_digest())
        self.fx.add_candidate("01-x", bad_case_proposal(trigger=[PRIOR_AGENT_SUMMARY]), bad_case_md())
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 4, "candidate 01-x: no reusable trigger")
        self.assertFailure(failures, 4, 'prior run\'s gate repair (prior_runs[].last_repair, source "gate")')

    def test_a_trigger_carrying_a_prior_run_id_is_run_specific(self) -> None:
        # it would pass hook_trigger (same texts) and then never match again: match.ts sees the
        # future run's texts, which carry no old run id
        self.fx.write_digest(self.onboard_digest(f"{PRIOR_RUN_ID}: {PRIOR_GATE_REPAIR}"))
        self.fx.add_candidate("01-x", bad_case_proposal(trigger=[f"{PRIOR_RUN_ID}: RUN_REPORT_UNIT"]), bad_case_md())
        self.fx.write_declaration()
        failures = self.fx.run()
        self.assertFailure(failures, 4, "candidate 01-x: no reusable trigger")
        self.assertFailure(failures, 4, "run-specific")

    def test_a_late_unit_bad_case_passes_rule_4_on_a_prior_gate_repair(self) -> None:
        self.fx.write_digest(eval_digest_with_prior_gate_repair())
        self.fx.add_candidate("01-x", bad_case_proposal(
            target_skill="sure_infer", applies_to=["sure_infer"],
            cell={"component": "run_report", "cause": "result_layout"}, claims=[], trigger=[LATE_TRIGGER],
        ), bad_case_md())
        self.fx.write_declaration()
        self.assertNoRule(self.fx.run(), 4)

    def test_a_late_unit_bad_case_passes_the_whole_gate(self) -> None:
        # the cell binding added for claims only fires on a component this run walked, and run_report
        # is never one of them, so claims: [] is the only honest shape here
        self.fx.write_digest(eval_digest_with_prior_gate_repair())
        sha = paths.sha256_file(self.fx.artifacts / "run_digest.json")
        self.fx.add_candidate("01-x", bad_case_proposal(
            target_skill="sure_infer", applies_to=["sure_infer"],
            cell={"component": "run_report", "cause": "result_layout"}, claims=[], trigger=[LATE_TRIGGER],
            source={"run_id": RUN_ID, "skill": "sure_infer", "target": TARGET_ID, "digest_sha256": sha},
        ), bad_case_md())
        self.fx.write_declaration()
        self.assertClean(self.fx.run(sha=sha))


# --- rule 5: infra isolation --------------------------------------------------------------

class Rule5Tests(GateTestCase):
    def test_infra_noise_forces_infra_cause_on_bad_cases(self) -> None:
        self.fx.add_candidate("01-x", bad_case_proposal(), bad_case_md())
        self.fx.write_declaration(infra_noise=True, infra_evidence=["artifacts/build_env.log:1"])
        failures = self.fx.run()
        self.assertFailure(failures, 5, "candidate 01-x: cell.cause must be 'infra' when infra_noise is true, got 'cuda_version_mismatch'")
        p = bad_case_proposal()
        p["cell"]["cause"] = "infra"
        self.fx.write_json("artifacts/candidates/01-x/proposal.json", p)
        self.assertNoRule(self.fx.run(), 5)

    def test_infra_noise_needs_resolvable_infra_evidence(self) -> None:
        self.fx.write_declaration(no_new_lessons=True, no_lessons_reason="node died", infra_noise=True, infra_evidence=[])
        self.assertFailure(self.fx.run(), 5, "infra_noise is true but infra_evidence is empty")
        self.fx.write_declaration(no_new_lessons=True, no_lessons_reason="node died", infra_noise=True,
                                  infra_evidence=["vc_logs/job.log", "/var/log/syslog", "artifacts/build_env.log:9"])
        failures = self.fx.run()
        self.assertFailure(failures, 5, "infra_evidence[0] 'vc_logs/job.log' does not resolve")
        self.assertFailure(failures, 5, "infra_evidence[1] '/var/log/syslog' is an absolute")
        self.assertFailure(failures, 5, "infra_evidence[2] 'artifacts/build_env.log:9' line 9 is beyond the end")
        self.fx.write_text("vc_logs/job.log", "node gpu-3 lost heartbeat\n")
        self.fx.write_declaration(no_new_lessons=True, no_lessons_reason="node died", infra_noise=True,
                                  infra_evidence=["vc_logs/job.log:1"])
        self.assertClean(self.fx.run())

    def test_infra_noise_false_has_no_effect(self) -> None:
        self.fx.add_candidate("01-x", bad_case_proposal(), bad_case_md())
        self.fx.write_declaration(infra_noise=False, infra_evidence=[])
        self.assertNoRule(self.fx.run(), 5)

    def test_facts_are_exempt_from_the_infra_cause_rule(self) -> None:
        self.fx.add_candidate("01-x", fact_proposal(), fact_md())
        self.fx.write_declaration(infra_noise=True, infra_evidence=["artifacts/build_env.log"])
        self.assertNoRule(self.fx.run(), 5)


# --- rule 6: causal needs path:line ------------------------------------------------------

class Rule6Tests(GateTestCase):
    def test_causal_requires_a_path_line_evidence(self) -> None:
        self.fx.add_candidate("01-x", bad_case_proposal(causal=True, evidence=["artifacts/build_env.log"]), bad_case_md())
        self.fx.write_declaration()
        self.assertFailure(self.fx.run(), 6, "candidate 01-x: causal is true but no evidence entry is in path:line form")

    def test_unsafe_path_line_does_not_count(self) -> None:
        self.fx.add_candidate("01-x", bad_case_proposal(causal=True, evidence=["../x.log:3", "artifacts/build_env.log:0"]), bad_case_md())
        self.fx.write_declaration()
        self.assertFailure(self.fx.run(), 6, "candidate 01-x: causal is true but no evidence entry is in path:line form")

    def test_causal_false_is_not_checked(self) -> None:
        self.fx.add_candidate("01-x", bad_case_proposal(causal=False, evidence=["artifacts/build_env.log"]), bad_case_md())
        self.fx.write_declaration()
        self.assertNoRule(self.fx.run(), 6)


# --- repair text ---------------------------------------------------------------------------

class FormatRepairTests(unittest.TestCase):
    def test_empty_when_no_failures(self) -> None:
        self.assertEqual(proposals.format_repair([]), "")

    def test_lists_every_failure_with_rule_tag_and_trigger_hint(self) -> None:
        text = proposals.format_repair([
            proposals.GateFailure(1, "candidate 01-x: op must be one of ('add', 'modify', 'supersede'), got 'edit'"),
            proposals.GateFailure(4, "candidate 01-x: no reusable trigger"),
        ])
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("check_memory_extraction gate: 2 problem(s)"))
        self.assertIn("- [rule 1] candidate 01-x: op must be one of", lines[1])
        self.assertIn("- [rule 4] candidate 01-x: no reusable trigger", lines[2])
        self.assertIn("would appear verbatim if the same failure happened again", text)
        self.assertIn("EXTRACTION.md", text)
        self.assertIn("no_new_lessons: true", text)
        without_rule4 = proposals.format_repair([proposals.GateFailure(1, "x")])
        self.assertNotIn("would appear verbatim", without_rule4)

    def test_trigger_hint_names_the_prior_run_route_and_its_exclusion(self) -> None:
        # The hint is the only copy of rule 4 an agent blocked on a late unit reads; if it still
        # says "this run" only, that agent concludes the prior_runs route does not exist.
        text = proposals.format_repair([proposals.GateFailure(4, "candidate 01-x: no reusable trigger")])
        self.assertIn("or in a prior run's gate repair", text)
        self.assertIn("a prior run's id", text)


# --- schema files (documentation + fixture) ------------------------------------------------

def _walk_for_key(node, key: str) -> bool:
    if isinstance(node, dict):
        return key in node or any(_walk_for_key(v, key) for v in node.values())
    if isinstance(node, list):
        return any(_walk_for_key(v, key) for v in node)
    return False


def _is_type(obj, kind: str) -> bool:
    return {
        "object": isinstance(obj, dict),
        "array": isinstance(obj, list),
        "string": isinstance(obj, str),
        "boolean": isinstance(obj, bool),
        "integer": isinstance(obj, int) and not isinstance(obj, bool),
        "number": isinstance(obj, (int, float)) and not isinstance(obj, bool),
        "null": obj is None,
    }[kind]


def schema_check(obj, schema: dict, where: str = "$") -> list[str]:
    """Tiny draft-07 subset (type / enum / required / properties / items / additionalProperties)."""
    errs: list[str] = []
    types = schema.get("type")
    if types is not None:
        allowed = types if isinstance(types, list) else [types]
        if not any(_is_type(obj, t) for t in allowed):
            return [f"{where}: expected {allowed}, got {type(obj).__name__}"]
    if "enum" in schema and obj not in schema["enum"]:
        errs.append(f"{where}: {obj!r} not in {schema['enum']}")
    if isinstance(obj, dict):
        for key in schema.get("required", []):
            if key not in obj:
                errs.append(f"{where}: missing required {key}")
        props = schema.get("properties", {})
        for key, sub in props.items():
            if key in obj:
                errs.extend(schema_check(obj[key], sub, f"{where}.{key}"))
        extra = schema.get("additionalProperties", True)
        for key in obj:
            if key in props:
                continue
            if extra is False:
                errs.append(f"{where}: undeclared key {key}")
            elif isinstance(extra, dict):
                errs.extend(schema_check(obj[key], extra, f"{where}.{key}"))
    if isinstance(obj, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(obj):
            errs.extend(schema_check(item, schema["items"], f"{where}[{i}]"))
    return errs


class SchemaFilesTests(unittest.TestCase):
    NAMES = ("run_digest", "extraction_declaration", "proposal", "meta", "index")

    def load(self, name: str) -> dict:
        return json.loads((paths.LIB_DIR / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8"))

    def test_five_files_are_draft07_objects_without_const(self) -> None:
        for name in self.NAMES:
            with self.subTest(name=name):
                schema = self.load(name)
                self.assertEqual(schema["$schema"], "http://json-schema.org/draft-07/schema#")
                self.assertEqual(schema["$id"], f"{name}.schema.json")
                self.assertTrue(schema["title"])
                self.assertEqual(schema["type"], "object")
                self.assertFalse(_walk_for_key(schema, "const"), f"{name} uses const; validate.ts only knows enum")

    def test_declaration_schema_matches_module_and_unit_definition(self) -> None:
        schema = self.load("extraction_declaration")
        self.assertEqual(schema["required"], list(proposals.DECLARATION_REQUIRED))
        self.assertEqual(schema["required"], ["schema", "no_new_lessons", "no_lessons_reason", "covered_by",
                                              "candidates", "infra_noise", "infra_evidence"])
        self.assertEqual(schema["properties"]["schema"]["enum"], [proposals.DECLARATION_SCHEMA])
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(schema["properties"]["no_lessons_reason"]["type"], ["string", "null"])

    def test_proposal_schema_enums_match_module_and_config(self) -> None:
        cfg = paths.load_config()
        schema = self.load("proposal")
        props = schema["properties"]
        self.assertEqual(schema["required"], list(proposals.PROPOSAL_REQUIRED))
        self.assertEqual(props["schema"]["enum"], [proposals.PROPOSAL_SCHEMA])
        self.assertEqual(props["type"]["enum"], list(proposals.ENTRY_TYPES))
        self.assertEqual(props["op"]["enum"], list(proposals.OPS))
        self.assertEqual(props["target_skill"]["enum"], cfg["target_skills"])
        self.assertEqual(props["cell"]["properties"]["cause"]["enum"], cfg["cause_enum"])
        self.assertEqual(props["claims"]["items"]["properties"]["kind"]["enum"], list(proposals.CLAIM_KINDS))
        self.assertEqual(self.load("run_digest")["properties"]["schema"]["enum"], [proposals.DIGEST_SCHEMA])
        self.assertEqual(self.load("index")["properties"]["schema"]["enum"], ["sure.memory.index.v1"])

    def test_fixtures_validate_against_the_schemas(self) -> None:
        self.assertEqual(schema_check(declaration(candidates=["01-x"]), self.load("extraction_declaration")), [])
        self.assertEqual(schema_check(bad_case_proposal(), self.load("proposal")), [])
        self.assertEqual(schema_check(fact_proposal(), self.load("proposal")), [])
        self.assertEqual(schema_check(make_digest(), self.load("run_digest")), [])
        self.assertEqual(schema_check(make_digest(error="x"), self.load("run_digest")), [])
        broken = bad_case_proposal(op="edit")
        self.assertTrue(schema_check(broken, self.load("proposal")))
        # last_commands carries {tool, command} rows like fix_window, not bare strings
        failed_unit = copy.deepcopy(make_digest())
        failed_unit["units"][1]["outcome"] = "failed"
        failed_unit["units"][1]["last_commands"] = [{"tool": "bash", "command": "python -c 'import torch'"}]
        self.assertEqual(schema_check(failed_unit, self.load("run_digest")), [])
        failed_unit["units"][1]["last_commands"] = ["python -c 'import torch'"]
        self.assertTrue(schema_check(failed_unit, self.load("run_digest")))


# sure/runtime/memory/test_proposals.py  (Part B, appended after Task 3's tests)

B_REPO_ROOT = Path(__file__).resolve().parents[3]
B_CONFIG = paths.load_config()
B_UNITS = paths.load_units()
B_RUN_ID = "20260818-120000-abcd1234"
B_ALL_SKILLS = ["sure_onboard", "sure_eval", "sure_infer", "sure_trans", "sure_feed"]
B_BUILD_ENV_REPAIR = (
    "BUILD_ENV gate failed (status=failed). pip resolved torch==2.4.0+cu121 but the host runs CUDA 12.8; "
    "RuntimeError: no kernel image is available for execution on the device"
)
B_IMPORT_REPAIR = "VALIDATE_IMPORT gate failed: ModuleNotFoundError: No module named 'torchaudio' (import_test.status=failed)"
_B_UNSET = object()


def _b_entry(entry_id: str, **over) -> dict:
    """One index.json entry (skeleton 1.7 shape) with sensible defaults; keyword overrides."""
    skill, slug = entry_id.split("/", 1)
    base = {
        "entry_id": entry_id, "type": "bad_case", "status": "confirmed", "target_skill": skill, "applies_to": [skill],
        "component": "build_env", "cause": "cuda_version_mismatch", "trigger": ["no kernel image is available"],
        "scope": None, "title": slug.replace("-", " "),
        "path": f"sure/skills/{skill}/references/memory/bad_cases/{slug}.md", "legacy": False, "op": "add",
        "target_entry": None, "similar_entry": None, "useful_activated": 0, "useful_unattributed": 0,
        "injections": 0, "disputed": 0, "created": "2026-08-10", "checked_at": None, "stale": False,
        "superseded_by": None,
    }
    base.update(over)
    return base


def _b_messages(failures, rule: int) -> list[str]:
    return [f.message for f in failures if f.rule == rule]


class PartBFixture:
    """A fake checkout with one run that has just passed verdict (onboard) or assessment (eval).

    <root>/repo/.sure/runs/<B_RUN_ID>/{state.json, artifacts/, vc_logs/, local_logs/}
    <root>/repo/sure/models/<model_name>/            onboard target dir (model_input_resolved.json model_dir)
    <root>/repo/sure/results/.../                    eval product dir (eval_input_resolved.json runtime.run_dir)
    <root>/repo/sure/memory/                         index.json is written only by write_index_file()
    The digest follows spec 4.3: unit_a failed once then passed, unit_b ended failed, unit_c passed first time.
    """

    def __init__(self, root: Path, skill: str = "sure_onboard") -> None:
        self.repo = root / "repo"
        self.skill = skill
        if skill == "sure_onboard":
            self.unit_a, self.unit_b, self.unit_c = "build_env", "validate_import", "verdict"
        else:
            self.unit_a, self.unit_b, self.unit_c = "dataset_scope", "execute_inference", "run_report"
        self.run_dir = self.repo / ".sure" / "runs" / B_RUN_ID
        self.art = self.run_dir / "artifacts"
        self.candidates_dir = self.art / "candidates"
        self.model_name = "openai__whisper-tiny"
        self.model_dir = self.repo / "sure" / "models" / self.model_name
        self.eval_product_dir = self.repo / "sure" / "results" / "whisper-tiny" / "asr" / "main_agent_whisper-tiny_1"
        self.digest_sha: str | None = None
        self.index: dict | None = None
        self._build()

    # -- layout ---------------------------------------------------------------

    def _build(self) -> None:
        for d in (self.art, self.run_dir / "vc_logs", self.run_dir / "local_logs", self.model_dir / "configs",
                  self.eval_product_dir, self.repo / "sure" / "memory", self.art / "memory_evidence"):
            d.mkdir(parents=True, exist_ok=True)
        lines = [f"[build_env] step {i}" for i in range(1, 31)]
        lines[11] = "RuntimeError: no kernel image is available for execution on the device"
        # write_bytes: text mode would turn \r\n into \r\r\n on Windows and double the line count
        (self.art / "build_env.log").write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))  # 30 lines, CRLF
        (self.art / "build_env_result.json").write_text('{"status": "failed", "attempt": 1}\n', encoding="utf-8")
        (self.run_dir / "vc_logs" / "job.log").write_text("job line 1\njob line 2\njob line 3\n", encoding="utf-8")
        (self.run_dir / "local_logs" / "smoke_test.log").write_text("smoke ok\n", encoding="utf-8")
        (self.art / "memory_evidence" / "1.txt").write_text(
            "PARTITION   TIMELIMIT\nsite-gpu  infinite\nopenbench   6:00:00\n", encoding="utf-8")
        (self.model_dir / "README.md").write_text("# whisper tiny\n", encoding="utf-8")
        (self.model_dir / "configs" / "model.yaml").write_text("device: cuda\n", encoding="utf-8")
        (self.eval_product_dir / "report.jsonl").write_text('{"dataset": "aishell1"}\n', encoding="utf-8")
        if self.skill == "sure_onboard":
            (self.art / "model_input_resolved.json").write_text(json.dumps({
                "model_id": "openai/whisper-tiny", "model_name": self.model_name, "model_dir": str(self.model_dir),
                "task_type": "asr", "deployment_type": "local", "package_profile": "none", "repo_url": "x",
            }), encoding="utf-8")
        else:
            (self.art / "eval_input_resolved.json").write_text(json.dumps({
                "schema": "sure.eval.input_resolved.v1",
                "user_input": {"model": "whisper-tiny", "datasets": ["aishell1"]},
                "runtime": {"run_id": "main_agent_whisper-tiny_1", "run_dir": str(self.eval_product_dir)},
            }), encoding="utf-8")
        self.write_digest(self.default_digest())
        self.index = {"schema": "sure.memory.index.v1", "built_at": "2026-08-18T00:00:00Z",
                      "sources_sha256": "0" * 64, "entries": self.default_entries(), "omitted_provisional": 0}

    def default_digest(self) -> dict:
        return {
            "schema": "sure.memory.run_digest.v1",
            "run": {"run_id": B_RUN_ID, "skill": self.skill, "args": "model_input=sure/inputs/whisper-tiny.yaml",
                    "target": {"kind": "model" if self.skill == "sure_onboard" else "eval", "id": "openai/whisper-tiny"},
                    "status_so_far": "running", "cutoff": 812, "memory_usage": []},
            "units": [
                {"id": self.unit_a, "outcome": "passed", "attempts": 2,
                 "repairs": [{"attempt": 1, "text": B_BUILD_ENV_REPAIR}],
                 "fix_window": [{"tool": "bash", "command": "pip install torch==2.4.0 --index-url https://download.pytorch.org/whl/cu128"}],
                 "last_commands": [],
                 "log_tail": {"path": "{run_dir}/artifacts/build_env.log",
                              "lines": ["RuntimeError: no kernel image is available for execution on the device"]}},
                {"id": self.unit_b, "outcome": "failed", "attempts": 3,
                 "repairs": [{"attempt": 1, "text": B_IMPORT_REPAIR}, {"attempt": 2, "text": B_IMPORT_REPAIR},
                             {"attempt": 3, "text": B_IMPORT_REPAIR}],
                 "fix_window": [], "last_commands": [{"tool": "bash", "command": "python -c 'import torchaudio'"}],
                 "log_tail": {"path": "{run_dir}/artifacts/import_execution.log",
                              "lines": ["ModuleNotFoundError: No module named 'torchaudio'"]}},
                {"id": self.unit_c, "outcome": "passed", "attempts": 1, "repairs": [], "fix_window": [],
                 "last_commands": [], "log_tail": None},
            ],
            "tool_errors": 1, "prior_runs": [], "memory_index_snapshot": [],
            "units_registry": {self.skill: B_UNITS["skills"][self.skill]},
        }

    def default_entries(self) -> list[dict]:
        s = self.skill
        return [
            _b_entry(f"{s}/no-kernel-image", component=self.unit_a, cause="cuda_version_mismatch",
                     trigger=["no kernel image is available"], title="CUDA arch mismatch: no kernel image for the device"),
            _b_entry(f"{s}/torchaudio-wheel-missing", status="provisional", component=self.unit_b,
                     cause="python_dependency_missing", trigger=["No module named 'torchaudio'"],
                     title="torchaudio wheel missing from the env", created=B_RUN_ID[:8]),
            _b_entry(f"{s}/old-nvml-superseded", status="superseded", component=self.unit_a, cause="infra",
                     trigger=["Can't initialize NVML"], title="NVML init failure (old)", superseded_by=f"{s}/nvml-legacy"),
            _b_entry(f"{s}/nvml-legacy", legacy=True, component="_", cause=None, trigger=["Can't initialize NVML"],
                     title="NVML init failure on shared nodes", created="legacy"),
            _b_entry("_shared/vc-partition-names", type="fact", target_skill="_shared", applies_to=list(B_ALL_SKILLS),
                     component="_", cause="n.a.", trigger=[], scope="cluster", title="Long jobs go to site-gpu",
                     path="sure/skills/_shared/memory/facts/vc-partition-names.md"),
        ]

    # -- writers ----------------------------------------------------------------

    def write_digest(self, digest: dict, *, keep_state: bool = False) -> str:
        """Write artifacts/run_digest.json; unless keep_state, also record its sha in state.json like the hook does."""
        path = self.art / "run_digest.json"
        path.write_text(json.dumps(digest, indent=2), encoding="utf-8")
        sha = paths.sha256_file(path)
        if not keep_state:
            self.write_state(sha)
        self.digest_sha = sha
        return sha

    def write_state(self, digest_sha: str | None) -> None:
        memory = {"digestCutoff": 812, "digestSha256": digest_sha, "digestPassed": self.unit_c} if digest_sha else {}
        state = {"phase": {"id": "extract_lessons", "status": "running"},
                 "checkpoint": {"id": "extract_lessons", "label": "Extract lessons", "resumable": True,
                                "data": {"currentUnit": "extract_lessons", "completedUnits": [self.unit_a, self.unit_c],
                                         "retries": {}, "failedArtifactDigests": {}, "memory": memory}}}
        (self.run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    def write_index_file(self, index: dict | None = None) -> None:
        (self.repo / "sure" / "memory" / "index.json").write_text(
            json.dumps(self.index if index is None else index), encoding="utf-8")

    def default_proposal(self, **over) -> dict:
        p = {
            "schema": "sure.memory.proposal.v2", "type": "bad_case", "op": "add", "target_skill": self.skill,
            "target_entry": None, "applies_to": [self.skill],
            "cell": {"component": self.unit_a, "cause": "python_dependency_missing"},
            "trigger": ["resolved torch==2.4.0+cu121 but the host runs CUDA 12.8"], "causal": True,
            "evidence": ["artifacts/build_env.log:12", "artifacts/build_env_result.json"],
            "claims": [{"kind": "gate_repair", "unit": self.unit_a, "attempt": 1, "status": "failed"},
                       {"kind": "unit_result", "unit": self.unit_a, "attempt": 2, "status": "passed"}],
            "source": {"run_id": B_RUN_ID, "skill": self.skill, "target": "openai/whisper-tiny",
                       "digest_sha256": self.digest_sha},
            "similar": None, "scope": None, "checked_at": None,
        }
        p.update(over)
        return p

    def default_body(self, title: str = "Torch wheel built for a different CUDA than the host") -> str:
        return (
            f"# {title}\n\n"
            "## Trigger\n`resolved torch==2.4.0+cu121 but the host runs CUDA 12.8` in the build_env repair text.\n\n"
            f"## Affected Step\n{self.skill} / {self.unit_a}\n\n"
            "## Minimum Evidence\nartifacts/build_env.log:12\n\n"
            "## Known Mitigation\nPin the torch index URL to the wheel that matches the host CUDA before installing "
            "the rest of the requirements.\n\n"
            "## Verification\npython -c \"import torch; print(torch.version.cuda)\" prints the host CUDA version.\n"
        )

    def fact_proposal(self, **over) -> dict:
        p = self.default_proposal(
            type="fact", target_skill="_shared", applies_to=list(B_ALL_SKILLS), cell={"component": "_", "cause": "n.a."},
            trigger=["site-gpu"], causal=False, evidence=["artifacts/memory_evidence/1.txt"], claims=[],
            scope="cluster", checked_at="2026-08-18")
        p.update(over)
        return p

    def fact_body(self, title: str = "site-gpu is the partition for long jobs") -> str:
        return (f"# {title}\n\nScope: cluster\nChecked-at: 2026-08-18\nEvidence: artifacts/memory_evidence/1.txt\n\n"
                "vc info lists site-gpu with no wall-clock cap.\n")

    def write_candidate(self, cid: str, proposal: dict | None = None, body: str | None = None) -> None:
        cdir = self.candidates_dir / cid
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "proposal.json").write_text(json.dumps(proposal or self.default_proposal()), encoding="utf-8")
        (cdir / "proposal.md").write_text(body if body is not None else self.default_body(), encoding="utf-8")

    def write_declaration(self, candidates: list, **over) -> None:
        decl = {"schema": "sure.memory.extraction.v2", "no_new_lessons": not candidates,
                "no_lessons_reason": None if candidates else "clean run, nothing new",
                "covered_by": [], "candidates": candidates, "infra_noise": False, "infra_evidence": []}
        decl.update(over)
        (self.art / "extraction_declaration.json").write_text(json.dumps(decl), encoding="utf-8")

    def good(self, cid: str = "01-torch-wheel-cuda", **over) -> None:
        self.write_candidate(cid, self.default_proposal(**over))
        self.write_declaration([cid])

    # -- run ------------------------------------------------------------------------

    def check(self, index=_B_UNSET) -> list:
        idx = self.index if index is _B_UNSET else index
        return proposals.check_extraction(
            self.run_dir, self.repo, config=B_CONFIG, units=B_UNITS, index=idx,
            checkpoint_digest_sha=proposals.read_checkpoint_digest_sha(self.run_dir))

    def main_argv(self, *, repo_root: bool = True) -> list[str]:
        argv = ["--run-dir", str(self.run_dir), "--produces", str(self.art / "extraction_declaration.json")]
        if repo_root:
            argv += ["--repo-root", str(self.repo)]
        return argv


def _b_link_dir(link: Path, target: Path) -> bool:
    """Directory symlink, else a Windows junction (no privilege needed); False when neither works."""
    try:
        link.symlink_to(target, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        return proc.returncode == 0 and link.exists()
    return False


class PartBCase(unittest.TestCase):
    skill = "sure_onboard"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = PartBFixture(Path(self.tmp.name), self.skill)

    def assertRule(self, failures, rule: int, needle: str | None = None) -> None:
        hits = _b_messages(failures, rule)
        self.assertTrue(hits, f"expected a rule {rule} failure, got {[(f.rule, f.message) for f in failures]}")
        if needle is not None:
            self.assertTrue(any(needle in m for m in hits), f"no rule {rule} message contains {needle!r}: {hits}")

    def assertNoRule(self, failures, rule: int) -> None:
        self.assertEqual(_b_messages(failures, rule), [])


class PartBHelperTests(unittest.TestCase):
    def test_h1_title(self) -> None:
        self.assertEqual(proposals.h1_title("# A title \n\n## Trigger\nx\n"), "A title")
        self.assertEqual(proposals.h1_title("\n\n#Not a title\n# Real title\n"), "Real title")
        self.assertEqual(proposals.h1_title("no heading at all\n"), "")

    def test_trigger_set_normalises(self) -> None:
        self.assertEqual(proposals.trigger_set({"trigger": [" A ", "a", "b", "", 3]}), {"a", "b"})
        self.assertEqual(proposals.trigger_set({"trigger": "not a list"}), set())

    def test_jaccard_and_near_duplicate(self) -> None:
        self.assertEqual(proposals.jaccard({"a", "b"}, {"a", "b", "c", "d"}), 0.5)
        self.assertEqual(proposals.jaccard(set(), {"a"}), 0.0)
        self.assertTrue(proposals.near_duplicate("Torch wheel built for a different CUDA than the host",
                                                 "torch wheel built for a different cuda than the host machine", 0.9))
        self.assertFalse(proposals.near_duplicate("Torch wheel built for a different CUDA than the host",
                                                  "partition not found when submitting the job", 0.9))
        self.assertFalse(proposals.near_duplicate("", "", 0.9))


class PartBEvidenceRuleTests(PartBCase):
    def test_run_relative_paths_under_artifacts_vc_logs_and_local_logs_pass(self) -> None:
        self.fx.good(evidence=["artifacts/build_env.log:12", "vc_logs/job.log:3", "local_logs/smoke_test.log",
                               "artifacts/candidates/01-torch-wheel-cuda/proposal.md"])
        self.assertNoRule(self.fx.check(), 2)

    def test_target_dir_paths_pass_for_onboard(self) -> None:
        self.fx.good(evidence=["README.md", "configs/model.yaml:1"])
        self.assertNoRule(self.fx.check(), 2)

    def test_missing_file_fails(self) -> None:
        self.fx.good(evidence=["artifacts/does_not_exist.log"])
        self.assertRule(self.fx.check(), 2, "does_not_exist.log")

    def test_absolute_and_parent_paths_fail(self) -> None:
        self.fx.good(evidence=[str(self.fx.art / "build_env.log"), "artifacts/../../secret.txt"])
        failures = self.fx.check()
        self.assertEqual(len(_b_messages(failures, 2)), 2)
        self.assertRule(failures, 2, "relative")

    def test_line_number_must_be_in_range(self) -> None:
        self.fx.good(evidence=["artifacts/build_env.log:30"])
        self.assertNoRule(self.fx.check(), 2)
        self.fx.good(evidence=["artifacts/build_env.log:31"])
        self.assertRule(self.fx.check(), 2, "31")

    def test_bad_evidence_entry_type_fails(self) -> None:
        self.fx.good(evidence=["artifacts/build_env.log", 12, ""])
        self.assertEqual(len(_b_messages(self.fx.check(), 2)), 2)

    def test_run_artifact_reached_through_a_link_outside_the_run_is_rejected(self) -> None:
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("nope\n", encoding="utf-8")
        link = self.fx.art / "linked"
        if not _b_link_dir(link, outside) or link.resolve() != outside.resolve():
            self.skipTest("this host cannot create a resolvable directory link")
        self.fx.good(evidence=["artifacts/linked/secret.txt"])
        self.assertRule(self.fx.check(), 2, "linked/secret.txt")

    def test_model_dir_link_to_nfs_is_accepted_lexically(self) -> None:
        nfs = Path(self.tmp.name) / "nfs"
        nfs.mkdir()
        (nfs / "config.json").write_text("{}\n", encoding="utf-8")
        link = self.fx.model_dir / "weights"
        if not _b_link_dir(link, nfs):
            self.skipTest("this host cannot create a directory link")
        self.fx.good(evidence=["weights/config.json"])
        self.assertNoRule(self.fx.check(), 2)

    def test_product_dirs_for_reads_model_dir_or_falls_back_to_model_name(self) -> None:
        self.assertEqual(proposals.product_dirs_for(self.fx.run_dir, self.fx.repo), [self.fx.model_dir])
        (self.fx.art / "model_input_resolved.json").write_text(json.dumps({"model_name": "other__model"}), encoding="utf-8")
        self.assertEqual(proposals.product_dirs_for(self.fx.run_dir, self.fx.repo),
                         [self.fx.repo / "sure" / "models" / "other__model"])
        (self.fx.art / "model_input_resolved.json").unlink()
        self.assertEqual(proposals.product_dirs_for(self.fx.run_dir, self.fx.repo), [])

    def test_evidence_bases_lists_run_root_then_lexical_product_dirs(self) -> None:
        self.fx.write_declaration([])
        ctx = proposals.build_context(self.fx.run_dir, self.fx.repo, config=B_CONFIG, units=B_UNITS, index=None,
                                      checkpoint_digest_sha=None, declaration={})
        self.assertEqual(proposals.evidence_bases(ctx), [(self.fx.run_dir, "resolve"), (self.fx.model_dir, "lexical")])


class PartBEvalEvidenceRuleTests(PartBCase):
    skill = "sure_infer"

    def test_eval_product_dir_comes_from_runtime_run_dir(self) -> None:
        self.assertEqual(proposals.product_dirs_for(self.fx.run_dir, self.fx.repo), [self.fx.eval_product_dir])
        self.fx.good(evidence=["report.jsonl:1", "vc_logs/job.log"])
        self.assertNoRule(self.fx.check(), 2)

    def test_eval_without_resolved_input_has_only_the_run_root(self) -> None:
        (self.fx.art / "eval_input_resolved.json").unlink()
        self.fx.good(evidence=["report.jsonl"])
        self.assertRule(self.fx.check(), 2, "report.jsonl")


class PartBClaimsRuleTests(PartBCase):
    def test_matching_claims_pass(self) -> None:
        self.fx.good()
        self.assertNoRule(self.fx.check(), 3)

    def test_unit_result_on_the_failed_unit_passes(self) -> None:
        self.fx.good(claims=[{"kind": "unit_result", "unit": self.fx.unit_b, "attempt": 3, "status": "failed"}])
        self.assertNoRule(self.fx.check(), 3)

    def test_unknown_unit_fails(self) -> None:
        self.fx.good(claims=[{"kind": "unit_result", "unit": "no_such_unit", "attempt": 1, "status": "passed"}])
        self.assertRule(self.fx.check(), 3, "no_such_unit")

    def test_unit_result_status_must_match_outcome(self) -> None:
        self.fx.good(claims=[{"kind": "unit_result", "unit": self.fx.unit_a, "attempt": 2, "status": "failed"}])
        self.assertRule(self.fx.check(), 3, "outcome")

    def test_unit_result_attempt_must_equal_attempts(self) -> None:
        self.fx.good(claims=[{"kind": "unit_result", "unit": self.fx.unit_a, "attempt": 1, "status": "passed"}])
        self.assertRule(self.fx.check(), 3, "attempts")

    def test_gate_repair_attempt_must_exist(self) -> None:
        self.fx.good(claims=[{"kind": "gate_repair", "unit": self.fx.unit_a, "attempt": 2, "status": "failed"}])
        self.assertRule(self.fx.check(), 3, "repairs")

    def test_gate_repair_status_must_be_failed(self) -> None:
        self.fx.good(claims=[{"kind": "gate_repair", "unit": self.fx.unit_a, "attempt": 1, "status": "passed"}])
        self.assertRule(self.fx.check(), 3, "failed")

    def test_unknown_kind_and_bad_attempt_type_fail(self) -> None:
        self.fx.good(claims=[{"kind": "hunch", "unit": self.fx.unit_a, "attempt": 1, "status": "failed"},
                             {"kind": "gate_repair", "unit": self.fx.unit_a, "attempt": "1", "status": "failed"},
                             "not an object"])
        self.assertEqual(len(_b_messages(self.fx.check(), 3)), 3)

    def test_error_digest_makes_claims_unverifiable(self) -> None:
        self.fx.good()
        self.fx.write_digest({"schema": "sure.memory.run_digest.v1", "error": "events.jsonl unreadable"})
        self.assertRule(self.fx.check(), 3, "no_new_lessons")

    def test_empty_claims_are_not_a_rule_3_problem(self) -> None:
        self.fx.write_candidate("01-fact", self.fx.fact_proposal(), self.fx.fact_body())
        self.fx.write_declaration(["01-fact"])
        self.assertNoRule(self.fx.check(), 3)


class PartBDedupRuleTests(PartBCase):
    def test_add_into_a_confirmed_cell_is_rejected(self) -> None:
        self.fx.good(cell={"component": self.fx.unit_a, "cause": "cuda_version_mismatch"})
        self.assertRule(self.fx.check(), 7, f"{self.skill}/no-kernel-image")

    def test_covered_by_does_not_free_an_occupied_cell_and_the_repair_says_so(self) -> None:
        # covered_by is honoured only for legacy trigger clashes, so a repair that offers it as an
        # alternative to modify/supersede sends the agent into its last retry for nothing.
        occupant = f"{self.skill}/no-kernel-image"
        cell = {"component": self.fx.unit_a, "cause": "cuda_version_mismatch"}
        self.fx.good(cell=cell)
        plain = _b_messages(self.fx.check(), 7)
        self.fx.write_declaration(["01-torch-wheel-cuda"], covered_by=[occupant])
        self.assertEqual(_b_messages(self.fx.check(), 7), plain)
        self.assertIn("remove this candidate and list", plain[0])

        self.fx.good(trigger=["No Kernel Image Is Available"])
        identical = _b_messages(self.fx.check(), 7)
        self.fx.write_declaration(["01-torch-wheel-cuda"], covered_by=[occupant])
        self.assertEqual(_b_messages(self.fx.check(), 7), identical)
        self.assertIn("remove this candidate and list", identical[0])

    def test_the_occupied_cell_repair_passes_when_it_is_followed(self) -> None:
        occupant = f"{self.skill}/no-kernel-image"
        self.fx.good(cell={"component": self.fx.unit_a, "cause": "cuda_version_mismatch"})
        shutil.rmtree(self.fx.candidates_dir / "01-torch-wheel-cuda")
        self.fx.write_declaration([], covered_by=[occupant],
                                  no_lessons_reason=f"the only lesson this run taught is already {occupant}")
        self.assertEqual(self.fx.check(), [])

    def test_modify_of_the_occupant_is_allowed(self) -> None:
        self.fx.good(op="modify", target_entry=f"{self.skill}/no-kernel-image",
                     cell={"component": self.fx.unit_a, "cause": "cuda_version_mismatch"},
                     trigger=["no kernel image is available"])
        self.assertNoRule(self.fx.check(), 7)

    def test_superseded_entry_does_not_occupy_its_cell(self) -> None:
        self.fx.good(cell={"component": self.fx.unit_a, "cause": "infra"})
        self.assertNoRule(self.fx.check(), 7)

    def test_add_into_a_provisional_cell_needs_similar_pointing_at_the_occupant(self) -> None:
        cell = {"component": self.fx.unit_b, "cause": "python_dependency_missing"}
        self.fx.good(cell=cell)
        self.assertRule(self.fx.check(), 7, "torchaudio-wheel-missing")
        self.fx.good(cell=cell, similar={"entry": f"{self.skill}/no-kernel-image", "difference": "other cause"})
        self.assertRule(self.fx.check(), 7, "torchaudio-wheel-missing")
        self.fx.good(cell=cell, similar={"entry": f"{self.skill}/torchaudio-wheel-missing", "difference": ""})
        self.assertRule(self.fx.check(), 7, "difference")
        self.fx.good(cell=cell, similar={"entry": f"{self.skill}/torchaudio-wheel-missing",
                                         "difference": "this one is about the wheel index, not the package"})
        self.assertNoRule(self.fx.check(), 7)

    def test_a_confirmed_occupant_that_can_never_inject_does_not_hold_the_cell_shut(self) -> None:
        # matchBadCases needs a hook trigger, so a confirmed entry with none can never be injected.
        # Seven of the twelve occupied cells in the real index are held only by such entries, and
        # each of them refuses a live lesson for its cell in favour of one that will never fire. A
        # dead occupant is treated like a provisional one: the add passes once it names the occupant
        # and says what differs.
        dead = f"{self.skill}/verdict-metric-bypass-dead"
        self.fx.index["entries"].append(_b_entry(
            dead, component=self.fx.unit_c, cause="metric_bypass", trigger=[],
            title="scores copied from a stale report instead of being recomputed"))
        cell = {"component": self.fx.unit_c, "cause": "metric_bypass"}
        self.fx.good(cell=cell)
        messages = _b_messages(self.fx.check(), 7)
        self.assertTrue(any(dead in m for m in messages), messages)
        self.assertFalse(any("occupied by confirmed" in m for m in messages), messages)
        self.fx.good(cell=cell, similar={"entry": dead, "difference": "this one is about the cached metric file"})
        self.assertNoRule(self.fx.check(), 7)

    def test_a_confirmed_occupant_that_can_inject_still_holds_the_cell_shut(self) -> None:
        # Control for the case above: naming the occupant and saying what differs frees a cell held
        # by a dead entry, and must not free one held by an entry the hooks can still select.
        occupant = f"{self.skill}/no-kernel-image"
        self.fx.good(cell={"component": self.fx.unit_a, "cause": "cuda_version_mismatch"},
                     similar={"entry": occupant, "difference": "this one is about the wheel index"})
        self.assertRule(self.fx.check(), 7, f"occupied by confirmed entry {occupant}")

    def test_occupancy_reads_the_indexer_s_never_injected_predicate(self) -> None:
        # One definition of "can never be injected", and it lives in index.py. A second copy in the
        # gate would drift from it, so the gate has to call that one: patching it moves the gate.
        from memory import index as index_mod
        self.fx.good(cell={"component": self.fx.unit_a, "cause": "cuda_version_mismatch"})
        self.assertRule(self.fx.check(), 7, "occupied by confirmed")
        with patch.object(index_mod, "never_injected", lambda entry: True):
            self.assertFalse(any("occupied by confirmed" in m for m in _b_messages(self.fx.check(), 7)))

    def test_identical_trigger_set_is_rejected_for_add(self) -> None:
        self.fx.good(trigger=["No Kernel Image Is Available"])
        failures = self.fx.check()
        self.assertRule(failures, 7, "identical")
        self.assertEqual(len(_b_messages(failures, 7)), 1)

    def test_subset_and_jaccard_overlap_require_similar(self) -> None:
        self.fx.index["entries"].append(_b_entry(
            f"{self.skill}/two-triggers", status="provisional", component=self.fx.unit_a, cause="resource_limit",
            trigger=["alpha trigger one", "beta trigger two"], title="two triggers entry"))
        self.fx.good(trigger=["alpha trigger one"])
        self.assertRule(self.fx.check(), 7, "two-triggers")
        self.fx.good(trigger=["alpha trigger one", "beta trigger two", "gamma trigger three"])
        self.assertRule(self.fx.check(), 7, "two-triggers")
        self.fx.good(trigger=["alpha trigger one", "gamma trigger three", "delta trigger four"])
        self.assertNoRule(self.fx.check(), 7)
        self.fx.good(trigger=["alpha trigger one"],
                     similar={"entry": f"{self.skill}/two-triggers", "difference": "narrower: only the alpha case"})
        self.assertNoRule(self.fx.check(), 7)

    def test_near_duplicate_title_requires_similar(self) -> None:
        self.fx.index["entries"].append(_b_entry(
            f"{self.skill}/torch-wheel-cuda-host", status="provisional", component=self.fx.unit_a,
            cause="runtime_backend_incompatible", trigger=["completely different trigger text"],
            title="Torch wheel built for a different CUDA than the host machine"))
        self.fx.good()
        self.assertRule(self.fx.check(), 7, "torch-wheel-cuda-host")
        self.fx.good(similar={"entry": f"{self.skill}/torch-wheel-cuda-host", "difference": "cu121 vs cu118 wheel"})
        self.assertNoRule(self.fx.check(), 7)

    def test_overlap_in_another_component_is_ignored(self) -> None:
        self.fx.index["entries"].append(_b_entry(
            f"{self.skill}/other-component", component=self.fx.unit_b, cause="resource_limit",
            trigger=["resolved torch==2.4.0+cu121 but the host runs CUDA 12.8", "another long trigger here"],
            title="Torch wheel built for a different CUDA than the host machine"))
        self.fx.good()
        self.assertNoRule(self.fx.check(), 7)

    def test_same_batch_identical_and_overlapping_candidates_are_rejected(self) -> None:
        self.fx.write_candidate("01-a", self.fx.default_proposal())
        self.fx.write_candidate("02-b", self.fx.default_proposal(cell={"component": self.fx.unit_a, "cause": "resource_limit"}))
        self.fx.write_declaration(["01-a", "02-b"])
        self.assertRule(self.fx.check(), 7, "01-a and 02-b")
        self.fx.write_candidate("02-b", self.fx.default_proposal(
            trigger=["resolved torch==2.4.0+cu121 but the host runs CUDA 12.8", "another long trigger here"],
            cell={"component": self.fx.unit_a, "cause": "resource_limit"}), self.fx.default_body("A different title"))
        self.assertRule(self.fx.check(), 7, "01-a and 02-b")
        self.fx.write_candidate("02-b", self.fx.default_proposal(
            trigger=["another long trigger here"], cell={"component": self.fx.unit_b, "cause": "resource_limit"}),
            self.fx.default_body("A different title"))
        self.assertNoRule(self.fx.check(), 7)

    def test_legacy_trigger_must_be_named_in_similar_or_covered_by(self) -> None:
        trig = ["resolved torch==2.4.0+cu121 but the host runs CUDA 12.8", "can't initialize nvml"]
        self.fx.good(trigger=trig)
        self.assertRule(self.fx.check(), 7, "nvml-legacy")
        self.fx.good(trigger=trig)
        self.fx.write_declaration(["01-torch-wheel-cuda"], covered_by=[f"{self.skill}/nvml-legacy"])
        self.assertNoRule(self.fx.check(), 7)
        self.fx.good(trigger=trig, similar={"entry": f"{self.skill}/nvml-legacy", "difference": "adds the torch angle"})
        self.assertNoRule(self.fx.check(), 7)

    def test_similar_entry_must_exist_in_the_index(self) -> None:
        self.fx.good(similar={"entry": f"{self.skill}/ghost", "difference": "x"})
        self.assertRule(self.fx.check(), 7, "ghost")

    def test_without_an_index_similar_is_unverifiable_but_plain_add_passes(self) -> None:
        self.fx.good()
        self.assertNoRule(self.fx.check(index=None), 7)
        self.fx.good(similar={"entry": f"{self.skill}/no-kernel-image", "difference": "x"})
        self.assertRule(self.fx.check(index=None), 7, "index unavailable")


class PartBTargetRuleTests(PartBCase):
    def test_modify_needs_an_existing_target_entry(self) -> None:
        self.fx.good(op="modify", target_entry=None)
        self.assertRule(self.fx.check(), 8, "target_entry")
        self.fx.good(op="supersede", target_entry=f"{self.skill}/ghost")
        self.assertRule(self.fx.check(), 8, "ghost")
        self.fx.good(op="modify", target_entry=f"{self.skill}/no-kernel-image")
        self.assertNoRule(self.fx.check(), 8)

    def test_add_must_leave_target_entry_null(self) -> None:
        self.fx.good(target_entry=f"{self.skill}/no-kernel-image")
        self.assertRule(self.fx.check(), 8, "null")

    def test_bad_case_applies_to_must_equal_target_skill(self) -> None:
        self.fx.good(applies_to=["sure_onboard", "sure_eval"])
        self.assertRule(self.fx.check(), 8, "applies_to")
        self.fx.good(applies_to=[])
        self.assertRule(self.fx.check(), 8, "applies_to")
        self.fx.write_candidate("01-fact", self.fx.fact_proposal(), self.fx.fact_body())
        self.fx.write_declaration(["01-fact"])
        self.assertNoRule(self.fx.check(), 8)

    def test_target_entry_unverifiable_without_index(self) -> None:
        self.fx.good(op="modify", target_entry=f"{self.skill}/no-kernel-image")
        self.assertRule(self.fx.check(index=None), 8, "index unavailable")

    def test_target_entry_must_belong_to_target_skill(self) -> None:
        # cli confirm on a modify marks target_entry superseded, so a cross-skill target retires
        # another skill's entry and files the replacement under this skill's cell.
        for op in ("modify", "supersede"):
            with self.subTest(op=op):
                self.fx.good(op=op, target_entry="_shared/vc-partition-names")
                self.assertRule(self.fx.check(), 8, "_shared")
        self.fx.good(op="modify", target_entry=f"{self.skill}/no-kernel-image")
        self.assertNoRule(self.fx.check(), 8)


class PartBSourceRuleTests(PartBCase):
    def test_three_way_sha_match_passes(self) -> None:
        self.fx.good()
        self.assertNoRule(self.fx.check(), 9)

    def test_wrong_run_id_fails(self) -> None:
        self.fx.good(source={"run_id": "20260101-000000-deadbeef", "skill": self.skill, "target": "openai/whisper-tiny",
                             "digest_sha256": self.fx.digest_sha})
        self.assertRule(self.fx.check(), 9, "source.run_id")

    def test_wrong_source_sha_fails(self) -> None:
        self.fx.good(source={"run_id": B_RUN_ID, "skill": self.skill, "target": "openai/whisper-tiny",
                             "digest_sha256": "0" * 64})
        self.assertRule(self.fx.check(), 9, "sha256sum")

    def test_checkpoint_without_digest_sha_fails(self) -> None:
        self.fx.good()
        self.fx.write_state(None)
        self.assertRule(self.fx.check(), 9, "checkpoint")

    def test_digest_rewritten_after_the_hook_built_it_fails(self) -> None:
        self.fx.good()
        digest = self.fx.default_digest()
        digest["tool_errors"] = 2
        self.fx.write_digest(digest, keep_state=True)
        self.assertRule(self.fx.check(), 9, "rewritten")

    def test_missing_digest_file_fails_only_when_candidates_exist(self) -> None:
        self.fx.good()
        (self.fx.art / "run_digest.json").unlink()
        self.assertRule(self.fx.check(), 9, "missing")
        self.fx.write_declaration([])
        for name in ("proposal.json", "proposal.md"):
            (self.fx.candidates_dir / "01-torch-wheel-cuda" / name).unlink()
        (self.fx.candidates_dir / "01-torch-wheel-cuda").rmdir()
        self.assertNoRule(self.fx.check(), 9)


class PartBDeclarationRuleTests(PartBCase):
    def test_clean_no_new_lessons_declaration_passes_every_rule(self) -> None:
        self.fx.write_declaration([])
        self.assertEqual(self.fx.check(), [])

    def test_good_candidate_passes_every_rule(self) -> None:
        self.fx.good()
        self.assertEqual(self.fx.check(), [])

    def test_no_new_lessons_true_forbids_candidates_and_needs_a_reason(self) -> None:
        self.fx.good()
        self.fx.write_declaration(["01-torch-wheel-cuda"], no_new_lessons=True, no_lessons_reason="x")
        self.assertRule(self.fx.check(), 10, "not empty")
        self.fx.write_declaration([], no_lessons_reason="  ")
        self.assertRule(self.fx.check(), 10, "no_lessons_reason")

    def test_no_new_lessons_false_needs_candidates(self) -> None:
        self.fx.write_declaration([], no_new_lessons=False, no_lessons_reason=None)
        self.assertRule(self.fx.check(), 10, "candidates is empty")

    def test_undeclared_candidate_dir_on_disk_fails(self) -> None:
        self.fx.good()
        (self.fx.candidates_dir / "02-stray").mkdir()
        self.assertRule(self.fx.check(), 10, "02-stray")
        self.fx.write_declaration([])
        self.assertRule(self.fx.check(), 10, "01-torch-wheel-cuda")

    def test_duplicate_ids_and_more_than_five_candidates_fail(self) -> None:
        self.fx.good()
        self.fx.write_declaration(["01-torch-wheel-cuda", "01-torch-wheel-cuda"])
        self.assertRule(self.fx.check(), 10, "twice")
        ids = []
        for i in range(6):
            cid = f"0{i + 1}-cand"
            self.fx.write_candidate(cid, self.fx.default_proposal(trigger=[f"distinct trigger number {i} here"]),
                                    self.fx.default_body(f"Distinct title number {i}"))
            ids.append(cid)
        self.fx.write_declaration(ids)
        self.assertRule(self.fx.check(), 10, "at most 5")

    def test_error_digest_only_accepts_no_new_lessons(self) -> None:
        self.fx.good()
        self.fx.write_digest({"schema": "sure.memory.run_digest.v1", "error": "events.jsonl unreadable"})
        self.assertRule(self.fx.check(), 10, "events.jsonl unreadable")
        self.fx.write_declaration([], no_lessons_reason="digest error: events.jsonl unreadable")
        for name in ("proposal.json", "proposal.md"):
            (self.fx.candidates_dir / "01-torch-wheel-cuda" / name).unlink()
        (self.fx.candidates_dir / "01-torch-wheel-cuda").rmdir()
        self.assertEqual(self.fx.check(), [])

    def test_missing_declaration_file_is_reported_by_part_a(self) -> None:
        self.assertRule(self.fx.check(), 1, "extraction_declaration.json")


class PartBMainTests(PartBCase):
    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = proposals.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_failure_prints_repair_to_stderr_and_exits_1(self) -> None:
        self.fx.good(source={"run_id": "other-run", "skill": self.skill, "target": "openai/whisper-tiny",
                             "digest_sha256": self.fx.digest_sha})
        self.fx.write_index_file()
        rc, out, err = self._run(self.fx.main_argv())
        self.assertEqual(rc, 1)
        self.assertIn("source.run_id", err)
        self.assertEqual(out, "")

    def test_clean_declaration_exits_0_with_ok_on_stdout(self) -> None:
        self.fx.write_declaration([])
        rc, out, err = self._run(self.fx.main_argv())
        self.assertEqual(rc, 0, err)
        self.assertIn("check_memory_extraction OK", out)

    def test_main_reads_checkpoint_sha_from_state_json(self) -> None:
        self.fx.good()
        self.fx.write_state(None)
        rc, _out, err = self._run(self.fx.main_argv())
        self.assertEqual(rc, 1)
        self.assertIn("checkpoint", err)

    def test_missing_produces_file_exits_1(self) -> None:
        rc, _out, err = self._run(self.fx.main_argv())
        self.assertEqual(rc, 1)
        self.assertIn("extraction_declaration.json", err)

    def test_index_json_on_disk_is_used_and_unknown_schema_is_ignored(self) -> None:
        self.fx.good(similar={"entry": f"{self.skill}/no-kernel-image", "difference": "x"})
        self.fx.write_index_file()
        rc, _out, err = self._run(self.fx.main_argv())
        self.assertNotIn("index unavailable", err)
        self.fx.write_index_file({"schema": "sure.memory.index.v0", "entries": []})
        rc, _out, err = self._run(self.fx.main_argv())
        self.assertEqual(rc, 1)
        self.assertIn("index unavailable", err)

    def test_default_repo_root_is_the_checkout_holding_the_library(self) -> None:
        self.assertEqual(proposals.default_repo_root(), B_REPO_ROOT)

    def test_read_checkpoint_digest_sha_tolerates_missing_or_odd_state(self) -> None:
        self.assertEqual(proposals.read_checkpoint_digest_sha(self.fx.run_dir), self.fx.digest_sha)
        (self.fx.run_dir / "state.json").write_text('{"checkpoint": {"data": {"memory": {"digestSha256": 5}}}}', encoding="utf-8")
        self.assertIsNone(proposals.read_checkpoint_digest_sha(self.fx.run_dir))
        (self.fx.run_dir / "state.json").unlink()
        self.assertIsNone(proposals.read_checkpoint_digest_sha(self.fx.run_dir))


class PartBWrapperAndSchemaCopyTests(unittest.TestCase):
    ONBOARD = B_REPO_ROOT / "sure" / "skills" / "sure_onboard"
    INFER = B_REPO_ROOT / "sure" / "skills" / "sure_infer"
    TRANS = B_REPO_ROOT / "sure" / "skills" / "sure_trans"
    FEED = B_REPO_ROOT / "sure" / "skills" / "sure_feed"

    def test_every_wrapper_exists_and_is_identical(self) -> None:
        a = (self.ONBOARD / "scripts" / "check_memory_extraction.py").read_bytes()
        for skill_dir in (self.INFER, self.TRANS, self.FEED):
            with self.subTest(skill=skill_dir.name):
                self.assertEqual((skill_dir / "scripts" / "check_memory_extraction.py").read_bytes(), a)
        self.assertIn(b"from memory import proposals", a)

    def test_wrapper_runs_the_gate_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = PartBFixture(Path(tmp))
            fx.write_declaration([])
            wrapper = self.ONBOARD / "scripts" / "check_memory_extraction.py"
            proc = subprocess.run([sys.executable, str(wrapper), *fx.main_argv()], capture_output=True, text=True,
                                  cwd=str(self.ONBOARD))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("check_memory_extraction OK", proc.stdout)
            fx.good(source={"run_id": "other", "skill": "sure_onboard", "target": "x", "digest_sha256": fx.digest_sha})
            proc = subprocess.run([sys.executable, str(wrapper), *fx.main_argv()], capture_output=True, text=True,
                                  cwd=str(self.ONBOARD))
            self.assertEqual(proc.returncode, 1)
            self.assertIn("source.run_id", proc.stderr)

    def test_schema_copies_are_byte_identical_to_the_shared_library(self) -> None:
        source = (paths.LIB_DIR / "schemas" / "extraction_declaration.schema.json").read_bytes()
        for skill_dir in (self.ONBOARD, self.INFER, self.TRANS, self.FEED):
            with self.subTest(skill=skill_dir.name):
                self.assertEqual((skill_dir / "schemas" / "extraction_declaration.schema.json").read_bytes(), source)
        schema = json.loads(source)
        self.assertEqual(schema["properties"]["schema"]["enum"], ["sure.memory.extraction.v2"])
        self.assertNotIn(b'"const"', source)
        self.assertEqual(schema["required"], ["schema", "no_new_lessons", "no_lessons_reason", "covered_by",
                                              "candidates", "infra_noise", "infra_evidence"])
        self.assertIs(schema["additionalProperties"], False)


# --- fix round: config/units read failures must not leak the host path (finding 1); rule 7's
# same-batch exact-trigger-set check is deliberately unscoped by cell (finding 2, docs only) -----

class PartCFixRoundTests(PartBCase):
    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = proposals.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_unreadable_config_json_does_not_leak_its_absolute_path(self) -> None:
        self.fx.write_declaration([])
        bogus_path = str(Path(self.tmp.name) / "nowhere" / "config.json")
        with patch.object(proposals.paths, "load_config",
                          side_effect=FileNotFoundError(f"[Errno 2] No such file or directory: '{bogus_path}'")):
            rc, out, err = self._run(self.fx.main_argv())
        self.assertEqual(rc, 1)
        self.assertNotIn(bogus_path, err)
        self.assertNotIn(str(Path(self.tmp.name)), err)
        self.assertIn("config.json", err)
        self.assertEqual(out, "")

    def test_unreadable_units_json_does_not_leak_its_absolute_path(self) -> None:
        self.fx.write_declaration([])
        bogus_path = str(Path(self.tmp.name) / "nowhere" / "units.json")
        with patch.object(proposals.paths, "load_units",
                          side_effect=FileNotFoundError(f"[Errno 2] No such file or directory: '{bogus_path}'")):
            rc, out, err = self._run(self.fx.main_argv())
        self.assertEqual(rc, 1)
        self.assertNotIn(bogus_path, err)
        self.assertNotIn(str(Path(self.tmp.name)), err)
        self.assertIn("units.json", err)
        self.assertEqual(out, "")

    def test_same_batch_identical_triggers_across_different_cells_are_still_rejected(self) -> None:
        # 01-a and 02-b differ in cell.component (and therefore would be skipped by the overlap
        # branch's skill/component scoping a few lines below), but their trigger sets are
        # identical; rule 7 must still reject the pair.
        self.fx.write_candidate("01-a", self.fx.default_proposal())
        self.fx.write_candidate("02-b", self.fx.default_proposal(
            cell={"component": self.fx.unit_b, "cause": "resource_limit"}))
        self.fx.write_declaration(["01-a", "02-b"])
        failures = self.fx.check()
        self.assertRule(failures, 7, "01-a and 02-b")
        self.assertRule(failures, 7, "have the same trigger set; merge them")


if __name__ == "__main__":
    unittest.main()
