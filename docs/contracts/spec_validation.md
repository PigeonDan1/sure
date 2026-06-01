# Spec Validation Contract

**Version**: 1.0  
**Scope**: Harness Controller (PLAN → BUILD_ENV transition)  
**Purpose**: Validate whether the planned onboarding inputs are sufficient and trustworthy before BUILD_ENV starts

---

## Purpose

`VALIDATE_SPEC` is a mandatory gate between `PLAN` and `BUILD_ENV`. Its purpose is to verify that:

- The provided spec is complete and internally consistent
- The runtime identity has been resolved to a single credible callable target
- Backend choice has sufficient evidence
- Build plan is executable
- Critical conflicts have been resolved
- Fixtures and io_contract are sufficient for subsequent validation

**Principle**: Do not start environment construction until the "paper plan" is validated as credible and complete.

---

## Validation Checks

### 1. Spec Completeness

**Check**: All required fields in `model.spec.yaml` are present

**Required fields**:
- `model_id`
- `model_name`
- `task_type`
- `deployment_type`
- `repo.url`
- `weights.source`
- `environment.preferred_backend`
- `environment.python_version`
- `entrypoints.import_test`
- `entrypoints.load_test`
- `entrypoints.infer_test`
- `io_contract.input_type`
- `io_contract.output_type`
- `acceptance.must_import`
- `acceptance.must_load`
- `acceptance.must_infer`

**Failure**: Missing required field → cannot proceed to BUILD_ENV

---

### 2. Evidence Sufficiency

**Check**: Critical fields have evidence support recorded

**Critical fields**:
- `model_name` / runtime load identity (must point to a single resolved callable target)
- `environment.preferred_backend` (must cite evidence in `backend_choice.json`)
- `entrypoints.infer_test` (must have fixture available)
- `weights.local_path` or `weights.source` (must be reachable)

**Failure**: Critical field without evidence → enter DIAGNOSE

---

### 3. Conflict Resolution

**Check**: All evidence conflicts recorded in `backend_choice.json` have resolutions

**Format verification**:
```json
{
  "evidence_conflicts": [
    {
      "field": "python_version",
      "resolved": true,
      "chosen_value": "3.10",
      "reason": "pyproject.toml is repository-native config, higher priority than README"
    }
  ]
}
```

**Failure**: Unresolved conflict → must resolve or escalate

---

### 4. Build Plan Executability

**Check**: `build_plan.json` contains executable steps

**Verification**:
- All required package managers are available (verified in preflight)
- All source URLs are reachable
- No circular dependencies
- Disk space sufficient (verified in preflight)
- Temporary extraction/cache strategy is explicit when weights are large or archive restore is expected

**Failure**: Build plan not executable → REPLAN with corrections

---

### 5. Fixture Availability

**Check**: Test fixtures required by `entrypoints.infer_test` are available

**Sources** (in priority order):
1. Model-specific fixture path (if specified in spec)
2. Task-type shared fixture (`tests/fixtures/shared/{task_type}/`)
3. Dataset-specific fixture (`tests/fixtures/{dataset}/`)

**Failure**: Fixture not found → mark in spec_validation.json, may proceed with warning if fallback available

**Additional rule**:
- Fixture fallback may narrow the test asset only when runtime identity has already been resolved to a narrower callable target
- If runtime identity has not changed, fallback must preserve the original task semantics

---

### 6. IO Contract Sufficiency

**Check**: `io_contract` is sufficient for contract test

**Minimum requirements**:
- `input_type` and `output_type` specified
- `primary_field` identified (for non-structured output)
- `required_fields` list provided (for structured output)

**Failure**: Insufficient io_contract → cannot perform VALIDATE_CONTRACT

---

### 7. Preflight Compatibility

**Check**: `backend_choice` is compatible with `preflight_summary`

**Compatibility rules**:
- If `preflight.gpu.available == false` and `model.requires_gpu == true` → warning
- If `preflight.package_managers.uv == false` and `backend == 'uv'` → error
- If `preflight.disk_space < required` → error
- If runtime preflight indicates CUDA initialization risk, build plan must record CPU fallback or mark GPU-only execution as blocked
- If runtime preflight indicates default TMPDIR/extract path is insufficient, build plan must record an alternate writable path

**Failure**: Incompatibility → REPLAN with adjusted backend

---

## Output Format

`spec_validation.json`:

```json
{
  "timestamp": "2024-03-27T21:30:00Z",
  "model_id": "nvidia/parakeet-tdt-0.6b-v2",
  "status": "passed",
  "checks": {
    "spec_completeness": {
      "passed": true,
      "missing_fields": []
    },
    "evidence_sufficiency": {
      "passed": true,
      "unsupported_fields": []
    },
    "conflict_resolution": {
      "passed": true,
      "unresolved_conflicts": []
    },
    "build_plan_executable": {
      "passed": true,
      "blocking_issues": []
    },
    "fixture_availability": {
      "passed": true,
      "fixture_path": "tests/fixtures/shared/asr/en_16khz_5s.wav",
      "source": "shared"
    },
    "io_contract_sufficient": {
      "passed": true,
      "missing_contract_fields": []
    },
    "preflight_compatible": {
      "passed": true,
      "incompatibilities": []
    }
  },
  "ready_for_build_env": true,
  "warnings": [],
  "blockers": []
}
```

---

## Failure Behavior

If any check fails:

1. **Status**: Set to `failed`
2. **Ready flag**: Set `ready_for_build_env: false`
3. **Action**: Enter DIAGNOSE / REPLAN
4. **Constraint**: **Do not proceed to BUILD_ENV**

**Failure classification**:
- `spec_incomplete`: Missing required fields
- `insufficient_evidence`: Critical fields lack evidence
- `runtime_identity_unresolved`: runtime target not resolved to a single callable identity
- `unresolved_conflict`: Evidence conflicts not resolved
- `build_plan_unexecutable`: Cannot execute build steps
- `fixture_unavailable`: No test fixture found
- `io_contract_insufficient`: Cannot support contract test
- `preflight_incompatible`: Backend choice conflicts with environment

---

## Integration with Workflow

```
PLAN
  ↓ (生成 spec, backend_choice, build_plan)
VALIDATE_SPEC
  ↓ (验证上述输入是否可信且完整)
  ├── PASSED → BUILD_ENV
  └── FAILED → DIAGNOSE / REPLAN
```

**Key principle**: VALIDATE_SPEC is the "paper validation" gate. If the plan itself is flawed, don't waste time building an environment that cannot succeed.
