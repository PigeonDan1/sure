from pathlib import Path
import json
import os
import subprocess
import sys

import pytest
import yaml
from typer.testing import CliRunner

from sure_eval.cli import app
from sure_eval.models.registry import ModelRegistry


SKILL_DIR = Path(__file__).parents[1] / "skills" / "sure-eval"


def test_skill_has_valid_frontmatter_and_agent_metadata() -> None:
    skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---\n")
    _, frontmatter, body = skill.split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == "sure-eval"
    assert metadata["description"]
    assert "readiness" in body

    ui = yaml.safe_load((SKILL_DIR / "agents" / "openai.yaml").read_text(encoding="utf-8"))
    assert ui["interface"]["display_name"] == "SURE-EVAL"
    assert ui["interface"]["default_prompt"]


def test_repository_discovery_resolves_the_skill() -> None:
    discovered = SKILL_DIR.parents[1] / ".agents" / "skills" / "sure-eval" / "SKILL.md"
    assert discovered.resolve() == (SKILL_DIR / "SKILL.md").resolve()
    metadata = yaml.safe_load(discovered.read_text().split("---", 2)[1])
    assert metadata["name"] == "sure-eval"


@pytest.fixture
def model_registry(monkeypatch, tmp_path: Path) -> ModelRegistry:
    model_dir = tmp_path / "models" / "fixture_asr"
    model_dir.mkdir(parents=True)
    (model_dir / "model.py").write_text(
        "from types import SimpleNamespace\n"
        "class ASRModel:\n"
        "    def __init__(self, **kwargs): pass\n"
        "    def transcribe(self, **kwargs):\n"
        "        return SimpleNamespace(text='hello world', language='en', timestamps=[])\n"
    )
    (model_dir / "server.py").write_text("# fixture runtime\n")
    (model_dir / "config.yaml").write_text(yaml.safe_dump({
        "name": "fixture_asr", "task": "ASR",
        "server": {"command": [sys.executable, "server.py"], "env": {"MODEL_TOKEN": "fixture-secret"}},
    }))
    registry = ModelRegistry(model_dir.parent)
    monkeypatch.setattr("sure_eval.cli.get_model_registry", lambda: registry)
    return registry


@pytest.mark.parametrize("model_args", [["fixture_asr"], ["--model", "fixture_asr"]])
def test_doctor_ready_json_and_no_environment_secrets(model_registry, monkeypatch, tmp_path, model_args):
    monkeypatch.setenv("INHERITED_TOKEN", "inherited-secret")
    report = tmp_path / "nested" / "readiness.json"
    result = CliRunner().invoke(app, ["doctor", *model_args, "--json", str(report)])
    assert result.exit_code == 0, result.output
    payload = json.loads(report.read_text())
    assert payload["status"] == "ready"
    assert payload["failure_class"] is None
    assert payload["env"] == {"MODEL_TOKEN": "<redacted>"}
    assert "fixture-secret" not in report.read_text() + result.output
    assert "inherited-secret" not in report.read_text() + result.output


def test_doctor_unknown_and_missing_runtime_write_blocked_reports(model_registry, tmp_path):
    for name, failure in [("unknown", "registry_missing"), ("fixture_asr", "runtime_executable_missing")]:
        if name == "fixture_asr":
            model_registry.get_model(name).config["server"]["command"] = ["/missing/python", "server.py"]
        report = tmp_path / f"{name}.json"
        result = CliRunner().invoke(app, ["doctor", name, "--json", str(report)])
        assert result.exit_code == 1, result.output
        payload = json.loads(report.read_text())
        assert payload["status"] == "blocked"
        assert payload["failure_class"] == failure


def test_doctor_rejects_ambiguous_or_missing_model():
    for args in [["doctor", "--json", "/unused.json"], ["doctor", "first", "--model", "second"]]:
        assert CliRunner().invoke(app, args).exit_code == 2


def test_agent_can_list_and_inspect_registered_models(model_registry):
    runner = CliRunner()
    for command in [["models", "list"], ["models", "inspect", "fixture_asr"]]:
        result = runner.invoke(app, command)
        assert result.exit_code == 0, result.output
        assert "fixture_asr" in result.output


def test_asr_dry_run_and_real_subprocess_prediction(model_registry, tmp_path):
    root = SKILL_DIR.parents[1]
    input_path = tmp_path / "input.jsonl"
    input_path.write_text(json.dumps({
        "instance_id": "sample", "task": "asr",
        "input": {"audio_path": str(root / "tests/fixtures/shared/asr/en_16k_10s.wav"), "sample_rate": 16000},
        "request": {"language": "en", "timestamps": False},
    }) + "\n")
    output = tmp_path / "predictions.jsonl"
    command = ["predict", "fixture_asr", "--input", str(input_path), "--output", str(output), "--task", "asr"]
    runner = CliRunner()
    result = runner.invoke(app, [*command, "--dry-run"])
    assert result.exit_code == 0, result.output
    assert not output.exists()
    result = runner.invoke(app, command)
    assert result.exit_code == 0, result.output
    prediction = json.loads(output.read_text())
    assert prediction["status"] == "ok"
    assert prediction["prediction"]["text"] == "hello world"
    manifest = json.loads((tmp_path / "predictions.manifest.json").read_text())
    assert manifest["num_ok"] == 1
    assert manifest["num_failed"] == 0


def test_offline_scoring_from_an_unrelated_working_directory(tmp_path):
    output = tmp_path / "run"
    result = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts/offline_smoke.py"), "--output-dir", str(output)],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(SKILL_DIR.parents[1] / "src")},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / "result.json").read_text())
    assert report["status"] == "completed"
    assert report["metrics"] == {"metric": "cer", "score": 0.25, "num_samples": 2}
