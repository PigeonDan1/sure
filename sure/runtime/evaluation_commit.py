"""Crash-aware, no-clobber directory publication for evaluation artifacts.

The evaluator owns the contents of a candidate directory; this module owns
only the storage transaction.  It deliberately has no SURE/domain imports so
the same protocol can be used by local, container and remote adapters.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

try:  # pragma: no cover - the supported deployment is POSIX, but imports stay portable.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


class EvaluationCommitError(RuntimeError):
    """Raised when a prepared publication no longer matches its inputs."""


CommitFaultPoint = str
CommitFaultInjector = Callable[[CommitFaultPoint], None]


def _fault(injector: CommitFaultInjector | None, point: CommitFaultPoint) -> None:
    """Invoke an opt-in crash injector used by the recovery test matrix.

    The normal evaluator never supplies this callback. Keeping it as an explicit
    argument makes fault injection deterministic without making an environment
    variable or a signal part of the publication protocol.
    """
    if injector is not None:
        injector(point)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_fingerprint(root: Path) -> str | None:
    """Return a deterministic content/type fingerprint, excluding mtime."""

    root = Path(os.path.abspath(os.path.expanduser(os.fspath(root))))
    if not os.path.lexists(root):
        return None
    root_stat = root.lstat()
    if stat.S_ISLNK(root_stat.st_mode):
        payload = f"root\tsymlink\t{os.readlink(root)}\n".encode("utf-8", errors="surrogateescape")
        return hashlib.sha256(payload).hexdigest()
    if not stat.S_ISDIR(root_stat.st_mode):
        return hashlib.sha256(f"file\t{_sha256(root)}\t{root_stat.st_size}".encode("ascii")).hexdigest()
    rows: list[str] = []
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        directory_names.sort()
        file_names.sort()
        for name in [*directory_names, *file_names]:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            entry_stat = path.lstat()
            if stat.S_ISLNK(entry_stat.st_mode):
                rows.append(f"{relative}\tsymlink\t{os.readlink(path)}")
            elif stat.S_ISDIR(entry_stat.st_mode):
                rows.append(f"{relative}\tdirectory")
            elif stat.S_ISREG(entry_stat.st_mode):
                rows.append(f"{relative}\tfile\t{entry_stat.st_size}\t{_sha256(path)}")
            else:
                rows.append(f"{relative}\tother\t{entry_stat.st_mode:o}")
    return hashlib.sha256(("\n".join(rows) + "\n").encode("utf-8", errors="surrogateescape")).hexdigest()


def _copy_tree(source: Path, destination: Path) -> None:
    """Copy a regular artifact tree while refusing symlinks and special files."""

    source = Path(os.path.abspath(os.path.expanduser(os.fspath(source))))
    if source.is_symlink() or not source.is_dir():
        raise EvaluationCommitError(f"commit source is not a directory: {source}")
    destination.mkdir(parents=True, exist_ok=False)
    for current, directory_names, file_names in os.walk(source, followlinks=False):
        current_path = Path(current)
        relative = current_path.relative_to(source)
        target_dir = destination / relative
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in [*directory_names, *file_names]:
            source_path = current_path / name
            if source_path.is_symlink():
                raise EvaluationCommitError(f"evaluation artifact tree contains a symlink: {source_path}")
            entry_stat = source_path.lstat()
            if stat.S_ISDIR(entry_stat.st_mode):
                (target_dir / name).mkdir()
            elif stat.S_ISREG(entry_stat.st_mode):
                shutil.copy2(source_path, target_dir / name)
            else:
                raise EvaluationCommitError(f"evaluation artifact tree contains a special file: {source_path}")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _publication_lock(path: Path) -> Iterator[None]:
    lock_path = path.parent / f".{path.name}.sure-commit.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _write_journal(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _fsync_directory(path.parent)


@dataclass
class PreparedEvaluationCommit:
    """A candidate tree and the immutable identities observed at prepare time."""

    transaction_root: Path
    candidate_root: Path
    destination_root: Path
    source_root: Path | None
    source_fingerprint: str | None
    destination_fingerprint: str | None
    destination_existed: bool
    candidate_fingerprint: str | None = None
    backup_root: Path | None = None
    phase: str = "prepared"
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def journal_path(self) -> Path:
        return self.transaction_root / "commit.json"

    def journal(self) -> dict[str, object]:
        return {
            "schema": "sure.evaluation.commit.v1",
            "phase": self.phase,
            "transaction_root": str(self.transaction_root),
            "candidate_root": str(self.candidate_root),
            "destination_root": str(self.destination_root),
            "source_root": None if self.source_root is None else str(self.source_root),
            "source_fingerprint": self.source_fingerprint,
            "destination_fingerprint": self.destination_fingerprint,
            "destination_existed": self.destination_existed,
            "candidate_fingerprint": self.candidate_fingerprint,
            "backup_root": None if self.backup_root is None else str(self.backup_root),
            "metadata": self.metadata,
        }


def recover_pending(destination: Path, *, rollback_published: bool = True) -> list[str]:
    """Remove/rollback orphaned transactions from a previous crashed process."""

    destination = Path(os.path.abspath(os.path.expanduser(os.fspath(destination))))
    parent = destination.parent
    if not parent.is_dir():
        return []
    recovered: list[str] = []
    for journal_path in sorted(parent.glob(".sure-eval-txn-*/commit.json")):
        try:
            payload = json.loads(journal_path.read_text(encoding="utf-8"))
            transaction_root = Path(str(payload["transaction_root"])).resolve()
            phase = str(payload.get("phase") or "")
            target = Path(str(payload.get("destination_root") or "")).resolve()
            if target != destination:
                continue
            # A journal is data, not authority.  Only the transaction directory
            # selected by the glob may be removed, and candidate/backup paths
            # must be its fixed children.  This prevents a tampered journal from
            # turning crash recovery into an arbitrary recursive delete.
            expected_transaction = journal_path.parent.resolve()
            if transaction_root != expected_transaction or not transaction_root.name.startswith(".sure-eval-txn-"):
                continue
            expected_candidate = transaction_root / "candidate"
            expected_backup = transaction_root / "backup"
            if payload.get("candidate_root") and Path(str(payload["candidate_root"])).resolve() != expected_candidate:
                continue
            backup_value = payload.get("backup_root")
            backup = expected_backup if backup_value else None
            if backup is not None and Path(str(backup_value)).resolve() != expected_backup:
                continue
            if phase not in {"initializing", "prepared", "publishing", "backup_moved", "published"}:
                continue
            with _publication_lock(destination):
                # Any phase after the journal was made visible is rolled back by
                # default.  In particular, backup_moved covers a crash between
                # the two directory renames; leaving that state published would
                # expose a candidate that never reached final validation.
                if phase in {"publishing", "backup_moved", "published"} and rollback_published:
                    if backup is not None and backup.exists():
                        if destination.exists() or destination.is_symlink():
                            quarantine = transaction_root / "quarantine"
                            os.replace(destination, quarantine)
                            shutil.rmtree(quarantine, ignore_errors=True)
                        os.replace(backup, destination)
                    elif not bool(payload.get("destination_existed")) and (destination.exists() or destination.is_symlink()):
                        quarantine = transaction_root / "quarantine"
                        os.replace(destination, quarantine)
                        shutil.rmtree(quarantine, ignore_errors=True)
                if transaction_root.exists():
                    shutil.rmtree(transaction_root, ignore_errors=True)
            recovered.append(str(transaction_root))
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            # A malformed orphan is retained for operator inspection; never guess
            # at deleting a potentially live publication.
            continue
    return recovered


def prepare_evaluation_commit(
    destination: Path,
    *,
    source: Path | None = None,
    metadata: dict[str, object] | None = None,
    fault: CommitFaultInjector | None = None,
) -> PreparedEvaluationCommit:
    """Materialize an isolated candidate without touching the destination."""

    destination = Path(os.path.abspath(os.path.expanduser(os.fspath(destination))))
    source = Path(os.path.abspath(os.path.expanduser(os.fspath(source)))) if source is not None else None
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Recovery scans the destination's sibling transaction journals.  Create
    # the parent first so a new nested output path is handled exactly like an
    # already materialized bundle.
    recover_pending(destination)
    destination_existed = destination.exists() or destination.is_symlink()
    destination_fingerprint = tree_fingerprint(destination)
    source_fingerprint = tree_fingerprint(source) if source is not None else None
    transaction_root = Path(tempfile.mkdtemp(prefix=".sure-eval-txn-", dir=destination.parent))
    candidate = transaction_root / "candidate"
    prepared = PreparedEvaluationCommit(
        transaction_root=transaction_root,
        candidate_root=candidate,
        destination_root=destination,
        source_root=source,
        source_fingerprint=source_fingerprint,
        destination_fingerprint=destination_fingerprint,
        destination_existed=destination_existed,
        phase="initializing",
        metadata=dict(metadata or {}),
    )
    try:
        # Make the transaction discoverable before candidate materialization.
        # A process exit during a large copy can then be cleaned by the next
        # invocation without guessing whether a journal-less directory is live.
        _write_journal(prepared.journal_path, prepared.journal())
        _fault(fault, "initializing_journal")
        if destination_existed:
            _copy_tree(destination, candidate)
        elif source is not None:
            _copy_tree(source, candidate)
        else:
            candidate.mkdir()
        _fault(fault, "candidate_materialized")
        prepared.phase = "prepared"
        _write_journal(prepared.journal_path, prepared.journal())
        _fault(fault, "prepared_journal")
        return prepared
    except Exception:
        shutil.rmtree(transaction_root, ignore_errors=True)
        raise


def publish_evaluation_commit(
    prepared: PreparedEvaluationCommit,
    *,
    fault: CommitFaultInjector | None = None,
) -> PreparedEvaluationCommit:
    """Publish a prepared candidate with source/destination compare-and-swap."""

    if prepared.phase != "prepared":
        raise EvaluationCommitError(f"cannot publish transaction in phase {prepared.phase}")
    destination = prepared.destination_root
    with _publication_lock(destination):
        current_destination = tree_fingerprint(destination)
        if current_destination != prepared.destination_fingerprint:
            raise EvaluationCommitError(
                f"destination changed after prepare: expected={prepared.destination_fingerprint} actual={current_destination}"
            )
        if prepared.source_root is not None:
            current_source = tree_fingerprint(prepared.source_root)
            if current_source != prepared.source_fingerprint:
                raise EvaluationCommitError(
                    f"source changed after prepare: expected={prepared.source_fingerprint} actual={current_source}"
                )
        if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
            raise EvaluationCommitError(f"publication destination is not a regular directory: {destination}")
        prepared.candidate_fingerprint = tree_fingerprint(prepared.candidate_root)
        backup = prepared.transaction_root / "backup"
        # Publish intent before the first destructive rename.  Recovery can
        # therefore distinguish a clean prepared transaction from a process
        # that died in the middle of the swap.
        prepared.backup_root = backup
        prepared.phase = "publishing"
        _write_journal(prepared.journal_path, prepared.journal())
        _fault(fault, "publish_intent")
        try:
            if destination.exists():
                os.replace(destination, backup)
                _fsync_directory(destination.parent)
                _fsync_directory(prepared.transaction_root)
            # The point denotes completion of the optional backup step.  It is
            # still reached for a new destination so crash tests exercise the
            # same journal state on both publication paths.
            _fault(fault, "backup_renamed")
            prepared.phase = "backup_moved"
            _write_journal(prepared.journal_path, prepared.journal())
            _fault(fault, "backup_journal")
            os.replace(prepared.candidate_root, destination)
            _fsync_directory(destination.parent)
            _fsync_directory(prepared.transaction_root)
            _fault(fault, "candidate_renamed")
        except Exception:
            # Leave the journal and backup in place.  The caller can perform a
            # single rollback, and a process restart can recover the same state;
            # restoring here would make a subsequent rollback mistake the
            # restored original for the candidate and potentially delete it.
            raise
        prepared.phase = "published"
        _write_journal(prepared.journal_path, prepared.journal())
        _fault(fault, "published_journal")
        _fsync_directory(destination.parent)
    return prepared


def finalize_evaluation_commit(
    prepared: PreparedEvaluationCommit,
    *,
    fault: CommitFaultInjector | None = None,
) -> None:
    """Delete the retained rollback copy after final validation succeeds."""

    if prepared.phase != "published":
        raise EvaluationCommitError(f"cannot finalize transaction in phase {prepared.phase}")
    with _publication_lock(prepared.destination_root):
        if prepared.candidate_fingerprint is not None:
            current = tree_fingerprint(prepared.destination_root)
            if current != prepared.candidate_fingerprint:
                raise EvaluationCommitError("published destination changed before finalize")
        _fault(fault, "before_finalize_cleanup")
        shutil.rmtree(prepared.transaction_root)
        prepared.phase = "finalized"


def rollback_evaluation_commit(
    prepared: PreparedEvaluationCommit,
    *,
    fault: CommitFaultInjector | None = None,
) -> None:
    """Restore the pre-commit destination, including after a process restart."""

    if prepared.phase == "finalized":
        raise EvaluationCommitError("cannot roll back a finalized transaction")
    with _publication_lock(prepared.destination_root):
        destination = prepared.destination_root
        if prepared.phase in {"publishing", "backup_moved", "published"}:
            quarantine = prepared.transaction_root / "quarantine"
            if prepared.backup_root is not None and prepared.backup_root.exists():
                if destination.exists() or destination.is_symlink():
                    os.replace(destination, quarantine)
                    shutil.rmtree(quarantine, ignore_errors=True)
                os.replace(prepared.backup_root, destination)
            elif tree_fingerprint(destination) == prepared.destination_fingerprint:
                # A failed second rename may already have restored the backup;
                # the original tree is intact, so only discard transaction data.
                pass
            elif not prepared.destination_existed and (destination.exists() or destination.is_symlink()):
                os.replace(destination, quarantine)
                shutil.rmtree(quarantine, ignore_errors=True)
            elif prepared.destination_existed:
                raise EvaluationCommitError("cannot prove the pre-commit destination is recoverable")
        shutil.rmtree(prepared.transaction_root, ignore_errors=True)
        _fault(fault, "rollback_cleanup")
        prepared.phase = "rolled_back"
