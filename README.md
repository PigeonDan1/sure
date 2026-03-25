# SURE-EVAL

**S**ystematic **U**nified **R**obust **E**valuation Framework for Audio Processing

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

SURE-EVAL is an autonomous evaluation framework for audio processing tools and models. It supports multiple tasks including ASR, S2TT, SD, SER, and more.

## Features

- **🤖 Autonomous Evaluation**: Automatically download datasets, run inference, compute metrics, and track RPS scores
- **📊 Multi-Task Support**: ASR, S2TT (Speech Translation), SD (Speaker Diarization), SA-ASR, SER, GR, SLU
- **🔧 MCP Tool Integration**: Evaluate any MCP-compliant tool or model
- **📈 RPS Tracking**: Relative Performance Score for tool comparison and ranking
- **🎯 Tool Recommendation**: Automatically recommend the best tool for each dataset
- **📁 Standardized Models**: Built-in model registry with standardized interfaces

## Installation

```bash
# Clone repository
git clone https://github.com/PigeonDan1/sure.git
cd sure

# Install dependencies
pip install -r requirements.txt

# Or install in development mode
pip install -e ".[dev]"
```

### Dependencies

- Python >= 3.10
- PyTorch >= 2.0
- transformers >= 4.40
- Other requirements in `requirements.txt`

## Quick Start

### 1. Clone and Install

```bash
# Clone repository
git clone https://github.com/PigeonDan1/sure.git
cd sure

# Install dependencies
pip install -r requirements.txt
```

### 2. Download SURE Benchmark Data

```bash
# Download all SURE benchmark datasets (~11GB)
python scripts/download_sure_data.py

# Or download specific parts
python scripts/download_sure_data.py --csv    # Annotations only
python scripts/download_sure_data.py --suites # Audio files only
```

### 3. Convert to JSONL Format

```bash
python scripts/convert_sure_to_jsonl.py \
    --csv-dir data/datasets/sure_benchmark/SURE_Test_csv \
    --output-dir data/datasets/sure_benchmark/jsonl
```

### 4. Run Demo (Recommended for First-Time Users)

```bash
# Verify setup
python demo/demo_quickstart.py

# Setup model environment (requires uv)
python demo/setup_model.py asr_qwen3

# Evaluate model on dataset
python demo/demo_evaluate_model.py --model asr_qwen3 --dataset aishell1 --samples 10
```

See [demo/README.md](demo/README.md) for more examples.

### 5. Configure MCP Tools (Advanced)

Edit `config/mcp_tools.yaml`:

```yaml
tools:
  asr_qwen3:
    name: "asr_qwen3"
    command: [".venv/bin/python", "server.py"]
    working_dir: "/path/to/sure/src/sure_eval/models/asr_qwen3"
    env:
      MODEL_PATH: "Qwen/Qwen3-ASR-1.7B"
      DEVICE: "auto"
    timeout: 300
```

### 6. Run Evaluation

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

# Batch evaluation
sure-eval batch-evaluate asr_qwen3 aishell1 librispeech_clean

# Compare tools
sure-eval compare asr_qwen3 asr_whisper --dataset aishell1
```

## Model Environment Setup

Each model has its own isolated Python environment managed by [uv](https://github.com/astral-sh/uv):

```bash
# Install uv first
curl -LsSf https://astral.sh/uv/install.sh | sh

# Setup model environment
python demo/setup_model.py asr_qwen3

# List all models and their status
python demo/setup_model.py --list
```

### Model Directory Structure

```
src/sure_eval/models/<model_name>/
├── pyproject.toml      # Dependencies (managed by uv)
├── .venv/              # Virtual environment
├── config.yaml         # MCP server configuration
├── server.py           # MCP server implementation
├── model.py            # Model wrapper
└── README.md           # Model documentation
```

### Why uv?

- **Fast**: 10-100x faster than pip
- **Reliable**: Deterministic dependency resolution
- **Lightweight**: Efficient virtual environments
- **Standards-compliant**: Supports PEP 518/621

## Model Registry

SURE-EVAL includes a standardized model registry:

```python
from sure_eval.models import ModelRegistry

registry = ModelRegistry()
registry.print_summary()

# Output:
# ============================================================
# SURE-EVAL Model Registry
# ============================================================
# ✓ asr_qwen3            [ASR       ] Automatic Speech Recognition u...
# ○ asr_whisper          [ASR       ] ASR using OpenAI Whisper - Tem...
# ○ s2tt_nllb            [S2TT      ] Speech-to-Text Translation usi...
# ○ diarizen             [SD        ] Speaker Diarization using Diar...
# ============================================================
# Total: 4 models
```

### Available Models

| Model | Task | Status | Description |
|-------|------|--------|-------------|
| [asr_qwen3](src/sure_eval/models/asr_qwen3) | ASR | ✓ Ready | Qwen3-ASR-1.7B (Chinese/English) |
| [asr_whisper](src/sure_eval/models/asr_whisper) | ASR | ○ Template | OpenAI Whisper (template) |
| [s2tt_nllb](src/sure_eval/models/s2tt_nllb) | S2TT | ○ Template | NLLB Translation (template) |
| [diarizen](src/sure_eval/models/diarizen) | SD | ○ Template | Speaker Diarization (template) |

## Project Structure

```
sure-eval/
├── src/sure_eval/
│   ├── agent/              # Autonomous evaluation agent
│   │   ├── evaluator.py    # Main evaluation orchestrator
│   │   └── orchestrator.py # Task orchestration
│   ├── core/               # Core utilities
│   │   ├── config.py       # Configuration management
│   │   └── logging.py      # Structured logging
│   ├── datasets/           # Dataset management
│   │   └── dataset_manager.py  # Unified dataset loader
│   ├── evaluation/         # Evaluation metrics
│   │   ├── sure_evaluator.py   # Standard evaluator
│   │   ├── metrics.py      # Metric implementations
│   │   ├── rps.py          # RPS calculation
│   │   └── normalization/  # Text normalization
│   ├── models/             # Model registry
│   │   ├── registry.py     # Model registry
│   │   ├── base.py         # Base model interface
│   │   ├── asr_qwen3/      # ASR Qwen3 implementation
│   │   ├── asr_whisper/    # ASR Whisper template
│   │   ├── s2tt_nllb/      # S2TT NLLB template
│   │   └── diarizen/       # Diarization template
│   └── tools/              # Tool clients
│       ├── mcp_client.py   # MCP tool client
│       └── api_adapter.py  # API adapters
├── scripts/                # Utility scripts
│   ├── download_sure_data.py
│   ├── convert_sure_to_jsonl.py
│   └── run_sure_evaluation.py
├── config/                 # Configuration files
│   ├── default.yaml        # Default configuration
│   └── mcp_tools.yaml      # MCP tool definitions
└── data/                   # Data directory (gitignored)
    └── datasets/
        └── sure_benchmark/
            ├── SURE_Test_csv/      # Annotations
            ├── SURE_Test_Suites/   # Audio files
            └── jsonl/              # JSONL format
```

## Supported Datasets

### SURE Benchmark

| Dataset | Task | Language | Samples |
|---------|------|----------|---------|
| AISHELL-1 | ASR | zh | 7,176 |
| AISHELL-5 | ASR | zh | 20,368 |
| LibriSpeech Clean | ASR | en | 2,619 |
| LibriSpeech Other | ASR | en | 2,939 |
| CoVoST2 EN-ZH | S2TT | en→zh | 15,531 |
| CoVoST2 ZH-EN | S2TT | zh→en | 4,898 |
| IEMOCAP | SER | en | 1,241 |
| KeSpeech | ASR | zh | 19,723 |
| MMSU | SLU | zh | 2,416 |

## Supported Metrics

| Task | Metrics | Description |
|------|---------|-------------|
| ASR | CER, WER | Character/Word Error Rate |
| S2TT | BLEU, chrF | Translation quality |
| SD | DER | Diarization Error Rate |
| SA-ASR | cpWER, DER | Multi-speaker metrics |
| SER/GR/SLU | Accuracy | Classification accuracy |

## Python API

### Autonomous Evaluation

```python
from sure_eval import AutonomousEvaluator

evaluator = AutonomousEvaluator()

# Quick test
result = evaluator.quick_test("asr_qwen3", "aishell1", num_samples=10)
print(f"WER: {result['score']:.2f}%, RPS: {result['rps']:.2f}")

# Full evaluation
result = evaluator.evaluate_tool("asr_qwen3", "aishell1", max_samples=100)
print(f"Score: {result.score}, RPS: {result.rps}")

# Batch evaluation
results = evaluator.batch_evaluate("asr_qwen3", ["aishell1", "librispeech_clean"])

# Compare tools
comparison = evaluator.compare_tools(["asr_qwen3", "asr_whisper"], "aishell1")

# Get recommendation
recommendation = evaluator.recommend_tool("aishell1")
print(f"Best tool: {recommendation['best_tool']}")
```

### Dataset Management

```python
from sure_eval import DatasetManager

manager = DatasetManager()

# Download and convert
manager.download_and_convert("aishell1")

# Get dataset info
info = manager.get_info("aishell1")
print(f"Task: {info['task']}, Language: {info['language']}")

# List available datasets
available = manager.list_available()
```

### Direct Evaluation

```python
from sure_eval import SUREEvaluator

evaluator = SUREEvaluator(language="zh")
result = evaluator.evaluate("ASR", "reference.txt", "hypothesis.txt")
print(f"WER: {result['wer']:.2f}%")
```

## Configuration

### Environment Variables

```bash
export SURE_EVAL_CONFIG="./config/default.yaml"
export HF_TOKEN="your-huggingface-token"
export DASHSCOPE_API_KEY="your-dashscope-key"
```

### Default Configuration

Edit `config/default.yaml`:

```yaml
data:
  root: "./data"
  datasets: "./data/datasets"
  models: "./data/models"
  results: "./results"

evaluation:
  default_metrics:
    ASR: "cer"
    S2TT: "bleu"
    SD: "der"
    SER: "accuracy"

rps:
  baselines:
    aishell1:
      metric: "cer"
      score: 4.6  # CER %
      higher_is_better: false
```

## Adding a New Model

1. Create model directory:
```bash
mkdir src/sure_eval/models/my_model
cd src/sure_eval/models/my_model
```

2. Create required files:
- `pyproject.toml` - Dependencies (uv-managed)
- `config.yaml` - Model and MCP configuration
- `model.py` - Model wrapper class
- `server.py` - MCP server implementation
- `README.md` - Documentation

3. Example `pyproject.toml`:
```toml
[project]
name = "my-model-eval"
version = "1.0.0"
description = "My custom model for SURE-EVAL"
requires-python = ">=3.10"
dependencies = [
    "torch>=2.0.0",
    "transformers>=4.40.0",
    # ... other dependencies
]

[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"
```

4. Example `config.yaml`:
```yaml
name: my_model
task: ASR
description: "My ASR model"

model:
  id: "org/model-name"
  size: "1B"
  languages: ["zh", "en"]

server:
  command: [".venv/bin/python", "server.py"]
  env:
    MODEL_PATH: "org/model-name"
  timeout: 300
```

5. Setup environment:
```bash
python demo/setup_model.py my_model
```

6. Test your model:
```bash
python demo/demo_evaluate_model.py --model my_model --dataset aishell1 --samples 10
```

## Documentation

- [Architecture](ARCHITECTURE.md) - System architecture details
- [Data Download Guide](DATA_DOWNLOAD_GUIDE.md) - SURE Benchmark download instructions
- [Data Processing Summary](DATA_PROCESSING_SUMMARY.md) - Data format conversion details
- [Refactoring Summary](REFACTORING_SUMMARY.md) - Recent refactoring notes

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Acknowledgments

- SURE Benchmark: [ModelScope](https://www.modelscope.cn/datasets/SUREBenchmark)
- Qwen3-ASR: [HuggingFace](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)
