# SURE Harness

[English](./README.md) · [中文](./README_ZH.md) · [Demo](https://sure-eval.com/harness) · [User guide](./docs/harness_user_guide.md) · [License](./LICENSE)

> Turn audio-model evaluation into agent-readable, artifact-gated workflows.

SURE Harness is the control plane for Pi/Codex-style TUI agents that discover
audio models, prepare them, run bounded validation, submit real evaluation jobs,
and leave an auditable trail.

A human states intent through slash commands. The agent plans and executes. The
harness keeps every run reproducible, inspectable, and safe to review.

---

## Start here

| Need | Go to |
| --- | --- |
| See the product flow first | [sure-eval.com/harness](https://sure-eval.com/harness) |
| Run from a fresh clone | [From zero to first run](./docs/harness_user_guide.md#from-zero-to-first-run) |
| Understand each slash command | [Command reference](./docs/harness_user_guide.md#command-reference) |
| Prepare valid inputs | [Input preparation](./docs/harness_user_guide.md#input-preparation) |
| Check expected outputs | [Output contracts](./docs/harness_user_guide.md#output-contracts) |
| Verify a finished run | [Verification checklist](./docs/harness_user_guide.md#verification-checklist) |

## At a glance

```mermaid
flowchart LR
    Init["/sure_init<br/>Configure"] --> Feed["/sure_feed<br/>Discover"]
    Feed --> Onboard["/sure_onboard<br/>Prepare"]
    Onboard --> Eval["/sure_eval<br/>Evaluate"]
    Eval --> Reval["/sure_reval<br/>Re-evaluate"]
    Eval --> Artifacts["Reports &<br/>Audit Trail"]
    Reval --> Artifacts

    style Init fill:#f0f7ff,stroke:#3b82f6
    style Feed fill:#f0f7ff,stroke:#3b82f6
    style Onboard fill:#f0f7ff,stroke:#3b82f6
    style Eval fill:#f0fdf4,stroke:#22c55e
    style Reval fill:#f0fdf4,stroke:#22c55e
    style Artifacts fill:#faf5ff,stroke:#a855f7
```

## Product workflows

| Command | Stage | What it does | Key artifacts |
| --- | --- | --- | --- |
| `/sure_init` | Configure | One-time setup: agent/provider, auth location, skill discovery, backend checks. | Project config |
| `/sure_feed` | Discover | Find candidate models from ModelScope, HuggingFace, GitHub, or curated input; classify them into SURE task families. | `model_input.yaml`, `feed_report.json` |
| `/sure_onboard` | Prepare | Turn a model repo into a runnable local inference unit with wrapper, environment plan, fixture, package gate, and verdict. | `verdict.json`, wrapper files, model spec |
| `/sure_eval` | Evaluate | Evaluate an already-onboarded audio model through a deterministic SURE-EVAL route plan and a gated execution surface. | `main_agent_run_report.json`, route plan, metric reports |
| `/sure_reval` | Re-evaluate | Reuse completed predictions, skip inference, and rerun current `sure-evaluation` metric routes or exact `pipeline_id` selections. | `reval_run_report.json`, copied predictions, route plan, metric reports |

For the full input and output contract of each command, see the [user guide](./docs/harness_user_guide.md).

## Architecture

```mermaid
flowchart TB
    subgraph Agent["TUI Agent"]
        CMD[Slash commands]
        PLAN[Planner]
    end

    subgraph Harness["SURE Harness"]
        HOOK[Hooks & state machines]
        SCRIPT[Deterministic scripts]
        SCHEMA[Schemas & contracts]
    end

    subgraph Engine["sure-evaluation engine"]
        ROUTE[Route nodes]
        METRIC[Metrics]
    end

    subgraph Runtime["Execution surface"]
        LOCAL[Local]
        DOCKER[Docker]
        VC[VC cluster]
    end

    CMD --> PLAN --> HOOK --> SCRIPT --> Engine
    SCRIPT --> Runtime

    style Agent fill:#f8fafc
    style Harness fill:#f0f7ff
    style Engine fill:#fff7ed
    style Runtime fill:#f0fdf4
```

## Core capabilities

| Capability | Responsibility |
| --- | --- |
| **Task routing** | Map model and dataset metadata to ASR, TTS, VC, KWS, S2TT, diarization, speech understanding, and related task families. |
| **Model input synthesis** | Convert discovery evidence into `MODEL_INPUT` YAML for onboarding. |
| **Fixture preparation** | Prepare small task-specific smoke fixtures without treating smoke data as benchmark evidence. |
| **Runtime planning** | Select local, Docker, or VC execution surfaces and record the decision. |
| **Execution gating** | Run smoke tests, block invalid fallbacks, and require terminal artifacts before a run can finish. |
| **Evaluation routing** | Read the external `sure-evaluation` engine, discover supported metrics, select route nodes, and verify node-local environments. |
| **Prediction re-evaluation** | Import predictions from a completed run or results mirror and recompute metrics without rerunning model inference. |
| **VC submission** | Materialize a submit-ready entrypoint, submit with provenance, and record resource repairs such as queue memory limits. |
| **Artifact manifests** | Persist `run.json`, `events.jsonl`, final manifest, reports, metric payloads, and failure diagnostics. |

## Inputs And Outputs

| Stage | Primary input | Main output | Ready when |
| --- | --- | --- | --- |
| Discover | model URL, provider query, or curated source | `sure/handoffs/<model>/model_input.yaml` | task evidence, repo, weights source, fixture, and IO contract are present |
| Prepare | `model=<handoff>` or `model_input_path=...` | `sure/models/<model>/verdict.json` | import/load/infer/contract validation passes |
| Evaluate | onboarded model plus datasets and metrics | predictions, route plan, metric reports, `main_agent_run_report.json` | predictions validate and every metric has report artifacts |
| Re-evaluate | completed `results_dir`, `run_dir`, or `predictions/` | fresh tmp run with `reval_run_report.json` | `evaluation_only=true`, old metric artifacts are not reused, selected pipeline IDs match reports |

## Quick start

### 1. Clone and install

```bash
git clone --depth 1 --single-branch --branch harness-tui-agent https://github.com/PigeonDan1/sure.git sure-harness
cd sure-harness
npm install --ignore-scripts
npm run sure:doctor
```

If HTTPS cloning stalls in a restricted network, use SSH after adding a GitHub
SSH key:

```bash
git clone --depth 1 --single-branch --branch harness-tui-agent git@github.com:PigeonDan1/sure.git sure-harness
```

### 2. Launch the TUI

```bash
./pi-test.sh --provider openai --model <model-name> --thinking high --approve
```

### 3. Run the workflow

```text
/sure_init
/sure_feed source=modelscope query="english asr" max_models=20
/sure_onboard model=<model>
/sure_eval model=<model_name> datasets=<dataset_name> metrics=wer max_samples=5 execution=vc
```

`/sure_onboard model=<model>` reads `sure/handoffs/<model>/model_input.yaml` by
default. Use `model_input_path=...` only when the handoff lives somewhere else.

For a small ASR smoke path:

```text
/sure_feed https://huggingface.co/Qwen/Qwen3-ASR-0.6B-hf
/sure_onboard model=Qwen__Qwen3-ASR-0.6B-hf device=auto package=none
/sure_eval model=Qwen__Qwen3-ASR-0.6B-hf datasets=aishell1 metrics=cer max_samples=1 execution=local device=auto
```

For local development, use `execution=local`:

```text
/sure_eval model=<model_name> datasets=<dataset_name> metrics=wer max_samples=5 execution=local
```

To recompute metrics from an existing run without inference:

```text
/sure_reval source=<results_or_run_dir> datasets=<dataset_name> max_samples=5 pipeline_id=<exact_pipeline_id>
```

Repeat `pipeline_id=...` to compare multiple evaluation chains for the same
metric. `/sure_reval` writes a fresh run directory and does not reuse old metric
artifacts.

> **Note:** When `execution=vc` is requested, the run must produce real VC
> submission evidence. The harness does not silently fall back to local
> execution.

## Evaluation engine

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

`/sure_eval` and `/sure_reval` also need SURE benchmark JSONL files. Either link them into the
default harness location:

```bash
mkdir -p data/datasets/sure_benchmark
ln -s /path/to/sure_benchmark/jsonl data/datasets/sure_benchmark/jsonl
npm run sure:doctor
```

or point the runtime at a dataset root that contains `sure_benchmark/jsonl`:

```bash
export SURE_EVAL_DATASETS_ROOT=/path/to/data/datasets
```

Typical evaluation routes:

| Task | Route |
| --- | --- |
| ASR zh CER | `normalization/wetext_norm -> scoring/wenet_cer` |
| TTS/VC zh CER | `frontend/funasr_loader_16k_mono -> transcription/paraformer_zh -> normalization/punctuation_strip_norm -> scoring/wenet_cer` |
| TTS en WER | `transcription/whisper_large_v3 -> normalization/whisper_norm -> scoring/wenet_wer` |

## Repository hygiene

| Keep in repo | Keep out of repo |
| --- | --- |
| Harness code, skill packages, schemas, prompts | API keys, provider tokens, auth files |
| Small fixtures and tests | Model weights, checkpoints, large datasets |
| | Generated predictions, metric result dumps |
| | `.sure/` run directories |
| | Local external-engine checkouts |
| | Model-local virtual environments or cache directories |

Runtime outputs are expected under ignored paths such as `.sure/`,
`sure/models/`, `sure/handoffs/*/artifacts/`, and
`sure/skills/sure_eval/results/`.

## Troubleshooting

### `Cannot find module 'typebox'`

SURE commands are registered together. A missing dependency in `/sure_onboard`
can therefore appear when you try to run `/sure_feed`.

Run the commands from the repository root:

```bash
npm install --ignore-scripts
npm run sure:doctor
```

If `sure:doctor` reports missing sparse-checkout paths, add them explicitly:

```bash
git sparse-checkout add scripts fixtures packages/coding-agent/examples
npm install --ignore-scripts
npm run sure:doctor
```

Then start the TUI through the local repository entrypoint:

```bash
./pi-test.sh --provider openai --model <model-name> --thinking high --approve
```

### `rate_limit_exceeded: Concurrency limit exceeded`

The model provider rejected the agent request because the same account already
has too many active requests. This interrupts the TUI session; it does not mean
the SURE run artifacts or model wrapper are invalid.

Close other Pi/Codex/TUI sessions using the same provider account, wait for the
gateway to release the in-flight request, then re-run the same slash command.
For long `/sure_onboard` runs, inspect `.sure/runs/<run_id>/state.json` to see
the last completed unit before retrying.

## Skill package layout

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

## Development checks

Targeted checks while iterating:

```bash
npm run check:sure-hooks
python3 -m py_compile sure/skills/sure_eval/scripts/*.py
```

Full validation:

```bash
npm run check
```

SURE-focused Vitest entry points:

```bash
cd packages/coding-agent
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-extension.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-feed.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-onboard-state-machine.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-eval-state-machine.test.ts
node ../../node_modules/vitest/dist/cli.js --run test/suite/sure-eval-red-lines.test.ts
```

## Design boundary

| Harness owns | Skill packages own |
| --- | --- |
| Slash-command discovery, run lifecycle, state persistence | Domain prompts, deterministic scripts |
| Hook execution, tool gates, final manifest validation | State machines, schemas, checkpoints |
| | Validation rules, repair instructions |

Do not move task-specific metrics, dataset assumptions, or SURE business logic
into the common harness unless the rule is truly shared by every skill.

## License

[MIT](./LICENSE)
