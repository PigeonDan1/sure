"""
Model Identity Mapping for SURE-EVAL.

This module provides bidirectional mapping between:
1. Local tool names (asr_qwen3, asr_whisper, etc.)
2. Benchmark model names (Qwen3-ASR-1.7B, Whisper-large-v3, etc.)

This is essential for:
- Looking up SOTA baselines when evaluating local tools
- Recording evaluation results in the correct model report
- Comparing local tools against benchmark results
"""

from __future__ import annotations

from typing import Optional

# Mapping from local tool name to benchmark model name
# Used when evaluating a local tool to find its SOTA comparison
TOOL_TO_BENCHMARK: dict[str, str] = {
    # ASR Tools
    "asr_qwen3": "Qwen3-ASR-1.7B",  # Note: Different from Qwen3-Omni (multi-task)
    "asr_whisper": "Whisper-large-v3",
    "sensevoice": "SenseVoice-Small",
    "firered_asr": "FireRed-ASR-1.7B",
    
    # Multi-task Models (support ASR + other tasks)
    "qwen3_omni": "Qwen3-Omni",
    "gemini_2.5pro": "Gemini-2.5pro",
    "gemini_3.0pro": "Gemini-3.0pro",
    "kimi_audio": "Kimi-Audio",
    
    # S2TT Tools
    "s2tt_nllb": "NLLB-200",  # Not in SURE SOTA, baseline model
    "seamless": "SeamlessM4T",  # Not in SURE SOTA
    
    # SER Tools
    "emotion2vec": "Emotion2Vec",  # Not in SURE SOTA
    
    # SD Tools
    "diarizen": "DiariZen",  # Not in SURE SOTA
    "pyannote": "PyAnnote",  # Not in SURE SOTA
}

# Reverse mapping: benchmark name to local tool name
BENCHMARK_TO_TOOL: dict[str, str] = {v: k for k, v in TOOL_TO_BENCHMARK.items()}

# Additional metadata about model relationships
MODEL_METADATA: dict[str, dict] = {
    "Qwen3-ASR-1.7B": {
        "type": "asr_specific",
        "table": "Table 3 (Front-end Perception)",
        "tasks": ["ASR"],
        "note": "ASR-optimized model, different from Qwen3-Omni",
    },
    "Qwen3-Omni": {
        "type": "multi_task",
        "table": "Table 4 (Horizontal Comparison)",
        "tasks": ["ASR", "S2TT", "SER", "GR", "SLU"],
        "note": "Multi-task foundation model",
    },
    "Whisper-large-v3": {
        "type": "asr_specific",
        "table": "Table 3",
        "tasks": ["ASR"],
        "note": "OpenAI's ASR model",
    },
    "FireRed-ASR-1.7B": {
        "type": "asr_specific",
        "table": "Table 3",
        "tasks": ["ASR"],
        "note": "Specialized in Chinese dialects",
    },
    "Kimi-Audio": {
        "type": "multi_task",
        "table": "Table 3 & 4",
        "tasks": ["ASR", "SER", "GR"],
        "note": "Moonshot's audio model",
    },
    "Gemini-2.5pro": {
        "type": "multi_task",
        "table": "Table 3 & 4",
        "tasks": ["ASR", "S2TT", "SER", "GR", "SLU"],
        "note": "Google's model with hotword injection",
    },
    "Gemini-3.0pro": {
        "type": "multi_task",
        "table": "Table 4",
        "tasks": ["ASR", "S2TT", "SER", "GR", "SLU"],
        "note": "Google's latest model",
    },
    "SenseVoice-Small": {
        "type": "asr_specific",
        "table": "Table 3",
        "tasks": ["ASR"],
        "note": "Alibaba's lightweight ASR",
    },
}


def get_benchmark_name(tool_name: str) -> Optional[str]:
    """
    Get benchmark model name from local tool name.
    
    Args:
        tool_name: Local tool name (e.g., 'asr_qwen3')
        
    Returns:
        Benchmark model name or None if not mapped
        
    Example:
        >>> get_benchmark_name('asr_qwen3')
        'Qwen3-ASR-1.7B'
    """
    return TOOL_TO_BENCHMARK.get(tool_name)


def get_tool_name(benchmark_name: str) -> Optional[str]:
    """
    Get local tool name from benchmark model name.
    
    Args:
        benchmark_name: Benchmark model name (e.g., 'Qwen3-ASR-1.7B')
        
    Returns:
        Local tool name or None if not mapped
        
    Example:
        >>> get_tool_name('Qwen3-ASR-1.7B')
        'asr_qwen3'
    """
    return BENCHMARK_TO_TOOL.get(benchmark_name)


def get_model_metadata(benchmark_name: str) -> Optional[dict]:
    """
    Get metadata about a benchmark model.
    
    Args:
        benchmark_name: Benchmark model name
        
    Returns:
        Model metadata dict or None
    """
    return MODEL_METADATA.get(benchmark_name)


def is_sota(tool_name: str, dataset: str, score: float, metric: str) -> tuple[bool, float]:
    """
    Check if a score is SOTA for a dataset.
    
    Args:
        tool_name: Local tool name
        dataset: Dataset name
        score: Score achieved
        metric: Metric type (lower_is_better or higher_is_better)
        
    Returns:
        Tuple of (is_sota, rps)
        
    Example:
        >>> is_sota('asr_qwen3', 'aishell1', 0.85, 'cer')
        (False, 0.94)  # 0.94 of SOTA (0.80)
    """
    from sure_eval.reports import SOTAManager
    
    benchmark_name = get_benchmark_name(tool_name)
    if not benchmark_name:
        return False, 0.0
    
    sota = SOTAManager()
    rps = sota.calculate_rps(dataset, score)
    
    if rps is None:
        return False, 0.0
    
    # RPS >= 1.0 means SOTA or better
    return rps >= 1.0, rps


def list_mapped_tools() -> list[str]:
    """List all tools with benchmark mappings."""
    return list(TOOL_TO_BENCHMARK.keys())


def list_benchmark_models() -> list[str]:
    """List all benchmark models with tool mappings."""
    return list(BENCHMARK_TO_TOOL.keys())


def register_mapping(tool_name: str, benchmark_name: str, metadata: Optional[dict] = None):
    """
    Register a new tool-to-benchmark mapping.
    
    Args:
        tool_name: Local tool name
        benchmark_name: Benchmark model name
        metadata: Optional model metadata
    """
    TOOL_TO_BENCHMARK[tool_name] = benchmark_name
    BENCHMARK_TO_TOOL[benchmark_name] = tool_name
    
    if metadata:
        MODEL_METADATA[benchmark_name] = metadata
