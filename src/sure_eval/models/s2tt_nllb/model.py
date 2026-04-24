from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class ModelLoadError(RuntimeError):
    pass


class InferenceError(RuntimeError):
    pass


@dataclass
class S2TTResult:
    text: str
    translation_text: str
    asr_text: str
    source_lang: str
    target_lang: str
    segments: list[dict[str, Any]]
    error_code: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("text", "translation_text", "asr_text"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class S2TTNLLBModel:
    """Minimal cascaded S2TT baseline using Whisper ASR + NLLB MT."""

    WHISPER_LANG_MAP = {
        "eng_Latn": "english",
        "zho_Hans": "chinese",
        "zho_Hant": "chinese",
        "fra_Latn": "french",
        "spa_Latn": "spanish",
        "deu_Latn": "german",
        "jpn_Jpan": "japanese",
        "kor_Hang": "korean",
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.model_root = Path(__file__).resolve().parent
        self.checkpoints_dir = Path(
            self.config.get("checkpoints_dir", self.model_root / "checkpoints")
        )
        self.runtime_dir = Path(self.config.get("runtime_dir", self.model_root / ".runtime"))
        self.asr_model_id = self.config.get("asr_model_id", "openai/whisper-tiny")
        self.mt_model_id = self.config.get("mt_model_id", "facebook/nllb-200-distilled-600M")
        requested_device = self.config.get("device") or os.environ.get("DEVICE", "cpu")
        self.device = self._resolve_device(requested_device)
        self.asr_model_dir = self.checkpoints_dir / "asr_frontend"
        self.mt_model_dir = self.checkpoints_dir / "mt_backend"
        self._asr_model = None
        self._asr_processor = None
        self._mt_model = None
        self._mt_tokenizer = None

    def _resolve_device(self, requested_device: str) -> str:
        if requested_device == "auto":
            try:
                import torch

                return "cuda" if torch.cuda.is_available() else "cpu"
            except Exception:
                return "cpu"
        return requested_device

    def _prepare_runtime_dirs(self) -> None:
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.asr_model_dir.mkdir(parents=True, exist_ok=True)
        self.mt_model_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(self.runtime_dir / "hf_home"))
        os.environ.setdefault("TRANSFORMERS_CACHE", str(self.runtime_dir / "transformers"))
        os.environ.setdefault("HF_HUB_CACHE", str(self.runtime_dir / "hub"))

    def _ensure_weights(self) -> None:
        self._prepare_runtime_dirs()
        try:
            from huggingface_hub import snapshot_download
        except Exception as exc:
            raise ModelLoadError(f"Failed to import huggingface_hub for weight download: {exc}") from exc

        if not any(self.asr_model_dir.iterdir()):
            snapshot_download(
                repo_id=self.asr_model_id,
                local_dir=str(self.asr_model_dir),
            )
        if not any(self.mt_model_dir.iterdir()):
            snapshot_download(
                repo_id=self.mt_model_id,
                local_dir=str(self.mt_model_dir),
            )

    def load(self) -> None:
        if (
            self._asr_model is not None
            and self._asr_processor is not None
            and self._mt_model is not None
            and self._mt_tokenizer is not None
        ):
            return

        self._ensure_weights()

        try:
            import torch
            from transformers import (
                AutoModelForSeq2SeqLM,
                AutoModelForSpeechSeq2Seq,
                AutoProcessor,
                AutoTokenizer,
            )

            asr_dtype = torch.float16 if self.device == "cuda" else torch.float32
            self._asr_processor = AutoProcessor.from_pretrained(str(self.asr_model_dir))
            self._asr_model = AutoModelForSpeechSeq2Seq.from_pretrained(
                str(self.asr_model_dir),
                dtype=asr_dtype,
                low_cpu_mem_usage=True,
            )
            self._mt_tokenizer = AutoTokenizer.from_pretrained(str(self.mt_model_dir))
            self._mt_model = AutoModelForSeq2SeqLM.from_pretrained(str(self.mt_model_dir))

            if self.device == "cuda":
                self._asr_model = self._asr_model.to("cuda")
                self._mt_model = self._mt_model.to("cuda")
            else:
                self._asr_model = self._asr_model.to("cpu")
                self._mt_model = self._mt_model.to("cpu")
        except Exception as exc:
            raise ModelLoadError(f"Failed to load Whisper or NLLB weights: {exc}") from exc

    def _language_for_whisper(self, source_lang: str) -> str:
        if source_lang in self.WHISPER_LANG_MAP:
            return self.WHISPER_LANG_MAP[source_lang]
        if source_lang.endswith("_Latn"):
            return source_lang.split("_", 1)[0]
        raise InferenceError(
            f"Unsupported source_lang for Whisper ASR frontend: {source_lang}"
        )

    def _transcribe(self, audio_path: str, source_lang: str) -> str:
        if self._asr_model is None or self._asr_processor is None:
            self.load()
        try:
            import librosa
            import torch

            audio, _ = librosa.load(audio_path, sr=16000, mono=True)
            prompt_ids = self._asr_processor.get_decoder_prompt_ids(
                language=self._language_for_whisper(source_lang),
                task="transcribe",
            )
            inputs = self._asr_processor(
                audio=audio,
                sampling_rate=16000,
                return_tensors="pt",
            )
            input_features = inputs.input_features.to(self._asr_model.device)
            predicted_ids = self._asr_model.generate(
                input_features,
                forced_decoder_ids=prompt_ids,
                max_new_tokens=225,
            )
            transcript = self._asr_processor.batch_decode(
                predicted_ids,
                skip_special_tokens=True,
            )[0].strip()
            if not transcript:
                raise InferenceError("ASR frontend returned empty transcript")
            if self.device == "cuda":
                torch.cuda.empty_cache()
            return transcript
        except InferenceError:
            raise
        except Exception as exc:
            raise InferenceError(f"ASR frontend failed for {audio_path}: {exc}") from exc

    def _translate(self, source_text: str, source_lang: str, target_lang: str) -> str:
        if self._mt_model is None or self._mt_tokenizer is None:
            self.load()
        try:
            import torch

            self._mt_tokenizer.src_lang = source_lang
            inputs = self._mt_tokenizer(source_text, return_tensors="pt")
            inputs = {key: value.to(self._mt_model.device) for key, value in inputs.items()}
            forced_bos_token_id = self._mt_tokenizer.convert_tokens_to_ids(target_lang)
            generated_tokens = self._mt_model.generate(
                **inputs,
                forced_bos_token_id=forced_bos_token_id,
                max_new_tokens=256,
            )
            translated = self._mt_tokenizer.batch_decode(
                generated_tokens,
                skip_special_tokens=True,
            )[0].strip()
            if not translated:
                raise InferenceError("NLLB backend returned empty translation")
            if self.device == "cuda":
                torch.cuda.empty_cache()
            return translated
        except InferenceError:
            raise
        except Exception as exc:
            raise InferenceError(
                f"NLLB translation backend failed for {source_lang}->{target_lang}: {exc}"
            ) from exc

    def predict(self, input_data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(input_data, dict):
            raise InferenceError("input_data must be a JSON-like dict")
        audio_path = input_data.get("audio_path")
        source_lang = input_data.get("source_lang")
        target_lang = input_data.get("target_lang")
        if not audio_path:
            raise InferenceError("audio_path is required")
        if not source_lang:
            raise InferenceError("source_lang is required")
        if not target_lang:
            raise InferenceError("target_lang is required")
        resolved_audio = Path(audio_path).expanduser().resolve()
        if not resolved_audio.exists():
            raise FileNotFoundError(f"Input audio not found: {resolved_audio}")

        asr_text = self._transcribe(str(resolved_audio), source_lang=source_lang)
        translation_text = self._translate(
            asr_text,
            source_lang=source_lang,
            target_lang=target_lang,
        )
        return S2TTResult(
            text=translation_text,
            translation_text=translation_text,
            asr_text=asr_text,
            source_lang=source_lang,
            target_lang=target_lang,
            segments=[],
            error_code=None,
        ).to_dict()

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "ready"
            if self._asr_model is not None and self._mt_model is not None
            else "loading",
            "message": "ASR and MT backends loaded"
            if self._asr_model is not None and self._mt_model is not None
            else "Model not loaded",
            "model_loaded": self._asr_model is not None and self._mt_model is not None,
            "device": self.device,
            "asr_model_id": self.asr_model_id,
            "mt_model_id": self.mt_model_id,
            "checkpoints_dir": str(self.checkpoints_dir),
            "runtime_dir": str(self.runtime_dir),
        }
