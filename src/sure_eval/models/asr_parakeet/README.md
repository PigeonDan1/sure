# ASR Parakeet Model for SURE-EVAL

NVIDIA Parakeet-TDT-0.6B-v2 ASR model wrapper for SURE-EVAL framework.

## Model Information

- **Model**: [nvidia/parakeet-tdt-0.6b-v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2)
- **Parameters**: 600M (0.6B)
- **Task**: Automatic Speech Recognition (ASR)
- **Language**: English only
- **License**: CC-BY-4.0 (Commercial use allowed)
- **Architecture**: FastConformer encoder + TDT decoder

## Features

- ✅ Accurate word-level timestamp predictions
- ✅ Automatic punctuation and capitalization
- ✅ Robust performance on spoken numbers and song lyrics
- ✅ Long audio support (up to 24 minutes per chunk)
- ✅ High performance: RTFx 3380 on HF-Open-ASR leaderboard (batch_size=128)

## Performance Benchmarks

| Dataset | WER |
|---------|-----|
| LibriSpeech (clean) | 1.69% |
| LibriSpeech (other) | 3.19% |
| AMI (Meetings) | 11.16% |
| Earnings-22 | 11.15% |
| GigaSpeech | 9.74% |

## Installation

### Prerequisites

- Python 3.10+
- CUDA-capable GPU (recommended) or CPU
- 4GB+ RAM
- 2GB+ storage for model weights

### Setup

```bash
cd src/sure_eval/models/asr_parakeet

# Create virtual environment
uv venv --python=python3.10

# Activate environment
source .venv/bin/activate

# Install dependencies (PyTorch first, then NeMo)
uv pip install torch>=2.0.0 torchaudio>=2.0.0
uv pip install nemo-toolkit[asr]>=2.0

# Or use pyproject.toml
uv pip install -e .
```

## Usage

### Direct Model Usage

```python
from model import ASRParakeetModel

# Initialize model
model = ASRParakeetModel()

# Transcribe audio
result = model.transcribe("audio.wav")
print(result.text)

# With timestamps
result = model.transcribe("audio.wav", return_timestamps=True)
for ts in result.timestamps:
    print(f"{ts['start']}s - {ts['end']}s: {ts['text']}")
```

### MCP Server Usage

```bash
# Start MCP server
.venv/bin/python server.py

# Test with MCP protocol
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | .venv/bin/python server.py
```

## Configuration

See `config.yaml` for MCP server configuration.

Key environment variables:
- `MODEL_PATH`: Model ID or local path (default: "nvidia/parakeet-tdt-0.6b-v2")
- `DEVICE`: Device to use - "auto", "cuda", or "cpu" (default: "auto")

## Hardware Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| RAM | 2GB | 4GB+ |
| GPU | - | NVIDIA Ampere/Blackwell/Hopper/Volta |
| GPU Memory | - | 2GB+ |
| Storage | 1.5GB | 2GB |

## API Reference

### `ASRParakeetModel`

```python
class ASRParakeetModel:
    def __init__(self, model_path: str | None = None, device: str = "auto")
    def transcribe(self, audio_path: str, return_timestamps: bool = False) -> TranscriptionResult
    def transcribe_batch(self, audio_paths: list[str]) -> list[TranscriptionResult]
```

### `TranscriptionResult`

```python
@dataclass
class TranscriptionResult:
    text: str                    # Transcribed text
    language: str | None         # Detected language (always "en")
    timestamps: list[dict] | None  # Optional timestamps
```

## Troubleshooting

### Model Download Issues

If model download is slow or fails:
```bash
# Use HuggingFace mirror (China)
export HF_ENDPOINT=https://hf-mirror.com

# Or pre-download
huggingface-cli download nvidia/parakeet-tdt-0.6b-v2 --local-dir ./model_cache
export MODEL_PATH=./model_cache
```

### CUDA Out of Memory

Reduce memory usage:
```python
# Use CPU
model = ASRParakeetModel(device="cpu")

# Or process shorter audio segments
```

### NeMo Installation Issues

If NeMo installation fails:
```bash
# Ensure PyTorch is installed first
uv pip install torch==2.4.0 torchaudio==2.4.0

# Then install NeMo
uv pip install nemo-toolkit[asr]==2.2.0
```

## References

- [HuggingFace Model Card](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2)
- [NVIDIA NeMo Documentation](https://docs.nvidia.com/nemo-framework/user-guide/latest/nemotoolkit/asr/intro.html)
- [FastConformer Paper](https://arxiv.org/abs/2305.05084)
- [TDT Decoder Paper](https://arxiv.org/abs/2304.06795)

## License

This model is released under CC-BY-4.0 license, allowing commercial use.
