# Docker Smoke False Pass Bad Case

## Trigger

Use this memory when a Docker validation script exits successfully or prints a
success marker even though the actual validation artifact reports failure.

Common evidence:

- `docker_validate.sh` only checks `test -s sample_output.json`.
- `sample_output.json` exists but contains `"overall": "FAILED"`.
- `validation.log` contains a failed `VALIDATE_LOAD`, `VALIDATE_INFER`, or
  `VALIDATE_CONTRACT` stage.
- The run hit a runtime error such as CUDA OOM, but the script still appended
  `docker_validate_status=passed`.

## Affected Step

Docker validation, registry pull-back smoke validation, and final deployment
readiness reporting.

## Minimum Evidence

Collect:

- `docker_validate.sh`
- `docker_artifacts/sample_output.json`
- `docker_artifacts/validation.log`, if present
- the last 80 lines of `docker_artifacts/docker_validate.log`
- GPU state if the failure is OOM (`nvidia-smi`)

## Fix Pattern

Do not treat the existence of `sample_output.json` as proof of success. Validate
the semantic pass condition:

- ASR or JSON verdict style wrappers: require `"overall": "PASSED"` in
  `sample_output.json` or the model-specific verdict.
- TTS/VC wrappers with stage logs: require the output audio file to exist and
  require `VALIDATE_CONTRACT` with `status: passed` in `validation.log`.
- Append `docker_validate_status=passed` only after the semantic checks pass.

If a transient local GPU OOM happens because the default GPU is occupied, choose
an idle GPU with `GPU_DEVICE=<id>` and rerun. Record the OOM as a local resource
condition, not as a model or image failure, only after a clean run passes on an
available GPU.

## Verification

Run the Docker validation script from a fresh pulled image and confirm both the
exit code and the semantic artifact:

```bash
GPU_DEVICE=<idle_gpu> src/sure_eval/models/<model>/docker_validate.sh
grep -q '"overall": "PASSED"' src/sure_eval/models/<model>/docker_artifacts/sample_output.json
```

For stage-log wrappers:

```bash
grep -q '"stage": "VALIDATE_CONTRACT", "status": "passed"' \
  src/sure_eval/models/<model>/docker_artifacts/validation.log
```
