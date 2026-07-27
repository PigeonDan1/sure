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

## SURE-Focused Tests

```bash
cd packages/coding-agent
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-extension.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-feed.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-onboard-state-machine.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-eval-state-machine.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-eval-red-lines.test.ts
```

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
