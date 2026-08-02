#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stage_model_artifacts  # noqa: E402
from write_runtime_inventory import write_inventory  # noqa: E402


REQUIRED_RUN_ARTIFACTS = stage_model_artifacts.REQUIRED_RUN_ARTIFACTS


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


class RuntimeInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.model_dir = self.root / "sure" / "models" / "demo__asr"
        self.artifacts = self.model_dir / "artifacts"
        self.artifacts.mkdir(parents=True)
        for name in stage_model_artifacts.CORE_FILES:
            (self.model_dir / name).write_text("# test\n", encoding="utf-8")
        (self.model_dir / ".venv" / "bin").mkdir(parents=True)
        (self.model_dir / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
        write_json(
            self.artifacts / "model_input_resolved.json",
            {
                "model_id": "demo/asr",
                "model_name": "demo__asr",
                "model_dir": str(self.model_dir),
                "task_type": "asr",
                "deployment_type": "local",
            },
        )
        write_json(
            self.artifacts / "build_env_result.json",
            {
                "env_ready": True,
                "backend": "uv",
                "installer": "uv",
                "python_version": "3.12.4",
                "python_executable": str(self.model_dir / ".venv" / "bin" / "python"),
                "model_dir": str(self.model_dir),
                "lockfile_path": "requirements.lock",
                "log_path": "build.log",
                "runtime_checks": {"required_imports": ["torch"]},
                "runtime_probe": {"status": "passed", "imports": {"torch": "ok"}},
            },
        )
        write_json(
            self.artifacts / "weights_manifest.json",
            {
                "weights_ready": True,
                "status": "fetched",
                "resolved_local_model_path": "checkpoints/demo.bin",
                "checkpoint_root": "checkpoints",
                "dependencies": [
                    {
                        "name": "demo.bin",
                        "local_path": "checkpoints/demo.bin",
                        "exists": True,
                        "link_type": "symlink",
                        "target": "/outside/large/demo.bin",
                    }
                ],
            },
        )
        write_json(self.artifacts / "package_gate.json", {"status": "passed", "readiness": {"local_ready": True}})
        write_json(self.artifacts / "verdict.json", {"status": "passed"})
        (self.model_dir / "requirements.lock").write_text("torch\n", encoding="utf-8")
        (self.model_dir / "checkpoints").mkdir()
        (self.model_dir / "checkpoints" / "demo.bin").write_text("payload", encoding="utf-8")

    def test_inventory_links_runtime_evidence_without_checkpoint_payloads(self) -> None:
        inventory = write_inventory(self.model_dir)
        self.assertEqual(inventory["schema"], "sure.onboard.runtime_inventory.v1")
        self.assertEqual(inventory["status"], "ready")
        self.assertEqual(inventory["runtime"]["backend"], "uv")
        link_entries = set(inventory["evidence"]["link_entries"])
        self.assertIn("build_env_result.json", link_entries)
        self.assertIn("weights_manifest.json", link_entries)
        self.assertNotIn("demo.bin", link_entries)
        self.assertFalse(inventory["policy"]["checkpoint_payload_links"])
        self.assertTrue((self.artifacts / "runtime_links_manifest.json").exists())

    def test_missing_terminal_evidence_is_partial_not_crash(self) -> None:
        (self.artifacts / "verdict.json").unlink()
        inventory = write_inventory(self.model_dir)
        self.assertEqual(inventory["status"], "partial")
        self.assertIn("artifacts/verdict.json", inventory["evidence"]["missing"])

    def test_stage_model_artifacts_generates_inventory_and_indexes_it(self) -> None:
        run_dir = self.root / "run"
        run_artifacts = run_dir / "artifacts"
        run_artifacts.mkdir(parents=True)
        for name in REQUIRED_RUN_ARTIFACTS:
            source = self.artifacts / name
            if source.exists():
                data = json.loads(source.read_text(encoding="utf-8"))
            elif name == "model_input_resolved.json":
                data = {
                    "model_id": "demo/asr",
                    "model_name": "demo__asr",
                    "model_dir": str(self.model_dir),
                    "task_type": "asr",
                    "deployment_type": "local",
                }
            else:
                data = {"ok": True, "name": name}
            write_json(run_artifacts / name, data)
        produces = run_artifacts / "artifact_manifest.json"
        rc = stage_model_artifacts.main_with_args(
            ["--run-dir", str(run_dir), "--produces", str(produces), "--allow-missing-run-artifacts"]
        )
        self.assertEqual(rc, 0)
        manifest = json.loads(produces.read_text(encoding="utf-8"))
        optional = manifest["artifacts"]["optional"]
        self.assertIn("runtime_inventory", optional)
        self.assertIn("runtime_links_manifest", optional)
        self.assertTrue((self.artifacts / "runtime_inventory.json").exists())


if __name__ == "__main__":
    unittest.main()
