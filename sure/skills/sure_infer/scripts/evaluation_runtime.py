#!/usr/bin/env python3
"""Resolve and materialize the locked sure-evaluation root runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness_runtime import HarnessRuntimeBindingError, load_harness_runtime
from resolve_evaluation_engine import git_environment, git_repo_root


REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from sure.runtime.uvenv import (  # noqa: E402
    child_environment,
    exclusive_lock,
    probe,
    runtime_python_relative,
    sha256_file,
    sync_command,
    uv_binary,
    venv_command,
)

SPEC_ROOT = REPO_ROOT / "sure" / "runtime" / "evaluation"
CACHE_ROOT = REPO_ROOT / "sure" / ".runtime" / "evaluation"


class EvaluationRuntimeError(RuntimeError):
    pass


class EvaluationIdentityUnavailable(EvaluationRuntimeError):
    """The identity cannot be established here at all, whatever its value is.

    This is the one boundary the host's injected binding exists to cross: no
    git to ask, or no engine checkout to ask about. Deliberately narrower than
    its parent -- "the engine moved" and "the harness binding does not hold
    up" are answers, not the absence of one, and must never fall through to
    the environment. Everything that catches EvaluationRuntimeError still
    catches this.
    """


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationRuntimeError(f"invalid runtime JSON {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise EvaluationRuntimeError(f"runtime JSON root must be an object: {path}")
    return payload


def evaluation_child_environment(parent: dict[str, str] | None = None) -> dict[str, str]:
    """Remove Harness interpreter state before evaluation."""
    return child_environment(parent)


def _engine_commit(engine_root: Path) -> str:
    try:
        if git_repo_root(engine_root) is None:
            return ""
        completed = subprocess.run(
            ["git", "-c", f"safe.directory={engine_root}", "rev-parse", "HEAD"],
            cwd=engine_root,
            capture_output=True,
            text=True,
            check=False,
            env=git_environment(evaluation_child_environment()),
        )
    except OSError as exc:
        # An empty commit means "git looked and found nothing pinned here"; a git
        # that will not run at all is a broken environment and has to say so,
        # otherwise the caller reports it as a commit mismatch against "".
        raise EvaluationIdentityUnavailable(
            f"cannot read the evaluation engine commit: git is unavailable ({exc})"
        ) from exc
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _engine_has_repository(engine_root: Path) -> bool:
    """Whether there is a repository here at all to ask about the commit."""
    try:
        return git_repo_root(engine_root) is not None
    except OSError:
        return False


def _approved_harness_runtime() -> dict[str, Any]:
    """The Harness Runtime this process is running under.

    This check used to be a second, weaker copy: it read the same environment
    but only asked whether the manifest in that directory called itself by the
    exported runtime_id. harness_runtime.load_harness_runtime already asks the
    rest -- that the manifest carries the expected schema, that its lock sha
    agrees with the exported one, and that the interpreter is executable and
    does not escape the runtime root. Nothing here can stop a determined agent
    on the same uid, but one policy in one place costs more to forge than two.
    """
    if not os.environ.get("SURE_HARNESS_RUNTIME_ROOT", "").strip():
        raise EvaluationRuntimeError("approved Harness Runtime binding is required")
    try:
        return load_harness_runtime()
    except HarnessRuntimeBindingError as exc:
        raise EvaluationRuntimeError(f"approved Harness Runtime binding is required: {exc}") from exc


def _engine_pyproject_sha256(pyproject: Path) -> str:
    """Identify the engine's pyproject.toml by its newline-normalised bytes.

    The engine is a separate repository, so the superproject's `* text=auto
    eol=lf` does not reach it: a clone made with git's default core.autocrlf
    on Windows writes it with CRLF, and hashing those bytes could never match
    the digest pinned in runtime.json. Normalising costs the ability to tell
    two files apart by line endings alone, which no lockfile depends on. An
    LF checkout hashes to exactly what it did before. Only a CRLF pair is a
    line ending here; a lone CR is content and stays in the digest.
    """
    return hashlib.sha256(pyproject.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _expected_binding(engine_root: Path) -> dict[str, Any]:
    engine_root = engine_root.expanduser().resolve()
    spec = _load_json(SPEC_ROOT / "runtime.json")
    lock_path = SPEC_ROOT / str(spec.get("lock_file") or "requirements.lock.txt")
    pyproject = engine_root / "pyproject.toml"
    if not lock_path.is_file() or not pyproject.is_file():
        raise EvaluationIdentityUnavailable(
            "evaluation runtime lock or engine pyproject.toml is missing; "
            "run git submodule update --init sure/external/sure-evaluation"
        )
    commit = _engine_commit(engine_root)
    if not commit:
        # No repository here is the same "cannot ask" as a missing git: the
        # container gets the engine without the .git it would need. A
        # repository that is present and still will not say is a different
        # thing -- an answer we did not get -- and must not reach the injected
        # identity, or whoever can make git fail chooses the provenance.
        if _engine_has_repository(engine_root):
            raise EvaluationRuntimeError(
                f"the evaluation engine repository would not report its commit: {engine_root}"
            )
        raise EvaluationIdentityUnavailable(
            f"the evaluation engine checkout is not a repository: {engine_root}"
        )
    if commit != spec.get("engine_commit"):
        raise EvaluationRuntimeError(
            f"evaluation engine commit differs from the locked runtime: expected={spec.get('engine_commit')} actual={commit}"
        )
    pyproject_sha = _engine_pyproject_sha256(pyproject)
    if pyproject_sha != spec.get("engine_pyproject_sha256"):
        raise EvaluationRuntimeError(
            "evaluation engine pyproject.toml differs from the locked runtime "
            "after newline normalisation"
        )

    harness = _approved_harness_runtime()

    lock_sha = sha256_file(lock_path)
    runtime_version = str(spec.get("runtime_version") or "root-v1")
    materialization_version = int(spec.get("materialization_version") or 1)
    python_version = str(spec.get("python") or "3.11")
    runtime_id = (
        f"sure-evaluation-{runtime_version}-m{materialization_version}-"
        f"{commit[:12]}-py{python_version.replace('.', '')}-{lock_sha[:12]}"
    )
    runtime_root = CACHE_ROOT / runtime_id
    return {
        "schema": "sure.evaluation.runtime.binding.v1",
        "runtime_id": runtime_id,
        "runtime_type": "evaluation_python",
        "runtime_version": runtime_version,
        "materialization_version": materialization_version,
        "python": python_version,
        "python_executable": str(runtime_root / runtime_python_relative()),
        "runtime_root": str(runtime_root),
        "manifest_path": str(runtime_root / "runtime-manifest.json"),
        "lock_path": str(lock_path),
        "lock_sha256": lock_sha,
        "engine_root": str(engine_root),
        "engine_commit": commit,
        "engine_pyproject_sha256": pyproject_sha,
        "harness_runtime_id": str(harness["runtime_id"]),
        "harness_runtime_root": str(harness["runtime_root"]),
        "required_imports": list(spec.get("required_imports") or []),
    }


def _verify(binding: dict[str, Any]) -> tuple[bool, str]:
    python = Path(str(binding["python_executable"]))
    manifest_path = Path(str(binding["manifest_path"]))
    if not python.is_file() or not manifest_path.is_file():
        return False, "runtime executable or manifest is missing"
    try:
        manifest = _load_json(manifest_path)
    except EvaluationRuntimeError as exc:
        return False, str(exc)
    for key in (
        "runtime_id",
        "runtime_type",
        "runtime_version",
        "materialization_version",
        "lock_sha256",
        "engine_commit",
        "engine_pyproject_sha256",
        "harness_runtime_id",
    ):
        if manifest.get(key) != binding.get(key):
            return False, f"runtime manifest {key} mismatch"
    code = "\n".join(f"import {name}" for name in binding["required_imports"])
    # The runtime does not name the engine, so the caller supplies it, the way
    # evaluate_predictions._external_env already does for the real evaluation
    # calls. Without this the engine's own package would not import here.
    env = evaluation_child_environment()
    env["PYTHONPATH"] = str(Path(str(binding["engine_root"])) / "src")
    completed = subprocess.run(
        [str(python), "-s", "-c", code],
        cwd=str(binding["engine_root"]),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env=env,
    )
    if completed.returncode != 0:
        return False, (completed.stderr or completed.stdout or "import verification failed").strip()
    return True, "locked Evaluation Runtime imports passed"


def _materialize(binding: dict[str, Any]) -> None:
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(CACHE_ROOT / ".prepare.lock"):
        ok, _ = _verify(binding)
        if ok:
            return
        uv = uv_binary(
            error=EvaluationRuntimeError,
            message="uv is required to prepare the locked Evaluation Runtime",
        )
        runtime_root = Path(str(binding["runtime_root"]))
        staging = Path(tempfile.mkdtemp(prefix=f".{binding['runtime_id']}.tmp-", dir=CACHE_ROOT))
        log_dir = CACHE_ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        log_path = log_dir / f"bootstrap-{stamp}-{os.getpid()}.log"
        try:
            env = evaluation_child_environment()
            env["UV_CACHE_DIR"] = str(CACHE_ROOT / "cache")
            env["UV_LINK_MODE"] = "copy"
            runtime_python = staging / runtime_python_relative()
            commands = [
                venv_command(uv, staging, python=str(binding["python"]), allow_python_downloads=True),
                sync_command(
                    uv,
                    runtime_python,
                    Path(str(binding["lock_path"])),
                    allow_python_downloads=True,
                ),
            ]
            transcript: list[str] = []
            for command in commands:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=900,
                    env=env,
                )
                transcript.append("$ " + " ".join(command) + "\n" + completed.stdout + "\n" + completed.stderr)
                if completed.returncode != 0:
                    log_path.write_text("\n".join(transcript), encoding="utf-8")
                    raise EvaluationRuntimeError(
                        f"Evaluation Runtime dependency install failed; see {log_path}"
                    )
            log_path.write_text("\n".join(transcript), encoding="utf-8")
            # uv fetches whatever 3.11 it can find, so the manifest is the only
            # record of which interpreter this runtime actually got. Provenance
            # only: the binding and _verify's comparison do not carry these.
            identity = probe(runtime_python, error=EvaluationRuntimeError)
            manifest = {
                **binding,
                "runtime_root": str(runtime_root),
                "python_executable": str(runtime_root / runtime_python_relative()),
                "manifest_path": str(runtime_root / "runtime-manifest.json"),
                "materialization": "uv_venv",
                "python_version": identity["python_version"],
                "python_abi": identity["python_abi"],
                "base_python_sha256": identity["base_python_sha256"],
                "prepared_at": datetime.now(timezone.utc).isoformat(),
                "install_log": str(log_path),
                "package_source": "configured uv index (credentials omitted)",
            }
            (staging / "runtime-manifest.json").write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            if runtime_root.exists():
                invalid = CACHE_ROOT / f".{runtime_root.name}.invalid-{stamp}-{os.getpid()}"
                runtime_root.rename(invalid)
            staging.rename(runtime_root)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise


def _attested_binding() -> dict[str, Any] | None:
    """The binding the host resolved before it launched this process, if any.

    container_execution.py resolves the runtime against the engine checkout and
    injects its identity. Resolving it again on the far side of
    the boundary needs git and the engine's .git directory, and the evaluation
    image carries neither: every container run died in [5/5] on "No such file or
    directory: 'git'" after the whole prediction pass had already been paid for.
    The injected identity is the evidence that survives the boundary.
    """
    runtime_id = os.environ.get("SURE_EVALUATION_RUNTIME_ID", "").strip()
    lock_sha = os.environ.get("SURE_EVALUATION_LOCK_SHA256", "").strip()
    manifest_path = os.environ.get("SURE_EVALUATION_RUNTIME_MANIFEST", "").strip()
    if not (runtime_id and lock_sha and manifest_path):
        return None
    manifest = _load_json(Path(manifest_path))
    mismatched = [
        f"{field} environment={expected!r} manifest={manifest.get(field)!r}"
        for field, expected in (("runtime_id", runtime_id), ("lock_sha256", lock_sha))
        if manifest.get(field) != expected
    ]
    if mismatched:
        # Naming only the runtime_id printed two identical ids whenever the lock
        # was the half that drifted, which is the more likely half to drift.
        raise EvaluationRuntimeError(
            "attested Evaluation Runtime does not match its manifest: " + "; ".join(mismatched)
        )
    binding = dict(manifest)
    # The manifest records host paths; a container reaches the same files through
    # its own mounts, which the host translated into these two variables.
    python_executable = os.environ.get("SURE_EVALUATION_PYTHON", "").strip()
    if python_executable:
        binding["python_executable"] = python_executable
    engine_home = os.environ.get("SURE_EVALUATION_HOME", "").strip()
    if engine_home:
        binding["engine_root"] = engine_home
    binding["verification"] = f"attested by the host as {runtime_id}"
    return binding


def ensure_evaluation_runtime(engine_root: Path, *, prepare: bool) -> dict[str, Any]:
    try:
        binding = _expected_binding(engine_root)
    except EvaluationIdentityUnavailable:
        # Only where the identity cannot be computed at all does the injected
        # one stand in: the evaluation image carries neither git nor the
        # engine's .git. The host runs this function too, from a shell the
        # model agent controls, so preferring the environment meant an
        # `export SURE_EVALUATION_RUNTIME_ID=...` before the submit script
        # became the run's recorded provenance. Compute it wherever it can be
        # computed.
        attested = _attested_binding()
        if attested is not None:
            return attested
        raise
    ok, evidence = _verify(binding)
    if not ok and prepare:
        _materialize(binding)
        ok, evidence = _verify(binding)
    if not ok:
        raise EvaluationRuntimeError(f"Evaluation Runtime is not ready: {evidence}")
    manifest = _load_json(Path(str(binding["manifest_path"])))
    return {**binding, "install_log": manifest.get("install_log"), "verification": evidence}


def evaluation_runtime_from_eval_input(
    eval_input: dict[str, Any], *, prepare: bool
) -> dict[str, Any] | None:
    evaluation = eval_input.get("evaluation") if isinstance(eval_input.get("evaluation"), dict) else {}
    if evaluation.get("backend") not in (None, "external"):
        return None
    engine = evaluation.get("engine") if isinstance(evaluation.get("engine"), dict) else {}
    engine_root = str(engine.get("engine_root") or "").strip()
    if not engine_root:
        if not isinstance(eval_input.get("evaluation"), dict):
            return None
        # Returning None here submitted the job anyway and left the container to
        # discover the missing engine in [5/5], after the whole prediction pass.
        raise EvaluationRuntimeError(
            "external evaluation was requested but no evaluation engine was resolved; "
            "point --evaluation-engine-root or SURE_EVALUATION_HOME at a sure-evaluation "
            "checkout, or select a non-external evaluation backend"
        )
    return ensure_evaluation_runtime(Path(engine_root), prepare=prepare)


def _write_readiness(output: str | None, record: dict[str, Any]) -> None:
    if not output:
        return
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-root", required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--output", help="Write the readiness record here, on the failing path too")
    args = parser.parse_args()
    try:
        binding = ensure_evaluation_runtime(Path(args.engine_root), prepare=args.prepare)
    except EvaluationRuntimeError as exc:
        # [2.6/5] used to redirect this program's stdout into the artifact, so
        # a refusal left a zero-byte file and the reason survived only as a
        # traceback on stderr. Only the judged outcome is recorded this way: a
        # crash is still a crash, and still wants its traceback.
        _write_readiness(
            args.output,
            {
                "schema": "sure.evaluation.runtime.readiness.v1",
                "ok": False,
                "status": "blocked",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "engine_root": str(args.engine_root),
                "prepare": bool(args.prepare),
                "error": {"code": "EVALUATION_RUNTIME_NOT_READY", "message": str(exc)},
            },
        )
        # /sure_eval reads stderr for its error text, so the plain reason stays there.
        print(str(exc), file=sys.stderr)
        return 1
    _write_readiness(args.output, binding)
    print(json.dumps(binding, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
