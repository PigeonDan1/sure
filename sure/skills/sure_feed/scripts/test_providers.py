#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sure_feed.providers.huggingface as hf_module  # noqa: E402
from sure_feed.fixture_registry import select_fixture_for_task  # noqa: E402
from sure_feed.providers.base import ProviderNetworkError, ProviderRequest, infer_task, synthesize_model_input, to_yaml  # noqa: E402
from sure_feed.providers.huggingface import HuggingFaceProvider  # noqa: E402
from sure_feed_online_discover import parse_model_url  # noqa: E402


class ProviderTests(unittest.TestCase):
    def sample_readme(self) -> str:
        return """---
library_name: sample_tts
---
```bash
conda create -n sample_tts python=3.10 -y
python -m pip install "git+https://github.com/acme-lab/sample-tts.git"
```
```python
from sample_tts.runtime import SampleTtsRuntime
import soundfile as sf

runtime = SampleTtsRuntime.from_pretrained(
    "acme-lab/sample-tts-base",
    precision="bfloat16",
)

result = runtime.generate(
    text="Hello, this is a quick speech synthesis test.",
    prompt_audio_path="/path/to/reference.wav",
    prompt_text="The exact transcript of the reference audio.",
)

sf.write("output.wav", result["audio"].float().cpu().numpy(), result["sample_rate"])
```
"""

    def test_huggingface_auto_falls_back_to_mirror_and_keeps_canonical_repo(self) -> None:
        original = hf_module.http_get_json
        original_text = hf_module.http_get_text
        calls: list[str] = []

        def fake_get_json(url: str, headers=None, timeout: int = 20):
            calls.append(url)
            if url.startswith(hf_module.PRIMARY_ENDPOINT):
                raise ProviderNetworkError("primary endpoint down")
            return [
                {
                    "modelId": "owner/asr-model",
                    "pipeline_tag": "automatic-speech-recognition",
                    "tags": ["license:apache-2.0"],
                    "downloads": 7,
                }
            ]

        def fake_get_text(url: str, headers=None, timeout: int = 20, max_bytes: int = 100_000):
            if url.startswith(hf_module.PRIMARY_ENDPOINT):
                return ""
            return self.sample_readme()

        hf_module.http_get_json = fake_get_json
        hf_module.http_get_text = fake_get_text
        try:
            provider = HuggingFaceProvider(endpoint_mode="auto")
            candidates = provider.search(ProviderRequest(source="huggingface", query="asr", task="asr", max_models=1))
        finally:
            hf_module.http_get_json = original
            hf_module.http_get_text = original_text

        self.assertEqual(len(candidates), 1)
        self.assertEqual(provider.last_endpoint_used, hf_module.MIRROR_ENDPOINT)
        self.assertIn("primary endpoint down", provider.fallback_reason or "")
        self.assertEqual(candidates[0]["repo"], "https://huggingface.co/owner/asr-model")
        self.assertEqual(candidates[0]["endpoint_used"], hf_module.MIRROR_ENDPOINT)

    def test_huggingface_direct_normalizes_tts_url(self) -> None:
        original = hf_module.http_get_json
        original_text = hf_module.http_get_text

        def fake_get_json(url: str, headers=None, timeout: int = 20):
            if url.startswith(hf_module.PRIMARY_ENDPOINT):
                raise ProviderNetworkError("primary endpoint down")
            return {
                "modelId": "acme-lab/sample-tts-base",
                "pipeline_tag": "text-to-speech",
                "library_name": "sample_tts",
                "sha": "abc123",
                "tags": ["text-to-speech", "tts", "license:apache-2.0"],
                "downloads": 710,
            }

        def fake_get_text(url: str, headers=None, timeout: int = 20, max_bytes: int = 100_000):
            if url.startswith(hf_module.PRIMARY_ENDPOINT):
                return ""
            return self.sample_readme()

        hf_module.http_get_json = fake_get_json
        hf_module.http_get_text = fake_get_text
        try:
            candidate = HuggingFaceProvider(endpoint_mode="auto").direct("acme-lab/sample-tts-base")
        finally:
            hf_module.http_get_json = original
            hf_module.http_get_text = original_text

        matched, task_type, score, evidence, match_source = infer_task(candidate, "auto")
        self.assertTrue(matched)
        self.assertEqual(task_type, "tts")
        self.assertEqual(match_source, "pipeline_tag")
        self.assertGreaterEqual(score, 0.9)
        self.assertEqual(candidate["repo"], "https://huggingface.co/acme-lab/sample-tts-base")
        self.assertIn("SampleTtsRuntime", candidate["model_card_text"])

    def test_synthesizes_entrypoints_from_retrieved_model_card(self) -> None:
        model_input, weak_fields, evidence = synthesize_model_input(
            {
                "source": "huggingface",
                "model_id": "acme-lab/sample-tts-base",
                "repo": "https://huggingface.co/acme-lab/sample-tts-base",
                "commit": "abc123",
                "weights_source": "huggingface",
                "library_name": "sample_tts",
                "model_card_url": "https://huggingface.co/acme-lab/sample-tts-base/blob/main/README.md",
                "model_card_text": self.sample_readme(),
            },
            "tts",
        )
        self.assertEqual(weak_fields, [])
        self.assertEqual(model_input["repo"]["commit"], "abc123")
        self.assertIn("from sample_tts.runtime import SampleTtsRuntime", model_input["entrypoints"]["import_test"])
        self.assertIn("SampleTtsRuntime.from_pretrained", model_input["entrypoints"]["load_test"])
        self.assertIn("runtime.generate", model_input["entrypoints"]["infer_test"])
        self.assertEqual(model_input["environment_hint"]["preferred_backend"], "conda")
        self.assertEqual(model_input["environment_hint"]["python_version"], "3.10")
        self.assertEqual(model_input["fixture"]["fixture_source"], "task_registry")
        self.assertEqual(model_input["fixture"]["fixture_index"], "fixtures/tasks/tts/README.md")
        self.assertTrue(model_input["fixture"]["reference_audio"].startswith("fixtures/tasks/tts/"))
        covered = {item.get("model_input_field") for item in evidence}
        self.assertIn("entrypoints.import_test", covered)
        self.assertIn("io_contract", covered)
        self.assertIn("fixture", covered)

    def test_policy_defaults_python_version_when_model_card_omits_it(self) -> None:
        readme = """---
library_name: voxcpm
---
GPU inference is recommended.

```bash
pip install voxcpm
```
```python
from voxcpm import VoxCPM

model = VoxCPM.from_pretrained("openbmb/VoxCPM2", load_denoiser=False)
wav = model.generate(
    text="VoxCPM2 brings multilingual support.",
    cfg_value=2.0,
    inference_timesteps=10,
)
```
"""
        model_input, weak_fields, evidence = synthesize_model_input(
            {
                "source": "huggingface",
                "model_id": "openbmb/VoxCPM2",
                "repo": "https://huggingface.co/openbmb/VoxCPM2",
                "commit": "abc123",
                "weights_source": "huggingface",
                "library_name": "voxcpm",
                "model_card_url": "https://huggingface.co/openbmb/VoxCPM2/blob/main/README.md",
                "model_card_text": readme,
            },
            "tts",
        )
        self.assertEqual(weak_fields, [])
        self.assertEqual(model_input["environment_hint"]["python_version"], "3.10")
        self.assertEqual(model_input["environment_hint"]["python_version_source"], "sure_policy_default")
        self.assertIn("from voxcpm import VoxCPM", model_input["entrypoints"]["import_test"])
        self.assertIn("VoxCPM.from_pretrained", model_input["entrypoints"]["load_test"])
        self.assertIn("model.generate", model_input["entrypoints"]["infer_test"])
        self.assertTrue(
            any(
                item.get("source") == "local"
                and item.get("field") == "sure_policy.python_version_default"
                and item.get("model_input_field") == "environment_hint.python_version"
                for item in evidence
            )
        )

    def test_runtime_strategy_handles_sherpa_onnx_without_standard_import_load_split(self) -> None:
        readme = """# Streaming ASR ONNX

This model provides streaming and offline CPU ASR checkpoints for sherpa-onnx.

```bash
python -m venv .venv
pip install sherpa-onnx
```
"""
        model_input, weak_fields, evidence = synthesize_model_input(
            {
                "source": "huggingface",
                "model_id": "example-org/streaming-asr-onnx",
                "repo": "https://huggingface.co/example-org/streaming-asr-onnx",
                "commit": "abc123",
                "weights_source": "huggingface",
                "pipeline_tag": "automatic-speech-recognition",
                "tags": [
                    "sherpa-onnx",
                    "onnx",
                    "zipformer-transducer",
                    "automatic-speech-recognition",
                ],
                "model_card_url": "https://huggingface.co/example-org/streaming-asr-onnx/blob/main/README.md",
                "model_card_text": readme,
            },
            "asr",
        )
        self.assertEqual(weak_fields, [])
        self.assertEqual(model_input["runtime_strategy"]["framework"], "sherpa-onnx")
        self.assertTrue(model_input["entrypoints"]["import_test"].startswith("policy_resolved:"))
        self.assertTrue(model_input["entrypoints"]["load_test"].startswith("policy_resolved:"))
        self.assertTrue(model_input["entrypoints"]["infer_test"].startswith("policy_resolved:"))
        self.assertNotIn("python -m venv", model_input["entrypoints"]["infer_test"])
        covered = {item.get("model_input_field") for item in evidence}
        self.assertIn("runtime_strategy", covered)
        self.assertIn("entrypoints.import_test", covered)
        self.assertIn("entrypoints.load_test", covered)
        self.assertIn("entrypoints.infer_test", covered)

    def test_fixture_registry_selects_task_specific_asr_fixture(self) -> None:
        fixture, io_contract, issues, evidence = select_fixture_for_task(
            "asr",
            {"model_id": "owner/asr", "description": "English automatic speech recognition"},
        )
        self.assertEqual(issues, [])
        self.assertIsNotNone(fixture)
        assert fixture is not None
        self.assertEqual(fixture["fixture_source"], "task_registry")
        self.assertEqual(fixture["fixture_index"], "fixtures/tasks/asr/README.md")
        self.assertIn("fixtures/tasks/asr/qwen3_asr_smoke/asr_en/", fixture["audio"])
        self.assertEqual(io_contract["primary_field"], "text")
        self.assertIn("fixture", {item.get("model_input_field") for item in evidence})

    def test_fixture_registry_routes_speech_understanding_to_atomic_fixtures(self) -> None:
        fixture, io_contract, issues, _evidence = select_fixture_for_task(
            "speech_understanding",
            {"model_id": "owner/transcribe-diarize", "description": "Transcription with speaker diarization"},
        )
        self.assertEqual(issues, [])
        self.assertIsNotNone(fixture)
        assert fixture is not None
        self.assertEqual(fixture["fixture_source"], "task_registry")
        self.assertEqual(fixture["fixture_index"], "fixtures/tasks/speech_understanding/README.md")
        self.assertEqual(fixture["selected_subtasks"], ["asr", "sd"])
        self.assertEqual(io_contract["primary_field"], "text")

    def test_fixture_registry_selects_sd_and_sa_asr_jsonl_fixtures(self) -> None:
        sd_fixture, sd_contract, sd_issues, _sd_evidence = select_fixture_for_task("sd", {"description": "speaker diarization"})
        self.assertEqual(sd_issues, [])
        self.assertIsNotNone(sd_fixture)
        assert sd_fixture is not None
        self.assertEqual(sd_fixture["gt"], "fixtures/tasks/sd/librispeech_2spk_smoke/gt.jsonl")
        self.assertEqual(sd_fixture["audio"], "fixtures/tasks/sd/librispeech_2spk_smoke/librispeech_2spk_001.wav")
        self.assertEqual(sd_fixture["samples"][0]["speakers"], ["spk1", "spk2"])
        self.assertEqual(sd_contract["primary_field"], "segments")

        sa_fixture, sa_contract, sa_issues, _sa_evidence = select_fixture_for_task(
            "sa_asr",
            {"description": "speaker attributed ASR"},
        )
        self.assertEqual(sa_issues, [])
        self.assertIsNotNone(sa_fixture)
        assert sa_fixture is not None
        self.assertEqual(sa_fixture["gt"], "fixtures/tasks/sa_asr/librispeech_2spk_smoke/gt.jsonl")
        self.assertEqual(sa_fixture["audio"], "fixtures/tasks/sa_asr/librispeech_2spk_smoke/librispeech_2spk_001.wav")
        self.assertEqual(sa_fixture["samples"][0]["speakers"], ["spk1", "spk2"])
        self.assertIn("text", sa_fixture["samples"][0]["segments"][0])
        self.assertEqual(sa_contract["primary_field"], "segments")

    def test_parse_direct_urls(self) -> None:
        self.assertEqual(
            parse_model_url("https://huggingface.co/acme-lab/sample-tts-base")["model_id"],
            "acme-lab/sample-tts-base",
        )
        self.assertEqual(
            parse_model_url("https://hf-mirror.com/acme-lab/sample-tts-base")["canonical_url"],
            "https://huggingface.co/acme-lab/sample-tts-base",
        )
        self.assertEqual(
            parse_model_url("https://modelscope.cn/models/owner/name")["source"],
            "modelscope",
        )
        self.assertEqual(parse_model_url("https://github.com/owner/repo")["model_id"], "owner/repo")

    def test_hyphenated_github_topic_matches_asr(self) -> None:
        matched, task_type, score, evidence, match_source = infer_task(
            {
                "source": "github",
                "model_id": "owner/repo",
                "repo": "https://github.com/owner/repo",
                "tags": ["speech-recognition"],
            },
            "asr",
        )
        self.assertTrue(matched)
        self.assertEqual(task_type, "asr")
        self.assertGreater(score, 0)
        self.assertEqual(match_source, "tags")
        self.assertEqual(evidence[0]["value"], "speech-recognition")

    def test_broad_audio_text_pipeline_is_narrowed_to_sa_asr_by_research_evidence(self) -> None:
        matched, task_type, score, evidence, match_source = infer_task(
            {
                "source": "huggingface",
                "model_id": "OpenMOSS-Team/MOSS-Transcribe-Diarize",
                "repo": "https://huggingface.co/OpenMOSS-Team/MOSS-Transcribe-Diarize",
                "pipeline_tag": "audio-text-to-text",
                "tasks": ["audio-text-to-text"],
                "tags": ["asr", "diarization", "timestamp-asr", "audio-text-to-text"],
                "model_card_text": "MOSS Transcribe Diarize performs transcription with speaker diarization.",
            },
            "auto",
        )
        self.assertTrue(matched)
        self.assertEqual(task_type, "sa_asr")
        self.assertEqual(match_source, "research_narrowing")
        self.assertGreaterEqual(score, 0.95)
        self.assertIn("task_narrowing.final_task", {item.get("field") for item in evidence})

    def test_github_never_defaults_weights_source_to_github(self) -> None:
        model_input, weak_fields, _evidence = synthesize_model_input(
            {
                "source": "github",
                "model_id": "owner/repo",
                "repo": "https://github.com/owner/repo",
            },
            "asr",
        )
        self.assertEqual(model_input["weights"]["source"], "release_or_pypi")
        self.assertIn("missing:weights.source", weak_fields)

    def test_yaml_emits_empty_lists_as_empty_sequences(self) -> None:
        self.assertIn("system_packages: []", to_yaml({"system_packages": []}))

    def test_yaml_quotes_multiline_strings(self) -> None:
        rendered = to_yaml({"infer_test": "python server.py \\\n--host 0.0.0.0"})
        self.assertIn("\\n", rendered)
        self.assertNotIn("\n--host", rendered)

    def test_yaml_quotes_numeric_strings(self) -> None:
        self.assertIn('python_version: "3.10"', to_yaml({"python_version": "3.10"}))


if __name__ == "__main__":
    unittest.main()
