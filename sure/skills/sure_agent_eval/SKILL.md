---
name: sure-agent-eval
description: Evaluate an Agent (an ordered chain of approved models, e.g. ASR -> LLM translator) over speech datasets: run the chain to produce a /sure_infer-compatible prediction bundle, then score it with the pinned sure-evaluation engine.
---

# /sure_agent_eval

Evaluate an **Agent** — an input → output system built from an ordered chain of approved models — over one or more datasets, and score its answers. Typical case: a speech-translation (AST/S2TT) agent whose stage 1 is an approved ASR model (speech → text via its MCP tool) and whose later stages are LLMs onboarded in the API-model pattern (text → translated text). The product is an inference bundle (`predictions/`, `protocol.yaml`, `prediction_generation_status.json`, `references/sure_benchmark/jsonl/`) plus an evaluation batch under `evaluation_runs/<batch-id>/` inside that bundle.

**Prerequisite**: run `/sure_init` first to select an agent, configure auth, and validate the environment for this project. Every stage model must already be approved (`/sure_onboard` → `/sure_approve`) below `storage.approved_models_roots[0]`.

Control principle: **agent declares, scripts execute.** The agent spec (`agent.yaml`) declares the chain and the task; `scripts/resolve_agent.py` (run by the hook at `pre_start`) validates it and resolves every stage to its approved model; `scripts/agent_runner.py` executes the chain; `scripts/run_agent_eval.py` scores the bundle; the hook gates enforce that every artifact is in the right place, the right format, and the right value domain.

## Agent Spec (`agent.yaml`)

```yaml
agent:
  name: qwen3asr_llm_s2tt   # identifier; used in the product directory name
  task: s2tt                # input speech -> output text (translation). AST is S2TT.
  input: speech
  output: text
stages:                     # run in declaration order; previous output feeds the next stage
  - id: asr
    model: qwen3_asr        # exact approved model directory name; MCP-tool stage
  - id: translate
    model: qwen_llm_mt      # API-model stage (config.yaml carries api.base_url + api_key_env)
    prompt_template: "Translate to {target_language}: {text}"
```

Schema: `schemas/agent_spec.schema.json`; an example lives in `examples/agent_s2tt_example.yaml`. A stage resolves to **mcp_tool** mode when its approved `config.yaml` declares `server.command`, and to **api** mode when it declares `api.base_url`. The first stage must be an mcp_tool stage: the runner drives position 0 over the dataset's audio through that model's MCP server. `prompt_template` placeholders: `{text}`, `{target_language}`, `{source_language}`, `{dataset}`, `{key}`.

SE is supported as a **single approved SE MCP stage**, with `agent.task: se`,
audio/speech input and output, and SE datasets. See `examples/agent_se_example.yaml`.
The runner sends noisy `audio_path` and a unique `output_path`; clean reference
audio is reserved for scoring. The stage must write nonempty audio to that exact
output path and return `audio_path` or `enhanced_audio`. An SE stage followed by
a text API stage is rejected. Use `metrics=si_sdr` for paired noisy/clean data,
after verifying the route in the pinned evaluator.

Credential red line: an API stage's key is read from the environment variable **named** by `api_key_env` at execution time. Only the variable name is ever recorded — never the value, in no artifact and no log.

## Parameters

| Parameter | Required | Meaning |
|-----------|----------|---------|
| `agent` | ✅ | Path to the agent spec (`agent.yaml`). |
| `datasets` | ✅ | Comma-separated source paths below a configured `allowed_source_roots` entry, each `<path>[@<version>]`. A flat source is `<name>__unversioned`. Dataset metadata (ds.jsonl `task` / `audio.speech.translation_language`), not a user flag, determines ASR vs S2TT. |
| `metrics` | ✅ | Comma-separated metrics, e.g. `metrics=bleu,chrf`; each resolves to the engine's default pipeline for the dataset's task and language. |
| `dataset_source_key` | — | Key in site policy `datasets.allowed_source_roots` that authorizes the supplied source paths. |
| `max_samples` | — | Sample cap for bounded validation runs. Omitted or `0` means full dataset. Recorded as `runtime.max_samples`; `scripts/agent_runner.py` reads it from the plan. |
| `device` | — | Evaluation device (`cpu` default). Never changes prediction identity. Recorded as `runtime.device`; `scripts/run_agent_eval.py` reads it from the plan. |
| `output_dir` | — | Absolute directory that becomes this invocation's product directory, replacing `sure/results/agents/<agent_name>/<run_id>`. Must be outside every configured `forbidden_output_roots` entry. Quote the value when the path contains spaces: `output_dir="/data/My Runs/job"`. |

`model`, `model_dir`, `source`, `pipeline_id`, `config` and `evaluation_engine_root` are rejected: the models come from the agent spec's stages, and weakening the pinned evaluator or the chain contract is not allowed.

Example:

```text
/sure_agent_eval agent=sure/skills/sure_agent_eval/examples/agent_s2tt_example.yaml datasets=/srv/sure/datasets/mini_s2tt_zh2en metrics=bleu,chrf
```

## State Machine

Advance happens **only** when the current unit's `produces` artifact is compliant (location + format + value domain; no forbidden fields). Linear units are agent self-driven; gate units additionally run a Python semantic check. Produce the current unit's artifact, then call `sure_update_state`.

| # | Unit | Kind | Produces | Gate script |
|---|------|------|----------|-------------|
| 1 | `resolve_agent` | linear | `agent_spec_resolved.json` | — |
| 2 | `run_agent` | **gate** | `execution_result.json` | `scripts/check_agent_execution.py` |
| 3 | `evaluate` | **gate** | `eval_run_report.json` | `scripts/check_agent_eval_report.py` |
| 4 | `run_report` | **gate** | `main_agent_run_report.json` | `scripts/check_agent_run_report.py` |

### Per-unit contract (Inputs → Output → Allowed → Must Not Do → Failure)

- **resolve_agent**: `pre_start` already ran `scripts/resolve_agent.py` and wrote `artifacts/agent_spec_resolved.json`. Read it: confirm the stage chain (each stage's model, mode, approved model directory, verdict), the resolved dataset set (task/language per dataset), and the metrics before any model is started. If the plan is wrong, fix the agent spec or the invocation parameters and re-run `scripts/resolve_agent.py` (permitted only from this unit). Must Not Do: do not author `agent_spec_resolved.json` by hand; do not set `job_status`/`status`/`report_persisted` (later units).
- **run_agent**: run `"$HARNESS_PYTHON_BIN" scripts/agent_runner.py --run-dir <sure_run_dir>` (`--max-samples N` only to override the plan's `runtime.max_samples`). It reads `agent_spec_resolved.json`, projects every dataset (target = translation for S2TT sources, transcription for ASR sources), runs the chain per sample — stage 1 through the model's MCP server (`tools/call` with `audio_path`), later stages through the API-model HTTP pattern — and writes the bundle into `runtime.product_dir` plus `execution_result.json` into `artifacts/`. Do not author `execution_result.json` by hand. The gate `check_agent_execution.py` validates the record against the resolved spec and, for a succeeded run, cross-checks the product tree (`predictions/<dataset>.txt` counts, `prediction_generation_status.json`, `protocol.yaml`, `references/sure_benchmark/jsonl/<dataset>.jsonl`). A terminal failure (`job_status: failed`) is a valid outcome; read `failed_stage`, `failed_dataset` and the logs before deciding what to do.
- **evaluate**: run `"$HARNESS_PYTHON_BIN" scripts/run_agent_eval.py --run-dir <sure_run_dir>` (`--device` only to override the plan's `runtime.device`). It requires a succeeded `execution_result.json`, validates the bundle predictions, resolves each requested metric to the engine's default pipeline for the dataset's task and language, runs the pinned sure-evaluation engine with the external backend in the locked Evaluation Runtime, appends the batch into `<product_dir>/evaluation_runs/agent_eval_<24-hex>/` and writes `eval_run_report.json` into `artifacts/`. Do not author `eval_run_report.json` by hand. The gate `check_agent_eval_report.py` validates the report against the resolved spec and the batch. A failed evaluation leaves `status: failed` with an `error_code`; read it before deciding what to do.
- **run_report**: {report_persisted, execution_path_actual, run_dir, ...}. Point `run_dir` at the agent product directory, record the agent name, selected datasets, metrics and batch id. `check_agent_run_report.py` accepts a completed run only when `artifacts/eval_run_report.json` reports `status: success`; a failed run needs the failed `eval_run_report.json` (or `execution_result.json`) and a `next_action`.

## Bundle Layout (product directory)

The `run_agent` product is deliberately compatible with the `/sure_infer` bundle so the scorer can reuse the same projection and TSV contracts:

```text
<product_dir>/
  protocol.yaml                          # agent chain identity + provenance (inference-only)
  prediction_generation_status.json      # per-dataset generation status (v2 shape)
  predictions/<dataset>.txt              # key<TAB>text TSV (the agent's final answer)
  predictions/<dataset>.jsonl            # SE: structured audio prediction + sample rate
  predictions/audio/<dataset>/*.wav      # SE: enhanced audio; TSV stores paths instead of text
  predictions/manifest.json              # per-dataset sha256 + row counts
  references/sure_benchmark/jsonl/<dataset>.jsonl
  evaluation_runs/agent_eval_<24-hex>/   # appended by the evaluate unit
```

## Backend

```bash
"$HARNESS_PYTHON_BIN" scripts/resolve_agent.py --agent <agent.yaml> --datasets <...> --metrics <...> --run-id <id> [--max-samples N] [--device cpu] --output <run_dir>/artifacts/agent_spec_resolved.json
"$HARNESS_PYTHON_BIN" scripts/agent_runner.py --run-dir <sure_run_dir> [--max-samples N]
"$HARNESS_PYTHON_BIN" scripts/run_agent_eval.py --run-dir <sure_run_dir> [--device cpu]
```

Run host-side scripts with `cwd` = this skill package dir. The scripts import the shared evaluation code from `../sure_infer/scripts/` (dataset projection, deployment binding, evaluation runtime, `evaluate_predictions.py`); the hooks forbid invoking those scripts directly, and forbid the whole raw inference surface (`generate_predictions_via_server.py`, `model_wrapper_mcp_server.py`, `infer_entrypoint.py`, `run_infer.py`, `run_eval.py`, MCP `tools/call`) from agent tool calls.

## Forbidden Actions

- Never write to the approved model or result roots from this skill.
- Never run the stage models except through `scripts/agent_runner.py`.
- Never record a credential value (API key, token) in any artifact, log, or report; only environment variable NAMES.
- Never author or edit `agent_spec_resolved.json`, `execution_result.json`, or `eval_run_report.json` by hand.
- Never change the requested dataset set or metrics between units.

## Gate Checks (enforced by hooks)

- `run_agent`: `check_agent_execution.py` validates the terminal record against the resolved spec and cross-checks the product tree of a succeeded run.
- For SE, the execution gate also verifies structured JSONL hashes, TSV/JSONL
  agreement, and existence of every nonempty enhanced audio file.
- `evaluate`: `check_agent_eval_report.py` validates the report's agent identity against `agent_spec_resolved.json`, requires every selected dataset to carry at least one scored metric on success, and checks the batch directory.
- `run_report`: `report_persisted` true, `execution_path_actual` declared, `run_dir` equals the recorded product directory, and the eval report's status agrees with the reported run status.

On gate failure the hook blocks with a `repair` message and bumps the retry counter (max 2); beyond that the unit is marked FAILED — repair deliberately or finish with `status: failed`. Do not blind-retry.

## Success Criteria

The `pre_finish` hook enforces: `main_agent_run_report.json` exists, the terminal gate passes, and the state machine reached the terminal unit. On success call `sure_finish` with `status: "success"` and `manifest_path: ".sure/runs/<run_id>/manifest.json"`. If incomplete or blocked, finish with `status: "incomplete"` or `status: "failed"` and a repair summary.
