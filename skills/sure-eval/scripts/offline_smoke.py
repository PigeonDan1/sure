#!/usr/bin/env python3
"""Exercise real SURE validation and scoring on synthetic predictions, offline."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / "src"))
    import yaml
    from sure_eval.core.config import Config

    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    dataset_dir = output / "datasets" / "sure_benchmark" / "jsonl"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        {"key": "utt1", "target": "你好", "task": "ASR", "language": "zh"},
        {"key": "utt2", "target": "世界", "task": "ASR", "language": "zh"},
    ]
    (dataset_dir / "aishell1.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8",
    )
    config = Config.from_yaml(root / "config" / "default.yaml").model_dump()
    config["data"].update({
        "datasets": str(output / "datasets"), "results": str(output / "results"),
    })
    config_path = output / "config.yaml"
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    predictions = output / "aishell1.txt"
    predictions.write_text("utt1\t你好\nutt2\t世间\n", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    commands = []
    for script, report, extra in [
        ("validate_prediction_files.py", "validation.json", ["--require-nonempty"]),
        ("evaluate_predictions.py", "metrics.json", []),
    ]:
        command = [
            sys.executable, str(root / "scripts" / script), "--dataset", "aishell1",
            "--pred", "aishell1", str(predictions), "--config", str(config_path),
            "--output", str(output / report), *extra,
        ]
        commands.append(command)
        completed = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
        (output / f"{script}.log").write_text(
            completed.stdout + completed.stderr, encoding="utf-8",
        )
        if completed.returncode:
            print(completed.stdout + completed.stderr, file=sys.stderr)
            return completed.returncode
    validation = json.loads((output / "validation.json").read_text(encoding="utf-8"))
    metric = json.loads((output / "metrics.json").read_text(encoding="utf-8"))["results"][0]
    if not validation["is_valid"] or metric["num_samples"] != 2 or abs(metric["score"] - 0.25) > 1e-9:
        raise RuntimeError("Synthetic fixture must validate and score one error in four characters")
    result = {
        "status": "completed", "task": "ASR", "model": "synthetic-predictions",
        "dataset": "aishell1 (synthetic fixture)", "run_dir": str(output),
        "readiness": "not_required_existing_predictions",
        "metrics": {"metric": metric["metric"], "score": metric["score"], "num_samples": 2},
        "prediction_path": str(predictions), "validation_path": str(output / "validation.json"),
        "report_path": str(output / "metrics.json"), "commands": commands,
    }
    (output / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
