# Sure

Sure is a Pi coding-agent harness for running scientific and model-evaluation workflows as slash-command skills.

The design boundary is intentional:

- Harness owns slash-command discovery, run lifecycle, state persistence, hook execution, tool gates, and final manifest validation.
- Skill packages own domain prompts, deterministic scripts, state machines, schemas, checkpoints, validation rules, and repair instructions.

Do not move task-specific phases, metrics, or SURE business logic into Harness unless the rule is truly common to every skill.

## Implemented Slash Commands

Only these repository skills are implemented today:

| Command | Purpose | Required inputs | Required terminal artifact |
|---------|---------|-----------------|----------------------------|
| `/paper_collect` | Offline deterministic paper collection template. | Topic text; optional `target <number>` or `target=<number>`. | `artifacts/papers.manifest.json` |
| `/scholar_profile` | Build a scholar profile and persona system prompt from DBLP/OpenAlex/Google Scholar/personal pages. | `scholar_name`; requires `OPENAI_API_KEY`. | `system_prompt.md`, `scholars.csv`, `source_urls.json`, `mainline.json`, `manifest.json` |
| `/sure_feed` | Scan ModelScope/HuggingFace models, match them to SURE task families, rank them, and emit a handoff manifest. | Optional `source`, `query`/`filter`, `max_models`, `since`, `watch_mode`. | `artifacts/handoff_manifest.json` |
| `/sure_onboard` | Onboard or repair an audio model into `sure/models/<model_id>/` with wrapper, spec, validation, and verdict. | `model_id`, `repo`, `task_type`, `deployment_type`. | `artifacts/verdict.json` |
| `/sure_eval` | Evaluate an already-onboarded audio model through the SURE-EVAL main-flow agent. | `model`, `task`. | `artifacts/main_agent_run_report.json`, `artifacts/execution_surface.json` |

Other command names may still be registered in Harness for future packages, but they are not usable until a matching `sure/skills/<skill>/sure.skill.json` exists.

## Typical Workflows

Paper collection:

```text
/paper_collect graph neural networks after 2022, target 10
```

Scholar profile:

```text
/scholar_profile scholar_name="Yoshua Bengio" language=en
```

SURE model pipeline:

```text
/sure_feed source=modelscope query="asr english" max_models=20
/sure_onboard model_id=<id> repo=<repo> task_type=asr deployment_type=local weights_source=<weights>
/sure_eval model=<id> task=asr max_samples=20
```

The SURE pipeline handoff is artifact-based. `/sure_feed` writes `handoff_manifest.json`; `/sure_onboard` consumes the selected repo/weights and writes global model products under `sure/models/<model_id>/`; `/sure_eval` reads that model directory. Skills do not call each other directly.

## Development

Install and check from the repo root:

```bash
npm install --ignore-scripts
npm run check
./pi-test.sh
```

Use `./pi-test.sh` to start the local TUI from source and run slash commands interactively.

Do not run `npm run build` or the full test suite unless specifically needed. For Sure-focused changes, prefer targeted checks:

```bash
cd packages/coding-agent
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-extension.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-feed.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-onboard-state-machine.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-eval-state-machine.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-eval-red-lines.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-eval-runbackend.test.ts
```

Hook type-checking is part of `npm run check` through:

```bash
npm run check:sure-hooks
```

## Skill Package Layout

Shared repository skills live under:

```text
sure/skills/<skill-name>/
```

Project-local or experimental overrides live under:

```text
.sure/skills/<skill-name>/
```

Discovery checks both locations. A project-local package overrides a repository package for the same command.

Expected package shape:

```text
sure/skills/<skill-name>/
  sure.skill.json
  SKILL.md
  hooks/
    index.ts
  scripts/
  schemas/
  examples/
  references/
```

Only `sure.skill.json` and the prompt file are required for discovery. Production skills should include hooks, scripts, schemas, and examples so the workflow is reviewable and testable.

## `sure.skill.json`

The manifest binds a package to a slash command:

```json
{
  "name": "sure_feed",
  "command": "/sure_feed",
  "description": "Feed ModelScope models into the SURE pipeline.",
  "prompt": "SKILL.md",
  "hooks": {
    "pre_start": [{ "module": "hooks/index.ts", "handler": "preStart" }],
    "pre_tool_call": [{ "module": "hooks/index.ts", "handler": "preToolCall" }],
    "post_tool_result": [{ "module": "hooks/index.ts", "handler": "postToolResult" }],
    "pre_finish": [{ "module": "hooks/index.ts", "handler": "preFinish" }],
    "post_finish": [{ "module": "hooks/index.ts", "handler": "postFinish" }],
    "on_error": [{ "module": "hooks/index.ts", "handler": "onError" }]
  },
  "artifacts": [
    {
      "type": "handoff_manifest",
      "path": "artifacts/handoff_manifest.json",
      "required": true,
      "description": "Final handoff manifest for /sure_onboard."
    }
  ],
  "ui": {
    "primaryCounters": ["completed_units", "total_units", "gate_blocks"],
    "artifactTypes": ["handoff_manifest"],
    "defaultExpandedSections": ["diagnostics", "artifacts"]
  }
}
```

Rules:

- `command` must be a registered Sure command.
- `prompt` and hook module paths must stay inside the skill package.
- Required artifacts should declare fixed paths when the Harness must verify them.
- `ui` is a display hint only. It must not encode workflow semantics.

## Prompt, Scripts, And Hooks

`SKILL.md` is the agent-facing operating manual. It should define arguments, workflow order, scripts to run, state updates, success criteria, and failure handling.

Scripts do deterministic work: API calls, downloads, parsing, conversion, scoring, validation, and export. They should accept explicit input/output paths, write durable files, and exit non-zero on unrecoverable failures.

Hooks enforce gates around the agent:

- `pre_start`: validate inputs, environment, credentials, and writable dirs.
- `pre_tool_call`: block unsafe or out-of-protocol tool calls.
- `post_tool_result`: inspect outputs, advance state machines, and return repairs.
- `pre_finish`: validate final artifacts before accepting `sure_finish`.
- `post_finish`: summarize or register accepted results.
- `on_error`: persist failure diagnostics.

Hook imports must use the hook entrypoint:

```ts
import type { SureHookContext, SureHookResult } from "@earendil-works/pi-coding-agent/hooks";
```

Keep hooks small. Heavy work belongs in scripts; hooks should validate, gate, advance checkpoints, and return repair instructions.

## Runtime Contract

Every Sure invocation creates a run directory:

```text
.sure/runs/<runId>/
  state.json
  events.jsonl
  manifest.json
  artifacts/
```

The agent can update display state through `sure_update_state`:

```json
{
  "phase": { "id": "match_task", "label": "Matching task", "status": "running", "progress": 0.4 },
  "message": "Matched 12 candidates.",
  "progress": 0.4,
  "counters": { "completed_units": 2, "total_units": 6 },
  "diagnostics": [{ "severity": "warning", "message": "Some metadata fetches failed." }],
  "artifacts": [{ "type": "scan_result", "path": "artifacts/scan_result.json", "status": "ready" }],
  "next_actions": ["Collect metadata"]
}
```

`sure_update_state` may update only `phase`, `message`, `progress`, `counters`, `diagnostics`, `artifacts`, and `next_actions`.

Checkpoints are controlled by hooks, not by model tool calls. A hook can persist a checkpoint by returning `state_patch.checkpoint`; `sure_update_state` rejects checkpoint updates.

## Final Manifest Contract

Every run must finish through `sure_finish` with a JSON manifest. The common envelope is:

```json
{
  "schema_version": "1",
  "run_id": "<runId>",
  "skill_name": "sure_feed",
  "status": "success",
  "created_at": "2026-07-14T00:00:00.000Z",
  "inputs": {},
  "outputs": {},
  "validation": {},
  "artifacts": [
    {
      "type": "handoff_manifest",
      "path": "artifacts/handoff_manifest.json"
    }
  ]
}
```

Harness validates:

- The manifest is JSON and contains `schema_version`, `run_id`, `skill_name`, `status`, `created_at`, `inputs`, `outputs`, and `validation`.
- `run_id` and `skill_name` match the active run.
- Manifest `status` matches `sure_finish.status`.
- `created_at` is parseable as an ISO date.
- `inputs`, `outputs`, and `validation` are JSON objects.
- Every artifact entry includes a non-empty `path`.
- Every artifact path exists, either as a project path or a run-relative path.
- Every required artifact in `sure.skill.json` is present in the final manifest and its declared path exists.

Run-relative artifact paths such as `artifacts/main_agent_run_report.json` are supported. Absolute paths under the project are also supported. Prefer run-relative paths inside manifests unless a skill intentionally produces global products such as `sure/models/<model_id>/`.

## SURE Business Rules

`/sure_feed`:

- Strong-plus-weak matching is mandatory. Matched candidates must record `match.match_source`.
- Do not synthesize candidates. If discovery returns nothing, emit `candidates: []`.
- `handoff_manifest.json` must be actionable: selected models need `repo`, and `weights_source` when known.

`/sure_onboard`:

- Model products belong under `sure/models/<model_id>/`, not only under the run directory.
- Weights should converge to model-local `.runtime/` or `checkpoints/`.
- Host-global checkpoint fallback requires `fallback_to_host_global=true` and a non-empty reason.
- Terminal success requires `verdict.json` and a passing terminal gate.

`/sure_eval`:

- Evaluation is for already-onboarded models. If readiness says onboarding or repair is needed, run `/sure_onboard` first.
- `run_evaluation.sh` must be derived only from templates under `sure/skills/sure_eval/scripts/templates/`.
- When `which vc` and `vc info` both succeed, `vc_submit` is mandatory. Local bash or docker execution is prohibited unless vc is unavailable and the fallback is explicitly recorded.
- Non-`vc_submit` final reports require `fallback_approved` and `local_fallback_reason`.

## Contributor Checklist

Before opening a PR for a Sure skill:

- Add the package under `sure/skills/<skill-name>/`.
- Keep local experiments under `.sure/skills/<skill-name>/`.
- Include `sure.skill.json`, `SKILL.md`, hooks, scripts, schemas, and examples.
- Keep prompt and hook paths inside the skill package.
- Put domain checks in skill hooks/scripts, not in Harness.
- Declare required artifact paths in `sure.skill.json` when the path is fixed.
- Write all final manifest artifact entries with existing paths.
- Use `sure_update_state` for display state and hook `state_patch.checkpoint` for checkpoints.
- Run `npm run check`; run focused Sure tests when Harness or state-machine behavior changes.
