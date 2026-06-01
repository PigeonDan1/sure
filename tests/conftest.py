from __future__ import annotations

import sys
import types


def _install_structlog_stub() -> None:
    if "structlog" in sys.modules:
        return

    class _Logger:
        def bind(self, **_: object) -> "_Logger":
            return self

        def __getattr__(self, _: str):
            return lambda *args, **kwargs: None

    class _Callable:
        def __call__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

    structlog_stub = types.ModuleType("structlog")
    structlog_stub.configure = lambda *args, **kwargs: None
    structlog_stub.get_logger = lambda *args, **kwargs: _Logger()
    structlog_stub.stdlib = types.SimpleNamespace(
        filter_by_level=_Callable(),
        add_logger_name=_Callable(),
        add_log_level=_Callable(),
        PositionalArgumentsFormatter=lambda *args, **kwargs: _Callable(),
        LoggerFactory=lambda *args, **kwargs: _Callable(),
        BoundLogger=_Logger,
    )
    structlog_stub.processors = types.SimpleNamespace(
        TimeStamper=lambda *args, **kwargs: _Callable(),
        StackInfoRenderer=lambda *args, **kwargs: _Callable(),
        format_exc_info=_Callable(),
        UnicodeDecoder=lambda *args, **kwargs: _Callable(),
        JSONRenderer=lambda *args, **kwargs: _Callable(),
    )
    structlog_stub.dev = types.SimpleNamespace(
        ConsoleRenderer=lambda *args, **kwargs: _Callable(),
    )
    sys.modules["structlog"] = structlog_stub


_install_structlog_stub()
