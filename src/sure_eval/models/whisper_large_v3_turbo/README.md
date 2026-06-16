# Whisper large-v3-turbo Model

Automatic Speech Recognition using OpenAI's Whisper large-v3-turbo model.

## Model Information

| Attribute | Value |
|-----------|-------|
| **Name** | whisper_large_v3_turbo |
| **Task** | ASR (Automatic Speech Recognition) |
| **Model** | openai/whisper-large-v3-turbo |
| **Size** | ~1.6GB |
| **Languages** | 99 languages |
| **License** | MIT |
| **Source** | [ModelScope](https://modelscope.cn/models/iic/Whisper-large-v3-turbo) |

## Capabilities

- **ASR**: Transcribe speech to text
- **Language Detection**: Automatic language identification
- **Timestamps**: Segment-level timestamps with start/end times

## Environment Setup

```bash
# Setup virtual environment
./setup.sh

# Or manual setup
uv venv --python 3.11
source .venv/bin/activate
uv pip install -e .
```

## Test Results

### AISHELL-1 (Chinese ASR)

| Date | Samples | CER | RPS | Notes |
|------|---------|-----|-----|-------|
| 2025-05-20 | 7176 | 7.28% | 0.11 | Full evaluation; low RPS is expected given model design trade-offs on Chinese ASR |

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
  whisper_large_v3_turbo:
    name: "whisper_large_v3_turbo"
    command: [".venv/bin/python", "server.py"]
    working_dir: "."
    env:
      MODEL_ID: "turbo"
      WHISPER_DOWNLOAD_ROOT: "./checkpoints"
      DEVICE: "auto"
    timeout: 300
```

### Direct Usage

```python
from model import ModelWrapper

model = ModelWrapper()
result = model.predict("audio.wav")
print(result.text)
print(result.language)
```

## API Reference

### Tools

- `asr_transcribe(audio_path)`: Transcribe audio to text
- `transcribe_audio(audio_path)`: Backward-compatible alias for `asr_transcribe`
- `healthcheck()`: Return wrapper readiness and runtime metadata

## Files

- `server.py` - MCP server implementation
- `model.py` - Core model wrapper (`ModelWrapper`)
- `config.yaml` - MCP configuration
- `model.spec.yaml` - Model specification
- `pyproject.toml` - Python dependencies
- `setup.sh` - Environment setup script
- `validate.py` - Validation script
- `checkpoints/` - Model weights directory
- `eval_runs/` - Evaluation run artifacts

## Notes

- First inference is slow (model loading)
- GPU recommended but not required; falls back to CPU automatically
- Requires `ffmpeg` system package for audio preprocessing
- Supports both `fp16` (GPU) and `fp32` (CPU) inference

## See Also

- [Model README](../../../../docs/agents/model_tool_agent/README.md)
- [Evaluation Guide](../../../docs/evaluation.md)
