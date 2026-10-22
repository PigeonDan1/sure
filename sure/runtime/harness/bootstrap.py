#!/usr/bin/env python3
"""Materialize and verify the versioned SURE Harness Python runtime."""

from __future__ import annotations

import argparse
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

for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "runtime" / "uvenv.py").is_file():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

from sure.runtime.uvenv import (  # noqa: E402
    child_environment,
    exclusive_lock,
    probe as probe_python,
    publish,
    runtime_python_relative,
    sha256_file,
    sync_command,
    uv_binary,
    venv_command,
)


SPEC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPEC_DIR.parents[2]
DEFAULT_RUNTIME_ROOT = REPO_ROOT / "sure" / ".runtime" / "harness"
MANIFEST_NAME = "runtime-manifest.json"
# uv installs the interpreter it fetched beside the environment, because it
# refuses to create a virtual environment in a directory that already exists and
# therefore cannot install into one. The tree is made self-contained afterwards
# by moving that interpreter in; see _relocate_base_python.
INSTALL_DIR_NAME = ".python-base"
BASE_PYTHON_NAME = "base-python"


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
    lock_sha256 = sha256_file(lock_path)
    harness_version = str(spec.get("harness_version") or "").strip()
    if not harness_version:
        raise HarnessRuntimeError("runtime definition must declare harness_version")
    try:
        materialization_version = int(spec.get("materialization_version") or 0)
    except (TypeError, ValueError):
        # A value int() cannot read is as unusable as a missing one, and only a
        # HarnessRuntimeError reaches the launcher as a message instead of a traceback.
        materialization_version = 0
    if materialization_version < 1:
        raise HarnessRuntimeError("runtime definition must declare materialization_version")
    runtime_id = (
        f"sure-harness-{harness_version}-m{materialization_version}"
        f"-py{python_version.replace('.', '')}-{lock_sha256[:12]}"
    )
    return spec, lock_path, lock_sha256, runtime_id


def _python_path(runtime_dir: Path) -> Path:
    return runtime_dir / runtime_python_relative()


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
        env=child_environment(),
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
    _assert_self_contained(runtime_dir)
    # The manifest records which base interpreter the venv was built on, and this
    # tree carries that interpreter itself. Reading the hash back is what proves
    # the interpreter inside the tree is still the one the lock was resolved for:
    # a tree whose interpreter was replaced underneath it no longer matches, and
    # a mismatch reaches resolve_runtime, which quarantines the directory and
    # rebuilds rather than trusting it.
    identity = probe_python(python, error=HarnessRuntimeError)
    if identity["base_python_sha256"] != manifest.get("base_python_sha256"):
        raise HarnessRuntimeError("Harness Runtime base_python_sha256 mismatch")
    probe = _probe(python, required_imports)
    if not str(probe.get("version") or "").startswith(f"{spec['python']}."):
        raise HarnessRuntimeError(
            f"Harness Runtime Python ABI mismatch: expected {spec['python']}, got {probe.get('version')}"
        )
    contract = dict(manifest)
    contract.update(
        {
            "status": "ready",
            # Not python.resolve(): the entry point is what a container executes,
            # and on POSIX it is a relative link into the tree's own interpreter.
            "python_executable": str(_python_path(runtime_dir.resolve())),
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


def _is_link(path: Path) -> bool:
    # On Windows, uv's version-agnostic alias directory is a junction, which
    # Path.is_symlink() reports as False. A junction is a link for relocation
    # purposes: readlink works on it, and a rename breaks its target. Detect it
    # via the reparse point tag on the lstat record: Path.is_junction arrived
    # only in 3.12, and bootstrap.py runs under the 3.11 interpreter it is
    # preparing, so it cannot rely on that method.
    if path.is_symlink():
        return True
    if os.name == "nt" and getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", None) is not None:
        try:
            return (
                getattr(path.lstat(), "st_reparse_tag", 0)
                == stat.IO_REPARSE_TAG_MOUNT_POINT
            )
        except OSError:
            pass
    return False


def _is_same_file(path: Path, other: Path) -> bool:
    try:
        return (path.stat().st_dev, path.stat().st_ino) == (
            other.stat().st_dev,
            other.stat().st_ino,
        )
    except OSError:
        return False


def _link_target(path: Path) -> str:
    return os.readlink(path)


def _aliases_of(install_dir: Path, source: Path) -> set[str]:
    """Names under install_dir that refer to the same interpreter directory.

    uv writes one real interpreter directory and a version-agnostic alias beside
    it. On Windows the alias is a junction, on POSIX a symlink, but the
    identity test below covers both without caring which one uv chose: anything
    whose st_dev/st_ino equals the source is the same directory.
    """
    aliases = {source.name}
    for entry in install_dir.iterdir():
        if entry == source or not entry.is_dir() or entry.name.startswith("."):
            continue
        if _is_same_file(entry, source):
            aliases.add(entry.name)
    return aliases


def _relative_links_into(
    runtime_dir: Path,
    install_dir: Path,
    source: Path,
    base_dir: Path,
    aliases: set[str],
) -> list[str]:
    """Re-point absolute links at the relocated interpreter; return escaped ones.

    uv installed the interpreter under install_dir beside the tree and the
    tree's links name that location. After the interpreter moves to base_dir,
    every absolute link naming the old location must be re-pointed relatively
    at the new one. A link that points anywhere else inside the tree is fine; a
    link that points outside it is reported so the caller can refuse the tree.
    """
    escaped: list[str] = []
    install_root = install_dir.resolve()
    source_root = source.resolve()
    tree_root = runtime_dir.resolve()
    for root, dirs, files in os.walk(runtime_dir):
        root_path = Path(root)
        for name in dirs + files:
            path = root_path / name
            if not _is_link(path):
                continue
            try:
                target = Path(_link_target(path))
            except OSError as exc:
                escaped.append(f"{path} (unreadable: {exc})")
                continue
            if not target.is_absolute():
                continue
            resolved = path.parent.joinpath(target).resolve(strict=False)
            try:
                inside = resolved.relative_to(source_root)
            except ValueError:
                try:
                    under_install = resolved.relative_to(install_root)
                except ValueError:
                    # Not one of the interpreter's own links. If it still
                    # resolves inside the tree, leave it; otherwise it escapes.
                    try:
                        resolved.relative_to(tree_root)
                    except ValueError:
                        escaped.append(str(path))
                    continue
                # uv spells the entry point through its version-agnostic alias
                # (cpython-3.11-...) rather than the release-named directory it
                # links to (cpython-3.11.17-...). The alias is unlinked before
                # this runs, so resolving it no longer reaches the real
                # directory and the name has to be rewritten here; carrying it
                # over would publish a link into a directory that is gone.
                first, *rest = under_install.parts
                if first not in aliases:
                    escaped.append(str(path))
                    continue
                inside = Path(*rest)
            relative = Path(os.path.relpath(base_dir, path.parent)) / inside
            path.unlink()
            path.symlink_to(relative, target_is_directory=True)
    return escaped


def _replace_windows_launchers(runtime_dir: Path, base_dir: Path) -> None:
    """Replace uv's Scripts launcher stubs with the interpreter's own binaries.

    uv writes Scripts/python.exe as a trampoline stub that embeds the base
    interpreter's absolute path in a fixed-width binary resource; renaming the
    tree breaks the stub. CPython's own venv avoids this by copying the real
    python.exe, pythonw.exe and the runtime DLLs into Scripts/, so do the same
    here. The DLLs matter: copying only python.exe starts and then dies with
    exit code 0xC0000135 on Windows.
    """
    if os.name != "nt":
        return
    scripts = runtime_dir / "Scripts"
    if not scripts.is_dir():
        return
    for name in ("python.exe", "pythonw.exe"):
        source = base_dir / name
        if source.is_file():
            shutil.copy2(source, scripts / name)
    for entry in base_dir.glob("*.dll"):
        shutil.copy2(entry, scripts / entry.name)


def _home_of(lines: list[str]) -> str:
    for line in lines:
        if line.startswith("home"):
            return line.split("=", 1)[1].strip()
    raise HarnessRuntimeError("Harness Runtime pyvenv.cfg has no home")


def _read_lines(path: Path, label: str) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise HarnessRuntimeError(f"cannot read {label}: {path}: {exc}") from exc


def _write_home(cfg: Path, lines: list[str], home: Path) -> None:
    rewritten = [
        f"home = {home}" if line.startswith("home") else line
        for line in lines
    ]
    cfg.write_text("\n".join(rewritten) + "\n", encoding="utf-8")


def _relocate_base_python(runtime_dir: Path, install_dir: Path) -> None:
    """Move uv's managed interpreter into the tree and re-point everything.

    uv installed exactly one real interpreter under install_dir (beside its
    version-agnostic alias). Move it to `<runtime>/base-python`, re-point the
    links that named the old location, replace Windows launcher stubs with the
    real interpreter, and rewrite pyvenv.cfg home to name the in-tree copy.
    """
    base_dir = runtime_dir / BASE_PYTHON_NAME
    directories = [
        entry for entry in install_dir.iterdir()
        if entry.is_dir() and not entry.name.startswith(".")
    ]
    if not directories:
        raise HarnessRuntimeError(f"uv installed no interpreter into {install_dir}")
    # The real interpreter is the one directory that is not itself a link; uv's
    # version-agnostic alias is a junction on Windows and a symlink on POSIX.
    # Everything else is then validated against that identity, so the alias
    # detection and the extra-interpreter check share one source of truth.
    reals = [entry for entry in directories if not _is_link(entry)]
    if len(reals) != 1:
        raise HarnessRuntimeError(
            f"uv installed {len(reals)} real interpreters into {install_dir}, expected exactly one"
        )
    source = reals[0]
    aliases = _aliases_of(install_dir, source)
    extras = [entry.name for entry in directories if entry.name not in aliases]
    if extras:
        raise HarnessRuntimeError(
            f"uv installed more than one interpreter into {install_dir}: "
            f"{', '.join(sorted(extras))}"
        )
    cfg = runtime_dir / "pyvenv.cfg"
    lines = _read_lines(cfg, "pyvenv.cfg")
    home = _home_of(lines)
    try:
        inside = Path(home).resolve().relative_to(source.resolve())
    except (OSError, ValueError) as exc:
        raise HarnessRuntimeError(
            f"Harness Runtime pyvenv.cfg home is not inside uv's interpreter: {home}"
        ) from exc
    # Drop the alias link before moving the real directory, so the junction
    # cannot outlive it. Both unlink and rmdir remove a junction on Windows
    # without touching its target; unlink is the pathlib spelling that works on
    # POSIX symlinked directories too.
    for entry in install_dir.iterdir():
        if entry.name in aliases and entry.name != source.name:
            try:
                entry.unlink()
            except OSError:
                pass
    shutil.move(str(source), str(base_dir))
    escaped = _relative_links_into(runtime_dir, install_dir, source, base_dir, aliases)
    if escaped:
        raise HarnessRuntimeError(
            "Harness Runtime links to paths outside the tree: " + "; ".join(escaped)
        )
    if os.name == "nt":
        _replace_windows_launchers(runtime_dir, base_dir)
    _write_home(cfg, lines, base_dir / inside)
    shutil.rmtree(install_dir, ignore_errors=True)


def _retarget(runtime_dir: Path, published: Path) -> None:
    """Rewrite paths that name the staging directory to the published one.

    The probe has to run while the tree is at its staging path, because
    pyvenv.cfg home names an absolute path. Right before the publish rename,
    that home must be re-pointed at the published absolute path, and text files
    under the entry-point directory (POSIX console-script shebangs like
    bin/typer) must follow. Windows console-script trampolines embed the staging
    path in a fixed-width binary resource and cannot be retargeted here; they
    are not on the execution path, which enters through Scripts/python.exe.
    """
    cfg = runtime_dir / "pyvenv.cfg"
    lines = _read_lines(cfg, "pyvenv.cfg")
    home = Path(_home_of(lines))
    staging = runtime_dir.resolve()
    published = published.resolve()
    try:
        inside = home.resolve().relative_to(staging)
    except ValueError as exc:
        raise HarnessRuntimeError(
            f"Harness Runtime pyvenv.cfg home is not inside the staging tree: {home}"
        ) from exc
    _write_home(cfg, lines, published / inside)
    entry_dir = runtime_dir / runtime_python_relative().split("/", 1)[0]
    old_bytes = str(staging).encode()
    new_bytes = str(published).encode()
    for path in entry_dir.iterdir():
        if not path.is_file() or _is_link(path):
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data or old_bytes not in data:
            continue
        path.write_bytes(data.replace(old_bytes, new_bytes))


def _assert_self_contained(runtime_dir: Path) -> None:
    """Refuse a runtime that reaches outside its own tree.

    A self-contained runtime is the entire point of the m4 layout: inside a
    container the host's interpreter is gone, so any link or pyvenv.cfg home
    that escapes the tree turns into a failure there. Verify it here instead of
    there.
    """
    root = runtime_dir.resolve()
    for current, dirs, files in os.walk(runtime_dir):
        current_path = Path(current)
        for name in dirs + files:
            path = current_path / name
            if not _is_link(path):
                continue
            try:
                target = Path(_link_target(path))
            except OSError as exc:
                raise HarnessRuntimeError(
                    f"Harness Runtime link is unreadable: {path}: {exc}"
                ) from exc
            resolved = path.parent.joinpath(target).resolve() if not target.is_absolute() else target.resolve()
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise HarnessRuntimeError(
                    f"Harness Runtime is not self-contained: {path} -> {target}"
                ) from exc
            # A link that stays inside the tree can still be broken: pointing it
            # at a directory the relocation removed keeps it "inside" while
            # making the entry point unstartable.
            if not resolved.exists():
                raise HarnessRuntimeError(
                    f"Harness Runtime link does not resolve: {path} -> {target}"
                )
    cfg = runtime_dir / "pyvenv.cfg"
    home = Path(_home_of(_read_lines(cfg, "pyvenv.cfg")))
    try:
        home.resolve().relative_to(root)
    except ValueError as exc:
        raise HarnessRuntimeError(
            f"Harness Runtime is not self-contained: pyvenv.cfg home {home}"
        ) from exc
    try:
        _python_path(runtime_dir).resolve().relative_to(root)
    except ValueError as exc:
        raise HarnessRuntimeError(
            "Harness Runtime is not self-contained: Python escapes the tree"
        ) from exc


def _build_runtime(
    runtime_root: Path,
    runtime_dir: Path,
    spec: dict[str, Any],
    lock_path: Path,
    lock_sha256: str,
    runtime_id: str,
) -> dict[str, Any]:
    uv = uv_binary(
        error=HarnessRuntimeError,
        message=(
            "uv is required to prepare the locked Harness Runtime; "
            "set SURE_UV_BIN to an existing uv executable"
        ),
    )
    logs_dir = runtime_root / "logs"
    cache_dir = runtime_root / "cache"
    for path in (logs_dir, cache_dir):
        path.mkdir(parents=True, exist_ok=True)
    max_attempts = max(1, min(2, int(spec.get("max_prepare_attempts") or 2)))
    errors: list[str] = []

    for attempt in range(1, max_attempts + 1):
        staging = Path(tempfile.mkdtemp(prefix=f".{runtime_id}.attempt-{attempt}-", dir=runtime_root))
        # Beside the staging tree, never inside it: uv writes the interpreter it
        # fetched here because it refuses to create a venv in a directory that
        # already exists. _relocate_base_python moves it into the tree.
        install_dir = runtime_root / f".{runtime_id}.attempt-{attempt}-{INSTALL_DIR_NAME}"
        log_path = logs_dir / f"bootstrap-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-attempt-{attempt}.log"
        env = child_environment()
        env.update(
            {
                "UV_CACHE_DIR": str(cache_dir),
                "UV_LINK_MODE": "copy",
                "UV_PYTHON_INSTALL_DIR": str(install_dir),
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
                # uv fetches its own CPython when the host has none. That download is
                # uv's, hash-checked by uv, and it is the reason a plain PC needs no
                # Python of its own; the interpreter it picked is recorded below.
                _run_logged(
                    venv_command(
                        uv,
                        staging,
                        python=str(spec["python"]),
                        allow_python_downloads=True,
                        python_preference="only-managed",
                    ),
                    env,
                    log,
                    timeout=600,
                )
                runtime_python = _python_path(staging)
                sync_extra = ["--link-mode", "copy", "--cache-dir", str(cache_dir)]
                if attempt == 2:
                    sync_extra.append("--refresh")
                _run_logged(
                    sync_command(
                        uv,
                        runtime_python,
                        lock_path,
                        allow_python_downloads=True,
                        extra=sync_extra,
                    ),
                    env,
                    log,
                    timeout=600,
                )
                _relocate_base_python(staging, install_dir)
                identity = probe_python(runtime_python, error=HarnessRuntimeError)
                imports = _probe(runtime_python, [str(value) for value in spec.get("required_imports", [])])
                manifest = {
                    "schema": "sure.harness.runtime.manifest.v1",
                    "runtime_id": runtime_id,
                    "runtime_type": "harness_python",
                    "harness_version": spec["harness_version"],
                    "python_abi": f"cp{str(spec['python']).replace('.', '')}",
                    "python_version": imports["version"],
                    "lock_sha256": lock_sha256,
                    "required_imports": spec.get("required_imports", []),
                    "package_source": "configured uv index (credentials omitted)",
                    "cache_dir": str(cache_dir.resolve()),
                    "install_log": str(log_path.resolve()),
                    "prepare_attempt": attempt,
                    "prepared_at": _utc_now(),
                    "materialization": "uv_venv",
                    "materialization_version": spec["materialization_version"],
                    "base_python_sha256": identity["base_python_sha256"],
                    # The tree carries its own interpreter, so it can be mounted
                    # into a container that has no Python of the host's.
                    "self_contained": True,
                    "base_python_relative": BASE_PYTHON_NAME,
                }
                _manifest_path(staging).write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
                # Last, because it is the only thing that has to know the final
                # name, and the probe above must run while the tree is where it
                # is: pyvenv.cfg names its interpreter by path.
                _retarget(staging, runtime_dir)
                publish(staging, runtime_dir, error=HarnessRuntimeError)
                return _verified_contract(runtime_dir, spec, lock_sha256, runtime_id)
        except (HarnessRuntimeError, OSError, subprocess.SubprocessError) as exc:
            errors.append(f"attempt {attempt}: {exc}; log={log_path}")
        finally:
            if staging.exists():
                shutil.rmtree(staging)
            if install_dir.exists():
                shutil.rmtree(install_dir, ignore_errors=True)
    raise HarnessRuntimeError("Harness Runtime preparation failed after bounded attempts: " + "; ".join(errors))


def _prune_superseded(runtime_root: Path, runtime_id: str) -> None:
    """Drop the runtimes under this root that the current runtime_id replaced.

    runtime_id carries the harness version, the materialization version and the
    lock hash, so every bump of any of them materializes a new directory beside
    the old one; nothing collected the old one and the root grew a whole venv
    per bump. Only well-formed runtime directories are in scope: the
    `.{runtime_id}.invalid-*` quarantines and `.{runtime_id}.attempt-*` staging
    trees are left alone, the first because it is the only record of why a
    runtime was rejected and the second because it belongs to a bootstrap in
    flight. Best effort: a directory some already-running process still holds
    open must not fail a bootstrap that has otherwise succeeded.
    """
    for entry in runtime_root.iterdir():
        if entry.name.startswith(".") or entry.name == runtime_id or not entry.is_dir():
            continue
        try:
            manifest = json.loads((entry / MANIFEST_NAME).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(manifest, dict) and manifest.get("schema") == "sure.harness.runtime.manifest.v1":
            shutil.rmtree(entry, ignore_errors=True)


def resolve_runtime(runtime_root: Path, *, repair: bool = True, prune: bool = True) -> dict[str, Any]:
    spec, lock_path, lock_sha256, runtime_id = _load_spec()
    runtime_root = runtime_root.expanduser().resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    runtime_dir = runtime_root / runtime_id
    with exclusive_lock(runtime_root / ".bootstrap.lock"):
        try:
            return _verified_contract(runtime_dir, spec, lock_sha256, runtime_id)
        except HarnessRuntimeError:
            if not repair:
                raise
        # Quarantine runs under the same lock as the rebuild: two sessions that both
        # find a stale runtime must not race on this rename.
        if runtime_dir.exists():
            quarantine = runtime_root / f".{runtime_id}.invalid-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
            runtime_dir.rename(quarantine)
        contract = _build_runtime(runtime_root, runtime_dir, spec, lock_path, lock_sha256, runtime_id)
        # Under the same lock as the build: no other resolve can be verifying or
        # publishing a sibling directory while this one is being reclaimed.
        if prune:
            _prune_superseded(runtime_root, runtime_id)
        return contract


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    parser.add_argument("--check", action="store_true", help="Verify only; do not prepare or repair")
    parser.add_argument("--json", action="store_true", help="Print the resolved runtime contract as JSON")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the runtime directories this runtime_id supersedes instead of reclaiming them",
    )
    args = parser.parse_args()
    try:
        contract = resolve_runtime(Path(args.runtime_root), repair=not args.check, prune=not args.keep)
    except HarnessRuntimeError as exc:
        print(f"HARNESS_RUNTIME_NOT_READY: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(contract, sort_keys=True) if args.json else contract["python_executable"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
