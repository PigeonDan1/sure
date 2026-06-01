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

### Model-Local Checkpoint Rule

For local models, tool onboarding should prefer storing checkpoints and runtime
caches under the model directory itself, for example:

```text
src/sure_eval/models/<model>/
├── checkpoints/
└── .runtime/
```

This avoids a common failure mode where a model passes onboarding on one host
but later cannot find its downloaded checkpoint because the cache lived only in
a host-global location.

If host-global fallback is unavoidable, the workflow must record that choice in:

- `artifacts/build_plan.json`
- `artifacts/weights_manifest.json`

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
- 若模型需要下载权重，默认优先落到 model-local checkpoint/cache 路径；若不能做到，必须明确记录 fallback 理由与路径
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
  cache_policy: model_local_first
  local_dir_name: checkpoints

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

### Speaker Verification (SV)
| Model | Backend | Status | Notes |
|-------|---------|--------|-------|
| [wespeaker](wespeaker/) | pip | ✅ Ready | WeSpeaker English ResNet221 (with lazy-import patch) |

### Failed Attempts (Reference)

| Model | Task | Backend | Status | Reason |
|-------|------|---------|--------|--------|
| [parakeet_1_1b_rnnt_multilingual_asr](parakeet_1_1b_rnnt_multilingual_asr/) | ASR | docker | ❌ Failed | Docker backend issues |

**Total: 15 models** (13 passed, 2 failed) across ASR, SD, VAD, SV, Speech Enhancement, Music IR, VLM, and Utility tasks.

### Model Directory Structure

Each model directory contains:

```
model_name/
├── model.spec.yaml         # Model specification
├── model.py                # Wrapper implementation
├── server.py               # MCP server
├── config.yaml             # MCP configuration
├── pyproject.toml          # Dependencies
├── checkpoints/            # Preferred local checkpoint location
├── .runtime/               # Preferred local runtime/cache root
├── __init__.py             # Package exports
├── validate.py             # Local validation script (fixture → infer → metrics)
├── fixture/                # Test samples + ground truth for local evaluation
│   └── <task>/
│       └── <sub-task>/
│           ├── gt.jsonl
│           └── sample_*.wav
└── artifacts/              # Generated artifacts
    ├── backend_choice.json
    ├── build.log
    ├── validation.log
    ├── verdict.json
    ├── sample_output.json
    └── ...
```

---

### Fixture & Local Validation

每个模型目录应包含一个 `fixture/` 文件夹和一个 `validate.py` 脚本：

- **`fixture/<task>/<sub-task>/`** — 存放测试音频和对应的 `gt.jsonl`：
  - `gt.jsonl` 每行一条 JSON，格式：`{"id": 1, "key": "...", "audio": "sample_1.wav", "ground_truth": "..."}`
  - 音频文件与 `gt.jsonl` 放在同一目录下
  - **样本数量**：2–3 条最佳，**最多不超过 5 条**（控制验证耗时）
  - 数据来源优先从 `tests/fixtures/` 或 `data/datasets/` 中复制对应 task 的样本

- **`validate.py`** — 本地端到端验证脚本：
  - 自动发现 `fixture/` 下的所有 sub-task
  - 执行 import → load → infer → evaluate 完整流程
  - 调用 `SUREEvaluator` 计算指标（WER/CER/BLEU/Accuracy/DER 等）
  - 输出 `artifacts/validation.log` 和 `artifacts/sample_output.json`
  - 参考模板：[`templates/validate.py`](../../templates/validate.py)

**为什么需要 fixture + validate.py？**
- `VALIDATE_INFER` 阶段只需跑通最小推理，不检查准确率
- `validate.py` 在 onboarding 完成后提供**落地准确性验证**，确认模型在真实数据上的输出质量
- 为后续 Main Flow Agent 的 `SMOKE_TEST_UNIT` 提供 bounded test 的基础

---

## Docker Image Workflow

本地模型完成 onboarding 后，应制作独立 Docker 镜像，用于集群提交和跨机器复现。镜像只固化运行环境，代码、fixture、权重和输出目录在运行时用绝对路径挂载。这里没有要求把 `.runtime` 中的权重迁移到 `checkpoints/`。

### Image Naming

统一命名：

```text
docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_<model_name>:v1.0
```

同一模型每次更新递增 tag：

```text
v1.1, v1.2, v1.3, ...
```

示例：

```text
docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_asr_qwen3:v1.1
```

### Build

每个模型目录应提供 `docker_build.sh`：

```bash
cd /absolute/path/to/sure-eval
src/sure_eval/models/<model>/docker_build.sh
```

可覆盖镜像名和基础镜像：

```bash
IMAGE_TAG=docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_<model>:v1.1 \
BASE_IMAGE=<base-image> \
src/sure_eval/models/<model>/docker_build.sh
```

如果基础镜像在仓库中但本机不存在，先拉取：

```bash
docker pull docker.v2.aispeech.com/<namespace>/<base_image>:<tag>
```

维护 Dockerfile 时采用最小改动构建方式：

- 新增的系统包、Python 包或补丁尽量追加在 `Dockerfile` 尾部，避免重排已有层导致缓存失效。
- 使用 BuildKit 的 apt/pip cache mount 加速下载，例如 `RUN --mount=type=cache,target=/var/cache/apt ...` 和 `RUN --mount=type=cache,target=/root/.cache/pip ...`。
- 如果曾进入镜像内部临时 `pip install` 包，必须同步更新 `Dockerfile`，保证下一次构建和集群运行可复现。

本地调试无需公司镜像；集群运行任务必须使用公司仓库镜像。构建完成后检查、推送并重新拉取验证：

```bash
docker image inspect docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_<model>:v1.1
docker push docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_<model>:v1.1
docker pull docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_<model>:v1.1
```

仓库生效可能需要十几分钟。如果 `docker pull` 返回 `manifest unknown`，说明该 tag 当前不可从公司仓库拉取，不能作为集群任务镜像。

如果 agent 执行 `docker push` 返回 `请求失败，状态码：502` 或其他 registry/proxy 5xx，不要直接放弃。优先清除代理变量后重试：

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
  docker push docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_<model>:v1.1
```

如果清除代理后返回 `operation not permitted`，说明当前沙箱网络拦截了直连 registry，应请求非沙箱/完整网络权限后用同一条命令重试。如果 registry 返回 `镜像已存在,请更新tag`，说明 push 请求已经到达公司仓库，且当前 tag 不允许覆盖；此时递增 tag，例如 `v1.2`，重新 build/tag/push。

push 成功或 tag 已存在后，都必须再次 `docker pull` 验证。只有 pull 返回 digest / `Image is up to date`，才可将公司仓库镜像标记为可用于集群任务。若清除代理和权限重试仍失败，再请用户在登录态完整的交互终端手动执行 push/pull。

通用推送流程：

```bash
dockerfile=Dockerfile.$image
docker build -f $dockerfile -t $image .
docker tag $image $REPO/$image
docker push $REPO/$image
docker pull $REPO/$image
```

注意：`docker pull` 是从仓库拉到本机；上传本机镜像到仓库使用 `docker push`。

### Validate In Docker

每个模型目录应提供 `docker_validate.sh`。脚本必须：

- 使用宿主绝对路径挂载代码、fixture、权重和 artifacts
- 不挂载宿主 `.venv`
- 使用镜像内 Python，例如 `/opt/<model>_venv/bin/python`
- 若 `validate.py` 查找 `REPO_ROOT/.venv/bin/python`，在容器内创建链接：

```bash
ln -sfn /opt/<model>_venv /workspace/sure-eval/.venv
```

运行示例：

```bash
MODEL_DIR=/absolute/path/to/src/sure_eval/models/<model> \
MODELSCOPE_CACHE=/absolute/path/to/src/sure_eval/models/<model>/.runtime/modelscope_cache \
ARTIFACTS_DIR=/absolute/path/to/src/sure_eval/models/<model>/docker_artifacts \
/absolute/path/to/src/sure_eval/models/<model>/docker_validate.sh
```

### vc submit

提交到集群时不要依赖当前工作目录。所有环境变量和挂载路径必须使用绝对路径：

```bash
MODEL_DIR=/hpc_stor03/.../src/sure_eval/models/<model>
MODELSCOPE_CACHE=/hpc_stor03/.../src/sure_eval/models/<model>/.runtime/modelscope_cache
ARTIFACTS_DIR=/hpc_stor03/.../src/sure_eval/models/<model>/docker_artifacts
```

不要挂载本地 `.venv`。如果模型确实需要 API token 或 endpoint，再用绝对路径注入 `.env`：

```bash
--env-file /absolute/path/to/.env
```

### asr_qwen3 Reference

`asr_qwen3` 已验证：

```text
image: docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_asr_qwen3:v1.1
pull digest: sha256:3bbd9b21a0f7a0344a6cf32e8f32c30df929a2b3f82bda7237fa3567ba935bf3
overall: PASSED
en WER: 0.08771929824561403
zh CER: 0.0
artifacts: src/sure_eval/models/asr_qwen3/docker_artifacts/
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
    MODEL_PATH: "./checkpoints/model"
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
