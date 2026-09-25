from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_artifact
import finalize_trans_bundle
import materialize_trans_inputs
import mcp_smoke
import prepare_fixture
import scaffold_adapter
import task_contract


class SeTransSupportTests(unittest.TestCase):
    def test_se_input_and_output_contracts(self) -> None:
        self.assertEqual(
            materialize_trans_inputs.resolve_task_type("SE", Path("unused.py"), Path("unused")),
            "se",
        )
        self.assertEqual(task_contract.contract_for("se")["io_contract"]["output_type"], "audio")
        tool, tool_schema = scaffold_adapter.tool_contract("se")
        self.assertEqual(tool, "enhance_speech")
        self.assertEqual(tool_schema["required"], ["audio_path"])
        self.assertEqual(scaffold_adapter.io_contract_for("se")["primary_field"], "audio_path")
        self.assertEqual(mcp_smoke.primary_output_field("enhance_speech"), "audio_path")

    def test_se_fixture_stages_clean_reference_and_rechecks_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_dir = root / "source"
            source_dir.mkdir()
            noisy = source_dir / "noisy.wav"
            clean = source_dir / "clean.wav"
            noisy.write_bytes(b"noisy audio")
            clean.write_bytes(b"clean audio")
            noisy.with_suffix(".expected.json").write_text(
                json.dumps({"reference_audio": clean.name}), encoding="utf-8"
            )
            run_dir = root / "run"
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            resolved = {"model_name": "speechbrain__metricgan", "task_type": "se", "fixture_path": str(noisy)}
            (artifacts / "trans_input_resolved.json").write_text(json.dumps(resolved), encoding="utf-8")
            with patch.object(sys, "argv", ["prepare_fixture.py", "--run-dir", str(run_dir)]):
                self.assertEqual(prepare_fixture.main(), 0)
            prepared = json.loads((artifacts / "fixture_manifest.json").read_text(encoding="utf-8"))
            check_artifact.validate_fixture_manifest(prepared)
            self.assertEqual(prepared["samples"][0]["annotation_fields"], ["reference_audio"])
            model_dir = root / "models" / resolved["model_name"]
            model_dir.mkdir(parents=True)
            finalize_trans_bundle.stage_fixture(run_dir, model_dir, resolved)
            finalized = json.loads((artifacts / "fixture_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual((model_dir / "fixture/se/clean.wav").read_bytes(), b"clean audio")
            check_artifact.validate_fixture_manifest(finalized)
            (model_dir / "fixture/se/clean.wav").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "reference checksum changed"):
                check_artifact.validate_fixture_manifest(finalized)

    def test_se_fixture_rejects_missing_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            noisy = root / "noisy.wav"
            noisy.write_bytes(b"noisy audio")
            noisy.with_suffix(".expected.json").write_text(
                json.dumps({"reference_audio": "missing.wav"}), encoding="utf-8"
            )
            run_dir = root / "run"
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            (artifacts / "trans_input_resolved.json").write_text(
                json.dumps({"model_name": "speechbrain__metricgan", "task_type": "se", "fixture_path": str(noisy)}),
                encoding="utf-8",
            )
            with patch.object(sys, "argv", ["prepare_fixture.py", "--run-dir", str(run_dir)]):
                with self.assertRaisesRegex(ValueError, "clean reference is missing"):
                    prepare_fixture.main()

    def test_repository_se_fixture_uses_matching_gt_row(self) -> None:
        repo_root = Path(__file__).resolve().parents[4]
        fixture = repo_root / "fixtures/tasks/se/librispeech_noise_smoke/noisy_1580.wav"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            resolved = {"model_name": "speechbrain__metricgan", "task_type": "se", "fixture_path": str(fixture)}
            (artifacts / "trans_input_resolved.json").write_text(json.dumps(resolved), encoding="utf-8")
            with patch.object(sys, "argv", ["prepare_fixture.py", "--run-dir", str(run_dir)]):
                self.assertEqual(prepare_fixture.main(), 0)
            manifest = json.loads((artifacts / "fixture_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["annotation_source"]["type"], "task_registry_fixture")
            check_artifact.validate_fixture_manifest(manifest)
            model_dir = root / "models" / resolved["model_name"]
            model_dir.mkdir(parents=True)
            finalize_trans_bundle.stage_fixture(run_dir, model_dir, resolved)
            finalized = json.loads((artifacts / "fixture_manifest.json").read_text(encoding="utf-8"))
            check_artifact.validate_fixture_manifest(finalized)
            self.assertTrue((model_dir / "fixture/se/reference_1580.wav").is_file())


if __name__ == "__main__":
    unittest.main()
