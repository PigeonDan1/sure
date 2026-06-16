# S2TT Metric

S2TT uses `BLEUMetric` for BLEU and `SUREEvaluator.evaluate("S2TT", ...)` when
chrF2 is also required. The metric environment needs `sacrebleu` and the minimal
dependencies needed to import the `sure_eval` package.

## Environment

```bash
cd src/sure_eval/evaluation/s2tt
uv sync
```

## Smoke

```bash
PYTHONPATH=../../../../.. uv run python -c "from sure_eval.evaluation.s2tt.metrics import BLEUMetric; print(BLEUMetric(language='zh').calculate_batch(['你好'], ['你好']).score)"
```
