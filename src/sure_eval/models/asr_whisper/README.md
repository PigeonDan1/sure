# ASR Whisper Model

Automatic Speech Recognition using OpenAI's Whisper model.

---

## ⚠️ 数据安全警告

**严禁删除以下文件：**
- 任何已提交到 git 的源代码
- `test_results/` - 他人测试结果
- `tests/fixtures/` - 共享测试数据

**如需清理空间，只能删除你自己创建的临时文件。**

---

## Model Information

| Attribute | Value |
|-----------|-------|
| **Name** | asr_whisper |
| **Task** | ASR |
| **Model** | openai/whisper-large-v3 |
| **Size** | 1.5B parameters |
| **Languages** | 99 languages |
| **License** | MIT |

## Status

✅ **Implemented** - Ready for evaluation

## Setup

```bash
# Create virtual environment
uv venv --python=python3.10

# Install dependencies
uv pip install openai-whisper torch numpy
```

Or use the setup script:

```bash
python demo/setup_model.py asr_whisper
```

## Usage

### Direct Model Usage

```python
from model import ASRWhisperModel

# Initialize model (will auto-download on first use)
model = ASRWhisperModel(model_path="large-v3")

# Transcribe audio
result = model.transcribe("audio.wav", language="zh")
print(result.text)
```

### MCP Server

```bash
# Start MCP server
.venv/bin/python server.py
```

## Available Models

| Model | Size | Parameters | English-only |
|-------|------|------------|--------------|
| tiny | 39 MB | 39 M | tiny.en |
| base | 74 MB | 74 M | base.en |
| small | 244 MB | 244 M | small.en |
| medium | 769 MB | 769 M | medium.en |
| large-v1 | 1.55 GB | 1550 M | N/A |
| large-v2 | 1.55 GB | 1550 M | N/A |
| large-v3 | 1.55 GB | 1550 M | N/A |

## Configuration

Edit `config.yaml` to change model:

```yaml
server:
  env:
    MODEL_PATH: "large-v3"  # Change to tiny, base, small, medium, large-v2, etc.
    DEVICE: "auto"  # auto, cuda, cpu
```

## Test Results

| Dataset | Date | WER | CER | RPS | Status |
|---------|------|-----|-----|-----|--------|
| aishell1 | - | - | - | - | Not tested |
| librispeech_clean | - | - | - | - | Not tested |

## References

- [Whisper GitHub](https://github.com/openai/whisper)
- [Whisper HuggingFace](https://huggingface.co/spaces/openai/whisper)
- [Whisper Paper](https://arxiv.org/abs/2212.04356)
