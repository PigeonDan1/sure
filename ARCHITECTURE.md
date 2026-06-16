# SURE-EVAL Architecture

SURE-EVAL is organized around two agent workflows and a deterministic evaluation
core. The current repository layout keeps agent harness documents under
`docs/agents/`, shared smoke fixtures under `fixtures/tasks/`, and task metric
implementations under `src/sure_eval/evaluation/<task>/`.

## Current Entry Points

| Purpose | Entry point |
|---------|-------------|
| Repository-level routing | `AGENTS.md` |
| Evaluate an already onboarded model | `docs/agents/main_flow_agent/README.md` |
| Onboard or repair a model tool | `docs/agents/model_tool_agent/README.md` |
| Main-flow harness rules | `docs/agents/main_flow_agent/AGENTS.md` |
| Model-tool harness rules | `docs/agents/model_tool_agent/AGENTS.md` |
| Shared task fixtures | `fixtures/tasks/README.md` |
| Task metrics | `src/sure_eval/evaluation/<task>/README.md` |

Historical root documents and one-off result snapshots live under
`docs/archive/`.

## High-Level System

```text
User request
  |
  +-- main_flow_agent
  |     - classify evaluation goal
  |     - check model/tool readiness
  |     - choose datasets and deterministic scripts
  |     - generate and validate run_evaluation.sh
  |     - run bounded smoke before full execution
  |     - evaluate predictions and write run reports
  |
  +-- model_tool_agent
        - discover model/runtime evidence
        - route task, environment, fixture, and memory context
        - validate spec before build
        - build environment and fetch model-local weights
        - validate import/load/infer/contract
        - generate wrapper/server/config/validate artifacts
        - optionally build and validate Docker image
```

The two workflows are connected by readiness. Main flow owns benchmark
orchestration; model tool-agent owns model integration and repair.

## Main Flow Agent

Use main flow when `src/sure_eval/models/<model>/` already exists and the user
wants to evaluate, rerun, audit, or triage results.

Canonical state machine:

```text
INTAKE
→ TASK_CLASSIFICATION_UNIT
→ TOOL_READINESS_AND_ROUTING_UNIT
→ PLAN_UNIT
→ DATASET_SCOPE_UNIT
→ SCRIPT_ROUTING_UNIT
→ EXECUTION_SURFACE_UNIT
→ EXECUTION_READINESS_UNIT
→ SMOKE_TEST_UNIT
→ EXECUTE / WAIT
→ ASSESSMENT_UNIT
→ RUN_REPORT_UNIT
```

Important contracts:

- `docs/agents/main_flow_agent/contracts/main_flow_architecture.md`
- `docs/agents/main_flow_agent/contracts/main_agent_spec.md`
- `docs/agents/main_flow_agent/contracts/eval_run_layout.md`
- `docs/agents/main_flow_agent/contracts/prediction_generation_contract.md`

Main flow must use deterministic scripts under `scripts/` and templates under:

```text
docs/agents/main_flow_agent/templates/
```

It must not reuse prior `eval_runs/` execution surfaces as the source for a new
run.

## Model Tool Agent

Use model tool-agent when a model has no usable local tool/server, when wrapper
or environment validation has not passed, or when main flow marks the target as
not ready.

Canonical state machine:

```text
DISCOVER
→ CLASSIFY
→ PLAN
→ VALIDATE_SPEC
→ BUILD_ENV
→ FETCH_WEIGHTS
→ VALIDATE_ENV_COMPAT
→ VALIDATE_IMPORT
→ VALIDATE_LOAD
→ VALIDATE_INFER
→ VALIDATE_CONTRACT
→ GENERATE_WRAPPER
→ SAVE_ARTIFACTS
```

Docker build/validate is a post-local-validation phase for models that need
containerized execution.

The context-selection layer does not replace this state machine. It only
controls which documents are read:

```text
task_playbooks/ROUTING.md
playbooks/env_ROUTING.md
memory/ROUTING.md
fixtures/tasks/<task>/README.md
src/sure_eval/evaluation/<task>/README.md
```

## Fixtures

Shared smoke fixtures are task-scoped:

```text
fixtures/tasks/asr/
fixtures/tasks/s2tt/
fixtures/tasks/ser/
fixtures/tasks/slu/
fixtures/tasks/gr/
fixtures/tasks/tts/
fixtures/tasks/vc/
fixtures/tasks/kws/
```

`fixtures/tasks/speech_understanding/` is a composite index for multi-task
speech understanding models. It points to atomic ASR/S2TT/SER/SLU/GR fixture
indexes instead of owning a separate shared fixture package.

Model-local fixture copies still live under:

```text
src/sure_eval/models/<model>/fixture/
```

## Evaluation Metrics

Task metric implementations and metric-local environment instructions live
under:

```text
src/sure_eval/evaluation/asr/
src/sure_eval/evaluation/classification/
src/sure_eval/evaluation/s2tt/
src/sure_eval/evaluation/tts/
src/sure_eval/evaluation/vc/
src/sure_eval/evaluation/kws/
```

ASR exposes `CERMetric` and `WERMetric` class names for registry consistency,
but both call the formal SURE ASR scoring path through `SUREEvaluator` and
`evaluation/asr/wenet_compute_cer.py`.

Each metric directory owns its `pyproject.toml` and `README.md` so dependencies
can be prepared per metric with `uv`.

## Repository Layout

```text
sure-eval/
├── AGENTS.md
├── README.md
├── ARCHITECTURE.md
├── config/
├── docs/
│   ├── agents/
│   │   ├── main_flow_agent/
│   │   └── model_tool_agent/
│   └── archive/
├── fixtures/tasks/
├── scripts/
├── src/sure_eval/
│   ├── agent/
│   ├── datasets/
│   ├── evaluation/
│   ├── models/
│   └── reports/
└── tests/
```

## Runtime Outputs

Runtime outputs are intentionally not part of the source layout:

- virtual environments: `.venv/`, `src/sure_eval/models/*/.venv/`
- model cache/weights: `.runtime/`, `checkpoints/`, provider caches
- evaluation outputs: `eval_runs/`, `docker_artifacts/`
- local data and reports: `data/`, `results/`, `reports/xforge/`

These are covered by `.gitignore` for new files. Already tracked historical run
artifacts require separate `git rm --cached` cleanup if the project decides to
remove them from version control.
