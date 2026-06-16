#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.xforge_collect_model import collect_remote_model_source
from xforge_sure_bridge.bridge import (
    BridgeError,
    emit_sure_model_agent_handoff,
    materialize_model_manifest,
    process_dataset_manifest,
    process_dataset_manifest_to_oref,
    write_oref_registry_entry,
    write_summary,
)
from xforge_sure_bridge.tool_agent_controller import accept_tool_agent_handoff
from xforge_sure_bridge.modelscope_fetch import (
    build_selected_candidate,
    emit_selected_resource_artifacts,
    emit_sure_integration_plan,
    write_fetch_failure,
    write_fetch_success,
)
from xforge_sure_bridge.modelscope_watcher import slugify


def _load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load_mapping(path: str | Path) -> dict:
    mapping_path = Path(path)
    if mapping_path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as exc:
            raise BridgeError("pyyaml is required for YAML schema mappings") from exc
        data = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    else:
        data = json.loads(mapping_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BridgeError("schema mapping must be a JSON/YAML object")
    return data


def _dataset_output_path(sure_dataset_dir: Path, sure_name: str) -> Path:
    return sure_dataset_dir / f"{sure_name}.jsonl"


def _model_dir(model_root: Path, resource_id: str) -> Path:
    return model_root / slugify(resource_id)


def _run_local_uv_bootstrap(model_dir: Path) -> dict:
    model_dir = model_dir.resolve()
    setup = model_dir / "local_uv_setup.sh"
    validate = model_dir / "local_uv_validate.sh"
    if not setup.exists() or not validate.exists():
        raise BridgeError("local uv bootstrap scripts are missing")
    setup_completed = subprocess.run(
        [str(setup)],
        cwd=model_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if setup_completed.returncode != 0:
        raise BridgeError(
            "local uv setup failed: "
            + (setup_completed.stderr.strip() or setup_completed.stdout.strip())
        )
    validate_completed = subprocess.run(
        [str(validate)],
        cwd=model_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if validate_completed.returncode != 0:
        raise BridgeError(
            "local uv validation failed: "
            + (validate_completed.stderr.strip() or validate_completed.stdout.strip())
        )
    return {
        "setup_script": str(setup),
        "validate_script": str(validate),
        "setup_returncode": setup_completed.returncode,
        "validate_returncode": validate_completed.returncode,
        "local_uv_env": str((model_dir / "artifacts" / "local_uv_env.json").resolve()),
        "local_uv_validation": str((model_dir / "artifacts" / "local_uv_validation.json").resolve()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch a human-selected ModelScope model or dataset")
    parser.add_argument("--resource", choices=["model", "dataset"], required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--id", required=True)
    parser.add_argument("--name")
    parser.add_argument("--language")
    parser.add_argument("--manifest-dir", default="data/artifacts/xforge/modelscope/manifests")
    parser.add_argument("--handoff-dir", default="data/artifacts/xforge/modelscope/handoff")
    parser.add_argument("--sure-plan-dir", default="data/artifacts/xforge/modelscope/sure_plans")
    parser.add_argument("--fetch-run-dir", default="data/artifacts/xforge/modelscope/fetch_runs")
    parser.add_argument("--model-root", default="src/sure_eval/models")
    parser.add_argument("--sure-dataset-dir", default="data/datasets/xforge_sure")
    parser.add_argument("--oref-local", action="store_true", help="Also materialize dataset as local OREF layout")
    parser.add_argument("--oref-dataset-root", default="data/datasets", help="Root directory for OREF local datasets")
    parser.add_argument("--oref-config", default="config/oref_datasets.yaml", help="OREF dataset registry YAML")
    parser.add_argument(
        "--allow-missing-audio",
        action="store_true",
        help="Write OREF placeholder audio paths when raw audio files are unavailable",
    )
    parser.add_argument("--source-provider", choices=["modelscope", "local"], default="modelscope")
    parser.add_argument("--source-path", help="Local model path when --source-provider local")
    parser.add_argument("--schema-mapping", help="YAML or JSON mapping for dataset conversion to SURE JSONL")
    parser.add_argument("--no-download", action="store_true", help="Emit manifest/handoff only")
    parser.add_argument(
        "--skip-local-uv-setup",
        action="store_true",
        help="Generate local uv scripts but do not execute model-local .venv bootstrap",
    )
    args = parser.parse_args()

    command = sys.argv[:]
    try:
        candidate = build_selected_candidate(
            resource_type=args.resource,
            task=args.task,
            resource_id=args.id,
            name=args.name,
            language=args.language,
        )
        artifacts = emit_selected_resource_artifacts(
            candidate=candidate,
            manifest_dir=args.manifest_dir,
            handoff_dir=args.handoff_dir,
        )
        result = {
            "provider": "modelscope",
            "resource_type": args.resource,
            "task": args.task,
            "resource_id": args.id,
            **artifacts,
        }
        manifest = _load_json(artifacts["manifest_path"])
        plan_model_dir = _model_dir(Path(args.model_root), args.id)
        sure_plan_path = emit_sure_integration_plan(
            resource_type=args.resource,
            manifest=manifest,
            manifest_path=artifacts["manifest_path"],
            handoff_path=artifacts["handoff_path"],
            sure_plan_dir=args.sure_plan_dir,
            model_dir=plan_model_dir,
            sure_dataset_dir=args.sure_dataset_dir,
        )
        result["sure_plan_path"] = sure_plan_path

        if args.resource == "model" and not args.no_download:
            collect_input_manifest = manifest
            if args.source_provider == "local":
                if not args.source_path:
                    raise BridgeError("--source-path is required when --source-provider local")
                collect_input_manifest = {
                    **manifest,
                    "source": {
                        "provider": "local",
                        "id": args.source_path,
                        "original_source": manifest["source"],
                    },
                }
            model_dir = _model_dir(Path(args.model_root), args.id)
            collected_manifest = collect_remote_model_source(collect_input_manifest, model_dir)
            collect_summary = materialize_model_manifest(collected_manifest, model_dir)
            summary_path = model_dir / "artifacts" / "xforge_collect_summary.json"
            write_summary(summary_path, collect_summary)
            sure_handoff = emit_sure_model_agent_handoff(
                manifest=manifest,
                manifest_path=artifacts["manifest_path"],
                handoff_path=artifacts["handoff_path"],
                model_dir=model_dir,
            )
            if not args.skip_local_uv_setup:
                local_uv = _run_local_uv_bootstrap(model_dir)
                sure_handoff = emit_sure_model_agent_handoff(
                    manifest=manifest,
                    manifest_path=artifacts["manifest_path"],
                    handoff_path=artifacts["handoff_path"],
                    model_dir=model_dir,
                )
                result["local_uv_bootstrap"] = local_uv
            tool_agent = accept_tool_agent_handoff(model_dir / "artifacts" / "tool_agent_request.json")
            result["tool_agent_controller"] = tool_agent
            result["collect_summary"] = collect_summary
            result["collect_summary_path"] = str(summary_path)
            result["sure_model_agent_handoff"] = sure_handoff

        if args.resource == "model" and args.no_download:
            model_dir = _model_dir(Path(args.model_root), args.id)
            sure_handoff = emit_sure_model_agent_handoff(
                manifest=manifest,
                manifest_path=artifacts["manifest_path"],
                handoff_path=artifacts["handoff_path"],
                model_dir=model_dir,
            )
            if not args.skip_local_uv_setup:
                local_uv = _run_local_uv_bootstrap(model_dir)
                sure_handoff = emit_sure_model_agent_handoff(
                    manifest=manifest,
                    manifest_path=artifacts["manifest_path"],
                    handoff_path=artifacts["handoff_path"],
                    model_dir=model_dir,
                )
                result["local_uv_bootstrap"] = local_uv
            tool_agent = accept_tool_agent_handoff(model_dir / "artifacts" / "tool_agent_request.json")
            result["tool_agent_controller"] = tool_agent
            result["sure_model_agent_handoff"] = sure_handoff

        if args.resource == "dataset" and args.schema_mapping:
            mapping = _load_mapping(args.schema_mapping)
            manifest["raw_root"] = str(mapping["raw_root"])
            manifest["raw_jsonl"] = str(mapping["raw_jsonl"])
            manifest["sure_name"] = str(mapping.get("sure_name") or manifest["sure_name"])
            manifest["language"] = str(mapping.get("language") or manifest["language"])
            manifest["field_mapping"] = dict(mapping["field_mapping"])
            output_path = _dataset_output_path(Path(args.sure_dataset_dir), manifest["sure_name"])
            dataset_summary = process_dataset_manifest(manifest, output_path)
            result["dataset_processing_summary"] = dataset_summary
            if args.oref_local:
                oref_summary = process_dataset_manifest_to_oref(
                    manifest,
                    args.oref_dataset_root,
                    allow_missing_audio=args.allow_missing_audio,
                )
                registry_summary = write_oref_registry_entry(
                    args.oref_config,
                    oref_summary["sure_name"],
                    oref_summary["registry_entry"],
                )
                result["oref_processing_summary"] = oref_summary
                result["oref_registry_summary"] = registry_summary

        success_path = write_fetch_success(args.fetch_run_dir, result)
        result["fetch_summary_path"] = success_path
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        failure_path = write_fetch_failure(
            fetch_run_dir=args.fetch_run_dir,
            resource_type=args.resource,
            task=args.task,
            resource_id=args.id,
            command=command,
            error=str(exc),
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        print(json.dumps({"fetch_summary_path": failure_path}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
