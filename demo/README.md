# SURE-EVAL Demo Scripts

This directory contains demo scripts to help you get started with SURE-EVAL quickly.

## Quick Start

### 1. Verify Setup

First, run the quick test to verify everything is working:

```bash
cd /path/to/sure-eval
python demo/demo_quickstart.py
```

This will:
- Load the configuration
- Discover available models and datasets
- Show you what's ready to use

### 2. Setup Model Environment

Before evaluating a model, you need to set up its environment using `uv`:

```bash
# Install uv if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Setup a specific model
python demo/setup_model.py asr_qwen3

# List all available models
python demo/setup_model.py --list
```

### 3. Run Evaluation

#### Evaluate a Single Model

```bash
# Quick test (10 samples)
python demo/demo_evaluate_model.py --model asr_qwen3 --dataset aishell1 --samples 10

# Full evaluation (all samples)
python demo/demo_evaluate_model.py --model asr_qwen3 --dataset aishell1 --full

# Verbose mode (shows predictions)
python demo/demo_evaluate_model.py --model asr_qwen3 --dataset aishell1 --samples 5 --verbose
```

#### Compare Multiple Models

```bash
# Compare two models on the same dataset
python demo/demo_compare_models.py --models asr_qwen3,asr_whisper --dataset aishell1

# With more samples
python demo/demo_compare_models.py --models asr_qwen3,asr_whisper --dataset aishell1 --samples 50

# Save results to file
python demo/demo_compare_models.py --models asr_qwen3 --dataset aishell1 --output results.json
```

## Demo Scripts Overview

| Script | Purpose | Usage |
|--------|---------|-------|
| `demo_quickstart.py` | Verify framework setup | `python demo_quickstart.py` |
| `setup_model.py` | Setup model environment | `python setup_model.py <model>` |
| `demo_evaluate_model.py` | Evaluate single model | `python demo_evaluate_model.py -m <model> -d <dataset>` |
| `demo_compare_models.py` | Compare multiple models | `python demo_compare_models.py -m <model1>,<model2> -d <dataset>` |
| `demo_reports.py` | View SOTA baselines and model reports | `python demo_reports.py --sota-summary` |

### 4. View SOTA Baselines and Model Reports

```bash
# Show SOTA baselines for all datasets
python demo/demo_reports.py --sota-summary

# Show model performance summary
python demo/demo_reports.py --model-summary Qwen3-Omni

# Show leaderboard for a dataset
python demo/demo_reports.py --leaderboard aishell1

# Compare models on a dataset
python demo/demo_reports.py --compare Qwen3-Omni,Kimi-Audio,Gemini-3.0pro --dataset aishell1

# Test RPS calculation
python demo/demo_reports.py --test-rps

# Generate Markdown report
python demo/demo_reports.py --generate-md
```

## Available Models

Models are located in `src/sure_eval/models/`:

- **asr_qwen3**: ASR using Qwen3-ASR-1.7B
- **asr_whisper**: ASR using OpenAI Whisper (template)
- **diarizen**: Speaker diarization (template)
- **s2tt_nllb**: Speech-to-text translation (template)

## Available Datasets

Datasets are expected in `data/processed/`:

- **aishell1**: Chinese Mandarin ASR dataset
- **librispeech_clean**: English ASR (clean)
- **librispeech_other**: English ASR (other)

## Model Environment Structure

Each model has its own isolated environment managed by `uv`:

```
src/sure_eval/models/<model_name>/
├── pyproject.toml          # Dependencies
├── .venv/                  # Virtual environment (created by setup_model.py)
├── config.yaml            # MCP server configuration
├── server.py              # MCP server implementation
├── model.py               # Model wrapper
└── README.md              # Model documentation
```

### Adding a New Model

1. Create a new directory in `src/sure_eval/models/`
2. Add `pyproject.toml` with dependencies
3. Add `config.yaml` with MCP configuration
4. Add `server.py` with MCP server implementation
5. Run `python demo/setup_model.py <model_name>`

Example `pyproject.toml`:

```toml
[project]
name = "my-model"
version = "1.0.0"
description = "My custom model"
requires-python = ">=3.10"
dependencies = [
    "torch>=2.0.0",
    "transformers>=4.40.0",
    # ... other dependencies
]

[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[tool.uv]
# uv configuration
```

## Troubleshooting

### uv not found

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or with pip
pip install uv
```

### Model not found

```bash
# List available models
python demo/setup_model.py --list

# Check if model directory exists
ls src/sure_eval/models/
```

### Dataset not found

```bash
# Check data directory structure
ls data/processed/

# Datasets should be in JSONL format:
# data/processed/<dataset_name>/data.jsonl
```

### Environment setup failed

```bash
# Remove existing environment and retry
rm -rf src/sure_eval/models/<model_name>/.venv
python demo/setup_model.py <model_name>
```

## Next Steps

- Read the main [README.md](../README.md) for full documentation
- Check [ARCHITECTURE.md](../ARCHITECTURE.md) for design details
- Explore the [Model Integration Guide](../docs/model_integration.md) to add new models
