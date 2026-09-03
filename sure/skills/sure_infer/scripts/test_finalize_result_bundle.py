#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import yaml

from finalize_result_bundle import finalize_bundle


class FinalizeResultBundleTests(unittest.TestCase):
    def test_localizes_metadata_without_mutating_predictions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "container-output"
            published = Path(temporary) / "published" / "run"
            predictions = root / "predictions"
            predictions.mkdir(parents=True)
            source = str(root.resolve())
            (root / "evaluation_payload.json").write_text(
                json.dumps({"artifact": f"{source}/metrics/report.json"}), encoding="utf-8"
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
