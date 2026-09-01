from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parents[3]
sys.path.insert(0, str(SCRIPTS_DIR))

import run_docker_build  # noqa: E402
import run_trans_validate  # noqa: E402
import mcp_smoke  # noqa: E402
import check_artifact  # noqa: E402
import materialize_trans_inputs  # noqa: E402
import prepare_fixture  # noqa: E402
import scaffold_adapter  # noqa: E402
import stage_model_payload  # noqa: E402
import write_runtime_inventory  # noqa: E402
import finalize_trans_bundle  # noqa: E402


# The partition name and registry host are site data; tests pin their own site
# policy so no real site value appears in this file.
_SITE_POLICY_DIR: tempfile.TemporaryDirectory | None = None
_SITE_POLICY_PREVIOUS: str | None = None
TEST_PARTITION = "gpu-test"
TEST_PROJECT = "example-project"
TEST_REGISTRY = "registry.example"


def setUpModule() -> None:
    global _SITE_POLICY_DIR, _SITE_POLICY_PREVIOUS
    _SITE_POLICY_DIR = tempfile.TemporaryDirectory()
    root = "/srv"
    document = "\n".join([
        "schema: sure.site.policy.v1",
        "site_id: test-site",
        "policy_version: 1",
        "storage:",
        f"  approved_models_roots: [{root}/models]",
        f"  approved_results_roots: [{root}/results]",
        f"  forbidden_output_roots: [{root}]",
        f"  runtime_root: {root}/runtime",
        "datasets:",
        f"  allowed_source_roots: [{root}/datasets]",
        "execution:",
        "  surfaces: [vc]",
        f"  vc_project: {TEST_PROJECT}",
        f"  vc_partitions: [{TEST_PARTITION}]",
        f"  vc_default_partition: {TEST_PARTITION}",
        "network:",
        f"  container_registry: {TEST_REGISTRY}",
        "container_delivery:",
        '  repository_template: "{registry}/example-org/sure-{task}-{model_name}"',
        "",
    ])
    path = Path(_SITE_POLICY_DIR.name) / "site.yaml"
    path.write_text(document, encoding="utf-8")
    _SITE_POLICY_PREVIOUS = os.environ.get("SURE_SITE_POLICY")
    os.environ["SURE_SITE_POLICY"] = str(path.resolve())


def tearDownModule() -> None:
    if _SITE_POLICY_PREVIOUS is None:
        os.environ.pop("SURE_SITE_POLICY", None)
    else:
        os.environ["SURE_SITE_POLICY"] = _SITE_POLICY_PREVIOUS
    if _SITE_POLICY_DIR is not None:
        _SITE_POLICY_DIR.cleanup()



class DockerBinaryResolutionTest(unittest.TestCase):
    """docker load, build and image inspect resolve through PATH just like the
    push does, so they must skip the agent's own bin dir for the same reason."""

    def test_execute_drops_the_agent_bin_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent_bin = root / "agent" / "bin"
            system_bin = root / "usr" / "bin"
            agent_bin.mkdir(parents=True)
            system_bin.mkdir(parents=True)
            environment = {
                "PATH": os.pathsep.join([str(agent_bin), str(system_bin)]),
                "PI_CODING_AGENT_DIR": str(root / "agent"),
            }
            completed = subprocess.CompletedProcess(["docker"], 0, "", "")
            with mock.patch.dict(os.environ, environment, clear=True):
                with mock.patch("subprocess.run", return_value=completed) as runner:
                    run_docker_build.execute(["docker", "version"], 10)
            entries = runner.call_args.kwargs["env"]["PATH"].split(os.pathsep)
        self.assertNotIn(str(agent_bin), entries)
        self.assertIn(str(system_bin), entries)


class TransScriptsTest(unittest.TestCase):
    def _python_probe_environment(self, root: Path) -> dict[str, str]:
        binaries = root / "python-probe-bin"
        binaries.mkdir()
        docker = binaries / "docker"
        docker.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"run\" ]; then printf '%s\\n' /opt/venv/bin/python; exit 0; fi\n"
            "if [ \"$1\" = \"image\" ] && [ \"$2\" = \"inspect\" ]; then printf '%s\\n' '{\"Id\":\"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\"}'; exit 0; fi\n"
            "if [ \"$1\" = \"image\" ]; then exit 0; fi\n"
            "exit 1\n",
            encoding="utf-8",
        )
        docker.chmod(0o755)
        environment = os.environ.copy()
        environment["PATH"] = f"{binaries}{os.pathsep}{environment['PATH']}"
        return environment

    def _payload_run_dir(self, root: Path) -> Path:
        """A run whose resolved input stages two weight files into the bundle."""
        run_dir = root / "run"
        artifacts = run_dir / "artifacts"
        artifacts.mkdir(parents=True)
        source = root / "delivery" / "model" / "demo"
        (source / "nested").mkdir(parents=True)
        (source / "weights.bin").write_bytes(b"weights")
        (source / "nested" / "extra.bin").write_bytes(b"extra")
        models_root = root / "sure" / "models"
        models_root.mkdir(parents=True)
        (artifacts / "trans_input_resolved.json").write_text(
            json.dumps({
                "model_name": "demo",
                "task_type": "asr",
                "model_path": str(source),
                "model_dir": str(models_root / "demo"),
                "model_mount_target": "/models/demo",
                "model_stage_policy": "copy",
                "path_policy": {"allowed_model_root": str(models_root)},
            }) + "\n",
            encoding="utf-8",
        )
        subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "stage_model_payload.py"), "--run-dir", str(run_dir)],
            check=True, capture_output=True, text=True,
        )
        return run_dir

    def _check_model_payload(self, run_dir: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable, str(SCRIPTS_DIR / "check_artifact.py"),
                "--run-dir", str(run_dir),
                "--produces", str(run_dir / "artifacts" / "model_payload_manifest.json"),
                "--kind", "model_payload",
            ],
            capture_output=True, text=True,
        )

    def test_fixture_cleanup_rejects_a_symlinked_staging_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            controlled = root / "fixture"
            outside = root / "outside"
            controlled.mkdir()
            outside.mkdir()
            sentinel = outside / "keep.wav"
            sentinel.write_bytes(b"keep")
            (controlled / "asr").symlink_to(outside, target_is_directory=True)

            with self.assertRaises(ValueError):
                prepare_fixture.clear_directory(controlled / "asr", controlled)
            with self.assertRaises(ValueError):
                finalize_trans_bundle.clear_directory(controlled / "asr", controlled)

            self.assertEqual(sentinel.read_bytes(), b"keep")

    def test_tts_fixture_declares_only_annotation_fields_the_gate_recomputes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            fixture = root / "prompt.wav"
            fixture.write_bytes(b"RIFF-prompt")
            (root / "prompt.expected.json").write_text(
                json.dumps({"text": "spoken target", "prompt_text": "prompt transcript"}) + "\n",
                encoding="utf-8",
            )
            (artifacts / "trans_input_resolved.json").write_text(
                json.dumps({
                    "model_name": "demo",
                    "task_type": "tts",
                    "fixture_path": str(fixture),
                    "path_policy": {"allowed_model_root": str(root / "models")},
                }) + "\n",
                encoding="utf-8",
            )
            subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "prepare_fixture.py"), "--run-dir", str(run_dir)],
                check=True, capture_output=True, text=True,
            )
            manifest = json.loads(
                (artifacts / "fixture_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["samples"][0]["annotation_fields"], ["text"])
            gt = json.loads((run_dir / "fixture" / "tts" / "gt.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(gt["reference_audio"], "prompt.wav")
            self.assertEqual(gt["prompt_text"], "prompt transcript")
            accepted = subprocess.run(
                [
                    sys.executable, str(SCRIPTS_DIR / "check_artifact.py"),
                    "--run-dir", str(run_dir),
                    "--produces", str(artifacts / "fixture_manifest.json"),
                    "--kind", "fixture",
                ],
                capture_output=True, text=True,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)

    def test_ground_truth_requires_a_reference_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            fixture = root / "smoke.wav"
            fixture.write_bytes(b"RIFF-smoke")
            fixture_manifest = artifacts / "fixture_manifest.json"
            fixture_manifest.write_text(
                json.dumps({
                    "status": "ready",
                    "model_dir": str(root),
                    "staged_dir": str(root),
                    "staged_path": str(fixture),
                    "gt_jsonl": str(root / "gt.jsonl"),
                    "samples": [{"audio": fixture.name, "audio_path": str(fixture), "annotation_fields": ["text"]}],
                    "sample_count": 1,
                }) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                finalize_trans_bundle.stage_fixture(run_dir, root / "model", {"task_type": "asr"})

    def test_generated_audio_is_promoted_only_from_the_validation_outputs_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = Path(temporary) / "artifacts"
            source = artifacts / "adapter_validation" / "outputs" / "speech.wav"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"RIFF-output")

            relative = finalize_trans_bundle.promote_generated_audio(
                artifacts, "/validation/outputs/speech.wav"
            )

            self.assertEqual(relative, "artifacts/outputs/speech.wav")
            self.assertEqual((artifacts / "outputs" / "speech.wav").read_bytes(), b"RIFF-output")
            outside = Path(temporary) / "outside.wav"
            outside.write_bytes(b"not-allowed")
            with self.assertRaises(ValueError):
                finalize_trans_bundle.promote_generated_audio(artifacts, str(outside))

    def test_mcp_smoke_uses_task_specific_arguments_and_primary_outputs(self) -> None:
        audio = Path("/fixture/reference.wav")
        self.assertEqual(
            mcp_smoke.tool_arguments("transcribe_audio", audio),
            {"audio_path": str(audio)},
        )
        self.assertEqual(
            mcp_smoke.tool_arguments("synthesize_speech", audio),
            {"text": "SURE smoke test", "prompt_audio_path": str(audio)},
        )
        self.assertEqual(
            mcp_smoke.tool_arguments("convert_voice", audio),
            {"source_audio_path": str(audio), "reference_audio_path": str(audio)},
        )
        self.assertEqual(
            mcp_smoke.tool_arguments("vad_predict", audio),
            {"audio_path": str(audio)},
        )
        self.assertEqual(mcp_smoke.primary_output_field("transcribe_audio"), "text")
        self.assertEqual(mcp_smoke.primary_output_field("diarize"), "segments")
        self.assertEqual(mcp_smoke.primary_output_field("sa_asr"), "segments")
        self.assertEqual(mcp_smoke.primary_output_field("sa-asr"), "segments")
        self.assertEqual(mcp_smoke.primary_output_field("vad_predict"), "speech_segments")
        self.assertEqual(mcp_smoke.primary_output_field("synthesize_speech"), "audio_path")
        self.assertEqual(mcp_smoke.primary_output_field("convert_voice"), "audio_path")

    def test_generated_audio_output_must_name_a_real_non_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            empty = root / "empty.wav"
            empty.write_bytes(b"")
            filled = root / "speech.wav"
            filled.write_bytes(b"RIFF-output")

            self.assertTrue(mcp_smoke.output_is_nonempty("text", "transcribed text"))
            self.assertFalse(mcp_smoke.output_is_nonempty("text", ""))
            self.assertTrue(mcp_smoke.output_is_nonempty("audio_path", str(filled)))
            self.assertFalse(mcp_smoke.output_is_nonempty("audio_path", str(empty)))
            self.assertFalse(mcp_smoke.output_is_nonempty("audio_path", str(root / "absent.wav")))

            self.assertTrue(mcp_smoke.output_is_nonempty("segments", [{"start": 0.0, "end": 1.0}]))
            self.assertFalse(mcp_smoke.output_is_nonempty("segments", []))
            self.assertTrue(mcp_smoke.output_is_nonempty("speech_segments", [{"start": 0.0, "end": 1.0}]))
            self.assertFalse(mcp_smoke.output_is_nonempty("speech_segments", []))

    def test_vad_task_uses_structured_timing_contract(self) -> None:
        tool_name, input_schema = scaffold_adapter.tool_contract("vad")
        contract = scaffold_adapter.io_contract_for("vad")
        self.assertEqual(tool_name, "vad_predict")
        self.assertEqual(input_schema["required"], ["audio_path"])
        self.assertEqual(contract["primary_field"], "speech_segments")
        self.assertEqual(contract["required_fields"], ["speech_segments"])
        self.assertNotIn("text", contract["required_fields"])

    def test_standalone_vad_entrypoints_infer_as_vad(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model = root / "StreamingVAD"
            model.mkdir()
            for filename, source in (
                ("firered_vad.py", "import torch\ndef vad_predict(audio_path): return []\n"),
                ("segmenter_vad.py", "import torch\ndef detect_speech(audio_path): return []\n"),
            ):
                entrypoint = root / filename
                entrypoint.write_text(source, encoding="utf-8")
                self.assertEqual(
                    materialize_trans_inputs.resolve_task_type(None, entrypoint, model),
                    "vad",
                )

            asr_entrypoint = root / "asr_with_vad_frontend.py"
            asr_entrypoint.write_text(
                "def get_speech_timestamps(audio): return []\n"
                "def vad_predict(audio): return get_speech_timestamps(audio)\n"
                "def transcribe(audio): return {'text': 'ok'}\n",
                encoding="utf-8",
            )
            self.assertEqual(
                materialize_trans_inputs.resolve_task_type(None, asr_entrypoint, model),
                "asr",
            )
            self.assertEqual(
                materialize_trans_inputs.resolve_task_type("vad", asr_entrypoint, model),
                "vad",
            )

    def test_vad_reference_requires_seconds_timebase_speech_segments(self) -> None:
        key, duration, segments = prepare_fixture.vad_reference(
            {
                "key": "fixture-key",
                "duration": 3.35,
                "speech_segments": [{"start": 0.5, "end": 2.85}],
            },
            Path("fixture.expected.json"),
        )
        self.assertEqual(key, "fixture-key")
        self.assertEqual(duration, 3.35)
        self.assertEqual(segments, [{"start": 0.5, "end": 2.85}])
        check_artifact.validate_vad_row(
            {"key": "fixture", "duration": duration, "speech_segments": segments}
        )
        with self.assertRaises(ValueError):
            prepare_fixture.vad_reference(
                {"key": "fixture-key", "duration": 3.35, "segments": [{"start": 0.5, "end": 2.85}]},
                Path("fixture.expected.json"),
            )

    def test_prepare_fixture_stages_the_public_vad_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            source = (
                REPO_ROOT
                / "fixtures"
                / "tasks"
                / "vad"
                / "librispeech_vad_smoke"
                / "librispeech_vad_001.wav"
            )
            (artifacts / "trans_input_resolved.json").write_text(
                json.dumps(
                    {
                        "model_name": "example__vad",
                        "task_type": "vad",
                        "build_context": str(source.parent),
                        "fixture_path": str(source),
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "prepare_fixture.py"), "--run-dir", str(run_dir)],
                check=True,
                capture_output=True,
                text=True,
            )
            manifest_path = artifacts / "fixture_manifest.json"
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "check_artifact.py"),
                    "--run-dir",
                    str(run_dir),
                    "--produces",
                    str(manifest_path),
                    "--kind",
                    "fixture",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            gt = json.loads(Path(manifest["gt_jsonl"]).read_text(encoding="utf-8"))
            self.assertEqual(gt["key"], "librispeech-vad-001")
            self.assertEqual(gt["duration"], 3.35)
            self.assertEqual(
                gt["speech_segments"],
                [
                    {"start": 0.551687, "end": 0.780875},
                    {"start": 1.033062, "end": 2.553813},
                ],
            )
            self.assertEqual(manifest["samples"][0]["key"], "librispeech-vad-001")

            model_dir = Path(temporary) / "bundle"
            model_dir.mkdir()
            finalize_trans_bundle.stage_fixture(
                run_dir,
                model_dir,
                {"model_name": "example__vad", "task_type": "vad"},
            )
            finalized = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(finalized["samples"][0]["key"], "librispeech-vad-001")
            self.assertEqual(finalized["samples"][0]["duration"], 3.35)

    def test_vad_validator_enforces_fixture_bounds_and_frame_scores(self) -> None:
        source = (SCRIPTS_DIR / "templates" / "validate.py").read_text(encoding="utf-8")
        contract = scaffold_adapter.io_contract_for("vad")
        source = source.replace("__TASK_TYPE__", "VAD")
        source = source.replace("__IO_CONTRACT_JSON__", json.dumps(contract))
        namespace = {
            "__name__": "generated_vad_validator",
            "__file__": str(SCRIPTS_DIR / "templates" / "validate.py"),
        }
        exec(compile(source, "generated_vad_validator.py", "exec"), namespace)
        valid = {
            "speech_segments": [{"start": 0.5, "end": 2.85}],
            "frame_scores": [{"start": 0.0, "end": 0.01, "score": 0.0}],
        }
        self.assertEqual(namespace["validate_contract"](valid, contract, 3.35), [])
        invalid = {
            "speech_segments": [{"start": 0.5, "end": 4.0}],
            "frame_scores": "not-a-list",
        }
        violations = namespace["validate_contract"](invalid, contract, 3.35)
        self.assertTrue(any("duration" in violation for violation in violations))
        self.assertIn("frame_scores must be a list", violations)

    def test_bundle_writes_reject_symlinked_destination_parents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "bundle"
            outside = root / "outside"
            bundle.mkdir()
            outside.mkdir()
            sentinel = outside / "keep.bin"
            sentinel.write_bytes(b"keep")
            (bundle / "payload").symlink_to(outside, target_is_directory=True)
            destination = bundle / "payload" / "weights.bin"

            with self.assertRaises(ValueError):
                stage_model_payload.ensure_safe_parent(bundle, destination)
            with self.assertRaises(ValueError):
                finalize_trans_bundle.ensure_safe_bundle_parent(bundle, destination)

            self.assertEqual(sentinel.read_bytes(), b"keep")

    def test_model_payload_manifest_hashes_every_staged_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = self._payload_run_dir(Path(temporary))
            payload = json.loads(
                (run_dir / "artifacts" / "model_payload_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                set(payload["files"]), {"weights.bin", "nested/extra.bin"}
            )
            self.assertEqual(payload["files"]["weights.bin"]["size_bytes"], len(b"weights"))
            self.assertEqual(len(payload["payload_identity_sha256"]), 64)
            passed = self._check_model_payload(run_dir)
            self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)

    def test_model_payload_check_rejects_an_unregistered_bundle_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = self._payload_run_dir(root)
            (root / "sure" / "models" / "demo" / "smuggled.bin").write_bytes(b"smuggled")
            rejected = self._check_model_payload(run_dir)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("exactly cover staged payload files", rejected.stdout + rejected.stderr)

    @unittest.skipIf(os.name == "nt", "docker mount syntax collides with Windows drive letters")
    def test_output_cleanup_refuses_a_mount_outside_the_run_directory(self) -> None:
        """The gate must not delete a host path the agent chose for it.

        run_command comes from the <stage>_result.json the agent under test
        writes, so a mount host path is attacker-controlled input.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            outside = root / "shared-checkout"
            outside.mkdir(parents=True)
            keep = outside / "products"
            keep.mkdir()
            spec = mock.Mock(
                mounts=[f"{outside}:/validation:ro"],
                env={"SURE_VALIDATE_ARTIFACTS_DIR": "/validation"},
            )
            with self.assertRaises(ValueError) as caught:
                run_trans_validate.prepare_container_outputs(spec, run_dir)
            self.assertIn("run directory", str(caught.exception))
            self.assertTrue(keep.is_dir(), "refused mount must not be touched")

    @unittest.skipIf(os.name == "nt", "docker mount syntax collides with Windows drive letters")
    def test_output_cleanup_refuses_to_follow_a_symlink_out_of_the_run_directory(self) -> None:
        """A symlink planted inside the run dir must not widen the blast radius."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            outside = root / "shared-checkout"
            outside.mkdir()
            keep = outside / "products"
            keep.mkdir()
            link = run_dir / "escape"
            link.symlink_to(outside, target_is_directory=True)
            spec = mock.Mock(
                mounts=[f"{link}:/validation:rw"],
                env={"SURE_VALIDATE_ARTIFACTS_DIR": "/validation"},
            )
            with self.assertRaises(ValueError) as caught:
                run_trans_validate.prepare_container_outputs(spec, run_dir)
            self.assertIn("run directory", str(caught.exception))
            self.assertTrue(keep.is_dir(), "symlinked-out mount must not be touched")

    @unittest.skipIf(os.name == "nt", "docker mount syntax collides with Windows drive letters")
    def test_output_cleanup_refuses_to_wipe_the_run_artifacts_directory(self) -> None:
        """artifacts/ holds the gate's own products, not container output."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            resolved_input = artifacts / "trans_input_resolved.json"
            resolved_input.write_text("{}", encoding="utf-8")
            spec = mock.Mock(
                mounts=[f"{artifacts}:/validation:rw"],
                env={"SURE_VALIDATE_ARTIFACTS_DIR": "/validation"},
            )
            with self.assertRaises(ValueError) as caught:
                run_trans_validate.prepare_container_outputs(spec, run_dir)
            self.assertIn("run artifacts directory", str(caught.exception))
            self.assertTrue(resolved_input.is_file(), "gate products must survive")

    @unittest.skipIf(os.name == "nt", "docker mount syntax collides with Windows drive letters")
    def test_output_cleanup_clears_the_mount_the_skill_documents(self) -> None:
        """SKILL.md prescribes -v <run_dir>/artifacts/adapter_validation:/validation."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            output = run_dir / "artifacts" / "adapter_validation"
            output.mkdir(parents=True)
            (output / "stale_result.json").write_text("{}", encoding="utf-8")
            (output / "stale_dir").mkdir()
            spec = mock.Mock(
                mounts=[f"{output}:/validation:rw"],
                env={"SURE_VALIDATE_ARTIFACTS_DIR": "/validation"},
            )
            run_trans_validate.prepare_container_outputs(spec, run_dir)
            self.assertEqual(list(output.iterdir()), [])

    @unittest.skipIf(os.name == "nt", "docker mount syntax collides with Windows drive letters")
    def test_output_cleanup_leaves_mounts_the_container_does_not_write_stage_output_into(self) -> None:
        """Only the declared stage output directory is the gate's to clear."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            models = run_dir / "models"
            models.mkdir(parents=True)
            (models / "weights.bin").write_bytes(b"weights")
            spec = mock.Mock(
                mounts=[f"{models}:/models:ro"],
                env={"SURE_VALIDATE_ARTIFACTS_DIR": "/validation"},
            )
            run_trans_validate.prepare_container_outputs(spec, run_dir)
            self.assertTrue((models / "weights.bin").is_file())

    def test_source_dockerfile_gets_git_install_and_restores_user(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "Dockerfile"
            augmented = Path(temporary) / "source.Dockerfile.sure"
            source.write_text("FROM python:3.12\nUSER 1000\n", encoding="utf-8")
            run_docker_build.write_git_augmented_dockerfile(source, augmented)
            text = augmented.read_text(encoding="utf-8")
        self.assertIn("apt-get install -y --no-install-recommends git ca-certificates", text)
        self.assertIn("USER root", text)
        self.assertTrue(text.rstrip().endswith("USER 1000"))

    def test_a_builder_stage_user_is_not_restored_onto_the_final_stage(self) -> None:
        """USER does not carry across a stage boundary, and docker only notices at run time."""
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "Dockerfile"
            augmented = Path(temporary) / "source.Dockerfile.sure"
            source.write_text(
                "FROM python:3.12 AS builder\n"
                "USER appuser\n"
                "RUN pip wheel -w /wheels .\n"
                "\n"
                "FROM nvidia/cuda:12.1.0-runtime-ubuntu22.04\n"
                "COPY --from=builder /wheels /wheels\n"
                'ENTRYPOINT ["python", "-m", "infer"]\n',
                encoding="utf-8",
            )
            run_docker_build.write_git_augmented_dockerfile(source, augmented)
            text = augmented.read_text(encoding="utf-8")
            self.assertIsNone(run_docker_build.last_user_instruction(source.read_text(encoding="utf-8")))
        self.assertFalse(text.rstrip().endswith("USER appuser"))
        self.assertTrue(text.rstrip().endswith(run_docker_build.GIT_INSTALL_RUN.rstrip()))

    def test_final_stage_user_is_still_restored_in_a_multi_stage_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "Dockerfile"
            augmented = Path(temporary) / "source.Dockerfile.sure"
            source.write_text(
                "FROM python:3.12 AS builder\n"
                "USER appuser\n"
                "RUN pip wheel -w /wheels .\n"
                "\n"
                "FROM python:3.12\n"
                "COPY --from=builder /wheels /wheels\n"
                "USER 1000\n",
                encoding="utf-8",
            )
            run_docker_build.write_git_augmented_dockerfile(source, augmented)
            text = augmented.read_text(encoding="utf-8")
        self.assertTrue(text.rstrip().endswith("USER 1000"))

    def test_source_image_runner_loads_tar_before_git_augmentation_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            delivery = root / "delivery"
            environment_dir = delivery / "environment"
            run_dir = root / "run"
            binaries = root / "bin"
            environment_dir.mkdir(parents=True)
            binaries.mkdir()
            (delivery / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")
            image_tar = environment_dir / "demo-image.tar"
            image_tar.write_bytes(b"docker-image")
            image_id = "sha256:" + "a" * 64
            repo_tag = "example/demo:source"
            augmented_tag = "sure-trans/demo:source-" + hashlib.sha256((delivery / "Dockerfile").read_bytes()).hexdigest()[:16]
            (environment_dir / "image-inspect.json").write_text(
                json.dumps({"Id": image_id, "RepoTags": [repo_tag]}) + "\n",
                encoding="utf-8",
            )
            (delivery / "SHA256SUMS").write_text(
                f"{hashlib.sha256(image_tar.read_bytes()).hexdigest()}  environment/{image_tar.name}\n",
                encoding="utf-8",
            )
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            (artifacts / "trans_input_resolved.json").write_text(
                json.dumps({
                    "dockerfile": str(delivery / "Dockerfile"),
                    "build_context": str(delivery),
                    "model_name": "demo",
                    "source_image_policy": "auto",
                    "image_tar": None,
                }) + "\n",
                encoding="utf-8",
            )
            calls = root / "docker-calls.log"
            fake_docker = binaries / "docker"
            fake_docker.write_text(
                "#!/bin/sh\n"
                "echo \"$*\" >> \"$DOCKER_CALLS\"\n"
                f"if [ \"$1\" = \"load\" ]; then echo 'Loaded image: {repo_tag}'; exit 0; fi\n"
                "if [ \"$1\" = \"build\" ]; then exit 0; fi\n"
                "if [ \"$1\" = \"image\" ] && [ \"$2\" = \"inspect\" ]; then "
                f"printf '%s\\n' '{{\"Id\":\"{image_id}\",\"RepoTags\":[\"{repo_tag}\",\"{augmented_tag}\"]}}'; exit 0; fi\n"
                "echo unexpected docker invocation >&2; exit 1\n",
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)
            output = artifacts / "source_image_result.json"
            environment = os.environ.copy()
            environment["PATH"] = f"{binaries}:{environment['PATH']}"
            environment["DOCKER_CALLS"] = str(calls)
            subprocess.run([
                sys.executable, str(SCRIPTS_DIR / "run_docker_build.py"), "--run-dir", str(run_dir),
                "--produces", str(output),
            ], check=True, capture_output=True, text=True, env=environment)
            subprocess.run([
                sys.executable, str(SCRIPTS_DIR / "check_artifact.py"), "--run-dir", str(run_dir),
                "--produces", str(output), "--kind", "source_image",
            ], check=True, capture_output=True, text=True)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["source_image_policy"], "load")
            self.assertEqual(payload["image_tar"], str(image_tar))
            self.assertTrue(payload["load_verified"])
            self.assertIn("build ", calls.read_text(encoding="utf-8"))
            self.assertTrue(payload["git_required"])

    def test_source_image_runner_falls_back_when_load_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            delivery = root / "delivery"
            run_dir = root / "run"
            binaries = root / "bin"
            delivery.mkdir()
            binaries.mkdir()
            dockerfile = delivery / "Dockerfile"
            dockerfile.write_text("FROM python:3.12\n", encoding="utf-8")
            (delivery / "cached-image.tar").write_bytes(b"invalid-image")
            repo_tag = "sure-trans/demo:source-" + hashlib.sha256(dockerfile.read_bytes()).hexdigest()[:16]
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            (artifacts / "trans_input_resolved.json").write_text(
                json.dumps({"dockerfile": str(dockerfile), "build_context": str(delivery), "model_name": "demo", "source_image_policy": "auto"}) + "\n",
                encoding="utf-8",
            )
            fake_docker = binaries / "docker"
            fake_docker.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"load\" ]; then echo invalid archive >&2; exit 1; fi\n"
                "if [ \"$1\" = \"build\" ]; then exit 0; fi\n"
                "if [ \"$1\" = \"image\" ] && [ \"$2\" = \"inspect\" ]; then "
                f"printf '%s\\n' '{{\"Id\":\"sha256:{'c' * 64}\",\"RepoTags\":[\"{repo_tag}\"]}}'; exit 0; fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)
            output = artifacts / "source_image_result.json"
            environment = os.environ.copy()
            environment["PATH"] = f"{binaries}:{environment['PATH']}"
            subprocess.run([
                sys.executable, str(SCRIPTS_DIR / "run_docker_build.py"), "--run-dir", str(run_dir),
                "--produces", str(output),
            ], check=True, capture_output=True, text=True, env=environment)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["source_image_policy"], "build")
            self.assertTrue(payload["fallback_to_build"])
            self.assertEqual(payload["source_image_attempts"][-1]["mode"], "load")

    def test_docker_build_runner_executes_build_and_inspect(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            delivery = root / "delivery"
            run_dir = root / "run"
            binaries = root / "bin"
            delivery.mkdir()
            binaries.mkdir()
            (delivery / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")
            repo_tag = "sure-trans/demo:source-" + hashlib.sha256((delivery / "Dockerfile").read_bytes()).hexdigest()[:16]
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            (artifacts / "trans_input_resolved.json").write_text(
                json.dumps({
                    "dockerfile": str(delivery / "Dockerfile"),
                    "build_context": str(delivery),
                    "model_name": "demo",
                }) + "\n",
                encoding="utf-8",
            )
            fake_docker = binaries / "docker"
            fake_docker.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"build\" ]; then exit 0; fi\n"
                "if [ \"$1\" = \"image\" ] && [ \"$2\" = \"inspect\" ]; then "
                f"printf '%s\\n' '{{\"Id\":\"sha256:{'b' * 64}\",\"RepoTags\":[\"{repo_tag}\"]}}'; exit 0; fi\n"
                "echo unexpected docker invocation >&2; exit 1\n",
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)
            output = artifacts / "source_image_result.json"
            environment = os.environ.copy()
            environment["PATH"] = f"{binaries}:{environment['PATH']}"
            subprocess.run([
                sys.executable,
                str(SCRIPTS_DIR / "run_docker_build.py"),
                "--run-dir",
                str(run_dir),
                "--produces",
                str(output),
            ], check=True, capture_output=True, text=True, env=environment)
            subprocess.run([
                sys.executable,
                str(SCRIPTS_DIR / "check_artifact.py"),
                "--run-dir",
                str(run_dir),
                "--produces",
                str(output),
                "--kind",
                "source_image",
            ], check=True, capture_output=True, text=True)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["source_image_policy"], "build")
            self.assertTrue(payload["build_executed"])
            self.assertEqual(payload["build_exit_code"], 0)
            self.assertEqual(payload["image_id"], "sha256:" + "b" * 64)

    def test_materialize_and_dependency_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            delivery = root / "delivery"
            code = delivery / "code"
            runtime = delivery / "runtime"
            model = delivery / "model" / "demo"
            run_dir = root / "run"
            code.mkdir(parents=True)
            runtime.mkdir()
            model.mkdir(parents=True)
            (delivery / "requirements.txt").write_text("torch==2.9.1\ntransformers==4.57.6\n", encoding="utf-8")
            examples = delivery / "examples"
            examples.mkdir()
            (examples / "smoke.wav").write_bytes(b"RIFF-smoke")
            (examples / "smoke.expected.json").write_text(
                json.dumps({"text": "fixture annotation"}) + "\n", encoding="utf-8"
            )
            (delivery / "Dockerfile").write_text(
                "FROM python:3.12\nCOPY requirements.txt /opt/requirements.txt\nCOPY code/ /opt/code/\nCOPY runtime/ /opt/runtime/\n",
                encoding="utf-8",
            )
            (code / "infer.py").write_text("import torch\nfrom transformers import AutoModel\n", encoding="utf-8")
            (model / "config.json").write_text("{}\n", encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "materialize_trans_inputs.py"),
                    "--dockerfile",
                    str(delivery / "Dockerfile"),
                    "--model",
                    str(model),
                    "--inference-entrypoint",
                    str(code / "infer.py"),
                    "--framework",
                    "pytorch",
                    "--model-framework",
                    "transformers",
                    "--task-type",
                    "asr",
                    "--run-dir",
                    str(run_dir),
                    "--repo-root",
                    str(root),
                    "--image-version",
                    "0.1.0",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "inspect_dependencies.py"), "--run-dir", str(run_dir)],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "detect_framework.py"), "--run-dir", str(run_dir)],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "prepare_fixture.py"), "--run-dir", str(run_dir)],
                check=True,
                capture_output=True,
                text=True,
            )

            resolved = json.loads((run_dir / "artifacts" / "trans_input_resolved.json").read_text(encoding="utf-8"))
            report = json.loads((run_dir / "artifacts" / "inference_dependency_report.json").read_text(encoding="utf-8"))
            framework = json.loads((run_dir / "artifacts" / "framework_detection.json").read_text(encoding="utf-8"))
            fixture = json.loads((run_dir / "artifacts" / "fixture_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(resolved["framework"], "pytorch")
            self.assertEqual(resolved["model_framework"], "transformers")
            self.assertEqual(resolved["task_type"], "asr")
            self.assertEqual(resolved["package_profile"], "docker-registry")
            self.assertEqual(resolved["source_image_policy"], "auto")
            self.assertEqual(report["status"], "ready")
            self.assertEqual(report["unresolved"], [])
            self.assertIn("torch", report["python_imports"])
            self.assertIn("code/", report["docker_copy_sources"])
            self.assertEqual(framework["status"], "ready")
            self.assertEqual(framework["detected_framework"], "pytorch")
            self.assertEqual(framework["detected_model_framework"], "transformers")
            self.assertFalse(framework["clarification_required"])
            self.assertEqual(fixture["sample_count"], 1)
            self.assertTrue(Path(fixture["staged_path"]).is_file())
            self.assertEqual(Path(fixture["model_dir"]), run_dir)
            self.assertEqual(Path(fixture["staged_dir"]), run_dir / "fixture" / "asr")
            self.assertEqual(Path(fixture["gt_jsonl"]), run_dir / "fixture" / "asr" / "gt.jsonl")
            self.assertEqual(fixture["samples"][0]["audio"], "smoke.wav")
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "check_artifact.py"),
                    "--run-dir",
                    str(run_dir),
                    "--produces",
                    str(run_dir / "artifacts" / "fixture_manifest.json"),
                    "--kind",
                    "fixture",
                ],
                check=True,
                capture_output=True,
                text=True,
            )

    def test_validation_runner_executes_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            artifacts = run_dir / "artifacts"
            artifacts.mkdir()
            (artifacts / "execution_compat.json").write_text(
                json.dumps({"status": "ready", "compat_ok": True, "selected_device": "cpu"}) + "\n",
                encoding="utf-8",
            )
            result = artifacts / "import_result.json"
            result.write_text(
                json.dumps({"status": "pending", "run_command": [sys.executable, "-c", "print('import ok')"]}) + "\n",
                encoding="utf-8",
            )
            subprocess.run([
                sys.executable, str(SCRIPTS_DIR / "run_trans_validate.py"), "--run-dir", str(run_dir),
                "--produces", str(result), "--kind", "import",
            ], check=True, capture_output=True, text=True)
            payload = json.loads(result.read_text(encoding="utf-8"))
            self.assertTrue(payload["executed"])
            self.assertTrue(payload["import_passed"])
            self.assertEqual(payload["status"], "passed")

    def test_gate_error_carries_the_reason_the_container_recorded(self) -> None:
        """A failed stage leaves its reason in a file, never on stdout.

        templates/validate.py catches every stage exception and records it in
        the mounted artifacts directory, so the job log holds no error at all.
        Without reading that file back, the gate reports a bare job failure and
        diagnose_oom never sees the CUDA OOM it was written to catch.
        """
        if os.name == "nt":
            self.skipTest("needs a POSIX fake docker on PATH")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            (artifacts / "execution_compat.json").write_text(
                json.dumps({"status": "ready", "compat_ok": True, "selected_device": "cpu"}) + "\n",
                encoding="utf-8",
            )
            validation = artifacts / "adapter_validation"
            validation.mkdir()
            oom = (
                "CUDA out of memory. Tried to allocate 130.00 MiB. GPU 0 has a total capacity "
                "of 23.52 GiB of which 6.06 MiB is free."
            )
            (validation / "load_result.json").write_text(
                json.dumps({"load_passed": False, "error": oom}) + "\n", encoding="utf-8"
            )
            binaries = root / "bin"
            binaries.mkdir()
            docker = binaries / "docker"
            docker.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            docker.chmod(0o755)
            result = artifacts / "load_result.json"
            result.write_text(json.dumps({
                "status": "pending",
                "run_command": [
                    "docker", "run", "--rm",
                    "-v", f"{validation}:/validation:rw",
                    "-e", "SURE_VALIDATE_ARTIFACTS_DIR=/validation",
                    "demo:adapter", "python", "/opt/sure_trans/validate.py", "--stage", "load",
                ],
            }) + "\n", encoding="utf-8")
            environment = os.environ.copy()
            environment["PATH"] = f"{binaries}{os.pathsep}{environment['PATH']}"
            blocked = subprocess.run([
                sys.executable, str(SCRIPTS_DIR / "run_trans_validate.py"), "--run-dir", str(run_dir),
                "--produces", str(result), "--kind", "load",
            ], capture_output=True, text=True, env=environment)
            self.assertNotEqual(blocked.returncode, 0)
            payload = json.loads(result.read_text(encoding="utf-8"))
            self.assertIn("CUDA out of memory", payload["error"])
            self.assertIn("GPU VRAM exhausted", payload["error"])

    def _equivalence_run_dir(
        self,
        root: Path,
        baseline: object,
        adapter: object,
        *,
        primary_field: str = "text",
    ) -> tuple[Path, Path]:
        """Seed a run whose equivalence command succeeds but proves nothing.

        The command exits 0 either way, so only a real comparison of the two
        recorded outputs can tell equivalent from not.
        """
        run_dir = root / "run"
        artifacts = run_dir / "artifacts"
        artifacts.mkdir(parents=True)
        (artifacts / "execution_compat.json").write_text(
            json.dumps({"status": "ready", "compat_ok": True, "selected_device": "cpu"}) + "\n",
            encoding="utf-8",
        )
        baseline_path = artifacts / "original_output.json"
        adapter_path = artifacts / "sample_output.json"
        baseline_path.write_text(json.dumps(baseline) + "\n", encoding="utf-8")
        adapter_path.write_text(json.dumps(adapter) + "\n", encoding="utf-8")
        (artifacts / "adapter_manifest.json").write_text(
            json.dumps({"io_contract": {"primary_field": primary_field}}) + "\n",
            encoding="utf-8",
        )
        result = artifacts / "equivalence_result.json"
        result.write_text(
            json.dumps({
                "status": "pending",
                "baseline_output": str(baseline_path),
                "adapter_output": str(adapter_path),
                "run_command": [sys.executable, "-c", "pass"],
            }) + "\n",
            encoding="utf-8",
        )
        return run_dir, result

    def _run_equivalence(self, run_dir: Path, result: Path) -> subprocess.CompletedProcess:
        return subprocess.run([
            sys.executable, str(SCRIPTS_DIR / "run_trans_validate.py"), "--run-dir", str(run_dir),
            "--produces", str(result), "--kind", "equivalence",
        ], check=False, capture_output=True, text=True)

    def test_equivalence_passes_when_both_outputs_carry_the_same_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, result = self._equivalence_run_dir(
                Path(temporary), {"text": "深交所副总经理周明指出"}, {"text": "深交所副总经理周明指出"}
            )
            process = self._run_equivalence(run_dir, result)
            self.assertEqual(process.returncode, 0, process.stderr)
            payload = json.loads(result.read_text(encoding="utf-8"))
            self.assertTrue(payload["equivalent"])
            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["comparison_evidence"]["baseline_text"], "深交所副总经理周明指出")

    def test_equivalence_fails_when_the_two_outputs_differ(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, result = self._equivalence_run_dir(
                Path(temporary), {"text": "深交所副总经理周明指出"}, {"text": "完全不同的转写"}
            )
            process = self._run_equivalence(run_dir, result)
            self.assertNotEqual(process.returncode, 0)
            payload = json.loads(result.read_text(encoding="utf-8"))
            self.assertFalse(payload["equivalent"])
            self.assertEqual(payload["status"], "failed")

    def test_equivalence_compares_vad_segment_arrays(self) -> None:
        segments = [{"start": 0.5, "end": 2.85}]
        frame_scores = [{"start": 0.0, "end": 0.01, "score": 0.5}]
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, result = self._equivalence_run_dir(
                Path(temporary),
                {"speech_segments": segments, "frame_scores": frame_scores},
                {"speech_segments": segments, "frame_scores": frame_scores},
                primary_field="speech_segments",
            )
            process = self._run_equivalence(run_dir, result)
            self.assertEqual(process.returncode, 0, process.stderr)
            payload = json.loads(result.read_text(encoding="utf-8"))
            self.assertTrue(payload["equivalent"])
            self.assertEqual(
                payload["comparison_evidence"]["baseline_value"],
                {"speech_segments": segments, "frame_scores": frame_scores},
            )

            run_dir, result = self._equivalence_run_dir(
                Path(temporary) / "missing-scores",
                {"speech_segments": segments, "frame_scores": frame_scores},
                {"speech_segments": segments},
                primary_field="speech_segments",
            )
            process = self._run_equivalence(run_dir, result)
            self.assertNotEqual(process.returncode, 0)

    def test_equivalence_rejects_an_output_field_that_is_not_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, result = self._equivalence_run_dir(
                Path(temporary), {"text": "一样的文本"}, {"text": "一样的文本"}
            )
            payload = json.loads(result.read_text(encoding="utf-8"))
            payload["baseline_output"] = "一样的文本"
            result.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            process = self._run_equivalence(run_dir, result)
            self.assertNotEqual(process.returncode, 0)
            self.assertIn("baseline_output", (process.stdout + process.stderr))

    def test_execution_compat_cpu_device_stays_local(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            artifacts = run_dir / "artifacts"
            binaries = root / "bin"
            artifacts.mkdir(parents=True)
            binaries.mkdir()
            docker = binaries / "docker"
            docker.write_text(
                "#!/bin/sh\n"
                "echo '{\"python_ok\":true,\"torch\":\"test\",\"cuda_available\":false,\"bf16_supported\":false,\"transformers\":\"test\"}'\n",
                encoding="utf-8",
            )
            docker.chmod(0o755)
            (artifacts / "trans_input_resolved.json").write_text(
                json.dumps({"device": "cpu", "gpu_required": False, "bf16_required": False, "model_framework": "transformers"}) + "\n",
                encoding="utf-8",
            )
            (artifacts / "source_image_result.json").write_text(
                json.dumps({"image": "demo", "image_id": "demo-id"}) + "\n",
                encoding="utf-8",
            )
            output = artifacts / "execution_compat.json"
            environment = os.environ.copy()
            environment["PATH"] = f"{binaries}:{environment['PATH']}"
            subprocess.run([
                sys.executable, str(SCRIPTS_DIR / "run_execution_compat.py"), "--run-dir", str(run_dir), "--produces", str(output),
            ], check=True, capture_output=True, text=True, env=environment)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(payload["compat_ok"])
            self.assertEqual(payload["selected_device"], "cpu")
            self.assertEqual(payload["execution_surface"], "local_docker")
            self.assertIsNone(payload["fallback"])

    def test_execution_compat_gpu_device_blocks_without_vc(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            artifacts = run_dir / "artifacts"
            binaries = root / "bin"
            artifacts.mkdir(parents=True)
            binaries.mkdir()
            docker = binaries / "docker"
            docker.write_text(
                "#!/bin/sh\n"
                "case \" $* \" in *\" push \"*) echo 'latest: digest: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa size: 1';; esac\n"
                "exit 0\n",
                encoding="utf-8",
            )
            docker.chmod(0o755)
            (artifacts / "trans_input_resolved.json").write_text(
                json.dumps({"device": "auto", "gpu_required": False, "bf16_required": False, "model_name": "demo", "model_framework": "transformers"}) + "\n",
                encoding="utf-8",
            )
            (artifacts / "source_image_result.json").write_text(
                json.dumps({"image": "demo", "image_id": "demo-id"}) + "\n",
                encoding="utf-8",
            )
            output = artifacts / "execution_compat.json"
            environment = os.environ.copy()
            environment["PATH"] = str(binaries)
            failed = subprocess.run([
                sys.executable, str(SCRIPTS_DIR / "run_execution_compat.py"), "--run-dir", str(run_dir), "--produces", str(output),
            ], capture_output=True, text=True, env=environment)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("vc is required for GPU validation", failed.stderr)

    def _fake_docker(self, binaries: Path, served_digest: str) -> Path:
        """A docker whose `pull <tag>` reports the digest the registry serves."""
        binaries.mkdir(parents=True, exist_ok=True)
        docker = binaries / "docker"
        docker.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "pull" ]; then\n'
            f'  echo "Digest: {served_digest}"\n'
            '  echo "Status: Image is up to date for $2"\n'
            "  exit 0\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        docker.chmod(0o755)
        return docker

    def test_registry_tag_digest_reads_back_what_the_registry_serves(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            digest = "sha256:" + "b" * 64
            self._fake_docker(root / "bin", digest)
            environment = os.environ.copy()
            environment["PATH"] = f"{root / 'bin'}{os.pathsep}{environment['PATH']}"
            probe = subprocess.run([
                sys.executable, "-c",
                "import sys, json; sys.path.insert(0, sys.argv[1]); import vc_exec;"
                "print(vc_exec.registry_tag_digest(sys.argv[2], __import__('pathlib').Path(sys.argv[3])))",
                str(SCRIPTS_DIR), "registry.example/demo:0.1.0", str(root / "pull.log"),
            ], capture_output=True, text=True, env=environment)
            self.assertEqual(probe.returncode, 0, probe.stderr)
            self.assertEqual(probe.stdout.strip(), digest)

    def test_vc_exec_refuses_to_submit_when_the_tag_moved(self) -> None:
        """vc submit only takes repo:tag, so pinning is proven before the submit."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._fake_docker(root / "bin", "sha256:" + "c" * 64)
            environment = os.environ.copy()
            environment["PATH"] = f"{root / 'bin'}{os.pathsep}{environment['PATH']}"
            refused = subprocess.run([
                sys.executable, str(SCRIPTS_DIR / "vc_exec.py"),
                "--image", "registry.example/demo:0.1.0",
                "--expect-digest", "sha256:" + "d" * 64,
                "--command", "true",
                "--log-dir", str(root / "logs"),
                "--produces", str(root / "smoke.json"),
            ], capture_output=True, text=True, env=environment)
            self.assertNotEqual(refused.returncode, 0)
            output = refused.stdout + refused.stderr
            self.assertIn("c" * 64, output)
            self.assertIn("d" * 64, output)
            self.assertNotIn("vc submit", output)

    def test_registry_gate_takes_a_tag_submit_that_proves_the_digest(self) -> None:
        """VC answers 镜像不存在 to any repo@sha256:... reference.

        Requiring the smoke to *run* on a digest-pinned reference therefore made
        the unit unsatisfiable on GPU. The pin is proven by the digest the tag
        resolved to at submit time instead.
        """
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            artifacts = run_dir / "artifacts"
            artifacts.mkdir()
            (artifacts / "execution_compat.json").write_text(
                json.dumps({"status": "ready", "compat_ok": True, "selected_device": "cuda"}) + "\n",
                encoding="utf-8",
            )
            digest = "sha256:" + "e" * 64
            smoke_log = artifacts / "vc_logs" / "post_pull_smoke" / "stdout.log"
            smoke_log.parent.mkdir(parents=True)
            smoke_log.write_text("mcp smoke ok\n", encoding="utf-8")
            (smoke_log.parent / "mcp_smoke.json").write_text(json.dumps({
                "status": "passed", "tool": "transcribe_audio",
                "initialize": {"ok": True}, "tools_list": {"ok": True},
                "tools_call": {"ok": True, "text_nonempty": True}, "shutdown": {"ok": True},
            }) + "\n", encoding="utf-8")
            base = {
                "schema": "sure.trans.docker_registry_result.v1",
                "status": "passed",
                "target_image": "registry.example/demo:0.1.0",
                "target_image_digest": digest,
                "target_image_ref": f"registry.example/demo@{digest}",
                "pull_verified": True,
            }
            smoke = {
                "vc_job_id": "job-smoke-123",
                "vc_partition": "gpu-test",
                "exit_code": 0,
                "image_ref": "registry.example/demo:0.1.0",
                "resolved_digest": digest,
                "log_path": str(smoke_log),
            }

            accepted = artifacts / "registry_tag_submit.json"
            accepted.write_text(json.dumps({**base, "post_pull_smoke": smoke}) + "\n", encoding="utf-8")
            subprocess.run([
                sys.executable, str(SCRIPTS_DIR / "check_artifact.py"), "--run-dir", str(run_dir),
                "--produces", str(accepted), "--kind", "registry",
            ], check=True, capture_output=True, text=True)

            moved = artifacts / "registry_tag_moved.json"
            moved.write_text(json.dumps({
                **base,
                "post_pull_smoke": {**smoke, "resolved_digest": "sha256:" + "f" * 64},
            }) + "\n", encoding="utf-8")
            blocked = subprocess.run([
                sys.executable, str(SCRIPTS_DIR / "check_artifact.py"), "--run-dir", str(run_dir),
                "--produces", str(moved), "--kind", "registry",
            ], capture_output=True, text=True)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("resolved_digest", blocked.stdout + blocked.stderr)

            unproven = artifacts / "registry_tag_unproven.json"
            unproven.write_text(json.dumps({
                **base,
                "post_pull_smoke": {k: v for k, v in smoke.items() if k != "resolved_digest"},
            }) + "\n", encoding="utf-8")
            blocked = subprocess.run([
                sys.executable, str(SCRIPTS_DIR / "check_artifact.py"), "--run-dir", str(run_dir),
                "--produces", str(unproven), "--kind", "registry",
            ], capture_output=True, text=True)
            self.assertNotEqual(blocked.returncode, 0)

    def test_registry_gate_requires_post_pull_smoke_for_gpu(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            artifacts = run_dir / "artifacts"
            artifacts.mkdir()
            (artifacts / "execution_compat.json").write_text(
                json.dumps({"status": "ready", "compat_ok": True, "selected_device": "cuda"}) + "\n",
                encoding="utf-8",
            )
            digest = "sha256:" + "a" * 64
            base = {
                "schema": "sure.trans.docker_registry_result.v1",
                "status": "passed",
                "target_image": "registry.example/demo:0.1.0",
                "target_image_digest": digest,
                "target_image_ref": f"registry.example/demo@{digest}",
                "pull_verified": True,
            }
            missing_smoke = artifacts / "registry_no_smoke.json"
            missing_smoke.write_text(json.dumps(base) + "\n", encoding="utf-8")
            blocked = subprocess.run([
                sys.executable, str(SCRIPTS_DIR / "check_artifact.py"), "--run-dir", str(run_dir),
                "--produces", str(missing_smoke), "--kind", "registry",
            ], capture_output=True, text=True)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("post_pull_smoke", blocked.stdout + blocked.stderr)

            smoke_log = artifacts / "vc_logs" / "post_pull_smoke" / "stdout.log"
            smoke_log.parent.mkdir(parents=True)
            smoke_log.write_text("mcp smoke ok\n", encoding="utf-8")
            without_evidence = artifacts / "registry_without_evidence.json"
            without_evidence.write_text(json.dumps({
                **base,
                "post_pull_smoke": {
                    "vc_job_id": "job-smoke-123",
                    "vc_partition": "gpu-test",
                    "exit_code": 0,
                    "image_ref": "registry.example/demo:0.1.0",
                    "resolved_digest": digest,
                    "log_path": str(smoke_log),
                },
            }) + "\n", encoding="utf-8")
            blocked = subprocess.run([
                sys.executable, str(SCRIPTS_DIR / "check_artifact.py"), "--run-dir", str(run_dir),
                "--produces", str(without_evidence), "--kind", "registry",
            ], capture_output=True, text=True)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("mcp_smoke.json", blocked.stdout + blocked.stderr)

            evidence = smoke_log.parent / "mcp_smoke.json"
            evidence.write_text(json.dumps({
                "schema": "sure.trans.mcp_smoke.v1",
                "status": "passed",
                "tool": "transcribe_audio",
                "initialize": {"ok": True},
                "tools_list": {"ok": True, "tools": ["transcribe_audio"]},
                "tools_call": {"ok": True, "text_nonempty": True, "text": "ok"},
                "shutdown": {"ok": True},
                "error": None,
            }) + "\n", encoding="utf-8")
            with_smoke = artifacts / "registry_with_smoke.json"
            with_smoke.write_text(json.dumps({
                **base,
                "post_pull_smoke": {
                    "vc_job_id": "job-smoke-123",
                    "vc_partition": "gpu-test",
                    "exit_code": 0,
                    "image_ref": "registry.example/demo:0.1.0",
                    "resolved_digest": digest,
                    "log_path": str(smoke_log),
                },
            }) + "\n", encoding="utf-8")
            subprocess.run([
                sys.executable, str(SCRIPTS_DIR / "check_artifact.py"), "--run-dir", str(run_dir),
                "--produces", str(with_smoke), "--kind", "registry",
            ], check=True, capture_output=True, text=True)

            (artifacts / "execution_compat.json").write_text(
                json.dumps({"status": "ready", "compat_ok": True, "selected_device": "cpu"}) + "\n",
                encoding="utf-8",
            )
            subprocess.run([
                sys.executable, str(SCRIPTS_DIR / "check_artifact.py"), "--run-dir", str(run_dir),
                "--produces", str(missing_smoke), "--kind", "registry",
            ], check=True, capture_output=True, text=True)

    def test_infers_task_and_blocks_incompatible_framework(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            delivery = root / "delivery"
            code = delivery / "code"
            model = delivery / "model" / "demo"
            run_dir = root / "run"
            code.mkdir(parents=True)
            model.mkdir(parents=True)
            (delivery / "Dockerfile").write_text("FROM python:3.12\nCOPY code/ /opt/code/\n", encoding="utf-8")
            (code / "infer.py").write_text(
                "import tensorflow as tf\n\ndef transcribe(audio_path): return tf.constant(audio_path)\n",
                encoding="utf-8",
            )
            (model / "weights.bin").write_bytes(b"weights")
            subprocess.run([
                sys.executable, str(SCRIPTS_DIR / "materialize_trans_inputs.py"), "--dockerfile", str(delivery / "Dockerfile"),
                "--model", str(model), "--inference-entrypoint", str(code / "infer.py"), "--framework", "pytorch",
                "--model-framework", "transformers",
                "--run-dir", str(run_dir), "--repo-root", str(root), "--image-version", "0.1.0",
            ], check=True, capture_output=True, text=True)
            subprocess.run([sys.executable, str(SCRIPTS_DIR / "inspect_dependencies.py"), "--run-dir", str(run_dir)], check=True, capture_output=True, text=True)
            subprocess.run([sys.executable, str(SCRIPTS_DIR / "detect_framework.py"), "--run-dir", str(run_dir)], check=True, capture_output=True, text=True)
            resolved = json.loads((run_dir / "artifacts" / "trans_input_resolved.json").read_text(encoding="utf-8"))
            framework = json.loads((run_dir / "artifacts" / "framework_detection.json").read_text(encoding="utf-8"))
            self.assertEqual(resolved["task_type"], "asr")
            self.assertEqual(framework["detected_framework"], "tensorflow")
            self.assertEqual(framework["status"], "blocked")
            self.assertFalse(framework["framework_requirement_met"])

    def test_runtime_inventory_claims_a_verified_identity_only_for_an_image_build_context(self) -> None:
        """A directory build context is whatever the build command pointed at."""
        pinned = "docker-image://registry.example/sure-harness@sha256:" + "c" * 64
        verified = write_runtime_inventory.identity_evidence(pinned)
        self.assertTrue(verified["identity_verified"])
        self.assertEqual(verified["identity_source"], "image-digest")

        unverified = write_runtime_inventory.identity_evidence("directory")
        self.assertFalse(unverified["identity_verified"])
        self.assertEqual(unverified["identity_source"], "build-directory")
        self.assertTrue(unverified["embedded"])

    def test_standalone_materialize_requires_the_shared_site_resolver(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            standalone = root / "standalone"
            standalone.mkdir()
            shutil.copyfile(
                SCRIPTS_DIR / "materialize_trans_inputs.py",
                standalone / "materialize_trans_inputs.py",
            )
            self.assertFalse((standalone / "vc_exec.py").exists())
            delivery = root / "delivery"
            code = delivery / "code"
            model = delivery / "model" / "demo"
            run_dir = root / "run"
            code.mkdir(parents=True)
            model.mkdir(parents=True)
            (delivery / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")
            (code / "infer.py").write_text("import torch\n", encoding="utf-8")
            (model / "weights.bin").write_bytes(b"weights")
            result = subprocess.run([
                sys.executable, str(standalone / "materialize_trans_inputs.py"),
                "--dockerfile", str(delivery / "Dockerfile"), "--model", str(model),
                "--inference-entrypoint", str(code / "infer.py"), "--framework", "pytorch",
                "--model-framework", "transformers", "--run-dir", str(run_dir),
                "--repo-root", str(root), "--image-version", "0.2.0",
                "--vc-partition", TEST_PARTITION, "--task-type", "asr",
            ], check=False, capture_output=True, text=True, cwd=standalone)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("complete sure-harness checkout", result.stderr)

    def test_a_torch_token_does_not_settle_a_delivery_that_also_ships_tensorflow(self) -> None:
        """SKILL.md blocks when PyTorch is not the primary computation framework."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            delivery = root / "delivery"
            code = delivery / "code"
            model = delivery / "model" / "demo"
            run_dir = root / "run"
            code.mkdir(parents=True)
            model.mkdir(parents=True)
            (delivery / "Dockerfile").write_text("FROM python:3.12\nCOPY code/ /opt/code/\n", encoding="utf-8")
            (code / "infer.py").write_text(
                "import tensorflow as tf\nimport torch\n\n"
                "def transcribe(audio_path):\n"
                "    features = torch.zeros(1)\n"
                "    return tf.constant(audio_path)\n",
                encoding="utf-8",
            )
            (model / "weights.bin").write_bytes(b"weights")
            subprocess.run([
                sys.executable, str(SCRIPTS_DIR / "materialize_trans_inputs.py"), "--dockerfile", str(delivery / "Dockerfile"),
                "--model", str(model), "--inference-entrypoint", str(code / "infer.py"), "--framework", "pytorch",
                "--model-framework", "transformers",
                "--run-dir", str(run_dir), "--repo-root", str(root), "--image-version", "0.1.0",
            ], check=True, capture_output=True, text=True)
            subprocess.run([sys.executable, str(SCRIPTS_DIR / "inspect_dependencies.py"), "--run-dir", str(run_dir)], check=True, capture_output=True, text=True)
            subprocess.run([sys.executable, str(SCRIPTS_DIR / "detect_framework.py"), "--run-dir", str(run_dir)], check=True, capture_output=True, text=True)
            framework = json.loads((run_dir / "artifacts" / "framework_detection.json").read_text(encoding="utf-8"))
            self.assertEqual(framework["detected_framework"], "tensorflow")
            self.assertEqual(framework["status"], "blocked")
            self.assertFalse(framework["framework_requirement_met"])

    def test_custom_pytorch_model_framework_continues_with_architecture_clarification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            delivery = root / "delivery"
            code = delivery / "code"
            model = delivery / "model" / "demo"
            run_dir = root / "run"
            code.mkdir(parents=True)
            model.mkdir(parents=True)
            (delivery / "Dockerfile").write_text("FROM python:3.12\nCOPY code/ /opt/code/\n", encoding="utf-8")
            (code / "infer.py").write_text(
                "import torch\nfrom torch import nn\n"
                "class AcousticModel(nn.Module):\n"
                "    def __init__(self):\n"
                "        super().__init__()\n"
                "        self.encoder = nn.Conv1d(80, 256, 3)\n"
                "def transcribe(audio_path): return audio_path\n",
                encoding="utf-8",
            )
            (model / "weights.bin").write_bytes(b"weights")
            subprocess.run([
                sys.executable, str(SCRIPTS_DIR / "materialize_trans_inputs.py"),
                "--dockerfile", str(delivery / "Dockerfile"),
                "--model", str(model),
                "--inference-entrypoint", str(code / "infer.py"),
                "--framework", "pytorch",
                "--model-framework", "wenet",
                "--run-dir", str(run_dir),
                "--repo-root", str(root),
                "--image-version", "0.1.0",
            ], check=True, capture_output=True, text=True)
            subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "inspect_dependencies.py"), "--run-dir", str(run_dir)],
                check=True, capture_output=True, text=True,
            )
            subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "detect_framework.py"), "--run-dir", str(run_dir)],
                check=True, capture_output=True, text=True,
            )
            output = run_dir / "artifacts" / "framework_detection.json"
            framework = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(framework["status"], "ready")
            self.assertEqual(framework["declared_model_framework"], "wenet")
            self.assertEqual(framework["detected_model_framework"], "custom")
            self.assertTrue(framework["model_framework_matches"])
            self.assertTrue(framework["clarification_required"])
            self.assertIn("cnn", framework["architecture_signals"])
            self.assertIn("custom PyTorch", framework["architecture_clarification"])
            subprocess.run([
                sys.executable, str(SCRIPTS_DIR / "check_artifact.py"),
                "--run-dir", str(run_dir),
                "--produces", str(output),
                "--kind", "framework",
            ], check=True, capture_output=True, text=True)

    def _harness_binding(self) -> dict[str, str]:
        return {
            "runtime_id": "sure-harness-v1-py311-abc123",
            "lock_sha256": "a" * 64,
            "python_executable": "/opt/sure-harness/sure-harness-v1-py311-abc123/bin/python",
            "manifest_path": "/opt/sure-harness/sure-harness-v1-py311-abc123/runtime-manifest.json",
            "runtime_root": "/opt/sure-harness/sure-harness-v1-py311-abc123",
        }

    def test_env_supplied_runtime_image_must_carry_the_active_runtime(self) -> None:
        """The env override is the path the README documents, so it must be checked too."""
        harness = self._harness_binding()
        reference = "registry.example/sure-harness@sha256:" + "c" * 64
        stale = mock.Mock(returncode=0, stdout=json.dumps({
            "Config": {"Labels": {
                "org.sure.harness.runtime_id": "sure-harness-v1-py311-old",
                "org.sure.harness.lock_sha256": "b" * 64,
            }},
        }), stderr="")
        with mock.patch.dict(os.environ, {"SURE_HARNESS_RUNTIME_IMAGE": reference}),              mock.patch.object(scaffold_adapter.subprocess, "run", return_value=stale):
            with self.assertRaises(ValueError) as caught:
                scaffold_adapter.harness_runtime_build_context(harness)
        self.assertIn("active Harness Runtime", str(caught.exception))

    def test_env_supplied_runtime_image_is_accepted_when_the_labels_match(self) -> None:
        harness = self._harness_binding()
        reference = "registry.example/sure-harness@sha256:" + "c" * 64
        good = mock.Mock(returncode=0, stdout=json.dumps({
            "Config": {"Labels": {
                "org.sure.harness.runtime_id": harness["runtime_id"],
                "org.sure.harness.lock_sha256": harness["lock_sha256"],
            }},
        }), stderr="")
        with mock.patch.dict(os.environ, {"SURE_HARNESS_RUNTIME_IMAGE": reference}),              mock.patch.object(scaffold_adapter.subprocess, "run", return_value=good):
            context = scaffold_adapter.harness_runtime_build_context(harness)
        self.assertEqual(context, f"docker-image://{reference}")

    def test_scaffold_prefers_the_source_image_tag_over_the_image_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            (artifacts / "trans_input_resolved.json").write_text(
                json.dumps({
                    "model_name": "demo",
                    "task_type": "asr",
                    "framework": "pytorch",
                    "model_framework": "transformers",
                    "model_mount_target": "/models/demo",
                    "inference_entrypoint": "infer.py",
                }) + "\n",
                encoding="utf-8",
            )
            (artifacts / "source_image_result.json").write_text(
                json.dumps({"image": "demo-source:0.1.0", "image_id": "sha256:" + "b" * 64}) + "\n",
                encoding="utf-8",
            )
            subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "scaffold_adapter.py"), "--run-dir", str(run_dir)],
                check=True,
                capture_output=True,
                text=True,
                env=self._python_probe_environment(root),
            )
            dockerfile = (run_dir / "adapter" / "Dockerfile.sure").read_text(encoding="utf-8")
            config = (run_dir / "adapter" / "config.yaml").read_text(encoding="utf-8")
            model_spec = (run_dir / "adapter" / "model.spec.yaml").read_text(encoding="utf-8")
            manifest = json.loads((artifacts / "adapter_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(dockerfile.splitlines()[0], "FROM demo-source:0.1.0")
            self.assertIn('ENTRYPOINT ["/opt/venv/bin/python", "/opt/sure_trans/server.py"]', dockerfile)
            self.assertIn('command: ["/opt/venv/bin/python", "/opt/sure_trans/server.py"]', config)
            self.assertIn("framework: pytorch", model_spec)
            self.assertIn("model_framework: transformers", model_spec)
            self.assertEqual(manifest["container_python_executable"], "/opt/venv/bin/python")
            self.assertEqual(
                manifest["server_command"],
                ["/opt/venv/bin/python", "/opt/sure_trans/server.py"],
            )

    def test_source_image_python_probe_rejects_a_relative_executable(self) -> None:
        completed = mock.Mock(returncode=0, stdout="python\n", stderr="")
        with mock.patch.object(scaffold_adapter.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(ValueError, "absolute Python executable"):
                scaffold_adapter.container_python_executable("demo-source:0.1.0")

    def test_source_image_python_probe_falls_back_to_python3(self) -> None:
        missing = mock.Mock(returncode=127, stdout="", stderr="executable file not found in $PATH")
        found = mock.Mock(returncode=0, stdout="/usr/bin/python3\n", stderr="")
        with mock.patch.object(
            scaffold_adapter.subprocess, "run", side_effect=[missing, found]
        ) as run:
            self.assertEqual(
                scaffold_adapter.container_python_executable("demo-source:0.1.0"),
                "/usr/bin/python3",
            )
        entrypoints = [call.args[0][call.args[0].index("--entrypoint") + 1] for call in run.call_args_list]
        self.assertEqual(entrypoints, ["python", "python3"])

    def test_source_image_python_probe_names_docker_when_it_is_missing(self) -> None:
        with mock.patch.object(
            scaffold_adapter.subprocess, "run", side_effect=FileNotFoundError("docker")
        ):
            with self.assertRaisesRegex(ValueError, "docker"):
                scaffold_adapter.container_python_executable("demo-source:0.1.0")

    def test_final_bundle_matches_eval_deployment_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            delivery = root / "delivery"
            code = delivery / "code"
            model = delivery / "model" / "demo"
            run_dir = root / "run"
            code.mkdir(parents=True)
            model.mkdir(parents=True)
            (delivery / "Dockerfile").write_text("FROM python:3.12\nCOPY code/ /opt/code/\n", encoding="utf-8")
            (code / "infer.py").write_text("import torch\n", encoding="utf-8")
            (model / "weights.bin").write_bytes(b"weights")
            examples = delivery / "examples"
            examples.mkdir()
            (examples / "smoke.wav").write_bytes(b"RIFF-smoke")
            (examples / "smoke.expected.json").write_text(
                json.dumps({"text": "human fixture annotation"}) + "\n",
                encoding="utf-8",
            )
            subprocess.run([
                sys.executable, str(SCRIPTS_DIR / "materialize_trans_inputs.py"), "--dockerfile", str(delivery / "Dockerfile"),
                "--model", str(model), "--inference-entrypoint", str(code / "infer.py"), "--framework", "pytorch",
                "--model-framework", "custom",
                "--task-type", "asr", "--run-dir", str(run_dir), "--repo-root", str(root), "--image-version", "0.1.0",
            ], check=True, capture_output=True, text=True)
            subprocess.run([sys.executable, str(SCRIPTS_DIR / "stage_model_payload.py"), "--run-dir", str(run_dir)], check=True, capture_output=True, text=True)
            subprocess.run([sys.executable, str(SCRIPTS_DIR / "prepare_fixture.py"), "--run-dir", str(run_dir)], check=True, capture_output=True, text=True)
            artifacts = run_dir / "artifacts"
            (artifacts / "runtime_binding.json").write_text(json.dumps({
                "runtimes": {
                    "harness": {
                        "binding": {
                            "runtime_id": "sure-harness-test",
                            "lock_sha256": "e" * 64,
                            "python_version": "3.11",
                            "python_abi": "cp311",
                        }
                    }
                }
            }) + "\n", encoding="utf-8")
            digest = "sha256:" + "a" * 64
            image = "registry.example/demo:latest"
            image_ref = f"registry.example/demo@{digest}"
            values = {
                "source_image_result.json": {"schema": "sure.trans.source_image_result.v1", "status": "passed", "image": "demo-source", "image_id": "sha256:" + "b" * 64, "dockerfile": str(delivery / "Dockerfile"), "dockerfile_sha256": "c" * 64, "build_context": str(delivery), "build_command": ["docker", "build"], "build_executed": True, "build_exit_code": 0, "build_log_path": str(artifacts / "source_image_build.log"), "source_image_policy": "build"},
                "original_inference_result.json": {"status": "passed", "input": "sample.wav", "output": {"text": "ok"}, "model_loaded": True, "inference_passed": True},
                "execution_compat.json": {"schema": "sure.trans.execution_compat.v1", "status": "ready", "compat_ok": True, "selected_device": "cpu"},
                "import_result.json": {"status": "passed", "run_command": ["true"], "import_passed": True, "executed": True},
                "load_result.json": {"status": "passed", "run_command": ["true"], "load_passed": True, "executed": True},
                "infer_result.json": {"status": "passed", "input": "smoke.wav", "run_command": ["true"], "infer_passed": True, "executed": True},
                "contract_result.json": {"status": "passed", "run_command": ["true"], "contract_passed": True, "executed": True},
                "mcp_result.json": {"status": "passed", "tool_name": "transcribe_audio", "run_command": ["true"], "mcp_passed": True, "executed": True},
                "equivalence_result.json": {"status": "passed", "baseline_output": "baseline.json", "adapter_output": "adapter.json", "run_command": ["true"], "equivalent": True, "executed": True},
                "adapter_image_result.json": {"status": "passed", "source_image": "demo-source", "target_image": "demo-adapter", "image_id": "sha256:" + "d" * 64, "server_command": ["/opt/venv/bin/python", "/opt/sure_trans/server.py"], "working_dir": "/opt/sure_trans"},
                "docker_registry_result.json": {"schema": "sure.trans.docker_registry_result.v1", "status": "passed", "target_image": image, "target_image_digest": digest, "target_image_ref": image_ref, "pull_verified": True},
                "framework_detection.json": {"schema": "sure.trans.framework_detection.v2", "declared_framework": "pytorch", "declared_model_framework": "custom", "detected_framework": "pytorch", "detected_model_framework": "custom", "framework_requirement_met": True, "model_framework_matches": True, "transformers_preferred": True, "clarification_required": True, "architecture_signals": [], "architecture_clarification": "Custom PyTorch implementation; no specific architecture family proven.", "status": "ready", "evidence": ["torch import"]},
                "inference_dependency_report.json": {"entrypoint": str(code / "infer.py"), "build_context": str(delivery), "docker_copy_sources": ["code/"], "python_imports": ["torch"], "support_paths": [str(code)], "unresolved": [], "external_paths": [], "status": "ready"},
            }
            for name, value in values.items():
                (artifacts / name).write_text(json.dumps(value) + "\n", encoding="utf-8")
            (artifacts / "source_image_build.log").write_text("fake docker build\n", encoding="utf-8")
            subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "scaffold_adapter.py"), "--run-dir", str(run_dir)],
                check=True,
                capture_output=True,
                text=True,
                env=self._python_probe_environment(root),
            )
            (run_dir / "adapter" / "model.py").write_text(
                "class ModelWrapper:\n    def __init__(self, config=None): self.model = object()\n    def load(self): return None\n    def predict(self, input_data): return {'text': 'ok'}\n    def healthcheck(self): return {'status': 'ready', 'model_loaded': True}\n",
                encoding="utf-8",
            )
            adapter_validation = artifacts / "adapter_validation"
            adapter_validation.mkdir()
            (adapter_validation / "sample_output.json").write_text(
                json.dumps({"text": "ok"}) + "\n",
                encoding="utf-8",
            )
            relative_python = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "write_runtime_inventory.py"),
                    "--run-dir",
                    str(run_dir),
                    "--python-executable",
                    "python",
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(relative_python.returncode, 0)
            self.assertIn("must be absolute", relative_python.stdout + relative_python.stderr)
            subprocess.run([sys.executable, str(SCRIPTS_DIR / "write_runtime_inventory.py"), "--run-dir", str(run_dir)], check=True, capture_output=True, text=True)
            inventory = json.loads((artifacts / "runtime_inventory.json").read_text(encoding="utf-8"))
            self.assertTrue(inventory["harness_runtime"]["required"])
            self.assertEqual(
                inventory["harness_runtime"]["python_executable"],
                "/opt/sure-harness/sure-harness-test/bin/python",
            )
            self.assertEqual(inventory["model_runtime"]["python_executable"], "/opt/venv/bin/python")
            self.assertEqual(inventory["container_runtime"]["python_executable"], "/opt/venv/bin/python")
            self.assertEqual(
                inventory["container_runtime"]["server_command"],
                ["/opt/venv/bin/python", "/opt/sure_trans/server.py"],
            )
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "check_artifact.py"),
                    "--run-dir",
                    str(run_dir),
                    "--produces",
                    str(artifacts / "runtime_inventory.json"),
                    "--kind",
                    "runtime_inventory",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run([sys.executable, str(SCRIPTS_DIR / "write_verdict.py"), "--run-dir", str(run_dir)], check=True, capture_output=True, text=True)
            verdict = json.loads((artifacts / "verdict.json").read_text(encoding="utf-8"))
            self.assertEqual(verdict["schema"], "sure.trans.verdict.v2")
            self.assertEqual(verdict["framework"]["computation"]["detected"], "pytorch")
            self.assertEqual(verdict["framework"]["model"]["declared"], "custom")
            self.assertTrue(verdict["framework"]["architecture_clarification"])
            subprocess.run([sys.executable, str(SCRIPTS_DIR / "finalize_trans_bundle.py"), "--run-dir", str(run_dir)], check=True, capture_output=True, text=True)
            subprocess.run([sys.executable, str(SCRIPTS_DIR / "finalize_trans_bundle.py"), "--run-dir", str(run_dir)], check=True, capture_output=True, text=True)
            model_dir = root / "sure" / "models" / "demo"
            check = subprocess.run(
                [sys.executable, "-c", "from deployment_binding import load_deployment_binding; from pathlib import Path; print(load_deployment_binding(Path(__import__('sys').argv[1]), 'demo')['target_image_ref'])", str(model_dir)],
                cwd=Path(__file__).resolve().parents[2] / "sure_eval" / "scripts",
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(check.stdout.strip(), image_ref)
            fixture_manifest = json.loads(
                (model_dir / "artifacts" / "fixture_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(Path(fixture_manifest["model_dir"]), model_dir)
            self.assertEqual(Path(fixture_manifest["staged_dir"]), model_dir / "fixture" / "asr")
            self.assertEqual(Path(fixture_manifest["gt_jsonl"]), model_dir / "fixture" / "asr" / "gt.jsonl")
            self.assertEqual(fixture_manifest["samples"][0]["annotation_fields"], ["text"])
            gt = json.loads((model_dir / "fixture" / "asr" / "gt.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(gt["text"], "human fixture annotation")
            self.assertEqual(fixture_manifest["annotation_source"]["type"], "fixture_expected_sidecar")
            self.assertFalse(fixture_manifest["annotation_source"]["fallback"])
            self.assertTrue((model_dir / "fixture" / "asr" / "smoke.expected.json").is_file())
            self.assertEqual(
                json.loads((model_dir / "artifacts" / "sample_output.json").read_text(encoding="utf-8")),
                {"text": "ok"},
            )
            manifest = json.loads(
                (model_dir / "artifacts" / "artifact_manifest.json").read_text(encoding="utf-8")
            )
            required_paths = {entry["path"] for entry in manifest["artifacts"]["required"].values()}
            self.assertIn("artifacts/sample_output.json", required_paths)
            self.assertIn("artifacts/fixture_manifest.json", required_paths)
            self.assertIn("model.py", required_paths)
            deployment = json.loads(
                (model_dir / "artifacts" / "deployment_ready.json").read_text(encoding="utf-8")
            )
            self.assertEqual(deployment["integrity_profile"], "manifest-complete-v1")
            self.assertEqual(deployment["weights_integrity"], "bundled")
            self.assertIn("artifacts/sample_output.json", deployment["required_artifact_sha256"])
            self.assertIn("model.py", deployment["required_artifact_sha256"])
            self.assertIn("weights.bin", deployment["required_artifact_sha256"])
            self.assertIn("fixture/asr/smoke.wav", deployment["required_artifact_sha256"])
            self.assertIn("fixture/asr/gt.jsonl", deployment["required_artifact_sha256"])
            package = json.loads((model_dir / "artifacts" / "package_gate.json").read_text(encoding="utf-8"))
            inventory = json.loads((model_dir / "artifacts" / "runtime_inventory.json").read_text(encoding="utf-8"))
            final_verdict = json.loads((model_dir / "artifacts" / "verdict.json").read_text(encoding="utf-8"))
            finalized_manifest = json.loads(
                (model_dir / "artifacts" / "artifact_manifest.json").read_text(encoding="utf-8")
            )
            self.assertLess(finalized_manifest["generated_at"], package["generated_at"])
            self.assertLess(package["generated_at"], inventory["generated_at"])
            self.assertLess(inventory["generated_at"], final_verdict["generated_at"])
            self.assertLess(final_verdict["generated_at"], deployment["generated_at"])
            finalized_check = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR.parents[1] / "sure_onboard" / "scripts" / "check_finalized_bundle.py"),
                    "--run-dir",
                    str(run_dir),
                    "--produces",
                    str(artifacts / "deployment_ready.json"),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                finalized_check.returncode,
                0,
                finalized_check.stdout + finalized_check.stderr,
            )
            fixture_check = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR.parents[1] / "sure_onboard" / "scripts" / "check_fixture.py"),
                    "--run-dir",
                    str(run_dir),
                    "--produces",
                    str(model_dir / "artifacts" / "fixture_manifest.json"),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(fixture_check.returncode, 0, fixture_check.stdout + fixture_check.stderr)


    def test_blocked_finalize_writes_an_honest_terminal_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            (artifacts / "trans_input_resolved.json").write_text(
                json.dumps({"model_name": "demo"}) + chr(10), encoding="utf-8"
            )
            (artifacts / "execution_compat.json").write_text(
                json.dumps({"status": "blocked", "compat_ok": False}) + chr(10), encoding="utf-8"
            )
            subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "finalize_trans_bundle.py"), "--run-dir", str(run_dir),
                 "--blocked", "source image push was rejected"],
                check=True, capture_output=True, text=True,
            )
            marker = json.loads((artifacts / "deployment_ready.json").read_text(encoding="utf-8"))
            self.assertEqual(marker["status"], "blocked")
            self.assertIn("source image push was rejected", marker["blocked_reason"])
            self.assertFalse(marker["execution_policy"]["container_only"])
            self.assertFalse((run_dir / "adapter").exists())

    def test_deployment_gate_accepts_a_blocked_marker_that_proves_it_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            (artifacts / "trans_input_resolved.json").write_text(
                json.dumps({"model_name": "demo"}) + chr(10), encoding="utf-8"
            )
            subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "finalize_trans_bundle.py"), "--run-dir", str(run_dir),
                 "--blocked", "source image push was rejected"],
                check=True, capture_output=True, text=True,
            )
            marker = artifacts / "deployment_ready.json"
            accepted = subprocess.run([
                sys.executable, str(SCRIPTS_DIR / "check_artifact.py"), "--run-dir", str(run_dir),
                "--produces", str(marker), "--kind", "deployment_ready",
            ], capture_output=True, text=True)
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)

            value = json.loads(marker.read_text(encoding="utf-8"))
            value["execution_policy"]["container_only"] = True
            lying = artifacts / "deployment_ready_lying.json"
            lying.write_text(json.dumps(value) + chr(10), encoding="utf-8")
            rejected = subprocess.run([
                sys.executable, str(SCRIPTS_DIR / "check_artifact.py"), "--run-dir", str(run_dir),
                "--produces", str(lying), "--kind", "deployment_ready",
            ], capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("container-only", rejected.stdout + rejected.stderr)

    def test_named_references_outrank_bare_image_ids(self) -> None:
        digest = "sha256:" + "a" * 64
        self.assertEqual(
            run_docker_build.prefer_named_references([digest, "demo:0.1.0"]),
            ["demo:0.1.0", digest],
        )
        self.assertEqual(
            run_docker_build.prefer_named_references(["demo:0.1.0", digest]),
            ["demo:0.1.0", digest],
        )

    def test_contract_stage_names_the_directory_infer_wrote_the_sample_into(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter = root / "adapter"
            adapter.mkdir()
            (adapter / "validate.py").write_text(
                (SCRIPTS_DIR / "templates" / "validate.py").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            artifacts = root / "contract_validation"
            artifacts.mkdir()
            env = dict(os.environ, SURE_VALIDATE_ARTIFACTS_DIR=str(artifacts))
            result = subprocess.run(
                [sys.executable, str(adapter / "validate.py"), "--stage", "contract"],
                capture_output=True, text=True, env=env,
            )
            self.assertNotEqual(result.returncode, 0)
            written = json.loads((artifacts / "contract_result.json").read_text(encoding="utf-8"))
            message = written["error"]
            self.assertIn("sample_output.json", message)
            self.assertIn("infer", message)
            self.assertIn("SURE_VALIDATE_ARTIFACTS_DIR", message)

    def test_adapter_gate_requires_dockerfile_to_bake_all_declared_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            artifacts = run_dir / "artifacts"
            adapter = run_dir / "adapter"
            adapter.mkdir(parents=True)
            artifacts.mkdir()
            for name in ("model.py", "__init__.py", "server.py", "config.yaml", "model.spec.yaml", "validate.py", "mcp_smoke.py"):
                content = "ok\n"
                if name == "config.yaml":
                    content = "server:\n  command: [\"/opt/venv/bin/python\", \"/opt/sure_trans/server.py\"]\n"
                (adapter / name).write_text(
                    "class ModelWrapper:\n    pass\n" if name == "model.py" else content,
                    encoding="utf-8",
                )
            dockerfile = adapter / "Dockerfile.sure"
            dockerfile.write_text(
                "FROM demo-source\n"
                "COPY --from=sure_harness_runtime / /opt/sure-harness/sure-harness-test/\n"
                "COPY model.py server.py config.yaml model.spec.yaml __init__.py mcp_smoke.py /opt/sure_trans/\n"
                "ENTRYPOINT [\"/opt/venv/bin/python\", \"/opt/sure_trans/server.py\"]\n",
                encoding="utf-8",
            )
            (artifacts / "source_image_result.json").write_text(
                json.dumps({"image": "demo-source", "image_id": "sha256:" + "b" * 64}) + "\n",
                encoding="utf-8",
            )
            manifest = {
                "status": "ready",
                "model_py": str(adapter / "model.py"),
                "init_py": str(adapter / "__init__.py"),
                "validate_py": str(adapter / "validate.py"),
                "server_py": str(adapter / "server.py"),
                "config_yaml": str(adapter / "config.yaml"),
                "model_spec": str(adapter / "model.spec.yaml"),
                "dockerfile": str(dockerfile),
                "mcp_smoke_py": str(adapter / "mcp_smoke.py"),
                "harness_runtime_embedded": True,
                "harness_runtime": {
                    "runtime_id": "sure-harness-test",
                    "lock_sha256": "e" * 64,
                    "python_executable": "/opt/sure-harness/sure-harness-test/bin/python",
                    "manifest_path": "/opt/sure-harness/sure-harness-test/runtime-manifest.json",
                    "runtime_root": "/opt/sure-harness/sure-harness-test",
                },
                "container_python_executable": "/opt/venv/bin/python",
                "server_command": ["/opt/venv/bin/python", "/opt/sure_trans/server.py"],
                "working_dir": "/opt/sure_trans",
                "source_image_reference": "demo-source",
                "source_image_id": "sha256:" + "b" * 64,
            }
            produces = artifacts / "adapter_manifest.json"
            produces.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
            blocked = subprocess.run([
                sys.executable, str(SCRIPTS_DIR / "check_artifact.py"), "--run-dir", str(run_dir),
                "--produces", str(produces), "--kind", "adapter",
            ], capture_output=True, text=True)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("validate.py", blocked.stdout + blocked.stderr)

            dockerfile.write_text(
                "FROM demo-source\n"
                "COPY --from=sure_harness_runtime / /opt/sure-harness/sure-harness-test/\n"
                "COPY model.py server.py config.yaml model.spec.yaml __init__.py validate.py mcp_smoke.py /opt/sure_trans/\n"
                "ENTRYPOINT [\"/opt/venv/bin/python\", \"/opt/sure_trans/server.py\"]\n",
                encoding="utf-8",
            )
            subprocess.run([
                sys.executable, str(SCRIPTS_DIR / "check_artifact.py"), "--run-dir", str(run_dir),
                "--produces", str(produces), "--kind", "adapter",
            ], check=True, capture_output=True, text=True)


    def test_server_template_keeps_stdout_pure_json_rpc(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "model.py").write_text(
                "class ModelWrapper:\n"
                "    def __init__(self, config=None):\n"
                "        self.model = None\n"
                "    def load(self):\n"
                "        print('junk progress line from model load', flush=True)\n"
                "        self.model = object()\n"
                "    def predict(self, input_data):\n"
                "        print('junk predict line', flush=True)\n"
                "        return {'text': 'ok', 'language': 'Chinese'}\n"
                "    def healthcheck(self):\n"
                "        return {'status': 'ready'}\n",
                encoding="utf-8",
            )
            template = (SCRIPTS_DIR / "templates" / "server.py").read_text(encoding="utf-8")
            template = template.replace('"__TOOL_NAME__"', '"transcribe_audio"')
            template = template.replace(
                "__INPUT_SCHEMA__",
                '{"type":"object","properties":{"audio_path":{"type":"string"}},"required":["audio_path"]}',
            )
            (root / "server.py").write_text(template, encoding="utf-8")
            payload = "".join(
                json.dumps(request) + "\n"
                for request in (
                    {"jsonrpc": "2.0", "id": "1", "method": "initialize", "params": {}},
                    {"jsonrpc": "2.0", "id": "2", "method": "tools/list", "params": {}},
                    {
                        "jsonrpc": "2.0",
                        "id": "3",
                        "method": "tools/call",
                        "params": {"name": "transcribe_audio", "arguments": {"audio_path": "sample.wav"}},
                    },
                    {"jsonrpc": "2.0", "id": "4", "method": "shutdown", "params": {}},
                )
            )
            completed = subprocess.run(
                [sys.executable, str(root / "server.py")],
                input=payload,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=root,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            lines = [line for line in completed.stdout.splitlines() if line.strip()]
            self.assertEqual([json.loads(line).get("id") for line in lines], ["1", "2", "3", "4"])
            for line in lines:
                self.assertIn("result", json.loads(line))
            self.assertIn("junk progress line", completed.stderr)
            self.assertIn("junk predict line", completed.stderr)

    def test_mcp_smoke_skips_non_json_stdout_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "polluted_server.py").write_text(
                "import json, sys\n"
                "for request in sys.stdin:\n"
                "    if not request.strip(): continue\n"
                "    print('junk log line', flush=True)\n"
                "    req = json.loads(request)\n"
                "    method = req['method']\n"
                "    if method == 'initialize':\n"
                "        payload = {'jsonrpc': '2.0', 'id': req['id'], 'result': {'protocolVersion': '2024-11-05'}}\n"
                "    elif method == 'tools/list':\n"
                "        payload = {'jsonrpc': '2.0', 'id': req['id'], 'result': {'tools': [{'name': 'transcribe_audio'}]}}\n"
                "    elif method == 'tools/call':\n"
                "        payload = {'jsonrpc': '2.0', 'id': req['id'], 'result': {'content': [{'type': 'text', 'text': '{\"text\": \"ok\"}'}]}}\n"
                "    else:\n"
                "        payload = {'jsonrpc': '2.0', 'id': req['id'], 'result': {}}\n"
                "    print(json.dumps(payload), flush=True)\n",
                encoding="utf-8",
            )
            produces = root / "mcp_smoke.json"
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "mcp_smoke.py"),
                    "--audio",
                    str(root / "sample.wav"),
                    "--tool",
                    "transcribe_audio",
                    "--server-command",
                    sys.executable,
                    str(root / "polluted_server.py"),
                    "--produces",
                    str(produces),
                    "--timeout",
                    "60",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            evidence = json.loads(produces.read_text(encoding="utf-8"))
            self.assertEqual(evidence["status"], "passed")
            self.assertGreaterEqual(evidence["stdout_junk_count"], 4)
            self.assertTrue(evidence["tools_call"]["ok"])


if __name__ == "__main__":
    unittest.main()
