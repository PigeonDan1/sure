#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


PYTHON_LIMIT = 5000


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    artifacts = run_dir / "artifacts"
    resolved = read_object(artifacts / "trans_input_resolved.json")
    dependencies = read_object(artifacts / "inference_dependency_report.json")
    build_context = Path(resolved["build_context"])
    evidence: list[str] = []
    corpus: list[str] = []
    for name in ("requirements.txt", "requirements.lock.txt", "pyproject.toml", "setup.py", "environment.yml", "environment.yaml"):
        path = build_context / name
        if path.is_file():
            corpus.append(path.read_text(encoding="utf-8", errors="replace"))
            evidence.append(str(path))
    python_files: list[Path] = []
    for support in dependencies.get("support_paths", []):
        root = Path(str(support))
        if root.is_file() and root.suffix == ".py":
            python_files.append(root)
        elif root.is_dir():
            python_files.extend(root.rglob("*.py"))
        if len(python_files) >= PYTHON_LIMIT:
            break
    for path in python_files[:PYTHON_LIMIT]:
        corpus.append(path.read_text(encoding="utf-8", errors="replace"))
    text = "\n".join(corpus).lower()
    imports = {str(item).lower() for item in dependencies.get("python_imports", [])}
    has_torch = "torch" in imports or re.search(r"(^|[^a-z])torch([^a-z]|$)", text) is not None
    has_transformers = "transformers" in imports or re.search(r"(^|[^a-z])transformers([^a-z]|$)", text) is not None
    incompatible = sorted(name for name in ("tensorflow", "jax", "flax") if name in imports or name in text)
    compatible = has_torch and has_transformers and not incompatible
    if compatible:
        detected = "pytorch_transformers"
    elif "tensorflow" in incompatible:
        detected = "tensorflow"
    elif any(name in incompatible for name in ("jax", "flax")):
        detected = "jax_flax"
    elif has_torch:
        detected = "pytorch_non_transformers"
    else:
        detected = "unknown"
    if has_torch:
        evidence.append("PyTorch import or dependency detected")
    if has_transformers:
        evidence.append("Transformers import or dependency detected")
    if "peft" in imports or "peft" in text:
        evidence.append("PEFT dependency detected")
    evidence.extend(f"incompatible primary framework evidence: {name}" for name in incompatible)
    payload = {
        "schema": "sure.trans.framework_detection.v1",
        "declared": resolved["framework"],
        "detected": detected,
        "primary_model_compatible": compatible,
        "conversion_required": not compatible,
        "conversion_succeeded": False,
        "status": "ready" if compatible else "blocked",
        "evidence": evidence,
        "auxiliary_runtimes_allowed": ["onnxruntime", "native_binary"],
        "scanned_python_files": min(len(python_files), PYTHON_LIMIT),
    }
    output = artifacts / "framework_detection.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
