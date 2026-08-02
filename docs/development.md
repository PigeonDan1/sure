# Development Guide

This page is for maintainers extending SURE Harness or adding new skills.

## Skill Package Layout

```text
sure/skills/<skill-name>/
  sure.skill.json   # skill manifest
  SKILL.md          # agent-facing operating manual
  hooks/            # state-machine gates
  scripts/          # deterministic execution
  schemas/          # artifact contracts
  references/       # domain references
  examples/         # usage examples
```

## Targeted Checks

Run focused checks while iterating:

```bash
npm run check:sure-hooks
python3 -m py_compile sure/skills/sure_eval/scripts/*.py
python3 -m unittest sure/skills/sure_onboard/scripts/test_runtime_inventory.py
python3 -m unittest sure/skills/sure_eval/scripts/test_protocol_provenance.py
```

Run the doctor after changes that affect setup, skill discovery, or external
engine detection:

```bash
npm run sure:doctor
```

Full validation:

```bash
npm run check
```

`npm run check` is intentionally non-mutating. Use `npm run format` when Biome
should rewrite files, and keep `git diff --check` clean before pushing.

Credential-free test launchers (`test.sh`, `pi-test.sh --no-env`, and
`pi-test.ps1 --no-env`) read their variable names from one shared file:

```text
scripts/credential-env.txt
```

Add new credential variable names there, sorted alphabetically. Do not place
secret values in scripts or docs. The no-env launchers temporarily move
`auth.json` out of the agent config directory during `--no-env` runs and
restore it on exit.

## SURE-Focused Tests

```bash
cd packages/coding-agent
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-extension.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-feed.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-onboard-state-machine.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-eval-state-machine.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-eval-runbackend.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-eval-red-lines.test.ts
```

## Runtime Provenance Lifecycle

| Stage | Artifact | Rule |
| --- | --- | --- |
| `/sure_onboard` | `runtime_inventory.json` | Summarize model-level backend, Python, runtime probe, weights manifest, and small evidence links. Do not link checkpoint payloads. |
| `/sure_eval` | `prediction_generation_status.json` | Record the actual MCP server command, working directory, safe env snapshot, explicit tool args, protocol resolver output, and dataset generation status. |
| `/sure_eval` | `protocol.yaml` | Read generation status first, runtime inventory second, model config third, environment fallback last. Keep inference fields separate from evaluation results. |
| `/sure_reval` | `prediction_reuse_manifest.json` | Copy/filter predictions only; do not reuse old metric artifacts. |
| `/sure_reval` | `source_inference_provenance.json` | Link source protocol/status/runtime inventory when available and mark unknown sources explicitly. |

## Design Boundary

| Harness owns | Skill packages own |
| --- | --- |
| Slash-command discovery, run lifecycle, state persistence. | Domain prompts, deterministic scripts. |
| Hook execution, tool gates, final manifest validation. | State machines, schemas, checkpoints. |
| Shared runtime contracts. | Validation rules and repair instructions. |

Do not move task-specific metrics, dataset assumptions, or SURE business logic
into the common harness unless the rule is truly shared by every skill.

## Repository Hygiene

Keep generated files out of Git:

```text
.sure/
sure/models/
sure/handoffs/*/artifacts/
sure/skills/sure_eval/results/
```

Do not commit API keys, provider tokens, auth files, model weights,
checkpoints, large datasets, prediction dumps, metric result dumps, virtual
environments, or cache directories.

`sure/external/sure-evaluation` is intentionally tracked as a Git submodule.
Commit only the gitlink pointer when bumping the verified engine version.
