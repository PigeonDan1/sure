# FireRedVAD Model

Voice Activity Detection and Audio Event Detection using FireRedTeam's FireRedVAD model.

## Model Information

| Attribute | Value |
|-----------|-------|
| **Name** | fireredvad |
| **Task** | VAD / AED (Voice Activity Detection / Audio Event Detection) |
| **Model** | FireRedTeam/FireRedVAD |
| **Model Variant** | FireRedVAD-VAD |
| **Deployment** | Local |
| **Backend** | pip |
| **Validated Runtime** | Python 3.10, CPU-only |
| **Languages** | 100+ languages |
| **Input Audio** | 16kHz 16-bit mono PCM wav |
| **License** | Apache 2.0 |
| **Source** | [HuggingFace](https://huggingface.co/FireRedTeam/FireRedVAD) / [GitHub](https://github.com/FireRedTeam/FireRedVAD) |

## Capabilities

- **Non-streaming VAD**: Detect speech regions from full audio
- **Streaming VAD**: Detect speech activity in streaming scenarios
- **AED**: Detect speech / singing / music events
- **Timestamps**: Return timestamp pairs for detected regions
- **JSON Contract**: Produces JSON-serializable outputs centered on `timestamps`

## Environment Setup

```bash
# Option 1: use the validated phase-1 pinned environment
cd /Users/wency/Desktop/上交/SURE/sure/src/sure_eval/models/fireredvad
python3.10 -m venv .venv
source .venv/bin/activate
pip install -r requirements.phase1.txt

# Option 2: install from pyproject
cd /Users/wency/Desktop/上交/SURE/sure/src/sure_eval/models/fireredvad
python3.10 -m venv .venv
source .venv/bin/activate
pip install -e .
```
### Additional Notes
```bash
# Download weights into the validated runtime location
huggingface-cli download FireRedTeam/FireRedVAD --local-dir ./pretrained_models/FireRedVAD

# Convert audio to the required format if needed
ffmpeg -i input_audio.wav -ar 16000 -ac 1 -acodec pcm_s16le -f wav output_16k.wav
```

## Test Results

### Local Smoke Test
| Date       | Fixture                                    | Status | Notes                                                                                                                                    |
| ---------- | ------------------------------------------ | ------ | ---------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-04-02 | `tests/fixtures/shared/vad/en_16k_10s.wav` | Passed | CPU-only phase-1 validation passed. Output was JSON-serializable and returned non-empty timestamps `[[0.5, 2.25]]` with duration `2.35`. |

### Phase-1 Validation Summary

| Stage                | Result | Duration  |
| -------------------- | ------ | --------- |
| Import               | Passed | 592.37 ms |
| Load                 | Passed | 5.754 ms  |
| Inference            | Passed | 41.127 ms |
| Contract             | Passed | 0.017 ms  |
| End-to-end retry run | Passed | 0.639 s   |
| Environment build    | Passed | 377 s     |

### Retry / Failure Note
| Attempt | Stage          | Issue                                           | Mitigation                                             | Final Outcome   |
| ------- | -------------- | ----------------------------------------------- | ------------------------------------------------------ | --------------- |
| 1       | VALIDATE_INFER | Relative fixture path caused audio open failure | Resolved fixture and model paths to absolute locations | Passed on retry |

### Upstream Benchmark Report
| Benchmark      | AUC-ROC | F1    | False Alarm Rate | Miss Rate | Notes                                  |
| -------------- | ------- | ----- | ---------------- | --------- | -------------------------------------- |
| FLEURS-VAD-102 | 99.60   | 97.57 | 2.69             | 3.62      | Reported by upstream FireRedVAD README |


## Usage

### As MCP Server

```yaml
# config/mcp_tools.yaml
tools:
  fireredvad:
    name: "fireredvad"
    command: [".venv/bin/python", "server.py"]
    working_dir: "/Users/wency/Desktop/sjtu/SURE/sure/sure-eval/src/sure_eval/models/fireredvad"
    env:
      MODEL_PATH: "pretrained_models/FireRedVAD/VAD"
      DEVICE: "cpu"
    timeout: 300
```

### Direct Usage (SURE Wrapper)

```python
from sure_eval.models.fireredvad import ModelWrapper

model = ModelWrapper()
model.load()

result = model.predict(
    "/Users/wency/Desktop/上交/SURE/sure/tests/fixtures/shared/vad/en_16k_10s.wav"
)

print(result.to_dict())
# {
#   "timestamps": [[0.5, 2.25]],
#   "dur": 2.35,
#   "wav_path": "/Users/wency/Desktop/上交/SURE/sure/tests/fixtures/shared/vad/en_16k_10s.wav"
# }
```

### Direct Usage (Repo-native API)
```python
from fireredvad import FireRedVad, FireRedVadConfig

vad_config = FireRedVadConfig(
    use_gpu=False,
    smooth_window_size=5,
    speech_threshold=0.4,
    min_speech_frame=20,
    max_speech_frame=2000,
    min_silence_frame=20,
    merge_silence_frame=0,
    extend_speech_frame=0,
    chunk_max_frame=30000,
)

vad = FireRedVad.from_pretrained("pretrained_models/FireRedVAD/VAD", vad_config)
result, probs = vad.detect("tests/fixtures/shared/vad/en_16k_10s.wav")
print(result)
```

## API Reference

### Tools / Core Operations

- `vad.detect(audio_path)`: Run the validated non-streaming FireRedVAD path on an audio file
- `healthcheck()`: Check whether the wrapper has loaded the model

## Files

- `model.spec.yaml` - Phase-1 model specification
- `pyproject.toml` - Python package definition for the validated local environment
- `requirements.phase1.txt` - Fully pinned phase-1 runtime dependencies
- `model.py` - Core SURE wrapper around the validated non-streaming VAD path
- `server.py` - MCP-style server exposing `vad_predict` and `healthcheck`
- `__init__.py` - Package exports
- `validate_phase1.py` - Minimal import/load/infer/contract validation script
- `artifacts/verdict.json` - Final onboarding verdict
- `artifacts/build.log` - Environment build and weight download log
- `artifacts/validation.log` - Validation and retry log
- `artifacts/runtime_output.json` - Example validated runtime output
- `artifacts/weights_manifest.json` - Weight inventory and download status

## Notes

- This is a **VAD/AED** model, not an ASR model, so local validation should focus on timestamp-bearing JSON outputs rather than WER/CER.
- The validated SURE phase-1 target is the **minimal non-streaming VAD path**.
- The accepted IO contract uses `audio_path` as input and JSON output with `timestamps` as the primary field.
- The local fixture is task-specific for VAD and is explicitly recorded as a shared fallback fixture that includes speech and non-speech regions.
- Weight download succeeded from Hugging Face without an HF token, but the build log warns that authenticated requests would provide higher rate limits.
- The current validated path is CPU-only; GPU is not required for phase-1 onboarding.
- First-run issues were not dependency failures but path-resolution failures during inference, so future failures should first check fixture/model path handling before deeper dependency debugging.

## See Also

- [Model README]((../README.md))
- [FireRedVAD Upstream Repository](https://github.com/FireRedTeam/FireRedVAD)
