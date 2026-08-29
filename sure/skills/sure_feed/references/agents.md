# SURE Feed Bridge Guide (in-package)

**Scope**: discover ModelScope/HuggingFace/GitHub speech models, match them to
SURE tasks, collect runtime evidence, optionally convert fetched resources to
the SURE resource layout (oref), synthesize canonical `MODEL_INPUT`, rank, and
emit a handoff manifest that `/sure_onboard` consumes. Self-contained inside
the `sure_feed` skill package — no external `XForge/skills` or upstream
`/hpc/.../sure` repo references.

**Role boundary**: this is the model-feeding bridge. It is NOT the onboarding
agent (`sure_onboard/SKILL.md`) and NOT the evaluation agent
(`sure_eval/SKILL.md`). It hands selected models to `/sure_onboard` via
`artifacts/debug/model_input_result.json` and
`artifacts/debug/handoff_manifest.json`.

---

## Non-Negotiable Rules

- Do not invent synthetic candidates when discovery fails — record an empty
  `candidates: []` and stop.
- Do not mark a remote model ready. Feed only discovers and normalizes evidence;
  `/sure_onboard` owns build, runtime validation, Docker, registry, and VC
  readiness.
- Strong-plus-weak task matching is mandatory (see §1) — never regress to
  one side. Preserve `match_source` provenance on every candidate.
- Every selected model must have a complete `MODEL_INPUT` before handoff:
  repo, weights, environment hint, phase-1 target, entrypoints, fixture, and IO
  contract.
- The user-facing onboarding artifacts are exactly `artifacts/model_input.yaml`
  and `artifacts/feed_report.json`. Candidate-specific YAML folders are
  debug-only and should not be used as the final handoff shape.
- GitHub is a repo/evidence source, not a default weights source. Use
  `release_or_pypi`, `huggingface`, `modelscope`, `local`, `api`, or `pip`.
- The debug handoff manifest must carry `repo`, `weights_source`, and
  `model_input` or `model_input_path` for every selected model so the terminal
  state-machine unit is auditable. `/sure_onboard` consumes
  `artifacts/model_input.yaml`.
- Online ModelScope discovery needs network access; remove proxy variables.
  Transient `502 Bad Gateway` is not "no candidates" — rerun first.

---

## 1. Multi-Source Matching Contract

Discovery for models must use a strong-plus-weak matching strategy:

- **Strong match first**: query structured provider semantics for the target
  task. Examples: ModelScope `tasks`, HuggingFace `pipeline_tag`, GitHub repo
  topics or release/package metadata.
- **Weak fallback second**: also inspect task abbreviations and speech-domain
  terms (`asr`, `speech`), accepting candidates whose metadata matches through
  model cards, README examples, custom tags, names, descriptions, or tags.
- **Short ambiguous abbreviations are NOT sufficient task evidence by
  themselves.** `gr` in a resource name does not imply gender recognition
  unless metadata also contains gender semantics or speech-domain evidence.
  `microsoft/Dayhoff-3b-GR-HM` is a known false-positive: it has
  `task:text-generation` + `custom_tag:protein-generation` → must be
  excluded from GR.
- **Preserve match provenance** on every candidate:
  - `match_source: "tasks"` (strong, official task)
  - `match_source: "pipeline_tag"` (strong HuggingFace task)
  - `match_source: "repo_topics"` (GitHub topic evidence)
  - `match_source: "model_card"` / `"readme"` / `"custom_tag"` (weaker fallback)

Some valid ModelScope speech models do not populate `tasks` — find them
through the `custom_tag` weak fallback when inside the requested time window.

Daily ranking: `task match → evidence quality → requested time window → report-date candidates first → downloads/stars`.

---

## 2. Backend Routing Rules (in-package scripts)

All backend scripts live under `scripts/`. Flat scripts (`xforge_*.py`) do the
network/disk work; the `sure_feed/` inner package provides catalog/bridge/watcher
logic. The two gate scripts are thin semantic validators:

| Script | Called by | Purpose |
|--------|-----------|---------|
| `sure_feed_online_discover.py` | agent (scan_modelscope unit) | no-download direct URL or search discovery for ModelScope/HuggingFace/GitHub, emits `artifacts/model_input.yaml`, `artifacts/feed_report.json`, and debug skeleton artifacts under `artifacts/debug/` |
| `xforge_collect_model.py` | agent (scan_modelscope unit) | discover candidates from ModelScope |
| `xforge_modelscope_fetch.py` | agent (collect_metadata unit) | fetch weights under local cache, record paths |
| `xforge_process_to_oref.py` / `xforge_modelscope_dataset_to_oref.py` | agent (convert_to_oref unit) | convert fetched artifacts to SURE oref layout |
| `xforge_watch_modelscope.py` / `xforge_daily_modelscope_summary.py` | agent (scan/watch mode) | incremental watch + daily human-review report |
| `sure_feed/bridge.py`, `catalog.py`, `modelscope_*.py`, `tool_agent_controller.py` | imported by flat scripts | in-package library |
| `check_match_task.py` | hook (MATCH_TASK gate) | verify every matched candidate carries `match_source` |
| `check_model_input.py` | hook (SYNTHESIZE_MODEL_INPUT gate) | verify every candidate has complete canonical `MODEL_INPUT` |
| `check_rank_select.py` | hook (RANK_AND_SELECT gate) | verify every selected candidate has `score ≥ 0` + `repo` |

**Heavy dependency note**: only `xforge_modelscope_dataset_to_oref.py` imports
`modelscope` at module top level. The `sure_feed/` package itself imports
cleanly without it, so gate scripts run under the locked common `HARNESS_PYTHON_BIN`.

**HuggingFace endpoint policy**: try canonical `https://huggingface.co` first.
If the request fails because the endpoint is unreachable or returns a transient
5xx, retry metadata reads against `https://hf-mirror.com`. Do not fallback for
401/403/private/gated model errors. Keep `MODEL_INPUT.repo.url` canonical
(`https://huggingface.co/<model_id>`) and record the actual endpoint in
evidence as `endpoint_used`.

---

## 3. Handoff Contract

`artifacts/model_input.yaml` is the canonical user-facing onboarding artifact.
`artifacts/feed_report.json` is the user-facing run summary.
`artifacts/debug/model_input_result.json` is the canonical intermediate
contract. The terminal `artifacts/debug/handoff_manifest.json` (emitted by the
EMIT_HANDOFF_MANIFEST unit) is retained for auditability:

```json
{
  "manifest_path": "<abs path to this file>",
  "source": "multi",
  "models": [
    {
      "model_id": "<slug>",
      "repo": "<repo URL or local path>",
      "weights_source": "huggingface",
      "task_type": "asr",
      "score": 1.5,
      "model_input_path": "artifacts/model_input.yaml",
      "model_input": {
        "model_id": "<owner/model>",
        "model_name": "<slug>",
        "task_type": "asr",
        "deployment_type": "local",
        "repo": {"url": "<repo URL>", "commit": null},
        "weights": {
          "source": "huggingface",
          "local_path": null,
          "required": true,
          "cache_policy": "model_local_first",
          "local_dir_name": "checkpoints"
        },
        "environment_hint": {
          "preferred_backend": "uv",
          "python_version": "3.10",
          "requires_gpu": true,
          "system_packages": ["ffmpeg", "libsndfile1"]
        },
        "phase1_runtime_target": ["import", "load", "infer"],
        "entrypoints": {
          "import_test": "import package",
          "load_test": "model = package.load_model('tiny', 'cpu')",
          "infer_test": "model.transcribe('fixtures/tasks/asr/qwen3_asr_smoke/asr_en/sample_1_367-130732-0006.wav')"
        },
        "fixture": {
          "fixture_id": "asr/qwen3_asr_smoke/asr_en",
          "fixture_source": "task_registry",
          "fixture_index": "fixtures/tasks/asr/README.md",
          "fixture_root": "fixtures/tasks/asr/qwen3_asr_smoke/asr_en",
          "gt": "fixtures/tasks/asr/qwen3_asr_smoke/asr_en/gt.jsonl",
          "audio": "fixtures/tasks/asr/qwen3_asr_smoke/asr_en/sample_1_367-130732-0006.wav",
          "task_specific": true,
          "fallback_allowed": false
        },
        "io_contract": {
          "input_type": "audio_path",
          "output_type": "json",
          "primary_field": "text",
          "required_fields": ["text"],
          "nonempty_fields": ["text"],
          "json_serializable": true
        }
      }
    }
  ],
  "next_action": "run /sure_onboard model_input_path=artifacts/model_input.yaml"
}
```

`/sure_onboard` reads `MODEL_INPUT` from `artifacts/model_input.yaml` and stages
the model in the global `sure/models/<model_name>/`, writing
`artifacts/verdict.json` and, once the bundle is sealed,
`artifacts/deployment_ready.json`. Staging is not approval: an operator reviews
the bundle and copies the complete model directory into a configured
`approved_models_roots` entry. `/sure_eval model=<model_name>` resolves only
that approved copy, and reads the `verdict.json` inside it to judge readiness.
