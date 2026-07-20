from __future__ import annotations

import pytest


def test_select_best_partition_defaults_to_pdgpu_4090(monkeypatch) -> None:
    from sure_eval.agent import vc_submitter

    monkeypatch.setattr(
        vc_submitter,
        "get_user_partitions",
        lambda: {"pdgpu-a10", "pdgpu-3090", "pdgpu-4090"},
    )

    assert vc_submitter.select_best_partition() == "pdgpu-4090"


def test_select_best_partition_uses_explicit_required_partition(monkeypatch) -> None:
    from sure_eval.agent import vc_submitter

    monkeypatch.setattr(
        vc_submitter,
        "get_user_partitions",
        lambda: {"pdgpu-a10", "pdgpu-3090", "pdgpu-4090"},
    )

    assert vc_submitter.select_best_partition("pdgpu-3090") == "pdgpu-3090"


def test_select_best_partition_fails_without_required_partition(monkeypatch) -> None:
    from sure_eval.agent import vc_submitter

    monkeypatch.setattr(
        vc_submitter,
        "get_user_partitions",
        lambda: {"pdgpu-a10", "pdgpu-3090"},
    )

    with pytest.raises(RuntimeError, match="pdgpu-4090"):
        vc_submitter.select_best_partition()


def test_select_best_partition_does_not_fallback_from_explicit_partition(monkeypatch) -> None:
    from sure_eval.agent import vc_submitter

    monkeypatch.setattr(
        vc_submitter,
        "get_user_partitions",
        lambda: {"pdgpu-a10", "pdgpu-4090"},
    )

    with pytest.raises(RuntimeError, match="pdgpu-3090"):
        vc_submitter.select_best_partition("pdgpu-3090")


def test_build_vc_submit_command_uses_explicit_container_python(monkeypatch) -> None:
    from sure_eval.agent import vc_submitter

    monkeypatch.setattr(vc_submitter, "select_best_partition", lambda partition=None: "pdgpu-3090")

    cmd = vc_submitter.build_vc_submit_command(
        model_name="SWivid__F5-TTS_Emilia-ZH-EN",
        run_id="main_agent_f5tts_seedtts_en_002",
        image="example/f5tts:v1",
        partition="pdgpu-3090",
        memory_gb=16,
        container_python_path="python",
    )

    command_text = " ".join(cmd)
    assert "export PYTHON_BIN=python;" in command_text
    assert "/opt/SWivid__F5-TTS_Emilia-ZH-EN_venv" not in command_text
    assert "ln -sfn" not in command_text


def test_build_vc_submit_command_adds_output_storage_mount(monkeypatch) -> None:
    from sure_eval.agent import vc_submitter

    monkeypatch.setattr(vc_submitter, "select_best_partition", lambda partition=None: "pdgpu-4090")

    cmd = vc_submitter.build_vc_submit_command(
        model_name="Qwen__Qwen3-ASR-1.7B",
        run_id="main_agent_qwen3_asr_001",
        image="example/qwen3-asr:v1",
        partition="pdgpu-4090",
        memory_gb=16,
        volume_mount="/hpc_stor02/work/sure-eval:/workspace/sure-eval",
        additional_host_paths=["/hpc_stor01/users/example/sure-results"],
    )

    assert cmd.count("-v") == 1
    volume = cmd[cmd.index("-v") + 1]
    assert "/hpc_stor02/work/sure-eval:/workspace/sure-eval" in volume
    assert "/hpc_stor01:/hpc_stor01" in volume


def test_trigger_vc_resolves_additional_mount_paths_from_surface_outputs() -> None:
    from sure_eval.agent.trigger_vc import resolve_additional_mount_paths

    surface = {
        "resolved_inputs": {
            "run_dir": "/hpc_stor03/sjtu_home/user/sure-eval/model/eval_runs/run_001",
            "output_dir": "/hpc_stor01/sjtu_home/user/sure-results",
            "results_dir": "/hpc_stor01/sjtu_home/user/sure-results/model/strict_core",
        },
        "expected_outputs": {
            "report_snapshot": "/hpc_stor01/sjtu_home/user/sure-results/model/strict_core/report_snapshot.md",
        },
    }

    assert resolve_additional_mount_paths(surface) == [
        "/hpc_stor03/sjtu_home/user/sure-eval/model/eval_runs/run_001",
        "/hpc_stor01/sjtu_home/user/sure-results",
        "/hpc_stor01/sjtu_home/user/sure-results/model/strict_core",
        "/hpc_stor01/sjtu_home/user/sure-results/model/strict_core/report_snapshot.md",
    ]


def test_trigger_vc_resolves_container_python_from_surface_runtime_paths() -> None:
    from sure_eval.agent.trigger_vc import resolve_container_python_path

    surface = {
        "resolved_inputs": {
            "vc_runtime_contract": {
                "runtime_paths": {
                    "container_python_path": "python",
                }
            }
        }
    }

    assert resolve_container_python_path(surface) == "python"
