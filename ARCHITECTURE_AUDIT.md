# SURE-EVAL Architecture Audit Report

**Date**: 2026-03-26  
**Auditor**: Kimi Code  
**Status**: Critical issues found, fixes in progress

---

## Executive Summary

The SURE-EVAL framework has a solid foundation with clear separation of concerns:
- ✅ Data Pipeline (download/format)
- ✅ Tool Management (MCP/Registry)
- ✅ Evaluation Engine (metrics/RPS)
- ✅ Report System (SOTA/tracking)
- ✅ Agent Orchestrator

However, several **critical integration issues** were discovered that prevent seamless operation.

---

## 🔴 Critical Issues (Blocking)

### Issue 1: Dataset Name Inconsistency

**Problem**: Dataset names are inconsistent across components

| Component | Dataset Name Example |
|-----------|---------------------|
| CSV filenames | `CS_dialogue`, `CoVoST2_S2TT_en2zh_test` |
| Config keys | `cs_dialogue`, `covost2_en2zh` |
| RPS baselines | `cs_dialogue`, `covost2_en2zh` |
| DatasetManager.list_available() | Returns CSV names like `CS_dialogue` |

**Impact**: RPS calculation fails because baseline lookup uses different keys than dataset discovery

**Evidence**:
```python
# DatasetManager returns 'CS_dialogue'
datasets = ae.dataset_manager.list_available()  # ['CS_dialogue', ...]

# But RPS baseline uses 'cs_dialogue'
baseline = ae.rps_manager.calculator.get_baseline('CS_dialogue')  # Returns None!
```

**Fix Required**: Normalize dataset names throughout the pipeline

---

### Issue 2: Model Identity Gap

**Problem**: Two separate model namespaces without mapping

| Registry (Local Tools) | Reports (Benchmark Results) |
|------------------------|----------------------------|
| `asr_qwen3` | `Qwen3-ASR-1.7B` (Table 3) |
| | `Qwen3-Omni` (Table 4) |
| `asr_whisper` | `Whisper-large-v3` |
| `diarizen` | (not in SURE yet) |

**Impact**: 
- Cannot link local tool evaluation results to benchmark reports
- Cannot automatically determine if a tool is SOTA
- No way to track which local tool corresponds to which paper result

**Fix Required**: Create bidirectional mapping between local tool IDs and benchmark model names

---

### Issue 3: Missing Integration Layer

**Current Flow** (broken):
```
User Request → Agent → Tool Evaluation → Raw Score
                                      ↓
                                [MISSING: Auto lookup SOTA]
                                      ↓
                                RPS Calculation (may fail)
                                      ↓
                                [MISSING: Update report]
                                      ↓
                                Result to User
```

**Expected Flow**:
```
User Request → Agent → Tool Evaluation → Raw Score
                                      ↓
                                Auto-detect Model Identity
                                      ↓
                                Lookup SOTA from Reports
                                      ↓
                                Calculate RPS
                                      ↓
                                Update Evaluation DB
                                      ↓
                                Generate/Update Report
                                      ↓
                                Result to User
```

---

## 🟡 Medium Issues (Improvement Needed)

### Issue 4: Config Path Dependencies

**Problem**: MCP tools config uses absolute paths:
```yaml
asr_qwen3:
  working_dir: "/cpfs/user/jingpeng/workspace/AUDIO_AGENT/..."
```

**Impact**: Not portable across machines

**Fix**: Use relative paths with environment variable substitution

---

### Issue 5: Missing Unified CLI

**Problem**: Multiple entry points without unified interface:
- `demo/demo_*.py` for demos
- `scripts/*.py` for utilities
- `sure-eval` CLI (partial)

**Fix**: Complete the main CLI with all functionality

---

### Issue 6: Report Auto-Update Not Implemented

**Problem**: Report system is read-only
- Can view SOTA baselines
- Can view model performance
- But cannot add new evaluation results to reports

**Expected**: Running evaluation should automatically update reports

---

## 🟢 Minor Issues (Nice to Have)

### Issue 7: Documentation Gaps
- Missing architecture diagram
- Missing contribution guide for adding new models
- Missing troubleshooting guide

### Issue 8: Test Coverage
- No unit tests for critical paths
- No integration tests for full pipeline

---

## Recommended Fix Priority

### Phase 1: Critical (Unblocks Basic Usage)
1. ✅ Fix dataset name normalization
2. ✅ Create model identity mapping
3. ✅ Connect evaluation flow to reports

### Phase 2: Important (Improves UX)
4. Make configs portable
5. Complete unified CLI
6. Add auto-update to reports

### Phase 3: Polish
7. Add tests
8. Improve documentation

---

## Detailed Fix Plan

### Fix 1: Dataset Name Normalization

**Location**: `src/sure_eval/datasets/dataset_manager.py`

**Changes**:
1. Add normalization function: `normalize_dataset_name(name)` → lowercase, remove suffixes
2. Store both `display_name` (original) and `normalized_name` (for lookups)
3. Update `list_available()` to return normalized names
4. Ensure all components use normalized names for baseline lookups

### Fix 2: Model Identity Mapping

**New File**: `src/sure_eval/models/model_mapping.py`

**Content**:
```python
TOOL_TO_BENCHMARK = {
    "asr_qwen3": "Qwen3-ASR-1.7B",  # Maps local tool to report name
    "asr_whisper": "Whisper-large-v3",
    # ...
}

BENCHMARK_TO_TOOL = {v: k for k, v in TOOL_TO_BENCHMARK.items()}
```

**Integration Points**:
- Agent uses this to determine which SOTA to compare against
- ReportManager uses this to find local tool results

### Fix 3: Connect Evaluation to Reports

**Location**: `src/sure_eval/agent/evaluator.py`

**Changes**:
1. After evaluation, auto-lookup SOTA from ReportManager
2. Calculate RPS using SOTA baseline
3. Save result to evaluation database
4. Option to update model report with new results

---

## Verification Checklist

After fixes, verify:
- [ ] Can evaluate a tool and get correct RPS
- [ ] RPS calculation uses correct SOTA baseline
- [ ] Evaluation results appear in reports
- [ ] Can compare local tool against benchmark SOTA
- [ ] All dataset names are consistent
- [ ] Model mapping works bidirectionally

---

## Appendix: Component Dependencies

```
sure-eval/
├── Data Pipeline
│   ├── download_sure_data.py ──→ SURE Benchmark datasets
│   ├── convert_sure_to_jsonl.py ──→ JSONL format
│   └── dataset_manager.py ──→ Used by Agent
│
├── Tool Management
│   ├── models/registry.py ──→ Local tool discovery
│   ├── models/<model>/ ──→ Tool implementations
│   └── tools/mcp_client.py ──→ Tool execution
│
├── Evaluation
│   ├── sure_evaluator.py ──→ Metrics computation
│   └── rps.py ──→ RPS calculation (needs SOTA)
│
├── Reports
│   ├── sota/sota_baseline.yaml ──→ SOTA definitions
│   ├── models/model_performance_report.json ──→ Benchmark results
│   └── report_manager.py ──→ Report queries (read-only)
│
└── Agent
    └── evaluator.py ──→ Orchestration (needs fixes)
```

**Key Missing Links**:
1. DatasetManager ↔ RPS baselines (naming mismatch)
2. ToolRegistry ↔ ReportManager (no mapping)
3. Agent ↔ Reports (no auto-update)
