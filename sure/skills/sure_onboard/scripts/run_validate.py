#!/usr/bin/env python3
"""Gate script for the VALIDATE_{IMPORT,LOAD,INFER,CONTRACT} units.

Routes by --kind to the corresponding minimal-validation test, then stamps the
matching *_passed boolean back into the artifact if the agent omitted it.

Kinds map to the minimal_validation contract (import/load/infer/contract):
    import    -> the model module imports without error
    load      -> the model object instantiates and loads weights
    infer     -> a minimal inference call produces output
    contract  -> the output satisfies model.spec.yaml io_contract

The actual execution is delegated to the sure_eval backend (inference/runner.py
+ protocols/resolver.py). This script re-confirms the *_passed flag and runs
the lightweight structural check; the heavy execution happens in the
generate_wrapper/validate.py template under sure/models/<model_id>/.

Called by the Sure hook:
    python3 scripts/run_validate.py --kind <import|load|infer|contract> \
        --run-dir <runDir> --produces <abs>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

KIND_TO_PASS_KEY = {
    "import": "import_passed",
    "load": "load_passed",
    "infer": "infer_passed",
    "contract": "contract_passed",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", required=True, choices=list(KIND_TO_PASS_KEY))
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    args = parser.parse_args()

    pass_key = KIND_TO_PASS_KEY[args.kind]
    path = Path(args.produces)
    if not path.exists():
        print(f"{args.kind}_result.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"{args.kind}_result.json is not valid JSON: {exc}", file=sys.stderr)
        return 1

    if not data.get(pass_key):
        error = data.get("error")
        detail = f"\n  error: {error}" if error else ""
        print(
            f"VALIDATE_{args.kind.upper()} gate failed: {pass_key} is false.{detail}",
            file=sys.stderr,
        )
        return 1

    # Cross-check: when the kind is load/infer/contract and a wrapper path /
    # model dir is declared, ensure the wrapper file exists.
    if args.kind in ("load", "infer", "contract"):
        model_dir = data.get("model_dir") or data.get("wrapper_path")
        if model_dir:
            candidate = Path(model_dir)
            # model_dir may point to the wrapper dir or a file; check parent.
            targets = [candidate, candidate.parent] if candidate.is_file() else [candidate]
            if not any(p.is_dir() and (p / "model.py").exists() for p in targets):
                print(
                    f"VALIDATE_{args.kind.upper()} gate: declared model dir/wrapper "
                    f"missing model.py: {model_dir}",
                    file=sys.stderr,
                )
                return 1

    print(f"run_validate OK: kind={args.kind}, {pass_key}=true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
