#!/usr/bin/env python3
"""
Evaluate prepared prediction files against canonical SURE-EVAL datasets.

This script is deterministic by design:
- dataset resolution goes through DatasetManager
- metric selection goes through SOTA baseline first
- optional result recording goes through RPSManager
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import string
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sure_eval.core.config import Config
from sure_eval.core.logging import configure_logging, get_logger
from sure_eval.datasets import DatasetManager
from sure_eval.evaluation.rps import RPSManager
from sure_eval.evaluation.sure_evaluator import SUREEvaluator
from sure_eval.reports import SOTAManager

configure_logging(level="INFO")
logger = get_logger(__name__)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_prediction_map(path: Path) -> dict[str, str]:
    predictions: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        if "\t" in line:
            key, value = line.split("\t", 1)
        else:
            parts = line.split(None, 1)
            key = parts[0]
            value = parts[1] if len(parts) > 1 else ""
        predictions[key] = value
    return predictions


def _write_eval_file(rows: list[str]) -> str:
    handle = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
    handle.write("\n".join(rows) + "\n")
    handle.close()
    return handle.name


def _describe_evaluation_context(task: str, language: str, metric: str) -> dict[str, Any]:
    """Describe the dataset-driven post-processing used by the evaluator."""
    context: dict[str, Any] = {
        "task": task,
        "language": language,
        "language_source": "dataset_jsonl",
        "metric": metric,
        "metric_source": "sota_baseline_or_task_default",
    }

    if task == "ASR":
        context.update(
            {
                "postprocessing": "SUREEvaluator._eval_asr",
                "normalization": "sure_eval.evaluation.normalization.asr_simple_tn.asr_num2words",
                "punctuation_policy": "evaluation-pipeline clean_marks.strip_all_punct compatible",
                "tokenization": "code_switch_mer_wer_cer" if language == "cs" else "character" if metric == "cer" or language == "zh" else "word",
                "case_sensitive": False,
            }
        )
    elif task == "S2TT":
        context.update(
            {
                "postprocessing": "SUREEvaluator._eval_s2tt",
                "normalization": "sacrebleu_tokenizer_by_language",
            }
        )
    elif task in {"SER", "GR", "SLU"}:
        context.update(
            {
                "postprocessing": "evaluation-pipeline process_prediction compatible" if task == "SLU" else f"SUREEvaluator.{task.lower()}_label_normalization",
                "normalization": "prompt_option_restoration" if task == "SLU" else "label_normalization",
            }
        )
    elif task == "SA-ASR":
        context.update(
            {
                "postprocessing": "SUREEvaluator._eval_sa_asr",
                "normalization": "evaluation-pipeline text_normalizer.normalize_text compatible",
            }
        )

    return context


def evaluate_prediction_file(
    dataset_manager: DatasetManager,
    sota_manager: SOTAManager,
    dataset_name: str,
    prediction_path: Path,
) -> dict[str, Any]:
    canonical_name = dataset_manager.normalize_dataset_name(dataset_name)
    jsonl_path = dataset_manager.get_jsonl_path(canonical_name)
    if not jsonl_path.exists():
        jsonl_path = dataset_manager.download_and_convert(canonical_name)

    samples = load_jsonl(jsonl_path)
    predictions = load_prediction_map(prediction_path)
    if not samples:
        raise ValueError(f"Dataset has no samples: {canonical_name}")

    task = samples[0].get("task", "ASR")
    language = samples[0].get("language", "auto")
    metric = sota_manager.get_metric(canonical_name)
    if not metric:
        metric = (
            "accuracy" if task in {"SER", "GR", "SLU"}
            else "bleu" if task == "S2TT"
            else "der" if task == "SD"
            else "cpwer" if task == "SA-ASR"
            else "mer" if task == "ASR" and language == "cs"
            else "cer"
        )

    ref_file = _write_eval_file([f"{sample.get('key', '')}\t{sample.get('target', '')}" for sample in samples])
    hyp_file = _write_eval_file([f"{sample.get('key', '')}\t{predictions.get(sample.get('key', ''), '')}" for sample in samples])

    try:
        evaluator = SUREEvaluator(language=language)
        eval_kwargs: dict[str, Any] = {}
        if task == "ASR":
            eval_kwargs["tochar"] = metric == "cer"
        elif task == "SLU":
            eval_kwargs["prompt_jsonl"] = str(jsonl_path)
        result = evaluator.evaluate(task, ref_file, hyp_file, **eval_kwargs)
    finally:
        Path(ref_file).unlink(missing_ok=True)
        Path(hyp_file).unlink(missing_ok=True)

    if isinstance(result, dict):
        details = result
    else:
        details = {"score": result}

    if task == "ASR":
        score = details.get(metric, details.get("score", 0.0))
    elif task == "S2TT" and metric == "bleu_char":
        score = details.get("bleu_char", details.get("bleu", details.get("score", 0.0)))
    elif task == "S2TT":
        score = details.get(metric, details.get("score", 0.0))
    elif task in {"SER", "GR", "SLU"}:
        score = details.get("accuracy", details.get("score", 0.0))
    elif task == "SD":
        score = details.get("der", details.get("score", 0.0))
    elif task == "SA-ASR":
        score = details.get("cpwer", details.get("score", 0.0))
    else:
        score = details.get("score", 0.0)

    rps = sota_manager.calculate_rps(canonical_name, score)

    return {
        "dataset": canonical_name,
        "jsonl_path": str(jsonl_path),
        "prediction_path": str(prediction_path),
        "task": task,
        "language": language,
        "metric": metric,
        "score": score,
        "rps": rps,
        "rps_is_unbounded": isinstance(rps, float) and not math.isfinite(rps),
        "num_samples": len(samples),
        "evaluation_context": _describe_evaluation_context(task, language, metric),
        "details": details,
    }


def _to_strict_jsonable(value: Any) -> Any:
    """Convert Python objects into strict-JSON-safe values."""
    if isinstance(value, dict):
        return {key: _to_strict_jsonable(subvalue) for key, subvalue in value.items()}
    if isinstance(value, list):
        return [_to_strict_jsonable(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _write_results_dir(
    results_dir: Path,
    tool_name: str,
    protocol_id: str,
    model_dir: Path | None,
    results: list[dict[str, Any]],
    dataset_manager: DatasetManager,
) -> None:
    """Write standardized results directory with report.jsonl, protocol.yaml and predictions/."""
    results_dir.mkdir(parents=True, exist_ok=True)

    # 1. protocol.yaml — extract from model config.yaml
    if model_dir and model_dir.exists():
        config_yaml = model_dir / "config.yaml"
        if config_yaml.exists():
            try:
                import yaml
                cfg = yaml.safe_load(config_yaml.read_text(encoding="utf-8"))
                protocols = cfg.get("protocols", {})
                protocol = protocols.get(protocol_id, {})
                protocol_yaml = results_dir / "protocol.yaml"
                protocol_yaml.write_text(
                    yaml.safe_dump(
                        {"protocol_id": protocol_id, **protocol},
                        allow_unicode=True,
                        sort_keys=False,
                    ),
                    encoding="utf-8",
                )
                logger.info("Wrote protocol.yaml", path=str(protocol_yaml))
            except Exception as exc:
                logger.warning("Failed to write protocol.yaml", error=str(exc))

    # 2. report.jsonl — one line per dataset result
    report_path = results_dir / "report.jsonl"
    with report_path.open("w", encoding="utf-8") as handle:
        for result in results:
            report_line = {
                "tool_uid": tool_name,
                "protocol_id": protocol_id,
                "dataset": result["dataset"],
                "task": result["task"],
                "language": result["language"],
                "metric": result["metric"],
                "score": result["score"],
                "rps": result.get("rps"),
                "num_samples": result["num_samples"],
                "evaluation_context": result.get("evaluation_context"),
                "prediction_file": f"predictions/{result['dataset']}.txt",
                "evaluator_version": "sure_eval v1.0",
            }
            handle.write(json.dumps(report_line, ensure_ascii=False) + "\n")
    logger.info("Wrote report.jsonl", path=str(report_path), num_entries=len(results))

    # 3. predictions/<dataset>.txt — gt<TAB>pred<TAB>result
    pred_dir = results_dir / "predictions"
    pred_dir.mkdir(parents=True, exist_ok=True)

    from sure_eval.evaluation.asr.wenet_compute_cer import Calculator, characterize

    for result in results:
        dataset_name = result["dataset"]
        jsonl_path = Path(result["jsonl_path"])
        prediction_path = Path(result["prediction_path"])

        if not jsonl_path.exists() or not prediction_path.exists():
            logger.warning("Skipping sample results: missing files", dataset=dataset_name)
            continue

        samples = load_jsonl(jsonl_path)
        predictions = load_prediction_map(prediction_path)
        task = result["task"]
        metric = result["metric"]
        is_cer = metric == "cer"

        output_path = pred_dir / f"{dataset_name}.txt"
        with output_path.open("w", encoding="utf-8") as handle:
            for sample in samples:
                key = sample.get("key", "")
                gt = sample.get("target", "")
                pred = predictions.get(key, "")

                # Per-sample WER/CER
                calc = Calculator()
                if is_cer:
                    gt_tokens = characterize(gt)
                    pred_tokens = characterize(pred)
                else:
                    gt_tokens = gt.upper().split()
                    pred_tokens = pred.upper().split()

                detail = calc.calculate(gt_tokens, pred_tokens)
                total = detail["all"]
                errors = detail["sub"] + detail["ins"] + detail["del"]
                sample_score = round(errors / total, 6) if total > 0 else 0.0

                handle.write(f"{gt}\t{pred}\t{sample_score}\n")
        logger.info("Wrote sample predictions", dataset=dataset_name, path=str(output_path))


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate deterministic prediction files")
    parser.add_argument("--dataset", nargs="+", required=True, help="Dataset names to evaluate")
    parser.add_argument("--pred-dir", type=str, help="Directory containing <dataset>.txt prediction files")
    parser.add_argument("--pred", action="append", nargs=2, metavar=("DATASET", "FILE"), help="Explicit dataset-to-prediction mapping")
    parser.add_argument("--tool-name", type=str, help="Optional tool name to record in evaluation history")
    parser.add_argument("--record", action="store_true", help="Record results in the evaluation database")
    parser.add_argument("--config", type=str, help="Config path")
    parser.add_argument("--output", type=str, help="Optional JSON output path")
    parser.add_argument("--results-dir", type=str, help="Results output directory (e.g., results/asr_qwen3/strict_core)")
    parser.add_argument("--protocol-id", type=str, default="strict_core", help="Inference protocol ID")
    parser.add_argument("--model-dir", type=str, help="Model directory to extract protocol.yaml from config.yaml")
    args = parser.parse_args()

    cfg = Config.from_yaml(args.config) if args.config else Config.from_env()
    dataset_manager = DatasetManager(cfg)
    sota_manager = SOTAManager()
    rps_manager = RPSManager(cfg)

    explicit_preds = {dataset_manager.normalize_dataset_name(name): Path(path) for name, path in (args.pred or [])}
    pred_dir = Path(args.pred_dir) if args.pred_dir else None

    results: list[dict[str, Any]] = []
    for requested_dataset in args.dataset:
        canonical_name = dataset_manager.normalize_dataset_name(requested_dataset)
        prediction_path = explicit_preds.get(canonical_name)
        if prediction_path is None:
            if pred_dir is None:
                raise ValueError(f"No prediction file provided for dataset: {canonical_name}")
            prediction_path = pred_dir / f"{canonical_name}.txt"
        if not prediction_path.exists():
            raise FileNotFoundError(f"Prediction file not found: {prediction_path}")

        result = evaluate_prediction_file(dataset_manager, sota_manager, canonical_name, prediction_path)
        results.append(result)

        if args.record:
            if not args.tool_name:
                raise ValueError("--record requires --tool-name")
            rps_manager.evaluate_and_record(
                tool_name=args.tool_name,
                dataset=result["dataset"],
                score=result["score"],
                metric=result["metric"],
                metadata={
                    "num_samples": result["num_samples"],
                    "prediction_path": result["prediction_path"],
                    "details": result["details"],
                },
            )

    payload = _to_strict_jsonable({"results": results})
    output = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False)
    print(output)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")
        logger.info("Wrote evaluation payload", path=str(output_path))

    if args.results_dir:
        results_dir = Path(args.results_dir)
        model_dir = Path(args.model_dir) if args.model_dir else None
        _write_results_dir(
            results_dir=results_dir,
            tool_name=args.tool_name or "unknown",
            protocol_id=args.protocol_id,
            model_dir=model_dir,
            results=results,
            dataset_manager=dataset_manager,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
