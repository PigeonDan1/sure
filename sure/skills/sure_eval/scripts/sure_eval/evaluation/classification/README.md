# Classification Metric

Classification exposes `AccuracyMetric` for SER, GR, and other label matching
tasks. The metric itself has no third-party runtime dependency, but the env
includes the minimal dependencies needed to import the `sure_eval` package.

## Environment

Preparing an engine checkout, not a step inside a `/sure_eval` run: a run
uses the locked Evaluation Runtime and never builds an environment of its own.

```bash
cd src/sure_eval/evaluation/classification
uv sync
```

## Smoke

```bash
PYTHONPATH=../../../../.. uv run python -c "from sure_eval.evaluation.classification.metrics import AccuracyMetric; print(AccuracyMetric().calculate_batch(['happy'], ['hap']).score)"
```
