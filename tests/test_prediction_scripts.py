from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

from sure_eval.core.config import Config


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
            "python",
            "scripts/materialize_predictions_template.py",
            "--dataset",
            "aishell1",
            "--output-dir",
            str(output_dir),
            "--config",
            str(config_path),
        ],
        cwd="/cpfs/user/jingpeng/workspace/sure-eval",
        check=True,
        capture_output=True,
        text=True,
    )

    payload = _extract_json_payload(result.stdout)
    assert payload["templates"][0]["dataset"] == "aishell1"
    assert (output_dir / "aishell1.txt").read_text(encoding="utf-8").splitlines() == ["utt1\t", "utt2\t"]
    assert (output_dir / "manifest.json").exists()


def test_validate_prediction_files_reports_missing_extra_and_empty(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    _write_dataset(tmp_path)
    pred_path = tmp_path / "aishell1.txt"
    pred_path.write_text("utt1\t你好\nutt3\t额外项\n", encoding="utf-8")

    result = subprocess.run(
        [
            "python",
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
        cwd="/cpfs/user/jingpeng/workspace/sure-eval",
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
