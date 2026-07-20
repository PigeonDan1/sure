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
- Docker image tags, when optional Docker packaging is requested, must align
  with the same repo id slug. Docker is not required for `package=none`.
- Validate `model.spec.yaml` before building the runtime.
- Keep weights and provider caches model-local by default.
- Validate the four minimum checks: import, load, infer, output contract.
- Do not treat sandbox-only CUDA visibility failures as final GPU evidence; if
  sandbox and host GPU evidence conflict, route to
  `memory/bad_cases/sandbox_cuda_visibility.md`.
- Save structured artifacts: `backend_choice.json`, `build.log`,
  `validation.log`, `sample_output.json`, `verdict.json`, and
  `artifact_manifest.json`.
- For local models that will run on cluster, build and validate a model-specific
  Docker image.

## Context Selection Rule

Use routing before reading optional memory:

1. Read `task_playbooks/ROUTING.md` and select task playbooks.
2. Read `playbooks/env_ROUTING.md` and select environment playbooks.
3. Read `memory/ROUTING.md` only when a failure, ambiguity, or known difficult
   case appears.

Do not load all task playbooks, all environment playbooks, or all bad cases by
default.

## Artifact Recording

When context selection affects a decision, record it in the run artifacts:

```json
{
  "context_selection": {
    "task_playbooks_read": [],
    "environment_playbooks_read": [],
    "memory_files_read": [],
    "reason": "short evidence-based reason"
  }
}
```
