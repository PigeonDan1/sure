# SURE-EVAL Model Onboarding

This directory contains the **Harness-First Agent Workflow** for automated tool/model environment configuration, plus all onboarded models.

---

## 🤖 Agent Workflow for Tool/Model Onboarding

SURE-EVAL provides a complete agent workflow that automates the entire process of configuring and validating audio processing tools.

### How It Works

1. **Initial Prompt** → Defines the agent's role and workflow
2. **MODEL_INPUT** → Specifies the model/tool to onboard
3. **Automated Execution** → Agent runs the complete pipeline
4. **Artifact Generation** → All results saved as structured files

### Usage

#### Step 1: Initial Prompt

Use this as the system prompt for your AI agent:

```text
cd /path/to/sure-eval
你现在扮演 SURE-EVAL 的模型接入执行代理。你的任务不是做开放式探索，而是严格按照仓库中定义的 harness-first 工作流，完成一个模型的第一阶段 onboarding。

你必须遵守以下文档：
1. src/sure_eval/models/AGENTS.md
2. docs/policies/constitution.md
3. docs/policies/evidence_priority.md
4. docs/policies/backend_selection.md
5. docs/policies/retry_and_escalation.md
6. docs/policies/phase1_target_policy.md
7. docs/contracts/spec_validation.md
8. docs/contracts/minimal_validation.md
9. docs/specs/wrapper_contract.md
10. docs/contracts/fixture_policy.md

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

#### Step 2: MODEL_INPUT Format

```yaml
model_id: owner/model-name
model_name: ModelName
task_type: asr|s2tt|sd|ser|speech_enhancement|...
deployment_type: local|api

repo:
  url: https://github.com/owner/repo
  commit: null  # or specific commit hash

weights:
  source: huggingface|pip|release_or_pypi
  local_path: null
  required: true

environment_hint:
  preferred_backend: uv|pixi|docker
  python_version: "3.10"
  requires_gpu: true|false
  system_packages: [ffmpeg, libsndfile1]

phase1_runtime_target:
  Validate the minimal callable path only:
  - confirm package is importable
  - load model with minimal config
  - run inference on fixture
  This phase does NOT require accuracy evaluation or production validation.

entrypoints:
  import_test: "import package"
  load_test: "model = package.load_model('tiny', 'cpu')"
  infer_test: "model.transcribe('tests/fixtures/shared/asr/en_16k.wav')"

fixture:
  audio: tests/fixtures/shared/asr/en_16k.wav
  task_specific: true|false
  fallback_allowed: true|false

io_contract:
  input_type: audio_path|text|json
  output_type: json|text
  primary_field: text|segments|labels
  required_fields: [field1, field2]
  nonempty_fields: [field1]
  json_serializable: true
```

> 💡 **Recommended Agents**: Claude Code (Opus) for complex cases, Codex GPT-5.4 for repo analysis. Avoid agents with 60s timeout limits for large installations.

#### Step 3: Run

Send Initial Prompt + MODEL_INPUT to your AI agent. The agent will automatically:
- Discover repository structure
- Select appropriate backend
- Build isolated environment
- Validate import/load/infer/contract
- Generate wrapper files
- Save all artifacts

---

## Configured Models

Models successfully onboarded via Agent Workflow:

### Speech Recognition (ASR)
| Model | Backend | Status | Notes |
|-------|---------|--------|-------|
| [whisper_large_v3_turbo](whisper_large_v3_turbo/) | uv | ✅ Ready | OpenAI Whisper Large V3 Turbo |
| [asr_qwen3](asr_qwen3/) | uv | ✅ Ready | Qwen3-ASR-1.7B (Chinese/English) |
| [asr_whisper](asr_whisper/) | uv | ✅ Ready | OpenAI Whisper base |
| [asr_parakeet](asr_parakeet/) | uv | ✅ Ready | NVIDIA Parakeet CTC |
| [parakeet_rnnt_1_1b](parakeet_rnnt_1_1b/) | uv | ✅ Ready | NVIDIA Parakeet RNNT 1.1B |
| [whisperx](whisperx/) | uv | ✅ Ready | Whisper + alignment + diarization |

### Speech Enhancement & Audio Processing
| Model | Backend | Status | Notes |
|-------|---------|--------|-------|
| [deepfilternet](deepfilternet/) | uv | ✅ Ready | DeepFilterNet2 noise suppression |
| [ffmpeg](ffmpeg/) | uv | ✅ Ready | Audio processing utility |
| [librosa](librosa/) | uv | ✅ Ready | Music feature extraction |

### Voice Activity Detection (VAD)
| Model | Backend | Status | Notes |
|-------|---------|--------|-------|
| [fireredvad](fireredvad/) | conda | ✅ Ready | SOTA industrial VAD/AED (97.57% F1) |
| [snakers4_silero-vad](snakers4_silero-vad/) | uv | ✅ Ready | Silero VAD |

### Speaker Tasks
| Model | Backend | Status | Notes |
|-------|---------|--------|-------|
| [diarizen](diarizen/) | conda | ✅ Ready | Speaker diarization (WavLM-based) |

### Vision-Language (VLM)
| Model | Backend | Status | Notes |
|-------|---------|--------|-------|
| [qwen2_vl](qwen2_vl/) | conda | ✅ Ready | Qwen2-VL-2B visual understanding |

### API-Based Models
| Model | Backend | Status | Notes |
|-------|---------|--------|-------|
| [qwen3_omni](qwen3_omni/) | API | ✅ Ready | Qwen3-Omni multimodal API |

---

### Failed Attempts (Reference)

| Model | Task | Backend | Status | Reason |
|-------|------|---------|--------|--------|
| [parakeet_1_1b_rnnt_multilingual_asr](parakeet_1_1b_rnnt_multilingual_asr/) | ASR | docker | ❌ Failed | Docker backend issues |
| [wespeaker](wespeaker/) | SV | pip | ❌ Failed | Import chain issues (eager frontend deps) |

**Total: 15 models** (12 passed, 3 failed) across ASR, SD, VAD, Speech Enhancement, Music IR, VLM, and Utility tasks.

### Model Directory Structure

Each model directory contains:

```
model_name/
├── model.spec.yaml         # Model specification
├── model.py                # Wrapper implementation
├── server.py               # MCP server
├── config.yaml             # MCP configuration
├── pyproject.toml          # Dependencies
├── __init__.py             # Package exports
└── artifacts/              # Generated artifacts
    ├── backend_choice.json
    ├── build.log
    ├── validation.log
    ├── verdict.json
    └── ...
```

---

## Manual Model Development

If you prefer manual development over agent workflow:

### Required Files

1. **README.md** - Model documentation
2. **config.yaml** - MCP configuration
3. **model.py** - Core model wrapper
4. **server.py** - MCP server
5. **pyproject.toml** - Python dependencies
6. **setup.sh** - Environment setup
7. **__init__.py** - Package exports

### Example config.yaml

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

### Test Your Model

```python
from sure_eval import AutonomousEvaluator

evaluator = AutonomousEvaluator()
result = evaluator.quick_test("my_model", "aishell1", num_samples=10)
print(f"WER: {result['score']:.2f}%, RPS: {result['rps']:.2f}")
```

---

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

# Generate MCP config
yaml_content = registry.generate_mcp_tools_yaml()
```

---

## See Also

- [Agent Policies](../../docs/policies/) - Constitution, evidence priority, backend selection
- [Validation Contracts](../../docs/contracts/) - Spec validation, minimal validation
- [Architecture Guide](../../ARCHITECTURE.md) - System design
