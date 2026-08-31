# Common Model Tool Agent Memory

This file is safe to read for every model onboarding task. Keep it short and
general. Task-specific, backend-specific, and failure-specific details belong in
routed files.

## Always Preserve These Invariants

- Start from evidence: upstream README, dependency files, existing model code,
  error logs, and model-local artifacts.
- Keep model changes inside `sure/models/<model_name>/` unless the task
  explicitly requires shared harness changes. `model_name` must be the
  single-segment normalized id derived from the provider id by replacing `/`
  with `__`; never derive it from task prefixes or informal aliases.
- Docker image tags must align with the same repo id slug. The default
  `docker-registry` profile requires registry push and immutable digest pull
  verification; an explicit `package=none` follows the sealed Python policy.
- Validate `model.spec.yaml` before building the runtime.
- Keep weights and provider caches model-local by default.
- Validate the four minimum checks: import, load, infer, output contract.
- Do not treat sandbox-only CUDA visibility failures as final GPU evidence; if
  sandbox and host GPU evidence conflict, route to
  `memory/bad_cases/sandbox_cuda_visibility.md`.
- Save structured artifacts: `backend_choice.json`, `build.log`,
  `validation.log`, `sample_output.json`, `verdict.json`, and
  `artifact_manifest.json`.
- For every local model, finish the selected delivery profile: publish a
  digest-pinned container binding by default, or materialize and seal the exact
  site-approved uv Model Runtime for explicit `package=none`.

## Context Selection Rule

Use routing before reading optional memory:

1. Read `task_playbooks/ROUTING.md` and select task playbooks.
2. Read `playbooks/env_ROUTING.md` and select environment playbooks.
3. Read `<run_dir>/artifacts/memory_context.json` once if it exists (hook-written
   memory facts for this run).
4. Read `memory/ROUTING.md` only when a failure, ambiguity, or known difficult
   case appears; it points at `sure/memory/index.md`, the merged memory index.

Do not load all task playbooks, all environment playbooks, or all bad cases by
default.

## Artifact Recording

Record the memory files you actually read in `context_selection.json` under
`selected_references.memory`; the schema has no other memory field:

```json
{
  "task_type": "asr",
  "selected_references": {
    "default": ["references/AGENTS.md", "references/memory/COMMON.md", "references/task_playbooks/ROUTING.md", "references/playbooks/env_ROUTING.md"],
    "task_playbooks": ["references/task_playbooks/ASR.md"],
    "environment_playbooks": ["references/playbooks/env_uv.md"],
    "contracts": ["references/contracts/spec_validation.md"],
    "memory": ["references/memory/COMMON.md", "sure/memory/index.md"]
  },
  "rationale": "short evidence-based reason"
}
```
