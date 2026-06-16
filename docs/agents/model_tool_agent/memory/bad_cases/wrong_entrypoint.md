# Wrong Entrypoint Bad Case

## Trigger

Use this memory when `model.spec.yaml`, `server.py`, `validate.py`, Docker
commands, or generated scripts declare one entrypoint but runtime starts a
different Python file or module.

Common evidence:

- Docker validation runs but imports a stale module.
- `predict.py` works manually but `validate.py` calls another path.
- `CMD`, `ENTRYPOINT`, or shell scripts use a previous model name.

## Affected Step

`WRITE_WRAPPER`, `BUILD_ENV`, Docker validation, or smoke validation.

## Minimum Evidence

Collect:

- `sed -n '1,220p' src/sure_eval/models/{model}/model.spec.yaml`
- `sed -n '1,220p' src/sure_eval/models/{model}/validate.py`
- `sed -n '1,220p' src/sure_eval/models/{model}/Dockerfile`
- the exact command executed by `docker_validate.sh`.

## Fix Pattern

Choose one canonical entrypoint for validation and make every generated script
call that path. Update wrapper code, model spec, and Docker command together.

Do not patch only the shell script while leaving `model.spec.yaml` inconsistent.

## Verification

Run the same command that failed and confirm logs mention the intended file or
module. If possible, add a simple startup log line with the resolved entrypoint.
