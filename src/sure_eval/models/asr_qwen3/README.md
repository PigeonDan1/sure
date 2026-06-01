# ASR Qwen3 Model

Automatic Speech Recognition using Alibaba's Qwen3-ASR-1.7B model.

## Model Information

| Attribute | Value |
|-----------|-------|
| **Name** | asr_qwen3 |
| **Task** | ASR (Automatic Speech Recognition) |
| **Model** | Qwen/Qwen3-ASR-1.7B |
| **Size** | 1.7B parameters (~3.2GB) |
| **Languages** | 52 languages (30 + 22 Chinese dialects) |
| **License** | Apache 2.0 |
| **Source** | [HuggingFace](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) |

## Capabilities

- **ASR**: Transcribe speech to text
- **Language Detection**: Automatic language identification
- **Timestamps**: Word-level timestamps (with forced aligner)

## Environment Setup

```bash
# Setup virtual environment
./setup.sh

# Or manual setup
cd /cpfs/user/jingpeng/workspace/sure-eval/src/sure_eval/models/asr_qwen3
uv venv --python 3.11
source .venv/bin/activate
uv pip install -e .
```

## Test Results

### AISHELL-1 (Chinese ASR)

| Date | Samples | WER | CER | RPS | Notes |
|------|---------|-----|-----|-----|-------|
| 2025-03-25 | 10 (quick test) | 0.79% | - | 101.60 | Initial test |

### LibriSpeech (English ASR)

| Date | Split | WER | RPS | Notes |
|------|-------|-----|-----|-------|
| TBD | test-clean | - | - | Pending |

### Other Datasets

| Dataset | Task | Status | Results |
|---------|------|--------|---------|
| AISHELL-5 | ASR (zh) | Not tested | - |
| KeSpeech | ASR (zh) | Not tested | - |
| CoVoST2 | S2TT | Not applicable | - |
| IEMOCAP | SER | Not applicable | - |

## Usage

### As MCP Server

```yaml
# config/mcp_tools.yaml
tools:
  asr_qwen3:
    name: "asr_qwen3"
    command: [".venv/bin/python", "server.py"]
    working_dir: "/cpfs/user/jingpeng/workspace/sure-eval/src/sure_eval/models/asr_qwen3"
    env:
      MODEL_PATH: "Qwen/Qwen3-ASR-1.7B"
      DEVICE: "auto"
    timeout: 300
```

### Direct Usage

```python
from sure_eval.models.asr_qwen3 import ASRQwen3Model

model = ASRQwen3Model()
result = model.transcribe("audio.wav", language="Chinese")
print(result.text)
```

## API Reference

### Tools

- `asr_transcribe(audio_path, language)`: Transcribe audio
- `asr_transcribe_with_timestamps(audio_path, language)`: Transcribe with timestamps

## Files

- `server.py` - MCP server implementation
- `model.py` - Core model wrapper
- `config.yaml` - MCP configuration
- `pyproject.toml` - Python dependencies
- `setup.sh` - Environment setup script
- `results/` - Test results directory

## Notes

- First inference is slow (model loading)
- GPU recommended but not required
- Supports batch processing

## See Also

- [Model README](../README.md)
- [Evaluation Guide](../../../docs/evaluation.md)
