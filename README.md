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
3. Continue to prediction generation and scoring

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

📖 **Example**: [Qwen3 ASR Case Study](docs/contracts/main_agent_qwen3_asr_case.md)

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
    output_dir: /tmp/main_agent_run_asr_qwen3
```

### Structured Outputs

- `task_classification.json`
- `tool_readiness_routing.json`
- `main_agent_plan.json`
- `dataset_decision.json`
- `script_routing.json`
- `assessment_report.json`
- `main_agent_run_report.json`

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
| [Qwen3 Case Study](docs/contracts/main_agent_qwen3_asr_case.md) | Real replay case |

---

## 📄 License

MIT License. See [LICENSE](LICENSE).
