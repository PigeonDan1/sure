#!/usr/bin/env python3
"""The legacy ASR scorer must normalize with the per-language rule maps.

asr_simple_tn_rules/<language>/ holds the symbol, time and digit maps; the rules
root only holds shared word lists. Passing the root as map_dir loads no map, so
"10%" stayed "ten %" while the reference said "ten percent", and WER/CER moved.

Run directly:
    cd sure/skills/sure_infer/scripts && python test_sure_evaluator_normalization.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sure_eval.evaluation import sure_evaluator  # noqa: E402
from sure_eval.evaluation.normalization import asr_simple_tn as tn  # noqa: E402

RULES_DIR = Path(tn.__file__).resolve().parent / "asr_simple_tn_rules"


def _write_pair(root: Path, ref: str, hyp: str) -> tuple[str, str]:
    ref_file = root / "ref.txt"
    hyp_file = root / "hyp.txt"
    ref_file.write_text(f"utt1\t{ref}\n", encoding="utf-8")
    hyp_file.write_text(f"utt1\t{hyp}\n", encoding="utf-8")
    return str(ref_file), str(hyp_file)


class LegacyEvaluatorRuleMapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _spy(self) -> tuple[list[Path], object]:
        seen: list[Path] = []
        real = tn.asr_num2words

        def spy(text, language, map_dir, debug, **kwargs):
            seen.append(Path(map_dir))
            return real(text, language, map_dir, debug, **kwargs)

        return seen, spy

    def test_asr_scoring_uses_the_language_rule_directory(self) -> None:
        ref, hyp = _write_pair(self.root, "ten percent", "10%")
        seen, spy = self._spy()
        with mock.patch.object(tn, "asr_num2words", spy):
            sure_evaluator.SUREEvaluator(language="en")._eval_asr(ref, hyp)
        self.assertTrue(seen)
        self.assertEqual(set(seen), {RULES_DIR / "en"})

    def test_symbol_map_is_applied_when_scoring(self) -> None:
        # "%" -> "percent" only comes from en/symbol.map; with the rules root as
        # map_dir the hypothesis stays "ten %" and the utterance scores an error.
        ref, hyp = _write_pair(self.root, "ten percent", "10%")
        result = sure_evaluator.SUREEvaluator(language="en")._eval_asr(ref, hyp)
        self.assertEqual(result["wer"], 0.0, result)

    def test_code_switch_scoring_uses_each_language_directory(self) -> None:
        ref, hyp = _write_pair(self.root, "ten percent 好", "10% 好")
        seen, spy = self._spy()
        with mock.patch.object(tn, "asr_num2words", spy):
            sure_evaluator.SUREEvaluator(language="cs")._eval_asr(ref, hyp)
        self.assertTrue(seen)
        self.assertTrue(set(seen) <= {RULES_DIR / "en", RULES_DIR / "zh"}, seen)

    def test_preprocessor_defaults_to_the_language_directory(self) -> None:
        processor = sure_evaluator.SUREEvaluator(language="zh")._get_preprocessor("zh")
        self.assertEqual(Path(processor.map_dir), RULES_DIR / "zh")


if __name__ == "__main__":
    unittest.main()
