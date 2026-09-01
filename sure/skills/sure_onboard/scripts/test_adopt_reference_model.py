from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adopt_reference_model import read_json, write_artifact_manifest, write_package_gate, write_verdict
from deployment_contract import require_timestamp_after


class AdoptedDocumentStampsTest(unittest.TestCase):
    """check_package_gate.py and check_verdict.py require each adopted document to be stamped
    strictly after the documents it cites; the adopter has to write stamps that satisfy that."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.target = Path(self.temporary.name) / "model"
        (self.target / "artifacts").mkdir(parents=True)
        self.manifest_path = write_artifact_manifest(self.target, "example/demo", "example__demo")
        write_package_gate(self.target, self.manifest_path)
        write_verdict(self.target, "example/demo", "example__demo", {})

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def document(self, name: str) -> dict:
        return read_json(self.target / "artifacts" / name)

    def test_the_package_gate_is_stamped_after_the_manifest_it_cites(self) -> None:
        require_timestamp_after(
            "package_gate.json",
            self.document("package_gate.json"),
            ("artifact_manifest.json", self.document("artifact_manifest.json")),
        )

    def test_the_verdict_is_stamped_after_the_package_gate(self) -> None:
        require_timestamp_after(
            "verdict.json",
            self.document("verdict.json"),
            ("package_gate.json", self.document("package_gate.json")),
        )


if __name__ == "__main__":
    unittest.main()
