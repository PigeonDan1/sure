import subprocess
import sys
import unittest
from pathlib import Path


class RunReportWrapperTest(unittest.TestCase):
    def test_loads_infer_checker_sibling_imports(self):
        wrapper = Path(__file__).with_name("check_run_report.py")
        result = subprocess.run(
            [sys.executable, str(wrapper), "--help"],
            cwd=wrapper.parent.parent,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--profile {infer,eval}", result.stdout)


if __name__ == "__main__":
    unittest.main()
