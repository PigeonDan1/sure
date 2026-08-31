# KWS Metric

No fixed shared keyword-spotting metric has been moved into this directory yet.
Add metric implementations here only with their own dependency list and smoke
command.

## Environment

Preparing an engine checkout, not a step inside a `/sure_eval` run: a run
uses the locked Evaluation Runtime and never builds an environment of its own.

```bash
cd src/sure_eval/evaluation/kws
uv sync
```
