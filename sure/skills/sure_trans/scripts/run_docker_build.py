#!/usr/bin/env python3
"""Load a source image tar when available, otherwise build the Dockerfile."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from vc_exec import agent_bin_cleared_env


ARCHIVE_SUFFIXES = (".tar", ".tar.gz", ".tgz")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def image_tag(model_name: str, dockerfile_sha256: str) -> str:
    safe_name = "".join(character.lower() if character.isalnum() else "-" for character in model_name).strip("-")
    return f"sure-trans/{safe_name}:source-{dockerfile_sha256[:16]}"


def inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def execute(command: list[str], timeout: float) -> dict:
    started = time.monotonic()
    try:
        process = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False, env=agent_bin_cleared_env()
        )
        return {
            "command": command,
            "exit_code": process.returncode,
            "stdout": process.stdout or "",
            "stderr": process.stderr or "",
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
        }
    except subprocess.TimeoutExpired as error:
        return {
            "command": command,
            "exit_code": 124,
            "stdout": str(error.stdout or ""),
            "stderr": str(error.stderr or "") + "\ncommand timed out",
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
        }
    except OSError as error:
        return {
            "command": command,
            "exit_code": 127,
            "stdout": "",
            "stderr": str(error),
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
        }


def inspect_image(image: str, timeout: float) -> tuple[dict | None, dict]:
    result = execute(["docker", "image", "inspect", image, "--format", "{{json .}}"], timeout)
    if result["exit_code"] != 0:
        return None, result
    try:
        value = json.loads(result["stdout"])
    except json.JSONDecodeError:
        return None, {**result, "stderr": result["stderr"] + "\ninvalid inspect JSON"}
    if not isinstance(value, dict) or not isinstance(value.get("Id"), str) or not value["Id"].startswith("sha256:"):
        return None, {**result, "stderr": result["stderr"] + "\ninspect did not return a sha256 image ID"}
    return value, result


def declared_archive_paths(build_context: Path) -> set[Path]:
    delivery = build_context / "delivery.json"
    if not delivery.is_file():
        return set()
    try:
        value = json.loads(delivery.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    paths: set[Path] = set()

    def visit(candidate: object) -> None:
        if isinstance(candidate, str) and candidate.lower().endswith(ARCHIVE_SUFFIXES):
            path = Path(candidate).expanduser()
            if not path.is_absolute():
                path = build_context / path
            path = path.resolve()
            if inside(path, build_context) and path.is_file():
                paths.add(path)
        elif isinstance(candidate, dict):
            for item in candidate.values():
                visit(item)
        elif isinstance(candidate, list):
            for item in candidate:
                visit(item)

    visit(value)
    return paths


def checksum_entries(build_context: Path) -> dict[Path, str]:
    entries: dict[Path, str] = {}
    for checksum_file in sorted(build_context.rglob("SHA256SUMS")):
        if checksum_file.is_symlink() or not checksum_file.is_file():
            continue
        for line in checksum_file.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r"^([0-9a-fA-F]{64})\s+[* ](.+)$", line.strip())
            if match:
                candidate = (checksum_file.parent / match.group(2)).resolve()
                if inside(candidate, build_context):
                    entries[candidate] = match.group(1).lower()
    return entries


def inspect_metadata_for(archive: Path) -> dict | None:
    for path in (archive.parent / "image-inspect.json", archive.parent / "manifest.json"):
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, list) and value and isinstance(value[0], dict):
            value = value[0]
        if isinstance(value, dict):
            return value
    return None


def find_image_tar(build_context: Path, explicit: str | None) -> tuple[Path | None, list[dict]]:
    attempts: list[dict] = []
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not inside(path, build_context):
            raise ValueError("image tar must be inside build context")
        if not path.is_file():
            raise ValueError(f"image tar does not exist: {path}")
        return path, [{"path": str(path), "selection": "explicit"}]

    declared = declared_archive_paths(build_context)
    candidates = set(declared)
    for path in build_context.rglob("*"):
        if path.is_file() and not path.is_symlink() and path.name.lower().endswith(ARCHIVE_SUFFIXES):
            candidates.add(path.resolve())
    checksums = checksum_entries(build_context)
    scored: list[tuple[int, str, Path]] = []
    for path in candidates:
        score = 0
        if path in declared:
            score += 200
        if inspect_metadata_for(path):
            score += 100
        if path in checksums:
            score += 50
        if any(word in path.name.lower() for word in ("image", "docker", "container")):
            score += 10
        scored.append((score, str(path), path))
    scored.sort(key=lambda item: (-item[0], item[1]))
    if scored:
        selected = scored[0][2]
        attempts.append({"path": str(selected), "selection": "auto", "candidate_count": len(scored)})
        return selected, attempts
    return None, attempts


def image_references_from_load(output: str) -> list[str]:
    references = [match.group(1).strip() for match in re.finditer(r"Loaded image:\s*(\S+)", output)]
    references.extend(match.group(1).strip() for match in re.finditer(r"Loaded image ID:\s*(sha256:[0-9a-f]{64})", output))
    return list(dict.fromkeys(references))


def prefer_named_references(references: list[str]) -> list[str]:
    """Put name:tag references ahead of bare image ids.

    The winner becomes source_image_result['image'], which scaffold_adapter
    bakes into the adapter Dockerfile's FROM. A bare sha256 id resolves
    locally but is not a buildable base, so it is only ever a fallback.
    """
    named = [ref for ref in references if not ref.startswith("sha256:")]
    return named + [ref for ref in references if ref.startswith("sha256:")]


def write_log(path: Path, command: list[str], result: dict, extra: str = "") -> None:
    path.write_text(
        f"started_at={datetime.now(timezone.utc).isoformat()}\n$ {' '.join(command)}\n"
        f"exit_code={result['exit_code']}\n{result['stdout']}\n{result['stderr']}\n{extra}",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=7200)
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    output = Path(args.produces).resolve()
    artifacts = run_dir / "artifacts"
    resolved = read_object(artifacts / "trans_input_resolved.json")
    dockerfile = Path(str(resolved["dockerfile"])).resolve()
    build_context = Path(str(resolved["build_context"])).resolve()
    if not dockerfile.is_file():
        raise ValueError(f"Dockerfile does not exist: {dockerfile}")
    if not build_context.is_dir():
        raise ValueError(f"Docker build context does not exist: {build_context}")
    if not inside(dockerfile, build_context):
        raise ValueError("Dockerfile must be inside build context")
    dockerfile_digest = sha256_file(dockerfile)
    requested_policy = str(resolved.get("source_image_policy", "auto"))
    selected_tar = None
    tar_attempts: list[dict] = []
    if requested_policy in {"auto", "load"}:
        selected_tar, tar_attempts = find_image_tar(build_context, str(resolved.get("image_tar")) if resolved.get("image_tar") else None)
        if requested_policy == "load" and selected_tar is None:
            raise ValueError("source_image_policy=load requires an image tar inside build context")

    image = image_tag(str(resolved["model_name"]), dockerfile_digest)
    output.parent.mkdir(parents=True, exist_ok=True)
    attempts: list[dict] = list(tar_attempts)
    if selected_tar is not None:
        load_command = ["docker", "load", "--input", str(selected_tar)]
        tar_digest = sha256_file(selected_tar)
        expected_tar_digest = checksum_entries(build_context).get(selected_tar)
        checksum_matches = expected_tar_digest is None or expected_tar_digest == tar_digest
        load_result = execute(load_command, args.timeout_seconds) if checksum_matches else {
            "command": load_command, "exit_code": 65, "stdout": "",
            "stderr": "image tar checksum does not match SHA256SUMS", "duration_ms": 0,
        }
        load_log = artifacts / "source_image_load.log"
        write_log(load_log, load_command, load_result, f"tar={selected_tar}\ntar_sha256={tar_digest}\n")
        metadata = inspect_metadata_for(selected_tar) or {}
        metadata_tags = metadata.get("RepoTags") if isinstance(metadata.get("RepoTags"), list) else []
        references = image_references_from_load(load_result["stdout"] + "\n" + load_result["stderr"])
        references.extend(tag for tag in metadata_tags if isinstance(tag, str))
        if isinstance(metadata.get("Id"), str):
            references.append(metadata["Id"])
        references = prefer_named_references(list(dict.fromkeys(references)))
        attempts.append({"mode": "load", "tar": str(selected_tar), "command": load_command, "exit_code": load_result["exit_code"], "references": references, "checksum_matches": checksum_matches})
        inspected = None
        selected_tag = None
        if load_result["exit_code"] == 0 and checksum_matches:
            for reference in references:
                inspected, _ = inspect_image(reference, min(args.timeout_seconds, 60))
                reference_matches = inspected is not None and (
                    reference == inspected.get("Id") or reference in (inspected.get("RepoTags") or [])
                )
                if reference_matches:
                    expected_id = metadata.get("Id")
                    if expected_id and expected_id != inspected["Id"]:
                        inspected = None
                        continue
                    selected_tag = reference
                    break
        if selected_tag and inspected:
            payload = {
                "schema": "sure.trans.source_image_result.v1", "status": "passed", "image": selected_tag,
                "image_id": inspected["Id"], "dockerfile": str(dockerfile), "dockerfile_sha256": dockerfile_digest,
                "build_context": str(build_context), "source_image_policy": "load",
                "requested_source_image_policy": requested_policy, "source_image_log_path": str(load_log),
                "image_tar": str(selected_tar), "tar_sha256": tar_digest, "load_command": load_command,
                "load_executed": True, "load_exit_code": 0, "load_duration_ms": load_result["duration_ms"],
                "load_log_path": str(load_log), "load_verified": True, "fallback_to_build": False,
                "source_image_attempts": attempts, "image_repo_tags": inspected.get("RepoTags"),
                "image_created": inspected.get("Created"),
            }
            output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(output)
            return 0
        load_error = "image tar checksum does not match SHA256SUMS" if not checksum_matches else ((load_result["stderr"] or load_result["stdout"]).strip() or "loaded image could not be verified")
        attempts[-1]["error"] = load_error
        if requested_policy == "load":
            payload = {
                "schema": "sure.trans.source_image_result.v1", "status": "failed", "image": None, "image_id": None,
                "dockerfile": str(dockerfile), "dockerfile_sha256": dockerfile_digest, "build_context": str(build_context),
                "source_image_policy": "load", "requested_source_image_policy": requested_policy,
                "image_tar": str(selected_tar), "tar_sha256": tar_digest, "load_command": load_command,
                "load_executed": checksum_matches, "load_exit_code": load_result["exit_code"], "load_log_path": str(load_log),
                "source_image_log_path": str(load_log), "load_verified": False, "source_image_attempts": attempts,
                "error": load_error,
            }
            output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            raise RuntimeError(load_error)

    build_command = ["docker", "build", "--progress", "plain", "--file", str(dockerfile), "--tag", image, str(build_context)]
    build_result = execute(build_command, args.timeout_seconds)
    build_log = artifacts / "source_image_build.log"
    write_log(build_log, build_command, build_result)
    payload = {
        "schema": "sure.trans.source_image_result.v1", "status": "failed", "image": image, "image_id": None,
        "dockerfile": str(dockerfile), "dockerfile_sha256": dockerfile_digest, "build_context": str(build_context),
        "source_image_policy": "build", "requested_source_image_policy": requested_policy,
        "source_image_log_path": str(build_log), "build_command": build_command, "build_executed": True,
        "build_exit_code": build_result["exit_code"], "build_duration_ms": build_result["duration_ms"],
        "build_log_path": str(build_log), "fallback_to_build": bool(attempts), "source_image_attempts": attempts,
    }
    if build_result["exit_code"] == 0:
        inspected, _ = inspect_image(image, min(args.timeout_seconds, 60))
        repo_tags = inspected.get("RepoTags") if inspected else None
        if inspected and isinstance(repo_tags, list) and image in repo_tags:
            payload["status"] = "passed"
            payload["image_id"] = inspected["Id"]
            payload["image_repo_tags"] = repo_tags
            payload["image_created"] = inspected.get("Created")
        else:
            payload["error"] = "docker image inspect did not confirm the generated tag"
    else:
        payload["error"] = (build_result["stderr"] or build_result["stdout"]).strip() or f"docker build exited {build_result['exit_code']}"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if payload["status"] != "passed":
        raise RuntimeError(str(payload["error"]))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
