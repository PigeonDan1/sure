#!/usr/bin/env python3
"""Run model inference for one /sure_infer run inside the approved runtime.

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
  SURE_EVAL_INPUT_RESOLVED, SURE_EVAL_FROM_STAGE, HARNESS_PYTHON_BIN,
  MODEL_PYTHON, SURE_EVAL_REQUIRE_NONEMPTY_PREDICTIONS
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import signal
import sys
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "runtime" / "evaluation" / "task_registry.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from sure.runtime.evaluation.task_registry import normalize_task, task_profile
from dataset_identity import projection_stems
from runtime_roles import same_runtime_executable

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
RESUMABLE_STAGES = frozenset({"validate", "protocol", "references", "finalize"})
PROTOCOL_IDS = ("standard_system", "strict_core")
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
    require_nonempty_predictions: bool
    child_env: dict[str, str]
    tool_name: str = ""
    config_path: Path | None = None
    datasets_root: Path | None = None
    datasets: list[str] = field(default_factory=list)
    languages: dict[str, str] = field(default_factory=dict)
    num_workers: int = 1
    gpu_devices: list[str] = field(default_factory=list)
    worker_selection: str = "explicit"
    worker_plans: dict[str, dict[str, Any]] = field(default_factory=dict)


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
    if ctx.num_workers > 1 and stage == "smoke":
        from parallel_predictions import _stop_group

        def interrupted(signum: int, _frame: Any) -> None:
            raise InterruptedError(f"Smoke interrupted by signal {signum}")

        handlers = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM)}
        process = None
        try:
            process = subprocess.Popen(command, cwd=str(ctx.harness_root), env=ctx.child_env, start_new_session=True)
            code = process.wait()
        finally:
            if process is not None:
                _stop_group(process)
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
    else:
        code = subprocess.run(command, cwd=str(ctx.harness_root), env=ctx.child_env, check=False).returncode
    if code != 0:
        raise StageError(stage, f"{script} exited with code {code}", code)


def _nonempty_prediction_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("\t", 1)
        if len(parts) > 1 and parts[1].strip():
            count += 1
    return count


def _prediction_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())


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
    from_stage = _env("SURE_EVAL_FROM_STAGE")
    if from_stage and from_stage not in RESUMABLE_STAGES:
        raise StageError(
            "guards",
            f"SURE_EVAL_FROM_STAGE must be one of {', '.join(sorted(RESUMABLE_STAGES))}, got {from_stage!r}",
        )

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
    if same_runtime_executable(model_python, sys.executable):
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
    try:
        num_workers = int(_env("SURE_INFER_NUM_WORKERS", "1"))
    except ValueError as exc:
        raise StageError("guards", "num_workers must be an integer between 1 and 8") from exc
    if not 1 <= num_workers <= 8:
        raise StageError("guards", "num_workers must be an integer between 1 and 8")
    require_nonempty_predictions = _env("SURE_EVAL_REQUIRE_NONEMPTY_PREDICTIONS") == "1"

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
    if num_workers > 1 and device not in {"", "auto", "cuda"}:
        raise StageError("guards", "Multi-GPU inference requires device=auto or device=cuda")
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
        require_nonempty_predictions=require_nonempty_predictions,
        child_env=child_env,
        num_workers=num_workers,
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
                task = str(model.get("task") or config.get("task") or config.get("task_type") or "").strip()
                if task:
                    normalized = normalize_task(task)
                    if normalized != "speech_understanding":
                        tool_name = str(task_profile(normalized)["tool_name"])
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
    args = ["--dataset", *ctx.requested_datasets, "--output", str(summary)]
    # Project onto a single, unambiguous dataset task when one exists and the
    # model does not declare a conflicting concrete task (its declared_task is
    # empty, a broad suite like SPEECH_UNDERSTANDING, or agrees with the dataset
    # task). Otherwise the model's declared_task wins: the model's own task
    # description outranks an ambiguous or conflicting dataset projection.
    model_task = ""
    resolved_input = _env("SURE_EVAL_INPUT_RESOLVED")
    if resolved_input:
        try:
            payload = _read_json(Path(resolved_input))
            model = payload.get("model") if isinstance(payload.get("model"), dict) else {}
            declared_task = str(model.get("declared_task") or "").strip()
            dataset_tasks: list[str] = []
            for row in payload.get("datasets") or []:
                if isinstance(row, dict) and row.get("task"):
                    dataset_task = str(row["task"]).strip()
                    if dataset_task and dataset_task not in dataset_tasks:
                        dataset_tasks.append(dataset_task)
            if len(dataset_tasks) == 1:
                dataset_task = dataset_tasks[0]
                if (
                    not declared_task
                    or normalize_task(declared_task) == "speech_understanding"
                    or normalize_task(declared_task) == normalize_task(dataset_task)
                ):
                    model_task = dataset_task
                else:
                    model_task = declared_task
            else:
                model_task = declared_task
        except Exception:
            model_task = ""
    if not model_task:
        config_yaml = ctx.model_dir / "config.yaml"
        if config_yaml.is_file():
            try:
                import yaml

                config = yaml.safe_load(config_yaml.read_text(encoding="utf-8")) or {}
                model = config.get("model") if isinstance(config.get("model"), dict) else {}
                model_task = str(
                    model.get("task") or config.get("task") or config.get("task_type") or ""
                ).strip()
            except Exception:
                model_task = ""
    if model_task:
        args.extend(["--task", model_task])
    _run(ctx, "prepare", "prepare_sure_dataset.py", *args)
    prepared = [
        str(item["dataset"])
        for item in _read_json(summary).get("prepared", [])
        if isinstance(item, dict) and item.get("dataset")
    ]
    if not prepared:
        raise StageError("prepare", "dataset preparation did not return any concrete dataset splits")
    ctx.datasets = prepared
    print(f"Concrete datasets: {' '.join(prepared)}", flush=True)

    if resolved_input:
        resolved = _read_json(Path(resolved_input))
        entries = resolved.get("datasets", [])
        ctx.worker_selection = resolved.get("runtime", {}).get("worker_selection", "explicit")
        # Generation is keyed by the prepared projection stem, not the canonical id.
        stems = projection_stems(entries)
        for row in entries:
            if (isinstance(row, dict) and "planned_workers" in row
                    and stems[str(row["name"])] in prepared):
                stem = stems[str(row["name"])]
                ctx.worker_plans[stem] = {
                    "worker_selection": ctx.worker_selection,
                    "planned_workers": row["planned_workers"],
                    "total_samples": row["num_samples"],
                    "selected_samples": row["selected_samples"],
                }
            if isinstance(row, dict) and row.get("name") and row.get("language"):
                name = str(row["name"])
                ctx.languages[stems.get(name, name)] = str(row["language"])
        if ctx.worker_plans:
            if set(ctx.worker_plans) != set(prepared):
                raise StageError("prepare", "Prepared datasets differ from the Worker plan")
            for row in _read_json(summary).get("prepared", []):
                plan = ctx.worker_plans[str(row["dataset"])]
                with Path(row["jsonl_path"]).open(encoding="utf-8") as handle:
                    total = sum(1 for line in handle if line.strip())
                selected = min(total, ctx.max_samples) if ctx.max_samples > 0 else total
                if total != plan["total_samples"] or selected != plan["selected_samples"]:
                    raise StageError("prepare", "Dataset sample count changed after Worker planning")
                if not 1 <= plan["planned_workers"] <= ctx.num_workers:
                    raise StageError("prepare", "Dataset Workers exceed the task allocation")
    fallback_language = _env("LANGUAGE")
    if fallback_language:
        for dataset in prepared:
            ctx.languages.setdefault(dataset, fallback_language)

def stage_materialize(ctx: Ctx) -> None:
    if ctx.num_workers > 1:
        # Only the parallel controller publishes formal prediction files.
        return
    predictions_dir = ctx.run_dir / "predictions"
    pending = [
        dataset for dataset in ctx.datasets
        if ctx.no_resume
        or not (predictions_dir / f"{dataset}.txt").is_file()
        or not (predictions_dir / f"{dataset}.jsonl").is_file()
    ]
    if not pending:
        return
    _run(
        ctx,
        "materialize",
        "materialize_predictions_template.py",
        "--dataset",
        *pending,
        "--output-dir",
        str(ctx.run_dir / "predictions"),
        "--manifest-name",
        "manifest.json",
        "--overwrite",
    )


def _generate_args(ctx: Ctx, dataset: str, *, max_samples: int | None, run_dir: Path | None = None) -> list[str]:
    args = [
        "--model-dir",
        str(ctx.model_dir),
        "--dataset",
        dataset,
        "--run-dir",
        str(run_dir or ctx.run_dir),
        "--tool-name",
        ctx.tool_name,
        "--protocol",
        ctx.protocol_id,
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
    if ctx.num_workers > 1:
        from parallel_predictions import discover_gpus

        ctx.gpu_devices = discover_gpus(ctx.num_workers, ctx.child_env)
        if not ctx.no_resume:
            # Recovery validates saved identities and rows in the controller;
            # do not load a diagnostic model when all keys may be complete.
            return
    dataset = ctx.datasets[0]
    samples = ctx.smoke_samples
    if samples is None:
        samples = ctx.max_samples if 0 < ctx.max_samples < DEFAULT_SMOKE_SAMPLES else DEFAULT_SMOKE_SAMPLES
    print(f"Smoke test on {dataset} ({samples} samples)", flush=True)
    smoke_ctx = ctx
    if ctx.num_workers > 1:
        smoke_ctx = replace(ctx, run_dir=ctx.run_dir / "parallel_inference" / "smoke",
                            child_env={**ctx.child_env, "CUDA_VISIBLE_DEVICES": ctx.gpu_devices[0],
                                       "DEVICE": "cuda", "SURE_EVAL_DEVICE_ACTUAL": "cuda"}, device="cuda")
    worker_flags = ["--worker-mode"] if ctx.num_workers > 1 else []
    existing_snapshot = not ctx.no_resume and (ctx.run_dir / "predictions" / f"{dataset}.jsonl").is_file()
    workspace = TemporaryDirectory(prefix="sure-smoke-", dir=ctx.run_dir) if existing_snapshot else nullcontext(smoke_ctx.run_dir)
    with workspace as smoke_dir:
        smoke_root = Path(smoke_dir)
        try:
            _run(
                smoke_ctx, "smoke", "generate_predictions_via_server.py",
                *_generate_args(smoke_ctx, dataset, max_samples=samples, run_dir=smoke_root),
                *worker_flags,
            )
            prediction_path = smoke_root / "predictions" / f"{dataset}.txt"
            valid = _nonempty_prediction_rows(prediction_path)
            generated = _prediction_rows(prediction_path)
            observed = valid if ctx.require_nonempty_predictions else generated
            if observed < 1:
                raise StageError(
                    "smoke",
                    f"smoke test produced no {'non-empty ' if ctx.require_nonempty_predictions else ''}prediction rows in the first {samples} samples of {dataset}; "
                    f"see {smoke_root / 'predictions' / 'logs'}",
                )
        except StageError as exc:
            if existing_snapshot:
                logs_dir = smoke_root / "predictions" / "logs"
                if logs_dir.is_dir():
                    preserved_logs = ctx.run_dir / "predictions" / "logs" / "resume-smoke"
                    shutil.copytree(logs_dir, preserved_logs, dirs_exist_ok=True)
                    raise StageError("smoke", f"{exc}; smoke logs: {preserved_logs}", exc.exit_code) from exc
            raise
        label = "non-empty rows" if ctx.require_nonempty_predictions else "prediction rows"
        print(f"Smoke test passed ({observed} {label}; {valid} non-empty)", flush=True)


def stage_generate(ctx: Ctx) -> None:
    if ctx.num_workers > 1:
        from parallel_predictions import run_dataset

        failed = []
        for dataset in ctx.datasets:
            workers = ctx.worker_plans.get(dataset, {}).get("planned_workers", ctx.num_workers)
            report = run_dataset(replace(ctx, num_workers=workers), dataset)
            if report["status"] != "completed":
                failed.append(dataset)
        if failed:
            raise StageError("generate", f"Incomplete datasets: {', '.join(failed)}; successful Worker rows are preserved")
        return
    for dataset in ctx.datasets:
        print(f"Generating predictions for {dataset}", flush=True)
        try:
            _run(
                ctx,
                "generate",
                "generate_predictions_via_server.py",
                *_generate_args(ctx, dataset, max_samples=ctx.max_samples or None),
            )
        finally:
            if dataset in ctx.worker_plans:
                from parallel_predictions import atomic_json

                path = ctx.run_dir / "prediction_generation_status.json"
                if path.exists():
                    status = _read_json(path)
                    status["worker_selection"] = ctx.worker_selection
                    for row in status.get("datasets", []):
                        if row.get("dataset") == dataset:
                            row.update(ctx.worker_plans[dataset], requested_workers=1, effective_workers=1)
                    atomic_json(path, status)


def stage_validate(ctx: Ctx) -> None:
    args = [
        "--dataset",
        *ctx.datasets,
        "--pred-dir",
        str(ctx.run_dir / "predictions"),
        "--output",
        str(ctx.run_dir / "validation_payload.json"),
    ]
    if ctx.require_nonempty_predictions:
        args.append("--require-nonempty")
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
    _run(ctx, "finalize", "finalize_result_bundle.py", "--run-dir", str(ctx.run_dir), "--published-run-dir", published, "--model-dir", str(ctx.model_dir))


def main() -> int:
    stage = "guards"
    ctx: Ctx | None = None
    completed = False
    try:
        ctx = stage_guards()
        flow = (
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
        )
        from_stage = _env("SURE_EVAL_FROM_STAGE")
        if from_stage:
            start = next(index for index, (name, _) in enumerate(flow) if name == from_stage)
            # These stages provide the dataset list, language map and config
            # needed by validation and protocol/reference finalization. They do
            # not start the model server or load model weights.
            required_prefix = {"tool_name", "config", "prepare"}
            flow = tuple(
                (name, function)
                for index, (name, function) in enumerate(flow)
                if name in required_prefix or index >= start
            )
            print(f"Resuming inference from stage: {from_stage}", flush=True)
        for stage, function in flow:
            function(ctx)
        completed = True
    except StageError as exc:
        print(f"ERROR [{exc.stage}]: {exc}", file=sys.stderr, flush=True)
        print(f"{STAGE_MARKER} {exc.stage}", flush=True)
        return exc.exit_code
    except Exception as exc:  # a stage crashed rather than failed cleanly
        print(f"ERROR [{stage}]: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        print(f"{STAGE_MARKER} {stage}", flush=True)
        return 1
    finally:
        if not completed and ctx is not None and ctx.num_workers > 1:
            from finalize_result_bundle import finalize_bundle

            try:
                finalize_bundle(ctx.run_dir, Path(_env("SURE_EVAL_PUBLISHED_RUN_DIR") or str(ctx.run_dir)))
            except Exception as exc:
                print(f"Failed to localize parallel diagnostics: {exc}", file=sys.stderr, flush=True)
    print("========================================")
    print(f"Inference completed: {ctx.run_id}")
    print(f"Predictions: {ctx.run_dir / 'predictions'}")
    print(f"Protocol: {ctx.run_dir / 'protocol.yaml'}")
    print("========================================", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
