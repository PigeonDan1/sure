from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from xforge_sure_bridge import (
    emit_sure_model_agent_handoff,
    materialize_model_manifest,
    process_dataset_manifest_to_oref,
    process_dataset_manifest,
)


class XForgeSureBridgeTest(unittest.TestCase):
    def test_process_dataset_manifest_writes_sure_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_audio = tmp_path / "raw" / "audio" / "sample.wav"
            raw_audio.parent.mkdir(parents=True)
            raw_audio.write_bytes(b"RIFF")
            raw_jsonl = tmp_path / "raw" / "samples.jsonl"
            raw_jsonl.write_text(
                '{"id":"utt1","audio":"audio/sample.wav","text":"hello","language":"en"}\n',
                encoding="utf-8",
            )
            manifest = {
                "resource_type": "dataset",
                "dataset_id": "demo/asr",
                "sure_name": "demo_asr",
                "task": "ASR",
                "language": "en",
                "raw_root": str(tmp_path / "raw"),
                "raw_jsonl": str(raw_jsonl),
                "field_mapping": {"key": "id", "path": "audio", "target": "text"},
            }
            output = tmp_path / "sure" / "demo_asr.jsonl"

            summary = process_dataset_manifest(manifest, output)

            self.assertEqual(summary["samples_written"], 1)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")),
                {
                    "key": "utt1",
                    "path": str(raw_audio.resolve()),
                    "target": "hello",
                    "task": "ASR",
                    "language": "en",
                    "dataset": "demo_asr",
                },
            )

    def test_process_dataset_manifest_to_oref_writes_dataset_manager_compatible_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_audio = tmp_path / "raw" / "clips" / "sample.wav"
            raw_audio.parent.mkdir(parents=True)
            raw_audio.write_bytes(b"RIFF")
            raw_jsonl = tmp_path / "raw" / "samples.jsonl"
            raw_jsonl.write_text(
                '{"id":"utt1","audio":"clips/sample.wav","text":"hello","language":"en","sample_rate":16000}\n',
                encoding="utf-8",
            )
            manifest = {
                "resource_type": "dataset",
                "dataset_id": "demo/asr",
                "sure_name": "demo_asr",
                "task": "ASR",
                "language": "en",
                "raw_root": str(tmp_path / "raw"),
                "raw_jsonl": "samples.jsonl",
                "field_mapping": {"key": "id", "path": "audio", "target": "text"},
            }

            summary = process_dataset_manifest_to_oref(manifest, tmp_path / "datasets")

            dataset_root = tmp_path / "datasets" / "demo_asr"
            sample_jsonl = dataset_root / "sample.jsonl"
            audio_copy = dataset_root / "audio" / "sample.wav"
            self.assertEqual(summary["samples_written"], 1)
            self.assertEqual(summary["oref_root"], str(dataset_root.resolve()))
            self.assertTrue(audio_copy.exists())
            record = json.loads(sample_jsonl.read_text(encoding="utf-8"))
            self.assertEqual(record["sample_id"], "utt1")
            self.assertEqual(record["attribute"]["path"], "audio/sample.wav")
            self.assertEqual(record["attribute"]["sample_rate"], 16000)
            self.assertEqual(record["annotation"][0]["transcription"]["text"], ["hello"])
            self.assertEqual(summary["registry_entry"]["jsonl_path"], "demo_asr/sample.jsonl")
            self.assertEqual(summary["registry_entry"]["audio_dir"], "demo_asr/audio")

    def test_process_dataset_manifest_to_oref_can_write_missing_audio_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_root = tmp_path / "raw"
            raw_root.mkdir(parents=True)
            raw_jsonl = raw_root / "samples.jsonl"
            raw_jsonl.write_text(
                '{"id":"utt1","audio":"missing/sample.wav","text":"hello","language":"en"}\n',
                encoding="utf-8",
            )
            manifest = {
                "resource_type": "dataset",
                "dataset_id": "demo/asr",
                "sure_name": "demo_asr",
                "task": "ASR",
                "language": "en",
                "raw_root": str(raw_root),
                "raw_jsonl": "samples.jsonl",
                "field_mapping": {"key": "id", "path": "audio", "target": "text"},
            }

            summary = process_dataset_manifest_to_oref(
                manifest,
                tmp_path / "datasets",
                allow_missing_audio=True,
            )

            self.assertEqual(summary["samples_written"], 1)
            self.assertEqual(summary["missing_audio_count"], 1)
            record = json.loads((tmp_path / "datasets" / "demo_asr" / "sample.jsonl").read_text(encoding="utf-8"))
            self.assertTrue(record["attribute"]["missing_audio_placeholder"])
            self.assertEqual(record["attribute"]["original_path"], "missing/sample.wav")
            self.assertEqual(record["attribute"]["path"], "audio/sample.wav")

    def test_model_manifest_materializes_model_local_weights(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            downloaded = tmp_path / "downloaded" / "model.bin"
            downloaded.parent.mkdir(parents=True)
            downloaded.write_bytes(b"weights")
            model_dir = tmp_path / "src" / "sure_eval" / "models" / "demo_model"
            manifest = {
                "resource_type": "model",
                "model_name": "demo_model",
                "task_type": "asr",
                "source": {"provider": "local", "id": str(downloaded)},
            }

            summary = materialize_model_manifest(manifest, model_dir)

            weights_manifest = json.loads(
                (model_dir / "artifacts" / "weights_manifest.json").read_text(encoding="utf-8")
            )
            expected_local_path = (model_dir / "checkpoints" / "model.bin").resolve()
            self.assertEqual(summary["local_model_path"], str(expected_local_path))
            self.assertEqual(weights_manifest["cache_policy"], "model_local_first")
            self.assertEqual(weights_manifest["materialization_strategy"], "copy_from_local_source")
            self.assertTrue(weights_manifest["checkpoint_materialized"])
            self.assertIsNone(weights_manifest["provider_cache_path"])
            self.assertTrue(Path(weights_manifest["resolved_local_model_path"]).exists())
            self.assertTrue(downloaded.exists())

    def test_model_manifest_uses_modelscope_runtime_cache_without_checkpoint_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            model_dir = tmp_path / "src" / "sure_eval" / "models" / "demo_model"
            downloaded = model_dir / ".runtime" / "modelscope_cache" / "org" / "demo-model"
            downloaded.mkdir(parents=True)
            (downloaded / "model.bin").write_bytes(b"weights")
            manifest = {
                "resource_type": "model",
                "model_name": "demo-model",
                "task_type": "asr",
                "source": {
                    "provider": "local",
                    "id": str(downloaded),
                    "original_source": {"provider": "modelscope", "id": "org/demo-model"},
                },
            }

            summary = materialize_model_manifest(manifest, model_dir)

            weights_manifest = json.loads(
                (model_dir / "artifacts" / "weights_manifest.json").read_text(encoding="utf-8")
            )
            expected_local_path = downloaded.resolve()
            self.assertEqual(summary["local_model_path"], str(expected_local_path))
            self.assertEqual(weights_manifest["materialization_strategy"], "modelscope_runtime_cache")
            self.assertFalse(weights_manifest["checkpoint_materialized"])
            self.assertEqual(weights_manifest["provider_cache_path"], str(downloaded.resolve()))
            self.assertTrue(downloaded.exists())
            self.assertTrue((downloaded / "model.bin").exists())
            self.assertFalse((model_dir / "checkpoints" / "demo-model").exists())

    def test_emit_sure_model_agent_handoff_creates_onboarding_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            model_dir = tmp_path / "src" / "sure_eval" / "models" / "zhifeixie__Mega-ASR"
            manifest_path = tmp_path / "manifest.json"
            handoff_path = tmp_path / "handoff.json"
            manifest = {
                "resource_type": "model",
                "model_name": "Mega-ASR",
                "task_type": "asr",
                "source": {
                    "provider": "modelscope",
                    "id": "zhifeixie/Mega-ASR",
                    "url": "https://modelscope.cn/models/zhifeixie/Mega-ASR",
                },
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            handoff_path.write_text("{}", encoding="utf-8")
            artifacts_dir = model_dir / "artifacts"
            artifacts_dir.mkdir(parents=True)
            weights_manifest = {
                "resolved_local_model_path": str((model_dir / "checkpoints" / "Mega-ASR").resolve()),
                "checkpoint_root": str((model_dir / "checkpoints").resolve()),
                "runtime_root": str((model_dir / ".runtime").resolve()),
            }
            (artifacts_dir / "weights_manifest.json").write_text(
                json.dumps(weights_manifest), encoding="utf-8"
            )

            summary = emit_sure_model_agent_handoff(manifest, manifest_path, handoff_path, model_dir)

            sure_handoff = json.loads((artifacts_dir / "xforge_sure_handoff.json").read_text(encoding="utf-8"))
            build_plan = json.loads((artifacts_dir / "build_plan.json").read_text(encoding="utf-8"))
            artifact_manifest = json.loads(
                (artifacts_dir / "artifact_manifest.json").read_text(encoding="utf-8")
            )
            tool_agent_request = json.loads(
                (artifacts_dir / "tool_agent_request.json").read_text(encoding="utf-8")
            )
            spec_validation = json.loads(
                (artifacts_dir / "spec_validation.json").read_text(encoding="utf-8")
            )
            spec = (model_dir / "model.spec.yaml").read_text(encoding="utf-8")
            self.assertEqual(summary["next_state"], "DOCKER_BUILD_CONFIRM")
            self.assertEqual(sure_handoff["target_agent_contract"], "docs/agents/model_tool_agent/AGENTS.md")
            self.assertEqual(sure_handoff["completed_states"], ["DISCOVER", "CLASSIFY", "PLAN", "VALIDATE_SPEC"])
            self.assertEqual(sure_handoff["xforge_completed_states"], ["FETCH_WEIGHTS"])
            self.assertTrue(sure_handoff["requires_user_confirmation_before_build"])
            self.assertEqual(sure_handoff["pending_user_interaction"]["state"], "DOCKER_BUILD_CONFIRM")
            self.assertEqual(sure_handoff["pending_user_interaction"]["continues_to_state"], "BUILD_ENV")
            self.assertEqual(build_plan["steps"][0]["state"], "VALIDATE_SPEC")
            self.assertEqual(build_plan["steps"][0]["status"], "completed_by_xforge_static_check")
            self.assertEqual(build_plan["steps"][1]["state"], "LOCAL_UV_BOOTSTRAP")
            self.assertEqual(build_plan["steps"][1]["status"], "generated_pending_execution")
            self.assertEqual(build_plan["steps"][2]["state"], "DOCKER_BUILD_CONFIRM")
            self.assertEqual(build_plan["steps"][2]["status"], "waiting_for_user_confirmation")
            self.assertEqual(build_plan["steps"][3]["status"], "completed_by_xforge")
            self.assertEqual(spec_validation["status"], "passed")
            self.assertEqual(artifact_manifest["known_artifacts"]["weights_manifest"], "present")
            self.assertEqual(artifact_manifest["known_artifacts"]["spec_validation"], "present")
            self.assertEqual(artifact_manifest["known_artifacts"]["preflight_summary"], "present")
            self.assertEqual(artifact_manifest["known_artifacts"]["local_uv_env"], "present")
            self.assertEqual(artifact_manifest["known_artifacts"]["dockerfile"], "present")
            self.assertEqual(artifact_manifest["known_artifacts"]["docker_build"], "present")
            self.assertEqual(artifact_manifest["known_artifacts"]["docker_validate"], "present")
            self.assertEqual(artifact_manifest["known_artifacts"]["config_yaml"], "present")
            self.assertEqual(artifact_manifest["known_artifacts"]["tool_agent_request"], "present")
            self.assertEqual(tool_agent_request["target_agent_contract"], "docs/agents/model_tool_agent/AGENTS.md")
            self.assertEqual(tool_agent_request["requested_start_state"], "DOCKER_BUILD_CONFIRM")
            self.assertTrue(tool_agent_request["requires_user_confirmation_before_build"])
            self.assertEqual(tool_agent_request["required_actions"][0]["state"], "LOCAL_UV_BOOTSTRAP")
            self.assertEqual(tool_agent_request["required_actions"][1]["state"], "DOCKER_BUILD_CONFIRM")
            self.assertTrue((model_dir / "Dockerfile").exists())
            self.assertTrue((model_dir / "docker_build.sh").exists())
            self.assertTrue((model_dir / "docker_validate.sh").exists())
            self.assertTrue((model_dir / "local_uv_setup.sh").exists())
            self.assertTrue((model_dir / "local_uv_validate.sh").exists())
            self.assertTrue((model_dir / "requirements-local.txt").exists())
            self.assertTrue((model_dir / "config.yaml").exists())
            self.assertTrue((model_dir / "model.py").exists())
            self.assertTrue((model_dir / "server.py").exists())
            self.assertTrue((model_dir / "validate.py").exists())
            self.assertTrue((model_dir / "__init__.py").exists())
            self.assertIn("repo_id: \"zhifeixie/Mega-ASR\"", spec)
            self.assertIn("local_path: \"./checkpoints/Mega-ASR\"", spec)

            dockerfile = (model_dir / "Dockerfile").read_text(encoding="utf-8")
            docker_build = (model_dir / "docker_build.sh").read_text(encoding="utf-8")
            docker_validate = (model_dir / "docker_validate.sh").read_text(encoding="utf-8")
            pyproject = (model_dir / "pyproject.toml").read_text(encoding="utf-8")
            local_setup = (model_dir / "local_uv_setup.sh").read_text(encoding="utf-8")
            local_validate = (model_dir / "local_uv_validate.sh").read_text(encoding="utf-8")
            local_env = json.loads((artifacts_dir / "local_uv_env.json").read_text(encoding="utf-8"))
            preflight = json.loads((artifacts_dir / "preflight_summary.json").read_text(encoding="utf-8"))
            self.assertIn("UV_PROJECT_ENVIRONMENT=/opt/zhifeixie__Mega-ASR_venv", dockerfile)
            self.assertIn("SURE_EVAL_ROOT=/workspace/sure-eval", dockerfile)
            self.assertIn("COPY pyproject.toml uv.lock requirements.txt ./", dockerfile)
            self.assertIn("COPY src/sure_eval ./src/sure_eval", dockerfile)
            self.assertIn("WORKDIR /workspace/sure-eval/src/sure_eval/models/zhifeixie__Mega-ASR", dockerfile)
            self.assertIn('"${VIRTUAL_ENV}/bin/python" -m pip install --no-deps -e .', dockerfile)
            self.assertIn("IMAGE_TAG=\"${IMAGE_TAG:-sure-zhifeixie-mega-asr:xforge-onboarding}\"", docker_build)
            self.assertIn("IMAGE_TAG=\"${IMAGE_TAG:-sure-zhifeixie-mega-asr:xforge-onboarding}\"", docker_validate)
            self.assertIn("-f src/sure_eval/models/zhifeixie__Mega-ASR/Dockerfile", docker_build)
            self.assertIn('"${MODEL_DIR}/checkpoints:${CONTAINER_MODEL_DIR}/checkpoints:ro"', docker_validate)
            self.assertIn('"${MODEL_DIR}/.runtime:${CONTAINER_MODEL_DIR}/.runtime"', docker_validate)
            self.assertIn('"${ARTIFACTS_DIR}:${CONTAINER_MODEL_DIR}/artifacts"', docker_validate)
            self.assertIn("PYTHONPATH=/workspace/sure-eval/src", docker_validate)
            self.assertIn('"librosa>=0.10.0"', pyproject)
            self.assertIn('"modelscope[audio]"', pyproject)
            self.assertIn('export UV_CACHE_DIR="${SCRIPT_DIR}/.runtime/uv-cache"', local_setup)
            self.assertIn('PYTHON_BIN="${PYTHON_FALLBACK:-python3.11}"', local_setup)
            self.assertIn('uv venv --python "${PYTHON_BIN}" .venv', local_setup)
            self.assertIn('uv pip install --python .venv/bin/python -r requirements-local.txt', local_setup)
            self.assertIn('INSTALL_SURE_EDITABLE:-0', local_setup)
            self.assertIn('SURE_XFORGE_STATIC_ONLY=1 .venv/bin/python validate.py', local_validate)
            self.assertEqual(local_env["status"], "generated_pending_execution")
            self.assertEqual(local_env["venv_path"], str((model_dir / ".venv").resolve()))
            self.assertIn("local_uv_setup.sh", local_env["setup_command"])
            self.assertIn("local_uv_validate.sh", local_env["validate_command"])
            self.assertIn("uv", preflight["package_managers"])
            self.assertEqual(preflight["local_uv_env"]["status"], "generated_pending_execution")

    def test_emit_sure_model_agent_handoff_supports_tts_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            model_dir = tmp_path / "src" / "sure_eval" / "models" / "SWivid__F5-TTS_Emilia-ZH-EN"
            manifest_path = tmp_path / "manifest.json"
            handoff_path = tmp_path / "handoff.json"
            manifest = {
                "resource_type": "model",
                "model_name": "f5-tts",
                "task_type": "tts",
                "source": {
                    "provider": "modelscope",
                    "id": "SWivid/F5-TTS_Emilia-ZH-EN",
                },
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            handoff_path.write_text("{}", encoding="utf-8")
            artifacts_dir = model_dir / "artifacts"
            artifacts_dir.mkdir(parents=True)
            (artifacts_dir / "weights_manifest.json").write_text(
                json.dumps(
                    {
                        "resolved_local_model_path": str(
                            (model_dir / ".runtime" / "modelscope_cache" / "SWivid" / "F5-TTS_Emilia-ZH-EN").resolve()
                        ),
                        "runtime_root": str((model_dir / ".runtime").resolve()),
                        "materialization_strategy": "modelscope_runtime_cache",
                    }
                ),
                encoding="utf-8",
            )

            emit_sure_model_agent_handoff(manifest, manifest_path, handoff_path, model_dir)

            spec = (model_dir / "model.spec.yaml").read_text(encoding="utf-8")
            config = (model_dir / "config.yaml").read_text(encoding="utf-8")
            model_py = (model_dir / "model.py").read_text(encoding="utf-8")
            server_py = (model_dir / "server.py").read_text(encoding="utf-8")
            self.assertIn('task_type: "tts"', spec)
            self.assertIn('input_type: "text"', spec)
            self.assertIn('output_type: "audio_path"', spec)
            self.assertIn('primary_field: "audio_path"', spec)
            self.assertIn("task: TTS", config)
            self.assertIn("name: \"tts_synthesize\"", config)
            self.assertIn("text is required", model_py)
            self.assertIn('"text": {"type": "string"}', server_py)
            self.assertIn('"required": ["text"]', server_py)
            self.assertIn('result.get("audio_path", "")', server_py)


if __name__ == "__main__":
    unittest.main()
