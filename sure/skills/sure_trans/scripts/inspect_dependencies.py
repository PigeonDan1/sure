#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import shlex
from pathlib import Path


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def docker_instructions(path: Path) -> list[tuple[str, str]]:
    logical: list[str] = []
    current = ""
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        current = f"{current} {line}".strip()
        if current.endswith("\\"):
            current = current[:-1].rstrip()
            continue
        parts = current.split(None, 1)
        logical.append((parts[0].upper(), parts[1] if len(parts) > 1 else ""))
        current = ""
    return logical


def copy_sources(instructions: list[tuple[str, str]]) -> list[str]:
    sources: list[str] = []
    for instruction, value in instructions:
        if instruction not in {"COPY", "ADD"}:
            continue
        value = value.strip()
        if value.startswith("["):
            items = json.loads(value)
            sources.extend(str(item) for item in items[:-1])
            continue
        tokens = shlex.split(value)
        tokens = [token for token in tokens if not token.startswith("--")]
        sources.extend(tokens[:-1])
    return sorted(set(sources))


class DependencyVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: set[str] = set()
        self.string_paths: set[str] = set()
        self.commands: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.update(alias.name.split(".")[0] for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.imports.add(node.module.split(".")[0])

    def visit_Call(self, node: ast.Call) -> None:
        name = ""
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            value = node.args[0].value
            if name in {"open", "Path", "read_text", "read_bytes", "CDLL"}:
                self.string_paths.add(value)
            if name in {"run", "Popen", "call", "check_call", "check_output"}:
                self.commands.add(value)
        self.generic_visit(node)


def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    resolved = load_json(run_dir / "artifacts" / "trans_input_resolved.json")
    build_context = Path(resolved["build_context"])
    source_kind = str(resolved.get("source_kind") or "docker")
    entrypoint = Path(resolved["inference_entrypoint"])
    model_path = Path(resolved["model_path"])

    sources: list[str] = []
    if source_kind == "docker":
        dockerfile = Path(resolved["dockerfile"])
        sources = copy_sources(docker_instructions(dockerfile))
    unresolved: list[str] = []
    support_paths: set[str] = set()
    for source in sources:
        if source.startswith("http://") or source.startswith("https://"):
            continue
        candidate = build_context / source
        if not candidate.exists():
            unresolved.append(f"Dockerfile source does not exist: {source}")
        else:
            support_paths.add(str(candidate.resolve()))

    visitor = DependencyVisitor()
    visitor.visit(ast.parse(entrypoint.read_text(encoding="utf-8"), filename=str(entrypoint)))
    external_paths = sorted(
        value for value in visitor.string_paths
        if value.startswith("/") and not inside(Path(value), build_context) and not inside(Path(value), model_path)
    )
    if inside(entrypoint, build_context):
        support_paths.add(str(entrypoint.parent.resolve()))
    else:
        external_paths.append(str(entrypoint))
    if source_kind == "python":
        support_paths.add(str(Path(resolved["lockfile"]).resolve()))

    payload = {
        "schema": "sure.trans.dependencies.v1",
        "entrypoint": str(entrypoint),
        "build_context": str(build_context),
        "docker_copy_sources": sources,
        "python_imports": sorted(visitor.imports),
        "literal_file_references": sorted(visitor.string_paths),
        "subprocess_references": sorted(visitor.commands),
        "support_paths": sorted(support_paths),
        "model_path": str(model_path),
        "unresolved": sorted(set(unresolved)),
        "external_paths": sorted(set(external_paths)),
        "dynamic_validation_required": True,
        "status": "ready" if not unresolved and not external_paths else "blocked",
    }
    output = run_dir / "artifacts" / "inference_dependency_report.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
