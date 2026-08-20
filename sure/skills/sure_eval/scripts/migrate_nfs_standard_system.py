#!/usr/bin/env python3
"""One-time audited migration from historical protocol labels to standard_system."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "site" / "loader.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from sure.site.loader import load_site_policy

_configured_policy = load_site_policy()
DEFAULT_RESULTS_ROOT = (
    Path(_configured_policy["policy"]["storage"]["approved_results_roots"][0])
    if _configured_policy
    else None
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalize_protocol_ids(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "standard_system"
                if key in {"protocol_id", "protocol_argument"}
                else _normalize_protocol_ids(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize_protocol_ids(item) for item in value]
    if isinstance(value, str):
        return value.replace("strict_core", "standard_system").replace("strict_one", "standard_system")
    return value


def _render_yaml(path: Path) -> bytes:
    text = path.read_text(encoding="utf-8")
    yaml.safe_load(text)
    text = text.replace("strict_core", "standard_system").replace("strict_one", "standard_system")
    text = re.sub(
        r"(?m)^(\s*(?:protocol_id|protocol_argument):\s*)[^\s#]+",
        r"\1standard_system",
        text,
    )
    return text.encode("utf-8")


def _render_json(path: Path) -> bytes:
    text = path.read_text(encoding="utf-8")
    value = json.loads(text)
    normalized = _normalize_protocol_ids(value)
    if normalized == value and "strict_core" not in text and "strict_one" not in text:
        return text.encode("utf-8")
    return (json.dumps(normalized, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _render_jsonl(path: Path) -> bytes:
    text = path.read_text(encoding="utf-8")
    rendered: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path}:{number}: {exc}") from exc
        normalized = _normalize_protocol_ids(value)
        rendered.append(
            line
            if normalized == value and "strict_core" not in line and "strict_one" not in line
            else json.dumps(normalized, ensure_ascii=False)
        )
    return ("\n".join(rendered) + ("\n" if text.endswith("\n") else "")).encode("utf-8")


def _render_markdown(path: Path) -> bytes:
    return (
        path.read_text(encoding="utf-8")
        .replace("strict_core", "standard_system")
        .replace("strict_one", "standard_system")
        .encode("utf-8")
    )


def plan_migration(root: Path) -> dict[str, Any]:
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    candidates = set(root.rglob("protocol.yaml"))
    candidates.update(path.parent / "report.jsonl" for path in root.rglob("protocol.yaml"))
    candidates.update(root.rglob("report_snapshot.md"))
    candidates.update(root.glob("*.json"))
    candidates.update(root.glob("*.md"))
    changed_files: list[dict[str, Any]] = []
    for path in sorted(path for path in candidates if path.is_file()):
        original = path.read_bytes()
        if path.suffix in {".yaml", ".yml"}:
            updated = _render_yaml(path)
        elif path.suffix == ".jsonl":
            updated = _render_jsonl(path)
        elif path.suffix == ".md":
            updated = _render_markdown(path)
        else:
            updated = _render_json(path)
        if original != updated:
            changed_files.append(
                {
                    "path": str(path),
                    "relative_path": str(path.relative_to(root)),
                    "before_sha256": _sha256_bytes(original),
                    "after_sha256": _sha256_bytes(updated),
                    "before_size": len(original),
                    "after_size": len(updated),
                }
            )
    renames: list[dict[str, str]] = []
    for old_protocol in ("strict_core", "strict_one"):
        for source in sorted(root.rglob(old_protocol), key=lambda item: len(item.parts), reverse=True):
            if not source.is_dir():
                continue
            target = source.with_name("standard_system")
            if target.exists():
                raise FileExistsError(f"migration directory collision: {source} -> {target}")
            renames.append({"source": str(source), "target": str(target)})
    return {
        "schema": "sure.eval.standard_system_migration.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results_root": str(root),
        "from_protocol": "strict_core",
        "to_protocol": "standard_system",
        "changed_files": changed_files,
        "directory_renames": renames,
    }


def apply_migration(plan: dict[str, Any], backup_dir: Path) -> dict[str, Any]:
    root = Path(plan["results_root"]).resolve()
    backup_dir = backup_dir.resolve()
    if backup_dir.exists() and any(backup_dir.iterdir()):
        raise FileExistsError(f"backup directory must be absent or empty: {backup_dir}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    for item in plan["changed_files"]:
        path = Path(item["path"])
        original = path.read_bytes()
        if _sha256_bytes(original) != item["before_sha256"]:
            raise ValueError(f"file changed after dry-run: {path}")
        backup = backup_dir / item["relative_path"]
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        if path.suffix in {".yaml", ".yml"}:
            updated = _render_yaml(path)
        elif path.suffix == ".jsonl":
            updated = _render_jsonl(path)
        elif path.suffix == ".md":
            updated = _render_markdown(path)
        else:
            updated = _render_json(path)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.migration-tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(updated)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, path.stat().st_mode)
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
    for item in plan["directory_renames"]:
        source = Path(item["source"])
        target = Path(item["target"])
        if not source.is_dir() or target.exists():
            raise ValueError(f"directory rename precondition changed: {source} -> {target}")
        source.rename(target)
    applied = dict(plan)
    applied["applied_at"] = datetime.now(timezone.utc).isoformat()
    applied["backup_dir"] = str(backup_dir)
    applied["status"] = "applied"
    manifest = backup_dir / "migration_manifest.json"
    manifest.write_text(json.dumps(applied, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return applied


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate approved NFS result protocol labels")
    parser.add_argument("--results-root")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        if args.results_root:
            results_root = Path(args.results_root)
        elif DEFAULT_RESULTS_ROOT is not None:
            results_root = DEFAULT_RESULTS_ROOT
        else:
            resolved = load_site_policy(required=True)
            results_root = Path(resolved["policy"]["storage"]["approved_results_roots"][0])
        plan = plan_migration(results_root)
        if args.apply:
            if not args.backup_dir:
                raise ValueError("--apply requires --backup-dir")
            plan = apply_migration(plan, Path(args.backup_dir))
    except (FileExistsError, FileNotFoundError, ValueError, yaml.YAMLError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    text = json.dumps(plan, indent=2, ensure_ascii=False)
    print(text)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
