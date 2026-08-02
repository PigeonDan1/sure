# /sure_feed

Feed ModelScope, HuggingFace, or GitHub speech models into the SURE pipeline: discover candidates, match to a SURE task family, collect metadata, optionally convert fetched resources to the SURE resource layout (oref), synthesize canonical `MODEL_INPUT`, rank/select, and emit a handoff manifest that `/sure_onboard` consumes. This skill is the Sure port of the XForge→SURE bridge. The state machine lives in `hooks/state-machine.ts`; this document is what the agent reads to drive each unit.

**Prerequisite**: run `/sure_init` first to select an agent, configure auth, and validate the environment for this project.

Control principle: **agent decides scope, scripts enforce format and execution.** You (the agent) choose the source, query, filters, and watch mode; the deterministic scripts under `scripts/` and the hook gates enforce that every artifact is in the right place, the right format, and the right value domain — and that the strong-plus-weak matching contract holds.

This skill is **self-contained**: all backend code is bundled under `scripts/` (the `sure_feed/` inner package + flat `xforge_*.py` scripts + gate validators). There are no references to the external `/hpc/.../sure` repo or to `XForge/skills`.

## Parameters

| Parameter | Required | Meaning |
|-----------|----------|---------|
| `source` | — | `modelscope \| huggingface \| github \| multi`. Default `multi` for online discovery. |
| `hf_endpoint` | — | `auto \| huggingface \| hf-mirror \| <url>`. Default `auto`; fallback to `hf-mirror` only for network/5xx failures. |
| `url` / positional URL | — | Direct HuggingFace, ModelScope, or GitHub model URL. Preferred over search. |
| `watch_mode` | — | `once \| watch`. Default `once`. |
| `query` / `filter` | — | Task / license / time-window filters for discovery. |
| `max_models` | — | Cap on candidates scanned this run. |
| `download` | — | When true, materialize/fetch weights where supported. Default false for feed discovery. |
| `handoff` | — | When true (default), publish `sure/handoffs/<model_name>/model_input.yaml` plus an `artifacts/` evidence folder for `/sure_onboard`. |
| `handoff_root` | — | Override the handoff publication root. Default: repo-level `sure/handoffs`. |
| `output_dir` | — | Defaults to the run directory. |
| `since` | — | Incremental-scan timestamp (watch mode). |
| `max_retries` | — | Consecutive blocked gate attempts allowed per unit before the run terminates. Default `3`. |

The run directory (`<run_dir>`, provided by the Sure invocation) retains normal run products under `artifacts/` for auditability. The onboarding handoff is additionally published as a single model folder:

```text
sure/handoffs/<model_name>/
├── model_input.yaml
└── artifacts/
    ├── feed_report.json
    ├── scan_result.json
    ├── match_task_result.json
    ├── metadata_result.json
    ├── oref_result.json
    ├── model_input_result.json
    ├── rank_select_result.json
    ├── handoff_manifest.json
    └── source_run.json
```

`model_input.yaml` is the only canonical onboarding input. `artifacts/` is retained research/evidence metadata. Handoff folders are not deleted automatically after onboard; they are a stable user-facing cache and audit surface.

## State Machine

Advance happens **only** when the current unit's `produces` artifact is compliant (location + format + value domain; no forbidden fields). Linear units are agent self-driven; gate units additionally run a Python semantic check. Produce the current unit's artifact, then call `sure_update_state`.

| # | Unit | Kind | Produces | Gate script |
|---|------|------|----------|-------------|
| 1 | `scan_modelscope` | linear | `scan_result.json` | — |
| 2 | `match_task` | **gate** | `match_task_result.json` | `scripts/check_match_task.py` |
| 3 | `collect_metadata` | linear | `metadata_result.json` | — |
| 4 | `convert_to_oref` | linear | `oref_result.json` | — |
| 5 | `synthesize_model_input` | **gate** | `model_input_result.json` | `scripts/check_model_input.py` |
| 6 | `rank_and_select` | **gate** | `rank_select_result.json` | `scripts/check_rank_select.py` |
| 7 | `emit_handoff_manifest` | linear | `handoff_manifest.json` | — |

Total: 7 units. Retries cap at 3 per unit; on exhaustion the run is marked FAILED with a `failure_taxonomy` reason — no blind retry.

If the same hook/gate blocks three consecutive attempts, stop agent-side repair and ask the user to confirm the model link, access permissions, and whether the model card/README contains enough install, load, inference, fixture, and output-contract information. Do not keep fabricating fields to escape the hook.

## Red Lines

- **Strong-plus-weak matching is mandatory.** Every matched candidate must carry `match.match_source` such as `tasks`, `pipeline_tag`, `model_card`, `readme`, `repo_topics`, or `custom_tag`. The MATCH_TASK gate rejects candidates that claim `matched:true` without provenance. Short ambiguous abbreviations (e.g. `gr` in a name) are NOT task evidence by themselves — see `references/agents.md` for the `microsoft/Dayhoff-3b-GR-HM` false-positive case.
- **Research narrows broad provider tags.** Provider taxonomies such as HuggingFace `audio-text-to-text` are broad evidence, not final SURE tasks. Before finalizing `task_type`, read model id, tags, README/model card, examples, and output contract. Apply narrowing precedence `sa_asr > sd > asr > speech_understanding`: `transcribe/transcription/asr` plus `diarization/speaker attribution` means `sa_asr`, not generic `speech_understanding`.
- **No synthetic candidates.** When discovery fails, emit `candidates: []` and stop. Do not invent models.
- **MODEL_INPUT is the onboarding contract.** Before `/sure_onboard`, every selected model must have a complete `MODEL_INPUT` object matching `schemas/model_input.schema.json`: repo, weights, environment hint, phase-1 target, entrypoints, fixture, and IO contract.
- **Policy defaults must be explicit evidence.** If upstream documentation does not declare `environment_hint.python_version`, use the SURE policy default only with evidence `{source: local, field: sure_policy.python_version_default, model_input_field: environment_hint.python_version}`. Do not mark this field unresolved solely because the model card omitted a Python version.
- **Runtime strategy can bridge non-standard repos.** Some models document ONNX/CLI/runtime surfaces rather than a clean Python `import/load/infer` split. In that case, emit `runtime_strategy` and `policy_resolved:` entrypoints backed by provider tags/model-card evidence. `policy_resolved:` entrypoints without `runtime_strategy` are invalid.
- **Agent research first.** Scripts may extract candidate fields from provider APIs and model cards, but the agent must continue researching README/examples/source files when fields are unresolved. Hooks verify evidence and block hacks; they are not a substitute for research.
- **Fixture registry first.** After task matching, open the matching `fixtures/tasks/<task>/README.md` and select concrete files from that task registry. Model-card demo audio/text may be recorded as `provider_fixture_hint`, but it must not replace the task fixture unless a model-specific fixture is explicitly created and recorded.
- **GitHub is not a default weights source.** GitHub can be the repo/evidence source. `weights.source` must be one of `huggingface`, `modelscope`, `local`, `api`, `pip`, or `release_or_pypi`.
- **Handoff must be actionable.** `sure/handoffs/<model_name>/model_input.yaml` is the single onboarding input. The debug `handoff_manifest.json` must still carry `repo`, `weights_source`, and `model_input` or `model_input_path` for every selected model so the terminal state-machine unit remains auditable. The RANK_AND_SELECT gate rejects selections missing `repo` or with a negative `score`.

## Per-Unit Contracts

Each unit below lists: **Inputs** (what to read), **Output** (produces + schema), **Allowed** (value domain), **Must Not Do** (forbidden fields — anti-step-merge), **Failure** (taxonomy + which script). One unit at a time. After producing the current unit's artifact, call `sure_update_state`.

### 1. scan_modelscope (linear)
- **Inputs**: a direct model URL or the `source`, `query`/`filter`, `max_models`, `since` parameters. Direct URL mode is preferred: run `scripts/sure_feed_online_discover.py <url>` and skip search/ranking ambiguity. For online no-download search across ModelScope/HuggingFace/GitHub, run `scripts/sure_feed_online_discover.py <query>`. For legacy ModelScope-only reports, run `scripts/xforge_collect_model.py` or `xforge_daily_modelscope_summary.py`. For HuggingFace, use `--hf-endpoint auto` by default; if direct `huggingface.co` is unreachable, the provider falls back to `https://hf-mirror.com` and records `endpoint_used`/`fallback_reason`.
- **Output**: `scan_result.json` (`schemas/scan_result.schema.json`). Top-level `candidates[]`, each `{model_id, source, repo, source_url, tasks[], custom_tag, pipeline_tag, tags[], description, license, download_count}` when available.
- **Allowed**: `source ∈ {modelscope, huggingface, github, multi}`.
- **Must Not Do**: do NOT include `selected` or `handoff_manifest_path` here — those belong to later units (anti-merge). Do NOT invent candidates; an empty `candidates: []` is valid.
- **Failure**: `discovery_failed` (network/proxy/502 → rerun without proxies first), `no_candidates_in_window`.

### 2. match_task (gate)
- **Inputs**: `scan_result.json`. For each candidate, classify against the target SURE task using provider fields (`tasks`, HuggingFace `pipeline_tag`, repo topics) as starting evidence, then do research narrowing with model id, tags, README/model card, examples, and output-contract hints.
- **Output**: `match_task_result.json` (`schemas/match_task_result.schema.json`). Each candidate carries a `match` object: `{matched, match_source, task_type, score, evidence[]}`.
- **Allowed**: `matched ∈ {true, false}`. `match_source` must name the evidence channel.
- **Must Not Do**: do NOT claim `matched:true` without recording `match_source`. Do NOT let a short ambiguous abbreviation (e.g. `gr`) count as task evidence unless metadata also carries speech/gender semantics. Do NOT finalize broad `audio-text-to-text` as `speech_understanding` when model evidence says transcription plus diarization/speaker attribution; narrow it to `sa_asr`.
- **Gate**: `scripts/check_match_task.py` — verifies every matched candidate has a non-empty `match_source`. Exit 0 = pass.
- **Failure**: `missing_match_provenance`, `weak_abbreviation_false_positive`.

### 3. collect_metadata (linear)
- **Inputs**: matched candidates from `match_task_result.json`. Collect model card, README, release, package, dependency, and example-inference evidence. Run `scripts/xforge_modelscope_fetch.py` only when `download=true`; default feed mode should not fetch large weights.
- **Output**: `metadata_result.json` (`schemas/metadata_result.schema.json`). `models[]` each `{model_id, source, repo, source_url, weights_source, license, frameworks[], entrypoint_hints, environment_hints, metadata_summary}`.
- **Allowed**: `weights_source` may be `null` only while evidence is still insufficient; it must be resolved before `synthesize_model_input`.
- **Must Not Do**: do NOT include `handoff_manifest_path` here (anti-merge). Do NOT mark a remote model ready unless a deterministic local path is recorded.
- **Failure**: `fetch_failed`, `weights_unresolved`.

### 4. convert_to_oref (linear)
- **Inputs**: `metadata_result.json`. If artifacts were fetched, run `scripts/xforge_process_to_oref.py` (or `xforge_modelscope_dataset_to_oref.py` for datasets) to convert them to the SURE resource (oref) layout. If this is no-download discovery, emit a valid `oref_result.json` with `converted[]` entries that preserve `model_id` and null/unmaterialized weights paths as appropriate.
- **Output**: `oref_result.json` (`schemas/oref_result.schema.json`). `converted[]` each `{model_id, oref_path, weights_path}`.
- **Must Not Do**: do NOT include `handoff_manifest_path` here (anti-merge).
- **Failure**: `conversion_failed`, `oref_path_missing`.

### 5. synthesize_model_input (gate)
- **Inputs**: `metadata_result.json`, `oref_result.json`, and task evidence from `match_task_result.json`. Build one canonical `MODEL_INPUT` per matched candidate.
- **Output**: `model_input_result.json` (`schemas/model_input_result.schema.json`). `model_inputs[]` each `{model_id, source, model_input_path, model_input, evidence[], confidence, missing_or_weak_fields}`.
- **Allowed**: `model_input.task_type ∈ {asr,s2tt,sd,ser,tts,vc,kws,slu,gr,speech_understanding,sa-asr,sa_asr}`; `deployment_type ∈ {local,api}`; `weights.source ∈ {huggingface,modelscope,local,api,pip,release_or_pypi}`.
- **Environment and runtime strategy**: `environment_hint.python_version` may come from upstream documentation or from the SURE policy default; record the source in evidence and, when available, `python_version_source`. If README/API evidence shows a runtime surface such as `sherpa-onnx`, `onnxruntime`, CLI serving, or another non-standard adapter path, emit `runtime_strategy {type, framework, load_surface, inference_surface}` and use `policy_resolved:` entrypoints instead of fabricating Python import/load snippets.
- **Fixture selection**: use `scripts/sure_feed/fixture_registry.py` or equivalent agent research to read `fixtures/tasks/<task>/README.md`, select the task-specific smoke fixture, and emit `model_input.fixture.fixture_source: task_registry` with `fixture_index`, `fixture_root`, and concrete sample paths. For `speech_understanding`, read `fixtures/tasks/speech_understanding/README.md`, infer atomic subtasks from model evidence, then select only those atomic fixture indexes.
- **Must Not Do**: do NOT pass a partial onboarding input. Do NOT set `weights.source: github`; use `release_or_pypi` for GitHub release assets or a concrete HuggingFace/ModelScope/local/API/pip source. Do NOT use environment setup commands (`python -m venv`, `pip install`, `conda create`, `uv venv`, system package installation) as inference entrypoints. Do NOT use `tests/fixtures/shared/...` or provider demo audio as the primary fixture when `fixtures/tasks/<task>/` has a representative sample.
- **Gate**: `scripts/check_model_input.py` — verifies every emitted `model_input` has repo, weights, environment hint, entrypoints, fixture, IO contract, evidence, and valid enums. Exit 0 = pass.
- **Failure**: `model_input_incomplete`, `weights_source_invalid`, `missing_evidence`.

### 6. rank_and_select (gate)
- **Inputs**: `model_input_result.json` plus earlier task/metadata evidence. Score candidates (task match strength, evidence quality, downloads/stars, recency, license, runtime clarity) and select.
- **Output**: `rank_select_result.json` (`schemas/rank_select_result.schema.json`). `selected[]` each `{model_id, repo, weights_source, task_type, score, rank_reason, model_input_path, model_input}`.
- **Allowed**: `score` must be `≥ 0`.
- **Must Not Do**: do NOT select a candidate without `repo` — `/sure_onboard` cannot handoff without a repo. Do NOT use a negative `score`.
- **Gate**: `scripts/check_rank_select.py` — verifies the selection is non-empty and every selected candidate has `repo` + non-negative `score`. Exit 0 = pass.
- **Failure**: `selection_empty`, `missing_repo_for_handoff`, `negative_score`.

### 7. emit_handoff_manifest (linear, terminal)
- **Inputs**: `rank_select_result.json`. Assemble the handoff manifest consumed by `/sure_onboard`.
- **Output**: `artifacts/debug/handoff_manifest.json` (`schemas/handoff_manifest.schema.json`). `{manifest_path, source, models[{model_id, repo, weights_source, task_type, score, model_input_path, model_input}], next_action}`. The user-facing onboarding input is published at `sure/handoffs/<model_name>/model_input.yaml`; the user-facing summary is copied into `sure/handoffs/<model_name>/artifacts/feed_report.json`.
- **Must Not Do**: do NOT omit `manifest_path` or `models`. Every `models[]` entry must carry `repo` and enough `MODEL_INPUT` data for `/sure_onboard`.
- **Failure**: `manifest_incomplete`.

## Backend Routing

- `scripts/sure_feed_online_discover.py` is the preferred no-download online discovery entrypoint. It writes run-local audit products under `<run_dir>/artifacts/` and publishes the clean onboarding product under `sure/handoffs/<model_name>/model_input.yaml`. The skeleton artifacts (`scan_result.json`, `match_task_result.json`, `metadata_result.json`, `oref_result.json`, `model_input_result.json`, `rank_select_result.json`, `handoff_manifest.json`) are debug-only in the run and copied into the handoff `artifacts/` folder.
- Flat scripts (`xforge_*.py`) do the network/disk work; the `sure_feed/` inner package provides catalog/bridge/watcher logic imported by the flat scripts.
- Only `xforge_modelscope_dataset_to_oref.py` imports `modelscope` at module top level; the `sure_feed/` package itself imports cleanly without it, so gate scripts run under bare `python3`.
- Network ModelScope calls need proxy variables removed (`env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy`). A transient `502 Bad Gateway` is not "no candidates" — rerun first.
- HuggingFace metadata calls use the canonical `https://huggingface.co` repo URL in `MODEL_INPUT` even when the actual API request used `https://hf-mirror.com`; mirror usage is evidence, not the canonical model identity.
- Per-skill dependency declaration lives in `scripts/pyproject.toml` (pyyaml, modelscope).

## Cross-Skill Handoff

`sure/handoffs/<model_name>/model_input.yaml` → `/sure_onboard model=<model_name>` or `/sure_onboard model_input_path=sure/handoffs/<model_name>/model_input.yaml` → model onboarded into global `sure/models/<model_id>/` with `verdict.json` → `/sure_eval model=<id>` reads that `verdict.json` to judge readiness. `sure/handoffs/<model_name>/artifacts/feed_report.json` is the user-facing run summary; `artifacts/debug/handoff_manifest.json` is retained as the terminal state-machine artifact.

See `references/agents.md` for the full matching contract, false-positive case, and backend routing table.
