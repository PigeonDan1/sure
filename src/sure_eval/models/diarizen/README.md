# DiariZen Speaker Diarization Tool

Speaker diarization tool for SURE-EVAL using DiariZen.

## Quick Start

```bash
# 1. Setup environment
source /cpfs/user/jingpeng/miniconda/etc/profile.d/conda.sh
conda create -n diarizen python=3.10 -y
conda activate diarizen

# 2. Install PyTorch
pip install torch==2.4.0 torchaudio==2.4.0 --index-url https://download.pytorch.org/whl/cpu

# 3. Install DiariZen
cd diarizen_src
pip install -e .

# 4. 【关键】安装 pyannote 子模块
cd pyannote-audio
pip install -e .

# 5. 锁定 NumPy 版本并安装其他依赖
pip install numpy==1.26.4 psutil accelerate

# 6. Test
cd ../..
python test_model.py
```

⚠️ **必须严格按照上述顺序安装！** 详见 [RULES.md](RULES.md) 和 [LESSONS_LEARNED.md](LESSONS_LEARNED.md)。

---

## Model Information

| Property | Value |
|----------|-------|
| **Model** | BUT-FIT/diarizen-wavlm-large-s80-md |
| **Architecture** | WavLM-Large with 80% structured pruning |
| **Task** | Speaker Diarization (SD) |
| **License** | CC BY-NC 4.0 (Non-commercial) |
| **Status** | ✅ Implemented & Tested |

### Benchmark Results (from paper)

| Dataset | DER |
|---------|-----|
| AMI-SDM | 14.0% |
| AISHELL-4 | 9.8% |
| AliMeeting-far | 12.5% |
| DIHARD3-full | 14.5% |
| VoxConverse | 9.2% |

---

## Usage

### Direct Model Usage

```python
from model import DiariZenModel

# Initialize model
model = DiariZenModel()

# Diarize audio
result = model.diarize("audio.wav")

# Print results
print(f"Detected {result.num_speakers} speakers")
for seg in result.segments:
    print(f"{seg.start:.1f}s - {seg.end:.1f}s: Speaker {seg.speaker}")

# Get RTTM format
print(result.rttm)

# Save RTTM to file
rttm_path = model.diarize_with_rttm_output("audio.wav", output_dir="./results")
```

### MCP Server

```bash
# Activate environment and start server
source /cpfs/user/jingpeng/miniconda/etc/profile.d/conda.sh
conda activate diarizen
python server.py
```

Server provides two tools:
- `diarize`: Perform speaker diarization
- `diarize_with_rttm`: Diarize and save RTTM output

---

## Input/Output Format

### Input
- Audio file (wav, flac, mp3, etc.)
- Optional parameters:
  - `num_speakers`: Exact number of speakers
  - `min_speakers`: Minimum number of speakers
  - `max_speakers`: Maximum number of speakers

### Output (RTTM Format)
```
SPEAKER session_name 1 start_time duration <NA> <NA> speaker_id <NA> <NA>
```

Example:
```
SPEAKER EN2002a_30s 1 0.000 2.700 <NA> <NA> 0 <NA> <NA>
SPEAKER EN2002a_30s 1 0.800 12.800 <NA> <NA> 3 <NA> <NA>
```

---

## Test Results

Tested on `EN2002a_30s.wav` (30s, 4 speakers):

```
✓ Diarization completed
  Detected 4 speakers
  13 segments

  Segments:
    0.0s - 2.7s: Speaker 0
    0.8s - 13.6s: Speaker 3
    5.8s - 6.4s: Speaker 0
    8.0s - 10.6s: Speaker 0
    ...
```

Run tests:
```bash
python test_model.py
```

---

## Project Structure

```
diariwen/
├── model.py              # Model wrapper
├── server.py             # MCP server
├── config.yaml           # MCP configuration
├── test_model.py         # Test script
├── README.md             # This file
├── RULES.md              # ⭐ Configuration rules (必读)
├── LESSONS_LEARNED.md    # ⭐ 踩坑总结 (必读)
├── requirements.txt      # Python dependencies
└── diarizen_src/         # DiariZen source (submodule)
    ├── diarizen/         # Main package
    ├── pyannote-audio/   # Modified pyannote (关键！)
    └── example/          # Test audio
```

---

## Known Issues

1. **短音频限制**: 音频文件必须 >= 16s（推荐 >= 30s），否则触发 pyannote bug
2. **Deprecation warnings**: SpeechBrain API 警告，不影响功能
3. **CPU only**: 当前配置使用 CPU，GPU 支持需安装 CUDA 版 PyTorch

---

## Critical Dependencies

| Package | Source | Version | Reason |
|---------|--------|---------|--------|
| pyannote.audio | **Submodule** | embedded | DiariZen 使用修改版 |
| numpy | PyPI | **1.26.4** | pyannote 子模块要求 |
| torch | PyPI | 2.4.0 | 兼容 NumPy 1.x |
| python | conda | 3.10 | 最低版本要求 |

⚠️ **pyannote.audio 必须从子模块安装，不能用 PyPI 版本！**

---

## Troubleshooting

### `'DiariZenPipeline' object has no attribute '_segmentation_model'`
**原因**: 使用了 PyPI 的 pyannote.audio  
**解决**: `cd diarizen_src/pyannote-audio && pip install -e .`

### `numpy._core.multiarray` error
**原因**: NumPy 版本不兼容  
**解决**: `pip install numpy==1.26.4`

### `UnboundLocalError: cannot access local variable 'segmentations'`
**原因**: 音频文件太短 (< 16s)  
**解决**: 使用更长的音频文件 (>= 30s)

---

## Citations

```bibtex
@inproceedings{han2025leveraging,
  title={Leveraging self-supervised learning for speaker diarization},
  author={Han, Jiangyu and Landini, Federico and Rohdin, Johan and Silnova, Anna and Diez, Mireia and Burget, Luk{\'a}{\v{s}}},
  booktitle={Proc. ICASSP},
  year={2025}
}
```

## References

- GitHub: https://github.com/BUTSpeechFIT/DiariZen
- HuggingFace: https://huggingface.co/BUT-FIT/diarizen-wavlm-large-s80-md
- Paper: ICASSP 2025

## License

- **Code**: MIT (DiariZen toolkit)
- **Model Weights**: CC BY-NC 4.0 (Non-commercial use only)
