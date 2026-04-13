# FireRedASR2-AED Model

Automatic Speech Recognition using FireRedTeam's FireRedASR2-AED standalone ASR model.

## Model Information

| Attribute | Value |
|-----------|-------|
| **Name** | fireredasr2_aed |
| **Task** | ASR (Automatic Speech Recognition) |
| **Model** | FireRedTeam/FireRedASR2-AED |
| **Runtime Family** | FireRedAsr2 (AED) |
| **Deployment Type** | Local |
| **Backend** | pip |
| **Python Version** | 3.10 |
| **Weights Source** | Hugging Face |
| **Fallback Weights Source** | ModelScope |
| **Local Weights Path** | `pretrained_models/FireRedASR2-AED` |
| **Upstream Repo** | [FireRedTeam/FireRedASR2S](https://github.com/FireRedTeam/FireRedASR2S) |
| **Standalone Module** | `fireredasr2s.fireredasr2` |
| **System Packages** | `ffmpeg`, `libsndfile1` |

## Capabilities

- **ASR**: Transcribe speech to text
- **Standalone Runtime Path**: Uses the validated repo-native `FireRedAsr2` AED path
- **Structured Output**: Returns JSON with `uttid` and `text`
- **MCP Server**: Supports local transcription and healthcheck through `server.py`

## Environment Setup

```bash
# Setup virtual environment
python3.10 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.phase1.txt
```

### System Dependencies

```bash
# Ubuntu / Debian
sudo apt-get update
sudo apt-get install -y ffmpeg libsndfile1 python3.10 python3.10-venv
```

### Upstream Source Setup

This package does **not** include the upstream FireRedASR2S source tree.

The current wrapper depends on a separate local checkout of the upstream repository at runtime, so clone it manually to the following path:

```bash
mkdir -p upstream
git clone https://github.com/FireRedTeam/FireRedASR2S upstream/FireRedASR2S
```

### Weights Setup

Place model weights under:

```bash
pretrained_models/FireRedASR2-AED
```

## Test Results

### Phase-1 Runtime Validation

| Date | Scope | Status | Notes |
|------|-------|--------|-------|
| 2026-04-13 | Import | Passed | Repo-native import path validated |
| 2026-04-13 | Load | Passed | Local weights loaded successfully |
| 2026-04-13 | Inference | Passed | Shared ASR fixture worked correctly |
| 2026-04-13 | Contract | Passed | Output contains required non-empty `text` field |

### Validation Timing

| Check | Duration |
|------|----------|
| Import | 4579.232 ms |
| Load | 11501.666 ms |
| Inference | 6120.637 ms |
| Contract | 0.482 ms |

### Final Verdict

| Attribute | Value |
|-----------|-------|
| **Status** | success |
| **Stopped At** | DONE |
| **Escalated** | false |
| **Fixture** | `tests/fixtures/shared/asr/en_16k_10s.wav` |
| **Primary Output Field** | `text` |
| **Required Fields** | `uttid`, `text` |

## Usage

### As MCP Server

```yaml
# config/mcp_tools.yaml
tools:
  fireredasr2_aed:
    name: "fireredasr2_aed"
    command: [".venv/bin/python", "server.py"]
    working_dir: "/path/to/sure-eval/src/sure_eval/models/fireredasr2_aed"
    timeout: 300
```

### Direct Usage

```python
from model import ModelWrapper

wrapper = ModelWrapper({
    "use_gpu": False,
    "use_half": False,
    "beam_size": 3,
    "nbest": 1,
    "decode_max_len": 0,
    "softmax_smoothing": 1.25,
    "aed_length_penalty": 0.6,
    "eos_penalty": 1.0,
    "return_timestamp": False,
})

result = wrapper.predict("audio.wav")
print(result["text"])
print(result)
```

### Package-style Usage

```python
from fireredasr2_aed import ModelWrapper

wrapper = ModelWrapper()
result = wrapper.predict("audio.wav")
print(result["text"])
```

## API Reference

### Tools

- `transcribe_audio(audio_path)`: Transcribe one local audio file
- `healthcheck()`: Return wrapper readiness and path information

### Core Methods

- `ModelWrapper.load()`: Load FireRedASR2-AED from local weights
- `ModelWrapper.predict(audio_path)`: Run transcription on one audio file
- `ModelWrapper.healthcheck()`: Return current wrapper status

## Files

- `server.py` - MCP server implementation
- `model.py` - Core model wrapper
- `config.yaml` - MCP configuration
- `model.spec.yaml` - Phase-1 onboarding spec
- `requirements.phase1.txt` - Python dependencies for local phase-1 validation
- `validate_phase1.py` - Validation script
- `artifacts/` - Validation logs, verdict, and manifests

## Notes

- First inference may be slower because the model needs to load
- Local weights are required before inference
- GPU is supported by the upstream runtime, but this phase-1 validation passed with CPU fallback on the current host
- This wrapper validates the **standalone FireRedASR2-AED** path only
- It does **not** switch to FireRedASR2-LLM
- It does **not** broaden scope to FireRedAsr2System
- The upstream source code is **not included** in this package and must be cloned separately before running the wrapper

## See Also

- [Model README](../README.md)
- [SURE-EVAL Root README](../../../../README.md)
