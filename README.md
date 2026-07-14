# Sure

Sure is a research-agent harness built on the Pi coding agent framework. It keeps the base agent useful as a normal coding assistant, while adding Sure slash-command skills such as `/paper_collect`, `/literature_survey`, `/knowledge_graph`, `/research_idea`, `/novelty_check`, `/run_experiment`, `/data_workflow`, `/model_eval`, `/model_mcp`, `/science_gateway`, `/research_discussion`, `/scholar_profile`, `/sure_eval`, and `/sure_onboard`.

The important design rule is simple: the Harness owns lifecycle, state persistence, tool gates, and artifact envelopes; each skill package owns domain-specific prompts, scripts, stages, metrics, validation rules, and repair logic.

## Development Setup

```bash
npm install --ignore-scripts
npm run check
./pi-test.sh
```

Use `./pi-test.sh` from the repository root to start the local TUI from source. Once a skill package exists, run it in the TUI with its slash command, for example:

```text
/paper_collect graph neural networks after 2022, target 50 papers
```

Do not run `npm run build` or the full test suite unless you specifically need them. For Sure changes, the focused test is:

```bash
cd packages/coding-agent
node node_modules/vitest/vitest.mjs --run test/suite/sure-extension.test.ts
```

## Where Skills Live

Repository-managed skills belong under:

```text
sure/skills/<skill-name>/
```

Project-local or experimental overrides belong under:

```text
.sure/skills/<skill-name>/
```

Runtime discovery checks both locations. Project-local `.sure/skills` packages override repository `sure/skills` packages for the same command. This lets contributors test private versions without changing the shared skill package.

Each skill package should use this shape:

```text
sure/skills/paper_collect/
  sure.skill.json
  SKILL.md
  hooks/
    index.ts
  scripts/
    collect.py
    normalize.py
  schemas/
    paper_collection.schema.json
  examples/
    minimal-input.json
    manifest.example.json
  README.md
```

Only `sure.skill.json` and the prompt file are required by discovery. Hooks, scripts, schemas, examples, and README are expected for reviewable production skills.

## `sure.skill.json`

The manifest connects a package to a Sure slash command.

```json
{
  "name": "paper_collect",
  "command": "/paper_collect",
  "description": "Collect papers and produce a standard paper collection manifest.",
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
      "type": "paper_collection",
      "path": "artifacts/papers.manifest.json",
      "required": true,
      "description": "Final paper collection manifest"
    }
  ],
  "ui": {
    "primaryCounters": ["collected_papers", "target_papers"],
    "artifactTypes": ["paper_collection"],
    "defaultExpandedSections": ["diagnostics", "artifacts"]
  }
}
```

Rules:

- `command` must be one of the registered Sure commands.
- `prompt` and hook module paths must stay inside the skill package.
- `artifacts` only declares common required files. Deep validation belongs in `pre_finish`.
- `ui` is a display hint only. It must not encode Harness logic.

## Prompt File

The prompt file is the skill's operating manual for the coding agent. Keep it specific and executable. It should tell the agent:

- what the skill does and what it must not do
- how to interpret user arguments
- which scripts to call and in what order
- what files to write under the run directory
- when to call `sure_update_state`
- how to produce the final manifest
- when to mark the run `success`, `incomplete`, or `failed`

Example prompt outline:

```md
# /paper_collect

Collect a deduplicated paper set for the user's topic.

Inputs:
- User arguments from the Sure invocation.
- Optional seed papers, DOI, arXiv IDs, or prior manifests.

Workflow:
1. Parse the topic and target count.
2. Run `scripts/collect.py`.
3. Run `scripts/normalize.py`.
4. Write artifacts under the run directory.
5. Call `sure_update_state` after each major phase.
6. Call `sure_finish` only after writing the final manifest.

Success requires:
- At least the target paper count, unless the manifest status is `incomplete`.
- A valid paper collection manifest.
- Deduplication evidence and failure logs.
```

## Scripts

Scripts contain deterministic or semi-deterministic work that should not live in the prompt. Put API calls, parsing, downloading, normalization, scoring, and export logic here.

Recommended script behavior:

- accept explicit input/output paths
- write durable files under the provided run directory
- be idempotent when possible
- exit non-zero on unrecoverable failure
- write machine-readable intermediate files for hooks to inspect
- never rely on hidden cwd assumptions

Example:

```bash
python sure/skills/paper_collect/scripts/collect.py \
  --query "graph neural networks after 2022" \
  --target 50 \
  --out .sure/runs/<runId>/artifacts/raw_candidates.jsonl
```

## Hooks And Gates

Hooks are TypeScript or JavaScript modules loaded from the skill package. They are the place for skill-specific gates. Harness executes them at fixed points and treats `ok: false` or `repair` as a blocking result.

Supported hook points:

- `pre_start`: check inputs, environment, credentials, writable output dirs
- `pre_tool_call`: block unsafe or off-protocol tool calls
- `post_tool_result`: inspect tool failures and suggest repair
- `pre_finish`: validate final artifacts before accepting `sure_finish`
- `post_finish`: register or summarize accepted results
- `on_error`: persist failure diagnostics

Example:

```ts
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { SureHookContext, SureHookResult } from "@earendil-works/pi-coding-agent";

export function preFinish(ctx: SureHookContext): SureHookResult {
  const manifestPath = join(ctx.runDir, "artifacts", "papers.manifest.json");
  if (!existsSync(manifestPath)) {
    return {
      ok: false,
      repair: "Write artifacts/papers.manifest.json before calling sure_finish.",
      state_patch: {
        phase: { id: "validate", label: "Validating artifacts", status: "blocked" },
        diagnostics: [{ severity: "error", message: "Missing paper collection manifest." }]
      }
    };
  }

  const manifest = JSON.parse(readFileSync(manifestPath, "utf-8"));
  const collected = Array.isArray(manifest.papers) ? manifest.papers.length : 0;
  if (collected < 50) {
    return {
      ok: false,
      repair: "Collect more papers or finish with status incomplete.",
      state_patch: {
        phase: { id: "validate", label: "Validating paper count", status: "blocked" },
        counters: { collected_papers: collected, target_papers: 50 }
      }
    };
  }

  return { ok: true };
}
```

Keep hooks small. Heavy work belongs in scripts; hooks should validate, gate, summarize, and return repair instructions.

## Runtime State

During an active Sure run, the skill can update display state with the `sure_update_state` tool or by returning `state_patch` from hooks.

```json
{
  "phase": { "id": "download", "label": "Downloading PDFs", "status": "running", "progress": 0.4 },
  "message": "Downloaded 20 of 50 target PDFs.",
  "counters": { "downloaded_pdfs": 20, "target_papers": 50 },
  "diagnostics": [
    {
      "severity": "warning",
      "message": "Some DOI downloads failed.",
      "repair": "Retry with arXiv fallback."
    }
  ],
  "artifacts": [
    {
      "type": "paper_collection",
      "name": "Raw candidates",
      "path": ".sure/runs/<runId>/artifacts/raw_candidates.jsonl",
      "status": "draft"
    }
  ],
  "checkpoint": {
    "id": "after_download",
    "label": "After PDF download",
    "resumable": true,
    "resume_hint": "Reuse the download cache and continue normalization."
  },
  "next_actions": ["Run normalization", "Retry failed PDF downloads"]
}
```

Harness validates only the common shape. It does not know that `download`, `dedup`, or `evaluate` are phases. Those names are owned by the skill package.

State is persisted in:

```text
.sure/runs/<runId>/state.json
.sure/runs/<runId>/events.jsonl
```

## Final Manifest

Every run must finish through `sure_finish`. The final manifest must be JSON and include this common envelope:

```json
{
  "schema_version": "1",
  "run_id": "<runId>",
  "skill_name": "paper_collect",
  "status": "success",
  "created_at": "2026-07-05T00:00:00.000Z",
  "inputs": {},
  "outputs": {},
  "validation": {},
  "artifacts": [
    {
      "type": "paper_collection",
      "path": ".sure/runs/<runId>/artifacts/papers.manifest.json"
    }
  ]
}
```

Harness checks the envelope and required artifact paths. The skill's `pre_finish` hook must check task-specific requirements such as paper count, graph node count, benchmark completeness, citation traceability, or experiment metrics.

## Contributor Checklist

Before opening a PR for a skill:

- Add the package under `sure/skills/<skill-name>/`.
- Include a runnable prompt, scripts, hooks, examples, and README.
- Keep all hook paths inside the skill package.
- Report status with `sure_update_state` or hook `state_patch`.
- Write a final manifest with the common envelope.
- Gate incomplete or invalid outputs in `pre_finish`.
- Run `npm run check`.
- Run `node node_modules/vitest/vitest.mjs --run test/suite/sure-extension.test.ts` from `packages/coding-agent` if Harness behavior changed.

## Notes For Maintainers

The original Pi packages are still present under `packages/`. Sure-specific Harness code currently lives under `packages/coding-agent/src/core/sure/`, and repository skills live under `sure/skills/`.

Do not move domain-specific validation into Harness. If a rule only makes sense for one skill, put it in that skill's hook or script.
