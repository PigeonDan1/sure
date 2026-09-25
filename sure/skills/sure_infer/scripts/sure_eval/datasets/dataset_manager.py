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
import math
import shutil
import subprocess
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dataset_alias import resolve_dataset_alias
from sure_eval.core.config import Config
from sure_eval.core.logging import get_logger
from .se_projection import project_se_samples
from .source_resolver import (
    DatasetSourceRef,
    is_source_entry,
    read_source_metadata,
    read_source_task,
    resolve_site_source_entry,
    source_default_task,
)

logger = get_logger(__name__)


# ``source`` marker written into every artifact produced from a dataset-pool
# source root. Records written before the marker was renamed carry the legacy
# spelling, so reads normalize it back to the current value.
SITE_DATASET_POOL_SOURCE = "site_dataset_pool"
LEGACY_SITE_DATASET_POOL_SOURCE = "aispeech_ds_pool"


def _normalized_source(source: Any) -> Any:
    """Report the current marker for records written before the rename."""
    if source == LEGACY_SITE_DATASET_POOL_SOURCE:
        return SITE_DATASET_POOL_SOURCE
    return source


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
    
    def __init__(self, config: Config | None = None, dataset_source_key: str = "default") -> None:
        self.config = config or Config.from_env()
        self.dataset_source_key = dataset_source_key
        self.data_dir = Path(self.config.data.datasets)
        self.sure_dir = self.data_dir / "sure_benchmark"
        self.jsonl_dir = self.sure_dir / "jsonl"
        
        # Ensure directories exist
        self.jsonl_dir.mkdir(parents=True, exist_ok=True)

    def _count_jsonl_rows(self, path: Path) -> int | None:
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())

    def _task_slug(self, task: str | None) -> str:
        return str(task or "unknown").strip().lower().replace("-", "_")

    def source_projection_name(self, dataset_id: str, task: str | None) -> str:
        """JSONL stem for a ds_pool source-root projection, cached per task."""
        return f"{dataset_id}__{self._task_slug(task)}"

    def get_jsonl_path(self, dataset_name: str) -> Path:
        """Get the JSONL file path for a dataset.

        Prefer an on-disk per-task projection when one uniquely matches. A bare
        ``source__version.jsonl`` left from pre-task naming is only used when no
        ``source__version__{task}.jsonl`` siblings exist.
        """
        canonical_name = self._canonical_name(dataset_name)
        existing = self._existing_jsonl_for_dataset(canonical_name)
        if existing:
            return existing
        return self.jsonl_dir / f"{canonical_name}.jsonl"

    def _existing_jsonl_for_dataset(self, dataset_name: str) -> Path | None:
        """Return an existing canonical JSONL path for aliases such as ``aishell1``.

        Prefer a unique per-task projection (``name__*.jsonl``) over a legacy bare
        ``name.jsonl`` so ASR/TTS/VAD files win. Ambiguous multi-task projections
        return None so prepare/download can take an explicit task instead of guessing.
        """
        name = str(dataset_name or "").strip()
        if not name:
            return None
        exact = self.jsonl_dir / f"{name}.jsonl"
        projections = sorted(path.stem for path in self.jsonl_dir.glob(f"{name}__*.jsonl"))
        if projections:
            resolved = resolve_dataset_alias(name, projections)
            return self.jsonl_dir / f"{resolved}.jsonl" if resolved else None
        if exact.exists():
            return exact
        return None

    def is_available(self, dataset_name: str) -> bool:
        """Check if dataset JSONL is available."""
        return self.get_jsonl_path(dataset_name).exists()

    def download_and_convert(self, dataset_name: str, task: str | None = None) -> Path:
        """
        Download SURE Benchmark dataset and convert to JSONL.

        Args:
            dataset_name: Dataset name (config name or CSV name)
            task: Optional projection task override for source-root entries
                (e.g. ``"TTS"``). When omitted the dataset's declared
                supported_tasks pick the projection task (legacy ASR default).

        Returns:
            Path to JSONL file
        """
        if is_source_entry(dataset_name):
            ref = resolve_site_source_entry(
                dataset_name, dataset_source_key=self.dataset_source_key
            )
            resolved_task = source_default_task(ref, task or "")
            return self._convert_source_root_to_jsonl(ref, resolved_task)

        canonical = self._canonical_name(dataset_name)
        existing_jsonl = self._existing_jsonl_for_dataset(canonical)
        if existing_jsonl:
            logger.info(
                "Using existing dataset JSONL",
                dataset=canonical,
                resolved_dataset=existing_jsonl.stem,
                jsonl=str(existing_jsonl),
            )
            return existing_jsonl

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

    def _extract_oref_language_label(self, record: dict[str, Any]) -> str:
        """Read a spoken-language label from common OREF annotation shapes."""
        candidates: list[Any] = [
            record.get("language"),
            record.get("lang"),
            record.get("label"),
            record.get("expected_language"),
            record.get("ground_truth"),
        ]
        annotations = record.get("annotation") or []
        if isinstance(annotations, list):
            for annotation in annotations:
                if not isinstance(annotation, dict):
                    continue
                candidates.extend(
                    annotation.get(field)
                    for field in ("language", "lang", "label", "expected_language", "ground_truth")
                )
                classification = annotation.get("classification")
                if isinstance(classification, dict):
                    candidates.extend(
                        classification.get(field)
                        for field in ("language", "lang", "label", "value")
                    )
        for value in candidates:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _extract_oref_translation_text(self, record: dict[str, Any]) -> str:
        annotations = record.get("annotation") or []
        for annotation in annotations:
            if not isinstance(annotation, dict):
                continue
            translation = annotation.get("translation") or {}
            if not isinstance(translation, dict):
                continue
            text = translation.get("text")
            if isinstance(text, list):
                joined = " ".join(str(item).strip() for item in text if str(item).strip())
                if joined:
                    return joined
            if isinstance(text, str) and text.strip():
                return text.strip()
        return ""

    def _extract_oref_speech_segments(
        self, record: dict[str, Any]
    ) -> tuple[list[dict[str, float]], str | None]:
        annotations = record.get("annotation")
        if not isinstance(annotations, list):
            return [], "missing annotation list"

        segments: list[dict[str, float]] = []
        for annotation in annotations:
            if not isinstance(annotation, dict):
                continue
            timestamp = annotation.get("timestamp")
            if not isinstance(timestamp, dict):
                continue
            begin = timestamp.get("begin_time")
            end = timestamp.get("end_time")
            if begin is None or end is None:
                continue
            try:
                start = float(begin)
                finish = float(end)
            except (TypeError, ValueError):
                return [], "invalid VAD timestamp"
            if not math.isfinite(start) or not math.isfinite(finish) or finish <= start:
                return [], "invalid VAD timestamp interval"
            segments.append({"start": start, "end": finish})

        return segments, None

    def _resolve_oref_audio_path(self, raw_value: str, raw_dir: Path) -> Path:
        audio_path = Path(raw_value).expanduser()
        if audio_path.is_absolute():
            return audio_path
        candidate = raw_dir / audio_path
        if candidate.exists():
            return candidate
        return raw_dir / audio_path.name

    def _project_sample_rows(
        self,
        *,
        sample_jsonl_path: Path,
        raw_dir: Path,
        task: str,
        language: str,
        dataset_label: str,
        metadata_base: dict[str, Any],
        require_audio_exists: bool = True,
        check_size: bool = True,
        collect_translation: bool = False,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        rows: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        source_records = 0
        seen_keys: set[str] = set()

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

                speech_segments: list[dict[str, float]] | None = None
                if task == "VAD":
                    speech_segments, segment_error = self._extract_oref_speech_segments(record)
                    if segment_error:
                        skipped.append({"line": line_no, "reason": segment_error})
                        continue
                elif collect_translation:
                    text = self._extract_oref_translation_text(record)
                    if not text:
                        skipped.append({"line": line_no, "reason": "missing translation text"})
                        continue
                    source_text = self._extract_oref_transcription_text(record)
                    if not source_text:
                        skipped.append({"line": line_no, "reason": "missing transcription text"})
                        continue
                else:
                    text = (
                        self._extract_oref_language_label(record)
                        if task == "LID"
                        else self._extract_oref_transcription_text(record)
                    )
                    if not text:
                        skipped.append({"line": line_no, "reason": "missing transcription text"})
                        continue

                key = str(record.get("sample_id") or audio_path.stem)
                if key in seen_keys:
                    skipped.append({"line": line_no, "reason": f"duplicate sample_id: {key}"})
                    continue
                seen_keys.add(key)

                row: dict[str, Any] = {
                    "key": key,
                    "path": str(audio_path),
                    "task": task,
                    "language": language,
                    "dataset": dataset_label,
                    "sample_rate": attr.get("sample_rate"),
                    "duration_ms": attr.get("duration", 0),
                    "metadata": {
                        **metadata_base,
                        "sample_id": record.get("sample_id"),
                        "parent_sample_id": record.get("parent_sample_id"),
                        "raw_data_md5": attr.get("raw_data_md5"),
                        "raw_data_format": attr.get("raw_data_format"),
                        "size": attr.get("size"),
                        "channels": attr.get("channels"),
                    },
                }
                if task == "VAD":
                    duration_ms = attr.get("duration", 0)
                    try:
                        duration = float(duration_ms) / 1000.0
                    except (TypeError, ValueError):
                        skipped.append({"line": line_no, "reason": "invalid audio duration"})
                        continue
                    if not math.isfinite(duration) or duration <= 0:
                        skipped.append({"line": line_no, "reason": "invalid audio duration"})
                        continue
                    row["duration"] = duration
                    row["speech_segments"] = speech_segments or []
                elif task == "LID":
                    row["label"] = text
                    row["target"] = text
                else:
                    row["target"] = text
                if collect_translation:
                    # S2TT reference rows keep the source-language transcription
                    # so triangle metrics (xcomet_xl) can build their src file;
                    # an empty one would be scored as a source, hence the skip above.
                    row["source"] = source_text
                rows.append(row)
        return rows, skipped, source_records

    def _copy_source_files(
        self,
        *,
        package_dir: Path,
        ds_jsonl_path: Path,
        sample_jsonl_path: Path,
        source_payload: dict[str, Any],
    ) -> None:
        source_dir = package_dir / "source"
        source_dir.mkdir(parents=True, exist_ok=True)
        if ds_jsonl_path.exists():
            shutil.copy2(ds_jsonl_path, source_dir / "ds.jsonl")
        if sample_jsonl_path.exists():
            shutil.copy2(sample_jsonl_path, source_dir / "sample.jsonl")
        (source_dir / "source.json").write_text(
            json.dumps(source_payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _source_projection_id(self, task: str) -> str:
        """Package projection dir id for a source-root task projection."""
        task = str(task).upper()
        if task == "ASR":
            return "asr_transcription_v1"
        if task == "VAD":
            return "vad_segments_v1"
        if task == "KWS":
            return "kws_wakeword_v1"
        if task == "LID":
            return "lid_labels_v1"
        if task == "S2TT":
            return "s2tt_translation_v1"
        if task == "SV":
            return "sv_embeddings_v1"
        if task == "SE":
            return "se_audio_v1"
        return f"{self._task_slug(task)}_readback_v1"

    def _convert_source_root_to_jsonl(self, ref: DatasetSourceRef, task: str | None = None) -> Path:
        """Project a site dataset-pool source root into SURE-EVAL JSONL.

        Projections are cached per task as ``<dataset_id>__<task_slug>.jsonl``
        so one multi-task source root (e.g. fleurs declaring ASR+TTS) keeps an
        independent file for every task.

        A missing ``task`` is discovered from the source itself rather than
        assumed ASR, so a KWS/LID/VAD/S2TT source projected without an explicit
        task still reaches its own projector.
        """
        task = str(task or source_default_task(ref)).strip().upper()
        projection_name = self.source_projection_name(ref.dataset_id, task)
        jsonl_path = self.jsonl_dir / f"{projection_name}.jsonl"
        if jsonl_path.exists():
            logger.info(
                "Using existing source-root projection",
                dataset=projection_name,
                jsonl=str(jsonl_path),
            )
            return jsonl_path

        sample_jsonl_path = Path(ref.sample_jsonl)
        ds_jsonl_path = Path(ref.ds_jsonl)
        raw_dir = Path(ref.raw_dir)
        if not sample_jsonl_path.exists():
            raise FileNotFoundError(f"source sample.jsonl not found: {sample_jsonl_path}")
        if not raw_dir.exists():
            raise FileNotFoundError(f"source raw_dir not found: {raw_dir}")
        # Native projectors (ASR/KWS/LID/VAD/S2TT/SV/SE). Everything else is a
        # readback projection of ASR-shaped rows (text as target, audio as path).
        native_tasks = {"ASR", "KWS", "LID", "VAD", "S2TT", "SV", "SE"}
        source_meta = read_source_metadata(ref)
        # Keep S2TT discovery when caller passed ASR default but ds declares S2TT
        # and no multi-task intent is in play — only when task is still ASR and
        # the source itself is S2TT-shaped without supported_tasks multi-tag.
        if task == "ASR" and not ref.supported_tasks and source_meta.get("task") == "S2TT":
            task = "S2TT"
            projection_name = self.source_projection_name(ref.dataset_id, task)
            jsonl_path = self.jsonl_dir / f"{projection_name}.jsonl"
            if jsonl_path.exists():
                return jsonl_path

        if task not in native_tasks and task not in {"TTS", "VC"}:
            # Allow any other synth-style readback; unknown non-synth still fails.
            # ponytail: open-ended readback for TTS/VC only; expand if more synth tasks land.
            raise ValueError(
                f"source-root projection for task {task!r} is not implemented; "
                f"supported tasks: ASR, KWS, LID, VAD, S2TT, SV, SE, TTS, VC"
            )

        ds_meta = self._load_single_json_object(ds_jsonl_path)
        language = str(
            (((ds_meta.get("audio") or {}).get("speech") or {}).get("language")) or "auto"
        )
        translation_language = source_meta.get("translation_language") or ""
        if task == "KWS" and language == "auto":
            language = "any"
        package_dir = self.sure_dir / ref.source_dataset_name
        projection_id = self._source_projection_id(task)
        projection_dir = package_dir / "projections" / projection_id
        projection_dir.mkdir(parents=True, exist_ok=True)

        metadata_base = {
            "source": SITE_DATASET_POOL_SOURCE,
            "source_dataset_root": ref.source_root,
            "source_dataset_name": ref.source_dataset_name,
            "version_id": ref.version_id,
        }
        # Readback (TTS/VC) reuses ASR-shaped row projection with task stamped.
        row_task = "ASR" if task in {"TTS", "VC"} else task
        if task == "SE":
            rows, skipped, source_records = project_se_samples(
                sample_jsonl_path=sample_jsonl_path,
                raw_dir=raw_dir,
                language=language,
                dataset_label=projection_name,
                metadata_base=metadata_base,
            )
        elif task == "KWS":
            rows, skipped, source_records = self._project_kws_sample_rows(
                sample_jsonl_path=sample_jsonl_path,
                raw_dir=raw_dir,
                language=language,
                dataset_label=projection_name,
                metadata_base=metadata_base,
            )
        elif task == "SV":
            trial_manifest = self._resolve_sv_trial_manifest(ref)
            rows, skipped, source_records = self._project_sv_sample_rows(
                sample_jsonl_path=sample_jsonl_path,
                raw_dir=raw_dir,
                trial_manifest=trial_manifest,
                language=language,
                dataset_label=projection_name,
                metadata_base=metadata_base,
            )
        else:
            rows, skipped, source_records = self._project_sample_rows(
                sample_jsonl_path=sample_jsonl_path,
                raw_dir=raw_dir,
                task=row_task,
                language=language,
                dataset_label=projection_name,
                metadata_base=metadata_base,
                collect_translation=(task == "S2TT"),
            )
            if task in {"TTS", "VC"}:
                for row in rows:
                    row["task"] = task
                    row["dataset"] = projection_name
        if skipped:
            reasons = ", ".join(f"line {item['line']}: {item['reason']}" for item in skipped[:5])
            raise ValueError(
                f"source-root conversion skipped {len(skipped)} of {source_records} records "
                f"for {ref.source_root}; first issues: {reasons}"
            )
        if not rows:
            raise ValueError(f"source-root conversion produced no samples for {ref.source_root}")
        if task == "KWS" and {row["expected_detected"] for row in rows} != {False, True}:
            raise ValueError("KWS evaluation requires at least one positive and one negative sample")
        if task == "SV":
            trial_gaps = self._sv_trial_key_gaps(trial_manifest, {str(row["key"]) for row in rows})
            if trial_gaps:
                raise ValueError(
                    "SV trial manifest references unknown sample keys: "
                    + ", ".join(sorted(trial_gaps)[:5])
                )

        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with jsonl_path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

        sure_jsonl = projection_dir / "sure.jsonl"
        shutil.copy2(jsonl_path, sure_jsonl)

        source_payload = {
            "source": SITE_DATASET_POOL_SOURCE,
            "task": task,
            "projector": projection_id,
            "source_dataset_name": ref.source_dataset_name,
            "version_id": ref.version_id,
            "dataset_root": ref.source_root,
            "raw_dir": ref.raw_dir,
            "ds_jsonl": ref.ds_jsonl,
            "sample_jsonl": ref.sample_jsonl,
            "created_at": _utc_now(),
        }
        self._copy_source_files(
            package_dir=package_dir,
            ds_jsonl_path=ds_jsonl_path,
            sample_jsonl_path=sample_jsonl_path,
            source_payload=source_payload,
        )

        if task == "SE":
            fields = {
                "key": "key|sample_id|noisy_audio stem",
                "path": "noisy_audio|attribute.path|audio|path",
                "noisy_audio": "noisy_audio|attribute.path|audio|path",
                "reference_audio": "reference_audio (optional for no-reference metrics)",
                "task": "constant:SE",
                "language": "row.language|ds.audio.speech.language",
            }
        elif task == "KWS":
            fields = {
                "key": "key|sample_id",
                "path": "attribute.path|path|audio|wav",
                "keywords": "keywords|keyword",
                "expected_detected": "expected_detected|expected|label|keyword_id",
                "expected_keyword": "expected_keyword|unambiguous keyword match",
                "duration": "duration|duration_ms|attribute.duration|wav header",
                "task": "constant:KWS",
                "language": "row.language|ds.audio.speech.language|any",
            }
        elif task == "SV":
            fields = {
                "key": "key|sample_id",
                "path": "audio|path|attribute.path|wav",
                "task": "constant:SV",
                "language": "row.language|ds.audio.speech.language",
                "speaker_id": "record.speaker_id",
                "trial_manifest": "record.trial_manifest|trial_manifest.json",
            }
        else:
            fields = {
                "key": "sample_id",
                "path": "attribute.path",
                "task": f"constant:{task}",
                "language": "ds.audio.speech.language",
                "sample_rate": "attribute.sample_rate",
            }
            if task == "VAD":
                fields.update(
                    {
                        "duration": "attribute.duration / 1000",
                        "speech_segments": "annotation[].timestamp.{begin_time,end_time}",
                    }
                )
            elif task == "LID":
                fields.update(
                    {
                        "label": "record.language|record.lang|record.label|annotation[].language|annotation[].label",
                        "duration_ms": "attribute.duration",
                    }
                )
            elif task == "S2TT":
                fields.update(
                    {
                        "target": "annotation[0].translation.text[0]",
                        "source": "annotation[0].transcription.text[0]",
                        "translation_language": "ds.audio.speech.translation_language",
                        "duration_ms": "attribute.duration",
                    }
                )
            else:
                # ASR and TTS/VC readback share transcription-as-target fields.
                fields.update(
                    {
                        "target": "annotation[0].transcription.text[0]",
                        "duration_ms": "attribute.duration",
                    }
                )
        mapping = {
            "projector": projection_id,
            "source_format": f"{SITE_DATASET_POOL_SOURCE}_sample_jsonl",
            "target_format": "sure_eval_jsonl_v1",
            "fields": fields,
        }
        try:
            import yaml

            mapping_text = yaml.safe_dump(mapping, allow_unicode=True, sort_keys=False)
        except Exception:
            mapping_text = json.dumps(mapping, indent=2, ensure_ascii=False) + "\n"
        (projection_dir / "mapping.yaml").write_text(mapping_text, encoding="utf-8")

        if task == "SE":
            io_contract = {
                "task": "SE",
                "input": {"primary_field": "path", "type": "audio_path", "required_fields": ["key", "path"]},
                "output": {"prediction_format": "jsonl+tsv_projection", "primary_field": "audio_path", "type": "audio_path"},
                "reference": {"primary_field": "reference_audio", "type": "audio_path", "required_for": ["si_sdr", "stoi", "pesq"]},
            }
        elif task == "KWS":
            io_contract = {
                "task": "KWS",
                "input": {
                    "primary_field": "path",
                    "type": "audio_path_with_keywords",
                    "required_fields": ["key", "path", "keywords"],
                },
                "output": {
                    "prediction_format": "jsonl+tsv_projection",
                    "required_fields": ["detected", "keyword", "score"],
                    "type": "keyword_detection",
                },
                "reference": {
                    "required_fields": ["expected_detected", "expected_keyword", "duration"],
                    "type": "keyword_detection",
                },
            }
        elif task == "LID":
            io_contract = {
                "task": "LID",
                "input": {"primary_field": "path", "type": "audio_path", "required_fields": ["key", "path"]},
                "output": {"prediction_format": "tsv", "columns": ["key", "label"], "type": "language_label"},
                "reference": {"primary_field": "label", "type": "language_label"},
            }
        elif task == "SV":
            io_contract = {
                "task": "SV",
                "input": {
                    "primary_field": "path",
                    "type": "audio_path",
                    "required_fields": ["key", "path", "speaker_id"],
                },
                "output": {
                    "prediction_format": "jsonl+tsv_projection",
                    "columns": ["key", "embedding"],
                    "type": "speaker_embedding",
                },
                "reference": {
                    "required_fields": ["speaker_id"],
                    "type": "speaker_verification",
                    "trial_manifest": "trial_manifest",
                },
            }
        elif task == "ASR":
            io_contract = {
                "task": task,
                "input": {"primary_field": "path", "type": "audio_path", "required_fields": ["key", "path"]},
                "output": {"prediction_format": "tsv", "columns": ["key", "prediction_text"], "type": "text"},
                "reference": {"primary_field": "target", "type": "text"},
            }
        elif task == "VAD":
            io_contract = {
                "task": task,
                "input": {"primary_field": "path", "type": "audio_path", "required_fields": ["key", "path"]},
                "output": {"prediction_format": "jsonl", "columns": ["key", "speech_segments"], "type": "json"},
                "reference": {"primary_field": "speech_segments", "type": "segments"},
            }
        elif task == "S2TT":
            io_contract = {
                "task": task,
                "input": {"primary_field": "path", "type": "audio_path", "required_fields": ["key", "path"]},
                "output": {"prediction_format": "tsv", "columns": ["key", "prediction_text"], "type": "text"},
                "reference": {
                    "primary_field": "target",
                    "type": "text",
                    "optional_source_field": "source",
                },
            }
        else:
            # TTS/VC readback: audio path is reference/prompt, target is text to synth.
            io_contract = {
                "task": task,
                "projection_kind": "readback",
                "input": {
                    "primary_field": "path",
                    "type": "audio_path",
                    "role": "reference_audio",
                    "required_fields": ["key", "path", "target"],
                },
                "output": {"prediction_format": "tsv", "columns": ["key", "prediction_audio"], "type": "audio_path"},
                "reference": {"primary_field": "target", "type": "text"},
            }
        (projection_dir / "io_contract.json").write_text(
            json.dumps(io_contract, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        conversion_report = {
            "source": SITE_DATASET_POOL_SOURCE,
            "dataset": projection_name,
            "source_dataset_name": ref.source_dataset_name,
            "source_dataset_root": ref.source_root,
            "version_id": ref.version_id,
            "source_sample_jsonl": ref.sample_jsonl,
            "source_ds_jsonl": ref.ds_jsonl,
            "output_jsonl": str(jsonl_path),
            "package_sure_jsonl": str(sure_jsonl),
            "task": task,
            "language": language,
            "translation_language": translation_language,
            "num_input_records": source_records,
            "num_output_records": len(rows),
            "num_skipped": len(skipped),
            "field_mapping": mapping["fields"],
            "lossiness": "none",
            "validation": {
                "require_audio_exists": True,
                "check_size": True,
                "check_md5": False,
            },
            "created_at": _utc_now(),
        }
        (projection_dir / "conversion_report.json").write_text(
            json.dumps(conversion_report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # Merge multi-task projections into the package manifest.
        manifest_path = package_dir / "dataset_manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                manifest = {}
        else:
            manifest = {}
        manifest.setdefault("dataset", ref.source_dataset_name)
        manifest["source"] = SITE_DATASET_POOL_SOURCE
        manifest["source_dataset_root"] = ref.source_root
        manifest["version_id"] = ref.version_id
        projections = manifest.setdefault("projections", {})
        projections[projection_id] = {
            "dataset": projection_name,
            "sure_jsonl": sure_jsonl.relative_to(package_dir).as_posix(),
            "mapping": f"projections/{projection_id}/mapping.yaml",
            "io_contract": f"projections/{projection_id}/io_contract.json",
            "conversion_report": f"projections/{projection_id}/conversion_report.json",
        }
        # Pin a stable default: ASR wins when present, else first written.
        # A pre-existing default that's still in projections is kept ONLY when
        # it's already the ASR projection — otherwise a TTS-first prepare would
        # silently stick the package's default at TTS even after ASR lands.
        if "asr_transcription_v1" in projections:
            manifest["default_projection"] = "asr_transcription_v1"
        elif not manifest.get("default_projection") or manifest["default_projection"] not in projections:
            manifest["default_projection"] = projection_id
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        logger.info(
            "Converted site dataset source root",
            dataset=projection_name,
            source=ref.source_root,
            task=task,
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
        if is_source_entry(name):
            return resolve_site_source_entry(name, dataset_source_key=self.dataset_source_key).dataset_id

        existing_jsonl = self._existing_jsonl_for_dataset(name)
        if existing_jsonl:
            return existing_jsonl.stem

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
    
    def get_info(self, dataset_name: str) -> dict[str, Any] | None:
        """Get dataset information."""
        canonical_name = self._canonical_name(dataset_name)
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
            info = {
                "name": existing_jsonl.stem,
                "display_name": existing_jsonl.stem,
                "config_name": existing_jsonl.stem,
                "task": first_sample.get("task"),
                "language": first_sample.get("language"),
                "source": _normalized_source(
                    sample_meta.get("source") or first_sample.get("source") or "local_jsonl"
                ),
                "jsonl_path": str(existing_jsonl),
                "num_samples": self._count_jsonl_rows(existing_jsonl),
                "is_available": True,
            }
            if sample_meta.get("source_dataset_name"):
                info["source_dataset_name"] = sample_meta["source_dataset_name"]
            if sample_meta.get("version_id"):
                info["version_id"] = sample_meta["version_id"]
            if sample_meta.get("source_dataset_root"):
                info["source_root"] = sample_meta["source_dataset_root"]
            return info

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
    @staticmethod
    def _kws_keywords(value: Any) -> list[str]:
        if isinstance(value, str):
            items = value.split(",")
        elif isinstance(value, (list, tuple)):
            items = value
        else:
            items = []
        keywords: list[str] = []
        for item in items:
            keyword = str(item).strip()
            if keyword and keyword not in keywords:
                keywords.append(keyword)
        return keywords

    @staticmethod
    def _kws_expected(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in {0, 1}:
            return bool(value)
        normalized = str(value or "").strip().lower()
        if normalized in {"detect", "detected", "positive", "true", "1", "yes"}:
            return True
        if normalized in {"reject", "rejected", "negative", "false", "0", "no"}:
            return False
        raise ValueError("expected/label must explicitly identify a positive or negative KWS sample")

    @staticmethod
    def _wav_duration(path: Path) -> float | None:
        try:
            with wave.open(str(path), "rb") as handle:
                rate = handle.getframerate()
                return handle.getnframes() / rate if rate > 0 else None
        except (OSError, EOFError, wave.Error):
            return None

    def _project_kws_sample_rows(
        self,
        *,
        sample_jsonl_path: Path,
        raw_dir: Path,
        language: str,
        dataset_label: str,
        metadata_base: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        rows: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        source_records = 0
        seen_keys: set[str] = set()

        with sample_jsonl_path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                source_records += 1
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError("row is not an object")
                    attr = record.get("attribute") if isinstance(record.get("attribute"), dict) else {}
                    raw_path = attr.get("path") or record.get("path") or record.get("audio") or record.get("wav")
                    if not raw_path:
                        raise ValueError("missing audio path (attribute.path, path, audio, or wav)")
                    audio_path = self._resolve_oref_audio_path(str(raw_path), raw_dir)
                    if not audio_path.is_file():
                        raise ValueError(f"audio_not_found: {audio_path}")
                    if attr.get("size") is not None and int(attr["size"]) != audio_path.stat().st_size:
                        raise ValueError(
                            f"audio_size_mismatch: expected {attr['size']} got {audio_path.stat().st_size}"
                        )

                    key = str(record.get("key") or record.get("sample_id") or audio_path.stem).strip()
                    if not key:
                        raise ValueError("missing key/sample_id")
                    if key in seen_keys:
                        raise ValueError(f"duplicate sample key: {key}")

                    keywords = self._kws_keywords(record.get("keywords") or record.get("keyword"))
                    if not keywords:
                        raise ValueError("missing non-empty keywords")
                    expected_value = record.get("expected_detected")
                    if expected_value is None:
                        expected_value = record.get("expected", record.get("label"))
                    if expected_value is None and record.get("keyword_id") is not None:
                        expected_value = int(record["keyword_id"]) >= 0
                    expected_detected = self._kws_expected(expected_value)

                    expected_keyword = record.get("expected_keyword")
                    text = str(
                        record.get("text")
                        or record.get("target")
                        or record.get("txt")
                        or self._extract_oref_transcription_text(record)
                        or ""
                    ).strip()
                    if expected_detected and expected_keyword is None:
                        compact_text = "".join(text.upper().split())
                        matches = [keyword for keyword in keywords if "".join(keyword.upper().split()) in compact_text]
                        if len(matches) == 1:
                            expected_keyword = matches[0]
                        elif len(keywords) == 1:
                            expected_keyword = keywords[0]
                    if expected_detected and not str(expected_keyword or "").strip():
                        raise ValueError("positive KWS sample is missing an unambiguous expected_keyword")
                    if expected_keyword is not None:
                        normalized_expected = "".join(str(expected_keyword).upper().split())
                        normalized_keywords = {"".join(keyword.upper().split()) for keyword in keywords}
                        if normalized_expected not in normalized_keywords:
                            raise ValueError("expected_keyword is not present in keywords")

                    duration_value = record.get("duration")
                    if duration_value is None and record.get("duration_ms") is not None:
                        duration_value = float(record["duration_ms"]) / 1000.0
                    if duration_value is None and attr.get("duration") is not None:
                        duration_value = float(attr["duration"]) / 1000.0
                    duration = float(duration_value) if duration_value is not None else self._wav_duration(audio_path)
                    if duration is None or not math.isfinite(duration) or duration <= 0:
                        raise ValueError("missing positive audio duration required for false_alarm_per_hour")

                    threshold = record.get("threshold")
                    if threshold is not None:
                        threshold = float(threshold)
                        if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
                            raise ValueError("threshold must be a finite number in [0, 1]")
                except (TypeError, ValueError) as exc:
                    skipped.append({"line": line_no, "reason": str(exc)})
                    continue

                seen_keys.add(key)
                row = {
                    "key": key,
                    "path": str(audio_path),
                    "audio": str(audio_path),
                    "target": "detect" if expected_detected else "reject",
                    "task": "KWS",
                    "language": str(record.get("language") or language or "any"),
                    "dataset": dataset_label,
                    "keywords": keywords,
                    "expected": "detect" if expected_detected else "reject",
                    "expected_detected": expected_detected,
                    "expected_keyword": str(expected_keyword) if expected_detected else None,
                    "duration": duration,
                    "duration_ms": round(duration * 1000.0, 3),
                    "sample_rate": attr.get("sample_rate") or record.get("sample_rate"),
                    "metadata": {
                        **metadata_base,
                        "sample_id": record.get("sample_id") or key,
                        "parent_sample_id": record.get("parent_sample_id"),
                        "raw_data_md5": attr.get("raw_data_md5"),
                        "raw_data_format": attr.get("raw_data_format"),
                        "size": attr.get("size") or audio_path.stat().st_size,
                        "channels": attr.get("channels"),
                    },
                }
                if threshold is not None:
                    row["threshold"] = threshold
                rows.append(row)
        return rows, skipped, source_records

    def _resolve_sv_trial_manifest(self, ref: Any) -> Path:
        """Locate and validate the trial manifest backing an SV source root.

        The manifest lives next to the version's sample.jsonl or at the source
        root; either layout is accepted. The manifest must parse as a JSON
        object naming a trials file (``trials_file``) that exists beside it.
        """
        candidates = [
            Path(ref.sample_jsonl).parent / "trial_manifest.json",
            Path(ref.raw_dir) / "trial_manifest.json",
            Path(ref.source_root) / "trial_manifest.json",
        ]
        manifest = next((candidate for candidate in candidates if candidate.is_file()), None)
        if manifest is None:
            searched = ", ".join(str(candidate) for candidate in candidates)
            raise FileNotFoundError(
                f"SV source root {ref.source_root} has no trial_manifest.json; searched: {searched}"
            )
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"SV trial manifest is not valid JSON: {manifest}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"SV trial manifest must be a JSON object: {manifest}")
        trials_name = str(payload.get("trials_file") or "").strip()
        if not trials_name:
            raise ValueError(f"SV trial manifest is missing trials_file: {manifest}")
        trials_path = Path(trials_name)
        if not trials_path.is_absolute():
            trials_path = manifest.parent / trials_path
        if not trials_path.is_file():
            raise FileNotFoundError(f"SV trials file not found: {trials_path}")
        return manifest

    @staticmethod
    def _sv_trial_keys(trials_path: Path) -> set[str]:
        """Collect enroll/test keys from a whitespace-separated trials table."""
        keys: set[str] = set()
        for line in trials_path.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            if parts[0].strip().lower() in {"enroll_key", "enroll", "enrollment_key"}:
                continue
            keys.update((parts[0], parts[1]))
        return keys

    def _sv_trial_key_gaps(self, manifest: Path, sample_keys: set[str]) -> set[str]:
        """Trial keys missing from the projected sample rows, if the table parses."""
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        trials_path = Path(str(payload.get("trials_file") or ""))
        if not trials_path.is_absolute():
            trials_path = manifest.parent / trials_path
        trial_keys = self._sv_trial_keys(trials_path)
        if not trial_keys:
            raise ValueError(f"SV trials file has no parseable enroll/test rows: {trials_path}")
        return trial_keys - sample_keys

    def _project_sv_sample_rows(
        self,
        *,
        sample_jsonl_path: Path,
        raw_dir: Path,
        trial_manifest: Path,
        language: str,
        dataset_label: str,
        metadata_base: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        """Project flat SV sample rows into speaker-embedding evaluation JSONL.

        Every projected row keeps its absolute audio path, the speaker id, and
        the resolved trial manifest path; the evaluator binds the manifest from
        the first reference row at scoring time.
        """
        rows: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        source_records = 0
        seen_keys: set[str] = set()
        default_manifest = str(trial_manifest)

        with sample_jsonl_path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                source_records += 1
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError("row is not an object")
                    attr = record.get("attribute") if isinstance(record.get("attribute"), dict) else {}
                    raw_path = attr.get("path") or record.get("audio") or record.get("path") or record.get("wav")
                    if not raw_path:
                        raise ValueError("missing audio path (audio, path, attribute.path, or wav)")
                    audio_path = self._resolve_oref_audio_path(str(raw_path), raw_dir)
                    if not audio_path.is_file():
                        raise ValueError(f"audio_not_found: {audio_path}")
                    if attr.get("size") is not None and int(attr["size"]) != audio_path.stat().st_size:
                        raise ValueError(
                            f"audio_size_mismatch: expected {attr['size']} got {audio_path.stat().st_size}"
                        )

                    key = str(record.get("key") or record.get("sample_id") or audio_path.stem).strip()
                    if not key:
                        raise ValueError("missing key/sample_id")
                    if key in seen_keys:
                        raise ValueError(f"duplicate sample key: {key}")

                    speaker_id = str(record.get("speaker_id") or "").strip()
                    if not speaker_id:
                        raise ValueError("missing speaker_id")

                    row_manifest = default_manifest
                    manifest_value = str(record.get("trial_manifest") or "").strip()
                    if manifest_value:
                        candidate = Path(manifest_value).expanduser()
                        if not candidate.is_absolute():
                            resolved = raw_dir / candidate
                            candidate = resolved if resolved.is_file() else raw_dir / candidate.name
                        if not candidate.is_file():
                            raise ValueError(f"trial_manifest not found: {candidate}")
                        row_manifest = str(candidate)
                except (TypeError, ValueError, OSError) as exc:
                    skipped.append({"line": line_no, "reason": str(exc)})
                    continue

                seen_keys.add(key)
                rows.append(
                    {
                        "key": key,
                        "path": str(audio_path),
                        "audio": str(audio_path),
                        "task": "SV",
                        "language": str(record.get("language") or language or "auto"),
                        "dataset": dataset_label,
                        "speaker_id": speaker_id,
                        "trial_manifest": row_manifest,
                        "metadata": {
                            **metadata_base,
                            "sample_id": record.get("sample_id") or key,
                            "speaker_id": speaker_id,
                            "raw_data_md5": attr.get("raw_data_md5"),
                            "raw_data_format": attr.get("raw_data_format"),
                            "size": attr.get("size") or audio_path.stat().st_size,
                            "channels": attr.get("channels"),
                        },
                    }
                )
        return rows, skipped, source_records
