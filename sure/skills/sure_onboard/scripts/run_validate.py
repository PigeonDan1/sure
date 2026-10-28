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

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "runtime" / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
# The protocol alias table has one home. Re-deriving it here would let the gate
# and the infer client drift apart on what "jsonl" is spelled like.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sure_infer" / "scripts"))
from deployment_contract import declared_server_protocol
from model_child_env import model_child_env
from server_protocol import JSONL_SERVER_PROTOCOL
from sure.runtime.evaluation.task_registry import normalize_task
from sure.runtime.model.device_dtype import dtype_from_config, normalize_device
from sure.runtime.model_server.tool_contract import tool_contracts
from sure.runtime.uvenv import runtime_python_relative

try:
    import yaml
except ImportError:  # pragma: no cover - harness dependency
    yaml = None

KIND_TO_PASS_KEY = {
    "import": "import_passed",
    "load": "load_passed",
    "infer": "infer_passed",
    "contract": "contract_passed",
}

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


def concrete_device(value: str | None) -> str | None:
    if not value or value.strip().lower() == "auto":
        return None
    try:
        return normalize_device(value)
    except ValueError:
        return None


def declared_dtype_for(data: dict, base: Path) -> str | None:
    model_dir = resolve_path(data.get("model_dir") or data.get("wrapper_path"), base)
    if model_dir is None:
        resolved = base / "artifacts" / "model_input_resolved.json"
        if resolved.is_file():
            model_dir = resolve_path(read_json(resolved).get("model_dir"), base)
    if model_dir is None or yaml is None:
        return None
    config_path = model_dir / "config.yaml"
    if not config_path.is_file():
        return None
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return dtype_from_config(config) if isinstance(config, dict) else None
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"cannot read declared dtype from {config_path}: {exc}") from exc


def rewrite_container_env(command: list[str] | str, updates: dict[str, str]) -> list[str] | str:
    """Insert env options before the image, without changing container arguments.

    Only direct Docker/Podman run argv is rewritten. Unsupported run options
    fail explicitly rather than guessing where the image starts.
    """
    if not updates or not isinstance(command, list) or not command or Path(command[0]).name not in {"docker", "podman"}:
        return command
    if len(command) < 3 or command[1] != "run":
        raise ValueError("validation container commands must use direct docker/podman run argv")
    boolean_options = {
        "--rm", "--detach", "--interactive", "--tty", "--privileged", "--read-only",
        "--init", "--publish-all", "--help", "--sig-proxy", "--oom-kill-disable",
        "--disable-content-trust", "--no-healthcheck", "--replace", "-d", "-i", "-t", "-P",
    }
    value_options = {
        "--env", "--env-file", "--entrypoint", "--volume", "--mount", "--workdir",
        "--user", "--name", "--gpus", "--network", "--net", "--ipc", "--pid", "--uts",
        "--shm-size", "--memory", "--memory-swap", "--cpus", "--cpuset-cpus", "--device",
        "--ulimit", "--security-opt", "--cap-add", "--cap-drop", "--runtime", "--platform",
        "--pull", "--publish", "--expose", "--hostname", "--add-host", "--dns", "--label",
        "--log-driver", "--log-opt", "--restart", "--stop-signal", "--stop-timeout",
        "--cidfile", "--userns", "--group-add", "--tmpfs", "--volume-from", "--volumes-from",
        "-e", "-v", "-w", "-u", "-p", "-h", "-l", "-a", "--attach",
    }
    result = list(command)
    seen: set[str] = set()
    index = 2
    while index < len(result):
        token = result[index]
        if token == "--" or not token.startswith("-"):
            break
        value_index: int | None = None
        value: str | None = None
        option = token.split("=", 1)[0]
        if option in value_options:
            if "=" in token:
                value_index, value = index, token.split("=", 1)[1]
            elif index + 1 < len(result):
                value_index, value = index + 1, result[index + 1]
            else:
                raise ValueError(f"container option {token} requires a value")
        elif token.startswith("-e") and not token.startswith("--"):
            option, value_index, value = "-e", index, token[2:]
        elif option in boolean_options or (not token.startswith("--") and set(token[1:]) <= set("dit")):
            index += 1
            continue
        else:
            raise ValueError(f"unsupported validation container option {token}; use explicit supported run options")
        if option in {"-e", "--env"} and value is not None:
            key = value.split("=", 1)[0]
            if key in updates:
                replacement = f"{key}={updates[key]}"
                result[value_index] = f"--env={replacement}" if value_index == index else replacement
                seen.add(key)
        index += 2 if value_index == index + 1 else 1
    if index >= len(result) or (result[index] == "--" and index + 1 >= len(result)):
        raise ValueError("validation container command must specify an image")
    flags: list[str] = []
    for key, value in updates.items():
        if key not in seen:
            flags.extend(["-e", f"{key}={value}"])
    result[2:2] = flags
    return result


def env_for(data: dict, validation_device: str | None = None, dtype: str | None = None) -> dict[str, str]:
    env = model_child_env()
    raw_env = data.get("env")
    if isinstance(raw_env, dict):
        for key, value in raw_env.items():
            if isinstance(key, str) and isinstance(value, (str, int, float, bool)):
                env[key] = str(value)
    if concrete_device(validation_device) is not None:
        # Keep the legacy bare `cuda` spelling when env_compat_result does not
        # provide concrete-device evidence. A resolved_device such as cuda:0
        # is passed through by validation_device_for below.
        target = validation_device
        env["SURE_DEVICE"] = target
        env["DEVICE"] = target
        env["SURE_EVAL_DEVICE_ACTUAL"] = target
    if dtype:
        env["SURE_EVAL_APPROVED_DTYPE"] = dtype
        env["SURE_MODEL_DTYPE"] = dtype
    # Artifact-declared variables may include model import paths, but they must
    # not be able to reintroduce Harness interpreter state after sanitization.
    return model_child_env(env)


def maybe_use_model_local_python(command: list[str] | str, *, shell: bool, cwd: Path) -> list[str] | str:
    if shell or not isinstance(command, list) or not command:
        return command
    executable = Path(command[0]).name
    python_names = {"python", "python3", "python3.10", "python3.11", "python3.12"}
    if executable not in python_names and command[0] != sys.executable:
        return command
    # The model venv spells its interpreter for the platform that created it.
    local_python = cwd / ".venv" / runtime_python_relative()
    if local_python.exists():
        return [str(local_python), *command[1:]]
    return command


def repo_root_for(run_dir: Path) -> Path:
    resolved = run_dir.expanduser().resolve()
    if resolved.parent.name == "runs" and resolved.parent.parent.name == ".sure":
        return resolved.parent.parent.parent
    return Path.cwd().resolve()


def normalize_repo_relative_text(value: str, repo_root: Path) -> str:
    # The substitution lands in a command the model declared, so spell the
    # path in POSIX: a backslash there is an escape (\b is a backspace), not a
    # separator. Windows accepts forward slashes wherever it accepts them.
    replacement = (repo_root / "sure" / "models").as_posix() + "/"
    # Pass a function, not the string, as the replacement. re.sub treats
    # backslashes in a *string* replacement as escapes (\s, \1, ...), which
    # raises on Windows where replacement contains path backslashes. A
    # function's return value is inserted literally, with no escape parsing.
    return re.sub(r"(?<![A-Za-z0-9_./-])sure/models/", lambda _match: replacement, value)


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
        device = str(compat.get("resolved_device") or compat.get("device") or "").lower()
        if concrete_device(device) is not None:
            return device

    resolved_path = run_dir / "artifacts" / "model_input_resolved.json"
    if resolved_path.exists():
        try:
            resolved = read_json(resolved_path)
        except ValueError:
            resolved = {}
        requested = str(resolved.get("device") or "").lower()
        if concrete_device(requested) is not None:
            return requested
    return None


def validate_artifact_device(data: dict, target: str | None) -> str | None:
    if concrete_device(target) is None:
        return None
    declarations = [data.get("device")]
    raw_env = data.get("env")
    if isinstance(raw_env, dict):
        declarations.extend(raw_env.get(key) for key in ("DEVICE", "SURE_DEVICE", "SURE_EVAL_DEVICE_ACTUAL"))
    for declared in declarations:
        if not declared:
            continue
        if concrete_device(str(declared)) == concrete_device(target):
            continue
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
    allow_empty_prediction = contract.get("allow_empty_prediction") is True
    if isinstance(primary_field, str) and primary_field and primary_field not in required_fields:
        required_fields.append(primary_field)
    if allow_empty_prediction and isinstance(primary_field, str):
        nonempty_fields = [field for field in nonempty_fields if field != primary_field]
    elif isinstance(primary_field, str) and primary_field and primary_field not in nonempty_fields:
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
    dtype = declared_dtype_for(data, run_dir)
    updates: dict[str, str] = {}
    target = validation_device if concrete_device(validation_device) else None
    if target:
        updates.update({"DEVICE": target, "SURE_DEVICE": target, "SURE_EVAL_DEVICE_ACTUAL": target})
    if dtype:
        updates.update({"SURE_EVAL_APPROVED_DTYPE": dtype, "SURE_MODEL_DTYPE": dtype})
    command = rewrite_container_env(command, updates)

    log_path = log_path_for(data, kind, run_dir, cwd)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    timeout_raw = data.get("timeout_seconds")
    timeout = float(timeout_raw) if isinstance(timeout_raw, (int, float)) and timeout_raw > 0 else 1800.0
    started = time.time()
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            env=env_for(data, validation_device, dtype),
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


def infer_model_dir(data: dict, run_dir: Path) -> Path:
    """The model directory this run validates, from the artifact or the fixture."""
    for raw in (data.get("model_dir"), data.get("wrapper_path")):
        candidate = resolve_path(raw, run_dir)
        if candidate is not None and candidate.is_dir():
            return candidate
    # The hook refuses VALIDATE_INFER without a fixture manifest, so it is a
    # safe second source for the model dir when the artifact does not name one.
    manifest = run_dir / "artifacts" / "fixture_manifest.json"
    if manifest.is_file():
        candidate = resolve_path(read_json(manifest).get("model_dir"), run_dir)
        if candidate is not None and candidate.is_dir():
            return candidate
    raise ValueError("validate_infer cannot locate the model dir whose declared server it must smoke")


def load_model_config(model_dir: Path) -> dict:
    path = model_dir / "config.yaml"
    if yaml is None or not path.is_file():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def smoke_arguments(run_dir: Path, input_schema: dict) -> dict:
    """The tool arguments the smoke drives with, taken from the prepared fixture.

    The fixture gate guarantees every sample carries an audio field, so that
    audio is the argument unless the tool declares an input the fixture also
    provides under that name. A tool whose input is not a fixture audio file (a
    TTS text prompt, say) is driven by an authored
    `<run_dir>/artifacts/server_smoke_input.json`; the evidence records whatever
    was actually sent, so a reviewer can see it either way.

    A required input the fixture cannot fill is refused rather than dropped: the
    call would reach the server missing a field its own contract calls mandatory,
    and a lenient answer would carry the gate on a call that proved nothing.
    """
    authored = run_dir / "artifacts" / "server_smoke_input.json"
    if authored.is_file():
        body = read_json(authored)
        arguments = body.get("arguments") if isinstance(body.get("arguments"), dict) else body
        if isinstance(arguments, dict) and arguments:
            return arguments
        raise ValueError(f"{authored} must hold a non-empty arguments object")
    manifest = run_dir / "artifacts" / "fixture_manifest.json"
    if not manifest.is_file():
        raise ValueError("driving the declared server needs fixture_manifest.json; run prepare_fixture first")
    samples = read_json(manifest).get("samples")
    sample = samples[0] if isinstance(samples, list) and samples and isinstance(samples[0], dict) else {}
    roles = sample.get("audio_roles") if isinstance(sample.get("audio_roles"), dict) else {}
    properties = input_schema.get("properties") if isinstance(input_schema.get("properties"), dict) else {}
    required = [name for name in input_schema.get("required", []) if isinstance(name, str)] if isinstance(input_schema.get("required"), list) else []

    def supplied(name: str) -> str:
        """The fixture value for a field name, or "" when it has none."""
        value = roles.get(name)
        if isinstance(value, str) and value:
            return value
        # A sample always carries its own audio; a fixture written by hand may
        # have no `audio_roles` at all, and `audio_path` is still its input.
        fallback = sample.get("audio_path") if name == "audio_path" else None
        return fallback if isinstance(fallback, str) and fallback else ""

    field = next((name for name in [*required, *properties] if supplied(name)), None)
    value = supplied(field or "audio_path")
    if not value:
        raise ValueError(
            f"the fixture records no audio input for the declared server smoke; "
            f"author {authored} with the arguments this tool takes"
        )
    # A required input the fixture does not fill is not an argument this tool can
    # be driven with: the call would reach the server missing a field its own
    # contract says is mandatory, and a lenient answer would carry the gate on a
    # call that proved nothing. The escape hatch is the same one above.
    unedriven = [name for name in required if name != field and not supplied(name)]
    if unedriven:
        raise ValueError(
            f"the fixture supplies no value for the required tool input(s) "
            f"{', '.join(unedriven)}; author {authored} with the arguments this tool takes"
        )
    return {field or "audio_path": value}


def declared_server_env(server: dict) -> dict[str, str]:
    """The env the infer client merges into the child before launching it."""
    raw = server.get("env")
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in raw.items()
        if isinstance(value, (str, int, float, bool))
    }


def declared_working_dir(server: dict, model_dir: Path) -> Path:
    """The cwd the server must start in, kept inside the model bundle.

    Same containment rule the infer client applies. Starting the smoke anywhere
    else exercises a process the model never runs, and a wrapper that resolves
    its weights relative to `.` then fails for a reason inference would not hit.
    """
    raw = server.get("working_dir")
    if not isinstance(raw, str) or not raw.strip():
        return model_dir
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = model_dir / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(model_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"config.yaml server.working_dir escapes the model bundle: {raw!r}") from exc
    if not candidate.is_dir():
        raise ValueError(f"config.yaml server.working_dir does not exist: {candidate}")
    return candidate


def server_declaration(model_dir: Path, run_dir: Path) -> dict | None:
    """What VALIDATE_INFER must drive, or None when the harness owns the wrapper.

    A `server:` block without `command` is served by the harness fallback
    wrapper, which is MCP by construction, so there is nothing model-local to
    start. A declared `command` must also declare the framing it speaks --
    `declared_server_protocol` refuses the silent default -- and must declare a
    tool a client can call: `tool_contracts` refuses a `tools[]` entry with no
    `input_schema.properties` here, rather than letting inference discover it.
    The response the smoke gets is checked against the framing resolved here,
    from the same resolver the infer client uses.
    """
    config = load_model_config(model_dir)
    declared = declared_server_protocol(config, model_dir)
    if declared is None:
        return None
    # A declared command owns a callable tool contract, and the infer client
    # filters the arguments it sends against `input_schema.properties`. A config
    # that declares `tools: [{name: vad}]` and stops there describes a tool that
    # cannot be called, and the client only says so after the bundle was
    # packaged and approved. Refusing it here is what the gate is for;
    # `tool_contracts` reads the same fields the client reads.
    contract = tool_contracts(config, source=f"{model_dir}/config.yaml")[0]
    server = config.get("server") if isinstance(config.get("server"), dict) else {}
    model = config.get("model") if isinstance(config.get("model"), dict) else {}
    classification_path = run_dir / "artifacts" / "classification.json"
    classification = read_json(classification_path) if classification_path.is_file() else {}
    task = normalize_task(
        model.get("task") or config.get("task") or config.get("task_type")
        or classification.get("task_type") or "ASR"
    ).upper()
    command = [str(item) for item in server.get("command", [])]
    # `command[0]` is the shorthand the model was built with (`.venv/bin/python`),
    # not a path that resolves here: the infer client and the runtime inventory
    # both swap it for the model's own interpreter before launching. A smoke that
    # starts it verbatim judges a process inference would never run -- on Windows
    # a `.venv/bin/python` that this platform's venv never made, on Linux a PATH
    # interpreter without the model's dependencies.
    command = [str(item) for item in maybe_use_model_local_python(command, shell=False, cwd=model_dir)]
    startup = server.get("startup_timeout_sec")
    startup = float(startup) if isinstance(startup, (int, float)) and startup > 0 else 0.0
    return {
        "command": command,
        "protocol": "jsonl" if declared == JSONL_SERVER_PROTOCOL else "mcp",
        "tool": contract.name,
        "task": task,
        # The smoke compares what the server advertises on `tools/list` against
        # this, so a config and a server that disagree are caught before sealing.
        "input_schema": contract.input_schema,
        "arguments": smoke_arguments(run_dir, contract.input_schema),
        # The infer client launches this server with the model's declared env
        # and working directory. The smoke has to reproduce both, or a wrapper
        # that reads MODEL_PATH out of server.env is judged broken by a gate
        # that never gave it one.
        "env": declared_server_env(server),
        "cwd": declared_working_dir(server, model_dir),
        # The smoke's own deadline covers start, the tool call and shutdown, so
        # it gets the model's declared startup budget plus room to answer.
        "timeout": startup + 300.0,
    }


def drive_declared_server(data: dict, run_dir: Path, model_dir: Path, validation_device: str | None) -> tuple[str, str]:
    """Run the protocol smoke for this model. Returns (status, detail).

    Returns ("skipped", reason) when the harness fallback wrapper serves the
    model, ("passed", evidence path) when the declared server answered in the
    declared framing, and raises ValueError with the evidence's own account
    when it did not.
    """
    declaration = server_declaration(model_dir, run_dir)
    if declaration is None:
        return "skipped", "config.yaml omits server.command: the harness fallback wrapper serves MCP"
    produces = run_dir / "artifacts" / "server_smoke.json"
    timeout = float(declaration["timeout"])
    # The model's declared env, then whatever the artifact declared over it: the
    # gate's own statement outranks the model's, the way a CLI override outranks
    # config. The same dict goes to the smoke as `--env-json`, because the runner
    # builds the server's environment from the sanitized child env and would
    # otherwise strip a declared `PYTHONPATH`/`PYTHONHOME` back out -- which the
    # infer client, starting from the same sanitized env, does not do.
    child_env = {
        **declaration["env"],
        **(data.get("env") if isinstance(data.get("env"), dict) else {}),
    }
    dtype = declared_dtype_for({"model_dir": str(model_dir)}, run_dir)
    updates: dict[str, str] = {}
    if concrete_device(validation_device):
        updates.update({key: validation_device for key in ("DEVICE", "SURE_DEVICE", "SURE_EVAL_DEVICE_ACTUAL")})
    if dtype:
        updates.update({"SURE_MODEL_DTYPE": dtype, "SURE_EVAL_APPROVED_DTYPE": dtype})
    child_env.update(updates)
    declaration["command"] = rewrite_container_env(declaration["command"], updates)
    invocation = {
        **data,
        "cwd": str(declaration["cwd"]),
        "env": child_env,
        "log_path": str(run_dir / "artifacts" / "server_smoke.log"),
        "timeout_seconds": timeout + 60.0,
        "run_command": [
            sys.executable,
            str(Path(__file__).resolve().parent / "run_server_smoke.py"),
            "--server-command", json.dumps(declaration["command"]),
            "--protocol", declaration["protocol"],
            "--tool", declaration["tool"],
            "--task", declaration["task"],
            "--input-schema-json", json.dumps(declaration["input_schema"]),
            "--arguments-json", json.dumps(declaration["arguments"]),
            "--env-json", json.dumps(child_env),
            "--cwd", str(declaration["cwd"]),
            "--timeout", f"{timeout:.0f}",
            "--produces", str(produces),
            "--server-stderr-log", str(run_dir / "artifacts" / "server_smoke_stderr.log"),
        ],
    }
    exit_code, _, log_path, detail, _ = run_validation_command(invocation, "infer", run_dir, validation_device)
    if exit_code != 0:
        raise ValueError(
            f"the declared server did not answer as {declaration['protocol']} (exit {exit_code}); "
            f"log_path={log_path}\n{detail}"
        )
    evidence = read_json(produces)
    if evidence.get("status") != "passed":
        raise ValueError(
            f"server smoke evidence did not pass: {evidence.get('error')} "
            f"(protocol={evidence.get('protocol')}, tool={evidence.get('tool')}, evidence={produces})"
        )
    if evidence.get("protocol") != declaration["protocol"]:
        raise ValueError(
            f"server smoke evidence records protocol={evidence.get('protocol')!r}, "
            f"but {model_dir}/config.yaml declares {declaration['protocol']!r}"
        )
    return "passed", str(produces)


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
        # In-process inference proves the model, not the server. A model that
        # ships server.py is driven here in the framing its config declares, so
        # a protocol mismatch fails at onboarding rather than at inference.
        try:
            smoke_status, smoke_detail = drive_declared_server(
                data, run_dir, infer_model_dir(data, run_dir), validation_device
            )
        except Exception as exc:  # noqa: BLE001
            print(f"VALIDATE_INFER gate failed: {exc}", file=sys.stderr)
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

    if args.kind == "infer":
        smoke_note = f", server_smoke={smoke_status} ({smoke_detail})"
    else:
        smoke_note = ""
    print(
        f"run_validate OK: kind={args.kind}, {pass_key}=true, "
        f"executed=true, duration_seconds={elapsed:.3f}, log_path={log_path}{smoke_note}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
