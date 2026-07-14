#!/usr/bin/env python3
"""
Convert SURE Benchmark CSV files to JSONL format for evaluation.

This script:
1. Reads CSV annotation files
2. Fixes audio paths to match actual directory structure
3. Generates JSONL with fields: key, path, target, task, language, dataset

Usage:
    python scripts/convert_sure_to_jsonl.py --csv-dir data/datasets/sure_benchmark/SURE_Test_csv --output-dir data/datasets/sure_benchmark/jsonl
    python scripts/convert_sure_to_jsonl.py --csv aishell1-test_ASR.csv --output aishell1.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Dict, List

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sure_eval.core.logging import configure_logging, get_logger

configure_logging(level="INFO")
logger = get_logger(__name__)


# Mapping from CSV name to audio directory name
# CSV paths use hyphens, audio dirs use underscores
CSV_TO_AUDIO_DIR = {
    # ASR datasets
    "aishell1-test_ASR": {
        "audio_dir": "aishell-1_test",
        "task": "ASR",
        "language": "zh",
        "path_prefix": "aishell-1-test/",
        "path_replace": "aishell-1_test/",
    },
    "aishell-5_eval1": {
        "audio_dir": "aishell-5_test",
        "task": "ASR",
        "language": "zh",
        "path_prefix": "aishell-5-eval1/",
        "path_replace": "aishell-5_test/",
    },
    "librispeech_test-clean_ASR": {
        "audio_dir": "librispeech-test-clean",
        "task": "ASR",
        "language": "en",
        "path_prefix": "librispeech_test-clean/",
        "path_replace": "librispeech-test-clean/",
    },
    "librispeech_test-other_ASR": {
        "audio_dir": "librispeech-test-other",
        "task": "ASR",
        "language": "en",
        "path_prefix": "librispeech_test-other/",
        "path_replace": "librispeech-test-other/",
    },
    "kespeech": {
        "audio_dir": "kespeech_test",
        "task": "ASR",
        "language": "zh",
        "path_prefix": "kespeech/",
        "path_replace": "kespeech_test/",
    },
    "voxpopuli_test": {
        "audio_dir": "voxpopuli_en_test",
        "task": "ASR",
        "language": "en",
        "path_prefix": "voxpopuli_test/",
        "path_replace": "voxpopuli_en_test/",
    },
    # Context ASR
    "contextasr_english": {
        "audio_dir": "librispeech-test-clean",  # Uses librispeech audio
        "task": "ASR",
        "language": "en",
        "path_prefix": "contextasr_english/",
        "path_replace": "librispeech-test-clean/",
    },
    "contextasr_mandarin": {
        "audio_dir": "aishell-1_test",  # Uses aishell audio
        "task": "ASR",
        "language": "zh",
        "path_prefix": "contextasr_mandarin/",
        "path_replace": "aishell-1_test/",
    },
    # S2TT (Speech-to-Text Translation)
    "CoVoST2_S2TT_en2zh_test": {
        "audio_dir": "CoVoST2_S2TT_en2zh_test",
        "task": "S2TT",
        "language": "en",
        "path_prefix": "CoVoST2_S2TT_en2zh_test/",
        "path_replace": "CoVoST2_S2TT_en2zh_test/",
    },
    "CoVoST2_S2TT_zh2en_test": {
        "audio_dir": "CoVoST2_S2TT_zh2en_test",
        "task": "S2TT",
        "language": "zh",
        "path_prefix": "CoVoST2_S2TT_zh2en_test/",
        "path_replace": "CoVoST2_S2TT_zh2en_test/",
    },
    # Code-switching ASR
    "CS_dialogue": {
        "audio_dir": "CS-Dialogue_test",
        "task": "ASR",
        "language": "cs",  # Code-switching (zh + en)
        "path_prefix": "CS_dialogue/",
        "path_replace": "CS-Dialogue_test/",
    },
    # SER (Speech Emotion Recognition)
    "IEMOCAP_SER_test": {
        "audio_dir": "IEMOCAP_test",
        "task": "SER",
        "language": "en",
        "path_prefix": "IEMOCAP_SER_test/",
        "path_replace": "IEMOCAP_test/",
    },
    # Gender Recognition
    "librispeech_test_clean_GR": {
        "audio_dir": "librispeech-test-clean",
        "task": "GR",
        "language": "en",
        "path_prefix": "librispeech_test-clean/",
        "path_replace": "librispeech-test-clean/",
    },
    # SLU (Spoken Language Understanding)
    "mmsu": {
        "audio_dir": "mmsu_reasoning_test",
        "task": "SLU",
        "language": "zh",
        "path_prefix": "mmsu/",
        "path_replace": "mmsu_reasoning_test/",
    },
}


def fix_path(csv_path: str, mapping: Dict) -> str:
    """
    Fix audio path from CSV format to match actual directory structure.
    
    CSV paths use hyphens and different naming conventions than actual directories.
    """
    # Replace prefix
    path = csv_path
    
    # Remove any leading prefix and replace with correct one
    for old_prefix, new_prefix in [
        ("aishell-1-test/", "aishell-1_test/"),
        ("aishell-5-eval1/", "aishell-5_test/"),
        ("librispeech_test-clean/", "librispeech-test-clean/"),
        ("librispeech_test-other/", "librispeech-test-other/"),
        ("kespeech/", "kespeech_test/"),
        ("voxpopuli_test/", "voxpopuli_en_test/"),
        ("contextasr_english/", "librispeech-test-clean/"),
        ("contextasr_mandarin/", "aishell-1_test/"),
        ("CoVoST2_S2TT_en2zh_test/", "CoVoST2_S2TT_en2zh_test/"),
        ("CoVoST2_S2TT_zh2en_test/", "CoVoST2_S2TT_zh2en_test/"),
        ("CS_dialogue/", "CS-Dialogue_test/"),
        ("IEMOCAP_SER_test/", "IEMOCAP_test/"),
        ("mmsu/", "mmsu_reasoning_test/"),
    ]:
        if path.startswith(old_prefix):
            path = new_prefix + path[len(old_prefix):]
            break
    
    # Handle IEMOCAP special case (has extra wav/ subdirectory in CSV)
    if path.startswith("IEMOCAP_test/wav/"):
        path = path.replace("IEMOCAP_test/wav/", "IEMOCAP_test/")
    
    return path


def get_key_from_path(path: str) -> str:
    """Extract key from audio path."""
    # Remove directory prefix and extension
    basename = os.path.basename(path)
    key = os.path.splitext(basename)[0]
    return key


def convert_csv_to_jsonl(
    csv_path: Path,
    output_path: Path,
    audio_base_dir: Path | None = None,
) -> int:
    """
    Convert a single CSV file to JSONL.
    
    Args:
        csv_path: Path to CSV file
        output_path: Path to output JSONL file
        audio_base_dir: Base directory for audio files (for validation)
        
    Returns:
        Number of samples converted
    """
    csv_name = csv_path.stem
    mapping = CSV_TO_AUDIO_DIR.get(csv_name)
    
    if not mapping:
        logger.warning(f"Unknown CSV file: {csv_name}, using default mapping")
        mapping = {
            "audio_dir": csv_name.replace("_", "-"),
            "task": "ASR",
            "language": "auto",
        }
    
    task = mapping["task"]
    language = mapping["language"]
    audio_dir = mapping["audio_dir"]
    
    samples = []
    
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        header = next(reader, None)
        
        if not header:
            logger.warning(f"Empty CSV: {csv_path}")
            return 0
        
        # Find column indices
        audio_col = 0
        text_col = 1
        
        for i, col in enumerate(header):
            col_upper = col.upper()
            if "FILE" in col_upper or "AUDIO" in col_upper or "PATH" in col_upper:
                audio_col = i
            elif "LABEL" in col_upper or "TEXT" in col_upper or "TRAN" in col_upper:
                text_col = i
        
        # Process rows
        for row in reader:
            if len(row) < 2:
                continue
            
            csv_audio_path = row[audio_col]
            text = row[text_col]
            
            # Fix path
            fixed_path = fix_path(csv_audio_path, mapping)
            
            # Get key from path
            key = get_key_from_path(fixed_path)
            
            # Create sample
            sample = {
                "key": key,
                "path": fixed_path,
                "target": text.strip(),
                "task": task,
                "language": language,
                "dataset": csv_name,
            }
            
            samples.append(sample)
    
    # Write JSONL
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        for sample in samples:
            f.write(json.dumps(sample, ensure_ascii=False) + '\n')
    
    logger.info(f"Converted {csv_name}: {len(samples)} samples -> {output_path}")
    return len(samples)


def convert_all(
    csv_dir: Path,
    output_dir: Path,
    audio_base_dir: Path | None = None,
) -> Dict[str, int]:
    """
    Convert all CSV files in directory to JSONL.
    
    Args:
        csv_dir: Directory containing CSV files
        output_dir: Output directory for JSONL files
        audio_base_dir: Base directory for audio files
        
    Returns:
        Dictionary mapping dataset names to sample counts
    """
    results = {}
    
    csv_files = sorted(csv_dir.glob("*.csv"))
    logger.info(f"Found {len(csv_files)} CSV files")
    
    for csv_path in csv_files:
        jsonl_name = csv_path.stem + ".jsonl"
        output_path = output_dir / jsonl_name
        
        try:
            count = convert_csv_to_jsonl(csv_path, output_path, audio_base_dir)
            results[csv_path.stem] = count
        except Exception as e:
            logger.error(f"Failed to convert {csv_path}: {e}")
    
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Convert SURE Benchmark CSV to JSONL"
    )
    parser.add_argument(
        "--csv-dir",
        type=str,
        help="Directory containing CSV files",
    )
    parser.add_argument(
        "--csv",
        type=str,
        help="Single CSV file to convert",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data/datasets/sure_benchmark/jsonl",
        help="Output directory for JSONL files",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output JSONL file (for single file conversion)",
    )
    parser.add_argument(
        "--audio-base-dir",
        type=str,
        default="data/datasets/sure_benchmark/SURE_Test_Suites",
        help="Base directory for audio files",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify audio files exist",
    )
    
    args = parser.parse_args()
    
    audio_base_dir = Path(args.audio_base_dir) if args.audio_base_dir else None
    
    if args.csv:
        # Single file conversion
        csv_path = Path(args.csv)
        output_path = Path(args.output) if args.output else Path(args.output_dir) / (csv_path.stem + ".jsonl")
        count = convert_csv_to_jsonl(csv_path, output_path, audio_base_dir)
        print(f"Converted {count} samples to {output_path}")
    elif args.csv_dir:
        # Batch conversion
        csv_dir = Path(args.csv_dir)
        output_dir = Path(args.output_dir)
        results = convert_all(csv_dir, output_dir, audio_base_dir)
        
        print("\n" + "=" * 60)
        print("Conversion Summary")
        print("=" * 60)
        total = 0
        for name, count in sorted(results.items()):
            print(f"{name:40s}: {count:6d} samples")
            total += count
        print("-" * 60)
        print(f"{'Total':40s}: {total:6d} samples")
        print("=" * 60)
    else:
        parser.error("Either --csv or --csv-dir must be specified")


if __name__ == "__main__":
    main()
