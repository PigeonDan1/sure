"""Development integration test, run with locked Harness Python.

Exercises real model/MCP/projection/evaluation backends. It does not create an
approval or represent a completed skill state machine.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--model-python", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--fixture", type=Path, help="Optional noisy/clean fixture directory containing gt.jsonl")
    args = parser.parse_args()
    repo = next(parent for parent in Path(__file__).resolve().parents if (parent / "sure" / "skills").is_dir())
    model = args.model_dir.resolve()
    model_python = args.model_python.resolve()
    output = args.output_dir.resolve()
    if output.exists():
        raise ValueError("Use a new output directory to preserve previous evidence")
    output.mkdir(parents=True)
    fixture = args.fixture.resolve() if args.fixture else repo / "fixtures/tasks/se/librispeech_noise_smoke"
    source = output / "datasets/se_smoke"
    shutil.copytree(fixture, source)
    rows = [json.loads(line) for line in (source / "gt.jsonl").read_text(encoding="utf-8").splitlines()]
    (source / "sample.jsonl").write_text("".join(json.dumps({
        "key": row["key"], "noisy_audio": row["noisy_audio"], "reference_audio": row["reference_audio"],
    }) + "\n" for row in rows), encoding="utf-8")
    write_json(source / "ds.jsonl", {"task": "SE", "audio": {"speech": {"language": "en"}}})
    policy = output / "site.json"
    write_json(policy, {
        "schema": "sure.site.policy.v1", "site_id": "se-development-smoke", "policy_version": 1,
        "storage": {
            "approved_models_roots": [str(output / "approved/models")],
            "approved_results_roots": [str(output / "approved/results")],
            "forbidden_output_roots": [str(output / "approved")],
            "runtime_root": str(output / "runtime"),
        },
        "datasets": {"allowed_source_roots": {"default": str(output / "datasets")},
                     "projection_root": str(output / "projections")},
        "execution": {"surfaces": ["local"], "local_runtimes": ["python"]},
    })
    os.environ["SURE_SITE_POLICY"] = str(policy)
    scripts = repo / "sure/skills"
    sys.path[:0] = [str(scripts / "sure_agent_eval/scripts"), str(scripts / "sure_infer/scripts")]
    from agent_runner import McpToolClient, run_agent
    from check_agent_execution import gate_errors
    from check_agent_eval_report import gate_errors as evaluation_gate_errors
    from run_agent_eval import run_agent_eval
    from evaluate_predictions import _describe_external_pipeline, _run_external_pipeline

    stage = {
        "id": "enhance", "model": "gtcrn", "mode": "mcp_tool", "task": "SE", "model_dir": str(model),
        "working_dir": str(model), "tool_names": ["enhance_speech"],
        "server_command": [str(model_python), str(scripts / "sure_infer/scripts/model_wrapper_mcp_server.py"),
                           "--model-dir", str(model)],
    }
    client = McpToolClient(stage, log_path=output / "mcp.log")
    try:
        advertised = client._request("tools/list", {})
        tool = next(tool for tool in advertised["tools"] if tool["name"] == "enhance_speech")
        assert "output_path" in tool["inputSchema"]["properties"], advertised
        write_json(output / "mcp-tools.json", advertised)
    finally:
        client.close()
    subprocess.run([str(model_python), str(model / "validate.py"), "--fixture", str(fixture),
                    "--output-dir", str(output / "direct")], cwd=model, check=True)
    # A backend test fixture, deliberately not passed off as resolve_agent output
    # from an approved deployment. Production callers must use resolve_agent.py.
    spec = {
        "agent": {"name": "gtcrn_se_development", "task": "se", "input": "speech", "output": "audio",
                  "spec_sha256": hashlib.sha256(json.dumps(stage, sort_keys=True).encode()).hexdigest()},
        "stages": [stage], "metrics": ["si_sdr"],
        "datasets": [{"dataset": "se_smoke__unversioned", "source_root": str(source),
                      "version_id": "unversioned", "task": "SE", "language": "en"}],
        "runtime": {"product_dir": str(output / "product"), "dataset_source_key": "default", "device": "cpu"},
    }
    run = output / "run"
    write_json(run / "artifacts/agent_spec_resolved.json", spec)
    result = run_agent(spec, run)
    assert result["job_status"] == "succeeded", result
    errors = gate_errors(run, run / "artifacts/execution_result.json")
    assert not errors, errors
    predictions = [json.loads(line) for line in (output / "product/predictions/se_smoke__unversioned.jsonl").read_text(encoding="utf-8").splitlines()]
    for row in predictions:
        assert (output / "direct" / f"{row['key']}.wav").read_bytes() == Path(row["prediction"]["audio_path"]).read_bytes()
    report = run_agent_eval(argparse.Namespace(run_dir=str(run), run_id="gtcrn_se_development", device="cpu"))
    assert report["status"] == "success", report
    assert not evaluation_gate_errors(run, run / "artifacts/eval_run_report.json")
    engine = repo / "sure/external/sure-evaluation"
    pipeline = _describe_external_pipeline(engine_root=engine, task="SE", language="en", metric="si_sdr", timeout=120)
    baseline_samples = output / "noisy-baseline.jsonl"
    baseline_samples.write_text("".join(json.dumps({
        "sample_id": row["key"], "enhanced_audio": str(source / row["noisy_audio"]),
        "noisy_audio": str(source / row["noisy_audio"]), "reference_audio": str(source / row["reference_audio"]),
    }) + "\n" for row in rows), encoding="utf-8")
    baseline = _run_external_pipeline(engine_root=engine, request={
        "task": "SE", "language": "en", "metric": "si_sdr", "pipeline": pipeline,
        "output_dir": str(output / "baseline-evaluation"), "samples_jsonl": str(baseline_samples), "device": "cpu",
    }, timeout=120)
    write_json(output / "smoke-report.json", {
        "status": "passed", "scope": "development backends; no approval or skill terminal state claimed",
        "mcp_tools_list": True, "direct_matches_mcp": True, "execution_gate": True, "evaluation_gate": True,
        "agent_evaluation": report, "noisy_baseline": baseline,
    })
    print(f"SE development integration passed: {output / 'smoke-report.json'}")


if __name__ == "__main__":
    main()
