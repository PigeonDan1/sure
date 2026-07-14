# /sure_feed

Feed ModelScope (or HuggingFace) speech models into the SURE pipeline: scan, match to a SURE task family, collect metadata, convert to the SURE resource layout (oref), rank/select, and emit a handoff manifest that `/sure_onboard` consumes. This skill is the Sure port of the XForge→SURE bridge. The state machine lives in `hooks/state-machine.ts`; this document is what the agent reads to drive each unit.

Control principle: **agent decides scope, scripts enforce format and execution.** You (the agent) choose the source, query, filters, and watch mode; the deterministic scripts under `scripts/` and the hook gates enforce that every artifact is in the right place, the right format, and the right value domain — and that the strong-plus-weak matching contract holds.

This skill is **self-contained**: all backend code is bundled under `scripts/` (the `sure_feed/` inner package + flat `xforge_*.py` scripts + gate validators). There are no references to the external `/hpc/.../sure` repo or to `XForge/skills`.

## Parameters

| Parameter | Required | Meaning |
|-----------|----------|---------|
| `source` | — | `modelscope \| huggingface`. Default `modelscope`. |
| `watch_mode` | — | `once \| watch`. Default `once`. |
| `query` / `filter` | — | Task / license / time-window filters for discovery. |
| `max_models` | — | Cap on candidates scanned this run. |
| `handoff` | — | When true (default), emit `handoff_manifest.json` for `/sure_onboard`. |
| `output_dir` | — | Defaults to the run directory. |
| `since` | — | Incremental-scan timestamp (watch mode). |

The run directory (`<run_dir>`, provided by the Sure invocation) holds structured outputs under `<run_dir>/artifacts/<unit.produces>`. The terminal `handoff_manifest.json` is what downstream skills read; its `models[].repo` / `weights_source` feed `/sure_onboard`.

## State Machine

Advance happens **only** when the current unit's `produces` artifact is compliant (location + format + value domain; no forbidden fields). Linear units are agent self-driven; gate units additionally run a Python semantic check. Produce the current unit's artifact, then call `sure_update_state`.

| # | Unit | Kind | Produces | Gate script |
|---|------|------|----------|-------------|
| 1 | `scan_modelscope` | linear | `scan_result.json` | — |
| 2 | `match_task` | **gate** | `match_task_result.json` | `scripts/check_match_task.py` |
| 3 | `collect_metadata` | linear | `metadata_result.json` | — |
| 4 | `convert_to_oref` | linear | `oref_result.json` | — |
| 5 | `rank_and_select` | **gate** | `rank_select_result.json` | `scripts/check_rank_select.py` |
| 6 | `emit_handoff_manifest` | linear | `handoff_manifest.json` | — |

Total: 6 units. Retries cap at 3 per unit; on exhaustion the run is marked FAILED with a `failure_taxonomy` reason — no blind retry.

## Red Lines

- **Strong-plus-weak matching is mandatory.** Every matched candidate must carry `match.match_source` (`"tasks"` strong, or `"custom_tag"` weak). The MATCH_TASK gate rejects candidates that claim `matched:true` without provenance. Short ambiguous abbreviations (e.g. `gr` in a name) are NOT task evidence by themselves — see `references/agents.md` for the `microsoft/Dayhoff-3b-GR-HM` false-positive case.
- **No synthetic candidates.** When discovery fails, emit `candidates: []` and stop. Do not invent models.
- **Handoff manifest must be actionable.** Every selected model in `handoff_manifest.json` must carry `repo` (and `weights_source` when known) — `/sure_onboard` needs these to clone/fetch. The RANK_AND_SELECT gate rejects selections missing `repo` or with a negative `score`.

## Per-Unit Contracts

Each unit below lists: **Inputs** (what to read), **Output** (produces + schema), **Allowed** (value domain), **Must Not Do** (forbidden fields — anti-step-merge), **Failure** (taxonomy + which script). One unit at a time. After producing the current unit's artifact, call `sure_update_state`.

### 1. scan_modelscope (linear)
- **Inputs**: the `source`, `query`/`filter`, `max_models`, `since` parameters. Run `scripts/xforge_collect_model.py` (or `xforge_daily_modelscope_summary.py` for daily reports) to query ModelScope.
- **Output**: `scan_result.json` (`schemas/scan_result.schema.json`). Top-level `candidates[]`, each `{model_id, repo, tasks[], custom_tag, download_count}`.
- **Allowed**: `source ∈ {modelscope, huggingface}`.
- **Must Not Do**: do NOT include `selected` or `handoff_manifest_path` here — those belong to later units (anti-merge). Do NOT invent candidates; an empty `candidates: []` is valid.
- **Failure**: `discovery_failed` (network/proxy/502 → rerun without proxies first), `no_candidates_in_window`.

### 2. match_task (gate)
- **Inputs**: `scan_result.json`. For each candidate, classify against the target SURE task using strong (ModelScope `tasks` semantics) then weak (`custom_tag` / name / description) fallback.
- **Output**: `match_task_result.json` (`schemas/match_task_result.schema.json`). Each candidate carries a `match` object: `{matched, match_source, task_type, score}`.
- **Allowed**: `match_source ∈ {"tasks", "custom_tag"}`. `matched ∈ {true, false}`.
- **Must Not Do**: do NOT claim `matched:true` without recording `match_source`. Do NOT let a short ambiguous abbreviation (e.g. `gr`) count as task evidence unless metadata also carries speech/gender semantics.
- **Gate**: `scripts/check_match_task.py` — verifies every matched candidate has a non-empty `match_source`. Exit 0 = pass.
- **Failure**: `missing_match_provenance`, `weak_abbreviation_false_positive`.

### 3. collect_metadata (linear)
- **Inputs**: matched candidates from `match_task_result.json`. Run `scripts/xforge_modelscope_fetch.py` to fetch weights under a local cache and record paths.
- **Output**: `metadata_result.json` (`schemas/metadata_result.schema.json`). `models[]` each `{model_id, repo, weights_source, license, frameworks[], metadata_summary}`.
- **Allowed**: `weights_source` may be `null` if not yet fetched (resolved in convert_to_oref).
- **Must Not Do**: do NOT include `handoff_manifest_path` here (anti-merge). Do NOT mark a remote model ready unless a deterministic local path is recorded.
- **Failure**: `fetch_failed`, `weights_unresolved`.

### 4. convert_to_oref (linear)
- **Inputs**: `metadata_result.json`. Run `scripts/xforge_process_to_oref.py` (or `xforge_modelscope_dataset_to_oref.py` for datasets) to convert fetched artifacts to the SURE resource (oref) layout.
- **Output**: `oref_result.json` (`schemas/oref_result.schema.json`). `converted[]` each `{model_id, oref_path, weights_path}`.
- **Must Not Do**: do NOT include `handoff_manifest_path` here (anti-merge).
- **Failure**: `conversion_failed`, `oref_path_missing`.

### 5. rank_and_select (gate)
- **Inputs**: `oref_result.json`. Score candidates (task match strength, downloads, time-window recency) and select.
- **Output**: `rank_select_result.json` (`schemas/rank_select_result.schema.json`). `selected[]` each `{model_id, repo, weights_source, score, rank_reason}`.
- **Allowed**: `score` must be `≥ 0`.
- **Must Not Do**: do NOT select a candidate without `repo` — `/sure_onboard` cannot handoff without a repo. Do NOT use a negative `score`.
- **Gate**: `scripts/check_rank_select.py` — verifies the selection is non-empty and every selected candidate has `repo` + non-negative `score`. Exit 0 = pass.
- **Failure**: `selection_empty`, `missing_repo_for_handoff`, `negative_score`.

### 6. emit_handoff_manifest (linear, terminal)
- **Inputs**: `rank_select_result.json`. Assemble the handoff manifest consumed by `/sure_onboard`.
- **Output**: `handoff_manifest.json` (`schemas/handoff_manifest.schema.json`). `{manifest_path, source, models[{model_id, repo, weights_source, task_type, score}], next_action}`.
- **Must Not Do**: do NOT omit `manifest_path` or `models`. Every `models[]` entry must carry `repo`.
- **Failure**: `manifest_incomplete`.

## Backend Routing

- Flat scripts (`xforge_*.py`) do the network/disk work; the `sure_feed/` inner package provides catalog/bridge/watcher logic imported by the flat scripts.
- Only `xforge_modelscope_dataset_to_oref.py` imports `modelscope` at module top level; the `sure_feed/` package itself imports cleanly without it, so gate scripts run under bare `python3`.
- Network ModelScope calls need proxy variables removed (`env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy`). A transient `502 Bad Gateway` is not "no candidates" — rerun first.
- Per-skill dependency declaration lives in `scripts/pyproject.toml` (pyyaml, modelscope).

## Cross-Skill Handoff

`handoff_manifest.json` → `/sure_onboard model_id=<id> repo=<repo> weights_source=<weights>` → model onboarded into global `sure/models/<model_id>/` with `verdict.json` → `/sure_eval model=<id>` reads that `verdict.json` to judge readiness. There is no direct skill-to-skill call; handoff is via artifact paths + the manifest.

See `references/agents.md` for the full matching contract, false-positive case, and backend routing table.
