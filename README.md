# SURE-EVAL

**S**ystematic **U**nified **R**obust **E**valuation Framework for Audio Processing

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

SURE-EVAL is an autonomous evaluation framework for audio processing tools and models, featuring a **Harness-First Agent Workflow** for automated tool onboarding.

**🚀 Onboard any audio tool in minutes, not hours.**

Simply provide a model specification → Agent auto-configures environment → Validated and ready to use.

---

## Features

### 🚀 Automated Tool Onboarding (New)

**The fastest way to integrate any audio processing tool.**

```bash
# Just provide model spec → Agent does the rest
# Works with: Speech Enhancement, ASR, Diarization, VAD, Music IR, and more
```

Our **Harness-First Agent Workflow** automatically:
- Discovers repository structure and dependencies
- Builds isolated environments (uv/conda/docker)
- Validates import → load → inference → contract
- Generates MCP-compliant wrappers
- Produces structured artifacts for reproducibility

[→ Start Onboarding](src/sure_eval/models/README.md) | [→ See Configured Models](src/sure_eval/models/README.md#configured-models)

### Core Capabilities

- **📊 Multi-Task Support**: ASR, S2TT (Speech Translation), SD (Speaker Diarization), SA-ASR, SER, Music IR, Speech Enhancement
- **🤖 Autonomous Evaluation**: Download datasets, run inference, compute metrics, track RPS scores
- **🔧 MCP Tool Integration**: Standardized interface for any tool
- **📈 RPS Tracking**: Relative Performance Score for tool comparison
- **🎯 Tool Recommendation**: AI-powered best tool selection

---

## Quick Start

### Installation

```bash
git clone https://github.com/PigeonDan1/sure.git
cd sure
pip install -r requirements.txt
```

### Download Data

```bash
# Download SURE benchmark datasets (~11GB)
python scripts/download_sure_data.py

# Convert to JSONL
python scripts/convert_sure_to_jsonl.py \
    --csv-dir data/datasets/sure_benchmark/SURE_Test_csv \
    --output-dir data/datasets/sure_benchmark/jsonl
```

### Run Evaluation

```bash
# Quick test (10 samples)
python -c "
from sure_eval import AutonomousEvaluator
evaluator = AutonomousEvaluator()
result = evaluator.quick_test('asr_qwen3', 'aishell1', num_samples=10)
print(f'WER: {result[\"score\"]:.2f}%, RPS: {result[\"rps\"]:.2f}')
"

# Full evaluation
sure-eval evaluate asr_qwen3 aishell1 --max-samples 100
```

---

## Model Onboarding via Agent Workflow

**Zero-config tool integration.** Provide a model specification, let the agent handle the rest.

**Recommended AI Agents for Onboarding:**
- **Claude Code** (Opus) - Best for complex dependency resolution
- **Codex GPT-5.4** - Excellent for repository analysis
- **Kimi Code** - Good for systematic workflow execution

> ⚠️ **Note**: Avoid agents with strict timeout limits (e.g., 60s) for large model installations.

**Quick Links:**
- [Agent Workflow Guide](src/sure_eval/models/README.md) - Complete documentation with prompt templates
- [Configured Models](src/sure_eval/models/README.md#configured-models) - 10+ models already onboarded

**Usage:**
```bash
cd /path/to/sure-eval
# Follow the guide in src/sure_eval/models/README.md
```

---

## Project Structure

```
sure-eval/
├── src/sure_eval/
│   ├── agent/              # Autonomous evaluation agent
│   ├── core/               # Core utilities
│   ├── datasets/           # Dataset management
│   ├── evaluation/         # Evaluation metrics
│   └── models/             # Model registry & onboarding workflow
├── scripts/                # Utility scripts
├── config/                 # Configuration files
└── docs/                   # Documentation
    ├── policies/           # Agent policies
    ├── contracts/          # Validation contracts
    └── playbooks/          # Operation guides
```

---

## Supported Tasks & Datasets

| Task | Datasets | Metrics |
|------|----------|---------|
| ASR | AISHELL-1, AISHELL-5, LibriSpeech | CER, WER |
| S2TT | CoVoST2 (EN-ZH, ZH-EN) | BLEU, chrF |
| SD | - | DER |
| SER | IEMOCAP | Accuracy |

---

## Documentation

- **[Model Onboarding Guide](src/sure_eval/models/README.md)** - Agent workflow for tool configuration
- **[Agent Policies](docs/policies/)** - Constitution, evidence priority, retry/escalation
- **[Validation Contracts](docs/contracts/)** - Spec validation, minimal validation, fixture policy
- **[Architecture](ARCHITECTURE.md)** - System design details

---

## License

MIT License - see [LICENSE](LICENSE) file for details.
