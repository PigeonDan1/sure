# SURE Model Onboarding Constitution

**Version**: 1.0  
**Scope**: All harness components and agents  
**Authority**: Invariant rules, override only by explicit human decision

---

## Purpose

This constitution defines invariant rules that all harness components and agents must follow during model onboarding. These rules are not operational guidelines—they are constraints that, if violated, compromise reproducibility, auditability, or safety.

---

## Core Principles

### 1. Evidence before assumption

**Rule**: When repository metadata, README text, and runtime evidence conflict, prefer executable evidence.

**Constraint on Agent/Harness**:
- Builder Agent must record chosen evidence and rejected alternatives in `backend_choice.json`
- Evaluator Agent must cite specific log lines when diagnosing failures
- Never infer critical fields (entrypoint, dependency version, hardware requirement) without executable evidence

**Violation handling**: Escalate to human review when evidence is insufficient for a critical decision.

---

### 2. Reproducibility over convenience

**Rule**: No onboarding result is considered successful unless the environment and inference path can be reconstructed from saved artifacts.

**Constraint on Agent/Harness**:
- `BUILD_ENV` must produce a lockfile or container recipe
- `FETCH_WEIGHTS` must record weight source, version, and local path
- `SAVE_ARTIFACTS` must include all files necessary for reconstruction

**Violation handling**: Mark build as FAILED if reproducibility artifacts cannot be generated.

---

### 3. No untracked mutation

**Rule**: Any modification to upstream code, configuration, or dependency pins must be recorded with rationale.

**Constraint on Agent/Harness**:
- Wrapper-layer adaptation preferred over source modification
- All patches saved to `artifacts/patches/` with diff and rationale
- `patch_report.json` must list all mutations and their justifications

**Violation handling**: Reject any build that modifies upstream source without recording patch.

---

### 4. Controlled side effects only

**Rule**: Do not modify host-level state, secrets, or unrelated files unless explicitly allowed by harness policy.

**Constraint on Agent/Harness**:
- All operations confined to model-specific directory
- No system package installation without isolation (container/conda)
- No modification to global git config, ssh keys, or environment variables outside model directory

**Violation handling**: Immediate stop and escalation on any host-level mutation attempt.

---

### 5. Structured decisions only

**Rule**: Backend selection, entrypoint choice, and failure diagnosis must be emitted as structured outputs with evidence.

**Constraint on Agent/Harness**:
- Builder Agent outputs: `backend_choice.json`, `build_plan.json`
- Evaluator Agent outputs: `failure_classification.json`, `retry_recommendation.json`
- No free-text decisions without accompanying structured record

**Violation handling**: Reject agent output if required structured fields are missing.

---

### 6. Targeted retry only

**Rule**: Retries must address an identified failure class. Blind repetition of the same step is prohibited.

**Constraint on Agent/Harness**:
- Retry only after `DIAGNOSE` produces `failure_type`
- `REPLAN` must specify what changed (dependency version, backend, config)
- Same `failure_type` twice requires escalation, not third retry

**Violation handling**: Block retry if `failure_type` is null or same as previous attempt.

---

### 7. Escalate on unresolved uncertainty

**Rule**: When critical evidence is missing or repeated attempts fail without progress, stop and escalate to human review.

**Constraint on Agent/Harness**:
- Maximum 3 retry attempts per checkpoint
- Escalation triggers: missing critical evidence, repeated unresolved failure, host-risk operation requested
- Escalation must include full artifact bundle and failure context

**Violation handling**: Hard stop after 3 failures or on escalation trigger.

---

### 8. Contract before completion

**Rule**: A model is not considered onboarded unless import, load, infer, and output contract checks pass or are explicitly waived with justification.

**Constraint on Agent/Harness**:
- `VALIDATE_IMPORT`, `VALIDATE_LOAD`, `VALIDATE_INFER`, `VALIDATE_CONTRACT` are mandatory gates
- `verdict.json` must record all four validation results
- Waivers allowed only with human approval and documented rationale

**Violation handling**: `verdict.status` must be FAILED if any validation fails without waiver.

---

### 9. Least-risk execution

**Rule**: Prefer the lowest-risk backend that still satisfies compatibility and reproducibility constraints.

**Constraint on Agent/Harness**:
- Backend priority: uv > pixi > docker (for equivalent functionality)
- Docker required only when: CUDA compilation, complex system deps, host pollution risk
- API backend only for remote-only models

**Violation handling**: Require explicit justification in `backend_choice.json` if higher-risk backend selected.

---

### 10. Model-specific exceptions remain local

**Rule**: Model-specific hacks, workarounds, or incompatibilities must remain in registry/playbook layers and must not be promoted into global rules without evidence.

**Constraint on Agent/Harness**:
- Per-model workarounds recorded in `docs/registry/known_issues.md`
- No model-specific logic in harness controller
- Global policy changes require evidence of cross-model applicability

**Violation handling**: Reject PR that embeds model-specific logic into harness or constitution.

---

## Amendment Process

This constitution may be amended only by:

1. Demonstrated evidence that a rule causes systematic failure
2. Human review and explicit approval
3. Version bump and change log entry
4. Notification to all active harness instances

**No agent may override constitution without human approval.**
