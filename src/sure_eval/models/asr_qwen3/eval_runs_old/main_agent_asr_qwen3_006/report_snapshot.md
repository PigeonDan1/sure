# SURE-EVAL Run Report: asr_qwen3

**Run ID**: `main_agent_asr_qwen3_006`  
**Date**: 2026-05-16  
**Task Type**: `evaluate_existing_model`  
**Execution Path**: `direct_server_use` (MCP server)

---

## Datasets Evaluated

| Dataset | Task | Language | Metric | Score | RPS | Samples |
|---------|------|----------|--------|-------|-----|---------|
| aishell1 | ASR | zh | CER | **1.58%** | 0.506 | 7,176 |
| librispeech_clean | ASR | en | WER | **2.29%** | 0.743 | 2,619 |

---

## Key Results

### AISHELL-1 (Chinese ASR)
- **CER**: 1.58% (104,900 chars: 103,386 correct, 1,435 substitutions, 145 insertions, 79 deletions)
- **RPS**: 0.506

### LibriSpeech test-clean (English ASR)
- **WER**: 2.29% (52,548 words: 51,426 correct, 888 substitutions, 80 insertions, 234 deletions)
- **RPS**: 0.743

---

## Assessment

- **Status**: ✅ Success
- **Anomalies**: None detected
- **Metric bounds check**: All scores well below 50% threshold
- **Recommendation**: No further action required. Results are strong and consistent.

---

## Artifacts

- **Predictions**: `src/sure_eval/models/asr_qwen3/eval_runs/main_agent_asr_qwen3_006/predictions/`
- **Evaluation Payload**: `src/sure_eval/models/asr_qwen3/eval_runs/main_agent_asr_qwen3_006/evaluation_payload.json`
- **Validation Payload**: `src/sure_eval/models/asr_qwen3/eval_runs/main_agent_asr_qwen3_006/validation_payload.json`
- **Run Log**: `src/sure_eval/models/asr_qwen3/eval_runs/main_agent_asr_qwen3_006/run.log`
