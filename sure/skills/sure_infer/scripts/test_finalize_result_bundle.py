#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import yaml

from finalize_result_bundle import _replace, finalize_bundle


class PrefixBoundaryTests(unittest.TestCase):
    """Both separators are boundaries, and neither may match mid-component.

    Spelled as data so each case runs on every host: the metadata a run leaves
    behind is spelled by whichever platform wrote it, not by the one reading
    it.
    """

    def test_windows_prefix_is_localized_on_its_own_boundary(self) -> None:
        source = r"C:\run\abc"

        self.assertEqual(_replace(source, source, "/published"), "/published")
        self.assertEqual(
            _replace(r"C:\run\abc\metrics\report.json", source, "/published"),
            r"/published\metrics\report.json",
        )
        self.assertEqual(
            _replace("C:/run/abc/metrics/report.json", r"C:/run/abc", "/published"),
            "/published/metrics/report.json",
        )
        self.assertEqual(_replace(r"C:\run\abcdef", source, "/published"), r"C:\run\abcdef")

    def test_posix_prefix_keeps_behaving_as_before(self) -> None:
        source = "/container/run/abc"

        self.assertEqual(_replace(source, source, "/published"), "/published")
        self.assertEqual(
            _replace("/container/run/abc/metrics/report.json", source, "/published"),
            "/published/metrics/report.json",
        )
        self.assertEqual(_replace("/container/run/abcdef", source, "/published"), "/container/run/abcdef")


class FinalizeResultBundleTests(unittest.TestCase):
    def test_localizes_metadata_without_mutating_predictions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "container-output"
            published = Path(temporary) / "published" / "run"
            predictions = root / "predictions"
            predictions.mkdir(parents=True)
            source = str(root.resolve())
            # Spelled the way the host that wrote it spells a path, so the
            # boundary after the run directory is this platform's separator.
            (root / "evaluation_payload.json").write_text(
                json.dumps({"artifact": str(root.resolve() / "metrics" / "report.json")}),
                encoding="utf-8",
            )
            (root / "protocol.yaml").write_text(
                yaml.safe_dump({"run": {"run_dir": source}}), encoding="utf-8"
            )
            prediction = json.dumps({"key": "demo", "normalized_prediction": f"literal {source}"}) + "\n"
            (predictions / "demo.jsonl").write_text(prediction, encoding="utf-8")
            (predictions / "manifest.json").write_text(
                json.dumps({"path": f"{source}/predictions/demo.jsonl"}), encoding="utf-8"
            )

            changed = finalize_bundle(root, published)

            self.assertIn("evaluation_payload.json", changed)
            self.assertEqual(
                json.loads((root / "evaluation_payload.json").read_text())["artifact"],
                str(published / "metrics" / "report.json"),
            )
            self.assertEqual((predictions / "demo.jsonl").read_text(), prediction)
            evidence = json.loads((root / "artifact_path_localization.json").read_text())
            self.assertFalse(evidence["prediction_content_modified"])


if __name__ == "__main__":
    unittest.main()
