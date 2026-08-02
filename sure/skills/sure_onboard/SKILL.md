# /sure_onboard

Onboard or repair an audio model into a reproducible local inference unit. This skill is the Sure port of the SURE-EVAL model-tool agent. The state machine lives in `hooks/state-machine.ts`; this document is what the agent reads to drive each unit.

**Prerequisite**: run `/sure_init` first to select an agent, configure auth, and validate the environment for this project.

Control principle: **agent decides scope, scripts enforce format and execution.** You load the MODEL_INPUT, select context, discover the repo, classify the task, choose a backend, create an executable build plan, build the env, fetch weights, validate, and emit a wrapper + verdict; the deterministic scripts under `scripts/` and the hook gates enforce that every artifact lands in `sure/models/<model_name>/` in the right format, the right value domain, and that the verdict is internally consistent.

## Parameters

| Parameter | Required | Meaning |
|-----------|----------|---------|
| `model` | ✅ preferred after `/sure_feed` | Handoff folder name under `sure/handoffs/<model>/`, for example `OpenMOSS-Team__MOSS-Transcribe-Diarize`. This resolves to `sure/handoffs/<model>/model_input.yaml`. |
| `model_input_path` | ✅ preferred for explicit paths | Path to the `MODEL_INPUT` YAML emitted by `/sure_feed`. This auto-fills `model_id`, `repo`, `task_type`, `deployment_type`, `preferred_backend`, `python_version`, and `weights_source`. |
| `model_id` | Required without `model_input_path` | Provider model id such as `Qwen/Qwen3-ASR-1.7B`. |
| `model_name` | Auto-filled from MODEL_INPUT or `model_id` | Single-segment directory name such as `Qwen__Qwen3-ASR-1.7B`; becomes `sure/models/<model_name>/`. |
| `repo` | Required without `model_input_path` | Repo URL or local path (DISCOVER input). |
| `task_type` | Required without `model_input_path` | `asr \| s2tt \| sd \| ser \| tts \| vc \| kws \| slu \| gr \| speech_understanding \| sa-asr \| sa_asr`. |
| `deployment_type` | Required without `model_input_path` | `local \| api`. |
| `preferred_backend` | — | `uv \| pip \| conda \| pixi \| docker \| api` (overrides auto-selection). |
| `python_version` | — | Pin a Python version. |
| `weights_source` | — | Weights URL / local path. |
| `package` | — | `none` (default), `docker-local`, or `docker-registry`. `none` means local-ready only. |
| `package_profile` | — | Alias for `package`; use only one. |
| `weights_link_policy` | — | `auto` (default), `copy`, `symlink`, `reuse-existing`, or `no-reuse`. |
| `skip_download` | — | bool — use existing local weights only. |
| `device` | — | `auto` (default), `cuda`, `cpu`, or `mps`. |
| `cpu_fallback_after_cuda_failures` | — | Default 3. When `device=auto` and host CUDA is visible, CPU fallback is accepted only after this many recorded CUDA failures. |
| `cuda_repair_attempts_before_cpu` | — | Default 3. When CUDA is visible and the first CUDA path fails, the agent must try at least this many CUDA environment repairs before CPU fallback can pass. |
| `force_repair` | — | bool — force a repair of an already-onboarded model. |
| `existing_model_dir` | — | For repair: point at the existing model artifacts dir. |
| `max_retries` | — | Default 3. |

The run directory (`<run_dir>`) holds structured run outputs under `<run_dir>/artifacts/<unit.produces>`. **Model entity products** (wrapper/spec/fixture/verdict) land in the repo-level global dir `sure/models/<model_name>/`, where `model_name` is the single-segment normalized name such as `Qwen__Qwen3-ASR-1.7B`. Do not use raw `owner/model` as a directory path.

Preferred handoff:

```bash
/sure_onboard model=OpenMOSS-Team__MOSS-Transcribe-Diarize
```

Explicit path handoff:

```bash
/sure_onboard model_input_path=sure/handoffs/OpenMOSS-Team__MOSS-Transcribe-Diarize/model_input.yaml
```

For a quick local check, a positional YAML path is also accepted:

```bash
/sure_onboard sure/handoffs/OpenMOSS-Team__MOSS-Transcribe-Diarize/model_input.yaml
```

The `sure/handoffs/<model>/` folder is not deleted after onboard. It remains a stable cache of the feed research result and lets users re-run or compare onboarding attempts without repeating discovery.

Recommended first command after start, when a `MODEL_INPUT` path is available:

```bash
python3 scripts/materialize_onboard_inputs.py \
  --model-input-path sure/handoffs/<model>/model_input.yaml \
  --run-dir <run_dir> \
  --repo-root <repo_root> \
  --package-profile none
```

This helper emits only `model_input_resolved.json` and `context_selection.json`. It deliberately does **not** emit `backend_choice.json` or `build_plan.json`; those must remain agent-research-first outputs based on repository evidence and documented import/load/inference paths.

`MODEL_INPUT` should be strict YAML. The helper includes a scalar-only fallback for legacy handoffs with unquoted multiline code snippets, but fallback output is partial and must be treated as a repair signal for `/sure_feed`, not as the preferred format.

### Command Boundaries

There are two command families and they must not be mixed:

1. **Harness scripts** (`scripts/check_*.py`, `scripts/run_validate.py`, `scripts/materialize_onboard_inputs.py`, `scripts/prepare_fixture.py`, `scripts/stage_model_artifacts.py`) run from the skill package with harness Python:

```bash
cd sure/skills/sure_onboard
python3 scripts/<script>.py --run-dir <run_dir> --produces <run_dir>/artifacts/<artifact>.json
```

Do not invoke harness scripts with `sure/models/<model>/.venv/bin/python` or with a bare `.venv/bin/python` from the repo root. The hook automatically runs the current gate script after the current unit's artifact exists; manual gate invocations are only diagnostics.

2. **Model runtime commands** run from the model directory or use an absolute model-local interpreter:

```bash
cd sure/models/<model_name>
.venv/bin/python <model runtime command>
```

Relative `.venv/bin/python` from the repo root is invalid because it either points at the wrong environment or fails before testing the model.

### Local-first discovery

`discover` is agent-research-first, but it must be bounded and model-local-first:

1. Read `<run_dir>/artifacts/model_input_resolved.json`.
2. Inspect `model_input_resolved.model_dir` if it already exists. This is the highest-priority evidence because `/sure_onboard model=<name>` may be repairing or validating an already adapted local model from the original SURE workspace.
3. Inspect `model_input_resolved.source.handoff_artifacts_dir` when present; `/sure_feed` research artifacts are the second-priority evidence.
4. Inspect only the declared `repo_url` or a bounded clone/cache under `model_dir/.runtime/` when local evidence is insufficient.
5. Do not run unbounded filesystem searches such as `find /`, `find /mnt`, or `find /hpc_stor03`. If a local path is needed, derive it from `model_dir`, `handoff_artifacts_dir`, `weights.local_path`, or the declared model-local checkpoint paths.
6. Do not download full checkpoints in `discover`: no `snapshot_download`, no `huggingface-cli download`, no safetensors/bin `hf_hub_download`, no long `sleep` polling. Use short metadata probes only (for example README/config/listing with timeout and `HF_ENDPOINT=https://hf-mirror.com` fallback when direct HuggingFace is unreachable). Full checkpoint transfer and retry policy belong to `fetch_weights`.
7. Do not validate model runtime dependencies with the current shell or base Python in `discover`: no `python -c "import torch/transformers/torchaudio/..."`, no load/infer probes, and no `from_pretrained`. Record dependency evidence from README/config/requirements in `repo_summary.json`; backend selection and environment creation happen later.

`repo_summary.json` should record the chosen strategy in `discovery_strategy`, plus `model_dir`, `handoff_artifacts_dir`, `local_path`, and `evidence_sources` when available. Remote GitHub/HuggingFace/ModelScope evidence is supporting evidence; it must not replace an existing, validated model-local implementation.

### Environment Boundary

There are two Python scopes:

- **Harness control Python**: allowed before `build_env` only for deterministic harness scripts such as `scripts/materialize_onboard_inputs.py`, `scripts/check_model_input.py`, and other gate scripts. These scripts parse/check artifacts and do not prove the model runtime works.
- **Model runtime Python**: required for model imports, load tests, inference tests, and dependency/version checks. It must come from the backend selected in `plan` and created or registered in `build_env`, preferably under `sure/models/<model_name>/.venv/` for `uv`, or the model-local runtime metadata for `conda`/`pixi`/`docker`/`api`.

The boundary is strict: `discover` researches evidence; `prepare_fixture` stages the task payload; `build_env` creates the isolated runtime; `generate_wrapper` creates the model-local executable adapter; `validate_import/load/infer/contract` executes model code through that wrapper and runtime.

### Device Policy

Default device policy is **CUDA-first** for local deployment:

1. `device=auto` means: if host CUDA is visible (`nvidia-smi -L` succeeds or equivalent runtime evidence exists), `validate_env_compat` must first select `device="cuda"` and prove weights load on CUDA.
2. `device=cuda` is a hard CUDA request. Do not mark `validate_env_compat` successful on CPU.
3. `device=cpu` is an explicit user override and may validate on CPU directly, but the verdict must not claim GPU readiness.
4. CPU fallback for `device=auto` is allowed only after recorded CUDA-first attempts fail and at least three CUDA environment repairs have been attempted. `env_compat_result.json` must include `device="cpu"`, `cuda_available=true`, and either top-level or `device_policy` fields: `cuda_attempts >= cpu_fallback_after_cuda_failures`, `cuda_failures` with at least that many entries, `cuda_repair_attempts >= cuda_repair_attempts_before_cpu`, and a non-empty `fallback_reason`.
5. After `validate_env_compat` selects a real device (`cuda`, `cpu`, or `mps`), `validate_import/load/infer/contract` must run on that same device. The validation artifact must not override `DEVICE`/`SURE_DEVICE` to a different device.
6. Valid CUDA repair attempts include actions such as reinstalling/pinning torch/torchaudio for the host CUDA driver (for example a cu128 wheel on a CUDA 12.8 host), switching to a documented CUDA-compatible backend, or rebuilding the model-local environment. A CPU-only torch install does not count as a CUDA repair attempt.

Recommended command at `save_artifacts`, after wrapper generation and validation artifacts exist:

```bash
python3 scripts/stage_model_artifacts.py \
  --run-dir <run_dir> \
  --produces <run_dir>/artifacts/artifact_manifest.json
```

This helper copies already-created run artifacts into `sure/models/<model_name>/artifacts/` and writes the preferred `artifact_manifest.json` both in the run directory and in the model directory. It does not create `model.py`, `model.spec.yaml`, validation results, weights, or verdicts; missing previous state-machine outputs remain a blocking error.

Migration command at `save_artifacts`, when a proven reference model directory from the original SURE workspace is being adopted into the harness:

```bash
python3 scripts/adopt_reference_model.py \
  --reference-model-dir /path/to/original/src/sure_eval/models/<model_name> \
  --target-model-dir sure/models/<model_name> \
  --model-id <owner/model> \
  --model-name <model_name> \
  --replace-symlink
```

This helper creates a thin harness-local model directory: large/runtime files and most wrapper files are symlinked to the reference model, `validate.py` is copied locally so re-runs write into harness-local `artifacts/`, and `artifacts/` is normalized to the harness contract (`artifact_manifest.json`, derived validation stage results, `package_gate.json`, and a local-ready `verdict.json`). Use it only when the reference directory already has passing validation/verdict evidence; do not use it to bypass a failed onboard.

Isolation rule: `sure/models/<model_name>/` itself must be a real harness-owned directory. Do not leave it as a symlink to the original SURE-EVAL workspace. Only large immutable assets under subdirectories such as `checkpoints/`, `.runtime/modelscope_cache/`, `.runtime/huggingface/`, or `.runtime/vocoder/` may be symlinked, and those links must be recorded in `weights_manifest.json`.

## State Machine

Advance happens **only** when the current unit's `produces` is compliant. Linear units are agent self-driven; gate units additionally run a Python semantic check. Produce the current unit's artifact, then call `sure_update_state`.

Default target is **local-ready**. Docker is optional and controlled by `package`:

- `package=none`: stop after local deployment artifacts and validation are ready.
- `package=docker-local`: require Docker build + Docker validation evidence before final success.
- `package=docker-registry`: require Docker build + validation + push + pull verification before final success.

VC/HPC submission is not part of this core skill. If needed later, implement it as a separate deployment skill/command.

| # | Unit | Kind | Produces | Gate script |
|---|------|------|----------|-------------|
| 1 | `load_model_input` | **gate** | `model_input_resolved.json` | `scripts/check_model_input.py` |
| 2 | `context_selection` | linear | `context_selection.json` | — |
| 3 | `discover` | linear | `repo_summary.json` | — |
| 4 | `classify` | linear | `classification.json` | — |
| 5 | `plan` | linear | `backend_choice.json` | — |
| 6 | `build_plan` | **gate** | `build_plan.json` | `scripts/check_build_plan.py` |
| 7 | `validate_spec` | **gate** | `spec_validation.json` | `scripts/check_spec.py` |
| 8 | `prepare_fixture` | **gate** | `fixture_manifest.json` | `scripts/check_fixture.py` |
| 9 | `build_env` | **gate** | `build_env_result.json` | `scripts/check_env.py` |
| 10 | `fetch_weights` | **gate** | `weights_manifest.json` | `scripts/check_weights.py` |
| 11 | `validate_env_compat` | **gate** | `env_compat_result.json` | `scripts/check_env_compat.py` |
| 12 | `generate_wrapper` | linear | `wrapper_manifest.json` | — |
| 13 | `validate_import` | **gate** | `import_result.json` | `scripts/run_validate.py --kind import` |
| 14 | `validate_load` | **gate** | `load_result.json` | `scripts/run_validate.py --kind load` |
| 15 | `validate_infer` | **gate** | `infer_result.json` | `scripts/run_validate.py --kind infer` |
| 16 | `validate_contract` | **gate** | `contract_result.json` | `scripts/run_validate.py --kind contract` |
| 17 | `save_artifacts` | **gate** | `artifact_manifest.json` | `scripts/check_artifact_manifest.py` |
| 18 | `package_gate` | **gate** | `package_gate.json` | `scripts/check_package_gate.py` |
| 19 | `verdict` | **gate** | `verdict.json` | `scripts/check_verdict.py` |

> `validate_env_compat` (unit 11) was missing from the skeleton and is added here: the env built in `build_env` must actually load the resolved weights on the available device, match the declared python version, and support the adapter protocol. `generate_wrapper` then materializes `validate.py` before the import/load/infer/contract tests execute.

### Per-unit contract (Inputs → Output → Allowed → Must Not Do → Failure)

- **load_model_input**: Output = `model_input_resolved.json` {model_id, model_name, model_dir, task_type, deployment_type, package_profile, path_policy, ...}. `model_name` must be a single directory segment; `model_dir` must point to `sure/models/<model_name>/`; `path_policy` records the harness-owned model-dir rule and asset-level symlink policy.
- **context_selection**: Output = `context_selection.json` {task_type, selected_references, skipped_references, rationale}. Read only the task/env/contracts actually needed; record what was read.
- **discover**: Inputs = resolved `repo_url`, `model_dir`, and optional handoff artifacts. Output = `repo_summary.json` with only these top-level fields: `repo_url` (string, required), `timestamp` (string), `model_id` (string), `model_name` (string), `task_type` (string), `deployment_type` (string), `commit` (string|null), `repo_commit` (string|null), `discovery_strategy` (string), `model_dir` (string|null), `model_dir_exists` (boolean), `handoff_artifacts_dir` (string|null), `local_path` (string|null), `evidence_sources` (array of string or object), `file_inventory` (array of strings, or object), `model_card_info` (object), `entrypoints` (object), `dependency_hints` (object), `fixture_hints` (object), `language` (string), `notes` (string); the schema has `additionalProperties:false`. Prefer existing `model_dir` and handoff artifacts before network clone/search. Must Not Do: unbounded filesystem search, full checkpoint download, safetensors/bin transfer, long sleep polling, current-shell/base-Python runtime dependency probes, model load/infer probes, `verdict_status`, `wrapper_path` (later units).
- **classify**: Output exactly `classification.json` with only these top-level fields: `task_type`, `deployment_type`, `sub_task`, `input_modality`, `output_modality`, `rationale`. Do not include metadata fields such as `timestamp`, `model_id`, `model_name`, or `task_type_reason`; the schema has `additionalProperties:false`. Allowed `task_type` ∈ {asr,s2tt,sd,ser,tts,vc,kws,slu,gr,speech_understanding,sa-asr,sa_asr}.
- **plan**: Output = `backend_choice.json` {backend, choice_reason, ...}. Allowed: backend ∈ {uv,pip,conda,pixi,docker,api}. See `references/policies/backend_selection.md` + `references/playbooks/env_ROUTING.md`.
- **build_plan**: Output = `build_plan.json` {model_id, model_dir, backend, package_profile, steps, ...}. It must be executable and must not include required VC/HPC submission steps.
- **validate_spec**: Output = `spec_validation.json` {checks, status}. All seven checks (spec_completeness/evidence_sufficiency/conflict_resolution/build_plan_executable/fixture_availability/io_contract_sufficient/preflight_compatible) must pass; status=passed. This unit proves that a task fixture source has been identified; it does not stage the model-local fixture. See `references/contracts/spec_validation.md`.
- **prepare_fixture**: Output = `fixture_manifest.json` {model_id, model_name, model_dir, task_type, source_dir, staged_dir, gt_jsonl, samples, sample_count, link_policy}. Use `scripts/prepare_fixture.py --run-dir <run_dir> --produces <run_dir>/artifacts/fixture_manifest.json` unless a custom source needs `--source-dir`. The helper selects the fixture source from `spec_validation.checks.fixture_availability.fixture_path` or `fixtures/tasks/<task>/...`, then copies it under `sure/models/<model_name>/fixture/<task>/<fixture_name>/` (`link_policy=copy`). `gt.jsonl` rows must reference relative audio paths inside the fixture directory and carry task annotations; `sa_asr` requires speaker-attributed `segments`; sample_count must be 1-5. This gate exists because `validate.py` discovers payloads from `model_dir/fixture/**/gt.jsonl` or `SURE_VALIDATE_INPUT_JSON`.
- **build_env**: Output = `build_env_result.json` {env_ready, backend, python_executable, lockfile_path|docker_image, log_path, runtime_checks, runtime_probe, repairs, ...}. env_ready=true. Declared `lockfile_path` and `log_path` must resolve under `model_dir` or run artifacts; Docker backend must declare `docker_image`. For `uv`, create/use the model-local `.venv` under `sure/models/<model_name>/` and ensure `.venv/bin/python` exists. For `conda`/`pixi`, record the selected env and its model-local evidence instead of opportunistically treating base Python as the runtime. If `model.py` already exists, the gate imports it with the selected runtime. If `runtime_checks.required_imports` is set, every declared import must pass. If the resolved request is `device=cuda`, the build env gate must also prove CUDA is visible in that runtime; do not write `env_ready=true` while the selected Python cannot import required packages or has an incompatible torch/transformers stack. If CUDA/dependency repair was needed, preserve it in `repairs` instead of deleting that evidence.
- **fetch_weights**: Output = `weights_manifest.json` {weights_ready|status=fetched, source, resolved_local_model_path, ...}. If `weights.required=true`, non-API/non-PyPI sources must resolve to an existing local checkpoint path. Prefer model-local `.runtime/` or `checkpoints/`; if the declared load path is outside `model_dir`, record `fallback_to_host_global=true` and a non-empty `fallback_reason`. For HuggingFace in restricted networks, first try direct metadata with timeout, then retry with `HF_ENDPOINT=https://hf-mirror.com`; if large files redirect to Xet/CAS (`cas-bridge.xethub.hf.co`) and that host times out, record the CAS/Xet failure in `source_attempts` and fail this unit with a user-actionable repair instead of looping. Rich upstream-style fields such as `required`, `repo_id`, `dependencies`, `checkpoint_root`, and `source_attempts` are accepted but must point to existing paths. See `references/contracts/model_local_checkpoint_rule.md`.
- **validate_env_compat**: Output = `env_compat_result.json` {compat_ok, device, requested_device, python_executable, python_version_match, adapter_protocol_supported, weights_loadable, runtime, weights, adapter, ...}. compat_ok=true must not contradict explicit false checks for python version, adapter protocol, or weights loadability. For local `device=auto`, visible host CUDA forces CUDA-first; CPU fallback must record `cuda_available`, `cuda_attempts`, `cuda_failures`, `cuda_repair_attempts`, and `fallback_reason`.
- **generate_wrapper**: Output = `wrapper_manifest.json` {wrapper_path, model_py, server_py, ...}. The wrapper set lands in `sure/models/<model_name>/` (model.py, server.py, __init__.py, validate.py). Generated `validate.py` must preserve the template CLI: `--stage import|load|infer|contract|all`, write `artifacts/<stage>_result.json`, write `artifacts/sample_output.json` during infer, and validate contract from `io_contract`. Templates live in `scripts/templates/`.
- **metric enrichment reference**: Metric reports are optional enrichment for `/sure_onboard package=none`, not the local-ready gate. When implementing or repairing metric reports, read `references/templates/validate_metric_enrichment.md` and the task playbook, reuse existing `sample_output.json` / generated audio whenever possible, and do not rerun model inference only to repair metric semantics.
- **validate_import/load/infer/contract**: Output = `{*_passed, error, run_command|validate_py, log_path, ...}`. The gate executes `run_command` or `validate_py`; a boolean alone is not accepted. `validate_infer` is additionally Hook-guarded: `fixture_manifest.json` must exist, point to `model_dir/fixture/<task>/.../gt.jsonl`, and declare 1-5 samples before inference can run. `validate_infer` must also leave a non-empty `sample_output.json` under the run or model artifacts directory. `validate_contract` re-reads that sample output and checks it against `MODEL_INPUT.io_contract` (`required_fields`, `nonempty_fields`, `primary_field`, and audio-output evidence).
- **save_artifacts**: Output = `artifact_manifest.json` {model_dir, artifacts.{required,conditional,optional}}. Gate checks model-local files exist: model.spec.yaml, model.py, server.py, __init__.py, validate.py, config.yaml. Prefer `scripts/stage_model_artifacts.py` here so run artifacts are durably copied to `sure/models/<model_name>/artifacts/` before the manifest gate runs.
- **package_gate**: Output = `package_gate.json` {status, package_profile, readiness, model_dir, artifact_manifest_path}. `package=none` requires `readiness.local_ready=true` **and** real evidence from the previous units: a gate-valid `artifact_manifest.json`, `env_compat_result.json` with `compat_ok=true`, passing `import_result.json`/`load_result.json`/`infer_result.json`/`contract_result.json`, and a JSON `sample_output.json` under the run or model artifacts directory. Docker readiness is required only for Docker package profiles; Docker profiles must also include docker evidence. VC is ignored.
- **verdict**: Output = `verdict.json` {status, instance_id, package, readiness, build, validation, artifacts}. status ∈ {passed/success/PASS/PASSED, failed, partial}. A harness-format success requires build.success, all four validation tests passed, package readiness matching `package_profile`, agreement with the preceding `package_gate.json`, and existing declared artifact paths (`spec_path`, `wrapper_path`, manifest/log/sample paths when present). Older upstream `PASS`/`PASSED` verdicts remain accepted through compatibility parsing.

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

## Product Layout (sure/models/<model_name>/)

```
sure/models/<model_name>/
├── model.spec.yaml
├── model.py / server.py / __init__.py / validate.py   # wrapper
├── config.yaml                                          # server launch config
├── artifacts/
│   ├── build_plan.json
│   ├── validation.log
│   ├── sample_output.json
│   ├── package_gate.json
│   ├── verdict.json
│   ├── artifact_manifest.json
│   ├── runtime_inventory.json                     # model-level runtime provenance
│   ├── runtime_links_manifest.json
│   └── runtime_links/                             # symlinks to small evidence files only
├── fixture/<task>/                                      # test audio + gt.jsonl (2–3 samples, max 5)
├── .runtime/ checkpoints/                               # weights convergence
└── eval_runs/<run_id>/                                  # this model's eval runs (original layout)
```

## Backend

The deterministic backend is bundled in `scripts/` (self-contained — no external repo reference). `scripts/sure_eval/` holds the model-tool framework (models/registry.py+base.py, inference/, protocols/). Gate scripts (`check_model_input.py`, `check_build_plan.py`, `check_spec.py`, `check_fixture.py`, `check_env.py`, `check_weights.py`, `check_env_compat.py`, `run_validate.py`, `check_artifact_manifest.py`, `check_package_gate.py`, `check_verdict.py`) validate each gate's artifact. Helpers are intentionally narrow: `materialize_onboard_inputs.py` creates only the deterministic early artifacts from a completed `/sure_feed` MODEL_INPUT; `prepare_fixture.py` stages an already-selected task fixture into the model directory; `stage_model_artifacts.py` stages already-produced run artifacts into the model-local artifact directory and automatically writes `runtime_inventory.json`; `write_runtime_inventory.py` can backfill runtime provenance for an existing model directory. Templates (model.spec.yaml, validate.py, verdict.json, artifact_manifest.json, spec_validation.json) live in `scripts/templates/`. Run as:

```bash
python3 scripts/<script>.py <args>   # cwd = skill package dir
```

## Failure Handling

On gate failure, record the failure in `validation.log` / `build.log` and enter DIAGNOSE → REPLAN (classify via `references/playbooks/failure_taxonomy.md`, retry per `references/policies/retry_and_escalation.md`). Max 3 retries (the hook bumps the per-unit counter); beyond that the unit is marked FAILED — do not blind-retry.

If the same hook/gate blocks three consecutive attempts, stop and ask the user to confirm the `model_input_path` or repo link, access permissions, and whether the referenced documentation contains enough install/load/inference/artifact information. Do not keep modifying artifacts just to bypass the hook.

## Success Criteria

The `pre_finish` hook enforces: `verdict.json` exists, the terminal gate passes, and the state machine reached the terminal unit. On success call `sure_finish` with `status: "success"` and `manifest_path: ".sure/runs/<run_id>/manifest.json"`. If incomplete or failed, finish with `status: "incomplete"` or `status: "failed"` and a repair summary.
