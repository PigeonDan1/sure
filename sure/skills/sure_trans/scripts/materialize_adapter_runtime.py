#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    artifacts = run_dir / "artifacts"
    resolved = read_object(artifacts / "trans_input_resolved.json")
    if resolved.get("source_kind") != "python":
        raise ValueError("materialize_adapter_runtime.py is only for Python input")
    manifest = read_object(artifacts / "adapter_manifest.json")
    if manifest.get("status") != "ready" or manifest.get("runtime_kind") != "python":
        raise ValueError("Python adapter manifest must be ready")
    files: dict[str, str] = {}
    for key in ("model_py", "init_py", "validate_py", "server_py", "config_yaml", "model_spec", "mcp_smoke_py"):
        path = Path(str(manifest.get(key) or "")).resolve()
        if not path.is_file():
            raise ValueError(f"adapter file is missing: {key}")
        files[key] = sha256_file(path)
    python_executable = Path(str(resolved["python_executable"])).resolve()
    lockfile = Path(str(resolved["lockfile"])).resolve()
    payload = {
        "schema": "sure.trans.adapter_runtime_result.v1",
        "status": "passed",
        "runtime_kind": "python",
        "python_executable": str(python_executable),
        "python_executable_sha256": sha256_file(python_executable),
        "lockfile": str(lockfile),
        "lockfile_sha256": sha256_file(lockfile),
        "server_command": manifest["server_command"],
        "working_dir": manifest["working_dir"],
        "files": files,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    output = artifacts / "adapter_image_result.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
