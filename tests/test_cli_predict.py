from __future__ import annotations

import json
import sys
import types
from pathlib import Path

from typer.testing import CliRunner


def _install_structlog_stub() -> None:
    if "structlog" in sys.modules:
        return

    class _Logger:
        def bind(self, **_: object) -> "_Logger":
            return self

        def __getattr__(self, _: str):
            return lambda *args, **kwargs: None

    class _Callable:
        def __call__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

    structlog_stub = types.ModuleType("structlog")
    structlog_stub.configure = lambda *args, **kwargs: None
    structlog_stub.get_logger = lambda *args, **kwargs: _Logger()
    structlog_stub.stdlib = types.SimpleNamespace(
        filter_by_level=_Callable(),
        add_logger_name=_Callable(),
        add_log_level=_Callable(),
        PositionalArgumentsFormatter=lambda *args, **kwargs: _Callable(),
        LoggerFactory=lambda *args, **kwargs: _Callable(),
        BoundLogger=_Logger,
    )
    structlog_stub.processors = types.SimpleNamespace(
        TimeStamper=lambda *args, **kwargs: _Callable(),
        StackInfoRenderer=lambda *args, **kwargs: _Callable(),
        format_exc_info=_Callable(),
        UnicodeDecoder=lambda *args, **kwargs: _Callable(),
        JSONRenderer=lambda *args, **kwargs: _Callable(),
    )
    structlog_stub.dev = types.SimpleNamespace(
        ConsoleRenderer=lambda *args, **kwargs: _Callable(),
    )
    sys.modules["structlog"] = structlog_stub


_install_structlog_stub()

from sure_eval.cli import app  # noqa: E402
from sure_eval.inference.adapters import create_predict_adapter  # noqa: E402
from sure_eval.inference.language import canonicalize_language, map_language_for_model  # noqa: E402
from sure_eval.inference.runner import get_runtime_readiness, _resolve_runtime_command  # noqa: E402
from sure_eval.inference.errors import AdapterError  # noqa: E402
from sure_eval.models.registry import ModelRegistry  # noqa: E402


def _missing_runtime_registry(tmp_path: Path) -> ModelRegistry:
    model_dir = tmp_path / "synthetic_missing_runtime_asr"
    model_dir.mkdir()
    (model_dir / "config.yaml").write_text(
        "\n".join(
            [
                "name: synthetic_missing_runtime_asr",
                "task: ASR",
                "server:",
                "  command: ['.venv/bin/python', 'server.py']",
                "  working_dir: '.'",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (model_dir / "model.py").write_text("class SyntheticASRModel:\n    pass\n", encoding="utf-8")
    (model_dir / "server.py").write_text("print('synthetic server')\n", encoding="utf-8")
    return ModelRegistry(models_dir=tmp_path)


def test_predict_dry_run_passes_for_asr_qwen3(tmp_path: Path) -> None:
    runner = CliRunner()
    output_path = tmp_path / "predictions.jsonl"

    result = runner.invoke(
        app,
        [
            "predict",
            "asr_qwen3",
            "--input",
            "tests/fixtures/cli/asr_input.jsonl",
            "--output",
            str(output_path),
            "--task",
            "asr",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "Predict Dry Run" in result.stdout
    assert "runtime_command" in result.stdout
    assert "runtime_executable" in result.stdout
    assert "working_dir" in result.stdout
    assert not output_path.exists()


def test_predict_dry_run_reports_runtime_not_found_for_synthetic_model(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    output_path = tmp_path / "synthetic_predictions.jsonl"
    registry = _missing_runtime_registry(tmp_path)
    monkeypatch.setattr("sure_eval.cli.get_model_registry", lambda: registry)

    result = runner.invoke(
        app,
        [
            "predict",
            "synthetic_missing_runtime_asr",
            "--input",
            "tests/fixtures/cli/asr_input.jsonl",
            "--output",
            str(output_path),
            "--task",
            "asr",
            "--dry-run",
        ],
    )

    assert result.exit_code == 1
    assert "runtime_executable_missing" in result.stdout.lower()
    assert "setup.sh" in result.stdout


def test_predict_dry_run_non_asr_model_reports_task_mismatch(tmp_path: Path) -> None:
    runner = CliRunner()
    output_path = tmp_path / "librosa_predictions.jsonl"

    result = runner.invoke(
        app,
        [
            "predict",
            "librosa",
            "--input",
            "tests/fixtures/cli/asr_input.jsonl",
            "--output",
            str(output_path),
            "--task",
            "asr",
            "--dry-run",
        ],
    )

    assert result.exit_code == 1
    assert "task" in result.stdout.lower()
    assert "librosa" in result.stdout


def test_doctor_reports_runtime_missing_for_synthetic_model(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    registry = _missing_runtime_registry(tmp_path)
    monkeypatch.setattr("sure_eval.cli.get_model_registry", lambda: registry)

    result = runner.invoke(app, ["doctor", "synthetic_missing_runtime_asr"])

    assert result.exit_code == 1
    assert "Runtime Executable" in result.stdout
    assert "FAIL" in result.stdout
    assert "setup.sh" in result.stdout


def test_resolve_runtime_command_uses_model_local_paths() -> None:
    model = ModelRegistry().get_model("asr_qwen3")
    assert model is not None

    command, working_dir, env = _resolve_runtime_command(model)

    assert command[0].endswith("src/sure_eval/models/asr_qwen3/.venv/bin/python")
    assert working_dir == model.path.resolve()
    assert env["MODEL_PATH"] == "Qwen/Qwen3-ASR-1.7B"


def test_runtime_readiness_detects_missing_runtime_for_synthetic_model(tmp_path: Path) -> None:
    model = _missing_runtime_registry(tmp_path).get_model("synthetic_missing_runtime_asr")
    assert model is not None

    readiness = get_runtime_readiness(model)

    assert readiness["runtime_executable"].endswith(
        "synthetic_missing_runtime_asr/.venv/bin/python"
    )
    assert readiness["failure_class"] == "runtime_executable_missing"
    runtime_check = next(check for check in readiness["checks"] if check["name"] == "runtime_executable")
    working_dir_check = next(check for check in readiness["checks"] if check["name"] == "working_dir")
    assert runtime_check["passed"] is False
    assert working_dir_check["passed"] is True


def test_canonicalize_language_maps_english_aliases() -> None:
    assert canonicalize_language("en") == "en"
    assert canonicalize_language("EN") == "en"
    assert canonicalize_language("English") == "en"


def test_canonicalize_language_maps_chinese_aliases() -> None:
    assert canonicalize_language("zh") == "zh"
    assert canonicalize_language("Chinese") == "zh"
    assert canonicalize_language("中文") == "zh"


def test_model_language_registry_maps_qwen3_and_default() -> None:
    assert map_language_for_model(model_name="asr_qwen3", language="en") == "English"
    assert map_language_for_model(model_name="asr_whisper", language="en") == "en"


def test_predict_and_validate_predictions(monkeypatch, tmp_path: Path) -> None:
    from sure_eval.inference import runner as inference_runner

    runner = CliRunner()
    input_path = tmp_path / "input.jsonl"
    input_rows = [
        {
            "instance_id": "case_ok",
            "task": "asr",
            "input": {
                "audio_path": "tests/fixtures/shared/asr/en_16k_10s.wav",
                "sample_rate": 16000,
            },
            "request": {"language": "en", "timestamps": False},
        },
        {
            "instance_id": "case_fail",
            "task": "asr",
            "input": {
                "audio_path": "tests/fixtures/shared/asr/en_16k_10s.wav",
                "sample_rate": 16000,
            },
            "request": {"language": "en", "timestamps": False},
        },
    ]
    with open(input_path, "w", encoding="utf-8") as handle:
        for row in input_rows:
            handle.write(json.dumps(row) + "\n")

    def _fake_subprocess_predict(*, record: dict[str, object], **kwargs) -> dict[str, object]:
        if record["instance_id"] == "case_fail":
            raise RuntimeError("simulated failure")
        return {
            "text": "hello world",
            "language": "en",
            "segments": [],
            "confidence": None,
        }

    monkeypatch.setattr(inference_runner, "_run_asr_subprocess", _fake_subprocess_predict)

    output_path = tmp_path / "predictions.jsonl"
    result = runner.invoke(
        app,
        [
            "predict",
            "asr_qwen3",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--task",
            "asr",
        ],
    )

    assert result.exit_code == 0
    assert output_path.exists()
    manifest_path = tmp_path / "predictions.manifest.json"
    assert manifest_path.exists()

    records = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert [record["status"] for record in records] == ["ok", "error"]

    validate_result = runner.invoke(
        app,
        [
            "validate-predictions",
            "--input",
            str(output_path),
            "--schema",
            "sure.prediction.v1",
        ],
    )
    assert validate_result.exit_code == 0
    assert "Prediction Validation" in validate_result.stdout


def test_qwen3_subprocess_receives_normalized_language(monkeypatch) -> None:
    from sure_eval.inference import runner as inference_runner

    model = ModelRegistry().get_model("asr_qwen3")
    assert model is not None
    record = {
        "instance_id": "case_001",
        "task": "asr",
        "input": {
            "audio_path": "tests/fixtures/shared/asr/en_16k_10s.wav",
            "sample_rate": 16000,
        },
        "request": {"language": "en", "timestamps": False},
    }

    captured: dict[str, object] = {}

    class _Completed:
        returncode = 0
        stdout = '{"text":"hello world","language":"English","segments":[],"confidence":null}\n'
        stderr = ""

    def _fake_run(cmd, **kwargs):  # noqa: ANN001
        captured["cmd"] = cmd
        captured["input"] = json.loads(kwargs["input"])
        return _Completed()

    monkeypatch.setattr(inference_runner.subprocess, "run", _fake_run)

    runtime_command, working_dir, env = inference_runner._resolve_runtime_command(model)
    result = inference_runner._run_asr_subprocess(
        model_info=model,
        runtime_command=runtime_command,
        working_dir=working_dir,
        env=env,
        record=record,
        device="auto",
    )

    assert result["language"] == "English"
    assert captured["input"]["language"] == "English"


def test_unsupported_language_produces_structured_error(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    input_path = tmp_path / "input.jsonl"
    input_path.write_text(
        json.dumps(
            {
                "instance_id": "case_fr",
                "task": "asr",
                "input": {
                    "audio_path": "tests/fixtures/shared/asr/en_16k_10s.wav",
                    "sample_rate": 16000,
                },
                "request": {"language": "fr", "timestamps": False},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    output_path = tmp_path / "predictions.jsonl"
    result = runner.invoke(
        app,
        [
            "predict",
            "asr_qwen3",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--task",
            "asr",
        ],
    )

    assert result.exit_code == 0
    record = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["status"] == "error"
    assert record["error"]["code"] == "unsupported_language"


def test_asr_whisper_uses_default_language_mapping() -> None:
    assert map_language_for_model(model_name="asr_whisper", language="en") == "en"


def test_predict_adapter_selection_covers_asr_utility_music_ir_vad_sd_s2tt_speaker_verification_speech_enhancement_songformer_and_omni() -> None:
    registry = ModelRegistry()
    asr_model = registry.get_model("asr_qwen3")
    ffmpeg_model = registry.get_model("ffmpeg")
    librosa_model = registry.get_model("librosa")
    songformer_model = registry.get_model("songformer")
    fireredvad_model = registry.get_model("fireredvad")
    silero_vad_model = registry.get_model("silero-vad")
    diarizen_model = registry.get_model("diarizen")
    s2tt_model = registry.get_model("s2tt_nllb")
    wespeaker_model = registry.get_model("wespeaker")
    deepfilternet_model = registry.get_model("deepfilternet")
    qwen3_omni_model = registry.get_model("qwen3_omni")
    assert asr_model is not None
    assert ffmpeg_model is not None
    assert librosa_model is not None
    assert songformer_model is not None
    assert fireredvad_model is not None
    assert silero_vad_model is not None
    assert diarizen_model is not None
    assert s2tt_model is not None
    assert wespeaker_model is not None
    assert deepfilternet_model is not None
    assert qwen3_omni_model is not None

    asr_adapter = create_predict_adapter(asr_model, task="asr")
    utility_adapter = create_predict_adapter(ffmpeg_model, task="utility")
    music_ir_adapter = create_predict_adapter(librosa_model, task="music_ir")
    songformer_adapter = create_predict_adapter(songformer_model, task="music_ir")
    fireredvad_adapter = create_predict_adapter(fireredvad_model, task="VAD")
    silero_vad_adapter = create_predict_adapter(silero_vad_model, task="vad")
    diarizen_adapter = create_predict_adapter(diarizen_model, task="diarization")
    s2tt_adapter = create_predict_adapter(s2tt_model, task="S2TT")
    speaker_adapter = create_predict_adapter(wespeaker_model, task="sv")
    enhancement_adapter = create_predict_adapter(deepfilternet_model, task="se")
    omni_adapter = create_predict_adapter(qwen3_omni_model, task="multimodal_chat")

    assert asr_adapter.runtime_protocol == "python_wrapper_transcribe"
    assert utility_adapter.runtime_protocol == "mcp_tool_call"
    assert utility_adapter.tool_name == "process_audio"
    assert music_ir_adapter.runtime_protocol == "mcp_tool_call"
    assert music_ir_adapter.tool_name == "extract_mfcc"
    assert songformer_adapter.runtime_protocol == "mcp_tool_call"
    assert songformer_adapter.tool_name == "analyze_music_structure"
    assert fireredvad_adapter.runtime_protocol == "mcp_tool_call"
    assert fireredvad_adapter.tool_name == "vad_predict"
    assert silero_vad_adapter.runtime_protocol == "mcp_tool_call"
    assert silero_vad_adapter.tool_name == "vad_predict"
    assert diarizen_adapter.runtime_protocol == "mcp_tool_call"
    assert diarizen_adapter.tool_name == "diarize"
    assert s2tt_adapter.runtime_protocol == "mcp_tool_call"
    assert s2tt_adapter.tool_name == "s2tt_translate"
    assert speaker_adapter.runtime_protocol == "mcp_tool_call"
    assert speaker_adapter.tool_name == "speaker_verify"
    assert enhancement_adapter.runtime_protocol == "mcp_tool_call"
    assert enhancement_adapter.tool_name == "enhance_audio"
    assert omni_adapter.runtime_protocol == "mcp_tool_call"
    assert omni_adapter.tool_name == "omni_chat_text_only"


def test_predict_adapter_unsupported_task_uses_task_adapter_code() -> None:
    model = ModelRegistry().get_model("librosa")
    assert model is not None

    try:
        create_predict_adapter(model, task="vlm")
    except AdapterError as exc:
        assert exc.code == "unsupported_task_adapter"
    else:  # pragma: no cover
        raise AssertionError("expected AdapterError")
