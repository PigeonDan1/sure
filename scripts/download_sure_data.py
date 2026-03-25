#!/usr/bin/env python3
"""
SURE Benchmark Dataset Downloader

Downloads and extracts SURE benchmark datasets from ModelScope:
- SURE_Test_csv: Annotation files (CSV format)
- SURE_Test_Suites: Audio files

Usage:
    python scripts/download_sure_data.py          # Download both
    python scripts/download_sure_data.py --csv    # Download only CSV
    python scripts/download_sure_data.py --suites # Download only audio suites
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sure_eval.core.logging import configure_logging, get_logger

configure_logging(level="INFO")
logger = get_logger(__name__)


def run_command(cmd: list[str], cwd: Path | None = None, timeout: int = 3600) -> bool:
    """Run shell command with logging."""
    logger.info(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            logger.error(f"Command failed: {result.stderr}")
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error("Command timed out")
        return False
    except Exception as e:
        logger.error(f"Command error: {e}")
        return False


def download_csv(data_dir: Path) -> bool:
    """Download SURE_Test_csv (annotations)."""
    logger.info("=" * 60)
    logger.info("Downloading SURE_Test_csv (Annotations)")
    logger.info("=" * 60)
    
    output_dir = data_dir / "sure_benchmark" / "SURE_Test_csv"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Check if already downloaded
    if (output_dir / "aishell1-test_ASR.csv").exists():
        logger.info("CSV files already exist, skipping download")
        return True
    
    # Download using modelscope CLI
    cmd = [
        "modelscope", "download",
        "--dataset", "SUREBenchmark/SURE_Test_csv",
        "--local_dir", str(output_dir),
    ]
    
    return run_command(cmd, timeout=600)


def download_suites(data_dir: Path) -> bool:
    """Download SURE_Test_Suites (audio files)."""
    logger.info("=" * 60)
    logger.info("Downloading SURE_Test_Suites (Audio Files)")
    logger.info("=" * 60)
    
    output_dir = data_dir / "sure_benchmark" / "SURE_Test_Suites"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Check if already downloaded
    tar_files = list(output_dir.glob("*.tar.gz"))
    if len(tar_files) >= 11:  # Expected number of archives
        logger.info(f"Found {len(tar_files)} tar archives, skipping download")
        return True
    
    # Download using modelscope CLI
    cmd = [
        "modelscope", "download",
        "--dataset", "SUREBenchmark/SURE_Test_Suites",
        "--local_dir", str(output_dir),
    ]
    
    return run_command(cmd, timeout=7200)  # 2 hours timeout


def extract_archives(data_dir: Path) -> bool:
    """Extract all tar.gz archives."""
    logger.info("=" * 60)
    logger.info("Extracting Audio Archives")
    logger.info("=" * 60)
    
    suites_dir = data_dir / "sure_benchmark" / "SURE_Test_Suites"
    tar_files = list(suites_dir.glob("*.tar.gz"))
    
    if not tar_files:
        logger.warning("No tar files found")
        return True
    
    logger.info(f"Found {len(tar_files)} archives to extract")
    
    for i, tar_file in enumerate(tar_files, 1):
        subset_name = tar_file.stem.replace(".tar", "")
        extract_dir = suites_dir / subset_name
        
        # Skip if already extracted
        if extract_dir.exists() and any(extract_dir.iterdir()):
            logger.info(f"[{i}/{len(tar_files)}] {subset_name}: Already extracted")
            continue
        
        logger.info(f"[{i}/{len(tar_files)}] Extracting {tar_file.name}...")
        extract_dir.mkdir(exist_ok=True)
        
        cmd = ["tar", "-xzf", str(tar_file), "-C", str(extract_dir)]
        if not run_command(cmd):
            logger.warning(f"Failed to extract {tar_file.name}")
    
    logger.info("Extraction complete!")
    return True


def verify_data(data_dir: Path) -> dict:
    """Verify downloaded data."""
    logger.info("=" * 60)
    logger.info("Verifying Downloaded Data")
    logger.info("=" * 60)
    
    result = {
        "csv": {},
        "suites": {},
    }
    
    # Check CSV
    csv_dir = data_dir / "sure_benchmark" / "SURE_Test_csv"
    if csv_dir.exists():
        csv_files = list(csv_dir.glob("*.csv"))
        result["csv"]["count"] = len(csv_files)
        result["csv"]["files"] = [f.name for f in csv_files[:5]]
        logger.info(f"CSV files: {len(csv_files)}")
    
    # Check Suites
    suites_dir = data_dir / "sure_benchmark" / "SURE_Test_Suites"
    if suites_dir.exists():
        # Count extracted directories
        extracted_dirs = [d for d in suites_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
        result["suites"]["extracted"] = len(extracted_dirs)
        
        # Count audio files
        total_audio = 0
        for d in extracted_dirs:
            wav_files = list(d.glob("*.wav"))
            total_audio += len(wav_files)
        result["suites"]["audio_files"] = total_audio
        
        logger.info(f"Extracted subsets: {len(extracted_dirs)}")
        logger.info(f"Total audio files: {total_audio}")
    
    return result


def main():
    parser = argparse.ArgumentParser(description="Download SURE Benchmark datasets")
    parser.add_argument("--csv", action="store_true", help="Download only CSV annotations")
    parser.add_argument("--suites", action="store_true", help="Download only audio suites")
    parser.add_argument("--data-dir", type=str, default="./data/datasets", help="Data directory")
    parser.add_argument("--skip-extract", action="store_true", help="Skip extraction step")
    args = parser.parse_args()
    
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    
    success = True
    
    # Download CSV (default or --csv)
    if not args.suites or args.csv:
        if not download_csv(data_dir):
            success = False
    
    # Download Suites (default or --suites)
    if not args.csv or args.suites:
        if not download_suites(data_dir):
            success = False
        
        # Extract archives
        if not args.skip_extract:
            extract_archives(data_dir)
    
    # Verify
    result = verify_data(data_dir)
    
    # Summary
    print("\n" + "=" * 60)
    print("Download Summary")
    print("=" * 60)
    print(f"CSV files: {result['csv'].get('count', 0)}")
    print(f"Audio subsets: {result['suites'].get('extracted', 0)}")
    print(f"Audio files: {result['suites'].get('audio_files', 0)}")
    print("=" * 60)
    
    if success:
        print("✓ Download completed successfully!")
        return 0
    else:
        print("✗ Download completed with errors")
        return 1


if __name__ == "__main__":
    sys.exit(main())
