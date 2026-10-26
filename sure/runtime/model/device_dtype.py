"""Shared device and approved dtype contract for model runtimes.

The model wrapper owns model-specific loading, but it must not invent a new
device/dtype policy.  These small helpers are dependency-free so onboarding
gates and runtime launchers can validate the same contract before loading a
large model.
"""
from __future__ import annotations

import re
from typing import Any

CUDA_DEVICE_RE = re.compile(r"^cuda(?::([0-9]+))?$", re.IGNORECASE)
SUPPORTED_DTYPES = {"bfloat16", "float16", "float32", "float64"}


def normalize_dtype_name(value: object) -> str:
    """Return a canonical torch dtype name or raise for an ambiguous value."""
    name = str(value or "").strip().lower()
    if name.startswith("torch."):
        name = name[6:]
    if name not in SUPPORTED_DTYPES:
        raise ValueError(
            f"unsupported model dtype {value!r}; declare one of "
            f"{', '.join(sorted(SUPPORTED_DTYPES))}"
        )
    return name


def normalize_device(
    requested: object,
    *,
    cuda_available: bool | None = None,
    cuda_count: int | None = None,
) -> str:
    """Normalize a model process device without changing its requested index.

    ``cuda`` means the first process-visible GPU.  ``cuda:N`` keeps N and is
    range-checked when a count is supplied.  The helper deliberately does not
    select a dtype.
    """
    value = str(requested or "auto").strip().lower()
    if value == "auto":
        if cuda_available is None:
            raise ValueError("auto device requires an explicit hardware probe")
        value = "cuda:0" if cuda_available else "cpu"
    if value == "cpu":
        return value
    match = CUDA_DEVICE_RE.fullmatch(value)
    if not match:
        raise ValueError(f"unsupported device {requested!r}; use cpu, cuda, or cuda:<index>")
    if cuda_available is False:
        raise RuntimeError(f"CUDA device {value} requested but CUDA is unavailable")
    index = int(match.group(1) or 0)
    if cuda_count is not None and index >= cuda_count:
        raise RuntimeError(
            f"CUDA device {value} is not visible; only {cuda_count} CUDA device(s) are available"
        )
    return f"cuda:{index}"


def dtype_from_config(config: dict[str, Any]) -> str | None:
    """Read a model dtype declaration from a config object."""
    model = config.get("model") if isinstance(config.get("model"), dict) else {}
    raw = model.get("dtype") or config.get("dtype")
    return normalize_dtype_name(raw) if raw else None


def probe_dtype(device: str, dtype_name: str, torch_module: Any) -> dict[str, Any]:
    """Run a small same-dtype operation on the resolved target device."""
    dtype = getattr(torch_module, dtype_name, None)
    if not isinstance(dtype, torch_module.dtype):
        raise ValueError(f"torch does not expose dtype {dtype_name!r}")
    target = torch_module.device(device)
    left = torch_module.ones((2, 2), dtype=dtype, device=target)
    right = torch_module.ones((2, 2), dtype=dtype, device=target)
    result = left @ right
    if result.dtype != dtype or not bool(torch_module.isfinite(result).all()):
        raise RuntimeError(f"dtype probe produced {result.dtype} for requested {dtype_name}")
    return {
        "status": "passed",
        "operation": "same_dtype_matmul",
        "device": str(target),
        "dtype": dtype_name,
    }
