<div align="center">

# 🔊 SURE-EVAL

**S**ystematic **U**nified **R**obust **E**valuation Framework for Audio Processing

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

</div>

---

## 📋 Overview

SURE-EVAL is an **automated evaluation framework** for audio tools and models, built around a simple principle:

> **🎯 Agent decides scope, scripts enforce execution.**

### Three-Layer Architecture

```
┌─────────────────────────────────────────────────────────┐
│  🤖 Main Flow Agent                                     │
│  Decides what should be run                             │
├─────────────────────────────────────────────────────────┤
│  🔧 Tool Onboarding Workflow                            │
│  Makes models callable in reproducible ways             │
├─────────────────────────────────────────────────────────┤
│  📜 Deterministic Script Layer                          │
│  Prepares, validates, scores, records                   │
└─────────────────────────────────────────────────────────┘
```

---

## ✨ What SURE-EVAL Solves

| Goal | How |
|------|-----|
| **🚀 Onboard a new audio model** | Turn raw repositories into stable local tools |
| **📊 Run controlled evaluations** | Select datasets → Generate predictions → Validate → Score → Record |

> 💡 **Key Insight**: Model integration is high-uncertainty, but evaluation execution should be low-uncertainty. SURE-EVAL separates these concerns.

---

## 🏗️ Architecture

### 🤖 1. Main Flow Agent

**Role**: Orchestration layer

**Responsibilities**:
- Understanding user goals
- Task classification
- Tool readiness verification
- Dataset scope selection
- Script routing
- Outcome assessment

📖 **Documentation**:
- [Agent README](src/sure_eval/agent/README.md)
- [Agent Guide](src/sure_eval/agent/AGENTS.md)

---

### 🔧 2. Tool Onboarding Workflow

**Location**: `src/sure_eval/models/`

**Responsibilities**:
- Backend selection
- Environment isolation
- Import / Load / Infer / Contract validation
- Wrapper generation
- Artifact management

📖 **Documentation**:
- [Models README](src/sure_eval/models/README.md)
- [Models Agent Guide](src/sure_eval/models/AGENTS.md)

---

### 📜 3. Deterministic Script Layer

**Core Scripts**:

| Script | Purpose |
|--------|---------|
| `prepare_sure_dataset.py` | Canonical dataset preparation |
| `materialize_predictions_template.py` | Prediction template generation |
| `validate_prediction_files.py` | Prediction validation |
| `evaluate_predictions.py` | Metric & RPS computation |
| `refresh_report_snapshot.py` | Result recording & reports |

---

## 🚀 Quick Start Guide

### 📍 Which Path Should I Use?

```
Start Here
    ↓
┌────────────────────────────────────────────────────────────┐
│ Do you have a model under src/sure_eval/models/<model>?   │
└────────────────────────────────────────────────────────────┘
    │
    ├── ❌ No → Use Tool Onboarding Workflow
    │         → Build model-local server first
    │         → Then use Main Flow Agent
    │
    └── ✅ Yes → Check config.yaml for server/tool path
                │
                ├── ❌ No server path
                │   → Use Tool Onboarding Workflow
                │
                └── ✅ Server path exists
                    → Run TOOL_READINESS_AND_ROUTING_UNIT
                        │
                        ├── 🟢 server_ready
                        │   → Continue to evaluation
                        │
                        ├── 🟡 server_declared_but_unverified
                        │   → Run smoke test first
                        │
                        └── 🔴 tool_broken_needs_repair
                            → Hand off to tool workflow
```

---

### 🛠️ Path A: Onboard a New Model

**Use when**: Model is not yet in `src/sure_eval/models/`

**Steps**:
1. Go to [Models README](src/sure_eval/models/README.md)
2. Use the tool onboarding prompt template
3. Let the workflow produce a callable model
4. Switch to Main Flow Agent for evaluation

---

### 🎯 Path B: Evaluate an Existing Model

**Use when**: Model already has a directory in `src/sure_eval/models/`

**Steps**:
1. Use prompt from [Agent README](src/sure_eval/agent/README.md)
2. Let agent execute:
   - `TASK_CLASSIFICATION_UNIT`
   - `TOOL_READINESS_AND_ROUTING_UNIT`
   - `PLAN_UNIT`
   - `DATASET_SCOPE_UNIT`
   - `SCRIPT_ROUTING_UNIT`
   - `EXECUTION_READINESS_UNIT`
3. Continue to prediction generation and scoring

Recommended artifact root:

- `src/sure_eval/models/<model>/eval_runs/<run_id>/`

Layout contract:

- [docs/contracts/eval_run_layout.md](docs/contracts/eval_run_layout.md)

---

## ⚡ Installation

```bash
# Clone repository
git clone https://github.com/PigeonDan1/sure.git
cd sure

# Install dependencies
pip install -r requirements.txt
```

---

## 📊 Deterministic Evaluation Pipeline

Execute evaluation without agents:

```bash
# 1️⃣ Prepare datasets
python scripts/prepare_sure_dataset.py \
  --dataset aishell1

# 2️⃣ Generate prediction templates
python scripts/materialize_predictions_template.py \
  --dataset aishell1 \
  --output-dir /tmp/predictions

# 3️⃣ Fill /tmp/predictions/aishell1.txt with:
#    key<TAB>prediction

# 4️⃣ Validate predictions
python scripts/validate_prediction_files.py \
  --dataset aishell1 \
  --pred-dir /tmp/predictions \
  --require-nonempty

# 5️⃣ Evaluate and record
python scripts/evaluate_predictions.py \
  --dataset aishell1 \
  --pred-dir /tmp/predictions \
  --tool-name my_model \
  --record \
  --output /tmp/eval_payload.json
```

---

## 🔄 Main Flow Execution

### Flow Diagram

```
TASK_CLASSIFICATION_UNIT
        ↓
TOOL_READINESS_AND_ROUTING_UNIT
        ↓
      PLAN_UNIT
        ↓
   DATASET_SCOPE_UNIT
        ↓
   SCRIPT_ROUTING_UNIT
        ↓
EXECUTION_READINESS_UNIT
        ↓
   EXECUTE / WAIT
        ↓
   ASSESSMENT_UNIT
        ↓
   RUN_REPORT_UNIT
```

> ⚠️ **Critical Rule**: Never skip tool readiness routing!

If a model declares a server path:
1. Prefer server-first smoke test
2. Confirm `server_ready` status
3. Only then proceed to evaluation

### Two-Stage Usage Note

SURE-EVAL currently treats **tool onboarding** and **evaluation** as two related
but distinct stages:

1. **Stage 1: Tool Onboarding / Adaptation**
   - Build or repair the model-local tool path under `src/sure_eval/models/<model>/`
   - Validate minimal callable readiness such as import / load / infer / contract
   - Produce reusable model-local artifacts, wrapper, and server/tool entrypoint

2. **Stage 2: Main-Flow Evaluation**
   - Reuse the onboarded tool path
   - Run tool-readiness routing
   - Select datasets and invoke deterministic evaluation scripts

This means a model should normally be **adapted first, evaluated second**.
If the main flow discovers that a target is `not_tool_ready` or
`tool_broken_needs_repair`, the correct action is to stop evaluation routing and
hand off to the existing tool onboarding workflow instead of improvising
evaluation-time fixes.

This is intentionally modeled as a **two-stage workflow**, not a required
multi-agent architecture. At the current stage of the project, keeping one main
flow agent plus one existing tool onboarding workflow is the preferred design.

For new or not-yet-adapted tools, users should provide onboarding-oriented
inputs as early as possible, such as:

- upstream repository or local code path
- model/checkpoint source
- expected task type and IO contract
- environment or dependency hints

If those inputs are missing, main flow may determine that evaluation cannot
continue yet because the onboarding stage is not ready to start cleanly.

📖 **Example**: [Qwen3 ASR Case Study](docs/contracts/main_agent_qwen3_asr_case.md)

Prediction generation should follow a hard contract rather than an implicit
"wait until files appear" step:

- [docs/contracts/prediction_generation_contract.md](docs/contracts/prediction_generation_contract.md)

For human-operated background runs, prefer a single-model single-dataset shell:

- [docs/contracts/single_model_single_dataset_shell.md](docs/contracts/single_model_single_dataset_shell.md)

Before handing that shell to a user, the main flow should run a bounded
execution-readiness validation:

- [docs/contracts/main_agent_execution_readiness_unit.md](docs/contracts/main_agent_execution_readiness_unit.md)

---

## 📝 Example: Evaluate with Main Flow Agent

Use the prompt template from [Agent README](src/sure_eval/agent/README.md), then provide:

```yaml
MAIN_FLOW_INPUT:
  user_goal: evaluate_existing_model

  target:
    model_name: asr_qwen3
    model_dir: src/sure_eval/models/asr_qwen3
    tool_workflow_ready: true

  constraints:
    allow_tool_workflow: true
    allowed_tasks: [ASR]
    allowed_datasets: null
    blocked_datasets: []
    dry_run: false

  evidence:
    readme_path: src/sure_eval/models/asr_qwen3/README.md
    config_path: src/sure_eval/models/asr_qwen3/config.yaml
    artifacts_dir: src/sure_eval/models/asr_qwen3/artifacts
    model_spec_path: src/sure_eval/models/asr_qwen3/model.spec.yaml

  runtime_context:
    available_scripts:
      - scripts/prepare_sure_dataset.py
      - scripts/materialize_predictions_template.py
      - scripts/validate_prediction_files.py
      - scripts/evaluate_predictions.py
    output_dir: src/sure_eval/models/asr_qwen3/eval_runs/main_agent_asr_qwen3_001
```

### Structured Outputs

- `task_classification.json`
- `tool_readiness_routing.json`
- `main_agent_plan.json`
- `dataset_decision.json`
- `script_routing.json`
- `execution_readiness_report.json`
- `assessment_report.json`
- `main_agent_run_report.json`
- `model_eval_manifest.json`

---

## 📁 Project Structure

```
sure-eval/
├── src/sure_eval/
│   ├── agent/              # 🤖 Main flow agent harness
│   ├── core/               # ⚙️ Core utilities
│   ├── datasets/           # 📂 Dataset management
│   ├── evaluation/         # 📊 Metrics and RPS
│   ├── models/             # 🔧 Tool onboarding & registry
│   └── reports/            # 📈 Reporting and baselines
├── scripts/                # 📜 Deterministic evaluation scripts
├── templates/              # 📝 Structured output templates
├── config/                 # ⚙️ Configuration files
└── docs/                   # 📚 Contracts, policies, playbooks
```

---

## 🎯 Supported Tasks

| Task | Description |
|------|-------------|
| **ASR** | Automatic Speech Recognition |
| **S2TT** | Speech-to-Text Translation |
| **SD** | Speaker Diarization |
| **SA-ASR** | Speaker-Aware ASR |
| **SER** | Speech Emotion Recognition |
| **Speech Enhancement** | Noise suppression, enhancement |
| **Music IR** | Music information retrieval |

---

## 📚 Documentation Map

| Document | Purpose |
|----------|---------|
| [Main Flow Agent](src/sure_eval/agent/README.md) | Agent system prompt & examples |
| [Agent Routing](src/sure_eval/agent/AGENTS.md) | Main flow routing guide |
| [Tool Onboarding](src/sure_eval/models/README.md) | Model integration workflow |
| [Architecture](docs/contracts/main_flow_architecture.md) | System architecture details |
| [Evaluation Run Layout](docs/contracts/eval_run_layout.md) | Model-local artifact layout per run |
| [Prediction Generation Contract](docs/contracts/prediction_generation_contract.md) | Hard contract for `wait_for_predictions` |
| [Single Model Single Dataset Shell](docs/contracts/single_model_single_dataset_shell.md) | One-command execution contract for human operators |
| [Execution Readiness Unit](docs/contracts/main_agent_execution_readiness_unit.md) | Preflight shell validation before background runs |
| [Model Eval Manifest](docs/contracts/model_eval_manifest.md) | One-file index for a model evaluation run |
| [Qwen3 Case Study](docs/contracts/main_agent_qwen3_asr_case.md) | Real replay case |

---

## 📄 License

MIT License. See [LICENSE](LICENSE).
