# SURE-EVAL Agent 工具管理指南

本文档指导 AI Agent 如何下载、配置、验证和管理 SURE-EVAL 中的模型工具。

---

## 目录

1. [工具分类与部署模式](#1-工具分类与部署模式)
2. [环境管理策略](#2-环境管理策略)
3. [工具配置流程](#3-工具配置流程)
4. [验证清单](#4-验证清单)
5. [故障排除](#5-故障排除)

---

## 1. 工具分类与部署模式

### 1.1 部署模式判断

| 模式 | 特征 | 示例工具 |
|------|------|----------|
| **API 模式** | 仅需 API Key，无需本地模型 | `qwen3_omni` |
| **本地部署** | 需要下载模型权重，本地推理 | `asr_qwen3`, `diarizen` |
| **混合模式** | 支持 API 或本地 | `asr_whisper` |

**判断方法**：
```bash
# 查看 config.yaml 中的 server 配置
cat src/sure_eval/models/{tool_name}/config.yaml | grep -A 5 "server:"
```

- 如果 `env` 中包含 `API_KEY` 相关 → **API 模式**
- 如果 `env` 中包含 `MODEL_PATH` → **本地部署模式**

### 1.2 工具清单

| 工具名 | 任务 | 部署模式 | 包管理 |
|--------|------|----------|--------|
| `asr_qwen3` | ASR | 本地 | UV |
| `asr_whisper` | ASR | 本地 | UV |
| `qwen3_omni` | OMNI | API | UV |
| `diarizen` | SD | 本地 | Conda |
| `s2tt_nllb` | S2TT | 本地 | UV |

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
```

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

echo "=== Setup complete ==="
```

### B. 模型注册检查

```python
# 检查工具是否已注册
from sure_eval.models.model_mapping import get_benchmark_name

benchmark = get_benchmark_name("{tool_name}")
print(f"Tool '{tool_name}' -> Benchmark '{benchmark}'")
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
