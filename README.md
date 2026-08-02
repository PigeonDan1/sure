# SURE Harness

<p align="center">
  <a href="https://sure-eval.com/harness"><img alt="Demo" src="https://img.shields.io/badge/Demo-sure--eval.com%2Fharness-2563eb?style=for-the-badge"></a>
  <a href="./docs/harness_user_guide.md"><img alt="User guide" src="https://img.shields.io/badge/Docs-User_Guide-16a34a?style=for-the-badge"></a>
  <a href="./docs/evaluation_engine.md"><img alt="Evaluation engine" src="https://img.shields.io/badge/Engine-sure--evaluation-f97316?style=for-the-badge"></a>
  <a href="./README_ZH.md"><img alt="Chinese README" src="https://img.shields.io/badge/Language-%E4%B8%AD%E6%96%87-7c3aed?style=for-the-badge"></a>
</p>

> TUI-agent control plane for speech and audio model evaluation.

SURE Harness helps Pi/Codex-style TUI agents evaluate audio models from model
discovery to audited reports. It turns onboarding, VC/local execution, metric
routing, and prediction re-evaluation into explicit slash-command workflows.

## Why SURE Harness

| User need | Product answer |
| --- | --- |
| Agents need a route through a complex evaluation repo. | Slash commands expose bounded, artifact-gated workflows. |
| Evaluation results must be reproducible and reviewable. | Every run writes reports, manifests, route plans, and validation payloads. |
| Metric routes evolve outside the harness. | The harness reads the `sure-evaluation` submodule at runtime. |
| Existing predictions should be reusable. | `/sure_reval` recomputes metrics without rerunning model inference. |

## Workflow

```mermaid
flowchart LR
    Feed["Discover<br/>/sure_feed"] --> Onboard["Prepare<br/>/sure_onboard"]
    Onboard --> Eval["Evaluate<br/>/sure_eval"]
    Eval --> Reval["Re-evaluate<br/>/sure_reval"]
    Eval --> Review["Review<br/>reports"]
    Reval --> Review

    classDef input fill:#eff6ff,stroke:#2563eb,color:#172554
    classDef run fill:#f0fdf4,stroke:#16a34a,color:#14532d
    classDef report fill:#f8fafc,stroke:#64748b,color:#0f172a
    class Feed,Onboard input
    class Eval,Reval run
    class Review report
```

## What You Can Do

| Block | Command | Prepare | Expect |
| --- | --- | --- | --- |
| Model discovery | `/sure_feed` | Model URL, provider query, or curated source. | `model_input.yaml` and discovery report. |
| Model preparation | `/sure_onboard` | Feed handoff or explicit `model_input_path`. | Runnable wrapper, model spec, fixture, and `verdict.json`. |
| Full evaluation | `/sure_eval` | Onboarded model, dataset, metric, execution target. | Predictions, validation payload, route plan, metric reports. |
| Prediction re-evaluation | `/sure_reval` | Existing `results_dir`, run dir, or `predictions/`. | Fresh evaluation-only run and `reval_run_report.json`. |
| Route-backed metrics | `sure-evaluation` | Engine submodule plus benchmark JSONL files. | Current metric capabilities, exact `pipeline_id` execution. |

## Quick Start

```bash
git clone --recurse-submodules --depth 1 --single-branch --branch harness-tui-agent https://github.com/PigeonDan1/sure.git sure-harness
cd sure-harness
npm install --ignore-scripts
```

If the repository was cloned without submodules:

```bash
git submodule update --init --recursive
```

Prepare benchmark data:

```bash
mkdir -p data/datasets/sure_benchmark
ln -s /path/to/sure_benchmark/jsonl data/datasets/sure_benchmark/jsonl
npm run sure:doctor
```

Start the TUI:

```bash
./pi-test.sh --provider openai --model <model-name> --thinking high --approve
```

Run the main path:

```text
/sure_init
/sure_feed source=modelscope query="english asr" max_models=20
/sure_onboard model=<model>
/sure_eval model=<model_name> datasets=<dataset_name> metrics=wer max_samples=5 execution=vc
```

Recompute metrics from existing predictions:

```text
/sure_reval source=<results_or_run_dir> datasets=aishell1 max_samples=5 pipeline_id=<exact_pipeline_id>
```

Dataset short names such as `aishell1` are accepted when exactly one versioned
prediction file matches, for example `aishell1__v1.0.2__asr.txt`.

Use `execution=local` for local development. Use `execution=vc` only when the
run should produce real VC submission evidence.

## Inputs And Outputs

| Stage | Primary input | Main output | Ready signal |
| --- | --- | --- | --- |
| Discover | Model URL or query. | `sure/handoffs/<model>/model_input.yaml` | Task evidence and IO contract are present. |
| Prepare | `model=<handoff>` or `model_input_path=...` | `sure/models/<model>/verdict.json` | Import/load/infer/contract checks pass. |
| Evaluate | Model, datasets, metrics, execution target. | `main_agent_run_report.json` plus metric artifacts. | Predictions validate and route reports exist. |
| Re-evaluate | Previous results or predictions. | `reval_run_report.json` in a fresh tmp run. | `evaluation_only=true`; old metric artifacts are not reused. |

## Standard Artifacts

| Artifact | Produced by | What it proves |
| --- | --- | --- |
| `runtime_inventory.json` | `/sure_onboard` | Model-level runtime, Python/backend, weights manifest, and evidence links. |
| `prediction_generation_status.json` | `/sure_eval` | Actual inference server command, environment snapshot, explicit tool args, protocol resolution, and dataset generation status. |
| `protocol.yaml` | `/sure_eval` and `/sure_reval` | Inference protocol only: model, runtime, parameters, prediction reuse, and provenance. |
| `report_snapshot.md` | `/sure_eval` and `/sure_reval` | Human-readable evaluation scope, route, metric, and result snapshot. |
| `report.jsonl` | `/sure_eval` and `/sure_reval` | Machine-readable per dataset-metric results. |
| `source_inference_provenance.json` | `/sure_reval` | Source protocol/status/runtime links when predictions are reused. |

## Operational Guardrails

| Area | Behavior |
| --- | --- |
| Re-evaluation dataset names | `/sure_reval` accepts the same short dataset aliases as `/sure_eval` when the match is unique. |
| Agent repair loop | Hook diagnostics report accumulated type mismatches, retry-derived `gate_blocks`, and the real blocking reason. |
| Local verification | `npm run check` is non-mutating; use `npm run format` when you want Biome to rewrite files. |
| Credential-safe tests | `test.sh`, `pi-test.sh --no-env`, and `pi-test.ps1 --no-env` read `scripts/credential-env.txt` and hide `auth.json` temporarily. Add variable names there, never secret values. |

## Docs

| Need | Read |
| --- | --- |
| From zero to first run, command fields, output contracts. | [User guide](./docs/harness_user_guide.md) |
| Metric engine setup, datasets, route and `pipeline_id` selection. | [Evaluation engine](./docs/evaluation_engine.md) |
| Common setup, provider, dataset, and VC failures. | [Troubleshooting](./docs/troubleshooting.md) |
| Skill package layout, development checks, design boundaries. | [Development guide](./docs/development.md) |
| Chinese documentation. | [README_ZH](./README_ZH.md), [用户指南](./docs/harness_user_guide_zh.md) |
| Product demo. | [sure-eval.com/harness](https://sure-eval.com/harness) |

## Repository Hygiene

| Keep in repo | Keep out of repo |
| --- | --- |
| Harness code, skill packages, schemas, prompts. | API keys, provider tokens, auth files. |
| Small fixtures and tests. | Model weights, checkpoints, large datasets. |
| Documentation and examples. | Predictions, metric result dumps, `.sure/` runs, virtual environments. |

Expected local output paths such as `.sure/`, `sure/models/`,
`sure/handoffs/*/artifacts/`, and `sure/skills/sure_eval/results/` are ignored.
`sure/external/sure-evaluation` is tracked as a submodule gitlink, so the parent
repository records the verified engine commit without committing engine files.

## License

[MIT](./LICENSE)
