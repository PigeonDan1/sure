# SURE-EVAL Architecture

**A Dynamic, Agent-Maintained Evaluation Framework for Audio Processing**

---

## Overview

SURE-EVAL is a comprehensive evaluation framework designed for autonomous testing and reporting of audio processing models. It enables users to evaluate new models against established benchmarks, automatically calculate performance metrics, and maintain an up-to-date leaderboard of SOTA results.

### Key Features

- 🔄 **Autonomous Evaluation**: End-to-end pipeline from data to report
- 📊 **Multi-Task Support**: ASR, S2TT, SD, SER, GR, SLU, SA-ASR
- 🏆 **SOTA Tracking**: Automatic RPS calculation against baselines
- 📝 **Dynamic Reports**: Auto-updating model performance reports
- 🔧 **Tool Integration**: Standardized MCP tool interface
- 📈 **Benchmark Alignment**: SURE Benchmark compatibility

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         USER INTERFACE                                   │
│  (CLI sure-eval / Demo Scripts / Python API)                            │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      AUTONOMOUS EVALUATOR (Agent)                        │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐ │
│  │  Orchestrate    │  │  Calculate RPS  │  │  Generate Reports       │ │
│  │  Evaluation Flow│  │  vs SOTA        │  │  & Leaderboards         │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
        ┌──────────────────────────┼──────────────────────────┐
        │                          │                          │
        ▼                          ▼                          ▼
┌───────────────┐      ┌──────────────────┐      ┌──────────────────────┐
│  DATA PIPELINE│      │  TOOL MANAGEMENT │      │  EVALUATION ENGINE   │
│               │      │                  │      │                      │
│ • Download    │      │ • Model Registry │      │ • SUREEvaluator      │
│ • Format      │      │ • MCP Client     │      │ • Metrics (WER/CER)  │
│ • Normalize   │      │ • Model Mapping  │      │ • RPS Calculator     │
└───────────────┘      └──────────────────┘      └──────────────────────┘
        │                          │                          │
        └──────────────────────────┼──────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         REPORT SYSTEM                                    │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐ │
│  │  SOTA Manager   │  │  Report Manager │  │  Evaluation Database    │ │
│  │  (Baselines)    │  │  (Performance)  │  │  (History)              │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Component Details

### 1. Data Pipeline

**Purpose**: Manage dataset download, formatting, and normalization

**Key Files**:
- `src/sure_eval/datasets/dataset_manager.py`
- `scripts/download_sure_data.py`
- `scripts/convert_sure_to_jsonl.py`

**Features**:
- SURE Benchmark dataset download (via ModelScope)
- CSV → JSONL format conversion
- Dataset name normalization (e.g., `CS_dialogue` → `cs_dialogue`)
- Path mapping and correction

**Usage**:
```python
from sure_eval import DatasetManager

dm = DatasetManager()
datasets = dm.list_available()  # Get normalized dataset names
jsonl_path = dm.download_and_convert("aishell1")
```

---

### 2. Tool Management

**Purpose**: Manage model tools with standardized interfaces

**Key Files**:
- `src/sure_eval/models/registry.py`
- `src/sure_eval/models/model_mapping.py`
- `src/sure_eval/tools/mcp_client.py`

**Features**:
- Model auto-discovery from `src/sure_eval/models/`
- MCP (Model Context Protocol) tool interface
- Bidirectional tool-to-benchmark mapping

**Model Mapping**:
```python
from sure_eval.models import get_benchmark_name, get_tool_name

# Local tool → Benchmark model
get_benchmark_name("asr_qwen3")  # → "Qwen3-ASR-1.7B"

# Benchmark model → Local tool
get_tool_name("Qwen3-ASR-1.7B")  # → "asr_qwen3"
```

**Tool Structure**:
```
models/<tool_name>/
├── pyproject.toml      # UV-managed dependencies
├── config.yaml         # MCP configuration
├── server.py           # MCP server implementation
├── model.py            # Model wrapper
└── README.md           # Documentation
```

---

### 3. Evaluation Engine

**Purpose**: Compute metrics and calculate relative performance

**Key Files**:
- `src/sure_eval/evaluation/sure_evaluator.py`
- `src/sure_eval/evaluation/rps.py`

**Supported Tasks**:
| Task | Metrics | Direction |
|------|---------|-----------|
| ASR | WER, CER, MER | Lower is better ↓ |
| S2TT | BLEU (char-level) | Higher is better ↑ |
| SER | Accuracy | Higher is better ↑ |
| GR | Accuracy | Higher is better ↑ |
| SLU | Accuracy | Higher is better ↑ |
| SD | DER | Lower is better ↓ |
| SA-ASR | cpWER, DER | Lower is better ↓ |

**RPS Calculation**:
```python
from sure_eval.evaluation import RPSManager

rps_mgr = RPSManager()
rps = rps_mgr.calculator.calculate("aishell1", score=0.85)
# RPS = SOTA_score / score = 0.80 / 0.85 = 0.94
```

---

### 4. Report System

**Purpose**: Track SOTA baselines and model performance

**Key Files**:
- `src/sure_eval/reports/sota_manager.py`
- `src/sure_eval/reports/report_manager.py`
- `reports/sota/sota_baseline.yaml`
- `reports/models/model_performance_report.json`

**SOTA Baselines** (14 datasets):
| Dataset | Metric | SOTA Score | SOTA Model |
|---------|--------|------------|------------|
| aishell1 | CER | 0.80% | Qwen3-Omni |
| librispeech_clean | WER | 1.70% | Qwen3-Omni |
| covost2_en2zh | BLEU-char | 46.25 | Qwen3-Omni |
| iemocap | ACC | 69.38% | Kimi-Audio |
| ... | ... | ... | ... |

**Features**:
- SOTA baseline lookup
- Model performance comparison
- Leaderboard generation
- Markdown report export

**Usage**:
```python
from sure_eval.reports import SOTAManager, ReportManager

# Check SOTA
sota = SOTAManager()
baseline = sota.get_baseline("aishell1")
print(f"SOTA: {baseline.sota_model} with {baseline.score}% CER")

# View leaderboard
reports = ReportManager()
reports.print_leaderboard("aishell1")
```

---

### 5. Autonomous Evaluator (Agent)

**Purpose**: Orchestrate the complete evaluation pipeline

**Key File**: `src/sure_eval/agent/evaluator.py`

**Pipeline**:
```
User Request
    │
    ▼
┌─────────────────┐
│ 1. Load Dataset │ ← DatasetManager (auto-download if needed)
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ 2. Run Tool     │ ← MCP Client (inference on samples)
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ 3. Evaluate     │ ← SUREEvaluator (compute metrics)
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ 4. Calc RPS     │ ← SOTAManager (vs SOTA baseline)
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ 5. Record       │ ← EvaluationDatabase (save results)
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ 6. Report       │ ← ReportManager (update leaderboard)
└─────────────────┘
    │
    ▼
Result to User
```

**Usage**:
```python
from sure_eval import AutonomousEvaluator

evaluator = AutonomousEvaluator()

# Quick test
result = evaluator.quick_test(
    tool_name="asr_qwen3",
    dataset="aishell1",
    num_samples=10
)

print(f"Score: {result['score']}%")
print(f"RPS: {result['rps']}")  # vs SOTA
print(f"SOTA Model: {result['sota_model']}")
```

---

## Data Flow

### Evaluation Flow

```python
# 1. User initiates evaluation
result = evaluator.quick_test("asr_qwen3", "aishell1", num_samples=10)

# 2. Dataset normalization
raw_dataset = "aishell1"
normalized = "aishell1"  # Already normalized

# 3. Tool mapping
local_tool = "asr_qwen3"
benchmark_model = "Qwen3-ASR-1.7B"

# 4. SOTA lookup
sota_model = "Qwen3-Omni"  # Different from Qwen3-ASR-1.7B!
sota_score = 0.80  # CER

# 5. Evaluation
actual_score = 0.85  # CER achieved

# 6. RPS calculation
rps = sota_score / actual_score  # 0.80 / 0.85 = 0.94

# 7. Result assembly
result = {
    "tool": "asr_qwen3",
    "benchmark_model": "Qwen3-ASR-1.7B",
    "dataset": "aishell1",
    "score": 0.85,
    "sota_model": "Qwen3-Omni",
    "sota_score": 0.80,
    "rps": 0.94,
    "is_sota": False,  # 0.94 < 1.0
}
```

### Report Update Flow

```python
# After evaluation, automatically update reports

# 1. Record in evaluation database
rps_mgr.evaluate_and_record(
    tool_name="asr_qwen3",
    dataset="aishell1",
    score=0.85,
    rps=0.94,
)

# 2. Update model report (if new SOTA)
if rps >= 1.0:
    report_manager.update_result(
        model="Qwen3-ASR-1.7B",
        dataset="aishell1",
        score=0.85,
        is_sota=True,
    )
    
# 3. Regenerate leaderboard
report_manager.print_leaderboard("aishell1")
```

---

## Integration Points

### Component Integration Matrix

| Component | Uses | Provides |
|-----------|------|----------|
| DatasetManager | Config | Normalized datasets |
| ToolRegistry | Config, MCP | Tool execution |
| ModelRegistry | Filesystem | Model discovery |
| ModelMapping | Static maps | Identity translation |
| SUREEvaluator | - | Metrics computation |
| RPSManager | Config | RPS calculation |
| SOTAManager | YAML baselines | SOTA lookup |
| ReportManager | JSON reports | Leaderboards |
| AutonomousEvaluator | All above | End-to-end flow |

### Critical Integration Notes

1. **Dataset Name Normalization**
   - All components use normalized names (lowercase, no suffixes)
   - `CS_dialogue` → `cs_dialogue`
   - Ensures consistent baseline lookup

2. **Model Identity Mapping**
   - Local tools ≠ Benchmark models
   - `asr_qwen3` → `Qwen3-ASR-1.7B`
   - Required for correct SOTA comparison

3. **BLEU Metric Specification**
   - SURE uses **character-level BLEU** for S2TT
   - Different from standard word-level BLEU
   - Important for fair comparison

4. **Qwen3 Distinction**
   - `Qwen3-Omni`: Multi-task (Table 4)
   - `Qwen3-ASR-1.7B`: ASR-specific (Table 3)
   - Different SOTA results for each

---

## Usage Patterns

### Pattern 1: Evaluate New Model

```python
from sure_eval import AutonomousEvaluator

evaluator = AutonomousEvaluator()

# Evaluate on multiple datasets
for dataset in ["aishell1", "librispeech_clean"]:
    result = evaluator.quick_test(
        tool_name="my_new_model",
        dataset=dataset,
        num_samples=100,
    )
    print(f"{dataset}: RPS = {result['rps']:.2f}")
```

### Pattern 2: Compare with SOTA

```python
from sure_eval.reports import ReportManager

reports = ReportManager()

# Get SOTA for dataset
sota_model, sota_result = reports.get_sota_for_dataset("aishell1")
print(f"SOTA: {sota_model} with {sota_result.raw_score}% CER")

# Compare your model
comparison = reports.compare_models(
    ["Qwen3-Omni", "Kimi-Audio", "Gemini-3.0pro"],
    dataset="aishell1"
)
```

### Pattern 3: Generate Report

```python
from sure_eval.reports import ReportManager

reports = ReportManager()

# Generate Markdown report
markdown = reports.generate_markdown_report(
    output_path="reports/leaderboard.md"
)

# Print leaderboard
reports.print_leaderboard()  # Overall
reports.print_leaderboard("aishell1")  # Specific dataset
```

---

## Extension Guide

### Adding a New Model

1. **Create model directory**:
```bash
mkdir src/sure_eval/models/my_model
cd src/sure_eval/models/my_model
```

2. **Add pyproject.toml**:
```toml
[project]
name = "my-model"
dependencies = ["torch", "transformers"]
```

3. **Implement MCP server** (`server.py`):
```python
class MyModelServer:
    def transcribe(self, audio_path):
        # Your inference code
        return {"text": transcription}
```

4. **Register mapping** (`src/sure_eval/models/model_mapping.py`):
```python
TOOL_TO_BENCHMARK["my_model"] = "My-Model-Paper-Name"
```

5. **Setup environment**:
```bash
python demo/setup_model.py my_model
```

### Adding a New Dataset

1. **Update dataset mapping** (`dataset_manager.py`):
```python
CSV_DATASETS["my_dataset"] = {
    "config_name": "my_dataset",
    "task": "ASR",
    "language": "zh",
}
```

2. **Add SOTA baseline** (`reports/sota/sota_baseline.yaml`):
```yaml
my_dataset:
  metric: "cer"
  score: 5.0
  higher_is_better: false
  sota_model: "Some-Model"
```

3. **Update config** (`config/default.yaml`):
```yaml
my_dataset:
  name: "My Dataset"
  task: "ASR"
```

---

## Testing

### Run Integration Tests

```bash
# Test all components
python demo/demo_end_to_end.py

# Test reports
python demo/demo_reports.py --sota-summary
python demo/demo_reports.py --leaderboard aishell1

# Test evaluation (requires setup)
python demo/demo_evaluate_model.py -m asr_qwen3 -d aishell1 -n 10
```

### Expected Output

```
✅ All integration tests passed!
The SURE-EVAL framework is fully operational.
```

---

## References

- SURE Benchmark: https://www.modelscope.cn/datasets/SUREBenchmark
- MCP Protocol: https://modelcontextprotocol.io
- Architecture Audit: `ARCHITECTURE_AUDIT.md`
- Metrics Guide: `reports/METRICS_GUIDE.md`

---

## License

MIT License - See LICENSE file for details.
