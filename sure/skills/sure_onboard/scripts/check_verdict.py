#!/usr/bin/env python3
"""Gate script for the VERDICT unit.

Verifies the verdict status is a terminal value. A success/passed verdict
requires build.success, all four validation tests passed, and readiness that
matches the selected package profile:

  - local package=docker-registry: local/docker/registry/bundle readiness
  - local package=none: local readiness is sufficient for Python Eval
  - local package=docker-local: terminal status must remain partial
  - API package=none: local client readiness is sufficient

VC/HPC readiness is intentionally not part of this core /sure_onboard gate.

Called by the Sure hook:
    python3 scripts/check_verdict.py --run-dir <runDir> --produces <abs>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

FOUR_TESTS = ["import_test", "load_test", "infer_test", "contract_test"]
PACKAGE_PROFILES = {"none", "docker-local", "docker-registry"}
SUCCESS_STATUSES = {"passed", "success", "PASS", "PASSED", "pass"}
TERMINAL_STATUSES = SUCCESS_STATUSES | {"failed", "partial", "FAIL", "fail"}


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must be a JSON object.")
    return data


def package_gate_from_run(run_dir: Path) -> tuple[dict, str | None]:
    path = run_dir / "artifacts" / "package_gate.json"
    if not path.exists():
        return {}, None
    try:
        return read_json(path), None
    except ValueError as exc:
        return {}, str(exc)


def package_profile(data: dict, package_gate: dict) -> str:
    pkg = data.get("package")
    if isinstance(pkg, dict) and isinstance(pkg.get("profile"), str):
        return pkg["profile"]
    if isinstance(data.get("package_profile"), str):
        return str(data["package_profile"])
    if isinstance(package_gate.get("package_profile"), str):
        return str(package_gate["package_profile"])
    return "none"


def readiness_from(data: dict, package_gate: dict) -> dict:
    readiness = data.get("readiness")
    if isinstance(readiness, dict):
        return readiness

    gate_readiness = package_gate.get("readiness")
    if isinstance(gate_readiness, dict):
        return gate_readiness

    local_deployment = data.get("local_deployment")
    if isinstance(local_deployment, dict):
        docker = local_deployment.get("docker") if isinstance(local_deployment.get("docker"), dict) else {}
        registry = local_deployment.get("registry") if isinstance(local_deployment.get("registry"), dict) else {}
        return {
            "local_ready": local_deployment.get("status") == "passed"
            or local_deployment.get("phase1_local_validation_passed") is True,
            "docker_ready": docker.get("build_passed") is True and docker.get("validate_passed") is True,
            "registry_ready": registry.get("push_passed") is True and registry.get("pull_verify_passed") is True,
            "vc_ready": None,
        }
    if data.get("tool_ready") is True or data.get("phase1_status") == "local_validation_passed":
        return {"local_ready": True, "docker_ready": False, "registry_ready": False, "vc_ready": None}
    if data.get("status") in {"PASS", "PASSED", "pass"}:
        return {"local_ready": True, "docker_ready": False, "registry_ready": False, "vc_ready": None}
    return {}


def build_success(data: dict) -> bool:
    build = data.get("build")
    if isinstance(build, dict):
        return build.get("success") is True
    checks = data.get("checks")
    if isinstance(checks, dict):
        return all(str(checks.get(key, "")).upper() == "PASS" for key in ("import", "load", "infer", "contract"))
    steps = data.get("steps")
    if isinstance(steps, dict):
        required = ["BUILD_ENV", "VALIDATE_IMPORT", "VALIDATE_LOAD", "VALIDATE_INFER", "VALIDATE_CONTRACT"]
        present = [key for key in required if key in steps]
        return bool(present) and all(str(steps.get(key, "")).upper().startswith("PASS") for key in present)
    return False


def failed_validation_tests(data: dict) -> list[str]:
    validation = data.get("validation")
    if isinstance(validation, dict):
        if any(key in validation for key in FOUR_TESTS):
            return [t for t in FOUR_TESTS if not (validation.get(t) or {}).get("passed")]
        wrapper = validation.get("wrapper")
        if isinstance(wrapper, dict):
            mapping = {
                "import_test": "import",
                "load_test": "load",
                "infer_test": "infer",
                "contract_test": "contract",
            }
            return [
                test_name
                for test_name, key in mapping.items()
                if not str(wrapper.get(key, "")).upper().startswith("PASS")
            ]
        repo_native = validation.get("repo_native")
        if isinstance(repo_native, dict):
            mapping = {
                "import_test": "import",
                "load_test": "load",
                "infer_test": "infer",
                "contract_test": "contract",
            }
            failed: list[str] = []
            for test_name, key in mapping.items():
                item = repo_native.get(key)
                status = item.get("status") if isinstance(item, dict) else item
                if not str(status or "").upper().startswith("PASS"):
                    failed.append(test_name)
            return failed
        return [t for t in FOUR_TESTS if not (validation.get(t) or {}).get("passed")]
    checks = data.get("checks")
    if isinstance(checks, dict):
        mapping = {
            "import_test": "import",
            "load_test": "load",
            "infer_test": "infer",
            "contract_test": "contract",
        }
        return [test_name for test_name, key in mapping.items() if str(checks.get(key, "")).upper() != "PASS"]
    return FOUR_TESTS[:]


def required_readiness_keys(profile: str) -> list[str]:
    keys = ["local_ready"]
    if profile in {"docker-local", "docker-registry"}:
        keys.append("docker_ready")
    if profile == "docker-registry":
        keys.extend(["registry_ready", "bundle_ready"])
    return keys


def is_harness_verdict(data: dict) -> bool:
    return (
        isinstance(data.get("package"), dict)
        or isinstance(data.get("readiness"), dict)
        or isinstance(data.get("package_profile"), str)
    )


def repo_root_for(run_dir: Path) -> Path:
    resolved = run_dir.expanduser().resolve()
    if resolved.parent.name == "runs" and resolved.parent.parent.name == ".sure":
        return resolved.parent.parent.parent
    return Path.cwd().resolve()


def resolve_existing(raw: str, bases: list[Path], repo_root: Path) -> bool:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path.exists()
    if len(path.parts) >= 2 and path.parts[0] == "sure" and path.parts[1] == "models":
        if (repo_root / path).exists():
            return True
    for base in bases:
        if (base / path).exists():
            return True
        if not str(path).startswith("artifacts/") and (base / "artifacts" / path).exists():
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    args = parser.parse_args()

    path = Path(args.produces).expanduser().resolve()
    if not path.exists():
        print(f"verdict.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = read_json(path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    run_dir = Path(args.run_dir).expanduser().resolve()
    repo_root = repo_root_for(run_dir)
    package_gate, package_gate_error = package_gate_from_run(run_dir)
    if package_gate_error:
        print(f"VERDICT gate: package_gate.json exists but is invalid: {package_gate_error}", file=sys.stderr)
        return 1
    status = data.get("status", "")
    if status not in TERMINAL_STATUSES:
        print(
            f'VERDICT gate: status must be a terminal value ("passed"/"success"/"PASS", '
            f'"failed"/"FAIL", or "partial"); got "{status}".',
            file=sys.stderr,
        )
        return 1

    if status in SUCCESS_STATUSES:
        profile = package_profile(data, package_gate)
        if profile not in PACKAGE_PROFILES:
            print(f"VERDICT gate: unsupported package profile {profile!r}.", file=sys.stderr)
            return 1
        if is_harness_verdict(data) and not package_gate:
            print(
                "VERDICT gate: harness success verdict requires artifacts/package_gate.json "
                "from the preceding PACKAGE_GATE unit.",
                file=sys.stderr,
            )
            return 1
        resolved_path = run_dir / "artifacts" / "model_input_resolved.json"
        try:
            resolved = read_json(resolved_path)
        except (OSError, ValueError) as exc:
            print(f"VERDICT gate: cannot read model_input_resolved.json: {exc}", file=sys.stderr)
            return 1
        if resolved.get("deployment_type") == "local" and profile == "docker-local":
            print(
                "VERDICT gate: local package=docker-local is diagnostic-only and cannot use a success status; "
                "emit status=partial, use package=none for Python Eval, or complete docker-registry delivery.",
                file=sys.stderr,
            )
            return 1
        if package_gate:
            gate_status = package_gate.get("status")
            gate_profile = package_gate.get("package_profile")
            if gate_status != "passed":
                print(f"VERDICT gate: package_gate.status must be passed, got {gate_status!r}.", file=sys.stderr)
                return 1
            if isinstance(gate_profile, str) and gate_profile != profile:
                print(
                    f"VERDICT gate: verdict package profile {profile!r} disagrees with "
                    f"package_gate.package_profile {gate_profile!r}.",
                    file=sys.stderr,
                )
                return 1

        if not build_success(data):
            print(
                "VERDICT gate: status is success but build evidence did not pass.",
                file=sys.stderr,
            )
            return 1
        failed_tests = failed_validation_tests(data)
        if failed_tests:
            print(
                "VERDICT gate: status is success but these validation tests are "
                "not passed=true: " + ", ".join(failed_tests),
                file=sys.stderr,
            )
            return 1

        readiness = readiness_from(data, package_gate)
        if readiness.get("local_ready") is not True:
            print(
                "VERDICT gate: success requires readiness.local_ready=true "
                "(or package_gate.json/local_deployment evidence proving local readiness).",
                file=sys.stderr,
            )
            return 1
        for key in required_readiness_keys(profile):
            if readiness.get(key) is not True:
                print(f"VERDICT gate: package={profile} requires readiness.{key}=true.", file=sys.stderr)
                return 1

        gate_readiness = package_gate.get("readiness") if isinstance(package_gate.get("readiness"), dict) else {}
        for key in required_readiness_keys(profile):
            if gate_readiness and gate_readiness.get(key) is not True:
                print(
                    f"VERDICT gate: package_gate readiness contradicts success; {key} is not true.",
                    file=sys.stderr,
                )
                return 1

        # Cross-check declared artifact paths exist. Old PASS/checks verdicts
        # may omit these fields; new harness verdicts should declare them.
        artifacts = data.get("artifacts") or {}
        model_dir_raw = data.get("model_dir") or artifacts.get("model_dir") or package_gate.get("model_dir")
        if not model_dir_raw and package_gate.get("artifact_manifest_path"):
            manifest_path = Path(str(package_gate["artifact_manifest_path"])).expanduser()
            if not manifest_path.is_absolute():
                candidates = []
                if len(manifest_path.parts) >= 2 and manifest_path.parts[0] == "sure" and manifest_path.parts[1] == "models":
                    candidates.append(repo_root / manifest_path)
                candidates.extend([run_dir / "artifacts" / manifest_path, repo_root / manifest_path])
                manifest_path = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
            if manifest_path.exists():
                try:
                    manifest = read_json(manifest_path)
                    model_dir_raw = manifest.get("model_dir")
                except ValueError:
                    model_dir_raw = None
        if not model_dir_raw and path.parent.name == "artifacts":
            model_dir_raw = str(path.parent.parent)
        bases = [run_dir, run_dir / "artifacts", repo_root]
        if model_dir_raw:
            model_dir = Path(str(model_dir_raw)).expanduser()
            if not model_dir.is_absolute():
                model_dir = repo_root / model_dir
            bases.extend([model_dir, model_dir / "artifacts"])
        for field in (
            "spec_path",
            "wrapper_path",
            "artifact_manifest_path",
            "manifest_path",
            "validation_log_path",
            "sample_output_path",
        ):
            raw = artifacts.get(field)
            if raw and not resolve_existing(str(raw), bases, repo_root):
                print(f"VERDICT gate: declared {field} does not exist: {raw}", file=sys.stderr)
                return 1

    print(f"check_verdict OK: status={status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
