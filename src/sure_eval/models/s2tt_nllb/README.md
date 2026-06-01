# S2TT NLLB Model

Speech-to-Text Translation using Meta's NLLB (No Language Left Behind) model.

## Model Information

| Attribute | Value |
|-----------|-------|
| **Name** | s2tt_nllb |
| **Task** | S2TT (Speech-to-Text Translation) |
| **Model** | facebook/nllb-200-distilled-600M |
| **Size** | 600M parameters |
| **Languages** | 200 languages |
| **License** | CC-BY-NC |

## Status

🚧 **Not Implemented** - Template for future implementation

## Capabilities

- Speech-to-text translation
- Multiple language pairs (e.g., en→zh, zh→en)
- Cascade ASR + MT approach

## Test Results

| Dataset | Date | BLEU | chrF2 | RPS | Status |
|---------|------|------|-------|-----|--------|
| CoVoST2_en2zh | - | - | - | - | Not tested |
| CoVoST2_zh2en | - | - | - | - | Not tested |

## See Also

- [NLLB Paper](https://arxiv.org/abs/2207.04672)
- [NLLB HuggingFace](https://huggingface.co/models?search=nllb)
