# Evidence Priority Policy

**Version**: 1.0  
**Scope**: Builder Agent, Evaluator Agent  
**Purpose**: Define how agents should prioritize evidence when multiple sources conflict

---

## Priority Hierarchy

When evidence sources conflict, agents must use the following priority order:

### 1. Verified Executable Behavior (Highest)

**Definition**: Runtime execution results that can be reproduced

**Sources**:
- Successful/failed import test
- Model load execution result
- Inference output on fixture
- Error stack traces with line numbers
- Host/runtime probes that deterministically block execution (e.g. `nvidia-smi`, CUDA init failure, tmpdir no-space)

**Usage**: Override all other evidence when available

**Recording**: Must be captured in `validation.log` with timestamp

---

### 2. Runtime Logs and Direct Failure Evidence

**Definition**: System-generated logs from failed attempts

**Sources**:
- Python tracebacks
- Package manager error output
- CUDA/GPU driver messages
- Network timeout logs

**Usage**: Primary input for `DIAGNOSE` phase

**Recording**: Raw log excerpt saved to `artifacts/logs/`

---

### 3. Repository-Native Configuration Files

**Definition**: Machine-readable config in the repo

**Sources** (in priority order):
- `pyproject.toml` (PEP 621, modern standard)
- `setup.py` / `setup.cfg` (legacy but explicit)
- `requirements.txt` (runtime deps)
- `environment.yml` (conda env)
- `Dockerfile` / `docker-compose.yml` (container)
- `Cargo.toml`, `package.json` (non-Python deps)

**Conflict Resolution**:
- When multiple present, prefer most specific to the backend
- Record conflicting declarations and chosen source

**Recording**: File hash and chosen values in `backend_choice.json`

---

### 4. Official Demo / Example Code

**Definition**: Executable code in repo's examples/ or demo/ directories

**Sources**:
- `examples/inference.py`
- `demo/app.py`
- `notebooks/tutorial.ipynb`
- Test files in `tests/`

**Usage**: Infer entrypoint signature, expected input/output format

**Caution**: May be outdated; verify against (1) if possible

**Recording**: Path to referenced example, noted if verification attempted

---

### 5. README Prose

**Definition**: Human-readable documentation

**Sources**:
- `README.md`
- `docs/installation.md`
- Model card on HuggingFace

**Usage**: High-level guidance only—never infer specific versions or commands

**Limitation**: Often outdated or optimistic; must cross-check with (3) and (1)

**Recording**: Quote relevant section, flag if untested

---

### 6. External Discussion / Assumptions (Lowest)

**Definition**: Information not in the repo

**Sources**:
- GitHub issues (unresolved)
- Forum discussions
- Stack Overflow answers
- Agent's training data assumptions

**Usage**: Generate hypotheses only, never base decisions solely on this

**Requirement**: Must be verified against (1) or (3) before use

**Recording**: Mark as "unverified assumption" with source URL

---

## Conflict Resolution Required Behavior

When agents encounter conflicts between evidence sources:

### Builder Agent (PLAN phase)

1. List all conflicting sources
2. State which priority level each belongs to
3. Document resolution choice with explicit reason
4. Output to `backend_choice.json`:

```json
{
  "evidence_conflicts": [
    {
      "field": "python_version",
      "sources": [
        {"value": "3.8", "source": "README.md", "priority": 5},
        {"value": "3.10", "source": "pyproject.toml", "priority": 3}
      ],
      "chosen": "3.10",
      "reason": "pyproject.toml is repository-native config, higher priority than README prose"
    }
  ]
}
```

### Evaluator Agent (DIAGNOSE phase)

1. Extract failure symptoms from runtime logs (priority 2)
2. Cross-reference with host/runtime preflight and repository config
3. Generate diagnosis based on highest-priority available evidence
4. Mark any assumptions as "requires verification"

---

## Forbidden Patterns

Agents must NOT:

1. **Guess version numbers** from README prose without explicit statement
2. **Assume compatibility** based on package name alone
3. **Ignore runtime errors** that contradict documentation
4. **Use training data** as primary evidence for version pins
5. **Override executable failure** with "it should work according to docs"

---

## Example Scenarios

### Scenario 1: README says Python 3.8+, pyproject.toml requires 3.10

**Resolution**: Use 3.10, record conflict and resolution
**Rationale**: pyproject.toml (priority 3) > README (priority 5)

### Scenario 2: Import test fails with "No module named 'nemo'", but requirements.txt lists nemo

**Resolution**: Build env failed to install, check build.log
**Rationale**: Runtime error (priority 2) reveals requirements.txt not satisfied

### Scenario 3: GitHub issue suggests workaround, official docs don't mention it

**Resolution**: Do not apply workaround without verification
**Rationale**: External discussion (priority 6) must not override official docs (priority 5) without evidence

### Scenario 4: README says GPU is required, runtime probe shows CUDA init fails

**Resolution**: Trust runtime probe, then decide whether CPU fallback still satisfies phase-1 target
**Rationale**: Executable environment evidence outranks README prose and hints

---

## Compliance Check

Harness Controller must verify that:

- All decisions in `backend_choice.json` cite evidence source
- No critical field (python_version, torch_version, entrypoint) is set without evidence
- All assumptions are marked as such
