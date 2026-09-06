#!/usr/bin/env python3
"""Materialize and verify the versioned SURE Harness Python runtime."""

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
from typing import Any, TextIO


SPEC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPEC_DIR.parents[2]
DEFAULT_RUNTIME_ROOT = REPO_ROOT / "sure" / ".runtime" / "harness"
MANIFEST_NAME = "runtime-manifest.json"


class HarnessRuntimeError(RuntimeError):
    """The common Harness Runtime could not be prepared or verified."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessRuntimeError(f"invalid runtime definition {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise HarnessRuntimeError(f"runtime definition must be a JSON object: {path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def _load_spec() -> tuple[dict[str, Any], Path, str, str]:
    spec = _read_json(SPEC_DIR / "runtime.json")
    if spec.get("schema") != "sure.harness.runtime.spec.v1":
        raise HarnessRuntimeError("unsupported Harness Runtime definition schema")
    python_version = str(spec.get("python") or "")
    if not python_version or python_version.count(".") != 1:
        raise HarnessRuntimeError("runtime definition must pin a Python major.minor ABI")
    lock_path = SPEC_DIR / str(spec.get("lock_file") or "")
    if not lock_path.is_file():
        raise HarnessRuntimeError(f"Harness dependency lock is missing: {lock_path}")
    lock_sha256 = _sha256(lock_path)
    harness_version = str(spec.get("harness_version") or "").strip()
    if not harness_version:
        raise HarnessRuntimeError("runtime definition must declare harness_version")
    runtime_id = f"sure-harness-{harness_version}-py{python_version.replace('.', '')}-{lock_sha256[:12]}"
    return spec, lock_path, lock_sha256, runtime_id


def _python_path(runtime_dir: Path) -> Path:
    return runtime_dir / "bin" / "python"


def _manifest_path(runtime_dir: Path) -> Path:
    return runtime_dir / MANIFEST_NAME


def _probe(python: Path, required_imports: list[str]) -> dict[str, Any]:
    modules = ",".join(required_imports)
    code = (
        "import importlib,json,platform,sys,sysconfig;"
        f"mods={modules!r}.split(',') if {modules!r} else [];"
        "[importlib.import_module(name) for name in mods];"
        "print(json.dumps({'executable':sys.executable,'version':platform.python_version(),"
        "'abi':sysconfig.get_config_var('SOABI') or '',"
        "'prefix':sys.prefix,'base_prefix':sys.base_prefix}))"
    )
    completed = subprocess.run(
        [str(python), "-s", "-c", code],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        raise HarnessRuntimeError(f"Harness Runtime import probe failed: {detail}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise HarnessRuntimeError("Harness Runtime import probe returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise HarnessRuntimeError("Harness Runtime import probe returned a non-object")
    return payload


def _verified_contract(
    runtime_dir: Path,
    spec: dict[str, Any],
    lock_sha256: str,
    runtime_id: str,
) -> dict[str, Any]:
    manifest_path = _manifest_path(runtime_dir)
    if not manifest_path.is_file():
        raise HarnessRuntimeError(f"Harness Runtime manifest is missing: {manifest_path}")
    manifest = _read_json(manifest_path)
    if manifest.get("schema") != "sure.harness.runtime.manifest.v1":
        raise HarnessRuntimeError("unsupported Harness Runtime manifest schema")
    expected = {
        "runtime_id": runtime_id,
        "lock_sha256": lock_sha256,
        "harness_version": spec["harness_version"],
        "python_abi": f"cp{str(spec['python']).replace('.', '')}",
        "materialization_version": spec["materialization_version"],
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise HarnessRuntimeError(f"Harness Runtime manifest {key} mismatch")
    required_imports = [str(value) for value in spec.get("required_imports", [])]
    python = _python_path(runtime_dir)
    if not python.is_file() or not os.access(python, os.X_OK):
        raise HarnessRuntimeError(f"Harness Runtime Python is missing or not executable: {python}")
    probe = _probe(python, required_imports)
    if not str(probe.get("version") or "").startswith(f"{spec['python']}."):
        raise HarnessRuntimeError(
            f"Harness Runtime Python ABI mismatch: expected {spec['python']}, got {probe.get('version')}"
        )
    contract = dict(manifest)
    contract.update(
        {
            "status": "ready",
            "python_executable": str(python.resolve()),
            "runtime_root": str(runtime_dir.resolve()),
            "manifest_path": str(manifest_path.resolve()),
            "spec_path": str((SPEC_DIR / "runtime.json").resolve()),
            "lock_path": str((SPEC_DIR / str(spec["lock_file"])).resolve()),
            "probe": probe,
        }
    )
    return contract


def _run_logged(command: list[str], env: dict[str, str], log: TextIO, *, timeout: int) -> None:
    redacted = [part.split("@", 1)[-1] if "://" in part and "@" in part else part for part in command]
    log.write(f"$ {json.dumps(redacted)}\n")
    log.flush()
    completed = subprocess.run(
        command,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise HarnessRuntimeError(f"command failed with exit code {completed.returncode}: {command[0]}")


def _uv_binary() -> str:
    explicit = os.environ.get("SURE_UV_BIN", "").strip()
    candidate = explicit or shutil.which("uv") or ""
    if not candidate or not Path(candidate).is_file():
        raise HarnessRuntimeError(
            "uv is required to prepare the locked Harness Runtime; set SURE_UV_BIN to an existing uv executable"
        )
    return str(Path(candidate).resolve())


def _shared_library_name(libdir: Path, declared: str, python_version: str) -> str:
    """Return the libpython actually present in libdir.

    Statically linked builds leave INSTSONAME empty, so the probe falls back to
    LDLIBRARY, which names an archive those builds do not ship. The usable
    shared object sits in the same directory under its conventional name.
    """
    if declared and (libdir / declared).is_file():
        return declared
    for candidate in (f"libpython{python_version}.so.1.0", f"libpython{python_version}.so"):
        if (libdir / candidate).is_file():
            return candidate
    return declared


def _source_python(python_version: str) -> tuple[Path, dict[str, str]]:
    candidate = shutil.which(f"python{python_version}") or ""
    if not candidate:
        raise HarnessRuntimeError(f"host bootstrap Python {python_version} is not available")
    source = Path(candidate).resolve()
    code = (
        "import json,sysconfig;"
        "print(json.dumps({'stdlib':sysconfig.get_path('stdlib'),"
        "'libdir':sysconfig.get_config_var('LIBDIR') or '',"
        "'ldlibrary':sysconfig.get_config_var('INSTSONAME') or sysconfig.get_config_var('LDLIBRARY') or '',"
        "'platlibdir':sysconfig.get_config_var('platlibdir') or 'lib'}))"
    )
    completed = subprocess.run(
        [str(source), "-I", "-c", code],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise HarnessRuntimeError(f"failed to inspect host bootstrap Python: {completed.stderr.strip()}")
    metadata = json.loads(completed.stdout)
    if not isinstance(metadata, dict) or not all(isinstance(value, str) for value in metadata.values()):
        raise HarnessRuntimeError("host bootstrap Python returned invalid path metadata")
    metadata["ldlibrary"] = _shared_library_name(
        Path(metadata["libdir"]), metadata["ldlibrary"], python_version
    )
    return source, metadata


def _copy_portable_base(staging: Path, python_version: str) -> dict[str, str]:
    source, metadata = _source_python(python_version)
    stdlib = Path(metadata["stdlib"]).resolve()
    library = (Path(metadata["libdir"]) / metadata["ldlibrary"]).resolve()
    if not stdlib.is_dir() or not library.is_file():
        raise HarnessRuntimeError("host bootstrap Python standard library or shared library is missing")
    base = staging / "base"
    (base / "bin").mkdir(parents=True)
    (base / "lib").mkdir()
    shutil.copy2(source, base / "bin" / f"python{python_version}")
    shutil.copy2(library, base / "lib" / library.name)
    stdlib_target = base / metadata["platlibdir"] / f"python{python_version}"
    shutil.copytree(
        stdlib,
        stdlib_target,
        ignore=shutil.ignore_patterns("site-packages", "__pycache__", "*.pyc"),
    )
    excluded_libraries = {
        "ld-linux-x86-64.so.2",
        "libc.so.6",
        "libdl.so.2",
        "libm.so.6",
        "libpthread.so.0",
        "librt.so.1",
        "libutil.so.1",
    }
    bundled_libraries: dict[str, str] = {library.name: _sha256(library)}
    for extension in sorted((stdlib / "lib-dynload").glob("*.so")):
        completed = subprocess.run(
            ["ldd", str(extension)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if completed.returncode != 0:
            raise HarnessRuntimeError(f"failed to inspect shared libraries for {extension.name}")
        for line in completed.stdout.splitlines():
            match = line.strip().split(" => ", 1)
            raw_path = match[1].split(" (", 1)[0] if len(match) == 2 else match[0].split(" (", 1)[0]
            dependency = Path(raw_path)
            if not dependency.is_absolute() or dependency.name in excluded_libraries:
                continue
            destination = base / "lib" / dependency.name
            if not destination.exists():
                shutil.copy2(dependency.resolve(), destination)
            bundled_libraries[dependency.name] = _sha256(destination)
    wrapper = _python_path(staging)
    wrapper.parent.mkdir()
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'runtime_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"\n'
        'export PYTHONHOME="$runtime_root/base"\n'
        'export PYTHONNOUSERSITE=1\n'
        'export LD_LIBRARY_PATH="$runtime_root/base/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\n'
        f'exec "$runtime_root/base/bin/python{python_version}" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return {
        "source_python": str(source),
        "source_python_sha256": _sha256(source),
        "source_stdlib": str(stdlib),
        "source_library": str(library),
        "source_library_sha256": _sha256(library),
        "platlibdir": metadata["platlibdir"],
        "bundled_library_sha256": bundled_libraries,
    }


def _build_runtime(
    runtime_root: Path,
    runtime_dir: Path,
    spec: dict[str, Any],
    lock_path: Path,
    lock_sha256: str,
    runtime_id: str,
) -> dict[str, Any]:
    uv = _uv_binary()
    logs_dir = runtime_root / "logs"
    cache_dir = runtime_root / "cache"
    _make_group_writable(runtime_root, recursive=False)
    for path in (logs_dir, cache_dir):
        path.mkdir(parents=True, exist_ok=True)
        _make_group_writable(path, recursive=False)
    max_attempts = max(1, min(2, int(spec.get("max_prepare_attempts") or 2)))
    errors: list[str] = []

    for attempt in range(1, max_attempts + 1):
        staging = Path(tempfile.mkdtemp(prefix=f".{runtime_id}.attempt-{attempt}-", dir=runtime_root))
        log_path = logs_dir / f"bootstrap-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-attempt-{attempt}.log"
        env = os.environ.copy()
        env.update(
            {
                "UV_CACHE_DIR": str(cache_dir),
                "UV_LINK_MODE": "copy",
            }
        )
        try:
            with log_path.open("w", encoding="utf-8") as log:
                log.write(
                    json.dumps(
                        {
                            "runtime_type": "harness_python",
                            "runtime_id": runtime_id,
                            "attempt": attempt,
                            "package_source": "configured uv index (credentials omitted)",
                            "lock_sha256": lock_sha256,
                            "cache_dir": str(cache_dir),
                            "started_at": _utc_now(),
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
                source_evidence = _copy_portable_base(staging, str(spec["python"]))
                site_packages = (
                    staging
                    / "base"
                    / source_evidence["platlibdir"]
                    / f"python{spec['python']}"
                    / "site-packages"
                )
                sync_command = [
                    uv,
                    "pip",
                    "sync",
                    "--target",
                    str(site_packages),
                    "--python-version",
                    str(spec["python"]),
                    "--require-hashes",
                    "--strict",
                    "--link-mode",
                    "copy",
                    "--cache-dir",
                    str(cache_dir),
                ]
                if attempt == 2:
                    sync_command.append("--refresh")
                sync_command.append(str(lock_path))
                _run_logged(sync_command, env, log, timeout=600)
                probe = _probe(_python_path(staging), [str(value) for value in spec.get("required_imports", [])])
                manifest = {
                    "schema": "sure.harness.runtime.manifest.v1",
                    "runtime_id": runtime_id,
                    "runtime_type": "harness_python",
                    "harness_version": spec["harness_version"],
                    "python_abi": f"cp{str(spec['python']).replace('.', '')}",
                    "python_version": probe["version"],
                    "lock_sha256": lock_sha256,
                    "required_imports": spec.get("required_imports", []),
                    "package_source": "configured uv index (credentials omitted)",
                    "cache_dir": str(cache_dir.resolve()),
                    "install_log": str(log_path.resolve()),
                    "prepare_attempt": attempt,
                    "prepared_at": _utc_now(),
                    "materialization": "portable_host_cpython",
                    "materialization_version": spec["materialization_version"],
                    "python_home": "base",
                    "library_paths": ["base/lib"],
                    "source_evidence": source_evidence,
                }
                _manifest_path(staging).write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
                _make_group_writable(staging)
                _make_group_writable(logs_dir)
                _make_group_writable(cache_dir)
                if runtime_dir.exists():
                    raise HarnessRuntimeError(f"runtime destination appeared during bootstrap: {runtime_dir}")
                staging.rename(runtime_dir)
                return _verified_contract(runtime_dir, spec, lock_sha256, runtime_id)
        except (HarnessRuntimeError, OSError, subprocess.SubprocessError) as exc:
            errors.append(f"attempt {attempt}: {exc}; log={log_path}")
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    raise HarnessRuntimeError("Harness Runtime preparation failed after bounded attempts: " + "; ".join(errors))


def resolve_runtime(runtime_root: Path, *, repair: bool = True) -> dict[str, Any]:
    spec, lock_path, lock_sha256, runtime_id = _load_spec()
    runtime_root = runtime_root.expanduser().resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    runtime_dir = runtime_root / runtime_id
    with (runtime_root / ".bootstrap.lock").open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            return _verified_contract(runtime_dir, spec, lock_sha256, runtime_id)
        except HarnessRuntimeError:
            if not repair:
                raise
        if runtime_dir.exists():
            quarantine = runtime_root / f".{runtime_id}.invalid-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
            runtime_dir.rename(quarantine)
        return _build_runtime(runtime_root, runtime_dir, spec, lock_path, lock_sha256, runtime_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    parser.add_argument("--check", action="store_true", help="Verify only; do not prepare or repair")
    parser.add_argument("--json", action="store_true", help="Print the resolved runtime contract as JSON")
    args = parser.parse_args()
    try:
        contract = resolve_runtime(Path(args.runtime_root), repair=not args.check)
    except HarnessRuntimeError as exc:
        print(f"HARNESS_RUNTIME_NOT_READY: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(contract, sort_keys=True) if args.json else contract["python_executable"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
