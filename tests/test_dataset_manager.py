from __future__ import annotations

import json
import subprocess
import types
from pathlib import Path
from unittest.mock import patch

from sure_eval.core.config import Config
from sure_eval.datasets.dataset_manager import DatasetManager


def _make_config(tmp_path: Path) -> Config:
    config = Config.from_env()
    config.data.datasets = str(tmp_path / "datasets")
    return config


def test_get_jsonl_path_uses_canonical_dataset_key(tmp_path: Path) -> None:
    manager = DatasetManager(_make_config(tmp_path))

    assert manager.get_jsonl_path("CoVoST2_S2TT_en2zh_test").name == "covost2_en2zh.jsonl"
    assert manager.get_jsonl_path("alimeeting").name == "alimeeting.jsonl"


def test_convert_csv_to_jsonl_uses_canonical_filename_and_dataset_key(tmp_path: Path) -> None:
    manager = DatasetManager(_make_config(tmp_path))
    csv_dir = manager.sure_dir / "SURE_Test_csv"
    csv_dir.mkdir(parents=True, exist_ok=True)
    csv_path = csv_dir / "CoVoST2_S2TT_en2zh_test.csv"
    csv_path.write_text("audio,text\nsample.wav,hello world\n", encoding="utf-8")

    jsonl_path = manager._convert_csv_to_jsonl(csv_path)

    assert jsonl_path.name == "covost2_en2zh.jsonl"
    rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["dataset"] == "covost2_en2zh"


def test_download_sure_suites_creates_directory_before_listing(tmp_path: Path) -> None:
    manager = DatasetManager(_make_config(tmp_path))

    with patch("sure_eval.datasets.dataset_manager.subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(args=["modelscope"], returncode=0)
        manager._download_sure_suites()

    assert (manager.sure_dir / "SURE_Test_Suites").exists()


def test_modelscope_download_uses_canonical_output_name(tmp_path: Path) -> None:
    manager = DatasetManager(_make_config(tmp_path))
    dataset_def = manager.config.get_dataset("alimeeting")
    assert dataset_def is not None

    fake_ms_dataset = types.SimpleNamespace(
        load=lambda *args, **kwargs: [{"id": "utt1", "audio": "a.wav", "text": "hello"}]
    )

    with patch.dict("sys.modules", {"modelscope.msdatasets": types.SimpleNamespace(MsDataset=fake_ms_dataset)}):
        output_path = manager._download_modelscope(dataset_def)

    assert output_path.name == "alimeeting.jsonl"
    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["dataset"] == "alimeeting"
