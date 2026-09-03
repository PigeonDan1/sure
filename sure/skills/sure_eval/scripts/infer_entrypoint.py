#!/usr/bin/env python3
"""Run model inference for one /sure_eval run inside the approved runtime.

This file replaces the bash execution templates. scripts/run_infer.py launches
it with the approved Harness Python (inside the container, or on the trusted
host for a Python binding) after writing execution_surface.json; every stage
below shells out to a bundled deterministic script with that same interpreter,
so nothing here depends on bash, ``node`` or the evaluation engine.

Stages, in order: guards, tool_name, config, prepare, materialize, smoke,
generate, validate, protocol, references, finalize. On failure the last stdout
line is ``INFER_STAGE_FAILED <stage>`` and the exit code is non-zero; the host
records the stage in execution_result.json.

Environment (all injected by the launcher; the names are the templates'):
  REPO_ROOT, MODEL_NAME, MODEL_DIR, RUN_DIR, RUN_ID, TOOL_NAME, PROTOCOL_ID,
  DATASETS, MAX_SAMPLES, NO_RESUME, DEVICE, METRICS, SMOKE_TEST_SAMPLES,
  SURE_EVAL_CONFIG, SURE_EVAL_DATASETS_ROOT, SURE_EVAL_APPROVED_MODEL_DIR,
  SURE_EVAL_APPROVED_RESULT_DIR, SURE_EVAL_PUBLISHED_RUN_DIR,
  SURE_EVAL_INPUT_RESOLVED, HARNESS_PYTHON_BIN, MODEL_PYTHON
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STAGES: tuple[str, ...] = (
    "guards",
    "tool_name",
    "config",
    "prepare",
    "materialize",
    "smoke",
    "generate",
    "validate",
    "protocol",
    "references",
    "finalize",
)
PROTOCOL_IDS = ("standard_system", "strict_core")
TASK_TOOL_DEFAULTS = {
    "ASR": "transcribe_audio",
    "S2TT": "translate_audio",
    "TTS": "synthesize_speech",
    "VC": "convert_voice",
}
DEFAULT_TOOL_NAME = "transcribe_audio"
DEFAULT_SMOKE_SAMPLES = 10
STAGE_MARKER = "INFER_STAGE_FAILED"


class StageError(RuntimeError):
    def __init__(self, stage: str, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.stage = stage
        self.exit_code = exit_code if exit_code else 1


@dataclass
class Ctx:
    repo_root: Path
    harness_root: Path
    model_name: str
    model_dir: Path
    run_dir: Path
    run_id: str
    protocol_id: str
    requested_datasets: list[str]
    max_samples: int
    no_resume: bool
    device: str
    metrics: str
    smoke_samples: int | None
    child_env: dict[str, str]
    tool_name: str = ""
    config_path: Path | None = None
    datasets_root: Path | None = None
    datasets: list[str] = field(default_factory=list)
    languages: dict[str, str] = field(default_factory=dict)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default) or default


def _real(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _run(ctx: Ctx, stage: str, script: str, *args: str) -> None:
    command = [sys.executable, str(ctx.repo_root / "scripts" / script), *args]
    print(f"[{stage}] {' '.join(command)}", flush=True)
    completed = subprocess.run(command, cwd=str(ctx.harness_root), env=ctx.child_env, check=False)
    if completed.returncode != 0:
        raise StageError(stage, f"{script} exited with code {completed.returncode}", completed.returncode)


def _nonempty_prediction_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("\t", 1)
        if len(parts) > 1 and parts[1].strip():
            count += 1
    return count


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def stage_guards() -> Ctx:
    repo_root = _real(_env("REPO_ROOT") or Path(__file__).resolve().parent.parent)
    if not (repo_root / "scripts").is_dir():
        raise StageError("guards", f"REPO_ROOT does not look like the sure_eval skill root: {repo_root}")
    harness_root = repo_root.parents[2] if len(repo_root.parents) > 2 else repo_root

    model_name = _env("MODEL_NAME")
    if not model_name:
        raise StageError("guards", "MODEL_NAME must be set")
    approved_model_dir = _env("SURE_EVAL_APPROVED_MODEL_DIR")
    if not approved_model_dir:
        raise StageError("guards", "SURE_EVAL_APPROVED_MODEL_DIR must name the approved model directory")
    approved = _real(approved_model_dir)
    model_dir = _real(_env("MODEL_DIR") or approved)
    if not approved.is_dir() or model_dir != approved:
        raise StageError("guards", f"MODEL_DIR must be the approved model directory {approved}, got {model_dir}")

    protocol_id = _env("PROTOCOL_ID", "standard_system")
    if protocol_id not in PROTOCOL_IDS:
        raise StageError("guards", f"PROTOCOL_ID must be one of {', '.join(PROTOCOL_IDS)}, got {protocol_id!r}")

    raw_run_dir = _env("RUN_DIR")
    if not raw_run_dir:
        raise StageError("guards", "RUN_DIR must be set")
    run_dir = _real(raw_run_dir)
    staging_root = (harness_root / "sure" / "results" / model_name / protocol_id).resolve()
    allowed_roots = [
        _real(value) for value in (_env("SURE_EVAL_APPROVED_RESULT_DIR"), _env("SURE_EVAL_PUBLISHED_RUN_DIR")) if value
    ]
    if not _is_under(run_dir, staging_root) and run_dir not in allowed_roots:
        raise StageError(
            "guards",
            f"RUN_DIR must stay under {staging_root} or equal the launcher's result directory, got {run_dir}",
        )

    model_python = _env("MODEL_PYTHON") or _env("PYTHON_BIN") or "python"
    resolved_model_python = shutil.which(model_python) or model_python
    if Path(resolved_model_python).exists() and _real(resolved_model_python) == _real(sys.executable):
        raise StageError("guards", "Harness Python and Model Python resolved to the same executable")
    # The Harness Python's own imports are proved by the compliance probe before
    # launch; the bundled scripts fail loudly on their own if that ever drifts.

    datasets = (_env("DATASETS") or _env("DATASET")).split()
    if not datasets:
        raise StageError("guards", "DATASETS must name at least one dataset source")
    try:
        max_samples = int(_env("MAX_SAMPLES", "0") or 0)
    except ValueError as exc:
        raise StageError("guards", f"MAX_SAMPLES must be an integer: {exc}") from exc
    smoke_samples: int | None = None
    if _env("SMOKE_TEST_SAMPLES"):
        try:
            smoke_samples = int(_env("SMOKE_TEST_SAMPLES"))
        except ValueError as exc:
            raise StageError("guards", f"SMOKE_TEST_SAMPLES must be an integer: {exc}") from exc

    child_env = dict(os.environ)
    scripts_dir = str(repo_root / "scripts")
    child_env["PYTHONPATH"] = scripts_dir + (os.pathsep + child_env["PYTHONPATH"] if child_env.get("PYTHONPATH") else "")
    child_env.setdefault("HF_HUB_CACHE", str(run_dir / ".runtime" / "cache" / "huggingface" / "hub"))
    child_env.setdefault("HARNESS_PYTHON_BIN", sys.executable)
    child_env["SURE_EVAL_HARNESS_PYTHON_BIN"] = child_env["HARNESS_PYTHON_BIN"]
    child_env["MODEL_PYTHON"] = model_python
    child_env["SURE_EVAL_METRICS"] = _env("METRICS")
    child_env.setdefault("SURE_EVAL_EXECUTION_PATH", "unknown")
    device = _env("DEVICE")
    if device:
        child_env.setdefault("SURE_EVAL_DEVICE_REQUEST", device)
        child_env.setdefault("SURE_EVAL_DEVICE_ACTUAL", device)

    (run_dir / "predictions" / "logs").mkdir(parents=True, exist_ok=True)
    ctx = Ctx(
        repo_root=repo_root,
        harness_root=harness_root,
        model_name=model_name,
        model_dir=model_dir,
        run_dir=run_dir,
        run_id=_env("RUN_ID") or run_dir.name,
        protocol_id=protocol_id,
        requested_datasets=datasets,
        max_samples=max_samples,
        no_resume=_env("NO_RESUME") == "1",
        device=device,
        metrics=_env("METRICS"),
        smoke_samples=smoke_samples,
        child_env=child_env,
    )
    print("========================================")
    print(f"SURE inference run: {ctx.run_id}")
    print(f"Model: {model_name} ({model_dir})")
    print(f"Datasets: {' '.join(datasets)}")
    print(f"Harness Python: {sys.executable}")
    print(f"Model Python: {model_python}")
    print(f"Execution path: {child_env['SURE_EVAL_EXECUTION_PATH']}")
    print(f"DEVICE: {device or 'auto/from-runtime'}")
    print(f"CUDA_VISIBLE_DEVICES: {os.environ.get('CUDA_VISIBLE_DEVICES', '<unset>')}")
    print(f"MAX_SAMPLES: {max_samples}")
    print("========================================", flush=True)
    return ctx


def stage_tool_name(ctx: Ctx) -> None:
    tool_name = _env("TOOL_NAME")
    if not tool_name:
        config_yaml = ctx.model_dir / "config.yaml"
        if config_yaml.is_file():
            import yaml

            config = yaml.safe_load(config_yaml.read_text(encoding="utf-8")) or {}
            for tool in config.get("tools") or []:
                if isinstance(tool, dict) and tool.get("name"):
                    tool_name = str(tool["name"])
                    break
            if not tool_name:
                model = config.get("model") if isinstance(config.get("model"), dict) else {}
                task = str(model.get("task") or config.get("task") or config.get("task_type") or "").strip().upper()
                tool_name = TASK_TOOL_DEFAULTS.get(task, "")
    ctx.tool_name = tool_name or DEFAULT_TOOL_NAME
    ctx.child_env["TOOL_NAME"] = ctx.tool_name


def stage_config(ctx: Ctx) -> None:
    explicit = _env("SURE_EVAL_CONFIG")
    generated = ctx.run_dir / "_harness_config.yaml"
    if explicit:
        config_path = _real(explicit)
        if not config_path.is_file():
            raise StageError("config", f"SURE_EVAL_CONFIG does not exist: {config_path}")
    elif generated.is_file():
        config_path = generated
    else:
        base_config = ctx.harness_root / "sure" / "external" / "sure-evaluation" / "config" / "default.yaml"
        datasets_root = _real(_env("SURE_EVAL_DATASETS_ROOT") or ctx.harness_root / "data" / "datasets")
        if not base_config.is_file():
            raise StageError("config", f"SURE_EVAL_CONFIG is unset and the engine default config is missing: {base_config}")
        if not (datasets_root / "sure_benchmark" / "jsonl").is_dir():
            raise StageError("config", f"SURE_EVAL_DATASETS_ROOT must contain sure_benchmark/jsonl: {datasets_root}")
        import yaml

        config = yaml.safe_load(base_config.read_text(encoding="utf-8")) or {}
        data = dict(config.get("data") or {})
        data.update(
            {
                "root": str(ctx.harness_root / "data"),
                "cache": str(ctx.harness_root / "data" / "cache"),
                "models": str(ctx.harness_root / "data" / "models"),
                "datasets": str(datasets_root),
                "results": str(ctx.run_dir / "results"),
            }
        )
        config["data"] = data
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
        config_path = generated
    ctx.config_path = config_path
    ctx.child_env["SURE_EVAL_CONFIG"] = str(config_path)

    datasets_root_value = _env("SURE_EVAL_DATASETS_ROOT")
    if not datasets_root_value:
        import yaml

        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        datasets_root_value = str((config.get("data") or {}).get("datasets") or "")
    ctx.datasets_root = _real(datasets_root_value) if datasets_root_value else (ctx.harness_root / "data" / "datasets")


def stage_prepare(ctx: Ctx) -> None:
    summary = ctx.run_dir / "prepare_summary.json"
    _run(ctx, "prepare", "prepare_sure_dataset.py", "--dataset", *ctx.requested_datasets, "--output", str(summary))
    prepared = [
        str(item["dataset"])
        for item in _read_json(summary).get("prepared", [])
        if isinstance(item, dict) and item.get("dataset")
    ]
    if not prepared:
        raise StageError("prepare", "dataset preparation did not return any concrete dataset splits")
    ctx.datasets = prepared
    print(f"Concrete datasets: {' '.join(prepared)}", flush=True)

    resolved_input = _env("SURE_EVAL_INPUT_RESOLVED")
    if resolved_input:
        for row in _read_json(Path(resolved_input)).get("datasets", []):
            if isinstance(row, dict) and row.get("name") and row.get("language"):
                ctx.languages[str(row["name"])] = str(row["language"])
    fallback_language = _env("LANGUAGE")
    if fallback_language:
        for dataset in prepared:
            ctx.languages.setdefault(dataset, fallback_language)


def stage_materialize(ctx: Ctx) -> None:
    _run(
        ctx,
        "materialize",
        "materialize_predictions_template.py",
        "--dataset",
        *ctx.datasets,
        "--output-dir",
        str(ctx.run_dir / "predictions"),
        "--manifest-name",
        "manifest.json",
        "--overwrite",
    )


def _generate_args(ctx: Ctx, dataset: str, *, max_samples: int | None) -> list[str]:
    args = [
        "--model-dir",
        str(ctx.model_dir),
        "--dataset",
        dataset,
        "--run-dir",
        str(ctx.run_dir),
        "--tool-name",
        ctx.tool_name,
    ]
    if max_samples:
        args += ["--max-samples", str(max_samples)]
    language = ctx.languages.get(dataset)
    if language:
        args += ["--language", language]
    if not ctx.no_resume:
        args.append("--resume")
    if ctx.device:
        args += ["--device", ctx.device]
    return args


def stage_smoke(ctx: Ctx) -> None:
    dataset = ctx.datasets[0]
    samples = ctx.smoke_samples
    if samples is None:
        samples = ctx.max_samples if 0 < ctx.max_samples < DEFAULT_SMOKE_SAMPLES else DEFAULT_SMOKE_SAMPLES
    print(f"Smoke test on {dataset} ({samples} samples)", flush=True)
    _run(ctx, "smoke", "generate_predictions_via_server.py", *_generate_args(ctx, dataset, max_samples=samples))
    valid = _nonempty_prediction_rows(ctx.run_dir / "predictions" / f"{dataset}.txt")
    if valid < 1:
        raise StageError(
            "smoke",
            f"smoke test produced no valid predictions in the first {samples} samples of {dataset}; "
            f"see {ctx.run_dir / 'predictions' / 'logs'}",
        )
    print(f"Smoke test passed ({valid} valid rows)", flush=True)


def stage_generate(ctx: Ctx) -> None:
    for dataset in ctx.datasets:
        print(f"Generating predictions for {dataset}", flush=True)
        _run(
            ctx,
            "generate",
            "generate_predictions_via_server.py",
            *_generate_args(ctx, dataset, max_samples=ctx.max_samples or None),
        )


def stage_validate(ctx: Ctx) -> None:
    args = [
        "--dataset",
        *ctx.datasets,
        "--pred-dir",
        str(ctx.run_dir / "predictions"),
        "--require-nonempty",
        "--output",
        str(ctx.run_dir / "validation_payload.json"),
    ]
    if ctx.max_samples:
        args += ["--max-samples", str(ctx.max_samples)]
    _run(ctx, "validate", "validate_prediction_files.py", *args)


def stage_protocol(ctx: Ctx) -> None:
    _run(
        ctx,
        "protocol",
        "protocol_writer.py",
        "--results-dir",
        str(ctx.run_dir),
        "--protocol-id",
        ctx.protocol_id,
        "--model-dir",
        str(ctx.model_dir),
        "--tool-name",
        ctx.tool_name,
    )


def stage_references(ctx: Ctx) -> None:
    assert ctx.datasets_root is not None
    source_dir = ctx.datasets_root / "sure_benchmark" / "jsonl"
    target_dir = ctx.run_dir / "references" / "sure_benchmark" / "jsonl"
    target_dir.mkdir(parents=True, exist_ok=True)
    for dataset in ctx.datasets:
        source = source_dir / f"{dataset}.jsonl"
        if not source.is_file():
            raise StageError("references", f"dataset projection is missing: {source}")
        shutil.copy2(source, target_dir / source.name)
    print(f"Reference projections copied to {target_dir}", flush=True)


def stage_finalize(ctx: Ctx) -> None:
    published = _env("SURE_EVAL_PUBLISHED_RUN_DIR") or str(ctx.run_dir)
    _run(ctx, "finalize", "finalize_result_bundle.py", "--run-dir", str(ctx.run_dir), "--published-run-dir", published)


def main() -> int:
    stage = "guards"
    try:
        ctx = stage_guards()
        for stage, function in (
            ("tool_name", stage_tool_name),
            ("config", stage_config),
            ("prepare", stage_prepare),
            ("materialize", stage_materialize),
            ("smoke", stage_smoke),
            ("generate", stage_generate),
            ("validate", stage_validate),
            ("protocol", stage_protocol),
            ("references", stage_references),
            ("finalize", stage_finalize),
        ):
            function(ctx)
    except StageError as exc:
        print(f"ERROR [{exc.stage}]: {exc}", file=sys.stderr, flush=True)
        print(f"{STAGE_MARKER} {exc.stage}", flush=True)
        return exc.exit_code
    except Exception as exc:  # a stage crashed rather than failed cleanly
        print(f"ERROR [{stage}]: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        print(f"{STAGE_MARKER} {stage}", flush=True)
        return 1
    print("========================================")
    print(f"Inference completed: {ctx.run_id}")
    print(f"Predictions: {ctx.run_dir / 'predictions'}")
    print(f"Protocol: {ctx.run_dir / 'protocol.yaml'}")
    print("========================================", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
