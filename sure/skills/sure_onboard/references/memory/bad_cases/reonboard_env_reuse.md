# Re-onboarding Env Reuse

## Trigger

Read this when a model re-onboarding run under
`src/sure_eval/models_reonboard/runs/<model>/`:

- has `.venv` as a symlink;
- runs Python from `src/sure_eval/models/<model>/.venv`;
- reports local uv success without creating a new run-local uv environment; or
- reuses an original model environment to validate a re-onboarding task.

## Why This Is Wrong

Re-onboarding is meant to prove the model tool-agent can reproduce the model
environment from the current specifications. Reusing an existing model `.venv`
only proves the old environment still works.

It is acceptable to reuse weights, Hugging Face cache, ModelScope cache, source
checkouts, and uv package cache. It is not acceptable to reuse the old Python
virtual environment as the re-onboarding environment.

## Required Fix

1. Remove the symlink or stop using the original environment.
2. Create a fresh `.venv` inside the re-onboarding run directory.
3. Install from the run-local `requirements*.txt` / setup script.
4. Run GPU validation from the run-local `.venv`.
5. Record reused non-environment resources in `artifact_manifest.json` or
   `verdict.json`.

## Verification

```bash
test -d src/sure_eval/models_reonboard/runs/<model>/.venv
test ! -L src/sure_eval/models_reonboard/runs/<model>/.venv
src/sure_eval/models_reonboard/runs/<model>/.venv/bin/python -c \
  "import sys; print(sys.executable)"
```

The printed executable must be under the re-onboarding run directory.

## Affected Examples

- `src/sure_eval/models_reonboard/runs/SWivid__F5-TTS_Emilia-ZH-EN`
- `src/sure_eval/models_reonboard/runs/IndexTeam__IndexTTS-2`
- `src/sure_eval/models_reonboard/runs/Plachtaa__seed-vc`
