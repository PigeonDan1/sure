#!/usr/bin/env python3
"""Read-only semantic checks for approval evidence.

The legacy approval commands create evidence as part of the Pi lifecycle. This
entrypoint is intentionally different: it never writes the target artifact.
It recomputes deterministic audit evidence, or checks the immutable links in
dynamic human-review evidence, and exits non-zero on any mismatch.
"""

from __future__ import annotations

import argparse
import json
import stat
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any

import approval_core


KINDS = {
    "input_resolved",
    "producer",
    "integrity",
    "repair_plan",
    "manifest",
    "review",
    "decision",
}


def _read_target(path: Path) -> dict[str, Any]:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise approval_core.ApprovalError(f"approval evidence is unavailable: {path}: {exc}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise approval_core.ApprovalError(f"approval evidence must be a regular file: {path}")
    return approval_core.read_json(path)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _assert_equal(actual: Any, expected: Any, label: str) -> None:
    if _canonical(actual) != _canonical(expected):
        raise approval_core.ApprovalError(f"{label} does not match recomputed approval evidence")


def _check_input(run_dir: Path, actual: dict[str, Any]) -> None:
    source_value = actual.get("source")
    if not isinstance(source_value, dict) or not isinstance(source_value.get("canonical"), str):
        raise approval_core.ApprovalError("resolved approval input has no canonical source")
    approval = actual.get("approval") if isinstance(actual.get("approval"), dict) else {}
    args = Namespace(
        invocation_cwd=str(Path(source_value["canonical"]).resolve().parent),
        model_dir=str(source_value["canonical"]),
        mode=actual.get("mode", "audit"),
        repair=actual.get("repair", "safe"),
        review_manifest=actual.get("review_manifest"),
        decision=actual.get("decision"),
        replace=bool(approval.get("replace", False)),
    )
    expected = approval_core.resolve_input(args)
    # ``source.supplied`` is presentation provenance and may intentionally be
    # relative to the caller's invocation directory. All resolved bindings are
    # compared exactly. A run-bound policy snapshot may expose the original
    # source path through a different snapshot location, so the immutable
    # policy digest is authoritative while the artifact's path is retained as
    # provenance.
    expected["source"]["supplied"] = source_value.get("supplied")
    actual_policy = actual.get("site_policy") if isinstance(actual.get("site_policy"), dict) else {}
    expected_policy = expected.get("site_policy") if isinstance(expected.get("site_policy"), dict) else {}
    if actual_policy.get("sha256") == expected_policy.get("sha256"):
        expected["site_policy"] = {**expected_policy, "path": actual_policy.get("path")}
    _assert_equal(actual, expected, "approve_input_resolved.json")


def _check_deterministic(run_dir: Path, actual: dict[str, Any], kind: str) -> None:
    if kind == "producer":
        expected = approval_core.classify_producer(run_dir)
        label = "producer_contract_report.json"
    elif kind == "integrity":
        expected = approval_core.audit_integrity(run_dir)
        label = "integrity_report.json"
    elif kind == "repair_plan":
        expected = approval_core.plan_repairs(run_dir)
        label = "repair_plan.json"
    else:  # pragma: no cover - guarded by dispatch
        raise approval_core.ApprovalError(f"unsupported deterministic approval kind: {kind}")
    _assert_equal(actual, expected, label)


def _check_manifest(run_dir: Path, actual: dict[str, Any]) -> None:
    if actual.get("schema") != "sure.approve.approval_manifest.v1" or actual.get("status") != "passed":
        raise approval_core.ApprovalError("approval manifest is not a passed v1 artifact")
    generated_at = actual.get("generated_at")
    if not isinstance(generated_at, str) or not generated_at:
        raise approval_core.ApprovalError("approval manifest has no generated_at timestamp")
    report = approval_core.read_json(approval_core.artifact(run_dir, "repair_report.json"))
    producer = approval_core.read_json(approval_core.artifact(run_dir, "producer_contract_report.json"))
    candidate = Path(str(report.get("candidate_dir") or "")).resolve()
    digest, entries, findings = approval_core.tree_digest(candidate, publication=True)
    if findings:
        raise approval_core.ApprovalError(findings[0]["message"])
    expected = {
        "schema": "sure.approve.approval_manifest.v1",
        "status": "passed",
        "model_name": producer.get("model_name"),
        "producer": producer.get("producer"),
        "contract": producer.get("contract"),
        "runtime_kind": producer.get("runtime_kind"),
        "candidate_dir": str(candidate),
        "candidate_digest": digest,
        "files": entries,
    }
    actual_without_time = {key: value for key, value in actual.items() if key != "generated_at"}
    _assert_equal(actual_without_time, expected, "approval_manifest.json")


def _check_review(run_dir: Path, actual: dict[str, Any]) -> None:
    if actual.get("schema") != "sure.approve.review_packet.v1" or actual.get("status") != "awaiting_approval":
        raise approval_core.ApprovalError("review packet is not awaiting explicit approval")
    if actual.get("packet_digest") != approval_core._packet_digest(actual):
        raise approval_core.ApprovalError("review packet digest is invalid")
    candidate = Path(str(actual.get("candidate_dir") or "")).resolve()
    candidate_digest, _, candidate_findings = approval_core.tree_digest(candidate, publication=True)
    if candidate_findings or candidate_digest != actual.get("candidate_digest"):
        raise approval_core.ApprovalError("review candidate changed after audit")
    manifest_path = Path(str(actual.get("approval_manifest") or "")).resolve()
    manifest = approval_core.read_json(manifest_path)
    if (
        manifest.get("candidate_dir") != str(candidate)
        or manifest.get("candidate_digest") != candidate_digest
        or approval_core.sha256_file(manifest_path) != actual.get("approval_manifest_sha256")
    ):
        raise approval_core.ApprovalError("review packet no longer matches its approval manifest")
    source_value = actual.get("source") if isinstance(actual.get("source"), dict) else {}
    source = Path(str(source_value.get("canonical") or "")).resolve()
    source_digest, _, source_findings = approval_core.tree_digest(source)
    if source_findings or source_digest != actual.get("source_digest"):
        raise approval_core.ApprovalError("review source changed after audit")
    policy = approval_core.load_active_policy()
    if policy["sha256"] != actual.get("site_policy_sha256"):
        raise approval_core.ApprovalError("active site policy changed after audit")
    # Recompute all stable fields through the legacy semantic function. Its
    # timestamp and packet digest are intentionally excluded from this compare.
    expected = approval_core.build_review(run_dir)
    stable_actual = {key: value for key, value in actual.items() if key not in {"generated_at", "packet_digest"}}
    stable_expected = {key: value for key, value in expected.items() if key not in {"generated_at", "packet_digest"}}
    _assert_equal(stable_actual, stable_expected, "review_packet.json")


def _check_decision(run_dir: Path, actual: dict[str, Any]) -> None:
    if actual.get("schema") != "sure.approve.approval_decision.v1":
        raise approval_core.ApprovalError("approval decision schema is invalid")
    review_path = Path(str(actual.get("review_packet") or "")).resolve()
    decision = actual.get("decision")
    if decision not in {"approve", "reject"}:
        raise approval_core.ApprovalError("approval decision must be approve or reject")
    expected = approval_core.verify_decision(review_path, decision, str(actual.get("rationale") or ""))
    dynamic = {"decided_at", "actor"}
    _assert_equal(
        {key: value for key, value in actual.items() if key not in dynamic},
        {key: value for key, value in expected.items() if key not in dynamic},
        "approval_decision.json",
    )
    if not isinstance(actual.get("decided_at"), str) or not isinstance(actual.get("actor"), dict):
        raise approval_core.ApprovalError("approval decision is missing human decision provenance")


def check(run_dir: Path, produces: Path, kind: str) -> None:
    actual = _read_target(produces.resolve())
    if kind == "input_resolved":
        _check_input(run_dir, actual)
    elif kind in {"producer", "integrity", "repair_plan"}:
        _check_deterministic(run_dir, actual, kind)
    elif kind == "manifest":
        _check_manifest(run_dir, actual)
    elif kind == "review":
        _check_review(run_dir, actual)
    elif kind == "decision":
        _check_decision(run_dir, actual)
    else:  # pragma: no cover - argparse prevents this
        raise approval_core.ApprovalError(f"unsupported approval artifact kind: {kind}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--produces", required=True, type=Path)
    parser.add_argument("--kind", choices=sorted(KINDS), default="input_resolved")
    args = parser.parse_args()
    try:
        check(args.run_dir.resolve(), args.produces, args.kind)
    except (approval_core.ApprovalError, OSError, KeyError, TypeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
