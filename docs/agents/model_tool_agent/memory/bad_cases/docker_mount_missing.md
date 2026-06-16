# Docker Mount Missing Bad Case

## Trigger

Use this memory when Docker starts but files that exist on the host are missing
inside the container.

Common evidence:

- `FileNotFoundError` for `fixture/`, `checkpoints/`, `.runtime/`, or artifacts.
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

## Verification

Run a container command that lists the expected files before running expensive
validation.

```bash
docker run --rm -v "$PWD:/workspace" -w /workspace {image} ls -la fixture
```
