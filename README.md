# SURE Harness

[中文说明](./README_ZH.md)

SURE Harness turns audio-model evaluation into agent-readable, artifact-gated
workflows. It is built for Pi/Codex-style TUI agents that need to discover a
model, prepare it, run bounded validation, submit real evaluation jobs, and
leave an auditable trail.

The product goal is simple: a human states intent through slash commands; the
agent plans and executes; the harness keeps the run reproducible, inspectable,
and safe to review.

## What It Does

SURE provides three product workflows:

| Command | Product job | Main output |
| --- | --- | --- |
| `/sure_feed` | Find candidate models from ModelScope, HuggingFace, GitHub, or curated input; classify them into SURE task families; create onboarding input. | `model_input.yaml`, `feed_report.json` |
| `/sure_onboard` | Turn a model repo into a runnable local inference unit with wrapper, environment plan, fixture, package gate, and verdict. | `verdict.json`, wrapper files, model spec |
| `/sure_eval` | Evaluate an already-onboarded audio model through a deterministic SURE-EVAL route plan and a gated execution surface. | `main_agent_run_report.json`, route plan, metric reports |

`/sure_init` configures the project once: agent/provider selection, auth
location, skill discovery, and local Python/backend checks.

## Atomic Capabilities

SURE is made of small, inspectable capabilities that compose into the product
workflows:

- **Task routing**: map model and dataset metadata to ASR, TTS, VC, KWS, S2TT,
  diarization, speech understanding, and related task families.
- **Model input synthesis**: convert discovery evidence into `MODEL_INPUT`
  YAML for onboarding.
- **Fixture preparation**: prepare small task-specific smoke fixtures without
  treating smoke data as benchmark evidence.
- **Runtime planning**: select local, Docker, or VC execution surfaces and
  record the decision.
- **Execution gating**: run smoke tests, block invalid fallbacks, and require
  terminal artifacts before a run can finish.
- **Evaluation routing**: read the external `sure-evaluation` engine, discover
  supported metrics, select route nodes, and verify node-local environments.
- **VC submission**: materialize a submit-ready entrypoint, submit with
  provenance, and record resource repairs such as queue memory limits.
- **Artifact manifests**: persist `run.json`, `events.jsonl`, final manifest,
  reports, metric payloads, and failure diagnostics.

These are deliberately atomic: scripts do deterministic work, hooks enforce
contracts, and the agent makes scoped decisions inside those contracts.

## Quick Start

From the repository root:

```bash
npm install --ignore-scripts
./pi-test.sh
```

Inside the TUI:

```text
/sure_init
/sure_feed source=modelscope query="english asr" max_models=20
/sure_onboard model_input=sure/handoffs/<model>/model_input.yaml
/sure_eval model=<model_name> datasets=<dataset_name> metrics=wer max_samples=5 execution=vc
```

For local development without VC:

```text
/sure_eval model=<model_name> datasets=<dataset_name> metrics=wer max_samples=5 execution=local
```

When VC is requested, the run must produce real VC submission evidence. The
harness does not silently fall back to local execution.

## Evaluation Engine

SURE Harness reads metric capabilities and route nodes from the standalone
`sure-evaluation` engine. Keep that checkout local and out of this repository's
Git history:

```bash
mkdir -p sure/external
git clone https://github.com/PigeonDan1/sure-evaluation.git sure/external/sure-evaluation
```

You can also point at a local engine explicitly:

```bash
export SURE_EVALUATION_HOME=/path/to/sure-evaluation
```

The harness expects evaluation routes such as:

- ASR zh CER: `normalization/wetext_norm -> scoring/wenet_cer`
- TTS/VC zh CER: `frontend/funasr_loader_16k_mono -> transcription/paraformer_zh -> normalization/punctuation_strip_norm -> scoring/wenet_cer`
- TTS en WER: `transcription/whisper_large_v3 -> normalization/whisper_norm -> scoring/wenet_wer`

## Repository Hygiene

This repository should contain harness code, skill packages, schemas, prompts,
small fixtures, and tests. It should not contain:

- API keys, provider tokens, or auth files.
- Model weights, checkpoints, large datasets, generated predictions, or metric
  result dumps.
- `.sure/` run directories.
- Local external-engine checkouts.
- Model-local virtual environments or cache directories.

Runtime outputs are expected under ignored paths such as `.sure/`,
`sure/models/`, `sure/handoffs/*/artifacts/`, and
`sure/skills/sure_eval/results/`.

## Skill Package Layout

Repository skills live under:

```text
sure/skills/<skill-name>/
```

Expected shape:

```text
sure/skills/<skill-name>/
  sure.skill.json
  SKILL.md
  hooks/
  scripts/
  schemas/
  references/
  examples/
```

`SKILL.md` is the agent-facing operating manual. Hooks own state-machine
gates. Scripts own deterministic execution. Schemas define the artifact
contracts.

## Development Checks

Use targeted checks while iterating:

```bash
npm run check:sure-hooks
python3 -m py_compile sure/skills/sure_eval/scripts/*.py
```

For broader validation:

```bash
npm run check
```

Useful SURE-focused Vitest entry points:

```bash
cd packages/coding-agent
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-extension.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-feed.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-onboard-state-machine.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-eval-state-machine.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-eval-red-lines.test.ts
```

## Design Boundary

Harness owns slash-command discovery, run lifecycle, state persistence, hook
execution, tool gates, and final manifest validation.

Skill packages own domain prompts, deterministic scripts, state machines,
schemas, checkpoints, validation rules, and repair instructions.

Do not move task-specific metrics, dataset assumptions, or SURE business logic
into the common harness unless the rule is truly shared by every skill.
