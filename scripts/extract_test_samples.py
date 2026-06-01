#!/usr/bin/env python3
"""
从 SURE Benchmark 中提取标准化测试样本。

用法:
    python scripts/extract_test_samples.py [--samples N] [--output-dir DIR]

示例:
    # 提取默认数量样本 (每个任务3个)
    python scripts/extract_test_samples.py
    
    # 提取5个样本
    python scripts/extract_test_samples.py --samples 5
"""

import json
import random
import shutil
import argparse
from pathlib import Path
from collections import defaultdict


def load_sure_data(base_dir: Path) -> list:
    """加载所有 SURE jsonl 数据."""
    all_data = []
    jsonl_files = list(base_dir.glob("*.jsonl"))
    
    for jsonl_file in jsonl_files:
        task = jsonl_file.stem.split('_')[0]  # e.g., "ASR_dev" -> "ASR"
        with open(jsonl_file, 'r', encoding='utf-8') as f:
            for line in f:
                record = json.loads(line.strip())
                record['source_file'] = jsonl_file.name
                record['task'] = task
                all_data.append(record)
    
    return all_data


def find_audio_file(audio_base: Path, sample: dict) -> Path | None:
    """智能查找音频文件路径."""
    dataset = sample.get("dataset", "")
    key = sample["key"]
    
    # 数据集到目录映射
    dataset_mapping = {
        "kespeech": "kespeech_test",
        "aishell1": "aishell-1_test",
        "aishell-5": "aishell-5_test",
        "librispeech": "librispeech-test-clean",
        "IEMOCAP": "IEMOCAP_test",
        "CoVoST2": "CoVoST2_S2TT_en2zh_test",  # 默认使用 en2zh
        "CS-Dialogue": "CS-Dialogue_test",
        "voxpopuli": "voxpopuli_en_test",
        "mmsu": "mmsu_reasoning_test",
        "contextasr": "contextasr_test",
    }
    
    # 1. 根据数据集查找
    for prefix, dir_name in dataset_mapping.items():
        if prefix in dataset:
            wav_path = audio_base / dir_name / f"{key}.wav"
            if wav_path.exists():
                return wav_path
    
    # 2. 深度搜索所有目录
    for subdir in audio_base.iterdir():
        if subdir.is_dir():
            wav_path = subdir / f"{key}.wav"
            if wav_path.exists():
                return wav_path
    
    return None


def extract_samples(
    sure_base: Path,
    audio_base: Path,
    output_dir: Path,
    samples_per_task: int = 3
):
    """提取测试样本."""
    
    print(f"Loading SURE data from {sure_base}...")
    all_data = load_sure_data(sure_base)
    print(f"Total records: {len(all_data)}")
    
    # 按任务分组
    by_task = defaultdict(list)
    for record in all_data:
        by_task[record['task']].append(record)
    
    # 创建输出目录
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 为每个任务提取样本
    for task, records in sorted(by_task.items()):
        print(f"\nProcessing {task} ({len(records)} records)...")
        
        task_dir = output_dir / task
        task_dir.mkdir(exist_ok=True)
        
        # 随机选择样本
        selected = random.sample(records, min(samples_per_task, len(records)))
        
        manifest = []
        for i, sample in enumerate(selected, 1):
            # 查找音频文件
            audio_path = find_audio_file(audio_base, sample)
            
            if audio_path and audio_path.exists():
                # 复制音频文件
                dest_name = f"sample_{i}_{sample['key']}.wav"
                dest_path = task_dir / dest_name
                shutil.copy2(audio_path, dest_path)
                
                manifest.append({
                    "id": i,
                    "key": sample["key"],
                    "dataset": sample.get("dataset", "unknown"),
                    "audio": dest_name,
                    "ground_truth": sample.get("text", sample.get("label", sample.get("target", "")))
                })
                print(f"  ✓ Sample {i}: {sample['key']} from {sample.get('dataset', 'unknown')}")
            else:
                print(f"  ✗ Sample {i}: {sample['key']} - audio not found")
        
        # 写入 manifest
        manifest_path = task_dir / "manifest.json"
        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
    
    print(f"\n✓ Test samples extracted to {output_dir}")


def main():
    parser = argparse.ArgumentParser(description="Extract test samples from SURE benchmark")
    parser.add_argument("--samples", type=int, default=3, help="Samples per task (default: 3)")
    parser.add_argument("--output-dir", type=str, default="tests/fixtures", help="Output directory")
    
    args = parser.parse_args()
    
    # 路径配置
    sure_base = Path("data/datasets/sure_benchmark/jsonl")  # jsonl files
    audio_base = Path("data/datasets/sure_benchmark/SURE_Test_Suites")
    output_dir = Path(args.output_dir)
    
    # 设置随机种子以保证可复现
    random.seed(42)
    
    extract_samples(sure_base, audio_base, output_dir, args.samples)


if __name__ == "__main__":
    main()
