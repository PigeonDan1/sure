"""Autonomous evaluation agent for SURE-EVAL."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from sure_eval.core.config import Config
from sure_eval.core.logging import get_logger
from sure_eval.datasets import DatasetManager
from sure_eval.evaluation import SUREEvaluator, RPSManager
# Model management removed - using DatasetManager and direct tool calls
from sure_eval.tools.mcp_client import ToolRegistry, ToolAdapter

logger = get_logger(__name__)


class EvaluationResult:
    """Result of an evaluation run."""
    
    def __init__(
        self,
        tool_name: str,
        dataset: str,
        metric: str,
        score: float,
        rps: float | None,
        num_samples: int,
        duration: float,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.tool_name = tool_name
        self.dataset = dataset
        self.metric = metric
        self.score = score
        self.rps = rps
        self.num_samples = num_samples
        self.duration = duration
        self.details = details or {}


class AutonomousEvaluator:
    """
    Autonomous evaluation agent.
    
    Orchestrates the complete evaluation pipeline:
    1. Dataset download (if needed)
    2. Load test samples (from JSONL format)
    3. Run tool inference
    4. Evaluate using SUREEvaluator (reference evaluation)
    5. Calculate RPS
    6. Record results
    """
    
    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config.from_env()
        self.config.ensure_directories()
        
        # Initialize components
        self.dataset_manager = DatasetManager(config)
        self.tool_registry = ToolRegistry()
        self.tool_adapter = ToolAdapter(self.tool_registry)
        self.rps_manager = RPSManager(config)
        
        logger.info("AutonomousEvaluator initialized")
    
    def evaluate_tool(
        self,
        tool_name: str,
        dataset: str,
        max_samples: int | None = None,
        metric_type: str | None = None,
    ) -> EvaluationResult:
        """
        Evaluate a tool on a dataset.
        
        This is the main entry point for autonomous evaluation.
        """
        start_time = time.time()
        
        logger.info(
            "Starting evaluation",
            tool=tool_name,
            dataset=dataset,
            max_samples=max_samples,
        )
        
        # Step 1: Load dataset (download if needed)
        jsonl_path, dataset_info = self._load_dataset(dataset)
        
        # Step 2: Load test samples
        samples = self._load_samples(jsonl_path, max_samples)
        logger.info("Loaded samples", num_samples=len(samples))
        
        # Step 3: Determine metric type and language
        task = dataset_info.get("task", "ASR") if dataset_info else "ASR"
        language = dataset_info.get("language", "auto") if dataset_info else "auto"
        
        if not metric_type:
            metric_type = self.config.get_default_metric(task)
        
        # Step 4: Run tool on samples
        predictions = self._run_tool(tool_name, samples, task)
        
        # Step 5: Save predictions to temp file for evaluation
        pred_file = self._save_predictions(samples, predictions)
        
        # Step 6: Evaluate using SUREEvaluator
        eval_result = self._evaluate_with_sure_evaluator(
            jsonl_path, pred_file, task, language
        )
        
        # Step 7: Calculate RPS
        score = self._extract_score(eval_result, task)
        rps = self.rps_manager.calculator.calculate(dataset, score)
        
        # Step 8: Record results
        duration = time.time() - start_time
        self.rps_manager.evaluate_and_record(
            tool_name=tool_name,
            dataset=dataset,
            score=score,
            metric=metric_type,
            metadata={
                "num_samples": len(samples),
                "duration": duration,
                "eval_details": eval_result,
            },
        )
        
        # Cleanup temp file
        os.unlink(pred_file)
        
        logger.info(
            "Evaluation completed",
            tool=tool_name,
            dataset=dataset,
            score=score,
            rps=rps,
            duration=duration,
        )
        
        return EvaluationResult(
            tool_name=tool_name,
            dataset=dataset,
            metric=metric_type,
            score=score,
            rps=rps,
            num_samples=len(samples),
            duration=duration,
            details=eval_result,
        )
    
    def batch_evaluate(
        self,
        tool_name: str,
        datasets: list[str],
        max_samples: int | None = None,
    ) -> list[EvaluationResult]:
        """Evaluate a tool on multiple datasets."""
        results = []
        for dataset in datasets:
            try:
                result = self.evaluate_tool(tool_name, dataset, max_samples)
                results.append(result)
            except Exception as e:
                logger.error(
                    "Evaluation failed",
                    tool=tool_name,
                    dataset=dataset,
                    error=str(e),
                )
        
        return results
    
    def compare_tools(
        self,
        tool_names: list[str],
        dataset: str,
        max_samples: int | None = None,
    ) -> dict[str, Any]:
        """Compare multiple tools on a dataset."""
        results = {}
        for tool_name in tool_names:
            try:
                result = self.evaluate_tool(tool_name, dataset, max_samples)
                results[tool_name] = {
                    "score": result.score,
                    "rps": result.rps,
                    "duration": result.duration,
                }
            except Exception as e:
                results[tool_name] = {"error": str(e)}
        
        # Rank by RPS
        ranked = sorted(
            [(name, data) for name, data in results.items() if "rps" in data],
            key=lambda x: x[1].get("rps", 0),
            reverse=True,
        )
        
        return {
            "dataset": dataset,
            "results": results,
            "ranking": ranked,
        }
    
    def recommend_tool(self, dataset: str) -> dict[str, Any]:
        """Recommend the best tool for a dataset."""
        recommendation = self.rps_manager.get_recommendation(dataset)
        
        # If no records, suggest default tool
        if not recommendation["ranking"]:
            dataset_info = self.config.get_dataset(dataset)
            if dataset_info and dataset_info.task in self.config.tools.default_tools:
                default_tool = self.config.tools.default_tools[dataset_info.task]
                recommendation["suggested_tool"] = default_tool
        
        return recommendation
    
    def quick_test(
        self,
        tool_name: str,
        dataset: str,
        num_samples: int = 10,
    ) -> dict[str, Any]:
        """
        Quick test of a tool on a small number of samples.
        
        This is useful for sanity checking a tool before full evaluation.
        
        Args:
            tool_name: Name of the tool to test
            dataset: Dataset name
            num_samples: Number of samples to test (default: 10)
            
        Returns:
            Dictionary with test results
        """
        logger.info("=" * 60)
        logger.info(f"QUICK TEST: {tool_name} on {dataset}")
        logger.info("=" * 60)
        
        start_time = time.time()
        
        # 1. Load dataset
        jsonl_path, dataset_info = self._load_dataset(dataset)
        samples = self._load_samples(jsonl_path, num_samples)
        logger.info(f"Loaded {len(samples)} samples")
        
        # 2. Run inference
        task = dataset_info.get("task", "ASR") if dataset_info else "ASR"
        language = dataset_info.get("language", "auto") if dataset_info else "auto"
        
        predictions = self._run_tool(tool_name, samples, task)
        
        # 3. Save predictions
        pred_file = self._save_predictions(samples, predictions)
        
        # 4. Evaluate
        eval_result = self._evaluate_with_sure_evaluator(
            jsonl_path, pred_file, task, language
        )
        
        os.unlink(pred_file)
        
        # 5. Calculate RPS
        score = self._extract_score(eval_result, task)
        rps = self.rps_manager.calculator.calculate(dataset, score)
        
        duration = time.time() - start_time
        
        # 6. Build result
        result = {
            "tool": tool_name,
            "dataset": dataset,
            "num_samples": len(samples),
            "duration": duration,
            "score": score,
            "metric": self.config.get_default_metric(task),
            "rps": rps,
            "details": eval_result,
            "predictions": [
                {
                    "key": s.get("key", ""),
                    "reference": s.get("target", ""),
                    "prediction": p,
                }
                for s, p in zip(samples, predictions)
            ],
        }
        
        # 7. Print summary
        logger.info("\n" + "=" * 60)
        logger.info("QUICK TEST RESULTS")
        logger.info("=" * 60)
        logger.info(f"Tool: {tool_name}")
        logger.info(f"Dataset: {dataset}")
        logger.info(f"Samples: {len(samples)}")
        logger.info(f"Duration: {duration:.2f}s")
        logger.info(f"Score: {score:.4f}")
        logger.info(f"RPS: {rps:.4f}" if rps else "RPS: N/A")
        logger.info("=" * 60)
        
        return result
    
    def _load_dataset(self, dataset_name: str) -> tuple[Path, dict | None]:
        """
        Load dataset, downloading if needed.
        
        Returns:
            Tuple of (jsonl_path, dataset_info)
        """
        jsonl_path = self.dataset_manager.get_jsonl_path(dataset_name)
        
        if not jsonl_path.exists():
            logger.info("Dataset not found, downloading", dataset=dataset_name)
            self.dataset_manager.download_and_convert(dataset_name)
            jsonl_path = self.dataset_manager.get_jsonl_path(dataset_name)
        
        if not jsonl_path.exists():
            raise FileNotFoundError(f"Dataset not available: {dataset_name}")
        
        # Get dataset info from first sample
        dataset_info = None
        try:
            with open(jsonl_path, 'r', encoding='utf-8') as f:
                first_line = f.readline()
                if first_line:
                    first_sample = json.loads(first_line)
                    dataset_info = {
                        "task": first_sample.get("task", "ASR"),
                        "language": first_sample.get("language", "auto"),
                        "dataset_key": first_sample.get("dataset", dataset_name),
                    }
        except Exception as e:
            logger.warning(f"Could not parse dataset info: {e}")
        
        return jsonl_path, dataset_info
    
    def _load_samples(
        self,
        jsonl_path: Path,
        max_samples: int | None = None,
    ) -> list[dict]:
        """Load test samples from JSONL file."""
        samples = []
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if max_samples and i >= max_samples:
                    break
                if line.strip():
                    samples.append(json.loads(line))
        
        return samples
    
    def _run_tool(
        self,
        tool_name: str,
        samples: list[dict],
        task: str,
    ) -> list[str]:
        """Run tool on samples and return predictions."""
        predictions = []
        
        logger.info("Running tool", tool=tool_name, num_samples=len(samples))
        
        # Get audio base directory
        audio_base = Path(self.config.data.datasets) / "sure_benchmark" / "SURE_Test_Suites"
        
        with self.tool_registry.create_client(tool_name) as client:
            for i, sample in enumerate(samples):
                # Get audio path (relative path in JSONL)
                rel_path = sample.get("path", "")
                audio_path = audio_base / rel_path
                
                if not audio_path.exists():
                    logger.warning("Audio file not found", path=str(audio_path), sample_idx=i)
                    predictions.append("")
                    continue
                
                try:
                    # Call tool based on task
                    if task == "ASR":
                        result = client.call("transcribe", {"audio_path": str(audio_path)})
                        prediction = result.get("content", [{}])[0].get("text", "")
                    elif task == "SER":
                        result = client.call("recognize_emotion", {"audio_path": str(audio_path)})
                        prediction = result.get("content", [{}])[0].get("emotion", "")
                    elif task == "GR":
                        result = client.call("recognize_gender", {"audio_path": str(audio_path)})
                        prediction = result.get("content", [{}])[0].get("gender", "")
                    elif task == "S2TT":
                        result = client.call("translate", {"audio_path": str(audio_path)})
                        prediction = result.get("content", [{}])[0].get("text", "")
                    elif task == "SD":
                        result = client.call("diarize", {"audio_path": str(audio_path)})
                        prediction = result.get("content", [{}])[0].get("rttm", "")
                    else:
                        # Generic invoke
                        result = client.call("invoke", {
                            "audio_path": str(audio_path),
                            "task": task,
                        })
                        prediction = result.get("content", [{}])[0].get("text", "")
                    
                    predictions.append(prediction)
                    
                except Exception as e:
                    logger.error(
                        "Tool call failed",
                        tool=tool_name,
                        sample_idx=i,
                        error=str(e),
                    )
                    predictions.append("")
                
                if (i + 1) % 100 == 0:
                    logger.info(f"Processed {i + 1}/{len(samples)} samples")
        
        return predictions
    
    def _save_predictions(
        self,
        samples: list[dict],
        predictions: list[str],
    ) -> str:
        """Save predictions to temp file for evaluation."""
        # Format: key\tprediction
        lines = []
        for sample, pred in zip(samples, predictions):
            key = sample.get("key", "")
            lines.append(f"{key}\t{pred}")
        
        # Write to temp file
        fd, path = tempfile.mkstemp(suffix=".txt", prefix="pred_")
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        
        return path
    
    def _evaluate_with_sure_evaluator(
        self,
        jsonl_path: Path,
        pred_file: str,
        task: str,
        language: str,
    ) -> dict[str, Any]:
        """
        Evaluate using SUREEvaluator (reference evaluation).
        
        Converts JSONL to ref format and runs evaluation.
        """
        # Convert JSONL to ref file format
        ref_lines = []
        with open(jsonl_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    sample = json.loads(line)
                    key = sample.get("key", "")
                    target = sample.get("target", "")
                    ref_lines.append(f"{key}\t{target}")
        
        # Write ref to temp file
        fd, ref_file = tempfile.mkstemp(suffix=".txt", prefix="ref_")
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write('\n'.join(ref_lines) + '\n')
        
        try:
            # Run evaluation using SUREEvaluator
            evaluator = SUREEvaluator(language=language)
            result = evaluator.evaluate(task, ref_file, pred_file)
            
            # Format result to dict
            if isinstance(result, dict):
                return result
            elif isinstance(result, (int, float)):
                return {"score": result}
            else:
                return {"result": str(result)}
        finally:
            os.unlink(ref_file)
    
    def _extract_score(self, eval_result: dict[str, Any], task: str) -> float:
        """Extract primary score from evaluation result."""
        if task == "ASR":
            # Use WER (lower is better, but RPS handles this)
            return eval_result.get("wer", 0.0)
        elif task in ["SER", "GR", "SLU"]:
            return eval_result.get("accuracy", 0.0)
        elif task == "S2TT":
            return eval_result.get("bleu", 0.0)
        elif task == "SD":
            return eval_result.get("der", 0.0)
        elif task == "SA-ASR":
            return eval_result.get("cpwer", 0.0)
        else:
            return eval_result.get("score", 0.0)
