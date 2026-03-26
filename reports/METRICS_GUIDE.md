# SURE Benchmark Metrics Guide

This document explains the metrics used in SURE Benchmark and important implementation details.

## Table of Contents

1. [BLEU Score - Character-Level vs Word-Level](#bleu-score)
2. [Model Distinctions](#model-distinctions)
3. [Metric Reference](#metric-reference)

---

## BLEU Score

### ⚠️ Important: Character-Level BLEU for S2TT

SURE Benchmark uses **CHARACTER-LEVEL BLEU** for Speech-to-Text Translation (S2TT) tasks on CoVoST2 dataset. This is explicitly stated in Table 4 description:

> "S2TT reports **character-level BLEU** on CoVoST2 En↔Zh"

### Why Character-Level?

Character-level BLEU is used because:
1. **Chinese tokenization**: Chinese doesn't have natural word boundaries, character-level is more stable
2. **Fair comparison**: Avoids tokenization differences between models
3. **Standard in Chinese NLP**: Many Chinese benchmarks use character-level metrics

### Implementation

```python
# sacrebleu with character-level tokenization
import sacrebleu

# Character-level BLEU (used in SURE)
refs = [["这是参考文本"]]
hyp = "这是假设文本"
bleu = sacrebleu.sentence_bleu(hyp, refs, tokenize="zh")

# Word-level BLEU (NOT used in SURE for S2TT)
bleu_word = sacrebleu.sentence_bleu(hyp, refs, tokenize="13a")
```

### S2TT Baselines (Character-Level BLEU)

| Dataset | SOTA Model | Score | Direction |
|---------|-----------|-------|-----------|
| CoVoST2 EN→ZH | Qwen3-Omni | 46.25 | ↑ higher |
| CoVoST2 ZH→EN | Gemini-2.5pro | 60.14 | ↑ higher |

### Converting Your Results

If you have word-level BLEU scores and need to compare with SURE:
1. Recompute using character-level tokenization
2. Use `tokenize="zh"` for sacrebleu
3. Do NOT directly compare word-level vs character-level scores

---

## Model Distinctions

### Qwen3-Omni vs Qwen3-ASR-1.7B

These are **two different models** with different purposes and test results:

#### Qwen3-Omni

- **Type**: Multi-task foundation model
- **Capabilities**: ASR, S2TT, SER, GR, SLU
- **Tested in**: Table 4 (Horizontal Comparison)
- **Results**:
  - AISHELL-1 CER: 0.80 (SOTA)
  - LibriSpeech Clean WER: 1.70 (SOTA)
  - CoVoST2 EN→ZH BLEU: 46.25 (SOTA)
  - IEMOCAP ACC: 66.16
  - MMSU ACC: 83.61

#### Qwen3-ASR-1.7B

- **Type**: ASR-specialized model
- **Capabilities**: ASR only
- **Tested in**: Table 3 (Front-end Perception)
- **Results**:
  - CS-Dialogue MER: 7.00 (SOTA)
  - ContextASR-Zh CER: 2.50 (SOTA)
  - KeSpeech CER: 5.12
  - VoxPopuli WER: 7.41

**Key Difference**: Qwen3-Omni is a multi-task model supporting translation and understanding tasks, while Qwen3-ASR-1.7B is optimized specifically for speech recognition.

### Which Model Should You Use?

- **For ASR only**: Use Qwen3-ASR-1.7B (lighter, ASR-optimized)
- **For multi-task**: Use Qwen3-Omni (supports S2TT, SER, GR, SLU)
- **For S2TT**: Must use Qwen3-Omni (Qwen3-ASR doesn't support translation)

---

## Metric Reference

### Error Rates (Lower is Better ↓)

| Metric | Full Name | Used For | Typical Range |
|--------|-----------|----------|---------------|
| WER | Word Error Rate | English ASR | 1-50% |
| CER | Character Error Rate | Chinese ASR | 1-30% |
| MER | Mixed Error Rate | Code-switching ASR | 5-20% |
| DER | Diarization Error Rate | Speaker Diarization | 10-30% |
| cpWER | concatenated PER | Multi-speaker ASR | 10-50% |

### Accuracy Metrics (Higher is Better ↑)

| Metric | Used For | Typical Range |
|--------|----------|---------------|
| Accuracy | Classification (SER, GR, SLU) | 50-95% |
| BLEU (char) | Translation (S2TT) | 15-60 |

### RPS (Relative Performance Score)

RPS compares a model's score against SOTA:

```
For error rates (lower is better):
  RPS = SOTA_score / your_score
  
For accuracy (higher is better):
  RPS = your_score / SOTA_score
```

**Interpretation**:
- RPS = 1.0: You achieved SOTA
- RPS = 0.8: You are at 80% of SOTA performance
- RPS > 1.0: You beat the current SOTA!

---

## Adding New Models

When adding new model results:

1. **Check the metric type**: CER for Chinese ASR, WER for English ASR
2. **Use correct BLEU**: Character-level for S2TT on CoVoST2
3. **Distinguish models**: Don't mix Qwen3-Omni and Qwen3-ASR results
4. **Update SOTA**: If you beat SOTA, update `reports/sota/sota_baseline.yaml`
5. **Document**: Add notes about special conditions (e.g., "with hotword injection")

---

## References

- SURE Benchmark Paper (Tables 3 & 4)
- sacrebleu documentation: https://github.com/mjpost/sacrebleu
- Character-level vs word-level BLEU discussion in Chinese NLP
