#!/usr/bin/env python3
"""
Run SURE Benchmark evaluation.

This script:
1. Loads ground truth from JSONL files
2. Loads predictions from text files
3. Runs evaluation for each task
4. Outputs results in a standardized format

Usage:
    # Evaluate single dataset
    python scripts/run_sure_evaluation.py \
        --gt data/datasets/sure_benchmark/jsonl/aishell1-test_ASR.jsonl \
        --pred predictions/aishell1_pred.txt \
        --task ASR
    
    # Evaluate all datasets
    python scripts/run_sure_evaluation.py \
        --gt-dir data/datasets/sure_benchmark/jsonl \
        --pred-dir predictions \
        --output results/sure_results.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sure_eval.core.logging import configure_logging, get_logger
from sure_eval.evaluation.sure_evaluator import SUREEvaluator

configure_logging(level="INFO")
logger = get_logger(__name__)


# Dataset to task mapping
DATASET_TASK_MAP = {
    "aishell1-test_ASR": ("ASR", "zh"),
    "aishell-5_eval1": ("ASR", "zh"),
    "librispeech_test-clean_ASR": ("ASR", "en"),
    "librispeech_test-other_ASR": ("ASR", "en"),
    "kespeech": ("ASR", "zh"),
    "voxpopuli_test": ("ASR", "en"),
    "contextasr_english": ("ASR", "en"),
    "contextasr_mandarin": ("ASR", "zh"),
    "CoVoST2_S2TT_en2zh_test": ("S2TT", "en"),
    "CoVoST2_S2TT_zh2en_test": ("S2TT", "zh"),
    "CS_dialogue": ("ASR", "cs"),
    "IEMOCAP_SER_test": ("SER", "en"),
    "librispeech_test_clean_GR": ("GR", "en"),
    "mmsu": ("SLU", "zh"),
}


def load_gt_jsonl(jsonl_path: Path) -> List[Dict[str, Any]]:
    """Load ground truth from JSONL file."""
    samples = []
    with open(jsonl_path, 'r', encoding='utf-8') as f:
        for line in f:
            if not line.strip():
                continue
            samples.append(json.loads(line))
    return samples


def load_pred_txt(txt_path: Path) -> Dict[str, str]:
    """Load predictions from text file (format: key text)."""
    preds = {}
    with open(txt_path, 'r', encoding='utf-8') as f:
        for line in f:
            if not line.strip():
                continue
            parts = line.strip().split(None, 1)
            if len(parts) == 2:
                preds[parts[0]] = parts[1]
            elif len(parts) == 1:
                preds[parts[0]] = ""
    return preds


def convert_to_eval_format(
    gt_samples: List[Dict],
    pred_dict: Dict[str, str],
) -> tuple[str, str]:
    """
    Convert samples to evaluation format.
    
    Returns:
        Tuple of (ref_file_path, hyp_file_path)
    """
    ref_lines = []
    hyp_lines = []
    
    for sample in gt_samples:
        key = sample.get("key", "")
        ref_text = sample.get("target", "")
        hyp_text = pred_dict.get(key, "")
        
        ref_lines.append(f"{key}\t{ref_text}")
        hyp_lines.append(f"{key}\t{hyp_text}")
    
    # Write to temp files
    import tempfile
    ref_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8')
    hyp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8')
    
    ref_file.write('\n'.join(ref_lines) + '\n')
    hyp_file.write('\n'.join(hyp_lines) + '\n')
    
    ref_file.close()
    hyp_file.close()
    
    return ref_file.name, hyp_file.name


def evaluate_dataset(
    gt_path: Path,
    pred_path: Path,
    task: str | None = None,
    language: str | None = None,
) -> Dict[str, Any]:
    """
    Evaluate a single dataset.
    
    Args:
        gt_path: Path to ground truth JSONL
        pred_path: Path to predictions TXT
        task: Task type (ASR, SER, etc.)
        language: Language code (zh, en, etc.)
        
    Returns:
        Evaluation result dictionary
    """
    # Auto-detect task and language from dataset name
    if task is None or language is None:
        dataset_name = gt_path.stem
        detected_task, detected_lang = DATASET_TASK_MAP.get(dataset_name, ("ASR", "auto"))
        task = task or detected_task
        language = language or detected_lang
    
    logger.info(f"Evaluating {gt_path.stem}: task={task}, language={language}")
    
    # Load data
    gt_samples = load_gt_jsonl(gt_path)
    pred_dict = load_pred_txt(pred_path)
    
    logger.info(f"  GT samples: {len(gt_samples)}")
    logger.info(f"  Predictions: {len(pred_dict)}")
    
    # Convert to eval format
    ref_file, hyp_file = convert_to_eval_format(gt_samples, pred_dict)
    
    try:
        # Run evaluation
        evaluator = SUREEvaluator(language=language)
        result = evaluator.evaluate(task, ref_file, hyp_file)
        
        # Format result
        formatted_result = {
            "dataset": gt_path.stem,
            "task": task,
            "language": language,
            "num_samples": len(gt_samples),
        }
        
        if task == "ASR":
            if language == "cs":
                # Code-switching: returns tuple (mer, wer, cer)
                mer_score, wer_score, cer_score = result
                formatted_result["mer_percent"] = round(mer_score * 100, 2)
                formatted_result["wer_percent"] = round(wer_score * 100, 2)
                formatted_result["cer_percent"] = round(cer_score * 100, 2)
            else:
                # Regular ASR
                formatted_result["wer_percent"] = round(result.get("wer", 0) * 100, 2)
                formatted_result["details"] = {
                    "all": result.get("all", 0),
                    "cor": result.get("cor", 0),
                    "sub": result.get("sub", 0),
                    "del": result.get("del", 0),
                    "ins": result.get("ins", 0),
                }
        elif task in ["SER", "GR", "SLU"]:
            formatted_result["accuracy_percent"] = round(result * 100, 2)
        elif task == "S2TT":
            formatted_result["bleu_score"] = round(result.get("bleu", 0), 2)
            formatted_result["chrf_score"] = round(result.get("chrf", 0), 2)
        elif task == "SD":
            formatted_result["der_percent"] = round(result.get("der", 0) * 100, 2)
        elif task == "SA-ASR":
            formatted_result["cpwer_percent"] = round(result.get("cpwer", 0) * 100, 2)
            formatted_result["der_percent"] = round(result.get("der", 0) * 100, 2)
        
        return formatted_result
        
    finally:
        # Cleanup temp files
        os.unlink(ref_file)
        os.unlink(hyp_file)


def evaluate_all(
    gt_dir: Path,
    pred_dir: Path,
    datasets: List[str] | None = None,
) -> Dict[str, Any]:
    """
    Evaluate all datasets.
    
    Args:
        gt_dir: Directory containing GT JSONL files
        pred_dir: Directory containing prediction TXT files
        datasets: List of dataset names to evaluate (None = all)
        
    Returns:
        Combined evaluation results
    """
    all_results = {
        "evaluation_time": datetime.now().isoformat(),
        "gt_dir": str(gt_dir),
        "pred_dir": str(pred_dir),
        "tasks": {},
    }
    
    # Find datasets to evaluate
    if datasets:
        gt_files = [gt_dir / f"{d}.jsonl" for d in datasets]
    else:
        gt_files = sorted(gt_dir.glob("*.jsonl"))
    
    for gt_path in gt_files:
        dataset_name = gt_path.stem
        pred_path = pred_dir / f"{dataset_name}.txt"
        
        if not pred_path.exists():
            logger.warning(f"Prediction file not found: {pred_path}, skipping")
            continue
        
        try:
            result = evaluate_dataset(gt_path, pred_path)
            all_results["tasks"][dataset_name] = result
            logger.info(f"  Result: {result}")
        except Exception as e:
            logger.error(f"Failed to evaluate {dataset_name}: {e}")
            all_results["tasks"][dataset_name] = {"error": str(e)}
    
    return all_results


def main():
    parser = argparse.ArgumentParser(description="Run SURE Benchmark evaluation")
    parser.add_argument("--gt", type=str, help="Ground truth JSONL file")
    parser.add_argument("--pred", type=str, help="Prediction TXT file")
    parser.add_argument("--gt-dir", type=str, help="Ground truth directory")
    parser.add_argument("--pred-dir", type=str, help="Prediction directory")
    parser.add_argument("--task", type=str, help="Task type (ASR, SER, etc.)")
    parser.add_argument("--language", type=str, help="Language (zh, en, etc.)")
    parser.add_argument("--datasets", type=str, nargs="+", help="Dataset names to evaluate")
    parser.add_argument("--output", type=str, help="Output JSON file for results")
    parser.add_argument("--save-dir", type=str, default="results", help="Directory to save results")
    
    args = parser.parse_args()
    
    if args.gt and args.pred:
        # Single dataset evaluation
        result = evaluate_dataset(
            Path(args.gt),
            Path(args.pred),
            args.task,
            args.language,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            logger.info(f"Result saved to {args.output}")
    
    elif args.gt_dir and args.pred_dir:
        # Batch evaluation
        results = evaluate_all(
            Path(args.gt_dir),
            Path(args.pred_dir),
            args.datasets,
        )
        
        print("\n" + "=" * 60)
        print("Evaluation Summary")
        print("=" * 60)
        for name, result in results["tasks"].items():
            if "error" in result:
                print(f"{name}: ERROR - {result['error']}")
            else:
                metrics = []
                if "wer_percent" in result:
                    metrics.append(f"WER={result['wer_percent']:.2f}%")
                if "accuracy_percent" in result:
                    metrics.append(f"Acc={result['accuracy_percent']:.2f}%")
                if "bleu_score" in result:
                    metrics.append(f"BLEU={result['bleu_score']:.2f}")
                if "der_percent" in result:
                    metrics.append(f"DER={result['der_percent']:.2f}%")
                print(f"{name}: {', '.join(metrics)}")
        print("=" * 60)
        
        # Save results
        save_dir = Path(args.save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        result_path = save_dir / f"sure_evaluation_{timestamp}.json"
        
        with open(result_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Results saved to {result_path}")
    
    else:
        parser.error("Either (--gt and --pred) or (--gt-dir and --pred-dir) must be specified")


if __name__ == "__main__":
    main()
