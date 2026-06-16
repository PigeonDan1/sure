from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts import xforge_modelscope_dataset_to_oref

from xforge_sure_bridge.modelscope_fetch import (
    build_selected_candidate,
    emit_selected_resource_artifacts,
    write_fetch_failure,
)


class XForgeModelScopeFetchTest(unittest.TestCase):
    def test_build_selected_model_candidate(self) -> None:
        candidate = build_selected_candidate(resource_type="model", task="asr", resource_id="iic/demo-asr")

        self.assertEqual(candidate["provider"], "modelscope")
        self.assertEqual(candidate["resource_type"], "model")
        self.assertEqual(candidate["resource_id"], "iic/demo-asr")
        self.assertEqual(candidate["task"], "asr")

    def test_emit_selected_model_manifest_and_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = build_selected_candidate(resource_type="model", task="asr", resource_id="iic/demo-asr")

            result = emit_selected_resource_artifacts(
                candidate=candidate,
                manifest_dir=root / "manifests",
                handoff_dir=root / "handoff",
            )

            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
            handoff = json.loads(Path(result["handoff_path"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["resource_type"], "model")
            self.assertEqual(manifest["source"]["provider"], "modelscope")
            self.assertEqual(manifest["source"]["id"], "iic/demo-asr")
            self.assertEqual(handoff["target_agent"], "sure_tool_agent")
            self.assertEqual(handoff["status"], "ready_for_model_collect")

    def test_emit_selected_dataset_manifest_is_blocked_without_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = build_selected_candidate(resource_type="dataset", task="ser", resource_id="speech/demo-ser")

            result = emit_selected_resource_artifacts(
                candidate=candidate,
                manifest_dir=root / "manifests",
                handoff_dir=root / "handoff",
            )

            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
            handoff = json.loads(Path(result["handoff_path"]).read_text(encoding="utf-8"))
            self.assertFalse(manifest["bridge_ready"])
            self.assertEqual(manifest["processing_status"], "requires_dataset_schema_mapping")
            self.assertEqual(handoff["target_agent"], "sure_main_agent")
            self.assertEqual(handoff["status"], "blocked_until_dataset_schema_mapping")

    def test_write_fetch_failure_records_audit_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            failure_path = write_fetch_failure(
                fetch_run_dir=Path(tmp),
                resource_type="model",
                task="asr",
                resource_id="iic/demo-asr",
                command=["python", "scripts/xforge_modelscope_fetch.py"],
                error="modelscope is required",
            )

            failure = json.loads(Path(failure_path).read_text(encoding="utf-8"))
            self.assertEqual(failure["status"], "failed")
            self.assertEqual(failure["resource_type"], "model")
            self.assertEqual(failure["task"], "asr")
            self.assertEqual(failure["resource_id"], "iic/demo-asr")
            self.assertEqual(failure["error"], "modelscope is required")

    def test_fetch_cli_emits_dataset_blocked_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/xforge_modelscope_fetch.py",
                    "--resource",
                    "dataset",
                    "--task",
                    "ser",
                    "--id",
                    "speech/demo-ser",
                    "--manifest-dir",
                    str(root / "manifests"),
                    "--handoff-dir",
                    str(root / "handoff"),
                    "--fetch-run-dir",
                    str(root / "fetch_runs"),
                    "--no-download",
                ],
                cwd=Path(__file__).resolve().parent.parent,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertTrue(Path(payload["handoff_path"]).exists())
            self.assertEqual(payload["resource_type"], "dataset")

    def test_fetch_cli_no_download_emits_model_sure_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/xforge_modelscope_fetch.py",
                    "--resource",
                    "model",
                    "--task",
                    "asr",
                    "--id",
                    "iic/demo-asr",
                    "--manifest-dir",
                    str(root / "manifests"),
                    "--handoff-dir",
                    str(root / "handoff"),
                    "--fetch-run-dir",
                    str(root / "fetch_runs"),
                    "--sure-plan-dir",
                    str(root / "sure_plans"),
                    "--model-root",
                    str(root / "models"),
                    "--no-download",
                ],
                cwd=Path(__file__).resolve().parent.parent,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            plan = json.loads(Path(payload["sure_plan_path"]).read_text(encoding="utf-8"))
            self.assertEqual(plan["resource_type"], "model")
            self.assertEqual(plan["status"], "waiting_for_checkpoint_download")
            self.assertEqual(plan["next_step"], "download_checkpoint")
            self.assertEqual(plan["expected_model_dir"], str((root / "models" / "iic__demo-asr").resolve()))
            self.assertEqual(plan["required_wrapper"]["entrypoint"], "model.py")

    def test_fetch_cli_no_download_emits_dataset_sure_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/xforge_modelscope_fetch.py",
                    "--resource",
                    "dataset",
                    "--task",
                    "asr",
                    "--id",
                    "speech/demo-asr-data",
                    "--manifest-dir",
                    str(root / "manifests"),
                    "--handoff-dir",
                    str(root / "handoff"),
                    "--fetch-run-dir",
                    str(root / "fetch_runs"),
                    "--sure-plan-dir",
                    str(root / "sure_plans"),
                    "--sure-dataset-dir",
                    str(root / "sure"),
                    "--no-download",
                ],
                cwd=Path(__file__).resolve().parent.parent,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            plan = json.loads(Path(payload["sure_plan_path"]).read_text(encoding="utf-8"))
            self.assertEqual(plan["resource_type"], "dataset")
            self.assertEqual(plan["status"], "waiting_for_raw_download_and_schema_mapping")
            self.assertEqual(plan["next_step"], "confirm_dataset_schema")
            self.assertEqual(plan["expected_raw_root"], "data/datasets/xforge_raw/demo-asr-data")
            self.assertEqual(plan["expected_sure_jsonl"], str((root / "sure" / "demo-asr-data.jsonl").resolve()))
            self.assertEqual(plan["required_field_mapping"], ["key", "path", "target"])

    def test_fetch_cli_collects_local_model_source_for_offline_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "downloaded" / "model.bin"
            source.parent.mkdir()
            source.write_bytes(b"weights")
            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/xforge_modelscope_fetch.py",
                    "--resource",
                    "model",
                    "--task",
                    "asr",
                    "--id",
                    "iic/demo-asr",
                    "--manifest-dir",
                    str(root / "manifests"),
                    "--handoff-dir",
                    str(root / "handoff"),
                    "--fetch-run-dir",
                    str(root / "fetch_runs"),
                    "--model-root",
                    str(root / "models"),
                    "--source-provider",
                    "local",
                    "--source-path",
                    str(source),
                ],
                cwd=Path(__file__).resolve().parent.parent,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["resource_type"], "model")
            self.assertTrue(Path(payload["collect_summary"]["weights_manifest"]).exists())
            self.assertIn("tool_agent_controller", payload)
            controller = payload["tool_agent_controller"]
            self.assertEqual(controller["status"], "blocked_for_user_confirmation")
            self.assertEqual(controller["current_state"], "DOCKER_BUILD_CONFIRM")
            self.assertTrue(Path(controller["state_path"]).exists())
            self.assertTrue(Path(controller["run_report_path"]).exists())
            model_dir = root / "models" / "iic__demo-asr"
            state = json.loads((model_dir / "artifacts" / "tool_agent_state.json").read_text(encoding="utf-8"))
            run_report = json.loads((model_dir / "artifacts" / "tool_agent_run_report.json").read_text(encoding="utf-8"))
            tool_agent_request = json.loads(
                (model_dir / "artifacts" / "tool_agent_request.json").read_text(encoding="utf-8")
            )
            spec = (model_dir / "model.spec.yaml").read_text(encoding="utf-8")
            self.assertEqual(state["agent"], "sure_model_tool_agent")
            self.assertEqual(state["current_state"], "DOCKER_BUILD_CONFIRM")
            self.assertEqual(state["completed_states"][:2], ["VALIDATE_SPEC", "LOCAL_UV_BOOTSTRAP"])
            self.assertEqual(run_report["handoff_consumed"], str((model_dir / "artifacts" / "tool_agent_request.json").resolve()))
            self.assertEqual(run_report["blocking_user_interaction"]["state"], "DOCKER_BUILD_CONFIRM")
            self.assertEqual(tool_agent_request["resource"]["provider"], "modelscope")
            self.assertEqual(tool_agent_request["resource"]["resource_id"], "iic/demo-asr")
            self.assertIn('repo_id: "iic/demo-asr"', spec)

    def test_fetch_cli_converts_dataset_with_schema_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = root / "raw"
            audio = raw_root / "audio" / "sample.wav"
            audio.parent.mkdir(parents=True)
            audio.write_bytes(b"RIFF")
            raw_jsonl = raw_root / "samples.jsonl"
            raw_jsonl.write_text(
                '{"id":"utt1","audio":"audio/sample.wav","text":"hello","language":"en"}\n',
                encoding="utf-8",
            )
            mapping = root / "mapping.json"
            mapping.write_text(
                json.dumps(
                    {
                        "raw_root": str(raw_root),
                        "raw_jsonl": "samples.jsonl",
                        "sure_name": "demo_ser",
                        "language": "en",
                        "field_mapping": {"key": "id", "path": "audio", "target": "text"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/xforge_modelscope_fetch.py",
                    "--resource",
                    "dataset",
                    "--task",
                    "ser",
                    "--id",
                    "speech/demo-ser",
                    "--manifest-dir",
                    str(root / "manifests"),
                    "--handoff-dir",
                    str(root / "handoff"),
                    "--fetch-run-dir",
                    str(root / "fetch_runs"),
                    "--sure-dataset-dir",
                    str(root / "sure"),
                    "--schema-mapping",
                    str(mapping),
                    "--no-download",
                ],
                cwd=Path(__file__).resolve().parent.parent,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["dataset_processing_summary"]["samples_written"], 1)
            self.assertTrue(Path(payload["dataset_processing_summary"]["jsonl_path"]).exists())

    def test_fetch_cli_converts_dataset_to_oref_with_schema_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = root / "raw"
            audio = raw_root / "audio" / "sample.wav"
            audio.parent.mkdir(parents=True)
            audio.write_bytes(b"RIFF")
            raw_jsonl = raw_root / "samples.jsonl"
            raw_jsonl.write_text(
                '{"id":"utt1","audio":"audio/sample.wav","text":"hello","language":"en"}\n',
                encoding="utf-8",
            )
            mapping = root / "mapping.json"
            mapping.write_text(
                json.dumps(
                    {
                        "raw_root": str(raw_root),
                        "raw_jsonl": "samples.jsonl",
                        "sure_name": "demo_asr",
                        "language": "en",
                        "field_mapping": {"key": "id", "path": "audio", "target": "text"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/xforge_modelscope_fetch.py",
                    "--resource",
                    "dataset",
                    "--task",
                    "asr",
                    "--id",
                    "speech/demo-asr",
                    "--manifest-dir",
                    str(root / "manifests"),
                    "--handoff-dir",
                    str(root / "handoff"),
                    "--fetch-run-dir",
                    str(root / "fetch_runs"),
                    "--sure-dataset-dir",
                    str(root / "sure"),
                    "--schema-mapping",
                    str(mapping),
                    "--oref-local",
                    "--oref-dataset-root",
                    str(root / "datasets"),
                    "--oref-config",
                    str(root / "oref_datasets.yaml"),
                    "--no-download",
                ],
                cwd=Path(__file__).resolve().parent.parent,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            oref_summary = payload["oref_processing_summary"]
            self.assertEqual(oref_summary["samples_written"], 1)
            self.assertTrue((root / "datasets" / "demo_asr" / "audio" / "sample.wav").exists())
            sample = json.loads((root / "datasets" / "demo_asr" / "sample.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(sample["sample_id"], "utt1")
            self.assertEqual(sample["annotation"][0]["transcription"]["text"], ["hello"])
            registry_text = (root / "oref_datasets.yaml").read_text(encoding="utf-8")
            self.assertIn("demo_asr", registry_text)
            self.assertIn("demo_asr/sample.jsonl", registry_text)

    def test_modelscope_dataset_to_oref_defaults_use_readable_dataset_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            (snapshot / "meta.csv").write_text(
                "Input:FILE,Info:FILE,Metadata:FILE\n"
                "audio/sample.wav,hello,meta.json\n",
                encoding="utf-8",
            )

            with (
                patch("scripts.xforge_modelscope_dataset_to_oref.snapshot_download", return_value=str(snapshot)),
                patch.object(
                    sys,
                    "argv",
                    [
                        "scripts/xforge_modelscope_dataset_to_oref.py",
                        "--id",
                        "speech/demo-asr",
                        "--task",
                        "asr",
                        "--language",
                        "en",
                        "--sure-name",
                        "demo_asr",
                        "--work-root",
                        str(root / "xforge_oref_smoke"),
                        "--allow-missing-audio",
                    ],
                ),
            ):
                stdout = StringIO()
                with redirect_stdout(stdout):
                    return_code = xforge_modelscope_dataset_to_oref.main()

            self.assertEqual(return_code, 0)
            work_root = root / "xforge_oref_smoke"
            self.assertTrue((work_root / "demo_asr" / "sample.jsonl").exists())
            self.assertTrue((work_root / "demo_asr" / "audio").exists())
            self.assertTrue((work_root / "oref_datasets.yaml").exists())
            self.assertTrue((work_root / "_xforge_internal" / "artifacts" / "manifests").exists())
            summary = json.loads((work_root / "demo_asr" / "oref_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["oref_root"], str((work_root / "demo_asr").resolve()))


if __name__ == "__main__":
    unittest.main()
