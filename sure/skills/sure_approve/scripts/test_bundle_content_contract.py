#!/usr/bin/env python3
"""Round-trip regression for the shared bundle-content contract.

The onboard finalize declared HF download-cache bookkeeping under checkpoints
as REQUIRED deployment artifacts while the approve candidate policy excludes
`.cache` — the two sides disagreed on what bundle content is and no honest
approval could pass. Source digests also hashed the model `.venv` because
_walk_entries never applied the exclusion it computed. Both are closed by one
shared contract in sure/runtime/bundle_content.py; these tests pin that both
sides consume the SAME policy and that excluded subtrees never re-enter
digests or required lists.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
ONBOARD_SCRIPTS = SCRIPTS_DIR.parents[2] / "skills" / "sure_onboard" / "scripts"
REPO_ROOT = SCRIPTS_DIR.parents[3]
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(ONBOARD_SCRIPTS))
sys.path.insert(0, str(REPO_ROOT))

import approval_core
import finalize_model_bundle as finalize
from sure.runtime import bundle_content


def build_model_dir(root: Path) -> Path:
    model_dir = root / "sure" / "models" / "demo__model"
    (model_dir / "artifacts").mkdir(parents=True)
    (model_dir / "checkpoints" / ".cache" / "huggingface").mkdir(parents=True)
    (model_dir / "checkpoints" / "weights.nemo").write_bytes(b"weights-bytes")
    (model_dir / "checkpoints" / ".cache" / "huggingface" / ".gitignore").write_text("cache-bookkeeping\n")
    (model_dir / ".venv" / "Scripts").mkdir(parents=True)
    (model_dir / ".venv" / "Scripts" / "lib0.py").write_text("print(0)\n")
    (model_dir / "artifacts" / "artifact_manifest.json").write_text(
        json.dumps({"schema": "sure.onboard.artifact_manifest.v1", "artifacts": {"required": {}}})
    )
    (model_dir / "artifacts" / "weights_manifest.json").write_text(
        json.dumps({
            "weights_ready": True,
            "required": True,
            "local_dir_name": "checkpoints",
            "checkpoint_root": str(model_dir / "checkpoints"),
            "resolved_local_model_path": str(model_dir / "checkpoints" / "weights.nemo"),
        })
    )
    (model_dir / "artifacts" / "sample_output.json").write_text("{}")
    (model_dir / "artifacts" / "sample_outputs.jsonl").write_text("")
    (model_dir / "fixture").mkdir()
    (model_dir / "fixture" / "gt.jsonl").write_text("{}\n")
    return model_dir


class BundleContentContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.model_dir = build_model_dir(Path(self._tmp.name))

    def test_policy_identity_across_producer_and_consumer(self) -> None:
        self.assertIs(approval_core.EXCLUDED_DIR_NAMES, bundle_content.EXCLUDED_DIR_NAMES)
        self.assertIs(approval_core.EXCLUDED_TOP_LEVEL, bundle_content.EXCLUDED_TOP_LEVEL)

    def test_digest_covers_only_bundle_content(self) -> None:
        digest, entries, findings = approval_core.tree_digest(self.model_dir)
        self.assertFalse(findings)
        self.assertFalse([e for e in entries if ".venv" in e["path"].split("/")])
        self.assertFalse([e for e in entries if ".cache" in e["path"].split("/")])
        self.assertTrue([e for e in entries if e["path"] == "checkpoints/weights.nemo"])

    def test_digest_is_stable_under_venv_churn(self) -> None:
        before, _, _ = approval_core.tree_digest(self.model_dir)
        churned = self.model_dir / ".venv" / "Scripts" / "lib1.py"
        churned.write_text("print('churned')\n")
        after, _, _ = approval_core.tree_digest(self.model_dir)
        self.assertEqual(before, after)

    def test_excluded_symlink_metadata_is_still_recorded(self) -> None:
        target = self.model_dir / "checkpoints" / "weights.nemo"
        link = self.model_dir / "checkpoints" / ".cache" / "linked.nemo"
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("symlinks unavailable on this host")
        _, entries, _ = approval_core.tree_digest(self.model_dir)
        recorded = [e for e in entries if e["path"].endswith("linked.nemo")]
        self.assertTrue(recorded)
        self.assertEqual(recorded[0]["type"], "symlink")

    def test_audit_flags_excluded_required_paths_at_integrity_time(self) -> None:
        excluded_file = self.model_dir / "checkpoints" / ".cache" / "huggingface" / ".gitignore"
        declared = {
            "checkpoints/.cache/huggingface/.gitignore": hashlib.sha256(excluded_file.read_bytes()).hexdigest(),
            "checkpoints/weights.nemo": hashlib.sha256(b"weights-bytes").hexdigest(),
        }
        findings: list[dict] = []
        approval_core._require_hashes(self.model_dir, {
            "required_artifact_sha256": declared,
            "bundle_identity_sha256": hashlib.sha256(approval_core.canonical_json(declared)).hexdigest(),
        }, findings, "sure_onboard")
        codes = sorted({f["code"] for f in findings})
        self.assertIn("ARTIFACT_PATH_EXCLUDED", codes)
        self.assertNotIn("ARTIFACT_TAMPERED", codes)
        excluded_findings = [f for f in findings if f["code"] == "ARTIFACT_PATH_EXCLUDED"]
        self.assertIn("bundle-content contract", excluded_findings[0]["repair"])

    def test_finalize_enlists_zero_cache_paths_on_a_cache_noisy_dir(self) -> None:
        manifest = __import__("finalize_model_bundle").update_manifest(self.model_dir, {
            "package_profile": "none", "deployment_type": "local",
            "model_id": "demo/demo", "model_name": "demo__model",
        })
        required = manifest["artifacts"]["required"]
        self.assertTrue([key for key in required if key == "file:checkpoints/weights.nemo"])
        self.assertFalse([key for key in required if ".cache" in key])
        cache_paths = [entry["path"] for entry in required.values() if ".cache" in entry["path"].split("/")]
        self.assertEqual(cache_paths, [])

    def test_enumeration_uses_bundle_relative_policy_for_all_names(self) -> None:
        for name in bundle_content.EXCLUDED_TOP_LEVEL:
            directory = self.model_dir / name
            directory.mkdir()
            (directory / "noise").write_bytes(b"noise")
        (self.model_dir / "checkpoints" / ".pytest_cache").write_bytes(b"noise")
        nested = self.model_dir / "checkpoints" / "results"
        nested.mkdir()
        (nested / "weights.bin").write_bytes(b"real weights")
        files = bundle_content.iter_bundle_files(self.model_dir)
        self.assertFalse([p for p in files if bundle_content.is_excluded(p.relative_to(self.model_dir))])
        self.assertIn(nested / "weights.bin", files)

    def test_excluded_weights_root_and_single_file_cannot_be_required(self) -> None:
        for root in ("results", "checkpoints/.cache/huggingface/.gitignore"):
            with self.subTest(root=root):
                (self.model_dir / "results").mkdir(exist_ok=True)
                (self.model_dir / "results" / "weights.bin").write_bytes(b"excluded")
                (self.model_dir / "artifacts" / "weights_manifest.json").write_text(
                    json.dumps({"required": True, "local_dir_name": root})
                )
                with self.assertRaisesRegex(ValueError, "no.*files"):
                    finalize.update_manifest(self.model_dir, {"package_profile": "none"})

    def test_nested_results_fixture_is_publishable_but_cache_is_not(self) -> None:
        nested = self.model_dir / "fixture" / "results"
        nested.mkdir()
        (nested / "sample.wav").write_bytes(b"sample")
        (self.model_dir / "fixture" / ".mypy_cache").write_bytes(b"noise")
        manifest = finalize.update_manifest(self.model_dir, {"package_profile": "none"})
        paths = [entry["path"] for entry in manifest["artifacts"]["required"].values()]
        self.assertIn("fixture/results/sample.wav", paths)
        self.assertNotIn("fixture/.mypy_cache", paths)

    def test_generated_outputs_do_not_require_cache_bookkeeping(self) -> None:
        output = self.model_dir / "artifacts" / "outputs"
        (output / ".cache").mkdir(parents=True)
        (output / ".cache" / "lock").write_bytes(b"noise")
        (output / "sample.wav").write_bytes(b"sample")
        manifest = finalize.update_manifest(self.model_dir, {"package_profile": "none"})
        paths = [entry["path"] for entry in manifest["artifacts"]["required"].values()]
        self.assertIn("artifacts/outputs/sample.wav", paths)
        self.assertNotIn("artifacts/outputs/.cache/lock", paths)

    def test_rerun_removes_only_excluded_generated_entries(self) -> None:
        manifest_path = self.model_dir / "artifacts" / "artifact_manifest.json"
        path = "checkpoints/.cache/huggingface/.gitignore"
        manifest_path.write_text(json.dumps({"artifacts": {"required": {
            f"file:{path}": {"path": path, "description": f"Model weight file: {path}."},
            "declared": {"path": "results/mandatory.json", "description": "User-required artifact."},
        }}}))
        manifest = finalize.update_manifest(self.model_dir, {"package_profile": "none"})
        self.assertNotIn(f"file:{path}", manifest["artifacts"]["required"])
        self.assertIn("declared", manifest["artifacts"]["required"])

    def test_manifest_hashes_survive_real_candidate_copy_and_detect_tampering(self) -> None:
        resolved = {"package_profile": "none", "deployment_type": "local", "model_name": "demo__model"}
        finalize.update_manifest(self.model_dir, resolved)
        for name, value in (
            ("package_gate.json", {"package_profile": "none"}),
            ("runtime_inventory.json", {"status": "ready"}),
            ("verdict.json", {"status": "passed"}),
            ("model_runtime_manifest.json", {}),
        ):
            value["timestamp"] = "2026-10-06T00:00:00+00:00"
            (self.model_dir / "artifacts" / name).write_text(json.dumps(value))
        marker = finalize.build_deployment_ready(
            Path(self._tmp.name), self.model_dir, resolved,
            json.loads((self.model_dir / "artifacts" / "package_gate.json").read_text()),
        )
        candidate = Path(self._tmp.name) / "candidate"
        candidate.mkdir()
        approval_core._copy_candidate(self.model_dir, candidate)
        findings: list[dict] = []
        approval_core._require_hashes(candidate, marker, findings, "sure_onboard")
        self.assertEqual(findings, [])
        self.assertFalse((candidate / "checkpoints" / ".cache").exists())
        self.assertFalse((candidate / ".venv").exists())
        for path in ("checkpoints/weights.nemo", "fixture/gt.jsonl"):
            with self.subTest(path=path):
                original = (candidate / path).read_bytes()
                (candidate / path).write_bytes(b"tampered")
                findings = []
                approval_core._require_hashes(candidate, marker, findings, "sure_onboard")
                self.assertIn("ARTIFACT_TAMPERED", [f["code"] for f in findings])
                (candidate / path).write_bytes(original)

    def test_first_local_weight_root_keeps_upstream_precedence(self) -> None:
        for field in ("checkpoint_root", "resolved_local_model_path"):
            with self.subTest(field=field):
                manifest = {"local_dir_name": "checkpoints", field: str(Path(self._tmp.name) / "outside")}
                self.assertEqual(finalize.resolve_weights_root(self.model_dir, manifest), self.model_dir / "checkpoints")
                manifest["fallback_to_host_global"] = True
                self.assertEqual(finalize.resolve_weights_root(self.model_dir, manifest), self.model_dir / "checkpoints")

    def test_absolute_internal_weight_metadata_is_normalized_before_relocation(self) -> None:
        run_dir = Path(self._tmp.name) / "run"
        run_dir.mkdir()
        finalize.normalize_generated_samples(run_dir, self.model_dir)
        path = self.model_dir / "artifacts" / "weights_manifest.json"
        manifest = json.loads(path.read_text())
        self.assertEqual(manifest["checkpoint_root"], "checkpoints")
        self.assertEqual(manifest["resolved_local_model_path"], "checkpoints/weights.nemo")
        relocated = Path(self._tmp.name) / "relocated"
        relocated.mkdir()
        approval_core._copy_candidate(self.model_dir, relocated)
        self.assertEqual(finalize.resolve_weights_root(relocated, manifest), relocated / "checkpoints")

    def test_external_weights_and_nonportable_local_names_keep_existing_behavior(self) -> None:
        manifest = {"required": True, "local_dir_name": "missing", "fallback_to_host_global": True,
                    "fallback_reason": "shared immutable weights", "checkpoint_root": str(Path(self._tmp.name) / "external")}
        self.assertIsNone(finalize.resolve_weights_root(self.model_dir, manifest))
        for raw in (str(self.model_dir / "checkpoints"), "../checkpoints"):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, "not portable"):
                    finalize.resolve_weights_root(self.model_dir, {"local_dir_name": raw})

    def test_finalization_rejects_explicit_required_excluded_path(self) -> None:
        for name in ("runtime_inventory.json", "verdict.json"):
            (self.model_dir / "artifacts" / name).write_text("{}")
        (self.model_dir / "artifacts" / "artifact_manifest.json").write_text(json.dumps({"artifacts": {"required": {
            "declared": {"path": "results/mandatory.json"},
        }}}))
        with self.assertRaisesRegex(ValueError, "excluded from bundle content"):
            finalize.build_deployment_ready(Path(self._tmp.name), self.model_dir, {}, {})


if __name__ == "__main__":
    unittest.main()
