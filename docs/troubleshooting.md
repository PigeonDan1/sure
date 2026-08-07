# Troubleshooting

Start with the harness doctor:

```bash
npm run sure:doctor
```

It checks the repository root, Node dependency surface, SURE skills,
`sure-evaluation`, and benchmark JSONL discovery.

## `Cannot find module 'typebox'`

SURE slash commands are registered together. A missing dependency in one skill
can surface while another command is starting.

Run from the repository root:

```bash
npm install --ignore-scripts
npm run sure:doctor
```

If sparse checkout paths are missing:

```bash
git sparse-checkout add scripts fixtures packages/coding-agent/examples
npm install --ignore-scripts
npm run sure:doctor
```

Then start the TUI through the local entrypoint:

```bash
./pi-test.sh --provider openai --model <model-name> --thinking high --approve
```

## `rate_limit_exceeded: Concurrency limit exceeded`

The model provider rejected the agent request because the same account already
has too many active requests. This interrupts the TUI session; it does not mean
the SURE run artifacts or model wrapper are invalid.

Close other Pi/Codex/TUI sessions using the same provider account, wait for the
gateway to release the in-flight request, then retry the same slash command.

For long `/sure_onboard` runs, inspect:

```text
.sure/runs/<run_id>/state.json
```

## `/sure_init` Model List and Key Questions

The provider picker queries model lists live where the provider supports
it. The listing source is shown as a notice:

- `live` — fresh from the provider; nothing to do.
- `cached` — the live query failed and a previously saved gateway list
  was used; the models shown may be stale.
- `builtin` — entries come from the built-in catalog and are not
  confirmed with the provider (shown when listing is unsupported or the
  query failed).

A gateway entry in `~/.pi/agent/models.json` whose name collides with a
built-in provider is skipped in the menu — rename the gateway entry.

Non-interactive `/sure_init` needs full arguments:
`--option <provider-id> --model <model-id> --api-key <key>`; a new
gateway additionally takes `--name <name> --base-url <url>`. A missing
key fails with `No API key configured for <provider>` — pass
`--api-key` or run interactively.

## Missing Benchmark JSONL Files

If `/sure_eval` or `/sure_reval` cannot resolve a dataset, check that one of
these is true:

```text
data/datasets/sure_benchmark/jsonl
```

exists, or:

```bash
export SURE_EVAL_DATASETS_ROOT=/path/to/data/datasets
```

points at a root containing `sure_benchmark/jsonl`.

If you have no data yet, it is downloaded from ModelScope and converted
with the repository's own scripts — the user guide's Benchmark Data
section has the full command sequence (`download_sure_data.py --csv`,
per-archive audio downloads, `convert_sure_to_jsonl.py`).

## A Short Dataset Alias Starts a Huge Download

Passing a short alias such as `datasets=aishell1` while the audio suites
are not fully in place makes the dataset manager treat the data as
missing and start a full 52.5 GB ModelScope download — no prompt, no
progress output, no timeout. A run that goes silent right after dataset
resolution is usually this. Prefer full JSONL file names such as
`datasets=aishell1-test_ASR`; they never trigger downloads.

## VC Execution Did Not Produce Submission Evidence

When `execution=vc` is requested, a successful run must include real VC
submission evidence. The harness should not silently fall back to local
execution.

Use `execution=local` for smoke runs and local development. Use `execution=vc`
only when the run is expected to submit to the VC cluster.

## Send the Job to a Specific VC Partition

Add `vc_partition=<partition>` to `/sure_eval` together with `execution=vc`.
Without it the harness picks a partition automatically. If the name is not in
your allowed set, input resolution fails immediately and the error message
lists the partitions you can use (from `vc info -u`).

## Exact `pipeline_id` Fails

Exact pipeline IDs are owned by the selected `sure-evaluation` checkout. If a
previously valid ID fails:

```bash
cd sure/external/sure-evaluation
git status --short
git rev-parse HEAD
```

Then compare the requested pipeline with the current engine catalog or describe
command. Re-run `/sure_reval` into a fresh tmp output directory after updating
the ID.
