#!/usr/bin/env python3
"""Filesystem primitives for the SURE memory system.

sure/memory/ is shared by everyone who uses one checkout, so:
- every directory / file created here is made group-writable (setfacl default ACL
  on the root when available, setgid + g+rwx / g+rw otherwise);
- every file write is temp file + os.replace (atomic on POSIX; retried on Windows);
- jsonl readers tolerate broken lines and report how many they skipped;
- writers of meta / index / decisions take one lock on sure/memory/.lock.

atomic_replace / atomic_write_bytes adapted from hermes-agent utils.py and
memory_lock from hermes-agent tools/memory_tool.py (NousResearch, MIT,
Copyright (c) 2025 Nous Research). Group-writable logic follows
sure/runtime/harness/bootstrap.py (_make_group_writable). See THIRD_PARTY.md.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import stat
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:  # POSIX only
    import fcntl
except ImportError:  # pragma: no cover - Windows dev box
    fcntl = None  # type: ignore[assignment]
try:  # POSIX only
    import grp
except ImportError:  # pragma: no cover
    grp = None  # type: ignore[assignment]
try:  # Windows only
    import msvcrt
except ImportError:  # pragma: no cover
    msvcrt = None  # type: ignore[assignment]

LIB_DIR = Path(__file__).resolve().parent
SUBDIRS = ("provisional", "outbox", "meta", "usage", "digests", "rejected")
LOCK_NAME = ".lock"
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_ENTRY_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
_REPLACE_ATTEMPTS = 5


# --- locations -----------------------------------------------------------------

def repo_root_from_package_dir(package_dir: Path) -> Path:
    """sure/skills/<skill> -> repo root (three levels up), mirroring resolve.ts repoRootForPackage."""
    return Path(package_dir).resolve().parents[2]


def memory_root(repo_root: Path) -> Path:
    return Path(repo_root) / "sure" / "memory"


def split_entry_id(entry_id: str) -> tuple[str, str] | None:
    """'<target_skill>/<slug>' -> (target_skill, slug); None when malformed."""
    if not isinstance(entry_id, str) or entry_id.count("/") != 1:
        return None
    skill, slug = entry_id.split("/", 1)
    if not (_ENTRY_SEGMENT_RE.match(skill) and _ENTRY_SEGMENT_RE.match(slug)):
        return None
    if skill in (".", "..") or slug in (".", ".."):
        return None
    return skill, slug


def slugify(text: str, fallback: str) -> str:
    """Lowercase ascii slug, max 60 chars; fallback when nothing survives (e.g. a Chinese title)."""
    slug = _SLUG_RE.sub("-", text.strip().lower()).strip("-")
    slug = slug[:60].rstrip("-")
    return slug or fallback


# --- permissions ---------------------------------------------------------------

def group_writable(path: Path) -> None:
    """Best effort chmod: files g+rw (g+x if already executable), dirs g+rwx+setgid. Never raises."""
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
        bits = stat.S_IRGRP | stat.S_IWGRP
        if os.path.isdir(path):
            bits |= stat.S_IXGRP | stat.S_ISGID
        elif mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            bits |= stat.S_IXGRP
        if mode | bits != mode:
            os.chmod(path, mode | bits)
    except OSError:
        pass


def _apply_default_acl(root: Path) -> None:
    """First-time root setup: setfacl default ACL so every child inherits group rw. Silent on failure."""
    setfacl = shutil.which("setfacl")
    if not setfacl or grp is None:
        return
    try:
        gid = os.stat(root).st_gid
        try:
            group = grp.getgrgid(gid).gr_name
        except KeyError:
            group = str(gid)
        entries = f"g:{group}:rwx,m::rwx,d:g:{group}:rwx,d:m::rwx"
        subprocess.run([setfacl, "-m", entries, "--", str(root)], capture_output=True, text=True, check=False)
    except OSError:
        pass


def ensure_dir(path: Path) -> None:
    """mkdir -p; each component we create becomes group-writable. Tolerates a concurrent creator."""
    path = Path(path)
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    for directory in reversed(missing):
        try:
            directory.mkdir()
        except FileExistsError:
            pass
        group_writable(directory)


def ensure_memory_tree(root: Path) -> None:
    """Create sure/memory/ and its fixed subdirectories; run the ACL setup the first time."""
    root = Path(root)
    fresh = not root.exists()
    ensure_dir(root)
    if fresh:
        _apply_default_acl(root)
    for name in SUBDIRS:
        ensure_dir(root / name)
    lock = root / LOCK_NAME
    if not lock.exists():
        try:
            lock.touch()
        except OSError:
            pass
    group_writable(lock)


def fix_perms(root: Path) -> list[str]:
    """cli fix-perms: walk sure/memory/ and re-apply group-writable bits. Returns paths that still
    failed, each with the reason when the walk could not even inspect it."""
    root = Path(root)
    failed: list[str] = []
    ensure_memory_tree(root)
    for path in [root, *root.rglob("*")]:
        # Other processes are writing this tree while we walk it: an atomic_write_bytes temp file
        # is replaced, a promote rewrites meta. A path that disappears between the walk and the
        # stat is not a permission problem, and abandoning the walk would leave the rest unfixed.
        # is_symlink() belongs inside the try: on 3.11 it reaches os.stat, so a path the walk may
        # not inspect raises right here.
        try:
            if path.is_symlink():
                continue
            before = stat.S_IMODE(os.stat(path).st_mode)
            group_writable(path)
            after = stat.S_IMODE(os.stat(path).st_mode)
        except FileNotFoundError:
            continue
        except OSError as exc:
            # Anything else (EACCES on the file or on a directory above it, a dead NFS mount) is a
            # real finding: the path is still there and still unfixed. Skipping it the way the race
            # above is skipped would report a healthy tree while a teammate cannot write the file.
            failed.append(f"{path} (cannot inspect: {exc.strerror or exc.__class__.__name__})")
            continue
        if not (after & stat.S_IWGRP) and (before == after):
            failed.append(str(path))
    return failed


# --- atomic writes -------------------------------------------------------------

def atomic_replace(src: Path, dst: Path) -> None:
    """os.replace with a short jittered retry: on Windows a reader holding dst makes replace fail with
    PermissionError (winerror 5/32/33) for a few ms. POSIX never retries."""
    delay = 0.02
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if os.name != "nt" or attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(delay + random.random() * delay)
            delay *= 2


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path = Path(path)
    ensure_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        group_writable(tmp)  # mkstemp creates 0600; the shared checkout needs g+rw
        atomic_replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_text(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


# --- jsonl ---------------------------------------------------------------------

def append_jsonl(path: Path, obj: dict, max_line_bytes: int) -> None:
    """Append one compact JSON line. Raises ValueError when the line exceeds max_line_bytes (nothing written)."""
    line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    data = (line + "\n").encode("utf-8")
    if len(data) > max_line_bytes:
        raise ValueError(f"jsonl line is {len(data)} bytes, limit {max_line_bytes}")
    path = Path(path)
    ensure_dir(path.parent)
    fresh = not path.exists()
    with open(path, "ab") as handle:
        handle.write(data)
        handle.flush()
    if fresh:
        group_writable(path)


def read_jsonl(path: Path) -> tuple[list[dict], int]:
    """(rows, skipped): every line that is not a JSON object is skipped and counted (torn writes, corruption)."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return [], 0
    rows: list[dict] = []
    skipped = 0
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if isinstance(obj, dict):
            rows.append(obj)
        else:
            skipped += 1
    return rows, skipped


# --- lock ----------------------------------------------------------------------

def locking_available() -> bool:
    return fcntl is not None or msvcrt is not None


@contextmanager
def memory_lock(root: Path) -> Iterator[None]:
    """Exclusive lock on <root>/.lock. fcntl.flock on POSIX, msvcrt.locking on Windows, no-op elsewhere.
    Callers must not nest it (flock on a second fd of the same file blocks the same process)."""
    root = Path(root)
    ensure_dir(root)
    lock_path = root / LOCK_NAME
    if not locking_available():
        yield
        return
    handle = open(lock_path, "a+", encoding="utf-8")
    group_writable(lock_path)  # the first user creates it; later users must be able to open it a+
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        else:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[union-attr]
        yield
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            else:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[union-attr]
        except OSError:
            pass
        handle.close()


# --- hashes, time, config ------------------------------------------------------

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# --- decisions.jsonl ------------------------------------------------------------

DECISIONS_NAME = "decisions.jsonl"
DECISION_ACTIONS = ("publish", "confirm", "reject", "supersede", "promote", "demote")


def decision_row(action: str, entry_id: str, by: str, **extra: Any) -> dict:
    """One decisions.jsonl row. `by` is "auto" for hook / promote decisions, "human" for cli ones."""
    if action not in DECISION_ACTIONS:
        raise ValueError(f"unknown decision action {action!r}")
    row: dict[str, Any] = {"action": action, "entry_id": entry_id, "by": by, "at": utc_now()}
    row.update(extra)
    return row


def append_decision(root: Path, row: dict) -> None:
    """Append one row to <root>/decisions.jsonl. The caller must already hold memory_lock(root)."""
    append_jsonl(Path(root) / DECISIONS_NAME, row, 65536)


def load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_config(memory_lib_dir: Path | None = None) -> dict:
    return load_json((memory_lib_dir or LIB_DIR) / "config.json")


def load_units(memory_lib_dir: Path | None = None) -> dict:
    return load_json((memory_lib_dir or LIB_DIR) / "units.json")


def load_log_paths(memory_lib_dir: Path | None = None) -> dict:
    return load_json((memory_lib_dir or LIB_DIR) / "log_paths.json")
