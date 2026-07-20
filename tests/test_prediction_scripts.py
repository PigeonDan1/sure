from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

from sure_eval.core.config import Config

REPO_ROOT = Path(__file__).resolve().parents[1]


def _extract_json_payload(stdout: str) -> dict:
    start = stdout.rfind("\n{")
    if start == -1:
        start = stdout.find("{")
    else:
        start += 1
    return json.loads(stdout[start:])


def _write_config(tmp_path: Path) -> Path:
    config = Config.from_env().model_dump()
    config["data"]["datasets"] = str(tmp_path / "datasets")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return config_path


def _write_dataset(tmp_path: Path, dataset_name: str = "aishell1") -> None:
    jsonl_dir = tmp_path / "datasets" / "sure_benchmark" / "jsonl"
    jsonl_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = jsonl_dir / f"{dataset_name}.jsonl"
    rows = [
        {"key": "utt1", "path": "a.wav", "target": "你好", "task": "ASR", "language": "zh", "dataset": dataset_name},
        {"key": "utt2", "path": "b.wav", "target": "世界", "task": "ASR", "language": "zh", "dataset": dataset_name},
    ]
    with open(jsonl_path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_materialize_predictions_template_generates_manifest_and_template(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    _write_dataset(tmp_path)
    output_dir = tmp_path / "templates"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/materialize_predictions_template.py",
            "--dataset",
            "aishell1",
            "--output-dir",
            str(output_dir),
            "--config",
            str(config_path),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = _extract_json_payload(result.stdout)
    assert payload["templates"][0]["dataset"] == "aishell1"
    assert (output_dir / "aishell1.txt").read_text(encoding="utf-8").splitlines() == ["utt1\t", "utt2\t"]
    assert (output_dir / "manifest.json").exists()


def test_materialize_predictions_template_preserves_existing_predictions(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    _write_dataset(tmp_path)
    output_dir = tmp_path / "templates"
    output_dir.mkdir()
    prediction_path = output_dir / "aishell1.txt"
    prediction_path.write_text("utt1\talready done\nutt2\t\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/materialize_predictions_template.py",
            "--dataset",
            "aishell1",
            "--output-dir",
            str(output_dir),
            "--config",
            str(config_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert prediction_path.read_text(encoding="utf-8") == "utt1\talready done\nutt2\t\n"
    payload = _extract_json_payload(result.stdout)
    assert payload["templates"][0]["template_path"] == str(prediction_path)


def test_multi_dataset_run_template_does_not_overwrite_resume_predictions() -> None:
    template = (REPO_ROOT / "docs/agents/main_flow_agent/templates/run_single_model.sh").read_text(
        encoding="utf-8"
    )
    materialize_block = template.split("[2/5] Materializing prediction templates...", 1)[1].split(
        "[2.5/5] Smoke test", 1
    )[0]

    assert "--overwrite" not in materialize_block


def test_validate_prediction_files_reports_missing_extra_and_empty(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    _write_dataset(tmp_path)
    pred_path = tmp_path / "aishell1.txt"
    pred_path.write_text("utt1\t你好\nutt3\t额外项\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/validate_prediction_files.py",
            "--dataset",
            "aishell1",
            "--pred",
            "aishell1",
            str(pred_path),
            "--config",
            str(config_path),
            "--require-nonempty",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["is_valid"] is False
    report = payload["results"][0]
    assert report["missing_keys"] == ["utt2"]
    assert report["extra_keys"] == ["utt3"]
    assert report["empty_prediction_keys"] == []


def test_resume_pending_prediction_keys_detects_complete_resume() -> None:
    from scripts.generate_predictions_via_server import _pending_prediction_keys

    samples = [{"key": "utt1"}, {"key": "utt2"}, {"key": "utt3"}]

    assert _pending_prediction_keys(samples, {"utt1": "one", "utt2": "two", "utt3": "three"}) == []
    assert _pending_prediction_keys(samples, {"utt1": "one", "utt3": "three"}) == ["utt2"]


def test_complete_resume_skips_server_startup_from_prediction_file(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    _write_dataset(tmp_path)
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.yaml").write_text(
        "server:\n"
        "  command: ['/no/such/python', 'server.py']\n"
        "  working_dir: '.'\n"
        "tools:\n"
        "  - name: transcribe_audio\n",
        encoding="utf-8",
    )
    run_dir = tmp_path / "run"
    predictions_dir = run_dir / "predictions"
    predictions_dir.mkdir(parents=True)
    (predictions_dir / "aishell1.txt").write_text("utt1\tone\nutt2\ttwo\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/generate_predictions_via_server.py",
            "--model-dir",
            str(model_dir),
            "--dataset",
            "aishell1",
            "--run-dir",
            str(run_dir),
            "--tool-name",
            "transcribe_audio",
            "--config",
            str(config_path),
            "--resume",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    payload = _extract_json_payload(result.stdout)
    assert payload["skipped_server_startup"] is True
    status = json.loads((run_dir / "prediction_generation_status.json").read_text(encoding="utf-8"))
    assert status["datasets"][0]["num_pending_samples"] == 0


def test_complete_resume_skips_server_startup_from_result_log(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    _write_dataset(tmp_path)
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.yaml").write_text(
        "server:\n"
        "  command: ['/no/such/python', 'server.py']\n"
        "  working_dir: '.'\n"
        "tools:\n"
        "  - name: transcribe_audio\n",
        encoding="utf-8",
    )
    run_dir = tmp_path / "run"
    predictions_dir = run_dir / "predictions"
    logs_dir = predictions_dir / "logs"
    logs_dir.mkdir(parents=True)
    (predictions_dir / "aishell1.txt").write_text("utt1\t\nutt2\t\n", encoding="utf-8")
    (logs_dir / "aishell1_results.log").write_text("utt1\tone\nutt2\ttwo\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/generate_predictions_via_server.py",
            "--model-dir",
            str(model_dir),
            "--dataset",
            "aishell1",
            "--run-dir",
            str(run_dir),
            "--tool-name",
            "transcribe_audio",
            "--config",
            str(config_path),
            "--resume",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    payload = _extract_json_payload(result.stdout)
    assert payload["skipped_server_startup"] is True
    assert (predictions_dir / "aishell1.txt").read_text(encoding="utf-8") == "utt1\tone\nutt2\ttwo\n"
