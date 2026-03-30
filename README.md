# SURE-EVAL

**S**ystematic **U**nified **R**obust **E**valuation Framework for Audio Processing

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

SURE-EVAL is an autonomous evaluation framework for audio processing tools and models. It supports multiple tasks including ASR, S2TT, SD, SER, and more.

---

## ⚠️ CRITICAL WARNING FOR AI AGENTS

> **READ THIS BEFORE MAKING ANY CHANGES TO THE REPOSITORY**

### 🚫 DO NOT DELETE EXISTING DATA WITHOUT EXPLICIT USER CONFIRMATION

**This is a serious incident that has occurred:**
- An AI agent accidentally deleted the `tests/fixtures/` directory containing standardized test samples
- This data is essential for model evaluation and took significant effort to curate

### ✅ RULES FOR FILE DELETION

| What You Can Delete | What You MUST NOT Delete | Required Action |
|---------------------|--------------------------|-----------------|
| Your own test scripts | `tests/fixtures/` directory | ❌ ASK USER FIRST |
| Your temporary files | Any existing test data | ❌ ASK USER FIRST |
| Cache/build artifacts | Source code you didn't write | ❌ ASK USER FIRST |
| Model prediction outputs | `.gitignore` entries without understanding | ❌ ASK USER FIRST |

### ⚠️ MANDATORY CHECKLIST BEFORE DELETING ANYTHING

1. **Is this file/directory created by you in this session?**
   - If NO → **DO NOT DELETE**
   - If YES → Proceed to step 2

2. **Is this file in `.gitignore`?**
   - If YES → Check if it's meant to be excluded from git but kept locally
   - If NO → **DO NOT DELETE without user confirmation**

3. **Does deleting this affect other parts of the system?**
   - If YES → **DO NOT DELETE without user confirmation**

4. **Have you asked the user for explicit permission?**
   - If NO → **DO NOT DELETE**

### 📋 WHEN IN DOUBT

**Always use this command to check before deleting:**
```bash
git status --short
```

- Files marked with `??` are untracked (new files)
- Files marked with `M` are modified
- Files marked with `D` are deleted (be very careful!)

**If a file is already tracked by git (not `??`), DO NOT delete it without user confirmation.**

### 🔴 CONSEQUENCES OF VIOLATING THESE RULES

- Loss of curated test datasets
- Broken evaluation pipelines
- Hours of debugging to restore lost data
- Potential data corruption

---

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

  qwen3_omni:
    name: "qwen3_omni"
    command: [".venv/bin/python", "server.py"]
    working_dir: "/path/to/sure/src/sure_eval/models/qwen3_omni"
    env:
      DASHSCOPE_API_KEY: "your-api-key"  # Set via environment or config
    timeout: 60
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
| [qwen3_omni](src/sure_eval/models/qwen3_omni) | OMNI | ✓ Ready | Qwen3-Omni API (text+audio) |
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
│   │   ├── qwen3_omni/     # Qwen3-Omni API client
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

### For AI Agents

#### 🤖 Agent Workflow for Tool Environment Configuration

SURE-EVAL 包含一套完整的 **Harness-First Agent Workflow**，可用于自动化配置任何音频处理工具的开发和部署环境。

**使用方法：**

1. **准备初始 Prompt**（复制以下内容作为 Agent 的 system prompt）：

```text
cd /path/to/sure-eval
你现在扮演 SURE-EVAL 的模型接入执行代理。你的任务不是做开放式探索，而是严格按照仓库中定义的 harness-first 工作流，完成一个模型的第一阶段 onboarding。

你必须遵守以下文档：
1. src/sure_eval/models/AGENTS.md
2. docs/policies/constitution.md
3. docs/policies/evidence_priority.md
4. docs/policies/retry_and_escalation.md
5. docs/contracts/spec_validation.md
6. docs/contracts/minimal_validation.md
7. docs/specs/wrapper_contract.md
8. docs/contracts/fixture_policy.md

你的目标：
- 针对给定模型，完成第一阶段端到端接入
- 当前只评估端到端成功与否，不要求节点级评估分析
- 你必须显式产出所有 required artifacts，以及满足条件时的 conditional artifacts
- 如果流程失败，必须进入 DIAGNOSE / REPLAN，并按 policy 决定是否重试或升级
- 不允许盲重试
- 不允许无记录 patch
- 不允许跳过 VALIDATE_SPEC

请按当前 workflow 执行：
DISCOVER → CLASSIFY → PLAN → VALIDATE_SPEC → BUILD_ENV → FETCH_WEIGHTS → VALIDATE_IMPORT → VALIDATE_LOAD → VALIDATE_INFER → VALIDATE_CONTRACT → GENERATE_WRAPPER → SAVE_ARTIFACTS

运行时验证对象说明：
- 第一阶段 runtime validation 验证 repo-native entrypoint / minimal callable path
- wrapper 在 contract 验证通过后生成，用于接入 SURE

你的工作要求：
- 所有关键决策必须基于 evidence，并记录到结构化工件
- 所有失败必须分类
- 所有工件必须落盘
- 最终输出 verdict.json，并简要汇报：成功 / 失败、停在哪一步、是否触发升级
- 额外输出一段"phase-1 target understanding"，用 3-8 行说明：
  1. 当前模型最小要验证的 repo-native path
  2. 当前 fixture 是否 task-specific
  3. 当前 backend 选择是强约束还是初始建议
  4. 当前失败时应优先检查 integration、dependency 还是 fixture mismatch

下面是本次模型输入：

MODEL_INPUT
```

2. **准备 MODEL_INPUT**（模型个性化配置，推荐使用强大的 LLM 生成）：

```yaml
model_id: Rikorose/DeepFilterNet
model_name: DeepFilterNet
task_type: speech_enhancement
deployment_type: local

repo:
  url: https://github.com/Rikorose/DeepFilterNet
  commit: null

weights:
  source: release_or_pypi
  local_path: null
  required: true

environment_hint:
  preferred_backend: uv
  python_version: "3.10"
  requires_gpu: false
  system_packages: [ffmpeg]

phase1_runtime_target:
  Validate the minimal enhancement path only:
  - confirm DeepFilterNet CLI or Python package is callable
  - enhance one noisy wav fixture
  - produce a non-empty enhanced output file
  This phase does NOT require PESQ/STOI evaluation or real-time deployment validation.

entrypoints:
  import_test: "deepFilter --help"
  load_test: "deepFilter --version"
  infer_test: "deepFilter tests/fixtures/shared/se/noisy_48k.wav --output-dir artifacts/df_out"

fixture:
  audio: tests/fixtures/shared/se/noisy_48k.wav
  transcript: tests/fixtures/shared/se/noisy_48k.txt
  task_specific: true
  fallback_allowed: false
  note: >
    Use a noisy speech wav at 48kHz because the official CLI path is defined for wav files at 48kHz.

io_contract:
  input_type: audio_path
  output_type: json
  primary_field: output_path
  required_fields: [output_path]
  nonempty_fields: [output_path]
  json_serializable: true

contract_expectation:
  - output_path exists
  - output file is non-empty
```

> 💡 **提示**：MODEL_INPUT 的质量直接影响适配难度。建议使用 GPT-4、Claude 或其他强大模型根据官方文档生成，确保字段准确、fixture 选择合理。

3. **运行 Agent**：将初始 Prompt + MODEL_INPUT 发送给 AI Agent，Agent 将自动执行完整 workflow 并生成所有工件。

#### 📚 Additional Agent Documentation

- **[Agent Tool Management Guide](src/sure_eval/models/AGENTS.md)** ⭐ - **Essential guide for AI agents configuring and managing tools**
  - Tool classification (API vs Local deployment)
  - Environment management strategy (UV vs Conda)
  - Step-by-step configuration workflow
  - Validation checklists and troubleshooting

### For Developers

- [Architecture](ARCHITECTURE.md) - System architecture details
- [Data Download Guide](DATA_DOWNLOAD_GUIDE.md) - SURE Benchmark download instructions
- [Data Processing Summary](DATA_PROCESSING_SUMMARY.md) - Data format conversion details
- [Refactoring Summary](REFACTORING_SUMMARY.md) - Recent refactoring notes
- [Integration Guide](INTEGRATION_GUIDE.md) - Integrating new models and tools

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Acknowledgments

- SURE Benchmark: [ModelScope](https://www.modelscope.cn/datasets/SUREBenchmark)
- Qwen3-ASR: [HuggingFace](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)
