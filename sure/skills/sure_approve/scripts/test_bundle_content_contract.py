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


if __name__ == "__main__":
    unittest.main()
