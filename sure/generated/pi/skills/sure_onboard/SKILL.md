---
name: sure-onboard
description: Adapt and validate a model, then seal either the default digest-pinned container or an explicitly approved local Python runtime for SURE Eval.
---

# /sure_onboard

Onboard or repair a model into a reproducible inference unit. The state machine lives in `hooks/state-machine.ts`; this document is what the agent reads to drive each unit.

**Prerequisite**: run `/sure_init` first to select an agent, configure auth, and validate the environment for this project.

Control principle: **agent decides scope, scripts enforce format and execution.** Docker registry delivery remains the default. An explicit `package=none` may instead become Eval-ready only when the site permits Python, the backend is `uv`, and a hash-locked Model Runtime is materialized and sealed. A model-local `.venv` by itself is validation evidence, never an Eval runtime.

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
| `package` | — | Local models default to `docker-registry`. Explicit `none` selects a sealed local Python runtime when site policy permits `python` and backend=`uv`; API also uses `none`. `docker-local` is diagnostic-only. |
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
"$HARNESS_PYTHON_BIN" scripts/materialize_onboard_inputs.py \
  --model-input-path sure/handoffs/<model>/model_input.yaml \
  --run-dir <run_dir> \
  --repo-root <repo_root> \
  --package-profile docker-registry \
  [--image-version <version>]
```

This helper emits only `model_input_resolved.json` and `context_selection.json`. For `docker-registry`, it resolves `container_delivery.target_image` from the active site policy before any model download or build; omit `--image-version` to select the next unused patch tag. It deliberately does **not** emit `backend_choice.json` or `build_plan.json`; those must remain agent-research-first outputs based on repository evidence and documented import/load/inference paths.

`MODEL_INPUT` should be strict YAML. The helper includes a scalar-only fallback for legacy handoffs with unquoted multiline code snippets, but fallback output is partial and must be treated as a repair signal for `/sure_feed`, not as the preferred format.

### Command Boundaries

There are two command families and they must not be mixed:

1. **Harness scripts** (`scripts/check_*.py`, `scripts/run_validate.py`, `scripts/materialize_onboard_inputs.py`, `scripts/materialize_model_runtime.py`, `scripts/prepare_fixture.py`, `scripts/stage_model_artifacts.py`) run from the skill package with harness Python:

```bash
cd sure/skills/sure_onboard
"$HARNESS_PYTHON_BIN" scripts/<script>.py --run-dir <run_dir> --produces <run_dir>/artifacts/<artifact>.json
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
5. Do not run unbounded filesystem searches such as `find /`, `find /mnt`, or site-specific shared roots. If a local path is needed, derive it from `model_dir`, `handoff_artifacts_dir`, `weights.local_path`, or the declared model-local checkpoint paths.
6. Do not download full checkpoints in `discover`: no `snapshot_download`, no `huggingface-cli download`, no safetensors/bin `hf_hub_download`, no long `sleep` polling. Use short metadata probes only (for example README/config/listing with timeout and `HF_ENDPOINT=https://hf-mirror.com` fallback when direct HuggingFace is unreachable). Full checkpoint transfer and retry policy belong to `fetch_weights`.
7. Do not validate model runtime dependencies with the current shell or base Python in `discover`: no `python -c "import torch/transformers/torchaudio/..."`, no load/infer probes, and no `from_pretrained`. Record dependency evidence from README/config/requirements in `repo_summary.json`; backend selection and environment creation happen later.

`repo_summary.json` should record the chosen strategy in `discovery_strategy`, plus `model_dir`, `handoff_artifacts_dir`, `local_path`, and `evidence_sources` when available. Remote GitHub/HuggingFace/ModelScope evidence is supporting evidence; it must not replace an existing, validated model-local implementation.

### Environment Boundary

There are two Python scopes:

- **Harness control Python**: allowed before `build_env` only for deterministic harness scripts such as `scripts/materialize_onboard_inputs.py`, `scripts/check_model_input.py`, and other gate scripts. These scripts parse/check artifacts and do not prove the model runtime works.
- **Model runtime Python**: required for model imports, load tests, inference tests, and dependency/version checks. It must come from the backend selected in `plan` and created or registered in `build_env`, preferably under `sure/models/<model_name>/.venv/` for `uv`, or the model-local runtime metadata for `conda`/`pixi`/`docker`/`api`.

The boundary is strict: `discover` researches evidence; `prepare_fixture` stages the task payload; `build_env` creates the isolated runtime; `generate_wrapper` creates the model-local executable adapter; `validate_import/load/infer/contract` executes model code through that wrapper and runtime.

For explicit local `package=none`, write the normal build result first as `<run_dir>/artifacts/build_env_draft.json`, then seal it with:

```bash
"$HARNESS_PYTHON_BIN" scripts/materialize_model_runtime.py \
  --run-dir <run_dir> \
  --input <run_dir>/artifacts/build_env_draft.json \
  --produces <run_dir>/artifacts/build_env_result.json
```

The draft must use backend=`uv`, point to an executable validation Python, and name a `--generate-hashes` lock inside `model_dir`. The generated result replaces its Python path with the active site's content-addressed Model Python. Do not copy that absolute path into portable bundle files.

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
"$HARNESS_PYTHON_BIN" scripts/stage_model_artifacts.py \
  --run-dir <run_dir> \
  --produces <run_dir>/artifacts/artifact_manifest.json
```

This helper copies already-created run artifacts into `sure/models/<model_name>/artifacts/` and writes the preferred `artifact_manifest.json` both in the run directory and in the model directory. It does not create `model.py`, `model.spec.yaml`, validation results, weights, or verdicts; missing previous state-machine outputs remain a blocking error.

Historical model migration is an administrator-only metadata operation. It is not a `/sure_onboard` helper and cannot synthesize passing validation, packaging, or verdict evidence from an old local environment.

Isolation rule: `sure/models/<model_name>/` itself must be a real harness-owned directory. Do not leave it as a symlink to the original SURE-EVAL workspace. Only large immutable assets under subdirectories such as `checkpoints/`, `.runtime/modelscope_cache/`, `.runtime/huggingface/`, or `.runtime/vocoder/` may be symlinked, and those links must be recorded in `weights_manifest.json`.

## State Machine

Advance happens **only** when the current unit's `produces` is compliant. Linear units are agent self-driven; gate units additionally run a Python semantic check. Produce the current unit's artifact, then call `sure_update_state`.

Default target for local models is **registry-backed container-ready**. Local Python is an explicit site-controlled alternative:

- `package=docker-registry`: default; build, validate, push, resolve the immutable digest, and pull-verify that exact digest.
- `package=docker-local`: diagnostic only; it may prove a local image but cannot produce an Eval-ready bundle.
- `package=none`: API delivery, or local Python when `execution.local_runtimes` includes `python`, backend=`uv`, and the Model Runtime is sealed from a hash-locked requirements file. It never enables VC execution.

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
| 17 | `package_container` | **gate** | `docker_registry_result.json` | `scripts/check_container_package.py` |
| 18 | `save_artifacts` | **gate** | `artifact_manifest.json` | `scripts/check_artifact_manifest.py` |
| 19 | `package_gate` | **gate** | `package_gate.json` | `scripts/check_package_gate.py` |
| 20 | `write_runtime_inventory` | **gate** | `runtime_inventory.json` | `scripts/check_runtime_inventory.py` |
| 21 | `verdict` | **gate** | `verdict.json` | `scripts/check_verdict.py` |
| 22 | `extract_lessons` | **gate** | `extraction_declaration.json` | `scripts/check_memory_extraction.py` |
| 23 | `finalize_model_bundle` | **gate** | `deployment_ready.json` | `scripts/check_finalized_bundle.py` |

> `validate_env_compat` (unit 11) was missing from the skeleton and is added here: the env built in `build_env` must actually load the resolved weights on the available device, match the declared python version, and support the adapter protocol. `generate_wrapper` then materializes `validate.py` before the import/load/infer/contract tests execute.

### Per-unit contract (Inputs → Output → Allowed → Must Not Do → Failure)

- **load_model_input**: Output = `model_input_resolved.json` {model_id, model_name, model_dir, task_type, deployment_type, package_profile, path_policy, ...}. `model_name` must be a single directory segment; `model_dir` must point to `sure/models/<model_name>/`; `path_policy` records the harness-owned model-dir rule and asset-level symlink policy.
- **context_selection**: Output = `context_selection.json` {task_type, selected_references, skipped_references, rationale}. Read only the task/env/contracts actually needed; record what was read. Also read `artifacts/memory_context.json` when it exists: the `pre_start` hook writes it with the memory facts that match this cluster, model family and datasets, shape `{schema: "sure.memory.context.v1", skill, target_id, facts: [{entry_id, title, path, scope, checked_at, stale, status}], omitted_provisional}`; the file is written even when nothing matched (`facts: []`); it is advisory, verify before relying, and `stale: true` means the fact is older than its scope's re-check limit. List the memory files you actually read under `selected_references.memory` (the schema already allows that key). Routing for the rest of the memory tree: `references/memory/ROUTING.md`.
- **discover**: Inputs = resolved `repo_url`, `model_dir`, and optional handoff artifacts. Output = `repo_summary.json` with only these top-level fields: `repo_url` (string, required), `timestamp` (string), `model_id` (string), `model_name` (string), `task_type` (string), `deployment_type` (string), `commit` (string|null), `repo_commit` (string|null), `discovery_strategy` (string), `model_dir` (string|null), `model_dir_exists` (boolean), `handoff_artifacts_dir` (string|null), `local_path` (string|null), `evidence_sources` (array of string or object), `file_inventory` (array of strings, or object), `model_card_info` (object), `entrypoints` (object), `dependency_hints` (object), `fixture_hints` (object), `language` (string), `notes` (string); the schema has `additionalProperties:false`. Prefer existing `model_dir` and handoff artifacts before network clone/search. Must Not Do: unbounded filesystem search, full checkpoint download, safetensors/bin transfer, long sleep polling, current-shell/base-Python runtime dependency probes, model load/infer probes, `verdict_status`, `wrapper_path` (later units).
- **classify**: Output exactly `classification.json` with only these top-level fields: `task_type`, `deployment_type`, `sub_task`, `input_modality`, `output_modality`, `rationale`. Do not include metadata fields such as `timestamp`, `model_id`, `model_name`, or `task_type_reason`; the schema has `additionalProperties:false`. Allowed `task_type` ∈ {asr,s2tt,sd,ser,tts,vc,kws,slu,gr,speech_understanding,sa-asr,sa_asr}.
- **plan**: Output = `backend_choice.json` {backend, choice_reason, ...}. Allowed: backend ∈ {uv,pip,conda,pixi,docker,api}. See `references/policies/backend_selection.md` + `references/playbooks/env_ROUTING.md`.
- **build_plan**: Output = `build_plan.json` {model_id, model_dir, backend, package_profile, steps, ...}. It must be executable and must not include required VC/HPC submission steps. For `docker-registry`, copy `container_delivery.target_image` exactly from `model_input_resolved.json`; do not infer or rewrite its registry namespace. Local `package=none` must select backend=`uv` and include `materialize_model_runtime.py` after producing a hash-locked dependency file.
- **validate_spec**: Output = `spec_validation.json` {checks, status}. All seven checks (spec_completeness/evidence_sufficiency/conflict_resolution/build_plan_executable/fixture_availability/io_contract_sufficient/preflight_compatible) must pass; status=passed. This unit proves that a task fixture source has been identified; it does not stage the model-local fixture. See `references/contracts/spec_validation.md`.
- **prepare_fixture**: Output = `fixture_manifest.json` {model_id, model_name, model_dir, task_type, source_dir, staged_dir, gt_jsonl, samples, sample_count, link_policy}. Use `scripts/prepare_fixture.py --run-dir <run_dir> --produces <run_dir>/artifacts/fixture_manifest.json` unless a custom source needs `--source-dir`. The helper selects the fixture source from `spec_validation.checks.fixture_availability.fixture_path` or `fixtures/tasks/<task>/...`, then copies it under `sure/models/<model_name>/fixture/<task>/<fixture_name>/` (`link_policy=copy`). `gt.jsonl` rows must reference relative audio paths inside the fixture directory and carry task annotations; `sa_asr` requires speaker-attributed `segments`; sample_count must be 1-5. This gate exists because `validate.py` discovers payloads from `model_dir/fixture/**/gt.jsonl` or `SURE_VALIDATE_INPUT_JSON`.
- **build_env**: Output = `build_env_result.json` {env_ready, backend, python_executable, lockfile_path|docker_image, log_path, runtime_checks, runtime_probe, repairs, ...}. env_ready=true. Declared `lockfile_path` and `log_path` must resolve under `model_dir` or run artifacts; Docker backend must declare `docker_image`. For `uv`, create/use the model-local `.venv` under `sure/models/<model_name>/` and ensure `.venv/bin/python` exists. For local `package=none`, first write a draft with backend=`uv` and a hash-locked file inside `model_dir`, then run `materialize_model_runtime.py --input <draft> --produces <run_dir>/artifacts/build_env_result.json`. The helper resolves a content-addressed runtime under `<site runtime_root>/models/<runtime_id>` and emits portable `model_runtime_manifest.json`; do not hand-author either binding. For `conda`/`pixi`, record the selected env and its model-local evidence instead of opportunistically treating base Python as the runtime. If `model.py` already exists, the gate imports it with the selected runtime. If `runtime_checks.required_imports` is set, every declared import must pass. If the resolved request is `device=cuda`, the build env gate must also prove CUDA is visible in that runtime; do not write `env_ready=true` while the selected Python cannot import required packages or has an incompatible torch/transformers stack. If CUDA/dependency repair was needed, preserve it in `repairs` instead of deleting that evidence.
- **fetch_weights**: Output = `weights_manifest.json` {weights_ready|status=fetched, source, resolved_local_model_path, ...}. If `weights.required=true`, non-API/non-PyPI sources must resolve to an existing local checkpoint path. Prefer model-local `.runtime/` or `checkpoints/`; if the declared load path is outside `model_dir`, record `fallback_to_host_global=true` and a non-empty `fallback_reason`. For HuggingFace in restricted networks, first try direct metadata with timeout, then retry with `HF_ENDPOINT=https://hf-mirror.com`; if large files redirect to Xet/CAS (`cas-bridge.xethub.hf.co`) and that host times out, record the CAS/Xet failure in `source_attempts` and fail this unit with a user-actionable repair instead of looping. Rich upstream-style fields such as `required`, `repo_id`, `dependencies`, `checkpoint_root`, and `source_attempts` are accepted but must point to existing paths. See `references/contracts/model_local_checkpoint_rule.md`.
- **validate_env_compat**: Output = `env_compat_result.json` {compat_ok, device, requested_device, python_executable, python_version_match, adapter_protocol_supported, weights_loadable, runtime, weights, adapter, ...}. compat_ok=true must not contradict explicit false checks for python version, adapter protocol, or weights loadability. For local `device=auto`, visible host CUDA forces CUDA-first; CPU fallback must record `cuda_available`, `cuda_attempts`, `cuda_failures`, `cuda_repair_attempts`, and `fallback_reason`.
- **generate_wrapper**: Output = `wrapper_manifest.json` {wrapper_path, model_py, server_py, ...}. The wrapper set lands in `sure/models/<model_name>/` (model.py, server.py, __init__.py, validate.py). Generated `validate.py` must preserve the template CLI: `--stage import|load|infer|contract|all`, write `artifacts/<stage>_result.json`, write `artifacts/sample_output.json` during infer, and validate contract from `io_contract`. Templates live in `scripts/templates/`. `config.yaml` may enable `protocols.strict_core` only when every conservative parameter (`temperature`, `do_sample`, `num_beams`, `num_return_sequences`, `seed`) maps to a property declared by the selected MCP tool `input_schema`, or is explicitly marked `model_param: null`, `status: not_applicable` with an architecture-specific reason. Omit/disable `strict_core` when that proof is unavailable; `/sure_infer` will still use `standard_system` by default.
- **metric enrichment reference**: Metric reports are optional enrichment, not a deployment gate. Reuse existing `sample_output.json` / generated audio whenever possible; do not rerun inference only to repair metric semantics.
- **validate_import/load/infer/contract**: Output = `{*_passed, error, run_command|validate_py, log_path, ...}`. The gate executes `run_command` or `validate_py`; a boolean alone is not accepted. `validate_infer` is additionally Hook-guarded: `fixture_manifest.json` must exist, point to `model_dir/fixture/<task>/.../gt.jsonl`, and declare 1-5 samples before inference can run. `validate_infer` must also leave a non-empty `sample_output.json` under the run or model artifacts directory. `validate_contract` re-reads that sample output and checks it against `MODEL_INPUT.io_contract` (`required_fields`, `nonempty_fields`, `primary_field`, and audio-output evidence).
- **package_container**: For `docker-local` or `docker-registry`, first run `"$HARNESS_PYTHON_BIN" scripts/describe_harness_runtime.py`. Add its exact named build context and `COPY --from=sure_harness_runtime` line to the model Docker build. Output `docker_registry_result.json` plus `docker_build_result.json` and `docker_validation.json`. Container evidence must bind the image digest and distinct model/harness runtimes; registry delivery also requires passing push/pull verification. For `package=none`, emit `status=skipped` with a reason and do not create Docker evidence.
- **save_artifacts**: Output = `artifact_manifest.json` {model_dir, artifacts.{required,conditional,optional}}. Gate checks model-local files exist and stages the selected delivery evidence. Local `package=none` requires the exact generated `model_runtime_manifest.json`; it does not infer one from `.venv`.
- **package_gate**: Run `scripts/write_package_gate.py --run-dir <run_dir> --produces <run_dir>/artifacts/package_gate.json`. It derives schema v2 evidence from the staged manifest and validated inputs instead of trusting an older sidecar. `docker-registry` requires exact Docker tag/digest/ref agreement. Local `none` requires all local validations, `bundle_ready=true`, all Docker readiness flags false, a byte-identical staged Model Runtime manifest, and a live site-runtime verification. Its timestamp must be later than the artifact manifest; `docker-local` remains non-Eval-ready.
- **write_runtime_inventory**: Output schema v2 = `runtime_inventory.json`. Container delivery records the digest-pinned image and read-only model mount. Local Python records only the portable Model Runtime ID, manifest/lock hashes, server command, tools, and model-core hashes; `/sure_infer` resolves the current site's absolute runtime root by ID. In both cases Model Python and Harness Python remain separate roles, and the inventory timestamp is later than package evidence.
- **verdict**: Run `scripts/write_verdict.py --run-dir <run_dir> --produces <run_dir>/artifacts/verdict.json`. It derives the verdict from staged build, validation, package, and runtime evidence instead of trusting an older sidecar. A local success requires `bundle_ready=true` and either verified `docker-registry` delivery or sealed `package=none` Python delivery. Its timestamp must be later than package and runtime evidence; `docker-local` must remain partial or failed.
- **extract_lessons**: Inputs = `artifacts/run_digest.json`, written by the hook the moment `verdict` passed (read it; never rebuild it in place). Output = `extraction_declaration.json` {schema, no_new_lessons, no_lessons_reason, covered_by, candidates, infra_noise, infra_evidence} plus 0 to 5 candidate directories under `artifacts/candidates/<nn>-<slug>/` (`proposal.json` + `proposal.md`) and, for facts, evidence files under `artifacts/memory_evidence/`. The full contract (digest fields, candidate formats, the gate's ten checks, the write-tools-only rule) is `sure/runtime/memory/EXTRACTION.md`; read it before writing anything. Write candidates and evidence first and the declaration last. `no_new_lessons: true` with a one-line reason is the normal result of a clean run. Must Not Do: do not run `scripts/build_run_digest.py` onto `artifacts/run_digest.json` (a preview goes to `--out <run_dir>/artifacts/run_digest.preview.json` and the gate ignores it); do not write under `sure/memory/` or `references/memory/`; do not use bash heredocs for these files. Failure: `scripts/check_memory_extraction.py` says which check failed; after two consecutive failures the hook advances on its own with `extraction: failed`, and switching to `no_new_lessons: true` with the reason is always a valid way out.
- **finalize_model_bundle**: Rewrites the manifest into portable finalized form, deterministically regenerates package, runtime, and verdict, verifies complete required-file hashes, and atomically writes `deployment_ready.json` last. Docker and API deliveries retain `sure.onboard.deployment_ready.v1`; an approved `package=none` Python delivery uses `sure.onboard.deployment_ready.v2`. The marker must be later than manifest/package/runtime/verdict and is the only terminal readiness marker consumed by `/sure_infer`.

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
│   ├── docker_build_result.json / docker_validation.json
│   ├── docker_registry_result.json
│   ├── package_gate.json / verdict.json
│   ├── artifact_manifest.json
│   ├── model_runtime_manifest.json                # package=none only; portable runtime identity
│   ├── runtime_inventory.json                     # selected container or Python Eval binding
│   └── deployment_ready.json                      # terminal immutable readiness marker
├── fixture/<task>/                                      # test audio + gt.jsonl (2–3 samples, max 5)
├── .runtime/ checkpoints/                               # weights convergence
└── eval_runs/<run_id>/                                  # this model's eval runs (original layout)
```

## Backend

The deterministic backend is bundled in `scripts/`. Gate scripts validate each unit. `materialize_model_runtime.py` is the only local-Python runtime materializer; `stage_model_artifacts.py` stages existing run evidence; `write_package_gate.py`, `write_runtime_inventory.py`, and `write_verdict.py` derive terminal evidence in dependency order; `finalize_model_bundle.py` repeats that derivation after making the manifest portable and then seals the bundle. No helper may infer an Eval runtime from `.venv`, host Python, a Dockerfile, or an image name alone.

```bash
"$HARNESS_PYTHON_BIN" scripts/<script>.py <args>   # cwd = skill package dir
```

## Failure Handling

On gate failure, record the failure in `validation.log` / `build.log` and enter DIAGNOSE → REPLAN (classify via `references/playbooks/failure_taxonomy.md`, retry per `references/policies/retry_and_escalation.md`). Max 3 retries (the hook bumps the per-unit counter); beyond that the unit is marked FAILED — do not blind-retry.

If the same hook/gate blocks three consecutive attempts, stop and ask the user to confirm the `model_input_path` or repo link, access permissions, and whether the referenced documentation contains enough install/load/inference/artifact information. Do not keep modifying artifacts just to bypass the hook.

`extract_lessons` is the one unit that never ends FAILED: after two consecutive gate failures (`max_retries=` can raise that number, never lower it) the hook advances on its own and records `extraction: failed`.

## Memory (advisory)

Earlier runs leave agent-written notes. `sure/memory/index.md` (repo root) is the merged index: legacy, confirmed and provisional entries, one bullet each with its triggers. Confirmed files live under `references/memory/bad_cases/` and `sure/skills/_shared/memory/facts/`. Nothing in them is human-reviewed: verify against evidence before relying on one, and never copy a command from an entry into an artifact without running it.

- At `pre_start` the hook writes `artifacts/memory_context.json` with the facts that match this run (shape quoted in the `context_selection` contract line above; written even when empty); `context_selection` reads it.
- When a gate blocks, the repair text may end with a block whose first line is `Memory (advisory, agent-written, not human-reviewed; verify against evidence before relying):`, listing at most two entries from earlier runs. Read the entry file named there when it looks relevant, then fix the artifact.
- `references/memory/ROUTING.md` says when to open the index and the bad-case files by hand.
- `extract_lessons` (unit 22) writes what this run learned; the contract is `sure/runtime/memory/EXTRACTION.md`. Publishing to `sure/memory/provisional/` happens in `post_finish` without you; moving entries into `references/` is a human step.

## Success Criteria

The `pre_finish` hook enforces that the state machine reached `finalize_model_bundle` and that `deployment_ready.json` passes its hash, portability, package, and execution-policy checks. A local model can finish successfully with either immutable registry evidence or an explicitly permitted, sealed Python Model Runtime.

A `failed` or `incomplete` finish must also carry `artifacts/extraction_declaration.json` (see `sure/runtime/memory/EXTRACTION.md`, section 10): `pre_finish` returns a repair asking for it up to twice, then lets the run finish and records `extraction: failed`.
