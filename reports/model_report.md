# SURE Benchmark Model Performance Report

**Report Date:** 2025-03-25

**Total Models:** 7

## SOTA Summary

| Dataset | Metric | SOTA Model | Score |
|---------|--------|------------|-------|
| aishell1 | CER | Qwen3-Omni | 0.80 |
| aishell5 | CER | FireRed-ASR-1.7B | 24.74 |
| contextasr_en | WER | Pipeline | 3.47 |
| contextasr_zh | CER | Qwen3-Omni | 2.50 |
| covost2_en2zh | BLEU | Qwen3-Omni | 46.25 |
| covost2_zh2en | BLEU | Gemini-2.5pro | 60.14 |
| cs_dialogue | MER | Qwen3-Omni | 7.00 |
| iemocap | ACCURACY | Kimi-Audio | 69.38 |
| kespeech | CER | FireRed-ASR-1.7B | 3.81 |
| librispeech_clean | WER | Qwen3-Omni | 1.70 |
| librispeech_gr | ACCURACY | Kimi-Audio | 92.02 |
| librispeech_other | WER | Qwen3-Omni | 3.05 |
| mmsu | ACCURACY | Gemini-3.0pro | 89.07 |
| voxpopuli | WER | Parakeet-en | 6.72 |

## Model Details

### FireRed-ASR-1.7B

- **Full Name:** FireRed-ASR-1.7B
- **Organization:** FireRed
- **Model Size:** 1.7B
- **Supported Tasks:** ASR
- **Average RPS:** 0.81
- **SOTA Count:** 2/6

| Dataset | Task | Metric | Score | RPS | SOTA |
|---------|------|--------|-------|-----|------|
| AISHELL-5 | ASR | CER | 24.74 | 1.00 | ✓ |
| ContextASR-En | ASR | WER | 8.01 | 0.43 |  |
| ContextASR-Zh | ASR | CER | 2.78 | 0.90 |  |
| CS-Dialogue | ASR | MER | 7.44 | 0.94 |  |
| KeSpeech | ASR | CER | 3.81 | 1.00 | ✓ |
| VoxPopuli-en | ASR | WER | 11.87 | 0.57 |  |

### Gemini-2.5pro

- **Full Name:** Gemini 2.5 Pro
- **Organization:** Google
- **Model Size:** Unknown
- **Supported Tasks:** ASR, GR, S2TT, SER, SLU
- **Average RPS:** 0.66
- **SOTA Count:** 2/14

| Dataset | Task | Metric | Score | RPS | SOTA |
|---------|------|--------|-------|-----|------|
| AISHELL-1 | ASR | CER | 4.49 | 0.18 |  |
| AISHELL-5 | ASR | CER | 64.49 | 0.38 |  |
| ContextASR-En | ASR | WER | 3.47 | 1.00 | ✓ |
| ContextASR-Zh | ASR | CER | 2.78 | 0.90 |  |
| CoVoST2 En→Zh | S2TT | BLEU | 41.44 | 0.90 |  |
| CoVoST2 Zh→En | S2TT | BLEU | 60.14 | 1.00 | ✓ |
| CS-Dialogue | ASR | MER | 17.96 | 0.39 |  |
| IEMOCAP | SER | ACCURACY | 63.01 | 0.91 |  |
| KeSpeech | ASR | CER | 31.82 | 0.12 |  |
| LibriSpeech Clean | ASR | WER | 3.07 | 0.55 |  |
| LibriSpeech GR | GR | ACCURACY | 59.64 | 0.65 |  |
| LibriSpeech Other | ASR | WER | 4.93 | 0.62 |  |
| MMSU-Reason | SLU | ACCURACY | 84.64 | 0.95 |  |
| VoxPopuli-en | ASR | WER | 9.03 | 0.74 |  |

### Gemini-3.0pro

- **Full Name:** Gemini 3.0 Pro
- **Organization:** Google
- **Model Size:** Unknown
- **Supported Tasks:** ASR, GR, S2TT, SER, SLU
- **Average RPS:** 0.68
- **SOTA Count:** 1/8

| Dataset | Task | Metric | Score | RPS | SOTA |
|---------|------|--------|-------|-----|------|
| AISHELL-1 | ASR | CER | 1.02 | 0.78 |  |
| CoVoST2 En→Zh | S2TT | BLEU | 15.92 | 0.34 |  |
| CoVoST2 Zh→En | S2TT | BLEU | 15.50 | 0.26 |  |
| IEMOCAP | SER | ACCURACY | 66.56 | 0.96 |  |
| LibriSpeech Clean | ASR | WER | 3.05 | 0.56 |  |
| LibriSpeech GR | GR | ACCURACY | 78.50 | 0.85 |  |
| LibriSpeech Other | ASR | WER | 4.40 | 0.69 |  |
| MMSU-Reason | SLU | ACCURACY | 89.07 | 1.00 | ✓ |

### Kimi-Audio

- **Full Name:** Kimi-Audio
- **Organization:** Moonshot AI
- **Model Size:** Unknown
- **Supported Tasks:** ASR, SER, GR
- **Average RPS:** 0.74
- **SOTA Count:** 3/11

| Dataset | Task | Metric | Score | RPS | SOTA |
|---------|------|--------|-------|-----|------|
| AISHELL-1 | ASR | CER | 0.80 | 1.00 | ✓ |
| AISHELL-5 | ASR | CER | 45.72 | 0.54 |  |
| ContextASR-En | ASR | WER | 6.66 | 0.52 |  |
| ContextASR-Zh | ASR | CER | 2.96 | 0.84 |  |
| CS-Dialogue | ASR | MER | 11.94 | 0.59 |  |
| IEMOCAP | SER | ACCURACY | 69.38 | 1.00 | ✓ |
| KeSpeech | ASR | CER | 7.80 | 0.49 |  |
| LibriSpeech Clean | ASR | WER | 2.30 | 0.74 |  |
| LibriSpeech GR | GR | ACCURACY | 92.02 | 1.00 | ✓ |
| LibriSpeech Other | ASR | WER | 3.83 | 0.80 |  |
| VoxPopuli-en | ASR | WER | 10.63 | 0.63 |  |

### Qwen3-Omni

- **Full Name:** Qwen3-ASR-1.7B
- **Organization:** Alibaba
- **Model Size:** 1.7B
- **Supported Tasks:** ASR, S2TT, SER, GR, SLU
- **Average RPS:** 0.92
- **SOTA Count:** 6/14

| Dataset | Task | Metric | Score | RPS | SOTA |
|---------|------|--------|-------|-----|------|
| AISHELL-1 | ASR | CER | 0.80 | 1.00 | ✓ |
| AISHELL-5 | ASR | CER | 25.46 | 0.97 |  |
| ContextASR-En | ASR | WER | 5.58 | 0.62 |  |
| ContextASR-Zh | ASR | CER | 2.50 | 1.00 | ✓ |
| CoVoST2 En→Zh | S2TT | BLEU | 46.25 | 1.00 | ✓ |
| CoVoST2 Zh→En | S2TT | BLEU | 50.61 | 0.84 |  |
| CS-Dialogue | ASR | MER | 7.00 | 1.00 | ✓ |
| IEMOCAP | SER | ACCURACY | 66.16 | 0.95 |  |
| KeSpeech | ASR | CER | 5.12 | 0.74 |  |
| LibriSpeech Clean | ASR | WER | 1.70 | 1.00 | ✓ |
| LibriSpeech GR | GR | ACCURACY | 82.74 | 0.90 |  |
| LibriSpeech Other | ASR | WER | 3.05 | 1.00 | ✓ |
| MMSU-Reason | SLU | ACCURACY | 83.61 | 0.94 |  |
| VoxPopuli-en | ASR | WER | 7.41 | 0.91 |  |

### SenseVoice-Small

- **Full Name:** SenseVoice Small
- **Organization:** Alibaba
- **Model Size:** Small
- **Supported Tasks:** ASR
- **Average RPS:** 0.51
- **SOTA Count:** 0/6

| Dataset | Task | Metric | Score | RPS | SOTA |
|---------|------|--------|-------|-----|------|
| AISHELL-5 | ASR | CER | 38.63 | 0.64 |  |
| ContextASR-En | ASR | WER | 14.52 | 0.24 |  |
| ContextASR-Zh | ASR | CER | 6.44 | 0.39 |  |
| CS-Dialogue | ASR | MER | 7.52 | 0.93 |  |
| KeSpeech | ASR | CER | 12.46 | 0.31 |  |
| VoxPopuli-en | ASR | WER | 12.50 | 0.54 |  |

### Whisper-large-v3

- **Full Name:** Whisper Large v3
- **Organization:** OpenAI
- **Model Size:** Large
- **Supported Tasks:** ASR
- **Average RPS:** 0.39
- **SOTA Count:** 0/6

| Dataset | Task | Metric | Score | RPS | SOTA |
|---------|------|--------|-------|-----|------|
| AISHELL-5 | ASR | CER | 45.11 | 0.55 |  |
| ContextASR-En | ASR | WER | 8.37 | 0.41 |  |
| ContextASR-Zh | ASR | CER | 8.29 | 0.30 |  |
| CS-Dialogue | ASR | MER | 15.91 | 0.44 |  |
| KeSpeech | ASR | CER | 30.65 | 0.12 |  |
| VoxPopuli-en | ASR | WER | 12.62 | 0.53 |  |
