#!/usr/bin/env python3
"""Resolve and materialize the locked sure-evaluation root runtime."""

from __future__ import annotations

import argparse
import fcntl
import grp
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness_runtime import HarnessRuntimeBindingError, load_harness_runtime


REPO_ROOT = Path(__file__).resolve().parents[4]
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _apply_acl(setfacl: str, entries: str, paths: list[Path]) -> None:
    for offset in range(0, len(paths), 256):
        command = [setfacl, "-m", entries, "--", *(str(path) for path in paths[offset : offset + 256])]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "setfacl failed").strip()
            raise OSError(f"cannot make runtime group-collaborative: {detail}")


def _make_group_writable(root: Path, *, recursive: bool = True) -> None:
    """Preserve executable bits and grant the runtime's owning group inherited write access."""
    paths = [root, *root.rglob("*")] if recursive else [root]
    paths = [path for path in paths if not path.is_symlink()]
    # Only an owner may change a file's ACL or mode. The cache is shared and its
    # log directory keeps every bootstrap log anyone has written, so reaching for
    # someone else's file fails with "Operation not permitted" and takes the whole
    # materialization down with it, packages already installed. Whoever wrote that
    # file made it group-collaborative on the way past; there is nothing to add.
    uid = os.getuid()
    paths = [path for path in paths if path.stat().st_uid == uid]
    directories = [path for path in paths if path.is_dir()]
    executables = [
        path
        for path in paths
        if not path.is_dir()
        and stat.S_IMODE(path.stat().st_mode) & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    ]
    regular = [path for path in paths if not path.is_dir() and path not in executables]
    setfacl = shutil.which("setfacl")
    if setfacl:
        group_id = root.stat().st_gid
        try:
            group = grp.getgrgid(group_id).gr_name
        except KeyError:
            group = str(group_id)
        _apply_acl(setfacl, f"g:{group}:rwx,m::rwx", [*directories, *executables])
        _apply_acl(setfacl, f"g:{group}:rw-,m::rw-", regular)
        _apply_acl(setfacl, f"d:g:{group}:rwx,d:m::rwx", directories)
        return

    for path in paths:
        mode = stat.S_IMODE(path.stat().st_mode)
        group_bits = stat.S_IRGRP | stat.S_IWGRP
        if path.is_dir():
            group_bits |= stat.S_IXGRP | stat.S_ISGID
        elif mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            group_bits |= stat.S_IXGRP
        path.chmod(mode | group_bits)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationRuntimeError(f"invalid runtime JSON {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise EvaluationRuntimeError(f"runtime JSON root must be an object: {path}")
    return payload


def evaluation_child_environment(parent: dict[str, str] | None = None) -> dict[str, str]:
    """Remove Harness-only dynamic libraries before launching evaluation tools."""
    env = dict(parent if parent is not None else os.environ)
    harness_root = env.get("SURE_HARNESS_RUNTIME_ROOT", "").strip()
    harness_lib = str(Path(harness_root) / "base" / "lib") if harness_root else ""
    entries = [entry for entry in env.get("LD_LIBRARY_PATH", "").split(":") if entry]
    entries = [entry for entry in entries if entry != harness_lib]
    if entries:
        env["LD_LIBRARY_PATH"] = ":".join(entries)
    else:
        env.pop("LD_LIBRARY_PATH", None)
    return env


def _engine_commit(engine_root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-c", f"safe.directory={engine_root}", "rev-parse", "HEAD"],
            cwd=engine_root,
            capture_output=True,
            text=True,
            check=False,
            env=evaluation_child_environment(),
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
        completed = subprocess.run(
            ["git", "-c", f"safe.directory={engine_root}", "rev-parse", "--git-dir"],
            cwd=engine_root,
            capture_output=True,
            text=True,
            check=False,
            env=evaluation_child_environment(),
        )
    except OSError:
        return False
    return completed.returncode == 0


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


def _expected_binding(engine_root: Path) -> dict[str, Any]:
    engine_root = engine_root.expanduser().resolve()
    spec = _load_json(SPEC_ROOT / "runtime.json")
    lock_path = SPEC_ROOT / str(spec.get("lock_file") or "requirements.lock.txt")
    pyproject = engine_root / "pyproject.toml"
    if not lock_path.is_file() or not pyproject.is_file():
        raise EvaluationIdentityUnavailable("evaluation runtime lock or engine pyproject.toml is missing")
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
    pyproject_sha = _sha256(pyproject)
    if pyproject_sha != spec.get("engine_pyproject_sha256"):
        raise EvaluationRuntimeError("evaluation engine pyproject.toml differs from the locked runtime")

    harness = _approved_harness_runtime()

    lock_sha = _sha256(lock_path)
    runtime_version = str(spec.get("runtime_version") or "root-v1")
    materialization_version = int(spec.get("materialization_version") or 1)
    dynamic_loader = Path(str(spec.get("dynamic_loader") or ""))
    if not dynamic_loader.is_file():
        raise EvaluationRuntimeError(f"evaluation runtime dynamic loader is missing: {dynamic_loader}")
    runtime_id = (
        f"sure-evaluation-{runtime_version}-m{materialization_version}-"
        f"{commit[:12]}-py311-{lock_sha[:12]}"
    )
    runtime_root = CACHE_ROOT / runtime_id
    return {
        "schema": "sure.evaluation.runtime.binding.v1",
        "runtime_id": runtime_id,
        "runtime_type": "evaluation_python",
        "runtime_version": runtime_version,
        "materialization_version": materialization_version,
        "dynamic_loader": str(dynamic_loader),
        "python_executable": str(runtime_root / "bin" / "python"),
        "runtime_root": str(runtime_root),
        "manifest_path": str(runtime_root / "runtime-manifest.json"),
        "site_packages": str(runtime_root / "site-packages"),
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
        "dynamic_loader",
        "lock_sha256",
        "engine_commit",
        "engine_pyproject_sha256",
        "harness_runtime_id",
    ):
        if manifest.get(key) != binding.get(key):
            return False, f"runtime manifest {key} mismatch"
    if python.read_text(encoding="utf-8") != _wrapper(binding):
        return False, "runtime wrapper differs from the materialization contract"
    code = "\n".join(f"import {name}" for name in binding["required_imports"])
    # The wrapper no longer names the engine, so the caller supplies it, the way
    # evaluate_predictions._external_env already does for the real evaluation
    # calls. Without this the engine's own package would not import here.
    env = evaluation_child_environment()
    env["PYTHONPATH"] = str(Path(str(binding["engine_root"])) / "src")
    completed = subprocess.run(
        [str(python), "-s", "-c", code],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env=env,
    )
    if completed.returncode != 0:
        return False, (completed.stderr or completed.stdout or "import verification failed").strip()
    return True, "locked Evaluation Runtime imports passed"


def _wrapper(binding: dict[str, Any]) -> str:
    """The launcher text, free of any path that depends on where it is read from.

    _verify compares this byte for byte, and one cache entry is reached under
    several names: the same storage carries more than one mount path, and the
    Harness Runtime lives in the repository on the host but under /opt inside
    the evaluation image. Baking either in made a sound runtime report
    "wrapper differs from the materialization contract" and killed the run at
    [2.6/5]. The runtime locates itself the way the Harness Runtime launcher
    does; the harness root arrives in the environment every caller already
    sets. Only the loader stays literal: it is a fixed system path the spec
    pins and _expected_binding checks for.
    """
    dynamic_loader = str(binding["dynamic_loader"])
    return f"""#!/usr/bin/env bash
set -euo pipefail
unset PYTHONHOME PYTHONEXECUTABLE
_sure_eval_root="$(cd "$(dirname "${{BASH_SOURCE[0]}}")/.." && pwd)"
_sure_eval_harness="${{SURE_HARNESS_RUNTIME_ROOT:?the approved Harness Runtime root is required}}"
_sure_eval_ld=()
IFS=: read -r -a _sure_eval_parent_ld <<< "${{LD_LIBRARY_PATH:-}}"
for _sure_eval_entry in "${{_sure_eval_parent_ld[@]}}"; do
  if [[ -n "$_sure_eval_entry" && "$_sure_eval_entry" != "$_sure_eval_harness/base/lib" ]]; then
    _sure_eval_ld+=("$_sure_eval_entry")
  fi
done
if ((${{#_sure_eval_ld[@]}})); then
  IFS=:; export LD_LIBRARY_PATH="${{_sure_eval_ld[*]}}"; unset IFS
else
  unset LD_LIBRARY_PATH
fi
export PYTHONNOUSERSITE=1
export PYTHONPATH="$_sure_eval_root/site-packages${{PYTHONPATH:+:$PYTHONPATH}}"
exec {dynamic_loader!r} --library-path "$_sure_eval_harness/base/lib" "$_sure_eval_harness/base/bin/python3.11" "$@"
"""


def _materialize(binding: dict[str, Any]) -> None:
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    _make_group_writable(CACHE_ROOT, recursive=False)
    lock_file = CACHE_ROOT / ".prepare.lock"
    with lock_file.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        ok, _ = _verify(binding)
        if ok:
            return
        uv = shutil.which("uv")
        if not uv:
            raise EvaluationRuntimeError("uv is required to prepare the locked Evaluation Runtime")
        runtime_root = Path(str(binding["runtime_root"]))
        staging = Path(tempfile.mkdtemp(prefix=f".{binding['runtime_id']}.tmp-", dir=CACHE_ROOT))
        log_dir = CACHE_ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        _make_group_writable(log_dir, recursive=False)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        log_path = log_dir / f"bootstrap-{stamp}-{os.getpid()}.log"
        try:
            site_packages = staging / "site-packages"
            site_packages.mkdir(parents=True)
            command = [
                uv,
                "pip",
                "install",
                "--python",
                str(Path(str(binding["harness_runtime_root"])) / "bin" / "python"),
                "--target",
                str(site_packages),
                "--require-hashes",
                "-r",
                str(binding["lock_path"]),
            ]
            completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=600)
            log_path.write_text(
                "$ " + " ".join(command) + "\n" + completed.stdout + "\n" + completed.stderr,
                encoding="utf-8",
            )
            if completed.returncode != 0:
                raise EvaluationRuntimeError(
                    f"Evaluation Runtime dependency install failed; see {log_path}"
                )
            (staging / "bin").mkdir()
            wrapper = staging / "bin" / "python"
            wrapper.write_text(_wrapper(binding), encoding="utf-8")
            wrapper.chmod(0o755)
            manifest = {
                **binding,
                "runtime_root": str(runtime_root),
                "python_executable": str(runtime_root / "bin" / "python"),
                "manifest_path": str(runtime_root / "runtime-manifest.json"),
                "site_packages": str(runtime_root / "site-packages"),
                "prepared_at": datetime.now(timezone.utc).isoformat(),
                "install_log": str(log_path),
                "package_source": "configured uv index (credentials omitted)",
            }
            (staging / "runtime-manifest.json").write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            _make_group_writable(staging)
            _make_group_writable(log_dir)
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
