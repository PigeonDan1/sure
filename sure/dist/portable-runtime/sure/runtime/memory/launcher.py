#!/usr/bin/env python3
"""Host-neutral entry point for the SURE memory writer lifecycle.

This is a compatibility adapter around the existing, heavily tested publish,
promote and index algorithms.  It supplies explicit workspace roots through
``paths.memory_workspace`` and emits one redacted receipt for a portable host.
The adapter never writes workflow checkpoints and its result is advisory; a
memory failure therefore cannot manufacture or revoke a SURE workflow PASS.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import sys
from pathlib import Path
from typing import Callable, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory import index, paths, promote, publish

RECEIPT_SCHEMA = "sure.memory.writer_receipt.v1"


def _digest_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _workspace_fingerprint(workspace: paths.MemoryWorkspace) -> dict[str, str]:
    """Expose stable identity without putting host paths in a portable receipt."""

    return {
        "repo_root": _digest_text(str(workspace.repo_root)),
        "memory_root": _digest_text(str(workspace.memory_root)),
        "canonical_root": _digest_text(str(workspace.canonical_root)),
        "legacy_skills_root": _digest_text(str(workspace.legacy_skills_root)),
    }


def _parse_summary(stdout: str) -> dict | None:
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return None


def _invoke(operation: str, callback: Callable[[], int], workspace: paths.MemoryWorkspace) -> int:
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = 1
    exception_name: str | None = None
    with paths.memory_workspace(workspace), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            code = int(callback())
        except Exception as exc:  # noqa: BLE001 - the receipt must stay machine-readable
            exception_name = type(exc).__name__
            code = 1

    summary = _parse_summary(stdout.getvalue())
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "operation": operation,
        "status": "SUCCEEDED" if code == 0 and exception_name is None else "FAILED",
        "exit_code": code,
        "workflow_disposition": "NO_EFFECT",
        "advisory": True,
        "workspace": _workspace_fingerprint(workspace),
        "stdout_digest": _digest_text(stdout.getvalue()),
        "stderr_digest": _digest_text(stderr.getvalue()),
        "stdout_line_count": len(stdout.getvalue().splitlines()),
        "stderr_line_count": len(stderr.getvalue().splitlines()),
        "summary": summary,
    }
    if exception_name is not None:
        receipt["reason_code"] = "MEMORY_WRITER_CRASHED"
        receipt["exception_type"] = exception_name
    elif code != 0:
        receipt["reason_code"] = "MEMORY_WRITE_FAILED"
    else:
        receipt["reason_code"] = "MEMORY_WRITE_SUCCEEDED"
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return code


def _workspace_from_args(args: argparse.Namespace) -> paths.MemoryWorkspace:
    reference_roots = list(args.reference_root or [])
    for name in ("SURE_REFERENCE_ROOT", "REFERENCE_ROOT"):
        value = os.environ.get(name)
        if value:
            reference_roots.extend(part for part in value.split(os.pathsep) if part)
    read_order = tuple(args.read_order.split(",")) if args.read_order else ("legacy", "canonical")
    return paths.make_memory_workspace(
        Path(args.repo_root),
        memory_root_override=Path(args.memory_root) if args.memory_root else None,
        canonical_root=Path(args.canonical_root) if args.canonical_root else None,
        legacy_skills_root=Path(args.legacy_skills_root) if args.legacy_skills_root else None,
        write_root=args.write_root,
        read_order=read_order,
        reference_roots=(Path(root) for root in reference_roots),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memory-launcher.py",
        description="Run the host-neutral SURE memory writer with explicit roots.",
    )
    parser.add_argument("--repo-root", required=True, help="logical checkout/workspace identity")
    parser.add_argument("--memory-root", help="writable memory state root")
    parser.add_argument("--canonical-root", help="canonical reference root")
    parser.add_argument("--legacy-skills-root", help="legacy reference alias root")
    parser.add_argument("--write-root", choices=("legacy", "canonical"), default="legacy")
    parser.add_argument(
        "--read-order",
        default="legacy,canonical",
        help="reference alias order, either legacy,canonical or canonical,legacy",
    )
    parser.add_argument(
        "--reference-root",
        action="append",
        help="read-only production/reference prefix; writable roots inside it are rejected",
    )
    sub = parser.add_subparsers(dest="operation", required=True)
    publish_parser = sub.add_parser("publish", help="publish extraction candidates and rebuild memory")
    publish_parser.add_argument("--run-dir", required=True)
    publish_parser.add_argument("--no-promote", action="store_true")
    index_parser = sub.add_parser("index", help="check or rebuild the merged memory index")
    index_mode = index_parser.add_mutually_exclusive_group(required=True)
    index_mode.add_argument("--check", action="store_true")
    index_mode.add_argument("--rebuild", action="store_true")
    promote_parser = sub.add_parser("promote", help="replay usage and apply promotion rules")
    promote_parser.add_argument("--no-rebuild-index", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        workspace = _workspace_from_args(args)
    except (OSError, ValueError) as exc:
        # Keep admission failures machine-readable and do not echo the rejected
        # path, which may be a production location.
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "operation": args.operation,
            "status": "NOT_EXECUTED",
            "exit_code": 2,
            "workflow_disposition": "NO_EFFECT",
            "advisory": True,
            "reason_code": "CAPABILITY_MISSING",
            "exception_type": type(exc).__name__,
        }
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        return 2

    if args.operation == "publish":
        return _invoke(
            "publish",
            lambda: publish.main(
                [
                    "--run-dir",
                    args.run_dir,
                    "--repo-root",
                    str(workspace.repo_root),
                    *( ["--no-promote"] if args.no_promote else []),
                ]
            ),
            workspace,
        )
    if args.operation == "index":
        mode = "--check" if args.check else "--rebuild"
        return _invoke(
            "index",
            lambda: index.main(["--repo-root", str(workspace.repo_root), mode]),
            workspace,
        )
    return _invoke(
        "promote",
        lambda: promote.main(
            [
                "--repo-root",
                str(workspace.repo_root),
                *( ["--no-rebuild-index"] if args.no_rebuild_index else []),
            ]
        ),
        workspace,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
