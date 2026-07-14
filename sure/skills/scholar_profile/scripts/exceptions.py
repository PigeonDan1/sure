#!/usr/bin/env python3
"""
Custom exception module.
Centralized exception types for the scholar profile pipeline.
"""


class ScholarProfileError(Exception):
    """Base exception for the scholar profile pipeline."""
    pass


class ConfigError(ScholarProfileError):
    """Configuration error."""
    pass


class APIError(ScholarProfileError):
    """API call error."""
    pass


class NetworkError(ScholarProfileError):
    """Network error."""
    pass


class ParseError(ScholarProfileError):
    """Parsing error."""
    pass


class FileError(ScholarProfileError):
    """File operation error."""
    pass


class ValidationError(ScholarProfileError):
    """Validation error."""
    pass


class DBLPError(ScholarProfileError):
    """DBLP-related error."""
    pass


class OpenAlexError(ScholarProfileError):
    """OpenAlex API error."""
    pass


class PDFError(ScholarProfileError):
    """PDF processing error."""
    pass


class LLMError(ScholarProfileError):
    """LLM call error."""
    pass


# ============ Error handling decorator ============

import functools
import logging
from typing import Callable, Type, Union

logger = logging.getLogger(__name__)


def handle_errors(
    *exceptions: Type[Exception],
    default_return=None,
    log_level: int = logging.WARNING,
    reraise: bool = False,
):
    """
    Error handling decorator.

    Args:
        exceptions: Exception types to catch.
        default_return: Default return value on exception.
        log_level: Logging level.
        reraise: Whether to re-raise the exception.
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except exceptions as e:
                logger.log(log_level, f"{func.__name__} failed: {type(e).__name__}: {e}")
                if reraise:
                    raise
                return default_return
            except Exception as e:
                logger.error(f"{func.__name__} unexpected error: {type(e).__name__}: {e}")
                if reraise:
                    raise
                return default_return
        return wrapper
    return decorator


def retry_on_error(
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
):
    """
    Retry decorator with exponential backoff.

    Args:
        max_retries: Maximum number of retries.
        delay: Initial delay in seconds.
        backoff: Delay multiplier.
        exceptions: Exception types to retry on.
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            import time
            last_exception = None
            current_delay = delay

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        logger.warning(
                            f"{func.__name__} attempt {attempt + 1}/{max_retries + 1} failed: "
                            f"{type(e).__name__}: {e}, retrying in {current_delay:.1f}s..."
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(
                            f"{func.__name__} failed after {max_retries + 1} attempts: "
                            f"{type(e).__name__}: {e}"
                        )

            raise last_exception
        return wrapper
    return decorator


# ============ Error context manager ============

class ErrorContext:
    """
    Error context manager.

    Usage:
        with ErrorContext("fetching data", raise_on_error=False):
            # code that may fail
            pass
    """

    def __init__(
        self,
        operation: str,
        raise_on_error: bool = True,
        default_return=None,
        log_level: int = logging.WARNING,
    ):
        self.operation = operation
        self.raise_on_error = raise_on_error
        self.default_return = default_return
        self.log_level = log_level
        self.exception = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.exception = exc_val
            logger.log(
                self.log_level,
                f"Error during {self.operation}: {exc_type.__name__}: {exc_val}"
            )
            if not self.raise_on_error:
                return True  # suppress exception
        return False

    def __bool__(self):
        return self.exception is None


# ============ Convenience functions ============

def safe_execute(
    func: Callable,
    *args,
    default=None,
    log_error: bool = True,
    **kwargs
):
    """
    Safely execute a function, catching all exceptions.

    Args:
        func: Function to execute.
        *args: Function arguments.
        default: Default return value on exception.
        log_error: Whether to log the error.
        **kwargs: Function keyword arguments.

    Returns:
        Function return value or default.
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        if log_error:
            logger.error(f"{func.__name__} failed: {type(e).__name__}: {e}")
        return default


def validate_not_none(value, name: str):
    """Validate that value is not None."""
    if value is None:
        raise ValidationError(f"{name} cannot be None")
    return value


def validate_not_empty(value, name: str):
    """Validate that value is not empty."""
    if not value:
        raise ValidationError(f"{name} cannot be empty")
    return value


def validate_file_exists(file_path, name: str = "File"):
    """Validate that file exists."""
    from pathlib import Path
    path = Path(file_path)
    if not path.exists():
        raise FileError(f"{name} not found: {path}")
    return path


# ============ Tests ============

if __name__ == "__main__":
    # Test error handling
    logging.basicConfig(level=logging.INFO)

    @handle_errors(ValueError, KeyError, default_return="default")
    def test_function():
        raise ValueError("test error")

    result = test_function()
    print(f"Result: {result}")

    # Test retry
    @retry_on_error(max_retries=2, delay=0.1, exceptions=(ValueError,))
    def test_retry():
        raise ValueError("retry test")

    try:
        test_retry()
    except ValueError as e:
        print(f"Expected error: {e}")

    # Test error context
    with ErrorContext("test operation", raise_on_error=False) as ctx:
        raise ValueError("context test")

    print(f"Context exception: {ctx.exception}")
