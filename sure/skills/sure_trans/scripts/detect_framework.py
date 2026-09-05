#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


PYTHON_LIMIT = 5000
ARCHITECTURE_PATTERNS = (
    ("conformer", re.compile(r"\bconformer\b")),
    ("transformer", re.compile(r"\btransformers?\b")),
    ("cnn", re.compile(r"\b(?:cnn|conv1d|conv2d|convolutional?)\b")),
    ("rnn", re.compile(r"\b(?:rnn|recurrent)\b")),
    ("lstm", re.compile(r"\blstm\b")),
    ("gru", re.compile(r"\bgru\b")),
    ("ctc", re.compile(r"\bctc\b")),
    ("transducer", re.compile(r"\b(?:rnn-?t|rnnt|transducer)\b")),
)


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def architecture_clarification(
    declared_model_framework: str,
    detected_model_framework: str,
    architecture_signals: list[str],
) -> str | None:
    matches = declared_model_framework == detected_model_framework or (
        declared_model_framework != "transformers" and detected_model_framework == "custom"
    )
    if declared_model_framework == "transformers" and detected_model_framework == "transformers" and matches:
        return None
    signals = ", ".join(architecture_signals) if architecture_signals else "no specific architecture family proven"
    if detected_model_framework == "custom":
        return (
            f"Declared model framework '{declared_model_framework}'. Static inspection found a custom PyTorch "
            f"implementation without a Transformers dependency; architecture signals: {signals}. The flow preserves "
            "this implementation and relies on original inference, adapter inference, and equivalence gates."
        )
    if detected_model_framework == "transformers":
        return (
            f"Declared model framework '{declared_model_framework}', while static inspection detected Transformers; "
            f"architecture signals: {signals}. The detected implementation is retained and validated by inference "
            "and equivalence gates."
        )
    return (
        f"Declared model framework '{declared_model_framework}', but static inspection could not determine a PyTorch "
        f"model implementation; architecture signals: {signals}."
    )


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
    declared_lockfile = resolved.get("lockfile")
    if declared_lockfile:
        lockfile = Path(str(declared_lockfile))
        if lockfile.is_file() and str(lockfile) not in evidence:
            corpus.append(lockfile.read_text(encoding="utf-8", errors="replace"))
            evidence.append(str(lockfile))
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
    has_torch = any(name == "torch" or name.startswith("torch.") for name in imports) or re.search(
        r"(^|[^a-z])torch([^a-z]|$)", text
    ) is not None
    has_transformers = any(name == "transformers" or name.startswith("transformers.") for name in imports) or re.search(
        r"(^|[^a-z])transformers([^a-z]|$)", text
    ) is not None
    additional_frameworks = sorted(name for name in ("tensorflow", "jax", "flax") if name in imports or name in text)
    # A torch token alone does not make PyTorch the primary computation
    # framework: a delivery that also ships TensorFlow or JAX has to be
    # blocked, which is what SKILL.md promises and what mainline did before
    # the non-Transformers relaxation.
    if has_torch and not additional_frameworks:
        detected_framework = "pytorch"
    elif "tensorflow" in additional_frameworks:
        detected_framework = "tensorflow"
    elif any(name in additional_frameworks for name in ("jax", "flax")):
        detected_framework = "jax_flax"
    else:
        detected_framework = "unknown"
    detected_model_framework = "transformers" if has_transformers else "custom" if has_torch else "unknown"
    declared_model_framework = str(resolved["model_framework"])
    model_framework_matches = declared_model_framework == detected_model_framework or (
        declared_model_framework != "transformers" and detected_model_framework == "custom"
    )
    architecture_signals = [name for name, pattern in ARCHITECTURE_PATTERNS if pattern.search(text)]
    clarification = architecture_clarification(
        declared_model_framework,
        detected_model_framework,
        architecture_signals,
    )
    if has_torch:
        evidence.append("PyTorch import or dependency detected")
    if has_transformers:
        evidence.append("Transformers import or dependency detected")
    if "peft" in imports or "peft" in text:
        evidence.append("PEFT dependency detected")
    evidence.extend(f"additional framework evidence: {name}" for name in additional_frameworks)
    payload = {
        "schema": "sure.trans.framework_detection.v2",
        "declared_framework": resolved["framework"],
        "declared_model_framework": declared_model_framework,
        "detected_framework": detected_framework,
        "detected_model_framework": detected_model_framework,
        "framework_requirement_met": detected_framework == "pytorch",
        "model_framework_matches": model_framework_matches,
        "transformers_preferred": True,
        "clarification_required": clarification is not None,
        "architecture_signals": architecture_signals,
        "architecture_clarification": clarification,
        "status": "ready" if detected_framework == "pytorch" else "blocked",
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
