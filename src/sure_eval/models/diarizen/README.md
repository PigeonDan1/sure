# DiariZen Speaker Diarization Tool

Speaker diarization tool for SURE-EVAL using DiariZen.

## Model Information

- **Model**: BUT-FIT/diarizen-wavlm-large-s80-md
- **Architecture**: WavLM-Large with 80% structured pruning
- **Task**: Speaker Diarization (SD)
- **License**: CC BY-NC 4.0 (Non-commercial)

## Installation

### Prerequisites

- Python >= 3.10
- CUDA-capable GPU (recommended, 8GB+ VRAM)

### Setup with UV

```bash
# From the diarizen model directory
cd /path/to/sure-eval/src/sure_eval/models/diarizen

# Install DiariZen from GitHub (required)
pip install git+https://github.com/BUTSpeechFIT/DiariZen.git

# Setup UV environment
python /path/to/sure-eval/demo/setup_model.py diarizen
```

### Manual Installation

```bash
# Create virtual environment
uv venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install dependencies
uv pip install -e .

# Install DiariZen from GitHub
uv pip install git+https://github.com/BUTSpeechFIT/DiariZen.git
```

## Usage

### As MCP Tool

```python
from sure_eval import AutonomousEvaluator

evaluator = AutonomousEvaluator()

# Evaluate on diarization dataset
result = evaluator.quick_test(
    tool_name="diarizen",
    dataset="alimeeting",  # or other SD dataset
    num_samples=10
)

print(f"DER: {result['score']}%")
print(f"RPS: {result['rps']}")
```

### Direct Usage

```python
from model import DiariZenModel

# Initialize model
model = DiariZenModel(
    model_path="BUT-FIT/diarizen-wavlm-large-s80-md",
    device="cuda"
)

# Diarize audio
result = model.diarize(
    audio_path="path/to/audio.wav",
    num_speakers=2  # Optional: specify speaker count
)

# Print results
print(f"Detected {result.num_speakers} speakers")
for seg in result.segments:
    print(f"{seg.start:.2f}s - {seg.end:.2f}s: {seg.speaker}")

# Get RTTM format
print(result.rttm)
```

### MCP Server Directly

```bash
# Start server
cd /path/to/sure-eval/src/sure_eval/models/diarizen
.venv/bin/python server.py

# Then use with MCP client
```

## Input/Output Format

### Input

- Audio file (wav, flac, mp3, etc.)
- Optional: num_speakers, min_speakers, max_speakers

### Output

**RTTM Format**:
```
SPEAKER session_name 1 start_time duration <NA> <NA> speaker_id <NA> <NA>
```

Example:
```
SPEAKER meeting_001 1 0.000 2.450 <NA> <NA> speaker_0 <NA> <NA>
SPEAKER meeting_001 1 2.450 5.120 <NA> <NA> speaker_1 <NA> <NA>
```

## Benchmark Results

From the original paper (DER without collar):

| Dataset | DER |
|---------|-----|
| AMI-SDM | 14.0% |
| AISHELL-4 | 9.8% |
| AliMeeting-far | 12.5% |
| DIHARD3-full | 14.5% |
| MSDWild | 15.6% |
| RAMC | 11.0% |
| VoxConverse | 9.2% |

## Citations

```bibtex
@inproceedings{han2025leveraging,
  title={Leveraging self-supervised learning for speaker diarization},
  author={Han, Jiangyu and Landini, Federico and Rohdin, Johan and Silnova, Anna and Diez, Mireia and Burget, Luk{\'a}{\v{s}}},
  booktitle={Proc. ICASSP},
  year={2025}
}

@article{han2025fine,
  title={Fine-tune Before Structured Pruning: Towards Compact and Accurate Self-Supervised Models for Speaker Diarization},
  author={Han, Jiangyu and Landini, Federico and Rohdin, Johan and Silnova, Anna and Diez, Mireia and Cernocky, Jan and Burget, Lukas},
  journal={arXiv preprint arXiv:2505.24111},
  year={2025}
}
```

## License

- **Code**: MIT (DiariZen toolkit)
- **Model Weights**: CC BY-NC 4.0 (Non-commercial use only)

See [DiariZen License](https://github.com/BUTSpeechFIT/DiariZen#license) for details.

## References

- GitHub: https://github.com/BUTSpeechFIT/DiariZen
- HuggingFace: https://huggingface.co/BUT-FIT/diarizen-wavlm-large-s80-md
- Paper: ICASSP 2025
