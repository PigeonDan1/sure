# SURE Harness User Guide

This guide is for users who clone the harness and want to know what they can do, what input each command needs, what output to expect, and how to verify that a run succeeded.

Product demo: https://sure-eval.com/harness

Related docs:

| Need | Read |
| --- | --- |
| Metric engine setup and exact pipeline selection. | [Evaluation engine](./evaluation_engine.md) |
| Common setup, provider, dataset, and VC failures. | [Troubleshooting](./troubleshooting.md) |
| Skill package layout and maintainer checks. | [Development guide](./development.md) |
| Chinese guide. | [用户指南](./harness_user_guide_zh.md) |

## Mental Model

SURE Harness turns audio-model evaluation into a set of artifact-gated slash commands for a TUI agent.

```text
discover model -> prepare model -> run evaluation -> re-evaluate existing predictions
```

The harness does not replace the metric engine. Metric capabilities, route nodes, exact `pipeline_id` selection, and node-local environment checks come from the `sure-evaluation` submodule under `sure/external/sure-evaluation` or an advanced `SURE_EVALUATION_HOME` override.

## From Zero To First Run

1. Clone and install the harness.

Prerequisites: Node.js >= 22.19.0, git, and Python >= 3.10 (see the
README's Quick Start for the one-line summary).

```bash
git clone --recurse-submodules --depth 1 --single-branch --branch harness-tui-agent https://github.com/PigeonDan1/sure.git sure-harness
cd sure-harness
npm install --ignore-scripts
pip install -r requirements.txt
npm run sure:doctor
```

2. Prepare the metric engine and benchmark JSONL files.

```bash
git submodule update --init --recursive

mkdir -p data/datasets/sure_benchmark
ln -s /path/to/sure_benchmark/jsonl data/datasets/sure_benchmark/jsonl
npm run sure:doctor
```

3. Start the TUI.

```bash
./pi-test.sh --provider openai --model <model-name> --thinking high --approve
```

4. Initialize the project.

```text
/sure_init
```

Non-interactive runs must pass --model explicitly.

5. Discover or provide a model.

```text
/sure_feed https://huggingface.co/Qwen/Qwen3-ASR-0.6B-hf
```

Expected output:

```text
sure/handoffs/<model_name>/model_input.yaml
sure/handoffs/<model_name>/artifacts/feed_report.json
```

6. Onboard the model into a runnable local unit.

```text
/sure_onboard model=<model_name> device=auto package=none
```

Expected output:

```text
sure/models/<model_name>/model.py
sure/models/<model_name>/model.spec.yaml
sure/models/<model_name>/config.yaml
sure/models/<model_name>/server.py
sure/models/<model_name>/verdict.json
sure/models/<model_name>/artifacts/
```

7. Evaluate the onboarded model.

```text
/sure_eval model=<model_name> datasets=aishell1__v1.0.2__asr metrics=cer max_samples=5 execution=local device=auto
```

Expected output:

```text
<eval_run_dir>/predictions/<dataset>.txt
<eval_run_dir>/predictions/<dataset>.jsonl
<eval_run_dir>/validation_payload.json
<eval_run_dir>/evaluation_route_plan.json
<eval_run_dir>/evaluation_payload.json
<eval_run_dir>/report.jsonl
<eval_run_dir>/metrics/<dataset>/<metric_slug>/report.json
<eval_run_dir>/metrics/<dataset>/<metric_slug>/pipeline_description.json
<eval_run_dir>/sample_reports/<dataset>/<metric_slug>.jsonl
<eval_run_dir>/main_agent_run_report.json
```

8. Re-evaluate an existing result without inference.

```text
/sure_reval source=<results_or_run_dir> datasets=aishell1__v1.0.2__asr max_samples=5 pipeline_id=asr.zh.cer.wetext_norm_zh_itn_v1.wenet_cer_v1 pipeline_id=asr.zh.cer.aispeech_norm_zh_v1.wenet_cer_v1
```

Expected output:

```text
<tmp_reval_run>/prediction_source_resolved.json
<tmp_reval_run>/prediction_reuse_manifest.json
<tmp_reval_run>/validation_payload.json
<tmp_reval_run>/evaluation_payload.json
<tmp_reval_run>/evaluation_route_plan.json
<tmp_reval_run>/metrics/
<tmp_reval_run>/sample_reports/
<tmp_reval_run>/main_agent_run_report.json
<tmp_reval_run>/reval_run_report.json
```

## Command Reference

| Command | Purpose | Required Input | Common Optional Input | Expected Output | Success Criteria |
| --- | --- | --- | --- | --- | --- |
| `/sure_init` | Configure the harness project and agent runtime: pick a provider (built-ins, models.json gateways, or a new OpenAI-compatible gateway) and pick the default model from the provider's model list (live where supported, built-in catalog otherwise). Non-interactive: `--option <id> --model <model-id>`; new gateway one-shot adds `--name <name> --base-url <url> --api-key <key>`. | none | provider/auth/model configuration | project-level config and doctor evidence | hooks discover skills and required dependencies |
| `/sure_feed` | Discover models and synthesize onboarding input. | direct model URL or `source/query` | `max_models`, `max_retries`, `download`, `handoff_root` | `sure/handoffs/<model>/model_input.yaml`, feed artifacts | selected model has task evidence, repo, weights source, fixture, IO contract |
| `/sure_onboard` | Turn a model into a runnable local inference unit. | `model=<handoff>` or `model_input_path=...` | `device`, `package`, `preferred_backend`, `skip_download` | `sure/models/<model>/` with wrapper, spec, config, fixture, verdict | import/load/infer/contract validations pass and `verdict.json` is ready |
| `/sure_eval` | Run prediction plus route-backed evaluation. | `model`, `datasets` | `metrics`, `max_samples`, `execution`, `device`, VC resources | predictions, validation payload, route plan, metric reports, run report | formal execution path is recorded; predictions validate; metric reports exist |
| `/sure_reval` | Recompute metrics from completed predictions only. | `source` | `datasets`, `metrics`, repeated `pipeline_id`, `max_samples`, `output_dir`, `device` | fresh tmp run with copied predictions and new metric artifacts | `evaluation_only=true`, `old_evaluation_reused=false`, report pipeline IDs match requested route |

## Input Preparation

### Model Discovery Input

Use `/sure_feed` when the user has a model link, a provider query, or a curated source list.

Supported source forms:

```text
/sure_feed https://huggingface.co/<owner>/<model>
/sure_feed https://www.modelscope.cn/models/<owner>/<model>
/sure_feed source=huggingface query="english asr" max_models=20
/sure_feed source=modelscope query="speech recognition" max_models=20
```

The output `model_input.yaml` is the only canonical input for `/sure_onboard`.

### MODEL_INPUT For Onboarding

`/sure_onboard` can start from a feed handoff:

```text
/sure_onboard model=<model_name>
```

or an explicit YAML file:

```text
/sure_onboard model_input_path=sure/handoffs/<model_name>/model_input.yaml
```

The onboarding input must resolve these fields:

| Field Group | Expected Content |
| --- | --- |
| identity | model id, normalized model name, source URL or local path |
| task | one SURE task family such as ASR, TTS, VC, KWS, S2TT, SD, SA-ASR, SLU, SER, GR |
| weights | HuggingFace, ModelScope, local, API, pip, or release/PyPI source |
| environment | Python version, backend preference, dependency evidence |
| entrypoints | import/load/infer/contract surfaces or a runtime strategy |
| fixture | task-registry smoke sample, not benchmark evidence |
| IO contract | input roles and prediction output shape |

### Evaluation Input

`/sure_eval` evaluates an onboarded model:

```text
/sure_eval model=<model_name> datasets=<dataset> metrics=<metric> max_samples=5 execution=local
```

Key fields:

| Field | Meaning |
| --- | --- |
| `model` | Name under `sure/models/<model>/` with a ready `verdict.json`. |
| `datasets` | Dataset names or aliases. Dataset JSONL metadata determines task and language. Short aliases such as `aishell1` expand only when they match one versioned dataset ID. |
| `metrics` | Canonical reported metrics such as `cer`, `wer`, `spk_sim`, `dnsmos`. |
| `max_samples` | Bounded sample count. `0` or omitted means full dataset. |
| `execution` | `local`, `vc`, or `auto`. `vc` must submit a real VC job; no silent local fallback. |
| `device` | `auto`, `cpu`, `cuda`, or `cuda:<index>`. |
| `vc_partition` | VC partition for `execution=vc`. Omitted means auto-selection. Unknown or unauthorized names fail at input resolution with the allowed list. |
| `vc_gpu` / `vc_mem` / `vc_cpu` / `vc_image` / `vc_job_name` | Optional VC resource overrides: GPUs, memory (GB), CPUs, Docker image, job name. |

### Re-evaluation Input

`/sure_reval` accepts an existing source:

| Source Kind | What It Means |
| --- | --- |
| `results_dir` | A mirrored results folder with `predictions/`, `report.jsonl`, and usually `protocol.yaml`. |
| `run_dir` | A canonical evaluation run directory with `predictions/`. |
| `predictions_dir` | A bare directory of `<dataset>.txt` and optionally `<dataset>.jsonl`; pass `model` and `datasets` when metadata cannot be inferred. |

`/sure_reval` resolves dataset names the same way `/sure_eval` does for the
prediction source: exact file stems win first, then a short name such as
`aishell1` may resolve to one unique versioned prediction stem such as
`aishell1__v1.0.2__asr`. Ambiguous short names fail closed instead of guessing.

Metric selection modes:

| Mode | Use When | Example |
| --- | --- | --- |
| `metrics` | You want the current default route for a reported metric. | `metrics=cer` |
| `pipeline_id` | You want an exact route variant, normalizer, transcriber, or scorer. | `pipeline_id=asr.zh.cer.aispeech_norm_zh_v1.wenet_cer_v1` |

Repeat `pipeline_id` to compare multiple chains for the same metric. The harness writes separate metric directories using `metric__pipeline_id`, so results do not overwrite each other.

## Output Contracts

### Feed Output

| Artifact | Meaning |
| --- | --- |
| `model_input.yaml` | Canonical onboarding input. |
| `feed_report.json` | User-facing discovery summary. |
| `scan_result.json` | Raw provider candidates. |
| `match_task_result.json` | Task matching evidence. |
| `metadata_result.json` | Repo, weights, dependency, and entrypoint evidence. |
| `rank_select_result.json` | Selected model and ranking reason. |
| `handoff_manifest.json` | Audit manifest for cross-skill handoff. |

### Onboard Output

| Artifact | Meaning |
| --- | --- |
| `model.py` / wrapper files | Runnable local adapter. |
| `model.spec.yaml` | Model identity, task, IO contract, and runtime metadata. |
| `fixture_manifest.json` | Smoke fixture used for validation. |
| `import_result.json`, `load_result.json`, `infer_result.json`, `contract_result.json` | Runtime validation stages. |
| `verdict.json` | Final readiness decision consumed by `/sure_eval`. |
| `artifact_manifest.json` | Index of onboard outputs. |
| `runtime_inventory.json` | Model-level runtime provenance: backend, Python, weights summary, runtime probe, evidence links. |
| `runtime_links/` | Symlinks to small evidence files only. Checkpoint payloads are not linked. |

### Eval Output

| Artifact | Meaning |
| --- | --- |
| `predictions/<dataset>.txt` | Key-tab-prediction file used for scoring. |
| `predictions/<dataset>.jsonl` | Structured prediction rows. `raw_response` is model output evidence, not a hyperparameter source. |
| `prediction_generation_status.json` | Actual inference server command, environment key snapshot, explicit tool args, protocol resolver output, and dataset generation status. |
| `protocol.yaml` | Inference-only protocol: model, runtime, parameters, prediction contract, reuse flag, and provenance. |
| `validation_payload.json` | Missing/extra/duplicate/empty prediction checks. |
| `evaluation_route_plan.json` | Engine commit, selected routes, pipeline IDs, nodes, and environment readiness. |
| `evaluation_payload.json` | Machine-readable dataset-metric results. |
| `metrics/<dataset>/<metric_slug>/report.json` | Submodule engine metric report. |
| `metrics/<dataset>/<metric_slug>/pipeline_description.json` | Selected pipeline identity and node chain. |
| `sample_reports/<dataset>/<metric_slug>.jsonl` | Per-sample score details when available. |
| `report.jsonl` | Standard per dataset-metric report rows. |
| `report_snapshot.md` | Human-readable evaluation protocol and result snapshot. |
| `main_agent_run_report.json` | Final run provenance, execution path, sample scope, and artifacts. |

### Reval Output

| Artifact | Meaning |
| --- | --- |
| `prediction_source_resolved.json` | Source kind, model/dataset inference, and prediction file paths. |
| `prediction_reuse_manifest.json` | Copied/filtered prediction files, sample counts, source/destination hashes. |
| `source_inference_provenance.json` | Source `protocol.yaml`, `prediction_generation_status.json`, and runtime inventory links when available. |
| `protocol.yaml` | Same inference-protocol shape as `/sure_eval`, with `prediction_reuse.enabled=true`. |
| `validation_payload.json` | Validation of the copied predictions for the requested sample scope. |
| `evaluation_route_plan.json` | Engine commit and selected re-evaluation routes. |
| `evaluation_payload.json` | New metric results. Old metric payloads are not reused. |
| `metrics/<dataset>/<metric__pipeline_id>/report.json` | Fresh metric report for an exact pipeline route. |
| `report.jsonl` / `report_snapshot.md` | Fresh machine-readable and human-readable reports. |
| `reval_run_report.json` | User-facing re-evaluation summary and comparison table. |

Important `reval_run_report.json` fields:

| Field | Expected Meaning |
| --- | --- |
| `evaluation_only` | Must be `true`. |
| `old_evaluation_reused` | Must be `false`. |
| `pipeline_ids` | Exact pipeline IDs requested by the user. Empty when using `metrics`. |
| `summary.comparisons[].pipelines[]` | Per-pipeline `pipeline_id`, ordered `nodes`, and `score`. |
| `summary.comparisons[].score_spread` | Difference between max and min score for that dataset/metric group. |
| `artifacts` | Paths to the generated run artifacts. |

### Inference Protocol And Provenance

`protocol.yaml` records inference parameters and runtime evidence only. Metric
selection, route nodes, scores, and sample-level reports live in
`evaluation_route_plan.json`, `report.jsonl`, and `report_snapshot.md`.

Parameter source priority is:

1. `prediction_generation_status.json`
2. `sure/models/<model>/artifacts/runtime_inventory.json`
3. model `config.yaml` protocol settings
4. process environment fallback

For `/sure_reval`, `prediction_reuse.generation_policy` must be
`reused_predictions_no_inference`, and source inference evidence is linked when
the source run exposes it.

## Verification Checklist

Use this checklist when reviewing a completed run.

### Onboarding

- `sure/models/<model>/verdict.json` exists.
- The verdict does not claim GPU readiness after a CPU-only fallback unless the device policy records that fallback.
- `model.py` or equivalent wrapper exists and is referenced by `model.spec.yaml`.
- `import/load/infer/contract` validation artifacts exist and pass.

### Evaluation

- `main_agent_run_report.json` exists and records `execution_path_requested` and `execution_path_actual`.
- For `execution=vc`, VC submission evidence exists; no silent local fallback.
- `validation_payload.json` has `is_valid=true`.
- `evaluation_route_plan.json` records the `sure-evaluation` engine commit.
- Every result has `report.json`, `pipeline_description.json`, and sample report artifacts.

### Re-evaluation

- `main_agent_run_report.json` notes that no model server was started and no inference was run.
- `reval_run_report.json` has `evaluation_only=true`.
- `reval_run_report.json` has `old_evaluation_reused=false`.
- `prediction_reuse_manifest.imported[].imported_samples` matches `max_samples` for bounded runs.
- `evaluation_payload.results[].pipeline_id == pipeline_description.pipeline_id == report.pipeline_id`.
- `evaluation_payload.results[].nodes` matches `report.pipeline_trace`.
- Multiple exact pipelines for the same metric write to separate artifact directories.

## Route And Pipeline IDs

Use the submodule engine to inspect available routes:

```bash
cd sure/external/sure-evaluation
sure-eval metric describe asr --language zh --metric cer --json
sure-eval metric describe asr --pipeline-id asr.zh.cer.aispeech_norm_zh_v1.wenet_cer_v1 --json
sure-eval agent plan asr --language zh --metric cer --json
```

The engine also ships a machine-readable catalog:

```text
sure/external/sure-evaluation/docs/pipeline_catalog.jsonl
sure/external/sure-evaluation/docs/pipeline_catalog.md
```

When one reported metric has multiple route variants, use exact `pipeline_id` rather than inventing a new metric name.

## Common Failure Modes

| Symptom | Likely Cause | Fix |
| --- | --- | --- |
| `/sure_feed` finds no models | Network/provider outage or query too narrow | Try a direct URL or rerun with a broader query. |
| `/sure_onboard` blocks at model input | Missing repo, weights, fixture, or IO contract | Repair the handoff or pass an explicit `model_input_path`. |
| `/sure_eval` cannot resolve dataset | Benchmark JSONL root missing | Link `data/datasets/sure_benchmark/jsonl` or set `SURE_EVAL_DATASETS_ROOT`. |
| `/sure_eval execution=vc` fails | VC CLI unavailable or submission failed | Fix VC access/resources; the harness must not fall back silently. |
| Job should land on a specific VC partition | Without `vc_partition` the harness auto-selects a partition | Add `vc_partition=<partition>` to `/sure_eval`; unknown names fail fast and the error lists the allowed partitions. |
| `/sure_reval` cannot infer metadata from a bare predictions directory | No report/protocol near the source | Pass `model=<name>` and `datasets=<dataset>`. |
| exact `pipeline_id` fails | Route node environment missing | Run the setup command from `evaluation_route_plan.json` or install the required node extra. |
