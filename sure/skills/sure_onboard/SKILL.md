# /sure_onboard

Onboard or repair an audio model into a reproducible local inference unit. This skill is the Sure port of the SURE-EVAL model-tool agent. The state machine lives in `hooks/state-machine.ts`; this document is what the agent reads to drive each unit.

Control principle: **agent decides scope, scripts enforce format and execution.** You discover the repo, classify the task, choose a backend, build the env, fetch weights, validate, and emit a wrapper + verdict; the deterministic scripts under `scripts/` and the hook gates enforce that every artifact lands in `sure/models/<model_id>/` in the right format, the right value domain, and that the verdict is internally consistent.

## Parameters

| Parameter | Required | Meaning |
|-----------|----------|---------|
| `model_id` | ✅ | Model name — becomes the `sure/models/<model_id>/` directory and `verdict.instance_id`. |
| `repo` | ✅ | Repo URL or local path (DISCOVER input). |
| `task_type` | ✅ | `asr \| tts \| vc \| kws \| speech_understanding`. |
| `deployment_type` | ✅ | `local \| api`. |
| `preferred_backend` | — | `uv \| pip \| conda \| pixi \| docker \| api` (overrides auto-selection). |
| `python_version` | — | Pin a Python version. |
| `weights_source` | — | Weights URL / local path. |
| `force_repair` | — | bool — force a repair of an already-onboarded model. |
| `existing_model_dir` | — | For repair: point at the existing model artifacts dir. |
| `max_retries` | — | Default 3. |

The run directory (`<run_dir>`) holds structured run outputs under `<run_dir>/artifacts/<unit.produces>`. **Model entity products** (wrapper/spec/fixture/verdict) land in the repo-level global dir `sure/models/<model_id>/` — this accumulates across onboard/repair runs and is the cross-skill handoff surface for `/sure_eval`.

## State Machine

Advance happens **only** when the current unit's `produces` is compliant. Linear units are agent self-driven; gate units additionally run a Python semantic check. Produce the current unit's artifact, then call `sure_update_state`.

| # | Unit | Kind | Produces | Gate script |
|---|------|------|----------|-------------|
| 1 | `discover` | linear | `repo_summary.json` | — |
| 2 | `classify` | linear | `classification.json` | — |
| 3 | `plan` | linear | `backend_choice.json` | — |
| 4 | `validate_spec` | **gate** | `spec_validation.json` | `scripts/check_spec.py` |
| 5 | `build_env` | **gate** | `build_env_result.json` | `scripts/check_env.py` |
| 6 | `fetch_weights` | **gate** | `weights_manifest.json` | `scripts/check_weights.py` |
| 7 | `validate_env_compat` | **gate** | `env_compat_result.json` | `scripts/check_env_compat.py` |
| 8 | `validate_import` | **gate** | `import_result.json` | `scripts/run_validate.py --kind import` |
| 9 | `validate_load` | **gate** | `load_result.json` | `scripts/run_validate.py --kind load` |
| 10 | `validate_infer` | **gate** | `infer_result.json` | `scripts/run_validate.py --kind infer` |
| 11 | `validate_contract` | **gate** | `contract_result.json` | `scripts/run_validate.py --kind contract` |
| 12 | `generate_wrapper` | linear | `wrapper_manifest.json` | — |
| 13 | `save_artifacts` | linear | `artifact_manifest.json` | — |
| 14 | `verdict` | **gate** | `verdict.json` | `scripts/check_verdict.py` |

> `validate_env_compat` (unit 7) was missing from the skeleton and is added here: the env built in `build_env` must actually load the resolved weights on the available device, match the declared python version, and support the adapter protocol — before the import/load/infer/contract tests.

### Per-unit contract (Inputs → Output → Allowed → Must Not Do → Failure)

- **discover**: Inputs = `repo`. Output = `repo_summary.json` {repo_url, ...}. Must Not Do: `verdict_status`, `wrapper_path` (later units).
- **classify**: Output = `classification.json` {task_type, ...}. Allowed: task_type ∈ {asr,tts,vc,kws,speech_understanding}.
- **plan**: Output = `backend_choice.json` {backend, choice_reason, ...}. Allowed: backend ∈ {uv,pip,conda,pixi,docker,api}. See `references/policies/backend_selection.md` + `references/playbooks/env_ROUTING.md`.
- **validate_spec**: Output = `spec_validation.json` {checks, status}. All seven checks (spec_completeness/evidence_sufficiency/conflict_resolution/build_plan_executable/fixture_availability/io_contract_sufficient/preflight_compatible) must pass; status=passed. See `references/contracts/spec_validation.md`.
- **build_env**: Output = `build_env_result.json` {env_ready, backend, lockfile_path|docker_image, ...}. env_ready=true. Docker branch: when backend=docker, declare a docker_image instead of a lockfile.
- **fetch_weights**: Output = `weights_manifest.json` {weights_ready, source, resolved_local_model_path, ...}. Model-local first (`.runtime/` or `checkpoints/`); a host-global fallback needs `fallback_to_host_global=true` + a non-empty `fallback_reason`. See `references/contracts/model_local_checkpoint_rule.md`.
- **validate_env_compat**: Output = `env_compat_result.json` {compat_ok, device, python_version_match, adapter_protocol_supported, ...}. compat_ok=true.
- **validate_import/load/infer/contract**: Output = `{*_passed, error, ...}`. See `references/contracts/minimal_validation.md`.
- **generate_wrapper**: Output = `wrapper_manifest.json` {wrapper_path, model_py, server_py, ...}. The wrapper set lands in `sure/models/<model_id>/` (model.py, server.py, __init__.py, validate.py). Templates in `scripts/templates/`.
- **save_artifacts**: Output = `artifact_manifest.json` {model_dir, artifacts.{spec_path,wrapper_path,...}}.
- **verdict**: Output = `verdict.json` {status, instance_id, build, validation, artifacts}. status ∈ {passed/success, failed, partial}. A success requires build.success AND all four validation tests passed. See `references/contracts/` + `scripts/templates/verdict.json`.

## Backend Routing Rules (Phase 1)

Rule-based backend selection (record the reason in `backend_choice.json`):

1. API-only model → `api`.
2. Repo has Dockerfile + complex deps → `docker`.
3. Repo has `environment.yml` / conda signals → `pixi` (or `conda`).
4. Repo has only `pyproject.toml` / `requirements.txt`, pure Python → `uv`.
5. CUDA compilation / custom C++ / k2 / complex submodules → `docker` first.
6. High host-pollution risk → `docker` first.

## Model-Local Checkpoint Rule

When `weights.required == true`, converge weights to the model directory:
- `.runtime/modelscope_cache/` — ModelScope / HF provider cache.
- `checkpoints/` — explicit local weights (may be empty if weights are in `.runtime/`).
Record fallback to host-global paths only when forced (capacity / permissions), with reason + target in `build_plan.json` and `weights_manifest.json`.

## Product Layout (sure/models/<model_id>/)

```
sure/models/<model_id>/
├── model.spec.yaml
├── model.py / server.py / __init__.py / validate.py   # wrapper
├── config.yaml                                          # server launch config
├── verdict.json                                         # terminal state (records all artifact paths)
├── validation.log / build.log
├── fixture/<task>/                                      # test audio + gt.jsonl (2–3 samples, max 5)
├── .runtime/ checkpoints/                               # weights convergence
└── eval_runs/<run_id>/                                  # this model's eval runs (original layout)
```

## Backend

The deterministic backend is bundled in `scripts/` (self-contained — no external repo reference). `scripts/sure_eval/` holds the model-tool framework (models/registry.py+base.py, inference/, protocols/). Gate scripts (`check_spec.py`, `check_env.py`, `check_weights.py`, `check_env_compat.py`, `run_validate.py`, `check_verdict.py`) validate each gate's artifact. Templates (model.spec.yaml, validate.py, verdict.json, artifact_manifest.json, spec_validation.json) live in `scripts/templates/`. Run as:

```bash
python3 scripts/<script>.py <args>   # cwd = skill package dir
```

## Failure Handling

On gate failure, record the failure in `validation.log` / `build.log` and enter DIAGNOSE → REPLAN (classify via `references/playbooks/failure_taxonomy.md`, retry per `references/policies/retry_and_escalation.md`). Max 3 retries (the hook bumps the per-unit counter); beyond that the unit is marked FAILED — do not blind-retry.

## Success Criteria

The `pre_finish` hook enforces: `verdict.json` exists, the terminal gate passes, and the state machine reached the terminal unit. On success call `sure_finish` with `status: "success"` and `manifest_path: ".sure/runs/<run_id>/manifest.json"`. If incomplete or failed, finish with `status: "incomplete"` or `status: "failed"` and a repair summary.
