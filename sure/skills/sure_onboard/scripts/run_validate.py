#!/usr/bin/env python3
"""Gate script for the VALIDATE_{IMPORT,LOAD,INFER,CONTRACT} units.

Routes by --kind to the corresponding minimal-validation test. The gate does
not trust a boolean alone: each validation artifact must provide either:

  - run_command: ["python", "-c", "..."] or a shell string; or
  - validate_py: path/to/validate.py plus optional validate_args.

The command is executed by this gate, stdout/stderr are written to log_path,
and only then is the matching *_passed boolean accepted.

Kinds map to the minimal_validation contract (import/load/infer/contract):
    import    -> the model module imports without error
    load      -> the model object instantiates and loads weights
    infer     -> a minimal inference call produces output
    contract  -> the output satisfies model.spec.yaml io_contract

Called by the Sure hook:
    python3 scripts/run_validate.py --kind <import|load|infer|contract> \
        --run-dir <runDir> --produces <abs>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

KIND_TO_PASS_KEY = {
    "import": "import_passed",
    "load": "load_passed",
    "infer": "infer_passed",
    "contract": "contract_passed",
}

ACTUAL_DEVICES = {"cuda", "cpu", "mps"}


def resolve_path(raw: object, base: Path) -> Path | None:
    if not raw:
        return None
    path = Path(str(raw)).expanduser()
    if path.is_absolute():
        return path
    return base / path


def command_from_artifact(data: dict, kind: str, run_dir: Path) -> tuple[list[str] | str | None, bool, Path]:
    cwd_raw = data.get("cwd") or data.get("model_dir")
    cwd = resolve_path(cwd_raw, run_dir) or run_dir

    raw_command = data.get("run_command")
    if isinstance(raw_command, list) and all(isinstance(item, str) for item in raw_command):
        return raw_command, False, cwd
    if isinstance(raw_command, str) and raw_command.strip():
        return raw_command, True, cwd

    validate_py = resolve_path(data.get("validate_py"), cwd)
    if validate_py:
        if not validate_py.exists():
            raise FileNotFoundError(f"validate_py does not exist: {validate_py}")
        validate_args = data.get("validate_args")
        extra_args = validate_args if isinstance(validate_args, list) and all(isinstance(item, str) for item in validate_args) else []
        # Generic validate.py files in the reference repo commonly run the full
        # import/load/infer/contract chain without a --stage flag. If a model
        # supports stage-specific args, the artifact can provide validate_args.
        return [sys.executable, str(validate_py), *extra_args], False, validate_py.parent

    return None, False, cwd


def log_path_for(data: dict, kind: str, run_dir: Path, cwd: Path) -> Path:
    raw = data.get("log_path")
    if raw:
        path = Path(str(raw)).expanduser()
        if path.is_absolute():
            return path
        if str(raw).startswith("artifacts/"):
            return cwd / path
        return run_dir / "artifacts" / path
    return run_dir / "artifacts" / f"{kind}_execution.log"


def env_for(data: dict, validation_device: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    if validation_device in ACTUAL_DEVICES:
        env.setdefault("SURE_DEVICE", validation_device)
        env.setdefault("DEVICE", validation_device)
    raw_env = data.get("env")
    if isinstance(raw_env, dict):
        for key, value in raw_env.items():
            if isinstance(key, str) and isinstance(value, (str, int, float, bool)):
                env[key] = str(value)
    return env


def maybe_use_model_local_python(command: list[str] | str, *, shell: bool, cwd: Path) -> list[str] | str:
    if shell or not isinstance(command, list) or not command:
        return command
    executable = Path(command[0]).name
    python_names = {"python", "python3", "python3.10", "python3.11", "python3.12"}
    if executable not in python_names and command[0] != sys.executable:
        return command
    local_python = cwd / ".venv" / "bin" / "python"
    if local_python.exists():
        return [str(local_python), *command[1:]]
    return command


def repo_root_for(run_dir: Path) -> Path:
    resolved = run_dir.expanduser().resolve()
    if resolved.parent.name == "runs" and resolved.parent.parent.name == ".sure":
        return resolved.parent.parent.parent
    return Path.cwd().resolve()


def normalize_repo_relative_text(value: str, repo_root: Path) -> str:
    replacement = str(repo_root / "sure" / "models") + "/"
    return re.sub(r"(?<![A-Za-z0-9_./-])sure/models/", replacement, value)


def normalize_repo_relative_command(command: list[str] | str, repo_root: Path) -> list[str] | str:
    if isinstance(command, str):
        return normalize_repo_relative_text(command, repo_root)
    return [normalize_repo_relative_text(part, repo_root) for part in command]


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a JSON object.")
    return data


def validation_device_for(run_dir: Path) -> str | None:
    env_compat_path = run_dir / "artifacts" / "env_compat_result.json"
    if env_compat_path.exists():
        try:
            compat = read_json(env_compat_path)
        except ValueError:
            compat = {}
        device = str(compat.get("device") or "").lower()
        if device in ACTUAL_DEVICES:
            return device

    resolved_path = run_dir / "artifacts" / "model_input_resolved.json"
    if resolved_path.exists():
        try:
            resolved = read_json(resolved_path)
        except ValueError:
            resolved = {}
        requested = str(resolved.get("device") or "").lower()
        if requested in ACTUAL_DEVICES:
            return requested
    return None


def declared_device_in_artifact(data: dict) -> str | None:
    device = data.get("device")
    if isinstance(device, str) and device.strip():
        return device.strip().lower()
    raw_env = data.get("env")
    if isinstance(raw_env, dict):
        for key in ("DEVICE", "SURE_DEVICE"):
            value = raw_env.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
    return None


def validate_artifact_device(data: dict, target: str | None) -> str | None:
    if target not in ACTUAL_DEVICES:
        return None
    declared = declared_device_in_artifact(data)
    if declared in ACTUAL_DEVICES and declared != target:
        return (
            f"validation artifact requests device={declared!r}, but "
            f"env_compat_result selected device={target!r}. Keep validation "
            "on the same device, or rerun VALIDATE_ENV_COMPAT with documented "
            "CPU fallback evidence first."
        )
    return None


def first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def sample_output_path_for(data: dict, run_dir: Path) -> Path | None:
    model_dir = resolve_path(data.get("model_dir"), run_dir)
    raw = data.get("sample_output_path")
    candidates: list[Path] = []
    if raw:
        path = Path(str(raw)).expanduser()
        if path.is_absolute():
            candidates.append(path)
        else:
            candidates.append(run_dir / "artifacts" / path)
            candidates.append(run_dir / path)
            if model_dir:
                candidates.append(model_dir / path)
                if str(raw).startswith("artifacts/"):
                    candidates.append(model_dir / path)
    candidates.append(run_dir / "artifacts" / "sample_output.json")
    if model_dir:
        candidates.append(model_dir / "artifacts" / "sample_output.json")
    return first_existing(candidates)


def load_sample_output(data: dict, run_dir: Path) -> tuple[Path, dict]:
    path = sample_output_path_for(data, run_dir)
    if not path:
        raise FileNotFoundError(
            "sample_output.json is required for VALIDATE_INFER/VALIDATE_CONTRACT. "
            "Write it under <run_dir>/artifacts/sample_output.json or declare sample_output_path."
        )
    return path, read_json(path)


def io_contract_from(data: dict, run_dir: Path) -> dict:
    contract = data.get("io_contract")
    if isinstance(contract, dict):
        return contract
    resolved_path = run_dir / "artifacts" / "model_input_resolved.json"
    if resolved_path.exists():
        resolved = read_json(resolved_path)
        normalized = resolved.get("normalized_model_input")
        if isinstance(normalized, dict) and isinstance(normalized.get("io_contract"), dict):
            return normalized["io_contract"]
    raise ValueError(
        "io_contract is required for VALIDATE_CONTRACT. "
        "Provide it in contract_result.json or in model_input_resolved.normalized_model_input.io_contract."
    )


def is_nonempty(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return True


def string_list(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def validate_output_contract(sample_output: dict, contract: dict) -> list[str]:
    violations: list[str] = []
    required_fields = string_list(contract.get("required_fields"))
    nonempty_fields = string_list(contract.get("nonempty_fields"))
    primary_field = contract.get("primary_field")
    if isinstance(primary_field, str) and primary_field and primary_field not in required_fields:
        required_fields.append(primary_field)
    if isinstance(primary_field, str) and primary_field and primary_field not in nonempty_fields:
        nonempty_fields.append(primary_field)

    for field in required_fields:
        if field not in sample_output:
            violations.append(f"required field missing: {field}")
    for field in nonempty_fields:
        if field in sample_output and not is_nonempty(sample_output.get(field)):
            violations.append(f"field must be nonempty: {field}")

    output_type = contract.get("output_type")
    if output_type == "audio":
        if not any(field in sample_output for field in ("audio_path", "wavs", "sample_rate", "wavs_summary")):
            violations.append("audio output requires audio_path, wavs, wavs_summary, or sample_rate evidence")
    if output_type in {"json", "text"} and not required_fields:
        violations.append("json/text output contract must declare required_fields or primary_field")

    json_serializable = contract.get("json_serializable")
    if json_serializable is True:
        try:
            json.dumps(sample_output)
        except TypeError as exc:
            violations.append(f"sample_output is not JSON serializable: {exc}")
    return violations


def run_validation_command(
    data: dict,
    kind: str,
    run_dir: Path,
    validation_device: str | None,
) -> tuple[int, float, Path, str, Path]:
    command, shell, cwd = command_from_artifact(data, kind, run_dir)
    if command is None:
        raise ValueError(
            "validation artifacts must include run_command or validate_py so "
            "the gate can execute the import/load/infer/contract check for real."
        )
    if not cwd.exists():
        raise FileNotFoundError(f"validation cwd does not exist: {cwd}")
    repo_root = repo_root_for(run_dir)
    command = maybe_use_model_local_python(command, shell=shell, cwd=cwd)
    command = normalize_repo_relative_command(command, repo_root)

    log_path = log_path_for(data, kind, run_dir, cwd)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    timeout_raw = data.get("timeout_seconds")
    timeout = float(timeout_raw) if isinstance(timeout_raw, (int, float)) and timeout_raw > 0 else 1800.0
    started = time.time()
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            env=env_for(data, validation_device),
            capture_output=True,
            text=True,
            shell=shell,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = time.time() - started
        rendered = command if isinstance(command, str) else " ".join(shlex.quote(part) for part in command)
        log_path.write_text(
            f"$ {rendered}\n\nTIMEOUT after {elapsed:.3f}s\n\nSTDOUT:\n{exc.stdout or ''}\n\nSTDERR:\n{exc.stderr or ''}\n",
            encoding="utf-8",
        )
        return 124, elapsed, log_path, f"validation command timed out after {elapsed:.3f}s", cwd

    elapsed = time.time() - started
    rendered = command if isinstance(command, str) else " ".join(shlex.quote(part) for part in command)
    log_path.write_text(
        f"$ {rendered}\n"
        f"cwd={cwd}\n"
        f"exit_code={proc.returncode}\n"
        f"duration_seconds={elapsed:.3f}\n\n"
        f"STDOUT:\n{proc.stdout or ''}\n\nSTDERR:\n{proc.stderr or ''}\n",
        encoding="utf-8",
    )
    detail = (proc.stderr or proc.stdout or "").strip()
    return proc.returncode, elapsed, log_path, detail, cwd


def generated_result_candidates(data: dict, kind: str, run_dir: Path, cwd: Path, produces: Path) -> list[Path]:
    filename = f"{kind}_result.json"
    candidates: list[Path] = []
    model_dir = resolve_path(data.get("model_dir") or data.get("wrapper_path"), run_dir)
    if model_dir and model_dir.is_file():
        model_dir = model_dir.parent
    if model_dir:
        candidates.append(model_dir / "artifacts" / filename)
    candidates.append(cwd / "artifacts" / filename)
    validate_py = resolve_path(data.get("validate_py"), cwd)
    if validate_py:
        candidates.append(validate_py.parent / "artifacts" / filename)
    candidates.append(run_dir / "artifacts" / filename)
    candidates.append(produces)

    seen: set[Path] = set()
    ordered: list[Path] = []
    for candidate in candidates:
        try:
            key = candidate.expanduser().resolve()
        except FileNotFoundError:
            key = candidate.expanduser().absolute()
        if key not in seen:
            seen.add(key)
            ordered.append(candidate)
    return ordered


def snapshot_key(path: Path) -> str:
    return str(path.expanduser().resolve(strict=False))


def snapshot_generated_results(
    data: dict,
    kind: str,
    run_dir: Path,
    cwd: Path,
    produces: Path,
) -> dict[str, tuple[int, bytes]]:
    snapshots: dict[str, tuple[int, bytes]] = {}
    for candidate in generated_result_candidates(data, kind, run_dir, cwd, produces):
        if not candidate.exists():
            continue
        try:
            stat = candidate.stat()
            snapshots[snapshot_key(candidate)] = (stat.st_mtime_ns, candidate.read_bytes())
        except OSError:
            continue
    return snapshots


def first_generated_result(
    data: dict,
    kind: str,
    run_dir: Path,
    cwd: Path,
    produces: Path,
    *,
    before: dict[str, tuple[int, bytes]],
) -> tuple[Path, dict] | None:
    for candidate in generated_result_candidates(data, kind, run_dir, cwd, produces):
        if not candidate.exists():
            continue
        # Model-local artifact directories may contain older adopted validation
        # results from a reference workspace. They are useful only when the
        # command executed by this gate refreshed them; otherwise they can
        # incorrectly override the current run artifact.
        try:
            stat = candidate.stat()
            current = candidate.read_bytes()
            previous = before.get(snapshot_key(candidate))
            if previous is not None and previous == (stat.st_mtime_ns, current):
                continue
        except OSError:
            continue
        try:
            result = json.loads(current.decode("utf-8"))
        except ValueError:
            continue
        except json.JSONDecodeError:
            continue
        if not isinstance(result, dict):
            continue
        return candidate, result
    return None


def normalize_validation_result(
    original: dict,
    generated: dict,
    kind: str,
    pass_key: str,
    elapsed: float,
    log_path: Path,
) -> dict:
    merged = dict(original)
    merged.update(generated)
    merged[pass_key] = bool(generated.get(pass_key))
    merged.setdefault("duration_ms", round(elapsed * 1000, 3))
    merged["error"] = generated.get("error")
    merged["log_path"] = str(log_path)
    merged["executed"] = True

    # The shared validate.py template writes sample_output_path for every stage,
    # but import/load schemas intentionally only describe those stages. Keep the
    # durable run artifacts schema-clean while preserving sample paths for
    # infer/contract, where package_gate and contract checks need them.
    if kind in {"import", "load"}:
        for key in ("sample_output_path", "output_summary", "io_contract", "io_contract_satisfied", "violations"):
            merged.pop(key, None)
    return merged


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", required=True, choices=list(KIND_TO_PASS_KEY))
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    args = parser.parse_args()

    pass_key = KIND_TO_PASS_KEY[args.kind]
    run_dir = Path(args.run_dir)
    path = Path(args.produces)
    if not path.exists():
        print(f"{args.kind}_result.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"{args.kind}_result.json is not valid JSON: {exc}", file=sys.stderr)
        return 1

    try:
        validation_device = validation_device_for(run_dir)
        device_violation = validate_artifact_device(data, validation_device)
        if device_violation:
            print(f"VALIDATE_{args.kind.upper()} gate failed: {device_violation}", file=sys.stderr)
            return 1
        _, _, expected_cwd = command_from_artifact(data, args.kind, run_dir)
        before_results = snapshot_generated_results(data, args.kind, run_dir, expected_cwd, path)
        exit_code, elapsed, log_path, detail, validation_cwd = run_validation_command(
            data,
            args.kind,
            run_dir,
            validation_device,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"VALIDATE_{args.kind.upper()} gate failed before execution: {exc}", file=sys.stderr)
        return 1
    if exit_code != 0:
        print(
            f"VALIDATE_{args.kind.upper()} gate execution failed with exit_code={exit_code}. "
            f"log_path={log_path}\n{detail}",
            file=sys.stderr,
        )
        return 1

    generated = first_generated_result(data, args.kind, run_dir, validation_cwd, path, before=before_results)
    if generated:
        _, generated_data = generated
        data = normalize_validation_result(data, generated_data, args.kind, pass_key, elapsed, log_path)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not data.get(pass_key):
        error = data.get("error")
        detail = f"\n  error: {error}" if error else ""
        print(
            f"VALIDATE_{args.kind.upper()} gate failed: {pass_key} is false.{detail}",
            file=sys.stderr,
        )
        return 1

    # Cross-check: when the kind is load/infer/contract and a wrapper path /
    # model dir is declared, ensure the wrapper file exists.
    if args.kind in ("load", "infer", "contract"):
        model_dir = data.get("model_dir") or data.get("wrapper_path")
        if model_dir:
            candidate = Path(model_dir)
            # model_dir may point to the wrapper dir or a file; check parent.
            targets = [candidate, candidate.parent] if candidate.is_file() else [candidate]
            if not any(p.is_dir() and (p / "model.py").exists() for p in targets):
                print(
                    f"VALIDATE_{args.kind.upper()} gate: declared model dir/wrapper "
                    f"missing model.py: {model_dir}",
                    file=sys.stderr,
                )
                return 1

    if args.kind == "infer":
        try:
            sample_path, sample_output = load_sample_output(data, run_dir)
        except Exception as exc:  # noqa: BLE001
            print(f"VALIDATE_INFER gate failed: {exc}", file=sys.stderr)
            return 1
        if not sample_output:
            print(f"VALIDATE_INFER gate failed: sample_output is empty: {sample_path}", file=sys.stderr)
            return 1

    if args.kind == "contract":
        try:
            sample_path, sample_output = load_sample_output(data, run_dir)
            contract = io_contract_from(data, run_dir)
        except Exception as exc:  # noqa: BLE001
            print(f"VALIDATE_CONTRACT gate failed: {exc}", file=sys.stderr)
            return 1
        violations = validate_output_contract(sample_output, contract)
        if violations:
            print(
                "VALIDATE_CONTRACT gate failed: sample_output does not satisfy io_contract "
                f"({sample_path}): " + "; ".join(violations),
                file=sys.stderr,
            )
            return 1
        if data.get("io_contract_satisfied") is False:
            print(
                "VALIDATE_CONTRACT gate failed: io_contract_satisfied is explicitly false.",
                file=sys.stderr,
            )
            return 1

    print(
        f"run_validate OK: kind={args.kind}, {pass_key}=true, "
        f"executed=true, duration_seconds={elapsed:.3f}, log_path={log_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
