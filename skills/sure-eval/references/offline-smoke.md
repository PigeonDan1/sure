# Offline smoke evaluation

From the SURE checkout, using the environment where SURE is installed:

```bash
python skills/sure-eval/scripts/offline_smoke.py --output-dir /tmp/sure-eval-smoke
```

This uses two synthetic Chinese ASR references and supplied predictions with one
wrong character out of four. It runs the actual validation and scoring scripts,
checks CER = 0.25 and sample count = 2, and writes `result.json`, `validation.json`,
`metrics.json`, predictions, configuration, and command logs. It downloads nothing
and calls no model. This validates scoring integration, not model inference or
benchmark quality. Use a fresh output directory to preserve previous evidence.

For user-provided predictions, use the same scripts with the actual canonical
dataset, prediction file and configuration:

```bash
python scripts/validate_prediction_files.py --dataset DATASET --pred DATASET PREDICTIONS.txt --config CONFIG.yaml --require-nonempty --output RUN_DIR/validation.json
python scripts/evaluate_predictions.py --dataset DATASET --pred DATASET PREDICTIONS.txt --config CONFIG.yaml --output RUN_DIR/metrics.json
```

Only score after validation exits 0 and `is_valid` is true. These commands can
download datasets if canonical data is missing: ensure it already exists for an
offline request. Predictions use `key<TAB>prediction`, with exactly one entry per
dataset key. Preserve the user's metric and dataset scope.
