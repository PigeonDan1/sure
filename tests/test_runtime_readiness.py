from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from sure_eval.cli import app
from sure_eval.inference.runner import get_runtime_readiness
from sure_eval.models.registry import ModelInfo


def test_runtime_readiness_detects_missing_working_dir(tmp_path: Path) -> None:
    model_dir = tmp_path / "fake_asr"
    model_dir.mkdir()
    (model_dir / "config.yaml").write_text("name: fake_asr\n", encoding="utf-8")
    (model_dir / "model.py").write_text("class Fake: pass\n", encoding="utf-8")
    (model_dir / "server.py").write_text("print('ok')\n", encoding="utf-8")

    model = ModelInfo(
        name="fake_asr",
        task="ASR",
        path=model_dir,
        config={
            "name": "fake_asr",
            "task": "ASR",
            "server": {
                "command": [".venv/bin/python", "server.py"],
                "working_dir": "missing_runtime_dir",
                "env": {},
                "timeout": 300,
            },
        },
    )

    readiness = get_runtime_readiness(model)

    assert readiness["failure_class"] == "working_dir_missing"
    working_dir_check = next(check for check in readiness["checks"] if check["name"] == "working_dir")
    assert working_dir_check["passed"] is False


def test_doctor_can_write_json_report(tmp_path: Path) -> None:
    runner = CliRunner()
    report_path = tmp_path / "doctor_report.json"

    result = runner.invoke(app, ["doctor", "asr_whisper", "--json", str(report_path)])

    assert result.exit_code == 1
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["model"] == "asr_whisper"
    assert report["failure_class"] == "runtime_executable_missing"
    assert report["status"] == "blocked"


def test_predict_dry_run_reports_input_schema_invalid(tmp_path: Path) -> None:
    runner = CliRunner()
    bad_input = tmp_path / "bad_input.jsonl"
    bad_input.write_text(
        json.dumps(
            {
                "instance_id": "case_bad",
                "task": "asr",
                "input": {
                    "audio_path": "tests/fixtures/shared/asr/en_16k_10s.wav",
                },
                "request": {"language": "en"},
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
            str(bad_input),
            "--output",
            str(output_path),
            "--task",
            "asr",
            "--dry-run",
        ],
    )

    assert result.exit_code == 1
    assert "input_schema_invalid" in result.stdout
