#!/usr/bin/env python3
"""Deterministic audit, decision binding, and publication for /sure_approve."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[4]
EVAL_SCRIPTS = REPO_ROOT / "sure" / "skills" / "sure_eval" / "scripts"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(EVAL_SCRIPTS))

from deployment_binding import DeploymentBindingError, load_deployment_binding
from sure.site.loader import SitePolicyError, load_site_policy


APPROVAL_GENERATED = {
    "artifacts/approval_manifest.json",
    "artifacts/review_packet.json",
    "artifacts/approval_decision.json",
    "artifacts/publication_result.json",
    "artifacts/approval_ready.json",
}
EXCLUDED_DIR_NAMES = {".cache", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache"}
EXCLUDED_TOP_LEVEL = {"eval_runs", "evaluation_runs", "results"}
SUCCESS = {"pass", "passed", "success", "ready"}
SHA256 = re.compile(r"^[0-9a-f]{64}$")
IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class ApprovalError(ValueError):
    """A producer bundle cannot advance through the approval flow."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApprovalError(f"invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ApprovalError(f"artifact must be a JSON object: {path}")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def paths_overlap(left: Path, right: Path) -> bool:
    return is_inside(left, right) or is_inside(right, left)


def artifact(run_dir: Path, name: str) -> Path:
    debug = run_dir / "artifacts" / "debug" / name
    return debug if debug.is_file() else run_dir / "artifacts" / name


def source_artifact(source: Path, name: str) -> Path:
    return source / "artifacts" / name


def status_passed(value: Any) -> bool:
    return str(value or "").strip().lower() in SUCCESS


def finding(severity: str, code: str, message: str, repair: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message, "repair": repair}


def fail_findings(findings: Iterable[dict[str, Any]]) -> bool:
    return any(item.get("severity") == "error" for item in findings)


def load_active_policy() -> dict[str, Any]:
    try:
        configured = load_site_policy(required=True)
    except SitePolicyError as exc:
        raise ApprovalError(str(exc)) from exc
    assert configured is not None
    return configured


def _relative_supplied(raw: str, invocation_cwd: Path) -> tuple[str, Path]:
    supplied = raw
    path = Path(raw).expanduser()
    return supplied, (path if path.is_absolute() else invocation_cwd / path).resolve()


def _resolved_model_name(source: Path) -> str:
    marker = source_artifact(source, "deployment_ready.json")
    if marker.is_file():
        value = read_json(marker).get("model_name")
        if isinstance(value, str) and value:
            return value
    return source.name


def resolve_input(args: argparse.Namespace) -> dict[str, Any]:
    invocation_cwd = Path(args.invocation_cwd or os.environ.get("SURE_INVOCATION_CWD") or os.getcwd()).resolve()
    supplied, source = _relative_supplied(args.model_dir, invocation_cwd)
    if not source.is_dir() or not os.access(source, os.R_OK | os.X_OK):
        raise ApprovalError(f"model_dir is not a readable directory: {source}")
    configured = load_active_policy()
    policy = configured["policy"]
    configured_root = Path(policy["storage"]["approved_models_roots"][0]).resolve()
    approve_supplied = args.approve_dir or str(configured_root)
    _, approve_root = _relative_supplied(approve_supplied, invocation_cwd)
    for raw_forbidden in policy["storage"]["forbidden_output_roots"]:
        forbidden = Path(str(raw_forbidden)).resolve()
        if approve_root != configured_root and is_inside(approve_root, forbidden):
            raise ApprovalError(f"approval root is under a forbidden output root: {approve_root}")
    model_name = _resolved_model_name(source)
    destination = approve_root / model_name
    if paths_overlap(source, destination):
        raise ApprovalError(f"source and destination overlap: {source} <-> {destination}")
    review_manifest = None
    if args.review_manifest:
        _, review_manifest_path = _relative_supplied(args.review_manifest, invocation_cwd)
        review_manifest = str(review_manifest_path)
    return {
        "schema": "sure.approve.input_resolved.v1",
        "status": "passed",
        "mode": args.mode,
        "repair": args.repair,
        "source": {"supplied": supplied, "canonical": str(source)},
        "approval": {
            "supplied_root": approve_supplied,
            "root": str(approve_root),
            "destination": str(destination),
            "configured_root": str(configured_root),
            "eval_visible": approve_root == configured_root,
            "replace": args.replace,
        },
        "review_manifest": review_manifest,
        "decision": args.decision,
        "site_policy": {
            "path": configured["path"],
            "sha256": configured["sha256"],
            "site_id": policy["site_id"],
            "execution": policy["execution"],
            "runtime_root": policy["storage"]["runtime_root"],
        },
    }


def _audit_context(run_dir: Path) -> tuple[dict[str, Any], Path]:
    resolved = read_json(artifact(run_dir, "approve_input_resolved.json"))
    source_value = resolved.get("source")
    if not isinstance(source_value, dict) or not isinstance(source_value.get("canonical"), str):
        raise ApprovalError("approve_input_resolved.source.canonical is missing")
    source = Path(source_value["canonical"]).resolve()
    if not source.is_dir():
        raise ApprovalError(f"producer source no longer exists: {source}")
    return resolved, source


def classify_producer(run_dir: Path) -> dict[str, Any]:
    _, source = _audit_context(run_dir)
    findings: list[dict[str, str]] = []
    onboard = source_artifact(source, "model_input_resolved.json").is_file()
    trans = source_artifact(source, "trans_input_resolved.json").is_file()
    if onboard == trans:
        findings.append(finding("error", "PRODUCER_AMBIGUOUS", "Bundle must contain evidence for exactly one producer.", "Rerun the responsible producer and preserve its resolved input artifact."))
    producer = "sure_trans" if trans and not onboard else "sure_onboard"
    try:
        marker = read_json(source_artifact(source, "deployment_ready.json"))
        inventory = read_json(source_artifact(source, "runtime_inventory.json"))
        package = read_json(source_artifact(source, "package_gate.json"))
    except ApprovalError as exc:
        findings.append(finding("error", "TERMINAL_EVIDENCE_MISSING", str(exc), f"Rerun /{producer} to its terminal unit."))
        marker, inventory, package = {}, {}, {}
    schema = marker.get("schema")
    profile = marker.get("package_profile")
    if schema == "sure.onboard.deployment_ready.v1" and profile == "docker-registry":
        contract, runtime_kind = "docker-v1", "container"
    elif schema == "sure.onboard.deployment_ready.v2" and profile == "none":
        contract, runtime_kind = "python-v2", "python"
    else:
        contract = "python-v2" if profile == "none" else "docker-v1"
        runtime_kind = "python" if contract == "python-v2" else "container"
        findings.append(finding("error", "UNSUPPORTED_TERMINAL_CONTRACT", f"Unsupported deployment contract schema={schema!r}, package_profile={profile!r}.", f"Rerun /{producer} with docker-registry or a site-enabled sealed uv runtime."))
    model_name = marker.get("model_name")
    inventory_model = inventory.get("model") if isinstance(inventory.get("model"), dict) else {}
    package_model = package.get("model_name", model_name)
    if not isinstance(model_name, str) or not model_name:
        findings.append(finding("error", "MODEL_NAME_MISSING", "deployment_ready.model_name is missing.", f"Rerun /{producer} finalization."))
        model_name = source.name
    if source.name != model_name or inventory_model.get("name") != model_name or package_model != model_name:
        findings.append(finding("error", "MODEL_IDENTITY_MISMATCH", "Source directory, deployment, runtime inventory, and package gate model identities must agree.", f"Rerun /{producer} with model_name={model_name}."))
    if producer == "sure_trans" and contract == "python-v2":
        findings.append(finding("error", "TRANS_PYTHON_UNSUPPORTED", "Current sure_trans products require the Docker v1 registry contract.", "Rerun /sure_trans with package=docker-registry."))
    return {
        "schema": "sure.approve.producer_contract_report.v1",
        "status": "failed" if fail_findings(findings) else "passed",
        "producer": producer,
        "contract": contract,
        "runtime_kind": runtime_kind,
        "model_name": model_name,
        "deployment_schema": schema,
        "source": str(source),
        "findings": findings,
        "rerun_command": f"/{producer} model_name={model_name}",
    }


def _declared_external_targets(source: Path) -> set[tuple[str, str]]:
    declared: set[tuple[str, str]] = set()
    for name in ("weights_manifest.json", "model_payload_manifest.json"):
        path = source_artifact(source, name)
        if not path.is_file():
            continue
        value: Any = read_json(path)
        stack = [value]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                link = current.get("path") or current.get("destination")
                target = current.get("target") or current.get("link_target") or current.get("source")
                if isinstance(link, str) and isinstance(target, str):
                    declared.add((link, str(Path(target).expanduser().resolve())))
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)
    return declared


def _walk_entries(root: Path, *, publication: bool) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    entries: list[dict[str, Any]] = []
    findings: list[dict[str, str]] = []
    declared = _declared_external_targets(root)
    for path in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        excluded_from_publication = _excluded(Path(relative))
        if relative in APPROVAL_GENERATED:
            continue
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            findings.append(finding("error", "UNREADABLE_PATH", f"Cannot inspect {relative}: {exc}", "Repair filesystem access and rerun the producer."))
            continue
        if stat.S_ISDIR(mode):
            continue
        if stat.S_ISREG(mode):
            entries.append({"path": relative, "type": "file", "mode": stat.S_IMODE(mode), "size": path.stat().st_size, "sha256": sha256_file(path)})
            continue
        if stat.S_ISLNK(mode):
            target_text = os.readlink(path)
            if excluded_from_publication:
                target_bytes = os.fsencode(target_text)
                entries.append(
                    {
                        "path": relative,
                        "type": "symlink",
                        "mode": stat.S_IMODE(mode),
                        "size": len(target_bytes),
                        "sha256": sha256_bytes(target_bytes),
                        "link_target": target_text,
                    }
                )
                continue
            target = path.resolve()
            if target.is_dir():
                findings.append(finding("error", "DIRECTORY_SYMLINK", f"Directory symlink is not publishable: {relative}", "Materialize the directory in the producer bundle and rerun."))
                continue
            if not target.is_file():
                findings.append(finding("error", "BROKEN_SYMLINK", f"Symlink target is not a regular file: {relative} -> {target_text}", "Repair the producer asset reference and rerun."))
                continue
            internal = is_inside(target, root)
            explicitly_declared = (relative, str(target)) in declared
            if not internal and not explicitly_declared and not excluded_from_publication:
                findings.append(finding("error", "EXTERNAL_SYMLINK", f"External symlink is not explicitly declared: {relative} -> {target}", "Declare the immutable asset in producer evidence or materialize it before approval."))
                continue
            entries.append({"path": relative, "type": "file" if publication else "symlink", "mode": stat.S_IMODE(mode), "size": target.stat().st_size, "sha256": sha256_file(target), "link_target": target_text})
            continue
        findings.append(finding("error", "SPECIAL_FILE", f"Special file is not publishable: {relative}", "Remove sockets, devices, or FIFOs and rerun the producer."))
    return entries, findings


def tree_digest(root: Path, *, publication: bool = False) -> tuple[str, list[dict[str, Any]], list[dict[str, str]]]:
    entries, findings = _walk_entries(root, publication=publication)
    identity = [{key: item[key] for key in ("path", "type", "mode", "size", "sha256") if key in item} for item in entries]
    return sha256_bytes(canonical_json(identity)), entries, findings


def _require_hashes(source: Path, marker: dict[str, Any], findings: list[dict[str, str]], producer: str) -> None:
    declared = marker.get("required_artifact_sha256")
    if not isinstance(declared, dict) or not declared:
        findings.append(finding("error", "ARTIFACT_HASHES_MISSING", "deployment_ready has no required artifact hashes.", f"Rerun /{producer} finalization."))
        return
    verified: dict[str, str] = {}
    for relative, expected in declared.items():
        if not isinstance(relative, str) or not isinstance(expected, str) or not SHA256.fullmatch(expected):
            findings.append(finding("error", "ARTIFACT_HASH_INVALID", f"Invalid declared hash entry: {relative!r}.", f"Rerun /{producer} finalization."))
            continue
        path = (source / relative).resolve()
        if not is_inside(path, source) or not path.is_file():
            findings.append(finding("error", "ARTIFACT_PATH_INVALID", f"Required artifact is missing or escapes the bundle: {relative}.", f"Rerun /{producer} finalization."))
            continue
        actual = sha256_file(path)
        if actual != expected:
            findings.append(finding("error", "ARTIFACT_TAMPERED", f"Required artifact hash mismatch: {relative}.", f"Rerun /{producer}; approval never repairs producer evidence."))
        verified[relative] = actual
    expected_identity = sha256_bytes(canonical_json(declared))
    if marker.get("bundle_identity_sha256") != expected_identity:
        findings.append(finding("error", "BUNDLE_IDENTITY_MISMATCH", "deployment bundle identity does not match its declared hashes.", f"Rerun /{producer} finalization."))


def audit_integrity(run_dir: Path) -> dict[str, Any]:
    resolved, source = _audit_context(run_dir)
    producer_report = read_json(artifact(run_dir, "producer_contract_report.json"))
    findings = list(producer_report.get("findings") or [])
    producer = str(producer_report.get("producer") or "sure_onboard")
    marker = read_json(source_artifact(source, "deployment_ready.json"))
    inventory = read_json(source_artifact(source, "runtime_inventory.json"))
    package = read_json(source_artifact(source, "package_gate.json"))
    verdict = read_json(source_artifact(source, "verdict.json"))
    if marker.get("status") != "ready":
        findings.append(finding("error", "DEPLOYMENT_NOT_READY", f"deployment_ready.status is {marker.get('status')!r}, not ready.", f"Rerun /{producer} to terminal readiness."))
    if not status_passed(verdict.get("status")):
        findings.append(finding("error", "VERDICT_FAILED", "Producer verdict is not terminal-success.", f"Rerun /{producer} after fixing validation."))
    if inventory.get("schema") != "sure.onboard.runtime_inventory.v2" or inventory.get("status") != "ready":
        findings.append(finding("error", "RUNTIME_INVENTORY_NOT_READY", "runtime_inventory v2 is missing or not ready.", f"Rerun /{producer} runtime inventory and finalization."))
    if package.get("schema") != "sure.onboard.package_gate.v2" or package.get("status") != "passed":
        findings.append(finding("error", "PACKAGE_GATE_FAILED", "package_gate v2 is missing or not passed.", f"Rerun /{producer} packaging."))
    _require_hashes(source, marker, findings, producer)
    contract = producer_report.get("contract")
    if contract == "docker-v1":
        runtime = inventory.get("container_runtime") if isinstance(inventory.get("container_runtime"), dict) else {}
        docker = package.get("docker") if isinstance(package.get("docker"), dict) else {}
        registry_path = source_artifact(source, "docker_registry_result.json")
        registry = read_json(registry_path) if registry_path.is_file() else {}
        image = runtime.get("target_image")
        digest = runtime.get("target_image_digest")
        image_ref = runtime.get("target_image_ref")
        values = [(document.get("target_image"), document.get("target_image_digest"), document.get("target_image_ref")) for document in (marker, docker, registry)]
        if package.get("package_profile") != "docker-registry" or runtime.get("required") is not True:
            findings.append(finding("error", "CONTAINER_BINDING_INCOMPLETE", "Docker approval requires docker-registry and a required container runtime.", f"Rerun /{producer} with package=docker-registry."))
        if not isinstance(digest, str) or not IMAGE_DIGEST.fullmatch(digest) or not isinstance(image_ref, str) or not image_ref.endswith(f"@{digest}") or any(value != (image, digest, image_ref) for value in values):
            findings.append(finding("error", "IMAGE_IDENTITY_MISMATCH", "Image, digest, and digest-pinned reference do not agree across terminal evidence.", f"Rerun /{producer} registry delivery."))
        if registry.get("pull_verified") is not True:
            findings.append(finding("error", "REGISTRY_PULL_UNVERIFIED", "Registry result does not prove pull_verified=true.", f"Rerun /{producer} registry verification."))
    else:
        policy_execution = resolved.get("site_policy", {}).get("execution", {}) if isinstance(resolved.get("site_policy"), dict) else {}
        local_runtimes = policy_execution.get("local_runtimes", ["container"]) if isinstance(policy_execution, dict) else []
        surfaces = policy_execution.get("surfaces", []) if isinstance(policy_execution, dict) else []
        runtime = inventory.get("model_runtime") if isinstance(inventory.get("model_runtime"), dict) else {}
        if "python" not in local_runtimes or "local" not in surfaces:
            findings.append(finding("error", "SITE_PYTHON_DISABLED", "Active site policy does not enable local Python Eval.", "Ask a site administrator to enable execution.local_runtimes=[python] and local execution, then rerun the audit."))
        if runtime.get("required") is not True or runtime.get("backend") != "uv":
            findings.append(finding("error", "MODEL_RUNTIME_UNSEALED", "Python approval requires a sealed uv Model Runtime.", f"Rerun /{producer} with package=none and backend=uv."))
        manifest_path = source_artifact(source, "model_runtime_manifest.json")
        if not manifest_path.is_file() or runtime.get("manifest_path") != "artifacts/model_runtime_manifest.json":
            findings.append(finding("error", "MODEL_RUNTIME_MANIFEST_MISSING", "Portable Model Runtime manifest is missing or not bound.", f"Rerun /{producer} Model Runtime sealing."))
        evidence = inventory.get("evidence") if isinstance(inventory.get("evidence"), dict) else {}
        if not isinstance(evidence.get("model_core_sha256"), dict) or not evidence.get("model_core_sha256"):
            findings.append(finding("error", "MODEL_CORE_HASHES_MISSING", "Python runtime inventory has no model-core hashes.", f"Rerun /{producer} runtime inventory."))
    if producer == "sure_trans":
        for name in ("original_inference_result.json", "infer_result.json", "contract_result.json", "mcp_result.json", "equivalence_result.json"):
            path = source_artifact(source, name)
            value = read_json(path) if path.is_file() else {}
            if not status_passed(value.get("status")):
                findings.append(finding("error", "TRANS_EQUIVALENCE_INCOMPLETE", f"Trans evidence is missing or failed: {name}.", "Rerun /sure_trans through original, adapter, MCP, and equivalence validation."))
        if not source_artifact(source, "model_payload_manifest.json").is_file():
            findings.append(finding("error", "TRANS_PAYLOAD_MISSING", "Trans model_payload_manifest.json is missing.", "Rerun /sure_trans payload staging."))
    evidence = inventory.get("evidence") if isinstance(inventory.get("evidence"), dict) else {}
    model_hashes = evidence.get("model_core_sha256")
    if isinstance(model_hashes, dict):
        for relative, expected in model_hashes.items():
            path = (source / str(relative)).resolve()
            if not isinstance(expected, str) or not is_inside(path, source) or not path.is_file() or sha256_file(path) != expected:
                findings.append(finding("error", "MODEL_CORE_TAMPERED", f"Model core hash mismatch: {relative}.", f"Rerun /{producer}; approval never repairs executable model files."))
    source_digest, entries, path_findings = tree_digest(source)
    findings.extend(path_findings)
    checks = {
        "terminal_contract": not any(item.get("code") in {"DEPLOYMENT_NOT_READY", "VERDICT_FAILED", "RUNTIME_INVENTORY_NOT_READY", "PACKAGE_GATE_FAILED"} for item in findings),
        "declared_hashes": not any(str(item.get("code", "")).startswith(("ARTIFACT_", "BUNDLE_")) for item in findings),
        "path_safety": not path_findings,
        "tree_entries": len(entries),
    }
    return {"schema": "sure.approve.integrity_report.v1", "status": "failed" if fail_findings(findings) else "passed", "source": str(source), "source_digest": source_digest, "checks": checks, "findings": findings}


def plan_repairs(run_dir: Path) -> dict[str, Any]:
    resolved = read_json(artifact(run_dir, "approve_input_resolved.json"))
    integrity = read_json(artifact(run_dir, "integrity_report.json"))
    mode = resolved.get("repair", "safe")
    _, source = _audit_context(run_dir)
    repairs = _required_repairs(source)
    findings = list(integrity.get("findings") or [])
    if mode == "none" and repairs:
        findings.append(
            finding(
                "error",
                "REPAIRS_DISABLED",
                f"The publication snapshot requires bounded repairs: {', '.join(repairs)}.",
                "Rerun /sure_approve with repair=safe, or repair the producer bundle upstream.",
            )
        )
    blocked = integrity.get("status") != "passed" or (mode == "none" and bool(repairs))
    return {
        "schema": "sure.approve.repair_plan.v1",
        "status": "failed" if blocked else "passed",
        "repair_mode": mode,
        "safe_repairs": repairs if mode == "safe" else [],
        "rerun_required": blocked,
        "findings": findings,
    }


def _excluded(relative: Path) -> bool:
    parts = relative.parts
    return bool(parts) and (parts[0] in EXCLUDED_TOP_LEVEL or any(part in EXCLUDED_DIR_NAMES for part in parts) or relative.as_posix() in APPROVAL_GENERATED)


def _required_repairs(source: Path) -> list[str]:
    required: set[str] = set()
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if _excluded(relative):
            required.add("exclude_unreferenced_caches")
            continue
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            required.add("materialize_allowed_file_links")
        elif stat.S_ISDIR(mode):
            if stat.S_IMODE(mode) != 0o755:
                required.add("normalize_publication_permissions")
        elif stat.S_ISREG(mode):
            target_mode = 0o755 if mode & stat.S_IXUSR else 0o644
            if stat.S_IMODE(mode) != target_mode:
                required.add("normalize_publication_permissions")
    return sorted(required)


def _copy_candidate(source: Path, candidate: Path, *, allow_repairs: bool = True) -> list[str]:
    required = _required_repairs(source)
    if required and not allow_repairs:
        raise ApprovalError(f"candidate copy requires disabled repairs: {', '.join(required)}")
    applied: set[str] = set()
    for path in sorted(source.rglob("*"), key=lambda value: value.relative_to(source).as_posix()):
        relative = path.relative_to(source)
        if _excluded(relative):
            applied.add("exclude_unreferenced_caches")
            continue
        destination = candidate / relative
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            destination.mkdir(parents=True, exist_ok=True)
            destination.chmod(0o755)
            if stat.S_IMODE(mode) != 0o755:
                applied.add("normalize_publication_permissions")
        elif stat.S_ISREG(mode):
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            target_mode = 0o755 if mode & stat.S_IXUSR else 0o644
            destination.chmod(target_mode)
            if stat.S_IMODE(mode) != target_mode:
                applied.add("normalize_publication_permissions")
        elif stat.S_ISLNK(mode):
            target = path.resolve()
            if not target.is_file():
                raise ApprovalError(f"cannot materialize non-file link: {relative}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target, destination)
            destination.chmod(0o755 if target.stat().st_mode & stat.S_IXUSR else 0o644)
            applied.add("materialize_allowed_file_links")
    return sorted(applied)


def apply_repairs(run_dir: Path) -> dict[str, Any]:
    _, source = _audit_context(run_dir)
    integrity = read_json(artifact(run_dir, "integrity_report.json"))
    plan = read_json(artifact(run_dir, "repair_plan.json"))
    if integrity.get("status") != "passed" or plan.get("status") != "passed":
        raise ApprovalError("cannot create a candidate from a failed integrity audit")
    before, _, _ = tree_digest(source)
    if before != integrity.get("source_digest"):
        raise ApprovalError("producer source changed after integrity audit")
    producer = read_json(artifact(run_dir, "producer_contract_report.json"))
    candidate = (run_dir / "candidate" / str(producer["model_name"])).resolve()
    if candidate.exists():
        if not is_inside(candidate, run_dir / "candidate"):
            raise ApprovalError("candidate cleanup target escapes the run directory")
        shutil.rmtree(candidate)
    candidate.mkdir(parents=True)
    applied = _copy_candidate(source, candidate, allow_repairs=plan.get("repair_mode") == "safe")
    after, _, _ = tree_digest(source)
    if before != after:
        raise ApprovalError("producer source changed while creating the candidate")
    return {"schema": "sure.approve.repair_report.v1", "status": "passed", "candidate_dir": str(candidate), "source": str(source), "source_digest_before": before, "source_digest_after": after, "repairs_applied": applied if plan.get("repair_mode") == "safe" else []}


def build_manifest(run_dir: Path) -> dict[str, Any]:
    report = read_json(artifact(run_dir, "repair_report.json"))
    producer = read_json(artifact(run_dir, "producer_contract_report.json"))
    candidate = Path(str(report["candidate_dir"])).resolve()
    digest, entries, findings = tree_digest(candidate, publication=True)
    if findings:
        raise ApprovalError(findings[0]["message"])
    return {"schema": "sure.approve.approval_manifest.v1", "status": "passed", "generated_at": now_iso(), "model_name": producer["model_name"], "producer": producer["producer"], "contract": producer["contract"], "runtime_kind": producer["runtime_kind"], "candidate_dir": str(candidate), "candidate_digest": digest, "files": entries}


def _packet_digest(packet: dict[str, Any]) -> str:
    normalized = dict(packet)
    normalized.pop("packet_digest", None)
    return sha256_bytes(canonical_json(normalized))


def build_review(run_dir: Path) -> dict[str, Any]:
    resolved = read_json(artifact(run_dir, "approve_input_resolved.json"))
    producer = read_json(artifact(run_dir, "producer_contract_report.json"))
    integrity = read_json(artifact(run_dir, "integrity_report.json"))
    repairs = read_json(artifact(run_dir, "repair_report.json"))
    manifest = read_json(artifact(run_dir, "approval_manifest.json"))
    runtime = read_json(artifact(run_dir, "runtime_verification.json"))
    approval_manifest_path = artifact(run_dir, "approval_manifest.json").resolve()
    packet = {
        "schema": "sure.approve.review_packet.v1",
        "status": "awaiting_approval",
        "generated_at": now_iso(),
        "source": resolved["source"],
        "approval": resolved["approval"],
        "model_name": producer["model_name"],
        "producer": producer["producer"],
        "producer_contract": producer["contract"],
        "runtime_kind": producer["runtime_kind"],
        "candidate_dir": manifest["candidate_dir"],
        "candidate_digest": manifest["candidate_digest"],
        "approval_manifest": str(approval_manifest_path),
        "approval_manifest_sha256": sha256_file(approval_manifest_path),
        "source_digest": repairs["source_digest_after"],
        "site_policy_path": resolved["site_policy"]["path"],
        "site_policy_sha256": resolved["site_policy"]["sha256"],
        "runtime_verification": runtime,
        "repairs_applied": repairs["repairs_applied"],
        "findings": integrity.get("findings", []),
        "decision_required": True,
    }
    packet["packet_digest"] = _packet_digest(packet)
    return packet


def verify_runtime(run_dir: Path) -> dict[str, Any]:
    manifest = read_json(artifact(run_dir, "approval_manifest.json"))
    resolved = read_json(artifact(run_dir, "approve_input_resolved.json"))
    candidate = Path(str(manifest["candidate_dir"])).resolve()
    current_digest, _, findings = tree_digest(candidate, publication=True)
    if findings or current_digest != manifest.get("candidate_digest"):
        raise ApprovalError("candidate changed after sealing")
    previous_policy = os.environ.get("SURE_SITE_POLICY")
    os.environ["SURE_SITE_POLICY"] = str(resolved["site_policy"]["path"])
    try:
        binding = load_deployment_binding(candidate, str(manifest["model_name"]))
    except DeploymentBindingError as exc:
        raise ApprovalError(str(exc)) from exc
    finally:
        if previous_policy is None:
            os.environ.pop("SURE_SITE_POLICY", None)
        else:
            os.environ["SURE_SITE_POLICY"] = previous_policy
    runtime_kind = binding.get("runtime_kind")
    if runtime_kind == "python":
        python = str(binding["python"]["python_executable"])
        validate = candidate / "validate.py"
        if not validate.is_file():
            raise ApprovalError("Python candidate has no validate.py runtime smoke entrypoint")
        completed = subprocess.run(
            [python, str(validate)],
            cwd=candidate,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env={**os.environ, "MODEL_DIR": str(candidate), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        smoke = {"command": [python, "validate.py"], "exit_code": completed.returncode, "stdout_sha256": sha256_bytes(completed.stdout.encode()), "stderr": completed.stderr[-2000:]}
    else:
        image_ref = str(binding.get("target_image_ref") or "")
        pull = subprocess.run(["docker", "pull", image_ref], capture_output=True, text=True, timeout=1800, check=False)
        if pull.returncode != 0:
            raise ApprovalError(f"candidate image pull verification failed: {pull.stderr[-2000:]}")
        container = binding["container"]
        model_target = str(container["model_mount"]["target"])
        command = [
            "docker",
            "run",
            "--rm",
            "--mount",
            f"type=bind,src={candidate},dst={model_target},readonly",
            "--workdir",
            str(container["working_dir"]),
        ]
        if container.get("gpu_required") is True:
            command.extend(["--gpus", "all"])
        command.extend([image_ref, str(container["python_executable"]), f"{model_target}/validate.py"])
        completed = subprocess.run(command, capture_output=True, text=True, timeout=1800, check=False)
        smoke = {"command": command, "pull_stdout_sha256": sha256_bytes(pull.stdout.encode()), "exit_code": completed.returncode, "stdout_sha256": sha256_bytes(completed.stdout.encode()), "stderr": completed.stderr[-2000:]}
    if completed.returncode != 0:
        raise ApprovalError(f"candidate runtime verification failed: {smoke['stderr']}")
    after, _, _ = tree_digest(candidate, publication=True)
    if after != current_digest:
        raise ApprovalError("runtime verification mutated the candidate bundle")
    return {"schema": "sure.approve.runtime_verification.v1", "status": "passed", "verified_at": now_iso(), "runtime_kind": runtime_kind, "candidate_digest_before": current_digest, "candidate_digest_after": after, "binding": binding, "smoke": smoke}


def verify_decision(review_path: Path, decision: str, rationale: str | None) -> dict[str, Any]:
    review_path = review_path.expanduser().resolve()
    packet = read_json(review_path)
    expected_packet = _packet_digest(packet)
    if packet.get("status") != "awaiting_approval" or packet.get("packet_digest") != expected_packet:
        raise ApprovalError("review packet is invalid or changed")
    if packet.get("schema") != "sure.approve.review_packet.v1":
        raise ApprovalError("review packet has an unsupported schema")
    candidate = Path(str(packet.get("candidate_dir") or "")).resolve()
    digest, _, findings = tree_digest(candidate, publication=True)
    if findings or digest != packet.get("candidate_digest"):
        raise ApprovalError("review candidate changed after audit")
    manifest_path = Path(str(packet.get("approval_manifest") or "")).resolve()
    manifest = read_json(manifest_path)
    if (
        manifest.get("schema") != "sure.approve.approval_manifest.v1"
        or manifest.get("candidate_dir") != str(candidate)
        or manifest.get("candidate_digest") != digest
        or manifest.get("model_name") != packet.get("model_name")
        or sha256_file(manifest_path) != packet.get("approval_manifest_sha256")
    ):
        raise ApprovalError("review packet no longer matches its approval manifest")
    source_value = packet.get("source") if isinstance(packet.get("source"), dict) else {}
    source = Path(str(source_value.get("canonical") or "")).resolve()
    source_digest, _, source_findings = tree_digest(source)
    if source_findings or source_digest != packet.get("source_digest"):
        raise ApprovalError("producer source changed after audit")
    policy = load_active_policy()
    if policy["sha256"] != packet.get("site_policy_sha256"):
        raise ApprovalError("active site policy changed after audit; rerun audit")
    if decision not in {"approve", "reject"}:
        raise ApprovalError("decision must be explicitly approve or reject")
    return {
        "schema": "sure.approve.approval_decision.v1",
        "status": "approved" if decision == "approve" else "rejected",
        "decision": decision,
        "decided_at": now_iso(),
        "review_packet": str(review_path),
        "review_packet_digest": expected_packet,
        "candidate_digest": digest,
        "actor": {"os_user": getpass.getuser(), "uid": os.getuid(), "gid": os.getgid()},
        "rationale": rationale or "",
        "accepted_exceptions": [],
    }


def _fsync_tree(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
    for path in sorted((item for item in root.rglob("*") if item.is_dir()), reverse=True):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def publish(run_dir: Path, replace: bool) -> dict[str, Any]:
    decision = read_json(artifact(run_dir, "approval_decision.json"))
    if decision.get("status") != "approved" or decision.get("decision") != "approve":
        raise ApprovalError("publication requires an approved explicit decision")
    review_path = Path(str(decision.get("review_packet") or "")).resolve()
    packet = read_json(review_path)
    if (
        packet.get("schema") != "sure.approve.review_packet.v1"
        or packet.get("status") != "awaiting_approval"
        or packet.get("packet_digest") != decision.get("review_packet_digest")
        or _packet_digest(packet) != decision.get("review_packet_digest")
    ):
        raise ApprovalError("approval decision no longer matches the review packet")
    policy = load_active_policy()
    if policy["sha256"] != packet.get("site_policy_sha256"):
        raise ApprovalError("active site policy changed after approval")
    source_value = packet.get("source") if isinstance(packet.get("source"), dict) else {}
    source = Path(str(source_value.get("canonical") or "")).resolve()
    source_digest, _, source_findings = tree_digest(source)
    if source_findings or source_digest != packet.get("source_digest"):
        raise ApprovalError("producer source changed before publication")
    candidate = Path(str(packet["candidate_dir"])).resolve()
    candidate_digest, _, findings = tree_digest(candidate, publication=True)
    if findings or candidate_digest != decision.get("candidate_digest"):
        raise ApprovalError("approved candidate changed before publication")
    manifest_path = Path(str(packet.get("approval_manifest") or "")).resolve()
    manifest = read_json(manifest_path)
    if (
        manifest.get("candidate_dir") != str(candidate)
        or manifest.get("candidate_digest") != candidate_digest
        or manifest.get("model_name") != packet.get("model_name")
        or sha256_file(manifest_path) != packet.get("approval_manifest_sha256")
    ):
        raise ApprovalError("approval manifest changed before publication")
    approval = packet.get("approval") if isinstance(packet.get("approval"), dict) else {}
    root = Path(str(approval.get("root") or "")).resolve()
    destination = Path(str(approval.get("destination") or "")).resolve()
    if destination.parent != root or destination.name != packet.get("model_name"):
        raise ApprovalError("review packet contains an invalid publication destination")
    if paths_overlap(candidate, destination):
        raise ApprovalError("candidate and destination overlap")
    root.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not replace:
        raise ApprovalError(f"publication destination already exists: {destination}")
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.approve-", dir=root))
    backup: Path | None = None
    try:
        _copy_candidate(candidate, staging)
        staged_digest, _, staged_findings = tree_digest(staging, publication=True)
        if staged_findings or staged_digest != candidate_digest:
            raise ApprovalError("staging copy does not match the approved candidate")
        artifacts = staging / "artifacts"
        artifacts.mkdir(exist_ok=True)
        shutil.copyfile(manifest_path, artifacts / "approval_manifest.json")
        shutil.copyfile(review_path, artifacts / "review_packet.json")
        shutil.copyfile(artifact(run_dir, "approval_decision.json"), artifacts / "approval_decision.json")
        result = {
            "schema": "sure.approve.publication_result.v1",
            "status": "passed",
            "published_at": now_iso(),
            "destination": str(destination),
            "candidate_digest": candidate_digest,
            "eval_visible": bool(approval.get("eval_visible")),
            "temporary_path": str(staging),
            "replaced": destination.exists(),
        }
        atomic_json(artifacts / "publication_result.json", result)
        _fsync_tree(staging)
        if destination.exists():
            backup = root / f".{destination.name}.replaced-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            destination.rename(backup)
        staging.rename(destination)
        descriptor = os.open(root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        result["temporary_path"] = None
        result["backup_path"] = str(backup) if backup else None
        atomic_json(destination / "artifacts" / "publication_result.json", result)
        return result
    except Exception:
        if backup is not None and not destination.exists() and backup.exists():
            backup.rename(destination)
        raise


def verify_publication(run_dir: Path) -> dict[str, Any]:
    publication = read_json(artifact(run_dir, "publication_result.json"))
    destination = Path(str(publication.get("destination") or "")).resolve()
    digest, _, findings = tree_digest(destination, publication=True)
    if findings or digest != publication.get("candidate_digest"):
        raise ApprovalError("published bundle does not match the approved candidate")
    decision = read_json(destination / "artifacts" / "approval_decision.json")
    packet = read_json(destination / "artifacts" / "review_packet.json")
    packet_digest = _packet_digest(packet)
    if (
        packet.get("schema") != "sure.approve.review_packet.v1"
        or packet.get("status") != "awaiting_approval"
        or packet.get("packet_digest") != packet_digest
        or packet.get("candidate_digest") != digest
    ):
        raise ApprovalError("published review packet is invalid or does not match the bundle")
    if (
        decision.get("schema") != "sure.approve.approval_decision.v1"
        or decision.get("status") != "approved"
        or decision.get("decision") != "approve"
        or decision.get("review_packet_digest") != packet_digest
        or decision.get("candidate_digest") != digest
    ):
        raise ApprovalError("published approval decision is invalid or does not match the bundle")
    approval = packet.get("approval") if isinstance(packet.get("approval"), dict) else {}
    if (
        destination != Path(str(approval.get("destination") or "")).resolve()
        or bool(publication.get("eval_visible")) != bool(approval.get("eval_visible"))
        or sha256_file(destination / "artifacts" / "approval_manifest.json")
        != packet.get("approval_manifest_sha256")
    ):
        raise ApprovalError("published destination or approval manifest does not match the review packet")
    previous_policy = os.environ.get("SURE_SITE_POLICY")
    os.environ["SURE_SITE_POLICY"] = str(packet["site_policy_path"])
    try:
        policy = load_active_policy()
        if policy["sha256"] != packet.get("site_policy_sha256"):
            raise ApprovalError("active site policy changed before publication verification")
        binding = load_deployment_binding(destination, str(packet["model_name"]))
    except DeploymentBindingError as exc:
        raise ApprovalError(f"published bundle is not consumable by sure_eval: {exc}") from exc
    finally:
        if previous_policy is None:
            os.environ.pop("SURE_SITE_POLICY", None)
        else:
            os.environ["SURE_SITE_POLICY"] = previous_policy
    ready = {"schema": "sure.approve.approval_ready.v1", "status": "ready", "verified_at": now_iso(), "destination": str(destination), "candidate_digest": digest, "eval_visible": bool(publication.get("eval_visible")), "deployment_binding": binding, "approval_decision_sha256": sha256_file(destination / "artifacts" / "approval_decision.json"), "publication_result_sha256": sha256_file(destination / "artifacts" / "publication_result.json")}
    atomic_json(destination / "artifacts" / "approval_ready.json", ready)
    return ready


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--produces", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    return parser


def _checked(path: Path, statuses: set[str], schema: str) -> int:
    try:
        value = read_json(path)
        if value.get("schema") != schema:
            raise ApprovalError(f"artifact schema must be {schema}")
        if value.get("status") not in statuses:
            raise ApprovalError(f"artifact status must be one of {sorted(statuses)}")
    except ApprovalError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _write_result(path: Path, producer: Any, nonzero_statuses: frozenset[str] = frozenset({"failed", "rejected"})) -> int:
    try:
        value = producer()
        atomic_json(path.resolve(), value)
    except (ApprovalError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if value.get("status") in nonzero_statuses:
        for item in value.get("findings", []):
            if isinstance(item, dict) and item.get("severity") == "error":
                print(item.get("message", "approval gate failed"), file=sys.stderr)
        return 1
    return 0


def main_resolve() -> int:
    parser = _common_parser()
    parser.add_argument("--model-dir", required=False)
    parser.add_argument("--approve-dir")
    parser.add_argument("--mode", choices=("audit", "approve"), default="audit")
    parser.add_argument("--repair", choices=("safe", "none"), default="safe")
    parser.add_argument("--review-manifest")
    parser.add_argument("--decision", choices=("approve", "reject"))
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--invocation-cwd")
    args = parser.parse_args()
    if args.check:
        return _checked(args.produces, {"passed"}, "sure.approve.input_resolved.v1")
    if not args.model_dir:
        parser.error("--model-dir is required in audit mode")
    return _write_result(args.produces, lambda: resolve_input(args))


def main_audit() -> int:
    parser = _common_parser()
    parser.add_argument("--kind", choices=("producer", "integrity"), required=True)
    args = parser.parse_args()
    if args.check:
        schema = "sure.approve.producer_contract_report.v1" if args.kind == "producer" else "sure.approve.integrity_report.v1"
        return _checked(args.produces, {"passed"}, schema)
    return _write_result(args.produces, lambda: classify_producer(args.run_dir.resolve()) if args.kind == "producer" else audit_integrity(args.run_dir.resolve()))


def main_plan() -> int:
    args = _common_parser().parse_args()
    return _checked(args.produces, {"passed"}, "sure.approve.repair_plan.v1") if args.check else _write_result(args.produces, lambda: plan_repairs(args.run_dir.resolve()))


def main_apply() -> int:
    args = _common_parser().parse_args()
    return _checked(args.produces, {"passed"}, "sure.approve.repair_report.v1") if args.check else _write_result(args.produces, lambda: apply_repairs(args.run_dir.resolve()))


def main_manifest() -> int:
    parser = _common_parser()
    parser.add_argument("--kind", choices=("manifest", "review"), required=True)
    args = parser.parse_args()
    statuses = {"passed"} if args.kind == "manifest" else {"awaiting_approval"}
    if args.check:
        schema = "sure.approve.approval_manifest.v1" if args.kind == "manifest" else "sure.approve.review_packet.v1"
        return _checked(args.produces, statuses, schema)
    return _write_result(args.produces, lambda: build_manifest(args.run_dir.resolve()) if args.kind == "manifest" else build_review(args.run_dir.resolve()))


def main_runtime() -> int:
    args = _common_parser().parse_args()
    return _checked(args.produces, {"passed"}, "sure.approve.runtime_verification.v1") if args.check else _write_result(args.produces, lambda: verify_runtime(args.run_dir.resolve()))


def main_decision() -> int:
    parser = _common_parser()
    parser.add_argument("--review-manifest")
    parser.add_argument("--decision", choices=("approve", "reject"))
    parser.add_argument("--rationale")
    args = parser.parse_args()
    if args.check:
        return _checked(args.produces, {"approved", "rejected"}, "sure.approve.approval_decision.v1")
    if not args.review_manifest or not args.decision:
        parser.error("--review-manifest and --decision are required")
    return _write_result(
        args.produces,
        lambda: verify_decision(Path(args.review_manifest), args.decision, args.rationale),
        nonzero_statuses=frozenset({"failed"}),
    )


def main_publish() -> int:
    parser = _common_parser()
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    return _checked(args.produces, {"passed"}, "sure.approve.publication_result.v1") if args.check else _write_result(args.produces, lambda: publish(args.run_dir.resolve(), args.replace))


def main_verify_publication() -> int:
    args = _common_parser().parse_args()
    return _checked(args.produces, {"ready"}, "sure.approve.approval_ready.v1") if args.check else _write_result(args.produces, lambda: verify_publication(args.run_dir.resolve()))
