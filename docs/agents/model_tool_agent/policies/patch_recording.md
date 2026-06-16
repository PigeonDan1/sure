# Patch Recording Policy

**Version**: 1.0  
**Scope**: Builder Agent, Harness Controller  
**Purpose**: Ensure all non-upstream modifications are tracked and reproducible

---

## Definition of "Patch"

A "patch" is any modification to:

1. **Upstream source code**: Files cloned from the model repository
2. **Dependency specifications**: Changes to version pins after initial lock
3. **Configuration files**: Runtime config modifications
4. **Build scripts**: Changes to setup.sh, Dockerfile after generation

**Not considered patches**:
- New files created by harness (model.py, server.py, __init__.py)
- Files in `artifacts/` directory
- Virtual environment contents
- Generated lockfiles

---

## Required Rules

### Rule 1: Any Mutation Must Be Recorded

**Requirement**: Every patch must have a corresponding entry in `artifacts/patch_report.json`

**Format**:
```json
{
  "patches": [
    {
      "id": "patch-001",
      "file": "upstream/src/model.py",
      "type": "source_modification",
      "diff_path": "artifacts/patches/patch-001.diff",
      "rationale": "Fixed import path for local structure",
      "temporary": true,
      "local_only": true,
      "required_for_repro": false
    }
  ]
}
```

### Rule 2: Prefer Wrapper-Layer Adaptation

**Hierarchy of solutions** (prefer lower numbers):

1. **Wrapper adaptation**: Modify `model.py` to handle upstream quirks
2. **Entrypoint shim**: Create adapter function without changing upstream
3. **Patch with upstream PR**: Modify upstream, plan to contribute back
4. **Permanent local fork**: Maintain separate fork (last resort)

**Requirement**: Document why wrapper adaptation was insufficient before applying patch.

### Rule 3: Save Patch Diff and Rationale

**Required artifacts**:
- `artifacts/patches/{patch-id}.diff`: Unified diff of changes
- `artifacts/patches/{patch-id}.rationale.txt`: Human-readable explanation

**Diff generation**:
```bash
git diff > artifacts/patches/patch-001.diff
# OR
diff -u original.py modified.py > artifacts/patches/patch-001.diff
```

### Rule 4: Mark Patch Lifecycle

Every patch must be classified:

| Field | Values | Meaning |
|-------|--------|---------|
| `temporary` | true/false | Will be removed when upstream fixes |
| `local_only` | true/false | Only needed for this specific host/setup |
| `required_for_repro` | true/false | Must be applied for successful onboarding |

**Examples**:
- Hotfix for urgent bug: `temporary=true, required_for_repro=true`
- Host-specific path: `temporary=false, local_only=true`
- Upstream already merged: `temporary=true, required_for_repro=false`

### Rule 5: Human Confirmation for Permanent Patches

**Automatic approval**: `temporary=true` patches

**Human confirmation required**: `temporary=false` patches

Confirmation must include:
- Why wrapper adaptation was insufficient
- Plan for upstream contribution or long-term maintenance
- Acceptable alternatives considered

---

## Patch Categories

### Category 1: Dependency Version Pin

**When**: Upstream specifies loose version, but only specific version works

**Recording**:
```json
{
  "patch_id": "pin-numpy-1.26.4",
  "type": "dependency_pin",
  "original": "numpy>=1.20",
  "modified": "numpy==1.26.4",
  "rationale": "NumPy 2.0 removed np.sctypes, required by nemo-toolkit 2.2.0"
}
```

### Category 2: Import Path Fix

**When**: Upstream import structure doesn't match local layout

**Recording**:
```json
{
  "patch_id": "fix-import-path",
  "type": "import_path",
  "original": "from src.model import Model",
  "modified": "from model import Model",
  "rationale": "Repo structure changed after packaging"
}
```

### Category 3: Configuration Default

**When**: Upstream requires manual config, but sensible default exists

**Recording**:
```json
{
  "patch_id": "default-device",
  "type": "config_default",
  "original": "device = None  # must be set",
  "modified": "device = 'cpu'",
  "rationale": "Allow import test to pass without GPU"
}
```

### Category 4: Bug Workaround

**When**: Known upstream bug with available workaround

**Recording**:
```json
{
  "patch_id": "workaround-short-audio",
  "type": "bug_workaround",
  "upstream_issue": "https://github.com/org/repo/issues/123",
  "rationale": "Skip audio < 16s to avoid UnboundLocalError in pyannote"
}
```

---

## Forbidden Patterns

**Never**:

1. Apply patches without recording
2. Patch upstream source when wrapper adaptation would suffice
3. Mark obviously temporary patches as permanent
4. Apply security-sensitive patches without human review
5. Patch without checking if upstream has fixed in newer version

---

## Patch Verification

Harness Controller must verify:

- [ ] `patch_report.json` exists if any source files modified
- [ ] Each patch has corresponding `.diff` file
- [ ] Each patch has `.rationale.txt` explaining why
- [ ] No tracked files are modified without patch entry

---

## Example Patch Report

```json
{
  "model_id": "nvidia/parakeet-tdt-0.6b-v2",
  "timestamp": "2024-03-27T21:30:00Z",
  "patches": [
    {
      "id": "pin-numpy",
      "files": ["pyproject.toml"],
      "type": "dependency_pin",
      "diff_path": "artifacts/patches/pin-numpy.diff",
      "rationale": "NumPy 2.0 removed np.sctypes, breaking nemo-toolkit",
      "temporary": true,
      "local_only": false,
      "required_for_repro": true,
      "upstream_fix": "Wait for nemo-toolkit 2.3.0"
    }
  ],
  "summary": {
    "total_patches": 1,
    "temporary": 1,
    "permanent": 0,
    "requires_human_review": 0
  }
}
```
