#!/usr/bin/env python3
"""Deterministic dependency evidence for Python and shell entrypoints.

This module keeps dependency discovery conservative without assuming that every
inference entrypoint is Python.  It records facts that can be checked locally
and marks dynamic shell/Python references for the main Agent to review.  The
Agent review never replaces path containment or file-existence checks.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any


LANGUAGES = {"python", "bash", "sh", "unknown"}
REVIEW_DISPOSITIONS = {
    "resolved_within_allowed_root",
    "optional_branch",
    "runtime_generated_within_allowed_root",
    "missing_required",
    "external_required",
    "runtime_conflict",
}
SAFE_REVIEW_DISPOSITIONS = {
    "resolved_within_allowed_root",
    "optional_branch",
    "runtime_generated_within_allowed_root",
}
HARD_REVIEW_DISPOSITIONS = {"missing_required", "external_required", "runtime_conflict"}
PYTHON_LIMIT = 5000
MANIFEST_PATH_SUFFIXES = {
    ".awk",
    ".bin",
    ".ckpt",
    ".conf",
    ".csv",
    ".json",
    ".jsonl",
    ".mp3",
    ".onnx",
    ".py",
    ".pth",
    ".safetensors",
    ".sh",
    ".so",
    ".txt",
    ".wav",
    ".yaml",
    ".yml",
}
PATH_FLAGS = {
    "--audio",
    "--audio-path",
    "--checkpoint",
    "--checkpoint-path",
    "--config",
    "--config-path",
    "--file",
    "--input",
    "--input-path",
    "--manifest",
    "--model",
    "--model-path",
    "--output",
    "--output-path",
    "--prompt-audio",
    "--prompt-audio-path",
    "--reference-audio",
    "--reference-audio-path",
    "--runtime",
    "--runtime-path",
    "--source",
    "--source-path",
    "--target",
    "--vocoder",
    "--vocoder-path",
}
OUTPUT_FLAGS = {"-o", "--log", "--log-path", "--output", "--output-path"}
SHELL_COMMANDS_WITH_PATH_ARGS = {
    "awk",
    "cat",
    "cd",
    "cp",
    "gawk",
    "ln",
    "mv",
    "sed",
    "sox",
    "tar",
}
SHELL_INTERPRETERS = {"bash", "python", "python3", "python3.11", "python3.12", "sh", "zsh"}
SYSTEM_PATH_PREFIXES = (
    "/bin/",
    "/dev/",
    "/lib/",
    "/lib64/",
    "/opt/sure_trans/",
    "/proc/",
    "/sbin/",
    "/sys/",
    "/tmp/",
    "/usr/",
    "/var/run/",
)
PATH_ASSIGNMENT_WORDS = ("audio", "checkpoint", "config", "dir", "file", "model", "path", "runtime", "vocoder")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def docker_instructions(path: Path) -> list[tuple[str, str]]:
    logical: list[tuple[str, str]] = []
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
    if current:
        parts = current.split(None, 1)
        logical.append((parts[0].upper(), parts[1] if len(parts) > 1 else ""))
    return logical


def copy_sources(instructions: list[tuple[str, str]]) -> list[str]:
    sources: list[str] = []
    for instruction, value in instructions:
        if instruction not in {"COPY", "ADD"}:
            continue
        value = value.strip()
        if value.startswith("["):
            items = json.loads(value)
            if isinstance(items, list):
                sources.extend(str(item) for item in items[:-1])
            continue
        tokens = shlex.split(value)
        tokens = [token for token in tokens if not token.startswith("--")]
        sources.extend(tokens[:-1])
    return sorted(set(sources))


def inside(path: Path, root: Path) -> bool:
    try:
        candidate = path.resolve()
        base = root.resolve()
        return candidate == base or candidate.is_relative_to(base)
    except (OSError, ValueError):
        return False


def detect_entrypoint_language(path: Path) -> str:
    try:
        first_line = path.read_text(encoding="utf-8", errors="replace").splitlines()[0].lower()
    except (OSError, IndexError):
        first_line = ""
    if first_line.startswith("#!"):
        if re.search(r"(?:^|\s|/)bash(?:\s|$)", first_line):
            return "bash"
        if re.search(r"(?:^|\s|/)sh(?:\s|$)", first_line):
            return "sh"
    if path.suffix.lower() in {".sh", ".bash"}:
        return "bash"
    if path.suffix.lower() == ".py":
        return "python"
    return "unknown"


class DependencyVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.imports: set[str] = set()
        self.string_paths: set[str] = set()
        self.commands: set[str] = set()
        self.dynamic_references: list[dict[str, Any]] = []

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.update(alias.name.split(".")[0] for alias in node.names)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.imports.add(node.module.split(".")[0])
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = ""
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        path_call = name in {"open", "Path", "read_text", "read_bytes", "CDLL"}
        command_call = name in {"run", "Popen", "call", "check_call", "check_output"}
        if (path_call or command_call) and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                value = first.value
                if path_call:
                    self.string_paths.add(value)
                if command_call:
                    self.commands.add(value)
            else:
                self.dynamic_references.append(
                    {
                        "id": f"python-dynamic:{self.path}:{node.lineno}:{name}",
                        "kind": "dynamic_reference",
                        "language": "python",
                        "operation": name,
                        "file": str(self.path),
                        "line": node.lineno,
                        "strength": "review",
                        "reason": "call argument is computed at runtime",
                    }
                )
        self.generic_visit(node)


def _path_like(token: str) -> bool:
    if not token or token.startswith("-"):
        return False
    if token.startswith(("http://", "https://")):
        return False
    if token.startswith(("/", "./", "../", "~/", "$", "${")):
        return True
    return "/" in token or Path(token).suffix.lower() in MANIFEST_PATH_SUFFIXES


def _system_path(token: str) -> bool:
    return token.startswith(SYSTEM_PATH_PREFIXES) or token in {"/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr"}


def _shell_lines(text: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    current = ""
    start = 1
    for number, raw in enumerate(text.splitlines(), 1):
        if not current:
            start = number
        current = f"{current} {raw.strip()}".strip()
        if current.endswith("\\"):
            current = current[:-1].rstrip()
            continue
        lines.append((start, current))
        current = ""
    if current:
        lines.append((start, current))
    return lines


def _shell_tokens(line: str) -> list[str]:
    try:
        return shlex.split(line, comments=True, posix=True)
    except ValueError:
        return []


def _command_index(tokens: list[str]) -> int:
    wrappers = {"command", "env", "exec", "nohup", "sudo", "time"}
    for index, token in enumerate(tokens):
        if "=" in token and not token.startswith(("./", "../", "/")):
            continue
        if token in wrappers:
            continue
        return index
    return len(tokens)


def _normalise_token(token: str) -> str:
    return token.strip(";,()[]{}")


def _shell_path_candidates(tokens: list[str]) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for token in tokens:
        if "=" not in token or token.startswith(("./", "../", "/")):
            continue
        key, value = token.split("=", 1)
        if any(word in key.lower() for word in PATH_ASSIGNMENT_WORDS) and _path_like(value):
            candidates.append((value, "assignment"))
    command_index = _command_index(tokens)
    if command_index >= len(tokens):
        return candidates
    command = Path(tokens[command_index]).name.lower()
    if command in {"source", "."} and command_index + 1 < len(tokens):
        candidates.append((tokens[command_index + 1], "source"))
    if command in SHELL_INTERPRETERS:
        arguments = tokens[command_index + 1 :]
        if arguments and (arguments[0] in {"-c", "-C"} or (arguments[0].startswith("-") and "c" in arguments[0])):
            candidates.append(("<computed>", "shell_command"))
        elif "-m" not in arguments:
            for token in arguments:
                if not token.startswith("-"):
                    candidates.append((token, "script"))
                    break
    if command in SHELL_COMMANDS_WITH_PATH_ARGS:
        for token in tokens[command_index + 1 :]:
            if _path_like(token):
                candidates.append((token, "command_argument"))
    for index, token in enumerate(tokens):
        if token.startswith("--") and "=" in token:
            flag, value = token.split("=", 1)
            if flag in PATH_FLAGS:
                operation = "output_argument" if flag in OUTPUT_FLAGS else "flag_argument"
                candidates.append((value, operation))
        elif token in PATH_FLAGS and index + 1 < len(tokens):
            candidates.append((tokens[index + 1], "flag_argument"))
    if command_index < len(tokens) and _path_like(tokens[command_index]):
        candidates.append((tokens[command_index], "command"))
    return candidates


def shell_evidence(path: Path, text: str, build_context: Path, model_path: Path) -> tuple[list[dict[str, Any]], list[str], list[str], list[dict[str, Any]], set[str]]:
    records: list[dict[str, Any]] = []
    literal_paths: list[str] = []
    commands: list[str] = []
    dynamic: list[dict[str, Any]] = []
    support_paths: set[str] = set()
    for line_number, line in _shell_lines(text):
        tokens = [_normalise_token(token) for token in _shell_tokens(line)]
        if not tokens:
            continue
        command_index = _command_index(tokens)
        command = Path(tokens[command_index]).name.lower() if command_index < len(tokens) else ""
        if command in {"eval", "bash", "sh", "python", "python3"} and any(
            token in {"-c", "-C"}
            or (token.startswith("-") and "c" in token[1:])
            or "$" in token
            or "$(" in token
            for token in tokens[command_index + 1 :]
        ):
            signal = {
                "id": f"shell-dynamic:{path}:{line_number}:command",
                "kind": "dynamic_reference",
                "language": "shell",
                "operation": command or "shell",
                "file": str(path),
                "line": line_number,
                "strength": "review",
                "reason": "shell command or path is computed at runtime",
            }
            dynamic.append(signal)
            records.append(signal)
        for raw_token, operation in _shell_path_candidates(tokens):
            token = raw_token.strip("'\"")
            if token == "<computed>" or "$" in token or "`" in token or "*" in token:
                signal = {
                    "id": f"shell-dynamic:{path}:{line_number}:{operation}:{raw_token}",
                    "kind": "dynamic_reference",
                    "language": "shell",
                    "operation": operation,
                    "token": raw_token,
                    "file": str(path),
                    "line": line_number,
                    "strength": "review",
                    "reason": "path contains shell expansion or a wildcard",
                }
                if signal["id"] not in {item["id"] for item in dynamic}:
                    dynamic.append(signal)
                    records.append(signal)
                continue
            if not _path_like(token) or _system_path(token):
                continue
            literal_paths.append(token)
            commands.append(token) if operation in {"script", "command"} else None
            base = path.parent
            candidate = Path(token).expanduser() if Path(token).is_absolute() else base / token
            resolved = candidate.resolve()
            record: dict[str, Any] = {
                "id": f"shell-path:{path}:{line_number}:{operation}:{token}",
                "kind": "path_reference",
                "language": "shell",
                "operation": operation,
                "path": token,
                "resolved_path": str(resolved),
                "file": str(path),
                "line": line_number,
                "strength": "hard",
            }
            if operation == "output_argument":
                continue
            if operation in {"flag_argument", "command_argument"} and tokens.index(raw_token) > 0:
                previous = tokens[tokens.index(raw_token) - 1]
                if previous in OUTPUT_FLAGS:
                    continue
            if Path(token).is_absolute() and not (inside(resolved, build_context) or inside(resolved, model_path)):
                record["classification"] = "external"
                records.append(record)
                continue
            if not resolved.exists():
                record["classification"] = "missing"
                records.append(record)
                continue
            record["classification"] = "allowed"
            records.append(record)
            support_paths.add(str(resolved if resolved.is_dir() else resolved.parent))
    return records, sorted(set(literal_paths)), sorted(set(commands)), dynamic, support_paths


def _python_files(support_paths: set[str], entrypoint: Path) -> list[Path]:
    files: set[Path] = set()
    if entrypoint.is_file() and entrypoint.suffix.lower() == ".py":
        files.add(entrypoint.resolve())
    for raw_root in sorted(support_paths):
        root = Path(raw_root)
        if root.is_file() and root.suffix.lower() == ".py":
            files.add(root.resolve())
        elif root.is_dir():
            for path in root.rglob("*.py"):
                files.add(path.resolve())
                if len(files) >= PYTHON_LIMIT:
                    return sorted(files)[:PYTHON_LIMIT]
    return sorted(files)[:PYTHON_LIMIT]


def _path_record(
    path: Path,
    value: str,
    operation: str,
    build_context: Path,
    model_path: Path,
    language: str,
    strength: str = "hard",
) -> dict[str, Any] | None:
    if not value or value.startswith(("http://", "https://")):
        return None
    candidate = Path(value).expanduser() if Path(value).is_absolute() else path.parent / value
    resolved = candidate.resolve()
    record: dict[str, Any] = {
        "id": f"{language}-path:{path}:{operation}:{value}",
        "kind": "path_reference",
        "language": language,
        "operation": operation,
        "path": value,
        "resolved_path": str(resolved),
        "file": str(path),
        "strength": strength,
    }
    if Path(value).is_absolute() and not (inside(resolved, build_context) or inside(resolved, model_path)):
        record["classification"] = "external"
    elif not resolved.exists():
        record["classification"] = "missing"
    else:
        record["classification"] = "allowed"
    return record


def _source_records(sources: list[str], build_context: Path) -> tuple[list[dict[str, Any]], set[str]]:
    records: list[dict[str, Any]] = []
    support_paths: set[str] = set()
    for source in sources:
        source_path = Path(source)
        record: dict[str, Any] = {
            "id": f"docker-copy:{source}",
            "kind": "docker_copy",
            "source": source,
            "strength": "hard",
        }
        if source_path.is_absolute():
            record["classification"] = "external"
            record["resolved_path"] = str(source_path)
            records.append(record)
            continue
        matches = sorted(build_context.glob(source)) if any(char in source for char in "*?[") else [build_context / source]
        existing = [candidate for candidate in matches if candidate.exists()]
        if not existing:
            record["classification"] = "missing"
            records.append(record)
            continue
        record["classification"] = "allowed"
        record["resolved_paths"] = [str(candidate.resolve()) for candidate in existing]
        records.append(record)
        support_paths.update(str(candidate.resolve()) for candidate in existing)
    return records, support_paths


def _syntax_check(path: Path, language: str, text: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if language == "python":
        try:
            ast.parse(text, filename=str(path))
        except SyntaxError as error:
            record = {
                "id": f"syntax:{path}",
                "kind": "syntax_error",
                "language": language,
                "file": str(path),
                "line": error.lineno or 1,
                "strength": "hard",
                "reason": str(error.msg),
            }
            return {"status": "failed", "language": language, "error": str(error)}, record
        return {"status": "passed", "language": language, "method": "ast.parse"}, None
    if language not in {"bash", "sh"}:
        return (
            {"status": "unknown", "language": language},
            {
                "id": f"language:{path}",
                "kind": "language_unknown",
                "language": language,
                "file": str(path),
                "strength": "review",
                "reason": "entrypoint language could not be identified",
            },
        )
    interpreter = language
    try:
        result = subprocess.run(
            [interpreter, "-n", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        record = {
            "id": f"syntax:{path}",
            "kind": "syntax_check_unavailable",
            "language": language,
            "file": str(path),
            "strength": "hard",
            "reason": type(error).__name__,
        }
        return {"status": "failed", "language": language, "error": str(error)}, record
    if result.returncode != 0:
        record = {
            "id": f"syntax:{path}",
            "kind": "syntax_error",
            "language": language,
            "file": str(path),
            "strength": "hard",
            "reason": (result.stderr or result.stdout or "shell syntax check failed")[-2000:],
        }
        return {"status": "failed", "language": language, "command": [interpreter, "-n", str(path)], "error": record["reason"]}, record
    return {"status": "passed", "language": language, "method": f"{interpreter} -n"}, None


def collect_dependency_evidence(resolved: dict[str, Any]) -> dict[str, Any]:
    build_context = Path(str(resolved["build_context"])).resolve()
    entrypoint = Path(str(resolved["inference_entrypoint"])).resolve()
    model_path = Path(str(resolved["model_path"])).resolve()
    language = detect_entrypoint_language(entrypoint)
    source_kind = str(resolved.get("source_kind") or "docker")
    sources: list[str] = []
    if source_kind == "docker" or resolved.get("dockerfile"):
        dockerfile = Path(str(resolved["dockerfile"])).resolve()
        sources = copy_sources(docker_instructions(dockerfile))
    records, support_paths = _source_records(sources, build_context)
    dependency_file = (
        Path(str(resolved["dependency_file"])).resolve()
        if resolved.get("dependency_file")
        else None
    )
    if source_kind == "python" and dependency_file is not None:
        support_paths.add(str(dependency_file))
    unresolved: list[str] = []
    external_paths: list[str] = []
    if inside(entrypoint, build_context):
        support_paths.add(str(entrypoint.parent.resolve()))
    else:
        external_paths.append(str(entrypoint))
        records.append(
            {
                "id": f"entrypoint-external:{entrypoint}",
                "kind": "entrypoint",
                "path": str(entrypoint),
                "classification": "external",
                "strength": "hard",
            }
        )

    literal_file_references: set[str] = set()
    subprocess_references: set[str] = set()
    dynamic_references: list[dict[str, Any]] = []
    syntax: dict[str, Any]
    try:
        entrypoint_text = entrypoint.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        entrypoint_text = ""
        syntax = {"status": "failed", "language": language, "error": str(error)}
        records.append(
            {
                "id": f"read:{entrypoint}",
                "kind": "read_error",
                "language": language,
                "file": str(entrypoint),
                "strength": "hard",
                "reason": type(error).__name__,
            }
        )
    else:
        syntax, syntax_record = _syntax_check(entrypoint, language, entrypoint_text)
        if syntax_record:
            records.append(syntax_record)
        if language in {"bash", "sh"}:
            shell_records, paths, commands, dynamic, shell_support = shell_evidence(
                entrypoint, entrypoint_text, build_context, model_path
            )
            records.extend(shell_records)
            literal_file_references.update(paths)
            subprocess_references.update(commands)
            dynamic_references.extend(dynamic)
            support_paths.update(shell_support)
        elif language == "python":
            visitor = DependencyVisitor(entrypoint)
            try:
                visitor.visit(ast.parse(entrypoint_text, filename=str(entrypoint)))
            except SyntaxError:
                pass
            literal_file_references.update(visitor.string_paths)
            subprocess_references.update(visitor.commands)
            dynamic_references.extend(visitor.dynamic_references)

    # Parse Python files that are part of the declared Docker/support closure.
    # This discovers imports in scripts called by a shell wrapper without
    # treating a shell file as Python.
    for python_path in _python_files(support_paths, entrypoint):
        try:
            text = python_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(text, filename=str(python_path))
        except (OSError, SyntaxError) as error:
            if python_path == entrypoint:
                continue
            records.append(
                {
                    "id": f"parse:{python_path}",
                    "kind": "parse_error",
                    "language": "python",
                    "file": str(python_path),
                    "strength": "review",
                    "reason": type(error).__name__,
                }
            )
            continue
        visitor = DependencyVisitor(python_path)
        visitor.visit(tree)
        for module in visitor.imports:
            records.append(
                {
                    "id": f"python-import:{python_path}:{module}",
                    "kind": "python_import",
                    "module": module,
                    "file": str(python_path),
                    "language": "python",
                    "strength": "support",
                }
            )
        literal_file_references.update(visitor.string_paths)
        subprocess_references.update(visitor.commands)
        dynamic_references.extend(visitor.dynamic_references)
        path_strength = "hard" if python_path == entrypoint else "review"
        for raw_path in visitor.string_paths:
            record = _path_record(
                python_path,
                raw_path,
                "python_literal",
                build_context,
                model_path,
                "python",
                strength=path_strength,
            )
            if record:
                records.append(record)

    for record in records:
        classification = record.get("classification")
        if classification == "missing" and record.get("strength") == "hard":
            unresolved.append(str(record.get("path") or record.get("source") or record.get("resolved_path")))
        elif classification == "external" and record.get("strength") == "hard":
            external_paths.append(str(record.get("path") or record.get("resolved_path") or record.get("source")))

    records.extend(dynamic_references)
    records = sorted({str(item["id"]): item for item in records}.values(), key=lambda item: str(item["id"]))
    hard_conflicts = [
        item
        for item in records
        if (
            item.get("strength") == "hard" and item.get("classification") in {"missing", "external"}
        )
        or item.get("kind") in {"syntax_error", "syntax_check_unavailable", "read_error"}
    ]
    review_signals = [
        item
        for item in records
        if item.get("strength") == "review"
        and (
            item.get("kind") in {"dynamic_reference", "parse_error", "language_unknown"}
            or item.get("classification") in {"missing", "external"}
        )
    ]
    evidence = {
        "entrypoint": str(entrypoint),
        "entrypoint_language": language,
        "syntax_check": syntax,
        "build_context": str(build_context),
        "docker_copy_sources": list(sources),
        "python_imports": sorted(
            {
                str(item["module"])
                for item in records
                if item.get("kind") == "python_import" and item.get("module")
            }
        ),
        "literal_file_references": sorted(set(literal_file_references)),
        "subprocess_references": sorted(set(subprocess_references)),
        "support_paths": sorted(support_paths),
        "model_path": str(model_path),
        "unresolved": sorted(set(unresolved)),
        "external_paths": sorted(set(external_paths)),
        "dependency_evidence": records,
        "hard_conflicts": hard_conflicts,
        "review_signals": review_signals,
        "dynamic_references": sorted(dynamic_references, key=lambda item: str(item["id"])),
        "dynamic_validation_required": bool(review_signals),
    }
    # A direct Python entrypoint's imports were historically reported even when
    # no support directory was available. Keep that behavior while allowing
    # shell wrappers to discover imports from their called Python files.
    if language == "python":
        direct = DependencyVisitor(entrypoint)
        try:
            direct.visit(ast.parse(entrypoint_text, filename=str(entrypoint)))
        except SyntaxError:
            pass
        evidence["python_imports"] = sorted(set(evidence["python_imports"]) | direct.imports)
    return evidence


def evidence_digest(evidence: dict[str, Any]) -> str:
    canonical = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def review_is_valid(review: object, review_signals: list[dict[str, Any]], allowed_roots: list[Path]) -> tuple[bool, str]:
    if not isinstance(review, dict):
        return False, "agent_review must be an object"
    if review.get("schema") != "sure.trans.dependencies_review.v1":
        return False, "agent_review.schema must be sure.trans.dependencies_review.v1"
    disposition = review.get("disposition")
    if disposition not in REVIEW_DISPOSITIONS:
        return False, "agent_review.disposition is invalid"
    explanation = review.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        return False, "agent_review.explanation must be non-empty"
    cited = review.get("evidence")
    available = {str(item.get("id")) for item in review_signals}
    if not isinstance(cited, list) or not cited:
        return False, "agent_review.evidence must cite review signal IDs"
    if any(not isinstance(item, str) or item not in available for item in cited):
        return False, "agent_review.evidence must cite only generated review signal IDs"
    paths = review.get("resolved_paths", [])
    if not isinstance(paths, list) or any(not isinstance(item, str) for item in paths):
        return False, "agent_review.resolved_paths must be an array of strings"
    if disposition in {"resolved_within_allowed_root", "runtime_generated_within_allowed_root"}:
        if not paths:
            return False, "an allowed-root review must provide resolved_paths"
        for raw_path in paths:
            path = Path(raw_path).expanduser()
            if not path.exists():
                return False, f"Agent resolved path does not exist: {raw_path}"
            if not any(inside(path, root) for root in allowed_roots):
                return False, f"Agent resolved path is outside allowed roots: {raw_path}"
    return True, ""


def dependency_status(evidence: dict[str, Any], review: object = None) -> str:
    if evidence.get("hard_conflicts") or evidence.get("unresolved") or evidence.get("external_paths"):
        return "blocked"
    signals = evidence.get("review_signals") or []
    if not signals:
        return "ready"
    if not isinstance(review, dict):
        return "needs_review"
    if review.get("disposition") in HARD_REVIEW_DISPOSITIONS:
        return "blocked"
    return "ready"
