#!/usr/bin/env python3
"""Run or plan five-sample parity validation against the main-flow reference."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from compare_main_flow_artifacts import compare_runs


DEFAULT_REFERENCE_REPO = Path(os.environ.get("LEGACY_SURE_EVAL_ROOT", "<legacy-sure-eval-root>"))


def _skill_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _shell_join(command: list[str]) -> str:
    return " ".join(sh_quote(part) for part in command)


def sh_quote(value: str) -> str:
    if not value:
        return "''"
    safe = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_+-=.,/:@%"
    if all(ch in safe for ch in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _branch_template(branch: str, harness_root: Path, reference_repo: Path) -> Path:
    if branch == "harness":
        return harness_root / "scripts" / "templates" / "run_single_model_single_dataset.sh"
    return reference_repo / "docs" / "agents" / "main_flow_agent" / "templates" / "run_single_model_single_dataset.sh"


def _audio_template(branch: str, harness_root: Path, reference_repo: Path) -> Path:
    if branch == "harness":
        return harness_root / "scripts" / "templates" / "run_audio_evaluation_only.sh"
    return reference_repo / "docs" / "agents" / "main_flow_agent" / "templates" / "run_audio_evaluation_only.sh"


def _branch_repo_root(branch: str, harness_root: Path, reference_repo: Path) -> Path:
    return harness_root if branch == "harness" else reference_repo


def _repo_root_from_harness_skill(harness_root: Path) -> Path:
    return harness_root.parents[2]


def _config_path_for_branch(branch: str, harness_root: Path, reference_repo: Path) -> Path:
    if branch == "harness":
        candidates = [
            _repo_root_from_harness_skill(harness_root) / "sure" / "external" / "sure-evaluation" / "config" / "default.yaml",
        ]
    else:
        candidates = [
            reference_repo / "config" / "default.yaml",
        ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"No SURE_EVAL_CONFIG candidate found for {branch}: {candidates}")


def _parity_config_for_branch(branch: str, harness_root: Path, reference_repo: Path, run_dir: Path) -> Path:
    if branch != "harness":
        return _config_path_for_branch(branch, harness_root, reference_repo)

    try:
        import yaml
    except Exception as exc:
        raise RuntimeError("PyYAML is required to create the harness parity config") from exc

    harness_repo = _repo_root_from_harness_skill(harness_root)
    harness_config = _config_path_for_branch("harness", harness_root, reference_repo)
    datasets_root = Path(os.environ.get("SURE_EVAL_DATASETS_ROOT", str(harness_repo / "data" / "datasets"))).expanduser()
    jsonl_root = datasets_root / "sure_benchmark" / "jsonl"
    if not jsonl_root.is_dir():
        raise FileNotFoundError(f"SURE_EVAL_DATASETS_ROOT must contain sure_benchmark/jsonl: {datasets_root}")
    config = yaml.safe_load(harness_config.read_text(encoding="utf-8")) or {}
    data = dict(config.get("data") or {})
    data.update(
        {
            "root": str(run_dir / "_data"),
            "cache": str(run_dir / "_data" / "cache"),
            "models": str(run_dir / "_data" / "models"),
            "datasets": str(datasets_root),
            "results": str(run_dir / "results"),
        }
    )
    config["data"] = data

    config_path = run_dir / "_parity_harness_config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return config_path


def _tool_name_from_model_config(model_dir: Path) -> str:
    config_path = model_dir / "config.yaml"
    if not config_path.is_file():
        return "transcribe_audio"
    try:
        import yaml

        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return "transcribe_audio"
    tools = config.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if isinstance(tool, dict) and isinstance(tool.get("name"), str) and tool["name"]:
                return tool["name"]
    return "transcribe_audio"


def _run_dir(work_dir: Path, branch: str, model_name: str, dataset: str) -> Path:
    safe_dataset = "".join(ch if ch.isalnum() or ch in "._=-" else "_" for ch in dataset)
    return work_dir / branch / model_name / safe_dataset


def _env_for_branch(
    *,
    branch: str,
    harness_root: Path,
    reference_repo: Path,
    model_name: str,
    model_dir: Path,
    dataset: str,
    run_dir: Path,
    sample_count: int,
    metrics: list[str],
) -> dict[str, str]:
    repo_root = _branch_repo_root(branch, harness_root, reference_repo)
    tool_name = _tool_name_from_model_config(model_dir)
    env = os.environ.copy()
    env.update(
        {
            "REPO_ROOT": str(repo_root),
            "MODEL_NAME": model_name,
            "MODEL_DIR": str(model_dir),
            "TOOL_NAME": tool_name,
            "DATASET": dataset,
            "DATASETS": dataset,
            "RUN_DIR": str(run_dir),
            "RESULTS_DIR": str(run_dir / "results"),
            "MAX_SAMPLES": str(sample_count),
            "SMOKE_TEST_SAMPLES": "1",
            "NO_RESUME": "1",
            "EVALUATION_BACKEND": "external",
            "STRICT_MAIN_FLOW": "1",
            "PROBE_TRANSCRIBE": "0",
            "SURE_EVAL_CONFIG": str(_parity_config_for_branch(branch, harness_root, reference_repo, run_dir)),
        }
    )
    if metrics:
        env["METRICS"] = " ".join(metrics)
    return env


def _command_for_branch(branch: str, harness_root: Path, reference_repo: Path) -> list[str]:
    return ["bash", str(_branch_template(branch, harness_root, reference_repo))]


def _command_for_audio_branch(branch: str, harness_root: Path, reference_repo: Path) -> list[str]:
    return ["bash", str(_audio_template(branch, harness_root, reference_repo))]


def _run_command(command: list[str], *, env: dict[str, str], cwd: Path, log_path: Path, timeout_sec: int) -> dict[str, Any]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_sec,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        returncode = completed.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        returncode = 124
    log_path.write_text(
        "COMMAND: " + _shell_join(command) + "\n"
        + "CWD: "
        + str(cwd)
        + "\n"
        + "RETURN_CODE: " + str(returncode) + "\n"
        + "TIMED_OUT: " + str(timed_out).lower() + "\n"
        + "\nSTDOUT\n"
        + stdout
        + "\nSTDERR\n"
        + stderr,
        encoding="utf-8",
    )
    return {
        "command": command,
        "cwd": str(cwd),
        "returncode": returncode,
        "timed_out": timed_out,
        "log_path": str(log_path),
    }


def _allow_partial_audio_validation(run_dir: Path, sample_count: int) -> None:
    validation_path = run_dir / "validation_payload.json"
    if not validation_path.exists():
        return
    payload = json.loads(validation_path.read_text(encoding="utf-8"))
    if payload.get("is_valid") is True:
        return

    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    if not results:
        return

    for result in results:
        if not isinstance(result, dict):
            return
        provided = int(result.get("provided_predictions") or 0)
        if provided < 1 or provided > sample_count:
            return
        blocking_keys = (
            "extra_keys",
            "duplicate_keys",
            "empty_prediction_keys",
            "structured_extra_keys",
            "structured_duplicate_keys",
            "invalid_structured_rows",
            "contract_violation_keys",
        )
        if any(result.get(key) for key in blocking_keys):
            return

    backup_path = run_dir / "validation_payload.full_dataset_check.json"
    if not backup_path.exists():
        backup_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    payload["is_valid"] = True
    payload["partial_validation"] = {
        "enabled": True,
        "reason": "main-flow parity validation is intentionally limited to the same first samples in both branches",
        "sample_count": sample_count,
        "full_dataset_validation_backup": str(backup_path),
    }
    for result in results:
        result["is_valid"] = True
        result["partial_validation"] = {
            "enabled": True,
            "provided_predictions": result.get("provided_predictions"),
            "expected_samples": result.get("expected_samples"),
            "missing_keys_count": len(result.get("missing_keys") or []),
            "structured_missing_keys_count": len(result.get("structured_missing_keys") or []),
        }
    validation_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _prediction_keys(prediction_path: Path) -> set[str]:
    keys: set[str] = set()
    for line in prediction_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        key = line.split("\t", 1)[0].split(None, 1)[0]
        if key:
            keys.add(key)
    return keys


def _env_with_partial_audio_dataset(env: dict[str, str], *, run_dir: Path, cwd: Path) -> dict[str, str]:
    validation_path = run_dir / "validation_payload.json"
    if not validation_path.exists():
        return env

    payload = json.loads(validation_path.read_text(encoding="utf-8"))
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    if not results:
        return env

    try:
        import yaml
    except Exception as exc:
        raise RuntimeError("PyYAML is required to create the partial audio dataset config") from exc

    partial_datasets_dir = run_dir / "_partial_audio_data" / "datasets"
    partial_jsonl_dir = partial_datasets_dir / "sure_benchmark" / "jsonl"
    partial_jsonl_dir.mkdir(parents=True, exist_ok=True)

    wrote_any = False
    for result in results:
        if not isinstance(result, dict):
            continue
        dataset = str(result.get("dataset") or "")
        jsonl_path = Path(str(result.get("jsonl_path") or ""))
        prediction_path = Path(str(result.get("prediction_path") or ""))
        if not dataset or not prediction_path.exists():
            continue
        if not jsonl_path.is_absolute():
            jsonl_path = cwd / jsonl_path
        if not jsonl_path.exists():
            continue

        keys = _prediction_keys(prediction_path)
        if not keys:
            continue
        output_path = partial_jsonl_dir / f"{dataset}.jsonl"
        with jsonl_path.open("r", encoding="utf-8") as source, output_path.open("w", encoding="utf-8") as target:
            for line in source:
                if not line.strip():
                    continue
                sample = json.loads(line)
                if str(sample.get("key", "")) in keys:
                    target.write(json.dumps(sample, ensure_ascii=False) + "\n")
        wrote_any = True

    if not wrote_any:
        return env

    base_config = Path(env["SURE_EVAL_CONFIG"])
    config = yaml.safe_load(base_config.read_text(encoding="utf-8")) or {}
    data = dict(config.get("data") or {})
    data.update(
        {
            "root": str(run_dir / "_partial_audio_data"),
            "cache": str(run_dir / "_partial_audio_data" / "cache"),
            "models": str(run_dir / "_partial_audio_data" / "models"),
            "datasets": str(partial_datasets_dir),
            "results": str(run_dir / "results"),
        }
    )
    config["data"] = data
    config_path = run_dir / "_partial_audio_config.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")

    audio_env = dict(env)
    audio_env["SURE_EVAL_CONFIG"] = str(config_path)
    audio_env["SURE_EVAL_MINIMAL_DATASET_MANAGER"] = "0"
    return audio_env


def _run_branch(
    *,
    branch: str,
    harness_root: Path,
    reference_repo: Path,
    model_name: str,
    model_dir: Path,
    dataset: str,
    run_dir: Path,
    sample_count: int,
    metrics: list[str],
    execute: bool,
    timeout_sec: int,
) -> dict[str, Any]:
    env = _env_for_branch(
        branch=branch,
        harness_root=harness_root,
        reference_repo=reference_repo,
        model_name=model_name,
        model_dir=model_dir,
        dataset=dataset,
        run_dir=run_dir,
        sample_count=sample_count,
        metrics=metrics,
    )
    command = _command_for_branch(branch, harness_root, reference_repo)
    repo_root = _branch_repo_root(branch, harness_root, reference_repo)
    planned = {
        "branch": branch,
        "run_dir": str(run_dir),
        "command": command,
        "env": {key: env[key] for key in sorted(env) if key in {
            "REPO_ROOT",
            "MODEL_NAME",
            "MODEL_DIR",
            "DATASET",
            "DATASETS",
            "TOOL_NAME",
            "RUN_DIR",
            "RESULTS_DIR",
            "MAX_SAMPLES",
            "SMOKE_TEST_SAMPLES",
            "METRICS",
            "EVALUATION_BACKEND",
            "STRICT_MAIN_FLOW",
            "SURE_EVAL_CONFIG",
            "SURE_EVALUATION_HOME",
        }},
    }
    if not execute:
        return planned

    result = _run_command(
        command,
        env=env,
        cwd=repo_root,
        log_path=run_dir / "logs" / f"{branch}_main_flow.log",
        timeout_sec=timeout_sec,
    )
    planned["execution"] = result
    handoff = run_dir / "evaluation_handoff.json"
    if result["returncode"] == 0 and handoff.exists() and not (run_dir / "evaluation_payload.json").exists():
        _allow_partial_audio_validation(run_dir, sample_count)
        audio_env = _env_with_partial_audio_dataset(env, run_dir=run_dir, cwd=repo_root)
        audio_command = _command_for_audio_branch(branch, harness_root, reference_repo)
        audio_result = _run_command(
            audio_command,
            env=audio_env,
            cwd=repo_root,
            log_path=run_dir / "logs" / f"{branch}_audio_eval.log",
            timeout_sec=timeout_sec,
        )
        planned["audio_execution"] = audio_result
    return planned


def main() -> int:
    parser = argparse.ArgumentParser(description="Run or plan harness/reference main-flow parity validation")
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--dataset", action="append", required=True, help="Dataset to validate. Repeat for multiple datasets.")
    parser.add_argument("--metric", action="append", default=[], help="Metric override. Repeat for multiple metrics.")
    parser.add_argument("--sample-count", type=int, default=5)
    parser.add_argument("--work-dir", default=f"/tmp/sure_eval_main_flow_parity_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}")
    parser.add_argument("--harness-skill-root", default=str(_skill_root()))
    parser.add_argument("--reference-repo-root", default=str(DEFAULT_REFERENCE_REPO))
    parser.add_argument("--execute", action="store_true", help="Actually run both branches. Without this, only prints the plan.")
    parser.add_argument("--timeout-sec", type=int, default=1800, help="Timeout per branch command.")
    parser.add_argument("--compare-only", action="store_true", help="Skip execution and compare existing run dirs.")
    parser.add_argument("--harness-run-dir", action="append", help="Existing harness run dir for compare-only. Repeat per dataset.")
    parser.add_argument("--reference-run-dir", action="append", help="Existing reference run dir for compare-only. Repeat per dataset.")
    parser.add_argument("--output")
    args = parser.parse_args()

    harness_root = Path(args.harness_skill_root).resolve()
    reference_repo = Path(args.reference_repo_root).resolve()
    model_dir = Path(args.model_dir).resolve()
    work_dir = Path(args.work_dir).resolve()
    metrics = [metric for raw in args.metric for metric in str(raw).replace(",", " ").split() if metric]

    report: dict[str, Any] = {
        "schema": "sure.eval.main_flow_parity.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "execute": args.execute,
        "sample_count": args.sample_count,
        "timeout_sec": args.timeout_sec,
        "harness_skill_root": str(harness_root),
        "reference_repo_root": str(reference_repo),
        "model_name": args.model_name,
        "model_dir": str(model_dir),
        "datasets": args.dataset,
        "metrics": metrics,
        "runs": [],
        "comparisons": [],
    }

    if args.compare_only:
        if not args.harness_run_dir or not args.reference_run_dir or len(args.harness_run_dir) != len(args.reference_run_dir):
            raise ValueError("--compare-only requires matching --harness-run-dir and --reference-run-dir values")
        pairs = zip(args.reference_run_dir, args.harness_run_dir, strict=True)
        for reference_run_dir, harness_run_dir in pairs:
            report["comparisons"].append(
                compare_runs(
                    Path(reference_run_dir).resolve(),
                    Path(harness_run_dir).resolve(),
                    sample_limit=args.sample_count,
                    score_tolerance=1e-9,
                )
            )
    else:
        for dataset in args.dataset:
            branch_reports = []
            for branch in ("reference", "harness"):
                run_dir = _run_dir(work_dir, branch, args.model_name, dataset)
                branch_reports.append(
                    _run_branch(
                        branch=branch,
                        harness_root=harness_root,
                        reference_repo=reference_repo,
                        model_name=args.model_name,
                        model_dir=model_dir,
                        dataset=dataset,
                        run_dir=run_dir,
                        sample_count=args.sample_count,
                        metrics=metrics,
                        execute=args.execute,
                        timeout_sec=args.timeout_sec,
                    )
                )
            report["runs"].append({"dataset": dataset, "branches": branch_reports})
            reference_run = _run_dir(work_dir, "reference", args.model_name, dataset)
            harness_run = _run_dir(work_dir, "harness", args.model_name, dataset)
            if args.execute and reference_run.exists() and harness_run.exists():
                report["comparisons"].append(
                    compare_runs(reference_run, harness_run, sample_limit=args.sample_count, score_tolerance=1e-9)
                )

    report["ok"] = all(item.get("ok", False) for item in report["comparisons"]) if report["comparisons"] else not args.execute
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
