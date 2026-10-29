#!/usr/bin/env python3
"""Invoke the native SURE harness without replacing its workflow gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]


def commands(root: Path) -> dict[str, dict]:
    result = {
        "sure_init": {"command": "/sure_init", "kind": "builtin"},
        "sure_resume": {"command": "/sure_resume", "kind": "builtin"},
    }
    for path in sorted((root / "sure" / "skills").glob("*/sure.skill.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        command = manifest["command"].lstrip("/")
        result[command] = {
            "command": f"/{command}", "kind": "workflow",
            "description": manifest.get("description"),
            "manual": str(path.parent / manifest["prompt"]),
            "manifest": str(path),
        }
    return result


def fingerprint(path: Path) -> tuple[int, int] | None:
    return (path.stat().st_mtime_ns, path.stat().st_size) if path.exists() else None


def invoke(options: argparse.Namespace, root: Path = ROOT) -> tuple[dict, int]:
    installed = commands(root)
    if options.command == "list":
        return {"status": "success", "root": str(root), "commands": list(installed.values())}, 0
    name = options.command.lstrip("/")
    name = name if name.startswith("sure_") else f"sure_{name}"
    if name not in installed:
        raise ValueError(f"Unknown command {options.command!r}; use list for this checkout's commands.")
    arguments = options.args_file.read_text(encoding="utf-8") if options.args_file else options.arguments
    tokens = shlex.split(arguments)
    if any(token.startswith(("--api-key", "--api_key", "api_key=")) for token in tokens):
        raise ValueError("Use provider environment variables or native auth storage, not an API key argument.")
    if any(token.startswith(("output_dir=", "--output_dir")) for token in tokens):
        raise ValueError("Use the helper's --output-dir option instead of embedding output_dir.")
    builtin = installed[name]["kind"] == "builtin"
    if builtin and options.output_dir:
        raise ValueError("init/resume use native manifest locations; omit --output-dir.")
    receipt_dir = root / ".sure" / "agent-invocations" / uuid4().hex
    output = options.output_dir.expanduser().resolve() if options.output_dir else receipt_dir / "output"
    if not builtin and output.exists() and any(output.iterdir()):
        raise ValueError("Use a new or empty --output-dir so old results cannot count as this invocation.")
    evidence = output / "result.json"
    if name == "sure_init":
        evidence = root / ".sure" / "init.json"
    elif name == "sure_resume":
        if len(tokens) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]+", tokens[0]):
            raise ValueError("resume requires exactly one run ID in --arguments.")
        evidence = root / ".sure" / "runs" / tokens[0] / "run.json"
        if not evidence.exists():
            raise ValueError(f"Run not found: {tokens[0]}")
    prompt = f"/{name} {arguments}".strip()
    if not builtin:
        prompt += " output_dir=" + json.dumps(str(output))
    argv = [
        "node", "--import", (root / "packages/coding-agent/test/source-resolver.ts").as_uri(),
        str(root / "packages/coding-agent/src/cli.ts"),
        "--offline", "--mode", "json", "--no-session",
        "--approve" if options.trust else "--no-approve", "-p", prompt,
    ]
    if options.dry_run:
        return {"status": "planned", "command": f"/{name}", "cwd": str(root), "argv": argv}, 0
    if not (root / "node_modules" / "tsx").exists():
        raise ValueError("Missing harness dependencies; run npm ci --ignore-scripts in the SURE checkout.")
    previous = fingerprint(evidence)
    receipt_dir.mkdir(parents=True)
    with (receipt_dir / "harness.jsonl").open("w", encoding="utf-8") as stdout:
        with (receipt_dir / "harness.stderr.log").open("w", encoding="utf-8") as stderr:
            completed = subprocess.run(argv, cwd=root, stdout=stdout, stderr=stderr, check=False)
    result = {
        "status": "incomplete", "command": f"/{name}", "exit_code": completed.returncode,
        "receipt_dir": str(receipt_dir), "evidence_path": str(evidence),
    }
    if evidence.exists() and fingerprint(evidence) != previous:
        native = json.loads(evidence.read_text(encoding="utf-8"))
        if name == "sure_init":
            valid = native.get("trusted") is True and native.get("uvOk") is True
            valid = valid and bool(native.get("defaultProvider")) and bool(native.get("defaultModel"))
            result["status"] = "success" if valid else "incomplete"
        else:
            valid = name == "sure_resume" or (
                native.get("schema") == "sure.run_result.v1" and native.get("command") == f"/{name}"
            )
            if valid:
                result.update({
                    "status": native.get("status", "incomplete"),
                    "run_id": native.get("run_id", native.get("runId")),
                    "run_dir": native.get("run_dir", native.get("runDir")),
                    "products": native.get("products", []),
                    "error": native.get("error", native.get("errorSummary")),
                })
            else:
                result["error"] = "Native result does not match this invocation."
    else:
        result["error"] = "No fresh native result; inspect the harness logs for auth, trust or pre-start errors."
    if completed.returncode != 0:
        result["status"] = "failed"
    (receipt_dir / "receipt.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result, 0 if result["status"] == "success" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", help="list, init, feed, onboard, trans, approve, infer, eval, agent_eval, resume")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--arguments", default="", help="Native slash-command arguments as one string")
    group.add_argument("--args-file", type=Path, help="UTF-8 file containing native arguments")
    parser.add_argument("--output-dir", type=Path, help="New/empty native workflow output directory")
    parser.add_argument("--trust", action="store_true", help="Trust this SURE checkout for this invocation")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without executing")
    options = parser.parse_args()
    try:
        result, code = invoke(options)
    except (ValueError, OSError) as error:
        result, code = {"status": "blocked", "error": str(error)}, 1
    print(json.dumps(result, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
