# ASR Metric

ASR exposes `CERMetric` and `WERMetric` class names for registry consistency,
but both classes use the benchmark-compatible SURE ASR path:

```text
SUREEvaluator.evaluate("ASR", ...) -> asr/wenet_compute_cer.py
```

This replaces the older simple edit-distance class implementation. Do not use a
second ASR scoring path unless it is documented as a separate institution or
benchmark variant.

## Environment

Preparing an engine checkout, not a step inside a `/sure_eval` run: a run
uses the locked Evaluation Runtime and never builds an environment of its own.

The metric env includes ASR-specific dependencies and the minimal dependencies
needed to import the `sure_eval` package.

```bash
cd src/sure_eval/evaluation/asr
uv sync
```

## Smoke

```bash
PYTHONPATH=../../../../.. uv run python -c "from sure_eval.evaluation.asr.metrics import CERMetric; print(CERMetric().calculate('你好世', '你好世界', language='zh').score)"
```
