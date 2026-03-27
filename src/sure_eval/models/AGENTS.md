# SURE-EVAL Agent 工具管理指南

本文档指导 AI Agent 如何下载、配置、验证和管理 SURE-EVAL 中的模型工具。请务必按照本文档逐步配置模型工具，不可跳跃步骤，出现问题优先查找此文档。

---

## ⚠️ 重要警告：数据删除规范

### 🚨 真实案例
> 曾有 AI Agent 误删 `tests/fixtures/` 目录下的标准化测试样本，导致评估流程中断，需要数小时重新准备数据。

### 🚫 禁止行为
**未经用户明确确认，严禁删除以下文件/目录：**
- `tests/fixtures/` - 测试样本数据（音频文件 + manifest）
- `data/datasets/` - 原始数据集
- `src/sure_eval/models/*/test_results/` - 他人测试结果
- 任何已提交到 git 的源代码

**严禁在系统级环境操作：**
- 🚫 **禁止**使用 `pip install` 直接安装到系统 Python（如 `/usr/bin/python` 或 `/usr/local/bin/python`）
- 🚫 **禁止**使用 `--break-system-packages` 参数强制安装到系统环境
- 🚫 **禁止**修改 `/usr/local/lib/python*/dist-packages` 下的任何文件
- ✅ **必须**在虚拟环境（UV 或 Conda）中进行所有依赖安装

> **警告**：系统级修改会影响整个服务器，可能导致其他项目无法运行。所有依赖必须隔离在各自模型的虚拟环境中。

### ✅ 允许删除的文件
**仅允许删除你自己在本次会话中创建的：**
- 临时测试脚本（`test_*.py`，且未提交）
- 临时缓存文件
- 你自己的模型推理输出

### 📋 删除前必须检查
```bash
# 1. 查看文件状态
git status --short

# 2. 理解标记含义
# ?? = 未跟踪的新文件（你自己创建的，可删除）
# M  = 已修改的文件（不要删除）
# A  = 已暂存的文件（不要删除）
# 无标记 = 已提交的文件（绝对不要删除）
```

**如果文件不是 `??` 状态，删除前必须获得用户明确许可。**

---

## 目录

1. [工具分类与部署模式](#1-工具分类与部署模式)
2. [环境管理策略](#2-环境管理策略)
3. [工具配置流程](#3-工具配置流程)
4. [验证清单](#4-验证清单)
5. [故障排除](#5-故障排除)
6. [测试框架](#6-测试框架)
7. [实践案例](#7-实践案例)
8. [复杂依赖配置黄金法则](#8-复杂依赖配置黄金法则) ⭐

---

## 1. 工具分类与部署模式

### 1.1 部署模式判断

| 模式 | 特征 | 示例工具 |
|------|------|----------|
| **API 模式** | 仅需 API Key，无需本地模型 | `qwen3_omni` |
| **本地部署** | 需要下载模型权重，本地推理 | `asr_qwen3`, `diarizen`, `asr_whisper` |
| **混合模式** | 支持 API 或本地 | `asr_whisper` |

**判断方法**：
```bash
# 查看 config.yaml 中的 server 配置
cat src/sure_eval/models/{tool_name}/config.yaml | grep -A 5 "server:"
```

- 如果 `env` 中包含 `API_KEY` 相关 → **API 模式**
- 如果 `env` 中包含 `MODEL_PATH` → **本地部署模式**

### 1.2 工具清单

| 工具名 | 任务 | 部署模式 | 包管理 | 状态 |
|--------|------|----------|--------|------|
| `asr_qwen3` | ASR | 本地 | UV | ✅ 已实现 |
| `asr_whisper` | ASR | 本地 | UV | ✅ 已实现 |
| `asr_parakeet` | ASR | 本地 | UV | ✅ 已实现 |
| `qwen3_omni` | OMNI | API | UV | ✅ 已实现 |
| `diarizen` | SD | 本地 | Conda | ✅ 已实现 |
| `s2tt_nllb` | S2TT | 本地 | UV | 🚧 模板 |

---

## 2. 环境管理策略

### 2.1 选择标准

| 条件 | 推荐工具 |
|------|----------|
| 纯 Python 依赖 | **UV** |
| 需要 CUDA/conda 特定版本 | **Conda** |
| 复杂 C++ 扩展 | **Conda** |
| 有 embedded submodule | **Conda** |

### 2.2 UV 环境（推荐默认）

```bash
# 1. 创建环境
cd src/sure_eval/models/{tool_name}
uv venv --python=python3.10

# 2. 安装依赖
uv pip install -r requirements.txt
# 或
uv pip install -e .

# 3. 验证
.venv/bin/python -c "import {package_name}; print('OK')"
```

### 2.3 Conda 环境（复杂依赖）

```bash
# 1. 创建环境
cd src/sure_eval/models/{tool_name}
conda env create -f environment.yml

# 2. 激活
conda activate {env_name}

# 3. 验证
python -c "import {package_name}; print('OK')"
```

---

## 3. 工具配置流程

### 3.1 API 模式工具配置

以 `qwen3_omni` 为例：

```bash
# Step 1: 验证模型/服务可用性
curl -I https://dashscope-intl.aliyuncs.com/compatible-mode/v1 \
  -H "Authorization: Bearer $DASHSCOPE_API_KEY" 2>/dev/null | head -1
# 期望: HTTP/2 200

# Step 2: 环境配置
cd src/sure_eval/models/qwen3_omni
uv venv --python=python3.10
uv pip install openai soundfile numpy

# Step 3: 配置 API Key
export DASHSCOPE_API_KEY="your-api-key"
# 或写入 config.yaml

# Step 4: 推理验证
.venv/bin/python -c "
from model import Qwen3OmniModel
model = Qwen3OmniModel()
print(model.chat_text_only('Hello'))
"
```

### 3.2 本地部署工具配置

以 `asr_qwen3` 为例：

```bash
# Step 1: 验证模型权重链接
MODEL_URL="https://huggingface.co/Qwen/Qwen3-ASR-1.7B"
curl -I $MODEL_URL 2>/dev/null | head -1
# 期望: HTTP/2 200

# Step 2: 环境配置
cd src/sure_eval/models/asr_qwen3
uv venv --python=python3.10
uv pip install -e .

# Step 3: 下载模型
# 方式 A: HuggingFace (推荐)
.venv/bin/huggingface-cli download Qwen/Qwen3-ASR-1.7B \
  --local-dir ./model_cache/Qwen3-ASR-1.7B

# 方式 B: ModelScope (国内)
.venv/bin/python -c "
from modelscope import snapshot_download
snapshot_download('qwen/Qwen3-ASR-1.7B', cache_dir='./model_cache')
"

# Step 4: 更新配置
export MODEL_PATH="./model_cache/Qwen3-ASR-1.7B"

# Step 5: 推理验证
.venv/bin/python -c "
from model import ASRQwen3Model
model = ASRQwen3Model()
result = model.transcribe('test_audio.wav')
print(result.text)
"
```

### 3.3 复杂依赖工具配置（Conda）

以 `diarizen` 为例：

```bash
# Step 1: 分析依赖复杂度
# - 有 embedded pyannote-audio submodule
# - 需要特定 PyTorch + CUDA 版本
# - 固定版本依赖多

# Step 2: Conda 环境
cd src/sure_eval/models/diarizen
conda env create -f environment.yml
conda activate diarizen

# Step 3: 验证 submodule
ls pyannote-audio/
# 期望: pyannote/ setup.py requirements.txt

# Step 4: 安装 editable 依赖
pip install -e pyannote-audio/
pip install -e .

# Step 5: 推理验证
python -c "
from diarizen.pipelines.inference import DiariZenPipeline
pipeline = DiariZenPipeline.from_pretrained('BUT-FIT/diarizen-wavlm-large-s80-md')
print('Model loaded successfully')
"
```

---

## 4. 验证清单

### 4.1 配置前验证

- [ ] **模型权重链接可访问**
  ```bash
  curl -I {model_url} | grep "200\|302"
  ```

- [ ] **API 服务端点可访问**（API 模式）
  ```bash
  curl -I {api_base_url} -H "Authorization: Bearer {api_key}"
  ```

- [ ] **依赖版本兼容性**
  ```bash
  # 检查 PyTorch + CUDA
  python -c "import torch; print(f'PyTorch: {torch.__version__}, CUDA: {torch.version.cuda}')"
  ```

### 4.2 配置中验证

- [ ] **环境创建成功**
  ```bash
  .venv/bin/python --version  # UV
  conda activate {env_name} && python --version  # Conda
  ```

- [ ] **关键依赖安装成功**
  ```bash
  .venv/bin/pip list | grep {key_package}
  ```

- [ ] **模型下载完整**
  ```bash
  # 检查文件大小
  du -sh {model_path}
  
  # 检查关键文件
  ls {model_path}/config.json
  ls {model_path}/pytorch_model.bin
  ```

### 4.3 配置后验证

- [ ] **模型可导入**
  ```bash
  .venv/bin/python -c "from model import {ModelClass}; print('OK')"
  ```

- [ ] **简单推理通过**
  ```bash
  # API 模式
  .venv/bin/python -c "
  from model import {ModelClass}
  model = {ModelClass}(api_key='test')
  result = model.inference('test_input')
  print(result)
  "
  
  # 本地模式（需要测试数据）
  .venv/bin/python -c "
  from model import {ModelClass}
  model = {ModelClass}()
  result = model.inference('path/to/test_input')
  print(result)
  "
  ```

- [ ] **MCP 服务器可启动**
  ```bash
  timeout 5 .venv/bin/python server.py <<< '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
  # 期望: {"jsonrpc":"2.0","id":1,"result":{...}}
  ```

---

## 5. 故障排除

### 5.1 常见错误

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| `401 Unauthorized` | API Key 无效 | 检查/更新 API Key |
| `404 Not Found` | 模型路径错误 | 检查 MODEL_PATH |
| `CUDA out of memory` | GPU 内存不足 | 减小 batch_size 或使用 CPU |
| `ModuleNotFoundError` | 依赖未安装 | 重新运行 pip install |
| `version conflict` | 依赖版本冲突 | 使用 Conda 替代 UV |
| `Connection timeout` | HuggingFace 下载慢 | 使用 ModelScope 镜像 |
| `CPU fallback warning` | CUDA 版本不匹配 | 更新驱动或接受 CPU 运行 |

### 5.2 调试命令

```bash
# 查看详细日志
DEBUG=1 .venv/bin/python server.py

# 测试模型加载时间
time .venv/bin/python -c "from model import {ModelClass}; m = {ModelClass}()"

# 检查依赖树
.venv/bin/pipdeptree

# 验证 CUDA 可用性
.venv/bin/python -c "import torch; print(f'CUDA: {torch.cuda.is_available()}')"

# 测试音频转写（使用 fixtures）
cd tests/fixtures
.venv/bin/python test_model.py --model {tool_name} --task {task_name}
```

### 5.3 超时处理策略

当命令执行可能超过 60 秒时（如下载模型、安装大型依赖比如torch等），**必须**使用后台任务：

```bash
# 推荐：使用后台任务（超时可达 24 小时）
cd src/sure_eval/models/{tool_name}
uv pip install nemo-toolkit[asr]  # 可能超时

# 替换为后台任务：
uv pip install nemo-toolkit[asr] &
# 或使用工具提供的后台执行功能
```

**超时重试策略：**
1. **首次超时**：加倍等待时长（60s → 120s）
2. **再次失败**：切换到后台任务模式
3. **两次失败后**：记录问题并退出，不要无限重试

**示例（模型下载）：**
```bash
# 第一次尝试（60s 超时）
timeout 60 huggingface-cli download nvidia/parakeet-tdt-0.6b-v2
# 如果失败，第二次尝试（120s 超时）
timeout 120 huggingface-cli download nvidia/parakeet-tdt-0.6b-v2
# 如果仍失败，使用后台任务
# 通过后台任务工具执行，设置 300s 或更长超时
```

---

## 6. 测试框架

### 6.1 测试样本结构

```
tests/fixtures/
├── ASR/              # 自动语音识别样本
│   ├── manifest.json # 样本清单
│   └── sample_*.wav  # 测试音频
├── S2TT/             # 语音翻译样本
├── SER/              # 语音情感识别样本
├── GR/               # 性别识别样本
├── SLU/             # 口语理解样本
└── test_model.py    # 统一测试脚本
```

### 6.2 生成测试样本

从 SURE Benchmark 抽取标准化测试样本：

```bash
# 使用默认配置（每个数据集3个样本）
python scripts/extract_test_samples.py

# 自定义样本数
python scripts/extract_test_samples.py --samples 5 --output-dir tests/fixtures
```

样本抽取规则：
- 每个任务类型至少包含 2-3 个不同数据集
- 覆盖中英文（必要时包括代码切换）
- 样本时长适中（3-10秒为宜）

### 6.3 运行模型测试

```bash
cd tests/fixtures

# 测试特定任务
python test_model.py --model asr_whisper --task ASR

# 测试所有支持的任务
python test_model.py --model asr_qwen3 --task all

# 保存详细结果
python test_model.py --model asr_whisper --task ASR --output results.json
```

### 6.4 人工评测流程

1. **准备测试数据**
   ```bash
   python scripts/extract_test_samples.py --samples 5
   ```

2. **运行模型推理**
   ```bash
   python test_model.py --model {your_model} --task {task} --output predictions.json
   ```

3. **人工对比结果**
   - 查看 `predictions.json` 中的 `ground_truth` vs `prediction`
   - 记录错误类型（同音字、形近字、语义错误等）
   - 计算准确率/CER/WER

4. **更新模型文档**
   - 在 `src/sure_eval/models/{model}/README.md` 中记录评测结果
   - 更新 `config.yaml` 中的 `results` 字段

---

## 7. 实践案例

### 案例 1: 添加 Whisper 模型

**背景**: 为 SURE-EVAL 添加 OpenAI Whisper ASR 模型支持。

**步骤**:

1. **分析模型特点**
   - 部署模式: 本地部署
   - 依赖: PyTorch + openai-whisper
   - 模型大小: large-v3 (1.5B参数, 约3GB)
   - 支持语言: 99种语言（包括中文）

2. **环境配置**
   ```bash
   cd src/sure_eval/models/asr_whisper
   uv venv --python=python3.10
   uv pip install openai-whisper torch numpy
   ```

3. **实现模型包装器** (`model.py`)
   ```python
   class ASRWhisperModel:
       def __init__(self, model_path="large-v3", device="auto"):
           self.model_path = model_path
           self.device = device
           self._model = None
       
       def _load_model(self):
           if self._model is None:
               import whisper
               self._model = whisper.load_model(self.model_path)
       
       def transcribe(self, audio_path, language=None):
           self._load_model()
           result = self._model.transcribe(audio_path, language=language)
           return TranscriptionResult(text=result["text"])
   ```

4. **实现 MCP 服务器** (`server.py`)
   - 参照 `asr_qwen3/server.py` 结构
   - 实现 `tools/call` 处理 `asr_transcribe`

5. **注册模型映射**
   ```python
   # src/sure_eval/models/model_mapping.py
   TOOL_TO_BENCHMARK["asr_whisper"] = "Whisper-large-v3"
   ```

6. **测试验证**
   ```bash
   # 单元测试
   .venv/bin/python -c "from model import ASRWhisperModel; m = ASRWhisperModel()"
   
   # MCP 测试
   timeout 5 .venv/bin/python server.py <<< '{"jsonrpc":"2.0","id":1,"method":"initialize"}'
   
   # 实际转写测试
   python test_model.py --model asr_whisper --task ASR
   ```

**遇到的问题与解决**:

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| 模型下载超时 (300s) | large-v3 模型 2.88GB，网络慢 | 使用后台任务 + 超时重试 |
| CUDA 驱动版本过旧 | 系统 CUDA 12.2，PyTorch 要求更高 | 自动回退到 CPU 模式 |
| FP16 不支持 CPU | Whisper 默认使用 FP16 | 使用 FP32（自动处理） |
| 中文同音字错误 | Whisper 对中文方言/口音敏感 | 记录为已知问题，建议使用 Qwen3-ASR |

**评测结果**:

| 数据集 | CER | 主要错误类型 |
|--------|-----|--------------|
| KeSpeech | 高 | 同音字、形近字 |
| AISHELL-1 | 中 | 口音适配问题 |

**结论**: Whisper 适合多语言通用场景，中文场景建议使用专门的 Qwen3-ASR。

### 案例 2: 添加 Parakeet 模型

**背景**: 为 SURE-EVAL 添加 NVIDIA Parakeet-TDT-0.6B-v2 英文 ASR 模型。

**模型特点**:
- 部署模式: 本地部署
- 依赖: PyTorch + NVIDIA NeMo toolkit
- 模型大小: 0.6B 参数 (约 1.2GB)
- 支持语言: 仅英语
- 特性: 时间戳、标点符号、自动大小写

**配置步骤**:

```bash
# Step 1: 验证模型链接
curl -I https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2

# Step 2: 创建环境（严格遵守 UV 环境）
cd src/sure_eval/models/asr_parakeet
uv venv --python=python3.10
source .venv/bin/activate

# Step 3: 安装依赖（PyTorch 必须先于 NeMo）
uv pip install torch>=2.0.0 torchaudio>=2.0.0
# 注意：如果系统已有 PyTorch，避免重复安装，可使用 PYTHONPATH
uv pip install nemo-toolkit[asr]>=2.0 soundfile

# Step 4: 验证安装
.venv/bin/python -c "import nemo.collections.asr; print('OK')"

# Step 5: 测试推理
.venv/bin/python -c "
from model import ASRParakeetModel
model = ASRParakeetModel(device='cpu')
result = model.transcribe('tests/fixtures/librispeech/sample_1.wav')
print(result.text)
"
```

**遇到的问题与解决**:

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| 虚拟环境无法导入系统 PyTorch | UV 环境隔离 | 在 `.pth` 文件中添加系统路径，或设置 `PYTHONPATH` |
| NeMo 版本与 PyTorch 不匹配 | torchvision 版本冲突 | 降级 torchvision 到兼容版本 |
| 模型下载超时 | 首次下载 1.2GB | 使用后台任务，设置 300s 超时 |
| 系统环境被破坏 | 误用 `pip install` 修改系统包 | **严禁**系统级安装，必须隔离在虚拟环境 |

**关键教训**:
- **严格遵守**"不动系统环境"原则
- PyTorch 必须在 NeMo 之前安装
- 首次模型下载需要较长超时

### 案例 3: 处理路径不一致问题

**问题**: IEMOCAP 数据集的音频路径在 jsonl 和实际文件系统中不一致。

**jsonl 中的路径**:
```json
{"path": "IEMOCAP_test/Ses05F_impro01/Ses05F_impro01_F000.wav"}
```

**实际路径**:
```
IEMOCAP_test/wav/Ses05F_impro01/Ses05F_impro01_F000.wav
```

**解决方案**:

在 `extract_test_samples.py` 中实现智能路径查找：

```python
def find_audio_file(audio_base: Path, sample: dict) -> Path | None:
    # 1. 尝试直接路径
    direct_path = audio_base / sample["path"]
    if direct_path.exists():
        return direct_path
    
    # 2. IEMOCAP 特殊处理
    if "IEMOCAP" in sample.get("dataset", ""):
        wav_path = audio_base / "IEMOCAP_test/wav" / (sample["key"] + ".wav")
        if wav_path.exists():
            return wav_path
        
        # 3. 深度搜索
        search_dir = audio_base / "IEMOCAP_test"
        for wav_file in search_dir.rglob(f"{sample['key']}.wav"):
            return wav_file
    
    return None
```

**经验总结**:
- 抽取样本时打印警告信息，便于发现路径问题
- 对已知数据集实现特殊处理逻辑
- 保留 `original_path` 字段用于调试

---

## 附录

### A. 快速配置脚本模板

```bash
#!/bin/bash
# setup_{tool_name}.sh

set -e

echo "=== Setting up {tool_name} ==="

# 1. 验证前置条件
command -v uv >/dev/null 2>&1 || { echo "UV required"; exit 1; }

# 2. 创建环境
cd src/sure_eval/models/{tool_name}
uv venv --python=python3.10

# 3. 安装依赖
uv pip install -r requirements.txt

# 4. 下载模型（如果需要）
if [ ! -d "model_cache" ]; then
    .venv/bin/huggingface-cli download {model_id} --local-dir ./model_cache
fi

# 5. 验证
echo "Testing import..."
.venv/bin/python -c "from model import {ModelClass}; print('✓ OK')"

# 6. 运行 fixtures 测试
echo "Testing on fixtures..."
cd tests/fixtures
.venv/bin/python test_model.py --model {tool_name} --task {task}

echo "=== Setup complete ==="
```

### B. 模型注册检查

```python
# 检查工具是否已注册
from sure_eval.models.model_mapping import get_benchmark_name

benchmark = get_benchmark_name("{tool_name}")
print(f"Tool '{tool_name}' -> Benchmark '{benchmark}'")

# 检查模型实现状态
from sure_eval.models.registry import ModelRegistry

registry = ModelRegistry()
info = registry.get_model("{tool_name}")
print(f"Implemented: {info.is_implemented}")
```

### C. 环境变量模板

```bash
# .env 文件模板
# API 模式
DASHSCOPE_API_KEY=your-api-key

# 本地模式
MODEL_PATH=/path/to/model
DEVICE=cuda  # or cpu

# 通用
HF_HOME=/path/to/huggingface/cache
PYTHONPATH=src/
```

### D. 添加新模型检查清单

- [ ] 创建模型目录 `src/sure_eval/models/{tool_name}/`
- [ ] 创建 `pyproject.toml` (UV) 或 `environment.yml` (Conda)
- [ ] 创建 `config.yaml` 配置
- [ ] 实现 `model.py` 包装器
- [ ] 实现 `server.py` MCP 服务器
- [ ] 创建 `README.md` 文档
- [ ] 注册到 `model_mapping.py`
- [ ] 运行 fixtures 测试
- [ ] 更新本 AGENTS.md 文档

---

## 8. 复杂依赖配置黄金法则 ⭐

> 基于 DiariZen 配置的实战经验总结，按重要性排序。
> 违反这些规则将导致数小时的调试时间。

### 8.1 法则一：永远不要动源代码（最重要）

**原则**：永远不要修改项目源码或子模块代码，即使看起来有个明显的 bug。

**案例：DiariZen 的教训**
- 短音频 (<16s) 触发 `UnboundLocalError` 于 pyannote.audio
- 错误想法：直接修改 `inference.py` 修复
- 正确做法：使用满足条件的音频文件（>= 30s）

**理由**：
1. **子模块可能是修改版**：DiariZen 的 pyannote-audio 是定制版本，与 PyPI 不同
2. **修改会破坏一致性**：一处修改可能导致其他依赖出问题
3. **升级困难**：修改后的代码在更新时会产生冲突
4. **难以复现**：他人按 README 安装后无法复现你的修改

**替代方案优先级**：
1. 检查安装步骤是否正确
2. 检查版本是否匹配
3. 使用 workaround（如音频长度要求）
4. 查阅官方 issues
5. **最后手段**：fork 并维护分支（而非直接修改）

---

### 8.2 法则二：必须严格按 README 逐步安装

**原则**：安装步骤必须严格按照官方文档的顺序执行，不得跳过或调整。

**案例：DiariZen 的正确安装顺序**
```bash
# ✅ 正确顺序
conda create -n diarizen python=3.10 -y
conda activate diarizen
pip install torch==2.4.0 torchaudio==2.4.0 --index-url https://download.pytorch.org/whl/cpu
cd diarizen_src
pip install -e .
cd pyannote-audio && pip install -e .  # 【关键】必须从子模块安装
pip install numpy==1.26.4 psutil accelerate
```

**禁止行为**：
| 错误行为 | 后果 | 正确做法 |
|----------|------|----------|
| `pip install pyannote.audio` (PyPI) | `'DiariZenPipeline' object has no attribute '_segmentation_model'` | `cd pyannote-audio && pip install -e .` |
| 先装 DiariZen 再装 PyTorch | 依赖解析冲突 | 先 PyTorch 后 DiariZen |
| 跳过 `pip install -e .` | 模块找不到 | 严格使用 editable 安装 |
| NumPy 未锁定 1.26.4 | `numpy._core.multiarray` 错误 | `pip install numpy==1.26.4` |

---

### 8.3 法则三：配置前必须分析可能的冲突

**原则**：在开始安装前，必须分析所有关键依赖的版本冲突可能性。

**冲突分析检查清单**：

#### 1. Python 版本矩阵
| 组件 | 最低版本 | 推荐版本 |
|------|----------|----------|
| DiariZen | 3.10 | 3.10 |
| pyannote.audio | 3.10 | 3.10 |

#### 2. PyTorch 兼容性链
```
PyTorch 2.4.0
    ├── 支持 NumPy 1.x ✅
    ├── 支持 NumPy 2.x ✅
    └── 与 DiariZen 兼容 ✅
    
决策：使用 PyTorch 2.4.0（最新稳定版）
```

#### 3. NumPy 版本锁定（关键决策点）
```
DiariZen requirements.txt → numpy==1.26.4
                 ↓
    pyannote.audio 子模块依赖 NumPy 1.x API
                 ↓
    必须严格锁定 1.26.4，不能升级到 2.x
```

#### 4. pyannote.audio 来源决策树（最关键）
```
项目是否包含 pyannote-audio 子模块？
    ├── 是 → 必须使用子模块版本
    │         ├── 检查子模块是否为空
    │         │   └── git submodule update --init --recursive
    │         └── 安装：pip install -e ./pyannote-audio
    │             【绝对不能】pip install pyannote.audio
    └── 否 → 可以使用 PyPI 版本
              └── pip install pyannote.audio=={version}
```

**DiariZen 的关键发现**：
- PyPI 的 pyannote.audio 3.1.1 与 DiariZen 的 `DiariZenPipeline` 不兼容
- 子模块中的 pyannote.audio 是修改版，API 不同
- **这是 4 小时调试的根本原因**

---

### 8.4 法则四：冲突发生时首先反思步骤跳跃

**原则**：遇到错误时，首先怀疑安装步骤是否有跳跃或顺序错误，而不是依赖本身有问题。

**排查流程**：
```
发生错误
    ↓
【第一步】是否严格按 README 顺序安装？
    ├── 否 → 重新安装，严格按照顺序
    │         └── 80% 问题解决 ✅
    ↓
【第二步】是否使用了子模块的依赖？
    ├── 否 → 卸载 PyPI 版本，安装子模块
    │         └── 15% 问题解决 ✅
    ↓
【第三步】版本是否严格匹配？
    ├── 否 → 锁定版本（如 numpy==1.26.4）
    │         └── 4% 问题解决 ✅
    ↓
【第四步】是否还有其他步骤遗漏？
    ├── 是 → 补全步骤
    │         └── 0.9% 问题解决 ✅
    ↓
【最后】才考虑代码 bug 或依赖冲突
              └── 0.1% 需要深入调试
```

**常见跳跃错误与症状**：

| 跳跃行为 | 错误症状 | 修复方法 |
|----------|----------|----------|
| 未装子模块 pyannote | `'DiariZenPipeline' object has no attribute '_segmentation_model'` | `cd pyannote-audio && pip install -e .` |
| NumPy 未锁定 | `numpy._core.multiarray` 错误 | `pip install numpy==1.26.4` |
| PyTorch 后装 | 依赖冲突警告 | 重新创建环境，先装 PyTorch |
| 未初始化子模块 | 空 pyannote-audio 目录 | `git submodule update --init --recursive` |
| 短音频测试 | `UnboundLocalError: cannot access local variable 'segmentations'` | 使用 >= 30s 音频 |

---

### 8.5 法则五：全局最优依赖决策

**原则**：当冲突无法避免时，以最少修改实现全局最优解。

**决策优先级金字塔**：
```
                    冲突解决优先级
                    
         ┌─────────────────────────────┐
         │   1. 子模块完整性（最高）    │
         │      不可妥协               │
         │      例：pyannote.audio     │
         └─────────────┬───────────────┘
                       ↓
         ┌─────────────────────────────┐
         │   2. 严格版本锁定           │
         │      必须精确匹配           │
         │      例：numpy==1.26.4     │
         └─────────────┬───────────────┘
                       ↓
         ┌─────────────────────────────┐
         │   3. 主框架版本             │
         │      选择最新兼容版         │
         │      例：PyTorch 2.4.0     │
         └─────────────┬───────────────┘
                       ↓
         ┌─────────────────────────────┐
         │   4. 辅助依赖               │
         │      满足最低要求           │
         │      例：psutil, accelerate │
         └─────────────────────────────┘
```

**决策案例 1：pyannote.audio 冲突**

**场景**：PyPI 的 pyannote.audio 3.1.1 与 DiariZen 子模块 API 不兼容

| 方案 | 修改内容 | 风险 | 全局最优 |
|------|----------|------|----------|
| A | 修改 DiariZen 适配 PyPI 版 | 破坏逻辑，升级困难 | ❌ |
| B | 强制降级 PyPI 版到 3.0.x | 可能与其他依赖冲突 | ❌ |
| C | **使用子模块版本** | 无代码修改，官方推荐 | ✅ |

**决策**：方案 C（使用子模块）
- 无需修改任何代码
- 与官方环境完全一致
- 可复现、可维护
- 符合法则一（不动源代码）

**决策案例 2：NumPy 版本冲突**

**场景**：DiariZen 需要 NumPy 1.26.4，但某些工具想要 NumPy 2.x

```
方案 A: 升级到 NumPy 2.x，修改 DiariZen 适配
    → 需要修改大量代码
    → 违反法则一
    → 需要 fork 维护
    → ❌

方案 B: 锁定 NumPy 1.26.4，验证其他工具兼容性
    → PyTorch 2.4 支持 NumPy 1.x ✅
    → 无需修改代码
    → 符合 DiariZen 官方要求
    → ✅
```

**决策**：方案 B（锁定版本）
- PyTorch 2.4 同时支持 NumPy 1.x 和 2.x
- 保持 NumPy 1.26.4 不影响其他组件
- 全局最优解

---

### 8.6 DiariZen 完整配置总结

**配置时间线**：
| 阶段 | 耗时 | 关键问题 |
|------|------|----------|
| 环境准备 | 30min | 未按官方顺序安装 |
| 依赖冲突解决 | 2h+ | pyannote 子模块与 PyPI 版本不兼容 |
| 模型测试 | 30min | 短音频触发 pyannote bug |
| 最终验证 | 15min | 成功运行，4 speakers 正确识别 |
| **总计** | **~4小时** | |

**关键依赖矩阵**：
| Package | Source | Version | 决策理由 |
|---------|--------|---------|----------|
| pyannote.audio | **Submodule** | embedded | 必须使用修改版 |
| numpy | PyPI | **1.26.4** | pyannote 子模块要求 |
| torch | PyPI | 2.4.0 | 兼容 NumPy 1.x |
| python | conda | 3.10 | 最低版本要求 |

**验证结果**：
```
✓ Diarization completed
  Detected 4 speakers
  13 segments
Model is ready for SURE evaluation.
```

---

### 8.7 黄金法则速查卡

```
┌─────────────────────────────────────────────────────────┐
│                      黄金法则                            │
├─────────────────────────────────────────────────────────┤
│  1. 不动代码 → 2. 严格按序 → 3. 预判冲突 →             │
│  4. 反思跳跃 → 5. 全局最优                              │
└─────────────────────────────────────────────────────────┘
```

**违反代价**：
| 违反规则 | 代价 |
|----------|------|
| 规则一（动代码） | 数小时调试 + 维护噩梦 |
| 规则二（乱序安装） | 环境混乱 + 无法复现 |
| 规则三（不预判） | 盲目试错 + 时间浪费 |
| 规则四（不反思） | 错误方向 + 无用功 |
| 规则五（局部修复） | 全局问题 + 连锁反应 |

**遵循规则，一次成功。**
