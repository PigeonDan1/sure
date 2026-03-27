# SURE-EVAL Models

Standardized model directory for SURE-EVAL.

## Directory Structure

```
models/
├── README.md           # This file
├── __init__.py         # Model registry
├── base.py             # Base model interface
├── registry.py         # Model registry implementation
│
├── asr_qwen3/          # ASR Qwen3 (implemented)
│   ├── README.md
│   ├── config.yaml
│   ├── model.py
│   ├── server.py
│   ├── pyproject.toml
│   ├── setup.sh
│   ├── __init__.py
│   └── results/
│       └── aishell1_20250325.json
│
├── asr_whisper/        # ASR Whisper (template)
│   └── README.md
│
├── s2tt_nllb/          # S2TT NLLB (template)
│   └── README.md
│
└── diarizen/           # Diarization (template)
    └── README.md
```

## Model Template

Each model should have:

### Required Files

1. **README.md** - Model documentation
2. **config.yaml** - MCP configuration
3. **model.py** - Core model wrapper
4. **server.py** - MCP server
5. **pyproject.toml** - Python dependencies
6. **setup.sh** - Environment setup
7. **__init__.py** - Package exports

### Optional Files

- **results/** - Test results directory
- **tests/** - Unit tests

## Quick Start

### Add a New Model

1. Create directory:
```bash
mkdir models/my_model
cd models/my_model
```

2. Create config.yaml:
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

3. Implement model.py and server.py

4. Test:
```python
from sure_eval.models import ModelRegistry

registry = ModelRegistry()
info = registry.get_model("my_model")
print(info.get_mcp_config())
```

### Setup Environment

```bash
cd models/asr_qwen3
./setup.sh
```

### Run Tests

```python
from sure_eval import AutonomousEvaluator

evaluator = AutonomousEvaluator()
result = evaluator.quick_test("asr_qwen3", "aishell1", num_samples=10)
```

## Available Models

| Model | Task | Status | Results |
|-------|------|--------|---------|
| asr_qwen3 | ASR | ✓ Implemented | aishell1 (WER 0.79%) |
| asr_whisper | ASR | ✓ Implemented | - |
| asr_parakeet | ASR | ✓ Implemented | - |
| qwen3_omni | OMNI | ✓ Implemented | - |
| s2tt_nllb | S2TT | ○ Template | - |
| diarizen | SD | ✓ Implemented | - |

## Model Registry

```python
from sure_eval.models import ModelRegistry

registry = ModelRegistry()

# List all models
models = registry.list_models()

# Get by task
asr_models = registry.list_by_task("ASR")

# Get model info
info = registry.get_model("asr_qwen3")
print(info.description)
print(info.get_mcp_config())
print(info.get_test_results())

# Generate MCP config
yaml_content = registry.generate_mcp_tools_yaml()
```

## Adding Test Results

Save test results to `results/{dataset}_{date}.json`:

```json
{
  "model": "asr_qwen3",
  "dataset": "aishell1",
  "date": "2025-03-25",
  "num_samples": 10,
  "metrics": {
    "wer": 0.79,
    "all": 127,
    "cor": 126,
    "sub": 1,
    "del": 0,
    "ins": 0
  },
  "rps": 101.60,
  "duration_seconds": 53.46
}
```

## See Also

- [Model Development Guide](../../../docs/model_development.md)
- [Evaluation Guide](../../../docs/evaluation.md)
