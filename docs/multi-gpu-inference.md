# Multi-GPU Inference

`/sure_infer` runs one model server per GPU. Automatic selection is the default: a
large dataset uses the cards this node has, and a small one uses a single server.

This is process-level parallelism, not model batching. Each Worker loads its own copy
of the model and calls it one sample at a time, so no model wrapper needs a batch
argument and predictions are identical to a single-server run.

## Selecting Workers

`num_workers` is optional.

| Selected samples (after `max_samples`) | Workers |
| --- | --- |
| fewer than 1000 | 1 |
| 1000 – 1999 | 2 |
| 2000 – 3999 | 4 |
| 4000 or more | 8 |

The planned count is then capped at the GPUs this node provides. The cap is the
allocation a local run actually gets: `CUDA_VISIBLE_DEVICES` when it is set,
otherwise the `nvidia-smi` device list. A 4000-sample dataset on a two-GPU node plans
two Workers, not eight, so automatic selection never asks for hardware the node does
not have. With no usable GPU the plan stays at one Worker and the run proceeds on the
existing single-server path.

An explicit `num_workers` is honoured up to the node's GPU count. Above it, input
resolution fails before any model is loaded:

```text
num_workers=8 asks for 8 GPUs but this node provides 2
```

`num_workers=1` always uses the single-server path. More than one Worker requires
`device=auto` or `device=cuda`; `device=cuda:<index>` narrows the run to one card and
is refused, never widened.

A mixed run plans each dataset separately and records the maximum in the resolved
runtime. Datasets are processed in input order, one at a time: models are loaded
again for the next dataset rather than held across the whole run.

## Execution

Each Worker is a separate process pinned to one device through
`CUDA_VISIBLE_DEVICES`, and uses `cuda:0` inside that process. Model configuration
cannot widen that visibility.

Keys are split into contiguous shards whose sizes differ by at most one, in source
order. A dataset with fewer samples than Workers uses fewer Workers.

Both local surfaces work:

- **`local_docker`** — the launcher passes `--gpus "device=…"` for the planned cards
  and does not forward the host `CUDA_VISIBLE_DEVICES`, because device ids are
  renumbered inside a restricted container. The entrypoint probes CUDA in Model
  Python before loading anything and derives the container ordinals.
- **`local_python`** — the host allocation is already the truth, so the node's
  `CUDA_VISIBLE_DEVICES` reaches the entrypoint unchanged.

The smoke pass runs first, on one GPU, under `parallel_inference/smoke/`, so a broken
model server is caught before the full dataset is started.

## Failures and recovery

Each Worker appends every successful sample to its own `completed.jsonl` and flushes
it. A Worker that fails does not terminate its peers, and a truncated final line is
read as "that sample did not complete" rather than as a prediction.

Sample errors leave the rest of the Worker's shard running when the server remains
usable. Only missing keys are retried, on their original GPU, at most twice after the
first attempt. Proven input or configuration errors are not retried automatically.

A stdio exchange that exceeds the approved server's `timeout` or `startup_timeout_sec`
(300 seconds by default) ends that attempt and cleans up its server process.

If a dataset still has missing keys, the execution fails, the dataset is not marked
completed, and every successful shard stays on disk. Later datasets in the same run
are still processed.

To reuse those keys, resume the same product directory:

```bash
"$HARNESS_PYTHON_BIN" scripts/run_infer.py \
  --run-dir <original_control_run_dir> \
  --resume-predictions
```

Recovery verifies the dataset projection, model configuration, approved runtime,
protocol and sample scope before reusing anything, then starts new attempt
directories and loads models only for the missing keys. A changed identity requires a
new product directory. Ordinary runs do not reuse previous attempts: a multi-Worker
run starts with `NO_RESUME=1` by default.

A shared out-of-memory condition can stop every Worker at once. It is reported from
the Worker logs as `gpu_oom` or `host_oom`, and the journals remain usable for
recovery. There is currently no memory preflight: N Workers each load a full model,
so a Worker count that fits the GPUs may still not fit host RAM.

## Artifacts

```text
<output_dir>/
├── predictions/<dataset>.txt
├── predictions/<dataset>.jsonl
├── prediction_generation_status.json
└── parallel_inference/<dataset>/
    ├── plan.json
    ├── merge_report.json
    ├── sessions/<session>.json
    └── workers/worker_<rank>/attempt_<number>/
        ├── attempt.json
        ├── keys.txt
        ├── completed.jsonl
        └── worker.log
```

`merge_report.json` is the authority on completeness: a successful dataset has
`status: completed`, `expected == generated` and `missing_keys: []`.

`execution_result.json` adds a `parallelism` block with the requested Worker count,
the selection mode (`auto` / `explicit`), the GPU list, and per dataset the planned and
effective Worker counts, sample scope, status and report path. The execution gate
rejects a succeeded run whose plan, generation status, merge report or GPU list
disagrees, so a tampered or drifted Worker count cannot pass.
