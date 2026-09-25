"""Real-model development smoke; does not approve a model or finish slash commands.

Run with the locked Harness Python. Model inference uses --model-python;
scoring uses the separately locked Evaluation Runtime.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[5]
EXAMPLE = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from sure.skills.sure_agent_eval.scripts import agent_runner
from generate_predictions_via_server import _build_tool_arguments, _normalize_prediction_payload


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-python", required=True, type=Path)
    parser.add_argument("--model-cache", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--metric", action="append", default=[])
    parser.add_argument("--example-dir", type=Path, default=EXAMPLE,
                        help="SE example with model.py, server.py and config.yaml")
    parser.add_argument("--fixture-dir", type=Path,
                        default=REPO / "fixtures/tasks/se/librispeech_noise_smoke")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    model_python = args.model_python.absolute()  # Keep the venv symlink.
    cache = args.model_cache.resolve()
    example = args.example_dir.resolve()
    example_config = yaml.safe_load((example / "config.yaml").read_text())
    model_id = example_config["model"]["id"]
    model_name = model_id.replace("/", "__")
    if str(example_config["model"]["task"]).upper() != "SE":
        raise ValueError("The development smoke requires an SE example")
    harness_root = Path(sys.prefix)
    manifest_path = harness_root / "runtime-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    environment = {
        **os.environ,
        "HARNESS_PYTHON_BIN": sys.executable,
        "SURE_HARNESS_RUNTIME_ROOT": str(harness_root),
        "SURE_HARNESS_RUNTIME_ID": manifest["runtime_id"],
        "SURE_HARNESS_LOCK_SHA256": manifest["lock_sha256"],
        "SURE_HARNESS_MANIFEST_PATH": str(manifest_path),
        "SURE_SE_MODEL_CACHE": str(cache),
        "SURE_SE_DEVICE": "cpu",
        "OMP_NUM_THREADS": "2",
        "MKL_NUM_THREADS": "2",
    }
    commands = []

    def run(label, command, *, expected=0, extra_env=None):
        completed = subprocess.run([str(part) for part in command], cwd=REPO, env={**environment, **(extra_env or {})},
                                   text=True, capture_output=True, timeout=600)
        (output / f"{label}.log").write_text(completed.stdout + completed.stderr)
        commands.append({"label": label, "command": [str(part) for part in command],
                         "exit_code": completed.returncode, "expected_exit_code": expected,
                         "environment_overrides": extra_env or {}})
        write_json(output / "commands.json", commands)
        if completed.returncode != expected:
            raise RuntimeError(f"{label} failed; see {output / (label + '.log')}")

    fixture = args.fixture_dir.resolve()
    rows = [json.loads(line) for line in (fixture / "gt.jsonl").read_text().splitlines() if line.strip()]
    if not 1 <= len(rows) <= 5:
        raise ValueError("Expected 1–5 fixture samples")
    contract = {"input_type": "audio_path", "output_type": "audio", "primary_field": "audio_path",
                "required_fields": ["audio_path"], "nonempty_fields": ["audio_path"], "json_serializable": True}
    for skill in ("sure_onboard", "sure_trans"):
        model_dir = output / skill
        model_dir.mkdir()
        for filename in ("model.py", "server.py", "config.yaml"):
            shutil.copy2(example / filename, model_dir / filename)
        shutil.copytree(fixture, model_dir / "fixture/se")
        template = (REPO / f"sure/skills/{skill}/scripts/templates/validate.py").read_text()
        for key, value in {"__MODEL_ID__": model_id,
                           "__MODEL_NAME__": model_name,
                           "__TASK_TYPE__": "SE", "__WRAPPER_CLASS__": "ModelWrapper",
                           "__PREDICT_METHOD__": "predict", "__IO_CONTRACT_JSON__": json.dumps(contract)}.items():
            template = template.replace(key, value)
        (model_dir / "validate.py").write_text(template)
        run(skill, [model_python, model_dir / "validate.py", "--stage", "all"])
        if skill == "sure_trans":
            # Trans intentionally validates one input per invocation.
            for index, row in enumerate(rows[1:], 2):
                run(f"sure_trans_{index}", [model_python, model_dir / "validate.py", "--stage", "all"],
                    extra_env={"SURE_VALIDATE_INPUT_JSON": json.dumps({"audio_path": str(fixture / row["audio"])}),
                               "SURE_VALIDATE_ARTIFACTS_DIR": str(model_dir / f"artifacts/sample_{index}")})

    source = output / "source/se_smoke"
    source.mkdir(parents=True)
    samples = [{"sample_id": row["key"], "attribute": {"path": str(fixture / row["audio"])},
                "reference_audio": str(fixture / row["reference_audio"])} for row in rows]
    (source / "sample.jsonl").write_text("".join(json.dumps(row) + "\n" for row in samples))
    (source / "ds.jsonl").write_text(json.dumps({"supported_tasks": ["SE"], "audio": {"speech": {"language": "en"}}}) + "\n")
    policy = yaml.safe_load((REPO / "config/site.default.yaml").read_text())
    policy["datasets"]["allowed_source_roots"]["default"] = str(output / "source")
    policy["datasets"]["projection_root"] = str(output / "projections")
    site = output / "site.yaml"
    site.write_text(yaml.safe_dump(policy))
    environment["SURE_SITE_POLICY"] = str(site)
    environment["SURE_DATASET_SOURCE_ROOT"] = str(output / "source")
    os.environ.update(environment)
    dataset = "se_smoke__unversioned__se"
    product = output / "agent-product"
    # In-memory development plan. No forged approval/runtime inventory or resolved-spec artifact.
    spec = {
        "agent": {"name": "se_development_smoke", "task": "se", "input": "speech",
                  "output": "speech", "spec_sha256": "0" * 64},
        "stages": [{"id": "enhance", "model": model_name, "mode": "mcp_tool",
                    "task": "SE", "tool_names": ["enhance_speech"], "model_dir": str(example),
                    "working_dir": str(example), "server_command": [str(model_python), str(example / "server.py")],
                    "env": {key: environment[key] for key in ("SURE_SE_MODEL_CACHE", "SURE_SE_DEVICE", "OMP_NUM_THREADS", "MKL_NUM_THREADS")}}],
        "datasets": [{"dataset": dataset, "task": "SE", "source_root": str(source), "version_id": "unversioned"}],
        "runtime": {"product_dir": str(product), "dataset_source_key": "default"},
    }
    client = agent_runner.McpToolClient(spec["stages"][0], log_path=output / "infer-mcp.log")
    infer_results = []
    try:
        for row in rows:
            arguments = _build_tool_arguments(repo_root=REPO, sample=row, task="SE", language="en",
                argument_name="audio_path", audio_path=fixture / row["audio"], output_audio_dir=output / "infer-audio")
            if any(key in arguments for key in ("reference_audio", "reference_audio_path")):
                raise ValueError("Clean reference leaked into SE inference")
            response = client.call(arguments)
            path, prediction = _normalize_prediction_payload(response, task="SE")
            agent_runner.extract_audio_path(prediction, Path(arguments["output_path"]))
            with wave.open(path) as audio:
                if audio.getnframes() == 0:
                    raise ValueError("Empty inference output")
            infer_results.append({"key": row["key"], "arguments": arguments, "prediction": prediction})
    finally:
        client.close()
    write_json(output / "infer-contract.json", infer_results)
    execution = agent_runner.run_agent(spec, output / "agent-run")
    if execution["job_status"] != "succeeded":
        raise RuntimeError(execution["error"])
    prediction_rows = [json.loads(line) for line in (product / f"predictions/{dataset}.jsonl").read_text().splitlines()]
    for row in prediction_rows:
        with wave.open(row["prediction"]["audio_path"]) as audio:
            if audio.getnframes() == 0 or audio.getframerate() != 16000 or audio.getnchannels() != 1:
                raise ValueError("Expected nonempty mono 16 kHz PCM WAV")
    config = output / "evaluation.yaml"
    config.write_text(yaml.safe_dump({"data": {"datasets": str(product / "references")}}))
    metrics = args.metric or ["si_sdr"]
    metric_args = [part for metric in metrics for part in ("--metric", metric)]
    eval_script = REPO / "sure/skills/sure_infer/scripts/evaluate_predictions.py"
    run("evaluation_runtime", [sys.executable, eval_script.parent / "evaluation_runtime.py", "--prepare",
                               "--engine-root", REPO / "sure/external/sure-evaluation",
                               "--output", output / "evaluation_runtime.json"])
    for name, pred_dir in (("enhanced", product / "predictions"), ("noisy_baseline", output / "baseline")):
        if name == "noisy_baseline":
            pred_dir.mkdir()
            (pred_dir / f"{dataset}.txt").write_text("".join(f"{row['sample_id']}\t{row['attribute']['path']}\n" for row in samples))
            (pred_dir / f"{dataset}.jsonl").write_text("".join(json.dumps({"key": row["sample_id"], "task": "SE",
                "prediction": {"audio_path": row["attribute"]["path"]}}) + "\n" for row in samples))
        staged_predictions = output / name / "predictions"
        shutil.copytree(pred_dir, staged_predictions)
        run(name, [sys.executable, eval_script, "--dataset", dataset, "--pred-dir", staged_predictions, "--config", config,
                   "--evaluation-backend", "external", "--device", "cpu", *metric_args,
                   "--run-dir", output / name, "--output", output / name / "payload.json"])
    # Exercise the real approval audit on an incomplete development model. It must refuse publication.
    approve = REPO / "sure/skills/sure_approve/scripts"
    audit_run = output / "approve"
    run("approve_resolve", [sys.executable, approve / "resolve_approve_input.py", "--run-dir", audit_run,
                           "--model-dir", output / "sure_onboard", "--produces", audit_run / "artifacts/approve_input_resolved.json"])
    run("approve_reject_incomplete", [sys.executable, approve / "audit_bundle.py", "--kind", "producer", "--run-dir", audit_run,
                                     "--produces", audit_run / "artifacts/producer_contract_report.json"], expected=1)
    scores = {name: [{"metric": result["metric"], "score": result["result"]["score"]}
                    for result in json.loads((output / name / "payload.json").read_text())["results"]]
              for name in ("enhanced", "noisy_baseline")}
    write_json(output / "summary.json", {"scope": "development backend smoke; not a completed or approved slash-command run",
               "model": model_id, "samples": len(samples), "scores": scores,
               "validated": ["onboard wrapper", "trans wrapper", "infer tool arguments and output projection", "agent MCP inference", "SE projection", "external evaluation", "approval rejects incomplete bundle"],
               "not_validated": ["sealed model runtime", "positive approval and publication", "approved model resolution", "slash-command terminal gates"]})
    print(output / "summary.json")


if __name__ == "__main__":
    main()
