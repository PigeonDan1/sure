# Qwen3-Omni Agent 配置指南

## 工具信息

| 属性 | 值 |
|------|-----|
| **工具名** | qwen3_omni |
| **任务** | OMNI (文本+音频生成) |
| **部署模式** | API |
| **包管理** | UV |
| **模型ID** | qwen3-omni-flash |
| **提供商** | Alibaba Cloud (DashScope) |

---

## 快速配置

```bash
# Step 1: 进入目录
cd src/sure_eval/models/qwen3_omni

# Step 2: 运行自动配置
bash setup.sh

# Step 3: 设置 API Key
export DASHSCOPE_API_KEY="your-api-key"

# Step 4: 验证
.venv/bin/python -c "from model import Qwen3OmniModel; m=Qwen3OmniModel(); print(m.chat_text_only('Hello'))"
```

---

## 详细配置步骤

### 1. 验证服务可用性

```bash
# 检查 API 端点
export DASHSCOPE_API_KEY="your-api-key"
curl -s https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models \
  -H "Authorization: Bearer $DASHSCOPE_API_KEY" | head -c 200

# 期望返回: 模型列表 JSON
```

### 2. 环境配置

```bash
# 创建 UV 环境
uv venv --python=python3.10

# 安装依赖
uv pip install openai>=1.0.0 soundfile>=0.12.0 numpy>=1.24.0

# 验证安装
.venv/bin/python -c "import openai, soundfile, numpy; print('✓ All packages installed')"
```

### 3. 推理验证

```python
# test_inference.py
import os
os.environ['DASHSCOPE_API_KEY'] = 'your-api-key'

from model import Qwen3OmniModel

model = Qwen3OmniModel()

# 测试 1: 纯文本
response = model.chat_text_only("Who are you?")
print(f"Text response: {response}")

# 期望包含: "Alibaba Cloud", "Qwen"

# 测试 2: 带音频
result = model.chat(
    "Hello, how are you?",
    generate_audio=True,
    output_audio_path="/tmp/test_output.wav"
)
print(f"Audio saved to: {result.audio_path}")
```

---

## 验证清单

- [ ] API Key 有效（能获取模型列表）
- [ ] 环境创建成功（.venv 存在）
- [ ] 依赖安装完整（openai, soundfile, numpy）
- [ ] 模型可导入（`from model import Qwen3OmniModel` 成功）
- [ ] 文本推理正常（返回非空字符串）
- [ ] MCP 服务器可启动

---

## 故障排除

| 问题 | 解决方案 |
|------|----------|
| `401 Unauthorized` | API Key 无效或过期，需重新获取 |
| `ModuleNotFoundError: openai` | 运行 `uv pip install openai` |
| 音频保存失败 | 检查输出目录是否存在且有写权限 |

---

## API 参考

- **Base URL**: `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`
- **模型**: `qwen3-omni-flash`
- **语音选项**: `Cherry`
- **音频格式**: `wav` (24kHz)

---

## 模型映射

```python
from sure_eval.models.model_mapping import get_benchmark_name

# 验证注册
benchmark = get_benchmark_name("qwen3_omni")
assert benchmark == "Qwen3-Omni"
```
