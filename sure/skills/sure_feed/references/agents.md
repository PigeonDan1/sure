# SURE Feed Bridge Guide (in-package)

**Scope**: scan ModelScope/HuggingFace for speech models, match them to SURE
tasks, convert to the SURE resource layout (oref), rank, and emit a handoff
manifest that `/sure_onboard` consumes. Self-contained inside the `sure_feed`
skill package — no external `XForge/skills` or upstream `/hpc/.../sure` repo
references.

**Role boundary**: this is the model-feeding bridge. It is NOT the onboarding
agent (`sure_onboard/SKILL.md`) and NOT the evaluation agent
(`sure_eval/SKILL.md`). It hands selected models to `/sure_onboard` via
`handoff_manifest.json`.

---

## Non-Negotiable Rules

- Do not invent synthetic candidates when discovery fails — record an empty
  `candidates: []` and stop.
- Do not mark a remote model ready unless a deterministic local path has been
  recorded (weights_source / oref_path in the manifest).
- Strong-plus-weak task matching is mandatory (see §1) — never regress to
  one side. Preserve `match_source` provenance on every candidate.
- Handoff manifest must carry `repo` (and `weights_source` when known) for
  every selected model — `/sure_onboard` needs these to clone/fetch.
- Online ModelScope discovery needs network access; remove proxy variables.
  Transient `502 Bad Gateway` is not "no candidates" — rerun first.

---

## 1. ModelScope Matching Contract

ModelScope discovery for models must use a strong-plus-weak matching strategy:

- **Strong match first**: query the ModelScope task-page semantics for the
  target task. For ASR: `tasks=auto-speech-recognition&type=audio`.
- **Weak fallback second**: also query task abbreviations and speech-domain
  terms (`asr`, `speech`), accept candidates whose metadata matches through
  custom tags, name, description, or tags.
- **Short ambiguous abbreviations are NOT sufficient task evidence by
  themselves.** `gr` in a resource name does not imply gender recognition
  unless metadata also contains gender semantics or speech-domain evidence.
  `microsoft/Dayhoff-3b-GR-HM` is a known false-positive: it has
  `task:text-generation` + `custom_tag:protein-generation` → must be
  excluded from GR.
- **Preserve match provenance** on every candidate:
  - `match_source: "tasks"` (strong, official task)
  - `match_source: "custom_tag"` (weak fallback)

Some valid ModelScope speech models do not populate `tasks` — find them
through the `custom_tag` weak fallback when inside the requested time window.

Daily ranking: `task match → requested time window → report-date candidates first → downloads`.

---

## 2. Backend Routing Rules (in-package scripts)

All backend scripts live under `scripts/`. Flat scripts (`xforge_*.py`) do the
network/disk work; the `sure_feed/` inner package provides catalog/bridge/watcher
logic. The two gate scripts are thin semantic validators:

| Script | Called by | Purpose |
|--------|-----------|---------|
| `xforge_collect_model.py` | agent (scan_modelscope unit) | discover candidates from ModelScope |
| `xforge_modelscope_fetch.py` | agent (collect_metadata unit) | fetch weights under local cache, record paths |
| `xforge_process_to_oref.py` / `xforge_modelscope_dataset_to_oref.py` | agent (convert_to_oref unit) | convert fetched artifacts to SURE oref layout |
| `xforge_watch_modelscope.py` / `xforge_daily_modelscope_summary.py` | agent (scan/watch mode) | incremental watch + daily human-review report |
| `sure_feed/bridge.py`, `catalog.py`, `modelscope_*.py`, `tool_agent_controller.py` | imported by flat scripts | in-package library |
| `check_match_task.py` | hook (MATCH_TASK gate) | verify every matched candidate carries `match_source` |
| `check_rank_select.py` | hook (RANK_AND_SELECT gate) | verify every selected candidate has `score ≥ 0` + `repo` |

**Heavy dependency note**: only `xforge_modelscope_dataset_to_oref.py` imports
`modelscope` at module top level. The `sure_feed/` package itself imports
cleanly without it, so gate scripts run under a bare `python3`.

---

## 3. Handoff Contract

`handoff_manifest.json` (emitted by the EMIT_HANDOFF_MANIFEST unit) is consumed
by `/sure_onboard`:

```json
{
  "manifest_path": "<abs path to this file>",
  "source": "modelscope",
  "models": [
    {
      "model_id": "<slug>",
      "repo": "<repo URL or local path>",
      "weights_source": "<weights URL/path or null>",
      "task_type": "asr",
      "score": 1.5
    }
  ],
  "next_action": "run /sure_onboard model_id=<id> repo=<repo> weights_source=<weights>"
}
```

`/sure_onboard` reads `repo` + `weights_source` from each entry, onboards the
model into the global `sure/models/<model_id>/`, and writes `verdict.json`.
`/sure_eval` then reads that `verdict.json` to judge readiness.
