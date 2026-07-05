try:
    from .model import ModelWrapper, TranscriptionResult
except ImportError:
    from model import ModelWrapper, TranscriptionResult

__all__ = ["ModelWrapper", "TranscriptionResult"]
