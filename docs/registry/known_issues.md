# Known Issues Registry

**Version**: 1.0  
**Scope**: Model-specific exceptions and workarounds  
**Purpose**: Track per-model issues without polluting global policy

---

## Entry Format

Each known issue must follow this structure:

```json
{
  "model_id": "nvidia/parakeet-tdt-0.6b-v2",
  "issue_id": "PARAKEET-001",
  "symptom": "ImportError: np.sctypes was removed",
  "condition": "NumPy >= 2.0",
  "severity": "blocking",
  "workaround": {
    "description": "Pin NumPy to 1.26.4",
    "implementation": "uv pip install numpy==1.26.4",
    "temporary": true
  },
  "upstream_issue": null,
  "affected_versions": ["nemo-toolkit<=2.2.0"],
  "resolved_in": null,
  "reproducibility_impact": "must_apply_patch",
  "notes": "NeMo 2.3.0 expected to fix"
}
```

---

## Severity Levels

| Level | Meaning | Action |
|-------|---------|--------|
| `blocking` | Prevents successful onboarding | Must apply workaround or escalate |
| `degraded` | Onboarding possible with limitations | Document limitation, continue |
| `warning` | Cosmetic or minor issue | Note in verdict, no action required |

---

## Reproducibility Impact

| Value | Meaning |
|-------|---------|
| `none` | Workaround not needed for reproduction |
| `document_only` | Workaround must be documented but not applied |
| `must_apply_patch` | Workaround required for successful build |
| `requires_host_config` | Host-level change needed (document and warn) |

---

## Registered Issues

### ASR Models

#### PARAKEET-001: NumPy 2.0 Compatibility

```json
{
  "model_id": "nvidia/parakeet-tdt-0.6b-v2",
  "issue_id": "PARAKEET-001",
  "symptom": "AttributeError: np.sctypes was removed in NumPy 2.0",
  "condition": "NumPy >= 2.0 && nemo-toolkit <= 2.2.0",
  "severity": "blocking",
  "workaround": {
    "description": "Pin NumPy to 1.26.4",
    "implementation": "uv pip install numpy==1.26.4",
    "temporary": true,
    "patch_file": null
  },
  "upstream_issue": null,
  "affected_versions": ["nemo-toolkit<=2.2.0"],
  "resolved_in": null,
  "reproducibility_impact": "must_apply_patch",
  "notes": "NeMo uses deprecated NumPy API. Expected fix in NeMo 2.3.0"
}
```

#### PARAKEET-002: Torchvision Version Mismatch

```json
{
  "model_id": "nvidia/parakeet-tdt-0.6b-v2",
  "issue_id": "PARAKEET-002",
  "symptom": "RuntimeError: operator torchvision::nms does not exist",
  "condition": "torchvision version mismatches PyTorch",
  "severity": "blocking",
  "workaround": {
    "description": "Install matching torchvision version",
    "implementation": "uv pip install torchvision==0.19.0 --index-url https://download.pytorch.org/whl/cpu",
    "temporary": true
  },
  "upstream_issue": null,
  "affected_versions": ["torch>=2.4.0"],
  "resolved_in": null,
  "reproducibility_impact": "must_apply_patch",
  "notes": "Must match torchvision to PyTorch version"
}
```

### VAD Models

#### SILERO-001: torchaudio/torchcodec CPU Trap

**Model**: `snakers4/silero-vad`  
**Issue**: torchaudio high-version triggers unnecessary CUDA dependency  
**Symptom**: RuntimeError on torchcodec/CUDA libs despite CPU-only requirement  
**Resolution**: Use CPU-only PyTorch stack (torch==2.2.2+cpu, torchaudio==2.2.2+cpu)  

**Model-level details**: [`src/sure_eval/models/snakers4_silero-vad/known_issues.yaml`](../../../src/sure_eval/models/snakers4_silero-vad/known_issues.yaml)

### Diarization Models

#### DIARIZEN-001: Short Audio Bug

```json
{
  "model_id": "BUT-FIT/diarizen-wavlm-large-s80-md",
  "issue_id": "DIARIZEN-001",
  "symptom": "UnboundLocalError: cannot access local variable 'segmentations'",
  "condition": "Audio duration < 16 seconds",
  "severity": "degraded",
  "workaround": {
    "description": "Use audio >= 30 seconds for testing",
    "implementation": "Select longer fixtures or pad audio",
    "temporary": false
  },
  "upstream_issue": "https://github.com/...",
  "affected_versions": ["all"],
  "resolved_in": null,
  "reproducibility_impact": "document_only",
  "notes": "PyAnnote.audio limitation, not DiariZen-specific"
}
```

---

## When to Add Entry

Add a known issue when:

1. **Issue is model-specific**: Doesn't affect all models of same task type
2. **Workaround exists**: Known mitigation, even if temporary
3. **Issue is reproducible**: Not a one-off environment glitch
4. **Issue is documented**: Sufficient detail for another agent to apply workaround

Do **not** add entry when:

1. **Issue is generic**: Applies to all models (belongs in playbooks)
2. **No workaround**: Pure upstream bug with no mitigation
3. **Issue is transient**: Network timeout, disk full, etc.
4. **Issue is already fixed**: Only track current or unresolved issues

---

## When to Promote to Global Policy

A model-specific issue may be promoted to global policy when:

1. **Pattern emerges**: Same issue appears in 3+ models
2. **Root cause is common**: Shared dependency, common pattern
3. **Workaround is universal**: Same fix applies broadly

Promotion process:
1. Document in `docs/playbooks/` or `docs/policies/`
2. Keep registry entry with link to global policy
3. Mark registry entry as `promoted_to_policy: true`

---

## Integration with Harness

### Builder Agent

Before backend selection, check registry:
- Query `known_issues.md` for model_id
- Apply workarounds to build plan
- Record applied workarounds in `backend_choice.json`

### Evaluator Agent

During diagnosis:
- Check if failure matches known issue symptom
- Suggest registered workaround
- If workaround fails, escalate rather than invent new patch

### Harness Controller

Before marking SUCCESS:
- Verify all `must_apply_patch` workarounds are recorded
- Include known issues in `verdict.json`

---

## Maintenance

**Review cycle**: Monthly

**Actions**:
- Check if upstream issues are resolved
- Update `resolved_in` field
- Remove entries older than 6 months that are resolved
- Promote patterns to global policy

---

## Template for New Entry

```markdown
### MODEL-XXX: Brief Description

```json
{
  "model_id": "org/model-name",
  "issue_id": "MODEL-XXX",
  "symptom": "Error message or behavior",
  "condition": "When does it occur",
  "severity": "blocking|degraded|warning",
  "workaround": {
    "description": "What to do",
    "implementation": "Command or code",
    "temporary": true|false
  },
  "upstream_issue": "URL or null",
  "affected_versions": ["version-range"],
  "resolved_in": "version or null",
  "reproducibility_impact": "none|document_only|must_apply_patch|requires_host_config",
  "notes": "Additional context"
}
```
```
