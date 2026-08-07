# Evaluation Engine

SURE Harness does not implement metric logic directly. It reads capabilities,
route nodes, normalizers, exact `pipeline_id` choices, and node-local
environments from the `sure-evaluation` submodule.

## Setup

Clone the harness with submodules:

```bash
git clone --recurse-submodules --depth 1 --single-branch --branch harness-tui-agent https://github.com/PigeonDan1/sure.git sure-harness
```

For an existing clone, initialize the submodule:

```bash
git submodule update --init --recursive
```

Advanced users can still point the harness at another checkout:

```bash
export SURE_EVALUATION_HOME=/path/to/sure-evaluation
```

Run the harness doctor after setup:

```bash
npm run sure:doctor
```

## Branch Relationship

Harness branch `harness-tui-agent` tracks the public engine as a Git submodule:

```text
https://github.com/PigeonDan1/sure-evaluation.git main
```

The submodule path is:

```text
sure/external/sure-evaluation
```

Before changing `/sure_eval`, `/sure_reval`, route selection, normalization
assumptions, or pipeline compatibility docs, sync and verify the submodule:

```bash
git submodule sync --recursive
git submodule update --remote --merge sure/external/sure-evaluation
npm run sure:doctor
```

Commit only the updated gitlink after verification:

```bash
git add .gitmodules sure/external/sure-evaluation
git commit -m "chore(sure): bump sure-evaluation submodule"
```

Do not vendor engine source files into the parent repository. The durable
runtime contract is the evidence written by each evaluation run:
`evaluation_route_plan.json` must record the engine path and commit used for
that run.

## Dataset Root

`/sure_eval` and `/sure_reval` need benchmark JSONL files plus the
matching audio. The default JSONL location is:

```text
data/datasets/sure_benchmark/jsonl
```

The data itself comes from ModelScope — the user guide's Benchmark Data
section has the download and conversion commands. If a prepared JSONL
tree already exists elsewhere, link it:

```bash
mkdir -p data/datasets/sure_benchmark
ln -s /path/to/sure_benchmark/jsonl data/datasets/sure_benchmark/jsonl
```

`SURE_EVAL_DATASETS_ROOT` can point at another root containing
`sure_benchmark/jsonl`, but it moves only the JSONL lookup:

```bash
export SURE_EVAL_DATASETS_ROOT=/path/to/data/datasets
```

Audio is always resolved against the repository root
(`data/datasets/sure_benchmark/SURE_Test_Suites/`), so when audio lives
elsewhere, keep a symlink inside the repository pointing at it.

## Route Selection

Use `metrics` when the user wants the current default route for a reported
metric:

```text
/sure_eval model=<model> datasets=<dataset> metrics=wer max_samples=5 execution=local
/sure_reval source=<run_dir> datasets=<dataset> metrics=wer max_samples=5
```

Use `pipeline_id` when the user needs an exact route variant:

```text
/sure_reval source=<run_dir> datasets=<dataset> max_samples=5 pipeline_id=<exact_pipeline_id>
```

Repeat `pipeline_id=...` to compare multiple chains for the same dataset and
metric. The harness writes separate metric artifact directories so results do
not overwrite each other.

## Typical Routes

| Task | Route shape |
| --- | --- |
| ASR zh CER | `normalization/wetext_norm -> scoring/wenet_cer` |
| TTS/VC zh CER | `frontend/funasr_loader_16k_mono -> transcription/paraformer_zh -> normalization/punctuation_strip_norm -> scoring/wenet_cer` |
| TTS en WER | `transcription/whisper_large_v3 -> normalization/whisper_norm -> scoring/wenet_wer` |

Treat this table as orientation only. The source of truth is the local
`sure-evaluation` engine selected for the run.

## Output Evidence

A completed evaluation or re-evaluation should expose the selected route in:

| Artifact | Field |
| --- | --- |
| `evaluation_route_plan.json` | engine path, engine commit, requested metrics or pipeline IDs |
| `evaluation_payload.json` | `results[].pipeline_id`, `results[].nodes`, metric artifact directory |
| `metrics/<dataset>/<metric_slug>/pipeline_description.json` | exact route metadata |
| `metrics/<dataset>/<metric_slug>/report.json` | score and pipeline trace |

For `/sure_reval`, also check `reval_run_report.json`:

```text
evaluation_only=true
old_evaluation_reused=false
```
