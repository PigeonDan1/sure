from __future__ import annotations

import unittest
from types import SimpleNamespace

from sure.runtime.model.device_dtype import dtype_from_config, normalize_device, normalize_dtype_name


class DeviceDtypeTests(unittest.TestCase):
    def test_cuda_forms_are_normalized_without_selecting_dtype(self) -> None:
        self.assertEqual(normalize_device("cuda", cuda_available=True, cuda_count=2), "cuda:0")
        self.assertEqual(normalize_device("cuda:1", cuda_available=True, cuda_count=2), "cuda:1")
        self.assertEqual(normalize_device("cpu", cuda_available=False), "cpu")

    def test_invalid_or_invisible_cuda_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_device("cuda:gpu", cuda_available=True, cuda_count=1)
        with self.assertRaises(RuntimeError):
            normalize_device("cuda:1", cuda_available=True, cuda_count=1)
        with self.assertRaises(RuntimeError):
            normalize_device("cuda:0", cuda_available=False)

    def test_dtype_is_normalized_independently(self) -> None:
        self.assertEqual(normalize_dtype_name("torch.bfloat16"), "bfloat16")
        self.assertEqual(dtype_from_config({"model": {"dtype": "float16"}}), "float16")
        with self.assertRaises(ValueError):
            normalize_dtype_name("auto")


if __name__ == "__main__":
    unittest.main()
