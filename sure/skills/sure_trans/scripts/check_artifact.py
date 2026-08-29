#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from vc_exec import default_partition


LEGACY_PATH = re.compile(r"/(?:mnt/cloudstorfs|hpc_stor\d+|hpc_\d+)/")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"artifact must be a JSON object: {path}")
    return value


def infer_repo_root(run_dir: Path) -> Path:
    resolved = run_dir.expanduser().resolve()
    if resolved.parent.name == "runs" and resolved.parent.parent.name == ".sure":
        return resolved.parent.parent.parent
    return Path.cwd().resolve()


def harness_model_dir(run_dir: Path) -> Path:
    resolved = read_object(Path(run_dir) / "artifacts" / "trans_input_resolved.json")
    model_name = str(resolved.get("model_name") or "")
    if not model_name or "/" in model_name or "\\" in model_name:
        raise ValueError("model_name must be a single directory segment")
    path_policy = resolved.get("path_policy") if isinstance(resolved.get("path_policy"), dict) else {}
    raw_root = path_policy.get("allowed_model_root")
    if raw_root:
        allowed_root = Path(str(raw_root)).expanduser().resolve()
    else:
        allowed_root = (infer_repo_root(Path(run_dir)) / "sure" / "models").resolve()
    return (allowed_root / model_name).resolve()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    parser.add_argument("--kind", required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    path = Path(args.produces)
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), "artifact must be a JSON object")
    kind = args.kind
    if kind == "input":
        for key in ("dockerfile", "build_context", "model_path", "inference_entrypoint"):
            candidate = Path(str(value.get(key, "")))
            require(candidate.is_absolute() and candidate.exists(), f"{key} must exist and be absolute")
        require(value.get("framework") == "pytorch", "framework must normalize to pytorch")
        require(
            isinstance(value.get("model_framework"), str) and bool(value["model_framework"].strip()),
            "model_framework is required",
        )
        expected_model_dir = harness_model_dir(run_dir)
        declared_model_dir = Path(str(value.get("model_dir") or "")).expanduser()
        try:
            declared_model_dir = declared_model_dir.resolve()
        except OSError:
            declared_model_dir = declared_model_dir.absolute()
        if declared_model_dir.exists() and declared_model_dir.is_symlink():
            raise ValueError("model_dir must be a real harness-owned directory, not a whole-directory symlink")
        require(
            declared_model_dir == expected_model_dir,
            f"model_dir must be the harness-owned bundle {expected_model_dir}; got {declared_model_dir}",
        )
    elif kind == "dependencies":
        require(value.get("status") == "ready", "dependency inspection is blocked")
        require(value.get("unresolved") == [], "dependency report contains unresolved paths")
        require(value.get("external_paths") == [], "dependency report contains undeclared external paths")
    elif kind == "framework":
        resolved = read_object(run_dir / "artifacts" / "trans_input_resolved.json")
        require(value.get("status") == "ready", "primary computation framework must be PyTorch")
        require(
            value.get("declared_framework") == resolved.get("framework") == "pytorch",
            "declared computation framework must match the resolved PyTorch input",
        )
        require(value.get("detected_framework") == "pytorch", "static inspection must detect PyTorch")
        require(value.get("framework_requirement_met") is True, "PyTorch framework requirement was not met")
        declared_model_framework = value.get("declared_model_framework")
        detected_model_framework = value.get("detected_model_framework")
        require(
            isinstance(declared_model_framework, str) and bool(declared_model_framework.strip()),
            "declared_model_framework is required",
        )
        require(
            declared_model_framework == resolved.get("model_framework"),
            "declared_model_framework must match the resolved input",
        )
        require(
            detected_model_framework in {"transformers", "custom"},
            "ready framework detection must identify the model framework category",
        )
        needs_clarification = (
            declared_model_framework != "transformers"
            or detected_model_framework != "transformers"
            or value.get("model_framework_matches") is not True
        )
        require(
            value.get("clarification_required") is needs_clarification,
            "clarification_required does not match the framework evidence",
        )
        clarification = value.get("architecture_clarification")
        if needs_clarification:
            require(
                isinstance(clarification, str) and bool(clarification.strip()),
                "non-Transformers or mismatched model frameworks require architecture clarification",
            )
        else:
            require(clarification is None, "matching Transformers models must not carry a stale clarification")
    elif kind == "fixture":
        require(value.get("status") == "ready", "fixture manifest is not ready")
        staged = Path(str(value.get("staged_path", "")))
        require(staged.is_file(), "staged fixture is missing")
        require(int(value.get("sample_count", 0)) == 1, "trans smoke fixture must contain exactly one bounded sample")
    elif kind == "source_image":
        require(value.get("status") == "passed", "source image materialization did not pass")
        require(value.get("source_image_policy") in {"load", "build"}, "source image policy must be load or build")
        require(value.get("source_image_policy") == value.get("requested_source_image_policy", value.get("source_image_policy")) or value.get("requested_source_image_policy") == "auto", "source image policy violates requested policy")
        require(Path(str(value.get("source_image_log_path", ""))).is_file(), "source image log is missing")
        require(value.get("image_id", "").startswith("sha256:"), "source image image_id must be a live sha256 ID")
        if value.get("source_image_policy") == "build":
            require(value.get("build_executed") is True, "source image build was not executed")
            require(value.get("build_exit_code") == 0, "docker build did not exit successfully")
            require(isinstance(value.get("build_command"), list) and value["build_command"][0:2] == ["docker", "build"], "source image must record docker build command")
            require(Path(str(value.get("build_log_path", ""))).is_file(), "source image build log is missing")
        else:
            require(value.get("load_executed") is True, "source image load was not executed")
            require(value.get("load_exit_code") == 0, "docker load did not exit successfully")
            require(isinstance(value.get("load_command"), list) and value["load_command"][0:2] == ["docker", "load"], "source image must record docker load command")
            image_tar = Path(str(value.get("image_tar", ""))).resolve()
            build_context = Path(str(value.get("build_context", ""))).resolve()
            require(image_tar.is_file() and image_tar.is_relative_to(build_context), "loaded image tar must be inside build context")
            require(value.get("tar_sha256") == sha256_file(image_tar), "loaded image tar checksum changed")
            require(value.get("load_verified") is True, "loaded image was not verified")
    elif kind == "adapter_image":
        require(value.get("status") == "passed", "adapter image build must pass")
    elif kind == "registry":
        require(value.get("status") == "passed", "registry package must pass")
        require(value.get("pull_verified") is True, "registry package must prove exact digest pull verification")
        require("@sha256:" in str(value.get("target_image_ref", "")), "registry target_image_ref must be digest-pinned")
        require(str(value.get("target_image_digest", "")).startswith("sha256:"), "registry target_image_digest must be a sha256 digest")
        compat_path = Path(run_dir) / "artifacts" / "execution_compat.json"
        selected_device = "cpu"
        if compat_path.is_file():
            selected_device = str(read_object(compat_path).get("selected_device") or "cpu")
        if selected_device == "cuda":
            smoke = value.get("post_pull_smoke")
            require(
                isinstance(smoke, dict),
                "GPU-validated models must repeat the MCP smoke test on VC after the exact digest pull and record post_pull_smoke evidence",
            )
            require(smoke.get("vc_job_id"), "post_pull_smoke must record the vc job id")
            expected_partition = default_partition()
            require(
                smoke.get("vc_partition") == expected_partition,
                f"post-pull MCP smoke must run on the site's dedicated partition {expected_partition}",
            )
            # vc submit takes repo:tag only and answers 镜像不存在 to any
            # repo@sha256:... reference, so the job cannot carry the pin in the
            # reference it runs. Requiring that made this unit unsatisfiable on
            # GPU. The submission proves the pin instead: vc_exec.py resolves
            # what the tag serves and refuses to submit on a mismatch.
            require(
                str(smoke.get("resolved_digest", "")) == str(value.get("target_image_digest", "")),
                "post_pull_smoke.resolved_digest must be the digest the submitted tag resolved to "
                "and must equal target_image_digest; submit through vc_exec.py --expect-digest so "
                "the pin is proven rather than asserted",
            )
            require(smoke.get("exit_code") == 0, "post-pull MCP smoke must exit 0")
            smoke_log = Path(str(smoke.get("log_path") or "")).expanduser()
            require(
                smoke_log.exists(),
                f"post_pull_smoke log path is missing: {smoke_log}",
            )
            evidence = smoke_log / "mcp_smoke.json" if smoke_log.is_dir() else smoke_log.parent / "mcp_smoke.json"
            require(
                evidence.is_file(),
                "post_pull_smoke must record mcp_smoke.json protocol evidence (initialize/tools/list/tools/call)",
            )
            protocol = read_object(evidence)
            require(protocol.get("status") == "passed", "post-pull MCP smoke evidence must pass")
            for step in ("initialize", "tools_list", "tools_call"):
                entry = protocol.get(step)
                require(
                    isinstance(entry, dict) and entry.get("ok") is True,
                    f"post-pull MCP smoke must prove {step} passed",
                )
            require(
                bool((protocol.get("tools_call") or {}).get("text_nonempty")),
                "post-pull MCP smoke must return non-empty text from tools/call",
            )
    elif kind == "model_payload":
        require(value.get("status") == "ready", "model payload was not staged")
        require(Path(str(value.get("destination", ""))).is_dir(), "staged model directory is missing")
        require(int(value.get("file_count", 0)) > 0, "staged model payload is empty")
        expected_model_dir = harness_model_dir(run_dir)
        declared_destination = Path(str(value.get("destination", ""))).expanduser().resolve()
        require(
            declared_destination == expected_model_dir,
            f"model payload must land in the harness-owned bundle {expected_model_dir}; got {declared_destination}",
        )
    elif kind == "adapter":
        require(value.get("status") == "ready", "adapter manifest must be ready")
        require(value.get("harness_runtime_embedded") is True, "adapter image must embed the common Harness Runtime")
        harness = value.get("harness_runtime") if isinstance(value.get("harness_runtime"), dict) else {}
        require(
            all(harness.get(key) for key in ("runtime_id", "lock_sha256", "python_executable", "manifest_path", "runtime_root")),
            "adapter manifest must declare the embedded Harness Runtime binding",
        )
        for key in ("model_py", "init_py", "validate_py", "server_py", "config_yaml", "model_spec", "dockerfile", "mcp_smoke_py"):
            candidate = Path(str(value.get(key, "")))
            require(candidate.is_file(), f"adapter file missing: {key}")
        dockerfile = Path(str(value.get("dockerfile", "")))
        require(dockerfile.is_file(), "adapter Dockerfile is missing")
        dockerfile_text = dockerfile.read_text(encoding="utf-8")
        require(
            "COPY --from=sure_harness_runtime" in dockerfile_text,
            "adapter Dockerfile must copy the locked Harness Runtime with the sure_harness_runtime build context",
        )
        build_context = str(value.get("harness_runtime_build_context") or "directory")
        if build_context.startswith("docker-image://"):
            require(
                re.fullmatch(r"docker-image://.+@sha256:[0-9a-f]{64}", build_context) is not None,
                "image-backed Harness Runtime build context must be digest-pinned",
            )
        for key in ("model_py", "init_py", "server_py", "config_yaml", "model_spec", "validate_py", "mcp_smoke_py"):
            declared = Path(str(value.get(key, "")))
            require(
                declared.name in dockerfile_text,
                f"adapter Dockerfile must COPY {declared.name} into the image; the manifest declares {key} but "
                "the Dockerfile does not reference it. Fix the COPY line (templates/Dockerfile.sure), rebuild the "
                "adapter image, and re-run the import gate",
            )
        model_source = Path(str(value["model_py"])).read_text(encoding="utf-8")
        require("NotImplementedError" not in model_source and "TODO" not in model_source, "model.py is still a scaffold")
    elif kind == "runtime_inventory":
        require(value.get("schema") == "sure.onboard.runtime_inventory.v2", "runtime inventory schema is incompatible with sure_eval")
        require(value.get("status") == "ready", "runtime inventory is not ready")
        container = value.get("container_runtime") or {}
        policy = value.get("policy") or {}
        require("@sha256:" in str(container.get("target_image_ref", "")), "runtime image must be digest-pinned")
        require(policy.get("eval_runtime") == "container_only", "Eval runtime must be container_only")
        require(policy.get("host_python_fallback") is False, "host Python fallback must be disabled")
        require(policy.get("nfs_models_mutable_by_eval") is False, "Eval must not mutate the approved model bundle")
        harness = value.get("harness_runtime") if isinstance(value.get("harness_runtime"), dict) else {}
        require(harness.get("required") is True, "trans adapter image must embed the Harness Runtime")
        require(harness.get("schema") == "sure.harness.runtime.binding.v1", "required Harness Runtime binding must use the common schema")
        require(
            all(harness.get(key) for key in ("runtime_id", "lock_sha256", "python_executable", "manifest_path", "runtime_root")),
            "required Harness Runtime binding is missing identity or path fields",
        )
        require(
            not LEGACY_PATH.search(json.dumps(harness, ensure_ascii=False)),
            "host Harness Runtime paths cannot be declared as the container runtime",
        )
        mount_policy = container.get("mount_policy") or {}
        require((mount_policy.get("model_bundle") or {}).get("read_only") is True, "model bundle mount must be read-only")
        require((mount_policy.get("result_workspace") or {}).get("read_only") is False, "result workspace mount must be writable")
    elif kind == "verdict":
        require(value.get("status") == "success", "verdict is not terminal-success")
        readiness = value.get("readiness")
        require(
            isinstance(readiness, dict)
            and readiness.get("bundle_ready") is True
            and readiness.get("registry_ready") is True,
            "verdict readiness must prove bundle and registry readiness",
        )
    elif kind == "deployment_ready":
        require(value.get("schema") == "sure.onboard.deployment_ready.v1", "deployment schema is incompatible with sure_eval")
        if value.get("status") == "blocked":
            require(
                str(value.get("blocked_reason") or "").strip() != "",
                "blocked deployment marker must record why the run stopped",
            )
            blocked_policy = value.get("execution_policy") if isinstance(value.get("execution_policy"), dict) else {}
            require(
                blocked_policy.get("container_only") is False,
                "blocked deployment marker must not claim container-only Eval readiness",
            )
            print(f"{kind} OK: {path}")
            return 0
        require(value.get("status") == "ready", "deployment is not ready")
        require("@sha256:" in str(value.get("target_image_ref", "")), "deployment image must be digest-pinned")
        model_dir = harness_model_dir(run_dir)
        model_copy = model_dir / "artifacts" / "deployment_ready.json"
        require(
            model_copy.is_file() and model_copy.read_bytes() == path.read_bytes(),
            "deployment_ready.json must be written identically to the run and model bundle",
        )
        policy = value.get("execution_policy") if isinstance(value.get("execution_policy"), dict) else {}
        require(
            policy.get("container_only") is True
            and policy.get("nfs_models_read_only") is True
            and policy.get("host_python_fallback") is False
            and policy.get("approved_image_override") is False,
            "final execution policy must be container-only with NFS read-only and no host fallback",
        )
        hashes = value.get("required_artifact_sha256")
        require(isinstance(hashes, dict) and hashes, "required_artifact_sha256 must list finalized artifacts")
        for raw, expected in hashes.items():
            relative = str(raw).removeprefix("artifacts/")
            artifact = model_dir / "artifacts" / relative
            require(
                artifact.is_file() and sha256_file(artifact) == expected,
                f"finalized artifact hash mismatch: {raw}",
            )
        bundle_hash = hashlib.sha256(
            json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        require(value.get("bundle_identity_sha256") == bundle_hash, "bundle_identity_sha256 does not match finalized artifact hashes")
        manifest = read_object(model_dir / "artifacts" / "artifact_manifest.json")
        require(
            manifest.get("status") == "finalized" and manifest.get("model_dir") == ".",
            "artifact_manifest.json must be refreshed into portable finalized form",
        )
        package = read_object(model_dir / "artifacts" / "package_gate.json")
        require(package.get("status") == "passed", "package_gate must be passed")
        gate_readiness = package.get("readiness") if isinstance(package.get("readiness"), dict) else {}
        require(
            gate_readiness.get("bundle_ready") is True and gate_readiness.get("registry_ready") is True,
            "package_gate readiness must prove bundle and registry readiness",
        )
        docker = package.get("docker") if isinstance(package.get("docker"), dict) else {}
        dockerfile = model_dir / str(docker.get("dockerfile_path") or "Dockerfile.sure")
        require(
            dockerfile.is_file() and docker.get("dockerfile_sha256") == sha256_file(dockerfile),
            "package gate Dockerfile hash does not match the model bundle",
        )
        declared_binding = value.get("harness_runtime") if isinstance(value.get("harness_runtime"), dict) else {}
        if declared_binding:
            inventory = read_object(model_dir / "artifacts" / "runtime_inventory.json")
            source_binding = inventory.get("harness_runtime") if isinstance(inventory.get("harness_runtime"), dict) else {}
            projected = {key: source_binding.get(key) for key in declared_binding}
            require(declared_binding == projected, "deployment Harness Runtime binding disagrees with runtime inventory")
            require(
                declared_binding.get("schema") == "sure.harness.runtime.binding.v1" and declared_binding.get("runtime_id"),
                "ready deployment must expose the common Harness Runtime binding",
            )
            require(
                not LEGACY_PATH.search(json.dumps(declared_binding, ensure_ascii=False)),
                "deployment Harness Runtime binding must reference an in-image runtime, not host paths",
            )
        portable = [
            read_object(model_dir / "artifacts" / name)
            for name in ("runtime_inventory.json", "package_gate.json", "artifact_manifest.json", "deployment_ready.json")
        ]
        require(
            not LEGACY_PATH.search(json.dumps(portable, ensure_ascii=False)),
            "finalized deployment sidecars contain legacy host absolute paths",
        )
    print(f"{kind} OK: {path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error))
        raise SystemExit(1)
