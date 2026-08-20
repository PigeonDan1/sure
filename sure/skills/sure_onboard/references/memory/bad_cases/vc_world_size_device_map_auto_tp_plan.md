# Bad Case: VC WORLD_SIZE Triggers Transformers tp_plan Auto

## Trigger

Use this memory when a model load or smoke run under `vc submit` fails with:

```text
We tried to initialize torch.distributed for you, but it failed, makesure you init torch distributed in your script to use `tp_plan='auto'`
```

Also route here when all of these are true:

- The job runs under `vc submit`.
- The container environment includes `WORLD_SIZE`, `RANK`, `MASTER_ADDR`, or `MASTER_PORT`.
- Model code calls Transformers or ModelScope loading with `device_map="auto"`.
- The run is intended to be single-process single-GPU inference, not distributed tensor parallel inference.

## Affected Step

- Model tool-agent `VALIDATE_LOAD`, `VALIDATE_INFER`, Docker validation, or VC validation.
- Main-flow direct server smoke generation when a model wrapper starts a local server inside a `vc` job.

## Root Cause

`vc submit` injects distributed environment variables such as:

```text
WORLD_SIZE=1
RANK=0
MASTER_ADDR=...
MASTER_PORT=...
```

Recent Transformers versions treat `device_map="auto"` with a non-empty
`WORLD_SIZE` as tensor-parallel intent and internally convert it to
`tp_plan="auto"`. A single-GPU single-process wrapper can therefore fail before
any prediction is generated, even though the job has a valid GPU allocation.

This is not a queue-selection failure. If logs show dataset preparation or
template generation succeeded, the queue and Python entrypoint are already past
the first gate.

## Minimum Evidence

Collect:

1. The failed `vc` job id and task id.
2. `vc logs -t <job-id>-master-0` showing the exact `tp_plan='auto'` error.
3. `vc describe -j <job-id>` or logged environment evidence showing
   `WORLD_SIZE`, `RANK`, `MASTER_ADDR`, or `MASTER_PORT`.
4. The model load code that passes `device_map="auto"` or imports a ModelScope
   loader that does so internally.
5. Whether the intended execution is single GPU or true distributed/tensor
   parallel inference.

## Known Mitigations

For single-GPU inference, do one of the following in the model-local wrapper,
not in shared evaluation scripts:

- Prefer an explicit single-device map such as `device_map="cuda:0"` or
  `device_map={"": "cuda:0"}` when the upstream loader accepts it.
- If upstream code hard-codes `device_map="auto"`, wrap model initialization in
  a narrow context that temporarily removes distributed variables, then restores
  them after load:

```python
from contextlib import contextmanager
import os

_DISTRIBUTED_ENV_KEYS = (
    "WORLD_SIZE",
    "RANK",
    "LOCAL_RANK",
    "MASTER_ADDR",
    "MASTER_PORT",
    "VC_MASTER_HOSTS",
    "VC_MASTER_NUM",
    "VC_WORKER_HOSTS",
    "VC_WORKER_NUM",
)


@contextmanager
def without_distributed_env():
    saved = {key: os.environ.get(key) for key in _DISTRIBUTED_ENV_KEYS}
    try:
        for key in _DISTRIBUTED_ENV_KEYS:
            os.environ.pop(key, None)
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
```

Use the context only around the offending single-process model load. Do not
remove distributed variables globally for models that intentionally use
`torchrun`, DeepSpeed, tensor parallelism, or multi-process inference.

If a ModelScope `AutoModelForCausalLM` path introduces the same behavior, prefer
the equivalent Transformers local loader when compatible with the checkpoint.
Still remove `WORLD_SIZE` during load if the code path keeps
`device_map="auto"`.

## Verification

Before a full run:

1. Add a wrapper-level test that sets `WORLD_SIZE=1` and `RANK=0`, enters the
   model-load path, and asserts those variables are absent during the offending
   load and restored afterward.
2. Run a bounded VC smoke on the same image and queue.
3. Confirm logs pass the previous failure point and generate at least one
   prediction artifact.

Useful checks:

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy vc logs -t <job-id>-master-0
```

```bash
python -m pytest tests/<model_wrapper_test>.py -q
```

## Example Artifacts

- `src/sure_eval/models/IndexTeam__IndexTTS-2/eval_runs/main_agent_IndexTeam__IndexTTS-2_010`
- `src/sure_eval/models/IndexTeam__IndexTTS-2/eval_runs/main_agent_IndexTeam__IndexTTS-2_011`

Both runs reached TTS smoke generation on an approved GPU partition and failed before
predictions because an IndexTTS-2 Qwen emotion submodel loaded with
`device_map="auto"` while `vc` had injected `WORLD_SIZE=1`.
