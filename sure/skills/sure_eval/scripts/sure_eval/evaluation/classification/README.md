# Classification Metric

Classification exposes `AccuracyMetric` for SER, GR, and other label matching
tasks. The metric itself has no third-party runtime dependency, but the env
includes the minimal dependencies needed to import the `sure_eval` package.

## Environment

```bash
cd src/sure_eval/evaluation/classification
uv sync
```

## Smoke

```bash
PYTHONPATH=../../../../.. uv run python -c "from sure_eval.evaluation.classification.metrics import AccuracyMetric; print(AccuracyMetric().calculate_batch(['happy'], ['hap']).score)"
```
