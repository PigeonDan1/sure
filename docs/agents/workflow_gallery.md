# Workflow Gallery

This gallery shows how the two SURE-EVAL agent workflows fit together. It is
for users who want the full picture before opening the detailed harness files.

## Which Workflow

```mermaid
flowchart LR
  U[User request] --> Q{What do you need?}
  Q -- Evaluate an existing model --> M[main_flow_agent]
  Q -- Onboard or repair a model --> T[model_tool_agent]
  Q -- Unsure --> R[main_flow_agent readiness gate]

  R --> RQ{Tool ready?}
  RQ -- yes --> M
  RQ -- no / broken --> T

  T --> TR[Callable model tool<br/>model.py / server.py / validate.py]
  TR --> M
  M --> O[Evaluation run<br/>scores / reports / manifest]
```

Use:

- `main_flow_agent` when the model already has a usable SURE model directory.
- `model_tool_agent` when model integration, dependency repair, wrapper work, or
  Docker validation is still needed.

## Main Flow Agent State Machine

```mermaid
flowchart TD
  A[INTAKE] --> B[TASK_CLASSIFICATION_UNIT]
  B --> C[TOOL_READINESS_AND_ROUTING_UNIT]
  C --> D{Tool ready?}
  D -- no --> Handoff[Handoff to model_tool_agent]
  Handoff --> C
  D -- yes --> E[PLAN_UNIT]
  E --> F[DATASET_SCOPE_UNIT]
  F --> G[SCRIPT_ROUTING_UNIT]
  G --> H[EXECUTION_SURFACE_UNIT]
  H --> I[EXECUTION_READINESS_UNIT]
  I --> J{Ready?}
  J -- no --> AssessBlocked[ASSESSMENT_UNIT<br/>blocked / repair / ask user]
  J -- yes --> K[SMOKE_TEST_UNIT]
  K --> L{Smoke pass?}
  L -- no --> AssessFailed[ASSESSMENT_UNIT<br/>failure analysis]
  AssessFailed --> Handoff
  L -- yes --> M[EXECUTE / WAIT]
  M --> N[ASSESSMENT_UNIT]
  N --> O[RUN_REPORT_UNIT]
```

Main flow produces a model-local run directory:

```text
src/sure_eval/models/<model>/eval_runs/<run_id>/
```

## Model Tool Agent State Machine

```mermaid
flowchart TD
  A[DISCOVER] --> CS[CONTEXT_SELECTION_UNIT]
  CS --> B[CLASSIFY]
  B --> C[PLAN]
  C --> D[VALIDATE_SPEC]
  D --> E{Spec valid?}
  E -- no --> FixSpec[Fix spec / record spec_validation.json]
  FixSpec --> D
  E -- yes --> F[BUILD_ENV]
  F --> G[FETCH_WEIGHTS]
  G --> H[VALIDATE_ENV_COMPAT]
  H --> I{Compatible?}
  I -- no --> Diag[DIAGNOSE / REPLAN]
  Diag --> F
  I -- yes --> J[VALIDATE_IMPORT]
  J --> K[VALIDATE_LOAD]
  K --> L[VALIDATE_INFER]
  L --> M[VALIDATE_CONTRACT]
  M --> N{Contract pass?}
  N -- no --> Diag
  N -- yes --> O[GENERATE_WRAPPER]
  O --> P[SAVE_ARTIFACTS]
  P --> Q{Docker needed?}
  Q -- no --> Done[Tool ready]
  Q -- yes --> R[Docker build / validate]
  R --> Done
```

Model tool-agent produces:

```text
src/sure_eval/models/<model>/
├── model.spec.yaml
├── model.py
├── server.py
├── config.yaml
├── validate.py
├── fixture/
└── artifacts/
```

## Context Selection Layer

The state machine stays stable. The context selection layer only decides which
documents are read for the current task, backend, fixture, metric, and failure.

```mermaid
flowchart TD
  I[MODEL_INPUT] --> T[task_playbooks/ROUTING.md]
  I --> E[playbooks/env_ROUTING.md]
  I --> M[memory/ROUTING.md]

  T --> TP[Selected task playbook<br/>ASR / S2TT / SER / SLU / GR / TTS / VC / KWS]
  TP --> F["fixtures/tasks/{task}/README.md"]
  TP --> EM["src/sure_eval/evaluation/{task}/README.md"]

  E --> EP[Selected environment playbook<br/>uv / pip / conda / pixi / docker / api]

  M --> R{Concrete failure trigger?}
  R -- yes --> BC["memory/bad_cases/<case>.md"]
  R -- no --> Skip[Skip bad-case memory]
```

## Worked Paths

### Existing ASR Model

```mermaid
flowchart LR
  A[asr_qwen3 exists] --> B[main_flow_agent]
  B --> C[readiness gate]
  C --> D[dataset scope: ASR]
  D --> E[generate run_evaluation.sh]
  E --> F[bounded smoke]
  F --> G[evaluate_predictions.py]
  G --> H[main_agent_run_report.json]
```

### New Multi-Task Speech Model

```mermaid
flowchart LR
  A[raw Kimi-Audio style model] --> B[model_tool_agent]
  B --> C[task routing: ASR + S2TT + SER + SLU + GR]
  C --> D[fixture routing: atomic fixtures]
  D --> E[env routing: Docker / uv]
  E --> F[validate import/load/infer/contract]
  F --> G[tool ready]
  G --> H[main_flow_agent evaluation]
```

### Metric Dependency Isolation

```mermaid
flowchart TD
  A[Task metric needed] --> B{Task}
  B -- ASR --> ASR[src/sure_eval/evaluation/asr/pyproject.toml]
  B -- S2TT --> S2TT[src/sure_eval/evaluation/s2tt/pyproject.toml]
  B -- SER / SLU / GR --> CLS[src/sure_eval/evaluation/classification/pyproject.toml]
  B -- Future TTS / VC / KWS --> FUT[task-local metric env]
```

## Detailed Documents

- `docs/agents/main_flow_agent/README.md`
- `docs/agents/model_tool_agent/README.md`
- `docs/agents/main_flow_agent/AGENTS.md`
- `docs/agents/model_tool_agent/AGENTS.md`
