# Retry and Escalation Policy

**Version**: 1.0  
**Scope**: Harness Controller, Evaluator Agent  
**Purpose**: Define bounded retry behavior and escalation thresholds

---

## Core Principle

**Targeted retry only**: Retries must address an identified failure class. Blind repetition is prohibited.

---

## Retry Rules

### 1. Retry Preconditions

Retry is allowed ONLY when:

- [ ] `DIAGNOSE` has produced a `failure_type` from `failure_taxonomy`
- [ ] `REPLAN` specifies what will change (dependency version, backend, config, approach)
- [ ] Change is targeted at the diagnosed failure class
- [ ] Retry count for this checkpoint is < 3

### 2. Retry Prohibitions

Retry is NOT allowed when:

- No `failure_type` identified (blind retry)
- Same `failure_type` as previous attempt without new mitigation (repetition)
- Retry count >= 3
- Failure type is `host_level_side_effect` or `security_risk`
- Human escalation has been requested

### 3. What Can Change in Retry

| Aspect | Can Change? | Example |
|--------|-------------|---------|
| Backend | Yes | uv → docker |
| Python version | Yes | 3.11 → 3.10 |
| Dependency version | Yes | torch==2.5.0 → torch==2.4.0 |
| System packages | Yes | Add libsndfile1 |
| Model entrypoint | Yes | Different import path |
| Upstream source code | No (without patch recording) | - |
| Host configuration | No | - |

---

## Escalation Triggers

Escalation to human review is REQUIRED when any of the following occur:

### Immediate Escalation (No Retry)

| Trigger | Reason |
|---------|--------|
| `host_level_side_effect` | Risk to system beyond model directory |
| `security_risk` | Credential exposure, unauthorized access |
| `untracked_patch_required` | Must modify upstream source to proceed |
| `missing_critical_evidence` | Cannot determine python_version, entrypoint, or weight source |
| `repeated_unresolved_failure` | Same checkpoint failed 3 times with different causes |

### Escalation After Retry Exhaustion

| Trigger | Retry Allowance |
|---------|-----------------|
| `dependency_unresolvable` | 2 attempts (different versions/backends) |
| `cuda_version_incompatible` | 2 attempts (CPU fallback, different CUDA) |
| `weight_download_failed` | 2 attempts (mirror sources, authentication) |
| `runtime_backend_incompatible` | 2 attempts (version pinning, backend switch) |

### Informational Escalation (Continue with Warning)

| Trigger | Action |
|---------|--------|
| `performance_degradation` | Continue, mark in verdict |
| `optional_feature_unavailable` | Continue, document limitation |
| `fixture_missing` | Continue with simplified test, note in report |

---

## Failure Type Decision Matrix

| failure_type | retryable | max_retries | preferred_action | escalate_when |
|--------------|-----------|-------------|------------------|---------------|
| `python_dependency_missing` | ✅ | 2 | Reinstall with version pin | Same error twice |
| `system_dependency_missing` | ✅ | 2 | Add to system_packages, switch to docker | Docker also fails |
| `cuda_version_mismatch` | ✅ | 2 | Fallback to CPU, or downgrade PyTorch | Inference too slow or still fails |
| `wrong_python_version` | ✅ | 2 | Recreate env with correct version | Version not available |
| `missing_weights` | ✅ | 2 | Retry download, check auth, use mirror | All sources exhausted |
| `wrong_entrypoint` | ✅ | 2 | Update wrapper, check model.spec.yaml | Cannot determine correct entrypoint |
| `config_not_set` | ✅ | 1 | Set env var, update config.yaml | Key not available |
| `runtime_backend_incompatible` | ✅ | 2 | Pin compatible versions | No compatible version found |
| `host_level_side_effect` | ❌ | 0 | N/A | Immediate escalation |
| `security_risk` | ❌ | 0 | N/A | Immediate escalation |
| `untracked_patch_required` | ❌ | 0 | N/A | Immediate escalation |
| `missing_critical_evidence` | ❌ | 0 | N/A | Immediate escalation |

---

## Retry Workflow

```
FAIL at CHECKPOINT_X
    ↓
DIAGNOSE -> failure_type
    ↓
CHECK: Is retryable?
    ├── NO -> ESCALATE immediately
    ↓
CHECK: retry_count < max_retries?
    ├── NO -> ESCALATE
    ↓
REPLAN -> specify changes
    ↓
RETRY_FROM_CHECKPOINT_X with changes
    ↓
SUCCESS -> continue
FAIL (same failure_type) -> ESCALATE
FAIL (new failure_type) -> DIAGNOSE again
```

---

## Escalation Format

When escalating, harness must generate:

```json
{
  "escalation": {
    "trigger": "missing_critical_evidence",
    "checkpoint": "PLAN",
    "attempts": 1,
    "context": {
      "failure_type_history": ["wrong_entrypoint"],
      "last_error": "No module named 'model'",
      "evidence_available": ["README.md", "examples/"],
      "evidence_missing": ["pyproject.toml", "requirements.txt"]
    },
    "artifacts": [
      "artifacts/build.log",
      "artifacts/validation.log",
      "model.spec.yaml"
    ],
    "question_for_human": "Cannot determine model entrypoint. README mentions multiple classes, no setup.py or pyproject.toml found. Please specify: (1) main model class name, (2) import path."
  }
}
```

---

## Human Override

Human reviewer can:

1. **Waive escalation** and allow additional retries
2. **Provide missing evidence** to unblock
3. **Approve untracked patch** with rationale
4. **Accept degraded mode** (e.g., CPU-only when GPU fails)

All overrides must be recorded in `verdict.json` with human identifier.
