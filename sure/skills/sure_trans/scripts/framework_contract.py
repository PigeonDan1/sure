#!/usr/bin/env python3
"""Evidence collection and review rules for the sure_trans framework gate.

The collector deliberately ignores arbitrary framework words in source text. Imports
and declared packages are recorded with their source and confidence so an Agent can
explain optional vendor backends without being able to erase hard evidence.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any


FRAMEWORK_NAMES = ("tensorflow", "jax", "flax")
MANIFEST_NAMES = (
    "requirements.txt",
    "requirements.lock.txt",
    "pyproject.toml",
    "setup.py",
    "environment.yml",
    "environment.yaml",
)
PYTHON_LIMIT = 5000
SAFE_REVIEW_DISPOSITIONS = {
    "optional_backend_only",
    "vendored_support",
    "text_only",
    "no_conflict",
}
REVIEW_DISPOSITIONS = SAFE_REVIEW_DISPOSITIONS | {"runtime_conflict"}
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


def _base_name(value: str) -> str:
    return value.strip().lower().replace("_", "-").split(".", 1)[0]


def _framework(value: str) -> str | None:
    name = _base_name(value)
    if name.startswith("tensorflow"):
        return "tensorflow"
    if name in {"jax", "jaxlib"}:
        return "jax"
    if name == "flax":
        return "flax"
    if name == "torch" or name.startswith("torch-"):
        return "torch"
    if name == "transformers" or name.startswith("transformers-"):
        return "transformers"
    return None


def _caught_import_error(node: ast.Try) -> bool:
    for handler in node.handlers:
        if handler.type is None:
            return True
        names: list[str] = []
        if isinstance(handler.type, ast.Name):
            names.append(handler.type.id)
        elif isinstance(handler.type, ast.Tuple):
            names.extend(item.id for item in handler.type.elts if isinstance(item, ast.Name))
        if any(name in {"ImportError", "ModuleNotFoundError", "Exception"} for name in names):
            return True
    return False


def _guarded_import(parents: list[ast.AST]) -> bool:
    for parent in parents:
        if isinstance(parent, ast.Try) and _caught_import_error(parent):
            return True
        if isinstance(parent, ast.If):
            try:
                condition = ast.unparse(parent.test).lower()
            except AttributeError:
                condition = ""
            if any(token in condition for token in ("is_tf", "is_jax", "is_flax", "available", "optional")):
                return True
    return False


class _ImportVisitor(ast.NodeVisitor):
    def __init__(self, path: Path, entrypoint: Path) -> None:
        self.path = path
        self.entrypoint = entrypoint
        self.parents: list[ast.AST] = []
        self.records: list[dict[str, Any]] = []

    def visit(self, node: ast.AST) -> Any:
        self.parents.append(node)
        try:
            return super().visit(node)
        finally:
            self.parents.pop()

    def _record(self, module: str, line: int) -> None:
        framework = _framework(module)
        if framework is None:
            return
        guarded = _guarded_import(self.parents[:-1])
        is_entrypoint = self.path.resolve() == self.entrypoint.resolve()
        if framework in FRAMEWORK_NAMES:
            strength = "hard" if is_entrypoint and not guarded else "review"
        else:
            strength = "support"
        record = {
            "id": f"import:{self.path}:{line}:{module}",
            "kind": "import",
            "framework": framework,
            "module": module,
            "file": str(self.path),
            "line": line,
            "guarded": guarded,
            "entrypoint": is_entrypoint,
            "strength": strength,
        }
        self.records.append(record)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._record(alias.name, node.lineno)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self._record(node.module, node.lineno)
        self.generic_visit(node)


def _requirement_records(path: Path, text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip().strip("'\"")
        line = re.sub(r"^[-*]\s*", "", line)
        match = re.match(r"([A-Za-z0-9][A-Za-z0-9_.-]*)", line)
        if not match:
            continue
        package = match.group(1)
        framework = _framework(package)
        if framework is None:
            continue
        records.append(
            {
                "id": f"package:{path}:{line_number}:{package.lower()}",
                "kind": "declared_package",
                "framework": framework,
                "package": package,
                "file": str(path),
                "line": line_number,
                "strength": "hard",
            }
        )
    return records


def _pyproject_records(path: Path, text: str) -> list[dict[str, Any]]:
    try:
        import tomllib
    except ImportError:
        return _requirement_records(path, text)
    try:
        document = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return _requirement_records(path, text)
    records: list[dict[str, Any]] = []
    project = document.get("project") if isinstance(document, dict) else None
    if not isinstance(project, dict):
        return records
    dependencies: list[tuple[str, str]] = []
    for field in ("dependencies",):
        values = project.get(field)
        if isinstance(values, list):
            dependencies.extend((str(value), "hard") for value in values)
    optional = project.get("optional-dependencies")
    if isinstance(optional, dict):
        for values in optional.values():
            if isinstance(values, list):
                dependencies.extend((str(value), "review") for value in values)
    for dependency, strength in dependencies:
        match = re.match(r"([A-Za-z0-9][A-Za-z0-9_.-]*)", dependency)
        if not match:
            continue
        package = match.group(1)
        framework = _framework(package)
        if framework is None:
            continue
        line = next((index for index, raw in enumerate(text.splitlines(), 1) if package.lower() in raw.lower()), 1)
        records.append(
            {
                "id": f"package:{path}:{line}:{package.lower()}",
                "kind": "declared_package",
                "framework": framework,
                "package": package,
                "file": str(path),
                "line": line,
                "strength": strength,
            }
        )
    return records


def _manifest_records(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if path.name == "pyproject.toml":
        return _pyproject_records(path, text)
    if path.name == "setup.py":
        records: list[dict[str, Any]] = []
        for line_number, raw in enumerate(text.splitlines(), 1):
            line = raw.split("#", 1)[0]
            for match in re.finditer(
                r"['\"](tensorflow(?:[-_][A-Za-z0-9_.-]+)?|jaxlib?|flax)(?:[<>=!~;][^'\"]*)?['\"]",
                line,
                flags=re.IGNORECASE,
            ):
                package = match.group(1)
                framework = _framework(package)
                if framework is None:
                    continue
                strength = "review" if "extras_require" in line.lower() else "hard"
                records.append(
                    {
                        "id": f"package:{path}:{line_number}:{package.lower()}",
                        "kind": "declared_package",
                        "framework": framework,
                        "package": package,
                        "file": str(path),
                        "line": line_number,
                        "strength": strength,
                    }
                )
        return records
    return _requirement_records(path, text)


def _python_files(dependencies: dict, entrypoint: Path) -> list[Path]:
    files: set[Path] = set()
    if entrypoint.is_file() and entrypoint.suffix == ".py":
        files.add(entrypoint.resolve())
    for support in dependencies.get("support_paths", []):
        root = Path(str(support))
        if root.is_file() and root.suffix == ".py":
            files.add(root.resolve())
        elif root.is_dir():
            for path in root.rglob("*.py"):
                files.add(path.resolve())
                if len(files) >= PYTHON_LIMIT:
                    break
        if len(files) >= PYTHON_LIMIT:
            break
    return sorted(files)[:PYTHON_LIMIT]


def collect_framework_evidence(resolved: dict, dependencies: dict) -> dict[str, Any]:
    build_context = Path(str(resolved["build_context"])).resolve()
    entrypoint = Path(str(resolved["inference_entrypoint"])).resolve()
    records: list[dict[str, Any]] = []
    corpus: list[str] = []
    for name in MANIFEST_NAMES:
        path = build_context / name
        if path.is_file():
            records.extend(_manifest_records(path))
            corpus.append(path.read_text(encoding="utf-8", errors="replace"))

    files = _python_files(dependencies, entrypoint)
    parse_errors: list[dict[str, Any]] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            corpus.append(text)
            tree = ast.parse(text, filename=str(path))
        except (OSError, SyntaxError) as error:
            parse_errors.append(
                {
                    "id": f"parse:{path}",
                    "kind": "parse_error",
                    "framework": "unknown",
                    "file": str(path),
                    "line": getattr(error, "lineno", 1) or 1,
                    "strength": "hard" if path == entrypoint else "review",
                    "reason": type(error).__name__,
                }
            )
            continue
        visitor = _ImportVisitor(path, entrypoint)
        visitor.visit(tree)
        records.extend(visitor.records)

    # Dependency reports from a richer inspector may contain imports whose source
    # file is not available to this process. They establish torch/Transformers,
    # while incompatible names remain review-only until a source file or manifest
    # proves they are runtime dependencies.
    for raw in dependencies.get("python_imports", []):
        module = str(raw)
        framework = _framework(module)
        if framework is None:
            continue
        records.append(
            {
                "id": f"dependency-import:{module.lower()}",
                "kind": "dependency_report_import",
                "framework": framework,
                "module": module,
                "strength": "support" if framework in {"torch", "transformers"} else "review",
            }
        )

    records.extend(parse_errors)
    records = sorted(records, key=lambda item: str(item["id"]))
    hard_conflicts = [
        item for item in records if item.get("framework") in FRAMEWORK_NAMES and item.get("strength") == "hard"
    ]
    review_signals = [
        item for item in records if item.get("framework") in FRAMEWORK_NAMES and item.get("strength") == "review"
    ]
    torch_records = [item for item in records if item.get("framework") == "torch"]
    transformer_records = [item for item in records if item.get("framework") == "transformers"]
    text = "\n".join(corpus).lower()
    architecture_signals = [name for name, pattern in ARCHITECTURE_PATTERNS if pattern.search(text)]
    evidence = {
        "entrypoint": str(entrypoint),
        "entrypoint_imports": [item for item in records if item.get("kind") == "import" and item.get("entrypoint")],
        "framework_evidence": records,
        "hard_conflicts": hard_conflicts,
        "review_signals": review_signals,
        "has_torch": bool(torch_records),
        "has_transformers": bool(transformer_records),
        "architecture_signals": architecture_signals,
        "scanned_python_files": len(files),
    }
    return evidence


def evidence_digest(evidence: dict[str, Any]) -> str:
    canonical = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def deterministic_framework(evidence: dict[str, Any]) -> str:
    if any(
        item.get("kind") == "parse_error" and item.get("strength") == "hard"
        for item in evidence.get("framework_evidence", [])
    ):
        return "unknown"
    hard = {str(item.get("framework")) for item in evidence.get("hard_conflicts", [])}
    if "tensorflow" in hard:
        return "tensorflow"
    if hard.intersection({"jax", "flax"}):
        return "jax_flax"
    if evidence.get("has_torch"):
        return "pytorch"
    return "unknown"


def review_is_valid(review: object, review_signals: list[dict[str, Any]]) -> tuple[bool, str]:
    if not isinstance(review, dict):
        return False, "agent_review must be an object"
    if review.get("schema") != "sure.trans.framework_review.v1":
        return False, "agent_review.schema must be sure.trans.framework_review.v1"
    disposition = review.get("disposition")
    if disposition not in REVIEW_DISPOSITIONS:
        return False, "agent_review.disposition must explain optional/vendor/text-only evidence or a runtime conflict"
    primary = review.get("primary_framework")
    if disposition in SAFE_REVIEW_DISPOSITIONS and primary != "pytorch":
        return False, "a non-blocking agent review must declare primary_framework=pytorch"
    if disposition == "runtime_conflict" and primary not in FRAMEWORK_NAMES:
        return False, "a runtime-conflict review must identify tensorflow, jax, or flax as primary_framework"
    explanation = review.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        return False, "agent_review.explanation must be non-empty"
    cited = review.get("evidence")
    available = {str(item.get("id")) for item in review_signals}
    if not isinstance(cited, list) or not cited:
        return False, "agent_review.evidence must cite review signal ids"
    if any(not isinstance(item, str) or item not in available for item in cited):
        return False, "agent_review.evidence must cite only generated review signal ids"
    secondary = review.get("secondary_frameworks", [])
    if not isinstance(secondary, list) or any(not isinstance(item, str) for item in secondary):
        return False, "agent_review.secondary_frameworks must be an array of framework names"
    return True, ""
