"""
Unified dataset manager for SURE-EVAL.

Handles:
1. Dataset download (HuggingFace, ModelScope, SURE Benchmark)
2. Format conversion (CSV → JSONL)
3. Path resolution
4. Configuration mapping
"""

from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path
from typing import Any

from sure_eval.core.config import Config
from sure_eval.core.logging import get_logger

logger = get_logger(__name__)


# Mapping from CSV filename to metadata
# This bridges the gap between actual filenames and config names
CSV_DATASETS = {
    "aishell1-test_ASR": {
        "config_name": "aishell1",
        "audio_dir": "aishell-1_test",
        "task": "ASR",
        "language": "zh",
        "path_mappings": {
            "aishell-1-test/": "aishell-1_test/",
        },
    },
    "aishell-5_eval1": {
        "config_name": "aishell5",
        "audio_dir": "aishell-5_test",
        "task": "ASR",
        "language": "zh",
        "path_mappings": {
            "aishell-5-eval1/": "aishell-5_test/",
        },
    },
    "librispeech_test-clean_ASR": {
        "config_name": "librispeech_clean",
        "audio_dir": "librispeech-test-clean",
        "task": "ASR",
        "language": "en",
        "path_mappings": {
            "librispeech_test-clean/": "librispeech-test-clean/",
        },
    },
    "librispeech_test-other_ASR": {
        "config_name": "librispeech_other",
        "audio_dir": "librispeech-test-other",
        "task": "ASR",
        "language": "en",
        "path_mappings": {
            "librispeech_test-other/": "librispeech-test-other/",
        },
    },
    "kespeech": {
        "config_name": "kespeech",
        "audio_dir": "kespeech_test",
        "task": "ASR",
        "language": "zh",
        "path_mappings": {
            "kespeech/": "kespeech_test/",
        },
    },
    "voxpopuli_test": {
        "config_name": "voxpopuli",
        "audio_dir": "voxpopuli_en_test",
        "task": "ASR",
        "language": "en",
        "path_mappings": {
            "voxpopuli_test/": "voxpopuli_en_test/",
        },
    },
    "contextasr_english": {
        "config_name": "contextasr_en",
        "audio_dir": "librispeech-test-clean",  # Shares audio with librispeech
        "task": "ASR",
        "language": "en",
        "path_mappings": {
            "contextasr_english/": "librispeech-test-clean/",
        },
    },
    "contextasr_mandarin": {
        "config_name": "contextasr_zh",
        "audio_dir": "aishell-1_test",  # Shares audio with aishell
        "task": "ASR",
        "language": "zh",
        "path_mappings": {
            "contextasr_mandarin/": "aishell-1_test/",
        },
    },
    "CoVoST2_S2TT_en2zh_test": {
        "config_name": "covost2_en2zh",
        "audio_dir": "CoVoST2_S2TT_en2zh_test",
        "task": "S2TT",
        "language": "en",
        "path_mappings": {},
    },
    "CoVoST2_S2TT_zh2en_test": {
        "config_name": "covost2_zh2en",
        "audio_dir": "CoVoST2_S2TT_zh2en_test",
        "task": "S2TT",
        "language": "zh",
        "path_mappings": {},
    },
    "CS_dialogue": {
        "config_name": "cs_dialogue",
        "audio_dir": "CS-Dialogue_test",
        "task": "ASR",
        "language": "cs",  # Code-switching
        "path_mappings": {
            "CS_dialogue/": "CS-Dialogue_test/",
        },
    },
    "IEMOCAP_SER_test": {
        "config_name": "iemocap",
        "audio_dir": "IEMOCAP_test",
        "task": "SER",
        "language": "en",
        "path_mappings": {
            "IEMOCAP_SER_test/": "IEMOCAP_test/",
            "IEMOCAP_SER_test/wav/": "IEMOCAP_test/",
        },
    },
    "librispeech_test_clean_GR": {
        "config_name": "librispeech_gr",
        "audio_dir": "librispeech-test-clean",
        "task": "GR",
        "language": "en",
        "path_mappings": {
            "librispeech_test-clean/": "librispeech-test-clean/",
        },
    },
    "mmsu": {
        "config_name": "mmsu",
        "audio_dir": "mmsu_reasoning_test",
        "task": "SLU",
        "language": "zh",
        "path_mappings": {
            "mmsu/": "mmsu_reasoning_test/",
        },
    },
}


class DatasetManager:
    """
    Unified dataset manager.
    
    Handles SURE Benchmark datasets and standard HuggingFace/ModelScope datasets.
    """
    
    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config.from_env()
        self.data_dir = Path(self.config.data.datasets)
        self.sure_dir = self.data_dir / "sure_benchmark"
        self.jsonl_dir = self.sure_dir / "jsonl"
        
        # Ensure directories exist
        self.jsonl_dir.mkdir(parents=True, exist_ok=True)
    
    def get_jsonl_path(self, dataset_name: str) -> Path:
        """Get the JSONL file path for a dataset."""
        # Map config name to CSV name if needed
        csv_name = self._config_to_csv_name(dataset_name)
        return self.jsonl_dir / f"{csv_name}.jsonl"
    
    def is_available(self, dataset_name: str) -> bool:
        """Check if dataset JSONL is available."""
        return self.get_jsonl_path(dataset_name).exists()
    
    def download_and_convert(self, dataset_name: str) -> Path:
        """
        Download SURE Benchmark dataset and convert to JSONL.
        
        Args:
            dataset_name: Dataset name (config name or CSV name)
            
        Returns:
            Path to JSONL file
        """
        csv_name = self._config_to_csv_name(dataset_name)
        
        if csv_name not in CSV_DATASETS:
            # Try standard HuggingFace/ModelScope download
            return self._download_standard(dataset_name)
        
        # Download SURE Benchmark datasets
        self._download_sure_csv()
        self._download_sure_suites()
        
        # Convert to JSONL
        csv_path = self.sure_dir / "SURE_Test_csv" / f"{csv_name}.csv"
        if not csv_path.exists():
            raise FileNotFoundError(f"CSV not found: {csv_path}")
        
        jsonl_path = self._convert_csv_to_jsonl(csv_path)
        
        logger.info("Dataset ready", dataset=dataset_name, jsonl=str(jsonl_path))
        return jsonl_path
    
    def _download_sure_csv(self) -> None:
        """Download SURE_Test_csv if not present."""
        csv_dir = self.sure_dir / "SURE_Test_csv"
        
        # Check if already downloaded
        if csv_dir.exists() and any(csv_dir.glob("*.csv")):
            logger.debug("SURE_Test_csv already exists")
            return
        
        logger.info("Downloading SURE_Test_csv...")
        
        cmd = [
            "modelscope", "download",
            "--dataset", "SUREBenchmark/SURE_Test_csv",
            "--local_dir", str(csv_dir),
        ]
        
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            logger.info("SURE_Test_csv downloaded")
        except subprocess.CalledProcessError as e:
            logger.error("Failed to download SURE_Test_csv", error=e.stderr)
            raise
        except FileNotFoundError:
            logger.error("modelscope CLI not found. Install with: pip install modelscope")
            raise
    
    def _download_sure_suites(self) -> None:
        """Download SURE_Test_Suites if not present."""
        suites_dir = self.sure_dir / "SURE_Test_Suites"
        
        # Check if already downloaded (look for extracted directories)
        audio_dirs = [d for d in suites_dir.iterdir() if d.is_dir() and not d.name.endswith(".tar.gz")]
        if len(audio_dirs) >= 5:  # Assume downloaded if we have several audio dirs
            logger.debug("SURE_Test_Suites already exists")
            return
        
        logger.info("Downloading SURE_Test_Suites...")
        
        cmd = [
            "modelscope", "download",
            "--dataset", "SUREBenchmark/SURE_Test_Suites",
            "--local_dir", str(suites_dir),
        ]
        
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            logger.info("SURE_Test_Suites downloaded")
            
            # Extract tar files
            self._extract_tar_files(suites_dir)
            
        except subprocess.CalledProcessError as e:
            logger.error("Failed to download SURE_Test_Suites", error=e.stderr)
            raise
        except FileNotFoundError:
            logger.error("modelscope CLI not found. Install with: pip install modelscope")
            raise
    
    def _extract_tar_files(self, suites_dir: Path) -> None:
        """Extract all tar.gz files in directory."""
        import tarfile
        
        for tar_file in suites_dir.glob("*.tar.gz"):
            extract_dir = suites_dir / tar_file.stem.replace(".tar", "")
            
            if extract_dir.exists() and any(extract_dir.iterdir()):
                logger.debug(f"Already extracted: {tar_file.name}")
                continue
            
            logger.info(f"Extracting {tar_file.name}...")
            extract_dir.mkdir(exist_ok=True)
            
            try:
                with tarfile.open(tar_file, "r:gz") as tar:
                    tar.extractall(path=extract_dir)
            except Exception as e:
                logger.warning(f"Failed to extract {tar_file.name}: {e}")
    
    def _convert_csv_to_jsonl(self, csv_path: Path) -> Path:
        """Convert CSV file to JSONL format."""
        csv_name = csv_path.stem
        jsonl_path = self.jsonl_dir / f"{csv_name}.jsonl"
        
        if jsonl_path.exists():
            logger.debug(f"JSONL already exists: {jsonl_path}")
            return jsonl_path
        
        # Get metadata
        meta = CSV_DATASETS.get(csv_name, {
            "task": "ASR",
            "language": "auto",
            "path_mappings": {},
        })
        
        task = meta["task"]
        language = meta["language"]
        path_mappings = meta.get("path_mappings", {})
        config_name = meta.get("config_name", csv_name)
        
        samples = []
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader, None)
            
            if not header:
                raise ValueError(f"Empty CSV: {csv_path}")
            
            # Find columns
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
                
                csv_path = row[audio_col]
                text = row[text_col]
                
                # Fix path
                fixed_path = self._fix_path(csv_path, path_mappings)
                
                # Extract key
                key = Path(csv_path).stem
                
                sample = {
                    "key": key,
                    "path": fixed_path,
                    "target": text.strip(),
                    "task": task,
                    "language": language,
                    "dataset": config_name,  # Use config name for mapping
                }
                
                samples.append(sample)
        
        # Write JSONL
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with open(jsonl_path, 'w', encoding='utf-8') as f:
            for sample in samples:
                f.write(json.dumps(sample, ensure_ascii=False) + '\n')
        
        logger.info(f"Converted {csv_name}: {len(samples)} samples")
        return jsonl_path
    
    def _fix_path(self, csv_path: str, mappings: dict[str, str]) -> str:
        """Fix audio path using mappings."""
        for old_prefix, new_prefix in mappings.items():
            if csv_path.startswith(old_prefix):
                return new_prefix + csv_path[len(old_prefix):]
        return csv_path
    
    def _config_to_csv_name(self, dataset_name: str) -> str:
        """Map config name to CSV filename."""
        # Direct match
        if dataset_name in CSV_DATASETS:
            return dataset_name
        
        # Reverse lookup by config_name
        for csv_name, meta in CSV_DATASETS.items():
            if meta.get("config_name") == dataset_name:
                return csv_name
        
        # Return as-is (might be standard HF/MS dataset)
        return dataset_name
    
    def _download_standard(self, dataset_name: str) -> Path:
        """Download standard HuggingFace/ModelScope dataset."""
        dataset_def = self.config.get_dataset(dataset_name)
        
        if not dataset_def:
            raise ValueError(f"Unknown dataset: {dataset_name}")
        
        if dataset_def.source == "huggingface":
            return self._download_huggingface(dataset_def)
        elif dataset_def.source == "modelscope":
            return self._download_modelscope(dataset_def)
        else:
            raise ValueError(f"Unknown source: {dataset_def.source}")
    
    def _download_huggingface(self, dataset_def) -> Path:
        """Download from HuggingFace."""
        from datasets import load_dataset
        
        logger.info(f"Downloading from HuggingFace: {dataset_def.dataset_id}")
        
        dataset = load_dataset(
            dataset_def.dataset_id,
            name=dataset_def.config,
            cache_dir=str(self.data_dir),
        )
        
        # Convert to JSONL
        output_path = self.jsonl_dir / f"{dataset_def.name}.jsonl"
        
        with open(output_path, 'w', encoding='utf-8') as f:
            for split in dataset_def.splits or ['test']:
                if split in dataset:
                    for item in dataset[split]:
                        sample = {
                            "key": item.get("id", ""),
                            "path": item.get("audio", ""),
                            "target": item.get("text", item.get("label", "")),
                            "task": dataset_def.task,
                            "language": dataset_def.language,
                            "dataset": dataset_def.name,
                        }
                        f.write(json.dumps(sample, ensure_ascii=False) + '\n')
        
        return output_path
    
    def _download_modelscope(self, dataset_def) -> Path:
        """Download from ModelScope."""
        try:
            from modelscope.msdatasets import MsDataset
        except ImportError:
            logger.error("modelscope not installed. Install with: pip install modelscope")
            raise
        
        logger.info(f"Downloading from ModelScope: {dataset_def.dataset_id}")
        
        dataset = MsDataset.load(
            dataset_def.dataset_id,
            cache_dir=str(self.data_dir),
        )
        
        # Convert to JSONL
        output_path = self.jsonl_dir / f"{dataset_def.name}.jsonl"
        
        with open(output_path, 'w', encoding='utf-8') as f:
            for item in dataset:
                sample = {
                    "key": item.get("id", ""),
                    "path": item.get("audio", ""),
                    "target": item.get("text", item.get("label", "")),
                    "task": dataset_def.task,
                    "language": dataset_def.language,
                    "dataset": dataset_def.name,
                }
                f.write(json.dumps(sample, ensure_ascii=False) + '\n')
        
        return output_path
    
    def normalize_dataset_name(self, name: str) -> str:
        """
        Normalize dataset name for consistent lookup across components.
        
        Converts various forms (CSV name, config name, display name) to
        the canonical config name used in baselines and reports.
        
        Examples:
            'CS_dialogue' -> 'cs_dialogue'
            'CoVoST2_S2TT_en2zh_test' -> 'covost2_en2zh'
            'aishell1-test_ASR' -> 'aishell1'
        """
        # First check if it's already a config name
        if name in self.config.datasets.definitions:
            return name
        
        # Check if it's a CSV name
        if name in CSV_DATASETS:
            config_name = CSV_DATASETS[name].get("config_name")
            if config_name:
                return config_name
            return name
        
        # Check reverse mapping (config -> CSV)
        for csv_name, meta in CSV_DATASETS.items():
            if meta.get("config_name") == name:
                return name
        
        # Try lowercase normalization as fallback
        lower_name = name.lower()
        if lower_name in self.config.datasets.definitions:
            return lower_name
        
        # Return as-is if no mapping found
        return name
    
    def list_available(self) -> list[str]:
        """
        List available datasets (normalized config names).
        
        Returns normalized names that can be used consistently across:
        - RPS baseline lookup
        - Report queries
        - Evaluation calls
        """
        available = set()
        
        # From JSONL files - always return normalized (config) names
        for jsonl_file in self.jsonl_dir.glob("*.jsonl"):
            csv_name = jsonl_file.stem
            normalized = self.normalize_dataset_name(csv_name)
            available.add(normalized)
        
        # From config
        for name in self.config.datasets.definitions.keys():
            available.add(self.normalize_dataset_name(name))
        
        return sorted(available)
    
    def get_info(self, dataset_name: str) -> dict[str, Any] | None:
        """Get dataset information."""
        csv_name = self._config_to_csv_name(dataset_name)
        
        if csv_name in CSV_DATASETS:
            meta = CSV_DATASETS[csv_name]
            return {
                "name": dataset_name,
                "csv_name": csv_name,
                "config_name": meta.get("config_name"),
                "task": meta["task"],
                "language": meta["language"],
                "source": "sure_benchmark",
                "is_available": self.is_available(dataset_name),
            }
        
        # Check config
        dataset_def = self.config.get_dataset(dataset_name)
        if dataset_def:
            return {
                "name": dataset_name,
                "task": dataset_def.task,
                "language": dataset_def.language,
                "source": dataset_def.source,
                "is_available": self.is_available(dataset_name),
            }
        
        return None
