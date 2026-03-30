# SURE-EVAL

**S**ystematic **U**nified **R**obust **E**valuation Framework for Audio Processing

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

SURE-EVAL is an autonomous evaluation framework for audio processing tools and models. It supports multiple tasks including ASR, S2TT, SD, SER, and more.

---

## Features

- **🤖 Autonomous Evaluation**: Automatically download datasets, run inference, compute metrics, and track RPS scores
- **📊 Multi-Task Support**: ASR, S2TT (Speech Translation), SD (Speaker Diarization), SA-ASR, SER, GR, SLU
- **🔧 MCP Tool Integration**: Evaluate any MCP-compliant tool or model
- **📈 RPS Tracking**: Relative Performance Score for tool comparison and ranking
- **🎯 Tool Recommendation**: Automatically recommend the best tool for each dataset
- **🤖 Agent Workflow**: Built-in harness-first workflow for automated tool/model onboarding ([Guide](src/sure_eval/models/README.md))

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

SURE-EVAL includes a **Harness-First Agent Workflow** for automated tool/model environment configuration.

**Key capabilities:**
- Automated environment setup (uv/conda/docker)
- Standardized validation pipeline
- Artifact generation and tracking
- Failure diagnosis and escalation

**Quick Links:**
- [Agent Workflow Guide](src/sure_eval/models/README.md) - Full documentation with prompt templates
- [Successfully Configured Models](src/sure_eval/models/README.md#configured-models) - See what's already working

**Usage:**
```bash
# Use the agent workflow to onboard any audio processing tool
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
