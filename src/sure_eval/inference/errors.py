"""Inference-specific error types."""

from __future__ import annotations


class InferenceSurfaceError(Exception):
    """Base error for unified inference surface failures."""


class SchemaValidationError(InferenceSurfaceError):
    """Raised when an input or output record violates the schema."""


class AdapterError(InferenceSurfaceError):
    """Raised when a model cannot be adapted to the inference surface."""

    def __init__(self, message: str, code: str = "unsupported_runtime_protocol") -> None:
        super().__init__(message)
        self.code = code


class LanguageNormalizationError(InferenceSurfaceError):
    """Raised when language canonicalization or model mapping fails."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
