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
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dataset_alias import resolve_dataset_alias
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


DATASET_COLLECTION_ALIASES = {
    "gigaspeech": ("gigaspeech_test",),
    "librispeech": ("librispeech_clean", "librispeech_other"),
    "libri_speech": ("librispeech_clean", "librispeech_other"),
    "slidespeech": ("slidespeech_test",),
    "wenet": ("wenetspeech_test_net", "wenetspeech_test_meeting"),
    "wenetspeech": ("wenetspeech_test_net", "wenetspeech_test_meeting"),
    "wenet_speech": ("wenetspeech_test_net", "wenetspeech_test_meeting"),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        
        # Load OREF dataset configuration from YAML
        self._oref_config = self._load_oref_config()
        self.oref_local_datasets = self._oref_config.get("datasets", {})
        self.dataset_fallbacks = self._oref_config.get("fallbacks", {})

    def _task_slug(self, task: str | None) -> str:
        return str(task or "unknown").strip().lower().replace("-", "_")

    def _oref_projection_name(self, dataset_name: str, meta: dict[str, Any]) -> str:
        config_name = str(meta.get("config_name") or dataset_name)
        version_id = str(meta.get("version_id") or "unversioned")
        task_slug = self._task_slug(meta.get("task"))
        return f"{config_name}__{version_id}__{task_slug}"

    def _resolve_config_path(self, value: str | Path) -> Path:
        path = Path(value).expanduser()
        if path.is_absolute():
            return path
        return self.data_dir / path

    def _oref_dataset_dir(self, dataset_name: str, meta: dict[str, Any]) -> Path:
        config_name = str(meta.get("config_name") or dataset_name)
        return self.sure_dir / config_name

    def _oref_platform_meta_for_projection(self, projection_name: str) -> tuple[str, dict[str, Any]] | None:
        for dataset_name, meta in self.oref_local_datasets.items():
            if meta.get("source") != "oref_platform":
                continue
            if self._oref_projection_name(dataset_name, meta) == projection_name:
                return dataset_name, meta
        return None

    def _count_jsonl_rows(self, path: Path) -> int | None:
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    
    def _load_oref_config(self) -> dict[str, Any]:
        """Load OREF dataset configuration from YAML file."""
        oref_yaml = Path(__file__).parent.parent.parent.parent / "config" / "oref_datasets.yaml"
        if oref_yaml.exists():
            import yaml
            with open(oref_yaml, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if isinstance(data, dict):
                    return data
        return {"datasets": {}, "fallbacks": {}}
    
    def get_jsonl_path(self, dataset_name: str) -> Path:
        """Get the JSONL file path for a dataset."""
        canonical_name = self._canonical_name(dataset_name)
        return self.jsonl_dir / f"{canonical_name}.jsonl"

    def _existing_jsonl_for_dataset(self, dataset_name: str) -> Path | None:
        """Return an existing canonical JSONL path for aliases such as ``aishell1``.

        Main-flow artifacts commonly use versioned dataset ids such as
        ``aishell1__v1.0.2__asr`` while operators may still request the short
        alias.  Prefer exact matches, then accept a unique versioned projection.
        Ambiguous projections are left unresolved so the normal download/error
        path can surface the configuration problem explicitly.

        The exact-match-else-unique-projection rule is shared with
        /sure_reval's prediction-source resolver (resolve_dataset_alias in
        sure/skills/sure_eval/scripts/dataset_alias.py) so both skills agree
        on what a short dataset name means.
        """
        exact = self.jsonl_dir / f"{dataset_name}.jsonl"
        candidates = [dataset_name] if exact.exists() else []
        candidates += [path.stem for path in self.jsonl_dir.glob(f"{dataset_name}__*.jsonl")]
        resolved = resolve_dataset_alias(dataset_name, candidates)
        return self.jsonl_dir / f"{resolved}.jsonl" if resolved else None

    def _oref_source_sample_jsonl_path(self, dataset_name: str, meta: dict[str, Any]) -> Path:
        if meta.get("sample_jsonl"):
            return self._resolve_config_path(meta["sample_jsonl"])
        if meta.get("jsonl_path"):
            return self._resolve_config_path(meta["jsonl_path"])
        dataset_root = self._resolve_config_path(meta["dataset_root"])
        version_id = str(meta.get("version_id") or "v1.0.1")
        return dataset_root / "sample_files" / version_id / "sample.jsonl"

    def _oref_source_ds_jsonl_path(self, dataset_name: str, meta: dict[str, Any]) -> Path:
        if meta.get("ds_jsonl"):
            return self._resolve_config_path(meta["ds_jsonl"])
        dataset_root = self._resolve_config_path(meta["dataset_root"])
        version_id = str(meta.get("version_id") or "v1.0.1")
        return dataset_root / "sample_files" / version_id / "ds.jsonl"

    def _oref_source_raw_dir(self, meta: dict[str, Any]) -> Path:
        if meta.get("raw_dir"):
            return self._resolve_config_path(meta["raw_dir"])
        if meta.get("audio_dir"):
            return self._resolve_config_path(meta["audio_dir"])
        dataset_root = self._resolve_config_path(meta["dataset_root"])
        return dataset_root / "raws" / "sample"
    
    def is_available(self, dataset_name: str) -> bool:
        """Check if dataset JSONL is available."""
        if self.get_jsonl_path(dataset_name).exists():
            return True
        # Check OREF local datasets
        canonical = self._canonical_name(dataset_name)
        if canonical in self.oref_local_datasets:
            meta = self.oref_local_datasets[canonical]
            if meta.get("source") == "oref_platform":
                projection = self._oref_projection_name(canonical, meta)
                if (self.jsonl_dir / f"{projection}.jsonl").exists():
                    return True
                return self._oref_source_sample_jsonl_path(canonical, meta).exists()
            oref_jsonl = self._resolve_config_path(meta["jsonl_path"])
            return oref_jsonl.exists()
        # Check fallback to OREF equivalent
        fallback = self.dataset_fallbacks.get(canonical)
        if fallback and fallback in self.oref_local_datasets:
            meta = self.oref_local_datasets[fallback]
            if meta.get("source") == "oref_platform":
                projection = self._oref_projection_name(fallback, meta)
                if (self.jsonl_dir / f"{projection}.jsonl").exists():
                    return True
                return self._oref_source_sample_jsonl_path(fallback, meta).exists()
            oref_jsonl = self._resolve_config_path(meta["jsonl_path"])
            return oref_jsonl.exists()
        return False
    
    def download_and_convert(self, dataset_name: str) -> Path:
        """
        Download SURE Benchmark dataset and convert to JSONL.
        
        Args:
            dataset_name: Dataset name (config name or CSV name)
            
        Returns:
            Path to JSONL file
        """
        if dataset_name in self.oref_local_datasets:
            meta = self.oref_local_datasets[dataset_name]
            if meta.get("source") == "oref_platform":
                return self._convert_oref_platform_to_jsonl(dataset_name)
            return self._convert_oref_jsonl_to_jsonl(dataset_name)

        canonical = self._canonical_name(dataset_name)
        existing_jsonl = self.get_jsonl_path(canonical)
        if existing_jsonl.exists():
            logger.info("Using existing dataset JSONL", dataset=canonical, jsonl=str(existing_jsonl))
            return existing_jsonl
        existing_jsonl = self._existing_jsonl_for_dataset(canonical)
        if existing_jsonl:
            logger.info(
                "Using existing canonical dataset JSONL",
                dataset=canonical,
                resolved_dataset=existing_jsonl.stem,
                jsonl=str(existing_jsonl),
            )
            return existing_jsonl

        projection_meta = self._oref_platform_meta_for_projection(canonical)
        if projection_meta:
            oref_dataset_name, _meta = projection_meta
            return self._convert_oref_platform_to_jsonl(oref_dataset_name)
        
        # Check OREF local datasets first
        if canonical in self.oref_local_datasets:
            meta = self.oref_local_datasets[canonical]
            if meta.get("source") == "oref_platform":
                return self._convert_oref_platform_to_jsonl(canonical)
            return self._convert_oref_jsonl_to_jsonl(canonical)
        
        # Fallback: SURE Benchmark -> OREF local equivalent
        fallback = self.dataset_fallbacks.get(canonical)
        if fallback and fallback in self.oref_local_datasets:
            oref_meta = self.oref_local_datasets[fallback]
            if oref_meta.get("source") == "oref_platform":
                oref_jsonl = self._oref_source_sample_jsonl_path(fallback, oref_meta)
            else:
                oref_jsonl = self._resolve_config_path(oref_meta["jsonl_path"])
            if oref_jsonl.exists():
                logger.info("Using OREF fallback", requested=canonical, fallback=fallback)
                if oref_meta.get("source") == "oref_platform":
                    return self._convert_oref_platform_to_jsonl(fallback, output_name=canonical)
                return self._convert_oref_jsonl_to_jsonl(fallback, output_name=canonical)
        
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
        suites_dir.mkdir(parents=True, exist_ok=True)
        
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
        canonical_name = self._canonical_name(csv_name)
        jsonl_path = self.jsonl_dir / f"{canonical_name}.jsonl"
        
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
    
    def _convert_oref_jsonl_to_jsonl(self, dataset_name: str, output_name: str | None = None) -> Path:
        """Convert OREF local sample.jsonl to SURE-EVAL standard JSONL format."""
        meta = self.oref_local_datasets[dataset_name]
        oref_jsonl_path = self._resolve_config_path(meta["jsonl_path"])
        audio_dir = self._resolve_config_path(meta["audio_dir"])
        
        if not oref_jsonl_path.exists():
            raise FileNotFoundError(f"OREF sample.jsonl not found: {oref_jsonl_path}")
        if not audio_dir.exists():
            raise FileNotFoundError(f"OREF audio directory not found: {audio_dir}")
        
        canonical_name = output_name or meta["config_name"]
        jsonl_path = self.jsonl_dir / f"{canonical_name}.jsonl"
        
        if jsonl_path.exists():
            logger.debug(f"JSONL already exists: {jsonl_path}")
            return jsonl_path
        
        task = meta["task"]
        language = meta["language"]
        samples = []
        
        with open(oref_jsonl_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                
                # Extract transcription text
                annotation = record.get("annotation", [])
                if not annotation:
                    continue
                
                transcription = annotation[0].get("transcription", {})
                text_list = transcription.get("text", [])
                if not text_list:
                    continue
                text = text_list[0].strip()
                
                # Extract audio path and map to local path
                attr = record.get("attribute", {})
                orig_path = attr.get("path", "")
                if not orig_path:
                    continue
                
                filename = Path(orig_path).name
                local_path = str((audio_dir / filename).resolve())
                
                # Use sample_id or filename stem as key
                sample_id = record.get("sample_id", "")
                key = sample_id if sample_id else Path(filename).stem
                
                sample = {
                    "key": key,
                    "path": local_path,
                    "target": text,
                    "task": task,
                    "language": language,
                    "dataset": canonical_name,
                    "sample_rate": attr.get("sample_rate", 16000),
                    "duration_ms": attr.get("duration", 0),
                }
                samples.append(sample)
        
        # Write JSONL
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with open(jsonl_path, 'w', encoding='utf-8') as f:
            for sample in samples:
                f.write(json.dumps(sample, ensure_ascii=False) + '\n')
        
        logger.info(f"Converted OREF {dataset_name}: {len(samples)} samples -> {jsonl_path}")
        return jsonl_path

    def _load_single_json_object(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return {}
        return json.loads(text)

    def _extract_oref_transcription_text(self, record: dict[str, Any]) -> str:
        annotations = record.get("annotation") or []
        for annotation in annotations:
            if not isinstance(annotation, dict):
                continue
            transcription = annotation.get("transcription") or {}
            if not isinstance(transcription, dict):
                continue
            text = transcription.get("text")
            if isinstance(text, list):
                joined = " ".join(str(item).strip() for item in text if str(item).strip())
                if joined:
                    return joined
            if isinstance(text, str) and text.strip():
                return text.strip()
        return ""

    def _resolve_oref_audio_path(self, raw_value: str, raw_dir: Path) -> Path:
        audio_path = Path(raw_value).expanduser()
        if audio_path.is_absolute():
            return audio_path
        candidate = raw_dir / audio_path
        if candidate.exists():
            return candidate
        return raw_dir / audio_path.name

    def _copy_oref_source_files(
        self,
        *,
        package_dir: Path,
        ds_jsonl_path: Path,
        sample_jsonl_path: Path,
        source_payload: dict[str, Any],
    ) -> None:
        oref_dir = package_dir / "oref"
        oref_dir.mkdir(parents=True, exist_ok=True)
        if ds_jsonl_path.exists():
            shutil.copy2(ds_jsonl_path, oref_dir / "ds.jsonl")
        if sample_jsonl_path.exists():
            shutil.copy2(sample_jsonl_path, oref_dir / "sample.jsonl")
        (oref_dir / "source.json").write_text(
            json.dumps(source_payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _convert_oref_platform_to_jsonl(self, dataset_name: str, output_name: str | None = None) -> Path:
        """Project an OREF platform dataset version into SURE-EVAL JSONL."""
        meta = self.oref_local_datasets[dataset_name]
        task = str(meta.get("task") or "ASR").upper()
        if task != "ASR":
            raise NotImplementedError(f"OREF platform projection currently supports ASR only, got: {task}")

        projection_name = self._oref_projection_name(output_name or dataset_name, {**meta, "config_name": output_name or meta.get("config_name") or dataset_name})
        jsonl_path = self.jsonl_dir / f"{projection_name}.jsonl"
        if jsonl_path.exists():
            logger.info("Using existing OREF platform projection", dataset=projection_name, jsonl=str(jsonl_path))
            return jsonl_path

        sample_jsonl_path = self._oref_source_sample_jsonl_path(dataset_name, meta)
        ds_jsonl_path = self._oref_source_ds_jsonl_path(dataset_name, meta)
        raw_dir = self._oref_source_raw_dir(meta)
        if not sample_jsonl_path.exists():
            raise FileNotFoundError(f"OREF platform sample.jsonl not found: {sample_jsonl_path}")
        if not raw_dir.exists():
            raise FileNotFoundError(f"OREF platform raw_dir not found: {raw_dir}")

        ds_meta = self._load_single_json_object(ds_jsonl_path)
        language = str(
            meta.get("language")
            or (((ds_meta.get("audio") or {}).get("speech") or {}).get("language"))
            or "auto"
        )
        dataset_root = self._resolve_config_path(meta["dataset_root"]) if meta.get("dataset_root") else sample_jsonl_path.parents[2]
        version_id = str(meta.get("version_id") or sample_jsonl_path.parent.name)
        oref_dataset_name = str(meta.get("platform_dataset_name") or dataset_root.name)
        package_dir = self._oref_dataset_dir(output_name or dataset_name, {**meta, "config_name": output_name or meta.get("config_name") or dataset_name})
        projection_dir = package_dir / "projections" / "asr_transcription_v1"
        projection_dir.mkdir(parents=True, exist_ok=True)

        rows: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        source_records = 0
        seen_keys: set[str] = set()
        require_audio_exists = bool((meta.get("validation") or {}).get("require_audio_exists", True))
        check_size = bool((meta.get("validation") or {}).get("check_size", True))

        with sample_jsonl_path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                source_records += 1
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    skipped.append({"line": line_no, "reason": f"invalid_json: {exc.msg}"})
                    continue

                attr = record.get("attribute") or {}
                raw_path = str(attr.get("path") or "")
                if not raw_path:
                    skipped.append({"line": line_no, "reason": "missing attribute.path"})
                    continue
                audio_path = self._resolve_oref_audio_path(raw_path, raw_dir)
                if require_audio_exists and not audio_path.exists():
                    skipped.append({"line": line_no, "reason": f"audio_not_found: {audio_path}"})
                    continue
                if check_size and audio_path.exists() and attr.get("size") is not None:
                    actual_size = audio_path.stat().st_size
                    if int(attr["size"]) != actual_size:
                        skipped.append({
                            "line": line_no,
                            "reason": f"audio_size_mismatch: expected {attr['size']} got {actual_size}",
                        })
                        continue

                text = self._extract_oref_transcription_text(record)
                if not text:
                    skipped.append({"line": line_no, "reason": "missing transcription text"})
                    continue

                key = str(record.get("sample_id") or audio_path.stem)
                if key in seen_keys:
                    skipped.append({"line": line_no, "reason": f"duplicate sample_id: {key}"})
                    continue
                seen_keys.add(key)

                rows.append({
                    "key": key,
                    "path": str(audio_path),
                    "target": text,
                    "task": task,
                    "language": language,
                    "dataset": projection_name,
                    "sample_rate": attr.get("sample_rate"),
                    "duration_ms": attr.get("duration", 0),
                    "metadata": {
                        "source": "oref_platform",
                        "oref_dataset": oref_dataset_name,
                        "version_id": version_id,
                        "sample_id": record.get("sample_id"),
                        "parent_sample_id": record.get("parent_sample_id"),
                        "raw_data_md5": attr.get("raw_data_md5"),
                        "raw_data_format": attr.get("raw_data_format"),
                        "size": attr.get("size"),
                        "channels": attr.get("channels"),
                    },
                })

        if skipped:
            reasons = ", ".join(f"line {item['line']}: {item['reason']}" for item in skipped[:5])
            raise ValueError(
                f"OREF platform conversion skipped {len(skipped)} of {source_records} records for {dataset_name}; "
                f"first issues: {reasons}"
            )
        if not rows:
            raise ValueError(f"OREF platform conversion produced no samples for {dataset_name}")

        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with jsonl_path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

        sure_jsonl = projection_dir / "sure.jsonl"
        shutil.copy2(jsonl_path, sure_jsonl)

        source_payload = {
            "source": "oref_platform",
            "oref_dataset": oref_dataset_name,
            "version_id": version_id,
            "dataset_root": str(dataset_root),
            "raw_dir": str(raw_dir),
            "ds_jsonl": str(ds_jsonl_path),
            "sample_jsonl": str(sample_jsonl_path),
            "created_at": _utc_now(),
        }
        self._copy_oref_source_files(
            package_dir=package_dir,
            ds_jsonl_path=ds_jsonl_path,
            sample_jsonl_path=sample_jsonl_path,
            source_payload=source_payload,
        )

        mapping = {
            "projector": "asr_transcription_v1",
            "source_format": "oref_platform_sample_jsonl",
            "target_format": "sure_eval_jsonl_v1",
            "fields": {
                "key": "sample_id",
                "path": "attribute.path",
                "target": "annotation[0].transcription.text[0]",
                "task": f"constant:{task}",
                "language": "config.language | ds.audio.speech.language",
                "sample_rate": "attribute.sample_rate",
                "duration_ms": "attribute.duration",
            },
        }
        try:
            import yaml
            mapping_text = yaml.safe_dump(mapping, allow_unicode=True, sort_keys=False)
        except Exception:
            mapping_text = json.dumps(mapping, indent=2, ensure_ascii=False) + "\n"
        (projection_dir / "mapping.yaml").write_text(mapping_text, encoding="utf-8")

        io_contract = {
            "task": task,
            "input": {"primary_field": "path", "type": "audio_path", "required_fields": ["key", "path"]},
            "output": {"prediction_format": "tsv", "columns": ["key", "prediction_text"], "type": "text"},
            "reference": {"primary_field": "target", "type": "text"},
        }
        (projection_dir / "io_contract.json").write_text(
            json.dumps(io_contract, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        conversion_report = {
            "source": "oref_platform",
            "dataset": projection_name,
            "config_name": meta.get("config_name") or dataset_name,
            "oref_dataset": oref_dataset_name,
            "version_id": version_id,
            "source_sample_jsonl": str(sample_jsonl_path),
            "source_ds_jsonl": str(ds_jsonl_path),
            "output_jsonl": str(jsonl_path),
            "package_sure_jsonl": str(sure_jsonl),
            "task": task,
            "language": language,
            "num_input_records": source_records,
            "num_output_records": len(rows),
            "num_skipped": len(skipped),
            "field_mapping": mapping["fields"],
            "lossiness": "none",
            "validation": {
                "require_audio_exists": require_audio_exists,
                "check_size": check_size,
                "check_md5": False,
            },
            "created_at": _utc_now(),
        }
        (projection_dir / "conversion_report.json").write_text(
            json.dumps(conversion_report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        manifest = {
            "dataset": meta.get("config_name") or dataset_name,
            "default_projection": "asr_transcription_v1",
            "source": "oref_platform",
            "oref_dataset": oref_dataset_name,
            "version_id": version_id,
            "projections": {
                "asr_transcription_v1": {
                    "dataset": projection_name,
                    "sure_jsonl": str(sure_jsonl.relative_to(package_dir)),
                    "mapping": "projections/asr_transcription_v1/mapping.yaml",
                    "io_contract": "projections/asr_transcription_v1/io_contract.json",
                    "conversion_report": "projections/asr_transcription_v1/conversion_report.json",
                }
            },
        }
        (package_dir / "dataset_manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        logger.info(
            "Converted OREF platform dataset",
            dataset=dataset_name,
            projection=projection_name,
            samples=len(rows),
            jsonl=str(jsonl_path),
        )
        return jsonl_path
    
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

    def _canonical_name(self, dataset_name: str) -> str:
        """Resolve any dataset alias to the canonical config key."""
        normalized = self.normalize_dataset_name(dataset_name)
        if normalized != dataset_name:
            return normalized

        dataset_def = self.config.get_dataset(dataset_name)
        if dataset_def:
            return dataset_name

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
        canonical_name = self._canonical_name_from_definition(dataset_def)
        output_path = self.jsonl_dir / f"{canonical_name}.jsonl"
        
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
                            "dataset": canonical_name,
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
        canonical_name = self._canonical_name_from_definition(dataset_def)
        output_path = self.jsonl_dir / f"{canonical_name}.jsonl"
        
        with open(output_path, 'w', encoding='utf-8') as f:
            for item in dataset:
                sample = {
                    "key": item.get("id", ""),
                    "path": item.get("audio", ""),
                    "target": item.get("text", item.get("label", "")),
                    "task": dataset_def.task,
                    "language": dataset_def.language,
                    "dataset": canonical_name,
                }
                f.write(json.dumps(sample, ensure_ascii=False) + '\n')
        
        return output_path

    def _canonical_name_from_definition(self, dataset_def) -> str:
        """Resolve a dataset definition back to its canonical config key."""
        for key, candidate in self.config.datasets.definitions.items():
            if candidate == dataset_def:
                return key
        return dataset_def.name
    
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
        existing_jsonl = self._existing_jsonl_for_dataset(name)
        if existing_jsonl:
            return existing_jsonl.stem

        fallback = self.dataset_fallbacks.get(name)
        if fallback and fallback in self.oref_local_datasets:
            meta = self.oref_local_datasets[fallback]
            if meta.get("source") == "oref_platform":
                projection_name = self._oref_projection_name(fallback, meta)
                if (self.jsonl_dir / f"{projection_name}.jsonl").exists():
                    return projection_name

        # First check if it's already a config name
        if name in self.config.datasets.definitions:
            return name
        
        # Check if it's a CSV name
        if name in CSV_DATASETS:
            config_name = CSV_DATASETS[name].get("config_name")
            if config_name:
                return config_name
            return name
        
        # Check OREF local datasets
        if name in self.oref_local_datasets:
            meta = self.oref_local_datasets[name]
            if meta.get("source") == "oref_platform":
                return self._oref_projection_name(name, meta)
            return meta.get("config_name", name)
        
        # Check reverse mapping (config -> CSV)
        for csv_name, meta in CSV_DATASETS.items():
            if meta.get("config_name") == name:
                return name
        
        # Check reverse mapping (config -> OREF)
        for oref_name, meta in self.oref_local_datasets.items():
            if meta.get("config_name") == name:
                if meta.get("source") == "oref_platform":
                    return self._oref_projection_name(oref_name, meta)
                return name

        if self._oref_platform_meta_for_projection(name):
            return name
        
        # Try lowercase normalization as fallback
        lower_name = name.lower()
        if lower_name in self.config.datasets.definitions:
            return lower_name

        underscore_name = lower_name.replace("-", "_")
        if underscore_name in self.config.datasets.definitions:
            return underscore_name
        
        # Return as-is if no mapping found
        return name

    def _collection_members(self, name: str) -> list[str]:
        collection_key = name.lower().replace("-", "_")
        if collection_key == "seedtts_test_eval":
            return sorted(
                key
                for key in self.config.datasets.definitions
                if key.startswith("seedtts_test_eval_")
            )
        if collection_key == "cv3_eval":
            return sorted(
                key
                for key in self.config.datasets.definitions
                if key.startswith("cv3_eval_")
            )
        return list(DATASET_COLLECTION_ALIASES.get(collection_key, ()))

    def expand_dataset_names(self, dataset_names: list[str] | tuple[str, ...]) -> list[str]:
        """Expand collection aliases into concrete, non-mixed dataset splits."""
        expanded: list[str] = []
        seen: set[str] = set()
        for dataset_name in dataset_names:
            members = self._collection_members(dataset_name)
            if not members:
                canonical_name = self.normalize_dataset_name(dataset_name)
                members = self._collection_members(canonical_name) or [canonical_name]
            for member in members:
                canonical_member = self.normalize_dataset_name(member)
                if canonical_member not in seen:
                    expanded.append(canonical_member)
                    seen.add(canonical_member)
        return expanded
    
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
            for expanded_name in self.expand_dataset_names([name]):
                available.add(expanded_name)
        
        return sorted(available)
    
    def to_inference_input(
        self,
        dataset_name: str,
        output_path: Path | str | None = None,
        max_samples: int | None = None,
    ) -> Path:
        """Convert dataset JSONL to inference-input JSONL for `sure-eval predict`.

        Args:
            dataset_name: Canonical dataset name (e.g. 'librispeech_test_clean')
            output_path: Optional output path; defaults to ``{dataset}.inference_input.jsonl``
                alongside the source JSONL.
            max_samples: Optional limit on number of samples.

        Returns:
            Path to the generated inference-input JSONL.
        """
        canonical = self._canonical_name(dataset_name)
        jsonl_path = self.get_jsonl_path(canonical)

        if not jsonl_path.exists():
            if canonical in self.oref_local_datasets:
                meta = self.oref_local_datasets[canonical]
                if meta.get("source") == "oref_platform":
                    jsonl_path = self._convert_oref_platform_to_jsonl(canonical)
                else:
                    jsonl_path = self._convert_oref_jsonl_to_jsonl(canonical)
            else:
                raise FileNotFoundError(f"Dataset JSONL not found: {jsonl_path}")

        if output_path is None:
            output_path = jsonl_path.with_suffix(".inference_input.jsonl")
        else:
            output_path = Path(output_path)

        output_path.parent.mkdir(parents=True, exist_ok=True)

        count = 0
        with open(jsonl_path, "r", encoding="utf-8") as fin, \
             open(output_path, "w", encoding="utf-8") as fout:
            for line in fin:
                if max_samples is not None and count >= max_samples:
                    break
                sample = json.loads(line.strip())
                record = {
                    "instance_id": sample.get("key", f"sample_{count}"),
                    "task": sample.get("task", "ASR").lower(),
                    "input": {
                        "audio_path": sample.get("path", ""),
                        "sample_rate": sample.get("sample_rate", 16000),
                    },
                    "request": {},
                }
                fout.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1

        logger.info(
            "Generated inference input",
            dataset=canonical,
            samples=count,
            output=str(output_path),
        )
        return output_path

    def get_info(self, dataset_name: str) -> dict[str, Any] | None:
        """Get dataset information."""
        canonical_name = self._canonical_name(dataset_name)
        projection_meta = self._oref_platform_meta_for_projection(canonical_name)
        if projection_meta:
            _oref_name, meta = projection_meta
            jsonl_path = self.jsonl_dir / f"{canonical_name}.jsonl"
            num_samples = self._count_jsonl_rows(jsonl_path)
            return {
                "name": canonical_name,
                "display_name": canonical_name,
                "config_name": canonical_name,
                "task": meta["task"],
                "language": meta["language"],
                "source": "oref_platform",
                "jsonl_path": str(jsonl_path),
                "num_samples": num_samples,
                "is_available": self.is_available(canonical_name),
            }

        csv_name = self._config_to_csv_name(canonical_name)
        
        if csv_name in CSV_DATASETS:
            meta = CSV_DATASETS[csv_name]
            dataset_def = self.config.get_dataset(canonical_name)
            return {
                "name": canonical_name,
                "display_name": dataset_def.name if dataset_def else canonical_name,
                "csv_name": csv_name,
                "config_name": canonical_name,
                "task": meta["task"],
                "language": meta["language"],
                "source": "sure_benchmark",
                "is_available": self.is_available(canonical_name),
            }

        existing_jsonl = self._existing_jsonl_for_dataset(canonical_name)
        if existing_jsonl:
            first_sample: dict[str, Any] = {}
            with open(existing_jsonl, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        first_sample = json.loads(line)
                        break
            sample_meta = first_sample.get("metadata") if isinstance(first_sample.get("metadata"), dict) else {}
            return {
                "name": existing_jsonl.stem,
                "display_name": existing_jsonl.stem,
                "config_name": existing_jsonl.stem,
                "task": first_sample.get("task"),
                "language": first_sample.get("language"),
                "source": sample_meta.get("source") or first_sample.get("source") or "local_jsonl",
                "jsonl_path": str(existing_jsonl),
                "num_samples": self._count_jsonl_rows(existing_jsonl),
                "is_available": True,
            }
        
        # Check OREF local datasets
        if canonical_name in self.oref_local_datasets:
            meta = self.oref_local_datasets[canonical_name]
            return {
                "name": canonical_name,
                "display_name": canonical_name,
                "config_name": canonical_name,
                "task": meta["task"],
                "language": meta["language"],
                "source": "oref_local",
                "is_available": self.is_available(canonical_name),
            }
        
        # Check config
        dataset_def = self.config.get_dataset(canonical_name)
        if dataset_def:
            return {
                "name": canonical_name,
                "display_name": dataset_def.name,
                "task": dataset_def.task,
                "language": dataset_def.language,
                "source": dataset_def.source,
                "is_available": self.is_available(canonical_name),
            }
        
        return None
