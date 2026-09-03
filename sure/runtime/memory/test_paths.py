from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # sure/runtime

from memory import paths  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]


class SlugifyTests(unittest.TestCase):
    def test_lowercases_and_joins_runs_of_non_alnum(self) -> None:
        self.assertEqual(paths.slugify("  CUDA arch mismatch: no kernel image!  ", "fb"), "cuda-arch-mismatch-no-kernel-image")

    def test_chinese_title_falls_back(self) -> None:
        self.assertEqual(paths.slugify("显卡架构不匹配", "a1b2c3d4-01"), "a1b2c3d4-01")

    def test_truncates_to_sixty_chars_without_trailing_dash(self) -> None:
        slug = paths.slugify("x" * 59 + " y" + "z" * 20, "fb")
        self.assertLessEqual(len(slug), 60)
        self.assertFalse(slug.endswith("-"))

    def test_split_entry_id(self) -> None:
        self.assertEqual(paths.split_entry_id("sure_onboard/no-kernel-image"), ("sure_onboard", "no-kernel-image"))
        for bad in ("nope", "a/b/c", "../x/y", "sure_onboard/", "/slug", "sure_onboard/Bad Slug"):
            with self.subTest(bad=bad):
                self.assertIsNone(paths.split_entry_id(bad))


class AtomicWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_writes_text_and_leaves_no_temp_file(self) -> None:
        target = self.root / "nested" / "dir" / "file.md"
        paths.atomic_write_text(target, "hello\n")
        self.assertEqual(target.read_text(encoding="utf-8"), "hello\n")
        leftovers = [p for p in target.parent.iterdir() if p.name != "file.md"]
        self.assertEqual(leftovers, [])

    def test_json_is_utf8_indented_with_trailing_newline(self) -> None:
        target = self.root / "x.json"
        paths.atomic_write_json(target, {"b": 1, "a": "中文"})
        raw = target.read_text(encoding="utf-8")
        self.assertTrue(raw.endswith("}\n"))
        self.assertIn('"a": "中文"', raw)
        self.assertEqual(json.loads(raw), {"b": 1, "a": "中文"})

    def test_overwrite_replaces_content(self) -> None:
        target = self.root / "x.txt"
        paths.atomic_write_text(target, "one")
        paths.atomic_write_text(target, "two")
        self.assertEqual(target.read_text(encoding="utf-8"), "two")

    def test_group_writable_never_raises(self) -> None:
        target = self.root / "x.txt"
        target.write_text("x", encoding="utf-8")
        paths.group_writable(target)          # file
        paths.group_writable(self.root)       # dir
        paths.group_writable(self.root / "missing")  # missing path: silent


class JsonlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "usage" / "run-1.jsonl"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_append_then_read_round_trip(self) -> None:
        paths.append_jsonl(self.path, {"kind": "inject", "n": 1}, 4096)
        paths.append_jsonl(self.path, {"kind": "settle", "n": 2}, 4096)
        rows, bad = paths.read_jsonl(self.path)
        self.assertEqual(bad, 0)
        self.assertEqual([r["n"] for r in rows], [1, 2])
        self.assertEqual(self.path.read_text(encoding="utf-8").count("\n"), 2)

    def test_read_skips_broken_lines_and_counts_them(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"ok": 1}\n{not json\n[1,2]\n{"ok": 2}\n{"trunc', encoding="utf-8")
        rows, bad = paths.read_jsonl(self.path)
        self.assertEqual([r["ok"] for r in rows], [1, 2])
        self.assertEqual(bad, 3)

    def test_read_missing_file_is_empty(self) -> None:
        self.assertEqual(paths.read_jsonl(self.path), ([], 0))

    def test_append_rejects_oversized_line(self) -> None:
        with self.assertRaises(ValueError):
            paths.append_jsonl(self.path, {"blob": "x" * 5000}, 4096)
        self.assertFalse(self.path.exists())


class LockAndTreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "sure" / "memory"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_ensure_memory_tree_creates_layout(self) -> None:
        paths.ensure_memory_tree(self.root)
        for name in ("provisional", "outbox", "meta", "usage", "digests", "rejected"):
            self.assertTrue((self.root / name).is_dir(), name)
        self.assertTrue((self.root / ".lock").exists())
        paths.ensure_memory_tree(self.root)  # idempotent

    def test_fix_perms_walks_past_a_file_that_vanishes_mid_walk(self) -> None:
        # fix-perms repairs a tree other processes are writing: an atomic_write_bytes temp file or a
        # concurrent promote can remove a path between rglob and stat. Aborting there leaves half the
        # tree unfixed and prints an absolute path.
        paths.ensure_memory_tree(self.root)
        for name in ("a.json", "vanishing.json", "z.json"):
            (self.root / "provisional" / name).write_text("{}", encoding="utf-8")
        real_stat = os.stat

        def racing_stat(path, *args, **kwargs):
            if str(path).endswith("vanishing.json"):
                raise FileNotFoundError(2, "No such file or directory")
            return real_stat(path, *args, **kwargs)

        seen: list[str] = []
        real_group_writable = paths.group_writable

        def spy(path):
            seen.append(Path(path).name)
            return real_group_writable(path)

        with patch.object(paths.os, "stat", side_effect=racing_stat), patch.object(paths, "group_writable", side_effect=spy):
            failed = paths.fix_perms(self.root)
        self.assertEqual(failed, [])
        self.assertIn("z.json", seen)          # the walk continued past the missing file
        self.assertIn("a.json", seen)
        self.assertNotIn("vanishing.json", seen)

    def test_fix_perms_reports_a_path_it_is_not_allowed_to_stat(self) -> None:
        # EACCES is not the vanished-file race: the file is still there and still not group-writable,
        # so it belongs in the "still failed" list. Dropping it reports a clean tree.
        paths.ensure_memory_tree(self.root)
        (self.root / "provisional" / "guarded.json").write_text("{}", encoding="utf-8")
        real_stat = os.stat

        def denying_stat(path, *args, **kwargs):
            if str(path).endswith("guarded.json"):
                raise PermissionError(13, "Permission denied")
            return real_stat(path, *args, **kwargs)

        with patch.object(paths.os, "stat", side_effect=denying_stat):
            failed = paths.fix_perms(self.root)
        self.assertEqual(len(failed), 1, failed)
        self.assertIn("guarded.json", failed[0])
        self.assertIn("Permission denied", failed[0])

    def test_fix_perms_reports_a_path_whose_symlink_check_is_denied(self) -> None:
        # On 3.11 Path.is_symlink() reaches os.stat, so a directory the walk may not inspect
        # raises here, before the guarded stat below -- the walk must record it, not crash.
        # (Caught on the cluster: the mock in the test above leaks into pathlib there.)
        paths.ensure_memory_tree(self.root)
        (self.root / "provisional" / "guarded.json").write_text("{}", encoding="utf-8")
        real_is_symlink = Path.is_symlink

        def denying_is_symlink(path: Path) -> bool:
            if str(path).endswith("guarded.json"):
                raise PermissionError(13, "Permission denied")
            return real_is_symlink(path)

        with patch.object(Path, "is_symlink", denying_is_symlink):
            failed = paths.fix_perms(self.root)
        self.assertEqual(len(failed), 1, failed)
        self.assertIn("guarded.json", failed[0])
        self.assertIn("Permission denied", failed[0])

    def test_lock_is_reentrant_across_sequential_uses(self) -> None:
        with paths.memory_lock(self.root):
            self.assertTrue((self.root / ".lock").exists())
        with paths.memory_lock(self.root):
            pass

    def test_lock_serialises_two_threads(self) -> None:
        # Two threads each open the lock file themselves; the second must wait for the first.
        order: list[str] = []
        started = threading.Event()

        def holder() -> None:
            with paths.memory_lock(self.root):
                started.set()
                order.append("a-in")
                threading.Event().wait(0.3)
                order.append("a-out")

        def waiter() -> None:
            started.wait(5)
            with paths.memory_lock(self.root):
                order.append("b-in")

        t1, t2 = threading.Thread(target=holder), threading.Thread(target=waiter)
        t1.start(); t2.start(); t1.join(10); t2.join(10)
        if paths.locking_available():
            self.assertEqual(order, ["a-in", "a-out", "b-in"])
        else:  # no fcntl / msvcrt: lock is a no-op and we only assert nothing hangs
            self.assertEqual(sorted(order), ["a-in", "a-out", "b-in"])

    def test_repo_root_from_package_dir(self) -> None:
        pkg = self.root.parent / "skills" / "sure_onboard"
        self.assertEqual(paths.repo_root_from_package_dir(pkg), self.root.parent.parent)
        self.assertEqual(paths.memory_root(self.root.parent.parent), self.root)


class DecisionRowTests(unittest.TestCase):
    def test_decision_row_shape(self) -> None:
        row = paths.decision_row("publish", "sure_onboard/x", "auto", run_id="r1")
        self.assertEqual(row["action"], "publish")
        self.assertEqual(row["entry_id"], "sure_onboard/x")
        self.assertEqual(row["by"], "auto")
        self.assertEqual(row["run_id"], "r1")
        self.assertRegex(row["at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        with self.assertRaises(ValueError):
            paths.decision_row("export", "sure_onboard/x", "human")

    def test_append_decision_writes_one_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sure" / "memory"
            paths.append_decision(root, paths.decision_row("confirm", "sure_eval/y", "human", reason="looks right"))
            rows, bad = paths.read_jsonl(root / "decisions.jsonl")
            self.assertEqual(bad, 0)
            self.assertEqual(rows[0]["action"], "confirm")
            self.assertEqual(rows[0]["reason"], "looks right")


class ConfigFilesTests(unittest.TestCase):
    def test_config_loads_with_expected_keys(self) -> None:
        cfg = paths.load_config()
        self.assertEqual(cfg["schema"], "sure.memory.config.v1")
        expected_keys = (
            "promote_useful_activated", "promote_min_distinct_runs", "demote_disputed_streak",
            "max_candidates_per_run", "max_triggers_per_candidate", "bad_case_max_words", "fact_max_words",
            "proposal_md_max_bytes",
            "trigger_min_chars", "evidence_max_bytes", "trigger_stopwords", "trigger_template_phrases",
            "target_skills", "cause_enum",
            "fact_scopes", "extraction_gate_max_failures", "finish_extraction_max_attempts", "inject_max_entries",
            "inject_max_chars_per_entry", "inject_max_chars_total", "inject_header", "memory_context_max_provisional",
            "digest_max_bytes", "digest_limits", "digest_trim_order", "index_md_max_lines", "index_md_max_bytes",
            "stale_after_days", "usage_max_line_bytes", "gate_digest_max_entries", "gate_digest_max_bytes",
            "publish_timeout_ms", "index_check_timeout_ms",
            "dedup_jaccard_min", "dedup_ratio_min",
        )
        for key in expected_keys:
            self.assertIn(key, cfg, key)
        # assertIn alone only catches a key being REMOVED from config.json; it stays green when a
        # key is ADDED without this list being updated to match — which is exactly how
        # gate_digest_max_entries / gate_digest_max_bytes (added on the .ts side of a parallel fix
        # wave) went uncovered here. Every key config.json carries besides "schema" is meant to be
        # named above, so also require the two sets to match: this fails on a removal (already
        # true) and on an untracked addition (new).
        self.assertEqual(set(cfg) - {"schema"}, set(expected_keys))
        self.assertEqual(cfg["promote_useful_activated"], 2)
        self.assertEqual(cfg["inject_max_entries"], 2)
        self.assertEqual(cfg["dedup_jaccard_min"], 0.5)
        self.assertEqual(cfg["dedup_ratio_min"], 0.9)
        self.assertTrue(cfg["inject_header"].startswith("Memory (advisory"))

    def test_cause_enum_starts_with_the_eval_failure_taxonomy(self) -> None:
        taxonomy = REPO_ROOT / "sure" / "skills" / "sure_infer" / "references" / "failure_taxonomy.md"
        names = re.findall(r"^## \d+\. (\S+)$", taxonomy.read_text(encoding="utf-8"), flags=re.M)
        self.assertEqual(len(names), 8)
        self.assertEqual(paths.load_config()["cause_enum"][:8], names)
        self.assertIn("infra", paths.load_config()["cause_enum"])
        self.assertIn("n.a.", paths.load_config()["cause_enum"])

    def test_units_registry_shape(self) -> None:
        units = paths.load_units()
        self.assertEqual(units["schema"], "sure.memory.units.v1")
        skills = units["skills"]
        self.assertEqual(set(skills), {"sure_onboard", "sure_infer", "sure_feed", "sure_reval", "sure_trans"})
        ob, ev, tr, fd = skills["sure_onboard"], skills["sure_infer"], skills["sure_trans"], skills["sure_feed"]
        self.assertEqual(ob[ob.index("verdict") + 1], "extract_lessons")
        self.assertEqual(ob[-1], "finalize_model_bundle")
        self.assertEqual(ev[ev.index("execute_inference") + 1], "extract_lessons")
        self.assertEqual(ev[-1], "run_report")
        self.assertEqual(tr[0], "load_trans_input")
        self.assertEqual(tr[tr.index("verdict") + 1], "extract_lessons")
        self.assertEqual(tr[-1], "finalize_model_bundle")
        self.assertEqual(fd[fd.index("rank_and_select") + 1], "extract_lessons")
        self.assertEqual(fd[-1], "emit_handoff_manifest")
        self.assertEqual(skills["sure_reval"], [])
        self.assertEqual(len(skills["sure_feed"]), 8)

    def test_log_paths_only_use_known_placeholders(self) -> None:
        log_paths = paths.load_log_paths()
        self.assertEqual(log_paths["schema"], "sure.memory.log_paths.v1")
        units = paths.load_units()["skills"]
        for skill, table in log_paths.items():
            if skill == "schema":
                continue
            for unit, candidates in table.items():
                self.assertIn(unit, units[skill], f"{skill}.{unit}")
                for candidate in candidates:
                    if candidate.startswith("artifact:"):
                        self.assertTrue(candidate.endswith(".json"), candidate)
                        continue
                    for placeholder in re.findall(r"\{[a-z_]+\}", candidate):
                        self.assertIn(placeholder, ("{run_dir}", "{product_dir}"), candidate)


if __name__ == "__main__":
    unittest.main()
