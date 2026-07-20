# Docker Mount Missing Bad Case

## Trigger

Use this memory when Docker starts but files that exist on the host are missing
inside the container.

Common evidence:

- `FileNotFoundError` for `fixture/`, `checkpoints/`, `.runtime/`, or artifacts.
- `validate.py` fails in `VALIDATE_SPEC` with
  `Missing required artifact: artifacts/backend_choice.json` or
  `Missing required artifact: artifacts/build_plan.json` even though the files
  exist on the host.
- Container `pwd` differs from the expected workdir.
- Bind mount path uses a relative host path from the wrong directory.

## Affected Step

Docker validation and model-local smoke tests.

## Minimum Evidence

Collect:

- host-side `pwd`
- `sed -n '1,220p' src/sure_eval/models/{model}/docker_validate.sh`
- container-side `pwd` and `ls -la` for the expected mounted directory.

## Fix Pattern

Use an absolute host model directory in bind mounts. Set `WORKDIR` and runtime
`-w` consistently. Keep fixture, checkpoints, and runtime cache under the model
directory unless the model spec explicitly documents otherwise.

If the model-local `validate.py` requires phase-1 evidence under
`artifacts/`, mount the host `artifacts/` directory read-only and write Docker
outputs to a separate writable directory such as `docker_artifacts/`. Do not
redirect `ARTIFACTS_DIR` to `docker_artifacts/` unless all required spec
evidence is also mounted there, because that can make a valid host onboarding
look incomplete inside the container.

## Verification

Run a container command that lists the expected files before running expensive
validation.

```bash
docker run --rm -v "$PWD:/workspace" -w /workspace {image} ls -la fixture
```

After validation, do not rely only on `test -s sample_output.json`. If a failed
wrapper writes diagnostic JSON, that check can pass while the validation failed.
Parse an explicit success marker such as `"overall": "PASSED"` or
`VALIDATE_CONTRACT` with `status=passed` before appending
`docker_validate_status=passed`.
