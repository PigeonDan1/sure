"""Language canonicalization and model-specific mapping for inference."""

from __future__ import annotations

from typing import Any

from sure_eval.inference.errors import LanguageNormalizationError

CANONICAL_LANGUAGE_ALIASES: dict[str, str] = {
    "en": "en",
    "english": "en",
    "zh": "zh",
    "chinese": "zh",
    "中文": "zh",
    "ja": "ja",
    "japanese": "ja",
    "ko": "ko",
    "korean": "ko",
    "auto": "auto",
}

MODEL_LANGUAGE_REGISTRY: dict[str, dict[str, str]] = {
    "default": {
        "en": "en",
        "zh": "zh",
        "ja": "ja",
        "ko": "ko",
        "auto": "auto",
    },
    "asr_qwen3": {
        "en": "English",
        "zh": "Chinese",
        "ja": "Japanese",
        "ko": "Korean",
        "auto": "auto",
    },
}


def supported_canonical_languages() -> list[str]:
    """Return the currently supported canonical language codes."""
    return ["en", "zh", "ja", "ko", "auto"]


def canonicalize_language(language: Any) -> str | None:
    """Canonicalize user-provided language aliases to the shared code set."""
    if language is None:
        return None
    if not isinstance(language, str):
        raise LanguageNormalizationError(
            "unsupported_language",
            f"Unsupported language value {language!r}. Supported canonical languages: {supported_canonical_languages()}",
        )

    normalized = language.strip()
    if not normalized:
        return None

    canonical = CANONICAL_LANGUAGE_ALIASES.get(normalized.lower())
    if canonical is None:
        raise LanguageNormalizationError(
            "unsupported_language",
            f"Unsupported language '{language}'. Canonicalization failed. Supported canonical languages: {supported_canonical_languages()}",
        )
    return canonical


def map_language_for_model(*, model_name: str, language: Any) -> str | None:
    """Map a canonical language code to the form expected by one model."""
    canonical = canonicalize_language(language)
    if canonical is None:
        return None

    mapping = MODEL_LANGUAGE_REGISTRY.get(model_name, MODEL_LANGUAGE_REGISTRY["default"])
    mapped = mapping.get(canonical)
    if mapped is None:
        raise LanguageNormalizationError(
            "language_not_supported_by_model",
            f"Model '{model_name}' does not support canonical language '{canonical}'. Supported for this model: {sorted(mapping.keys())}",
        )
    return mapped
