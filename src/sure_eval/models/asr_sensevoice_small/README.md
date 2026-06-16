# ASR SenseVoice Small Model

Automatic Speech Recognition using Alibaba's SenseVoice Small model via FunASR.

## Model Information

| Attribute | Value |
|-----------|-------|
| **Name** | asr_sensevoice_small |
| **Task** | ASR (Automatic Speech Recognition) |
| **Model** | iic/SenseVoiceSmall |
| **Size** | ~0.9GB |
| **Languages** | Chinese (zh), English (en), Japanese (ja), Korean (ko), Cantonese (yue) |
| **License** | Apache 2.0 |
| **Source** | [ModelScope](https://modelscope.cn/models/iic/SenseVoiceSmall) |

## Capabilities

- **ASR**: Transcribe speech to text
- **Language Detection**: Automatic language identification via output tags
- **Rich Output**: Returns raw FunASR result list with metadata

## Environment Setup

```bash
# Manual setup
uv venv --python 3.11
source .venv/bin/activate
uv pip install -e .
```

## Test Results

### LibriSpeech (English ASR)

| Date | Split | Samples | WER | RPS | Notes |
|------|-------|---------|-----|-----|-------|
| 2025-05-28 | test-clean | 2619 | 3.21% | 0.53 | Completed on pdgpu-2080ti-dsp |

### AISHELL-1 (Chinese ASR)

| Date | Samples | WER | CER | RPS | Notes |
|------|---------|-----|-----|-----|-------|
| TBD | - | - | - | - | Pending |

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
  asr_sensevoice_small:
    name: "asr_sensevoice_small"
    command: [".venv/bin/python", "server.py"]
    working_dir: "."
    env:
      MODEL_ID: "iic/SenseVoiceSmall"
      SENSEVOICE_MODEL_PATH: ".runtime/modelscope_cache/models/iic/SenseVoiceSmall"
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
- `healthcheck()`: Return wrapper readiness and runtime metadata

## Files

- `server.py` - MCP server implementation
- `model.py` - Core model wrapper (`ModelWrapper`)
- `config.yaml` - MCP configuration
- `model.spec.yaml` - Model specification
- `pyproject.toml` - Python dependencies
- `validate.py` - Validation script
- `checkpoints/` - Model weights directory
- `eval_runs/` - Evaluation run artifacts

## Notes

- First inference is slow (model loading)
- GPU recommended but not required; falls back to CPU automatically
- Requires `ffmpeg` system package for audio preprocessing
- **CUDA compatibility**: `torch==2.4.0` may be incompatible with newer GPUs (e.g., RTX 5090 / Blackwell sm_100). Use `pdgpu-2080ti-dsp` or other compatible partitions if you encounter CUDA kernel errors.
- Output text is automatically parsed to remove SenseVoice tags (e.g., `<|en|>`, `<|EMO_UNKNOWN|>`, `<|Speech|>`)

## See Also

- [Model README](../../../../docs/agents/model_tool_agent/README.md)
- [Evaluation Guide](../../../docs/evaluation.md)
