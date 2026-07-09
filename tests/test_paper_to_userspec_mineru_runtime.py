from __future__ import annotations

from pathlib import Path

from sure_eval.paper_to_userspec import mineru_runtime


def test_sure_paper_mineru_bin_takes_precedence_over_path(tmp_path: Path, monkeypatch) -> None:
    configured = tmp_path / "custom-mineru"
    configured.write_text("#!/bin/sh\n", encoding="utf-8")
    configured.chmod(0o755)
    path_mineru = tmp_path / "mineru"
    path_mineru.write_text("#!/bin/sh\n", encoding="utf-8")
    path_mineru.chmod(0o755)

    env = {
        "SURE_PAPER_MINERU_BIN": str(configured),
        "PATH": str(tmp_path),
    }
    monkeypatch.setattr(mineru_runtime, "LEGACY_MINERU_HINT", tmp_path / "missing-hint")

    result = mineru_runtime.discover_mineru_executable(env)

    assert result["available"] is True
    assert result["source"] == "SURE_PAPER_MINERU_BIN"
    assert result["path"] == str(configured.resolve())


def test_sure_paper_mineru_bin_missing_reports_clear_error(monkeypatch) -> None:
    missing = "/tmp/definitely_missing_mineru_for_sure_eval"
    monkeypatch.setattr(mineru_runtime, "LEGACY_MINERU_HINT", Path("/tmp/missing-mineru-hint"))

    result = mineru_runtime.discover_mineru_executable({"SURE_PAPER_MINERU_BIN": missing})

    assert result["available"] is False
    assert result["error_type"] == "mineru_executable_missing"
    assert missing in result["error_message"]


def test_mineru_env_defaults_to_modelscope(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(mineru_runtime, "PREFERRED_CLUSTER_CACHE", tmp_path / "missing-cache")
    env, selected = mineru_runtime.build_mineru_env({"HOME": str(tmp_path)})

    assert selected["MINERU_MODEL_SOURCE"] == "modelscope"
    assert selected["XDG_CACHE_HOME"] == str(tmp_path / ".cache")
    assert selected["MODELSCOPE_CACHE"] == str(tmp_path / ".cache" / "modelscope")
    assert env["MINERU_MODEL_SOURCE"] == selected["MINERU_MODEL_SOURCE"]


def test_mineru_env_sure_overrides(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(mineru_runtime, "PREFERRED_CLUSTER_CACHE", tmp_path / "missing-cache")
    env, selected = mineru_runtime.build_mineru_env(
        {
            "HOME": str(tmp_path),
            "SURE_PAPER_MINERU_MODEL_SOURCE": "local",
            "SURE_PAPER_MINERU_CACHE_HOME": str(tmp_path / "cache"),
            "SURE_PAPER_MODELSCOPE_CACHE": str(tmp_path / "ms"),
        }
    )

    assert selected == {
        "MINERU_MODEL_SOURCE": "local",
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "MODELSCOPE_CACHE": str(tmp_path / "ms"),
    }
    assert env["MODELSCOPE_CACHE"] == str(tmp_path / "ms")
