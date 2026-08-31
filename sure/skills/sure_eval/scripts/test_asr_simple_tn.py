#!/usr/bin/env python3
"""Tests for the number/symbol rules behind ASR text normalization.

`test_text_normalization.py` covers the pipeline end to end; this file pins
down the rule engine underneath it, including the defects it currently has.

Run directly:
    cd sure/skills/sure_eval/scripts && python test_asr_simple_tn.py
"""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sure_eval.evaluation.normalization import asr_simple_tn as tn  # noqa: E402
from sure_eval.evaluation.normalization.lang_en import TextNormalization_EN  # noqa: E402

RULES_DIR = Path(tn.__file__).parent / "asr_simple_tn_rules"


def write_map(directory: Path, name: str, body: str) -> str:
    path = directory / name
    path.write_bytes(body.encode("utf-8"))
    return str(path)


class MapFileParsingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_splits_on_a_pipe(self):
        path = write_map(self.tmp, "a.map", "x | y\n")

        self.assertEqual(tn.get_n2w_map(path, "en"), [{"x": " y "}])

    def test_falls_back_to_whitespace_when_there_is_no_pipe(self):
        path = write_map(self.tmp, "a.map", "x y\n")

        self.assertEqual(tn.get_n2w_map(path, "en"), [{"x": " y "}])

    def test_collapses_tabs_and_repeated_spaces(self):
        path = write_map(self.tmp, "a.map", "x\t\t|\ty\n")

        self.assertEqual(tn.get_n2w_map(path, "en"), [{"x": " y "}])

    def test_skips_comments_and_blank_lines(self):
        path = write_map(self.tmp, "a.map", "# a comment\n\nx | y\n")

        self.assertEqual(tn.get_n2w_map(path, "en"), [{"x": " y "}])

    def test_pads_the_replacement_for_a_spaced_language(self):
        path = write_map(self.tmp, "a.map", "x | y\n")

        self.assertEqual(tn.get_n2w_map(path, "en"), [{"x": " y "}])

    def test_does_not_pad_the_replacement_for_a_scriptio_continua_language(self):
        path = write_map(self.tmp, "a.map", "x | y\n")

        for language in ("zh", "zh_cn", "zh_CN", "ja", "ar"):
            with self.subTest(language=language):
                self.assertEqual(tn.get_n2w_map(path, language), [{"x": "y"}])

    def test_returns_nothing_for_a_file_that_is_not_there(self):
        self.assertIsNone(tn.get_n2w_map(str(self.tmp / "missing.map"), "en"))

    def test_a_malformed_line_kills_the_process(self):
        """get_n2w_map calls exit() on a bad line. A library has no business
        doing that, but the behaviour is pinned until someone changes it."""
        path = write_map(self.tmp, "a.map", "one two three\n")

        with self.assertRaises(SystemExit):
            tn.get_n2w_map(path, "en")


class MapSortingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_sorts_the_longest_key_first(self):
        """A shorter key that is a prefix of a longer one would otherwise eat it."""
        path = write_map(self.tmp, "a.map", "a | short\naaa | long\naa | middle\n")

        pairs = tn.load_and_sort_map(path, "en")

        self.assertEqual([key for key, _ in pairs], ["aaa", "aa", "a"])

    def test_an_empty_file_sorts_to_nothing(self):
        path = write_map(self.tmp, "a.map", "# only a comment\n")

        self.assertEqual(tn.load_and_sort_map(path, "en"), [])

    def test_the_fallback_path_loads_its_own_rules(self):
        """asr_num2words has to work when the caller passes no pre-sorted rules."""
        result = tn.asr_num2words("100% sure", "en", str(RULES_DIR / "en"), 0)

        self.assertEqual(result, " one hundred  percent  sure")

    def test_the_fallback_path_loads_them_for_the_right_language(self):
        """Chinese replacements must not be padded: the padding would put spaces
        into a script that is written without them."""
        result = tn.asr_num2words("气温25℃", "zh", str(RULES_DIR / "zh"), 0)

        self.assertEqual(result, "气温二十五摄氏度")


class TnReplaceTest(unittest.TestCase):
    def test_replaces_a_plain_key_anywhere(self):
        self.assertEqual(tn.tn_replace("a%b", "%", " percent "), "a percent b")

    def test_honours_word_boundaries_when_the_key_asks_for_them(self):
        self.assertEqual(tn.tn_replace("say kg now", r"\bkg\b", "kilo"), "say kilo now")

    def test_a_bounded_key_does_not_fire_inside_a_word(self):
        self.assertEqual(tn.tn_replace("skgs", r"\bkg\b", "kilo"), "skgs")


class LanguageSupportTest(unittest.TestCase):
    def test_knows_which_languages_num2words_covers(self):
        for language in ("en", "zh", "zh_cn", "zh_CN"):
            with self.subTest(language=language):
                self.assertTrue(tn.check_language(language))

        self.assertFalse(tn.check_language("fr"))

    def test_spells_a_number_in_a_supported_language(self):
        self.assertEqual(tn.num2words_fun(5, "en"), "five")
        self.assertEqual(tn.num2words_fun(5, "zh_CN"), "五")

    def test_returns_the_digits_unchanged_for_an_unsupported_language(self):
        self.assertEqual(tn.num2words_fun(5, "fr"), "5")

    def test_recognises_a_positive_integer(self):
        self.assertTrue(tn.is_positive_integer("5"))
        self.assertTrue(tn.is_positive_integer(5))
        self.assertFalse(tn.is_positive_integer("0"))
        self.assertFalse(tn.is_positive_integer("a"))


class NumberRegexTest(unittest.TestCase):
    def matches(self, text):
        return [m.group() for m in re.finditer(tn.NUM_REGEX, text)]

    def test_keeps_a_grouped_number_with_its_decimals_together(self):
        self.assertEqual(self.matches("1,234.56"), ["1,234.56"])

    def test_requires_three_digits_after_a_thousands_comma(self):
        self.assertEqual(self.matches("1,23"), ["1", "23"])

    def test_stops_at_the_second_decimal_point(self):
        self.assertEqual(self.matches("1.2.3"), ["1.2", "3"])

    def test_keeps_leading_zeros(self):
        self.assertEqual(self.matches("007"), ["007"])


class EnglishPreprocessTest(unittest.TestCase):
    def test_expands_an_ordinal(self):
        self.assertEqual(tn.preprocess_en_text("1st"), "first")
        self.assertEqual(tn.preprocess_en_text("12th"), "twelfth")

    def test_expands_a_currency_amount(self):
        self.assertEqual(tn.preprocess_en_text("$12.50"), "twelve dollars, fifty cents")
        self.assertEqual(tn.preprocess_en_text("$1,000"), "one thousand dollars")

    def test_expands_a_decade(self):
        self.assertEqual(tn.preprocess_en_text("1990s"), "nineteen nineties")

    def test_reads_a_phone_number_digit_by_digit(self):
        self.assertEqual(
            tn.preprocess_en_text("555-0199"), "five five five   zero one nine nine"
        )

    def test_marks_a_negative_number_and_leaves_the_digits_for_later(self):
        self.assertEqual(tn.preprocess_en_text("-5"), "minus 5")

    def test_expands_a_foot_mark(self):
        self.assertEqual(tn.preprocess_en_text("6'"), "6 feet")


class ChinesePreprocessTest(unittest.TestCase):
    def test_spells_a_number_below_one_hundred(self):
        self.assertEqual(tn.preprocess_zh_text("12"), "十二")
        self.assertEqual(tn.preprocess_zh_text("99"), "九十九")

    def test_leaves_a_number_of_one_hundred_or_more_to_the_main_pass(self):
        self.assertEqual(tn.preprocess_zh_text("100"), "100")

    def test_leaves_a_decimal_whole_to_the_main_pass(self):
        """Spelling out the halves separately would drop the point between them."""
        self.assertEqual(tn.preprocess_zh_text("12.5"), "12.5")
        self.assertEqual(tn.preprocess_zh_text("99.5"), "99.5")

    def test_expands_a_degree_sign(self):
        self.assertEqual(tn.preprocess_zh_text("25°C"), "二十五摄氏度")

    def test_rewrites_a_numeric_date(self):
        self.assertEqual(tn.preprocess_zh_text("2023-10-27"), "2023年十月二十七日")
        self.assertEqual(tn.preprocess_zh_text("2023/10/27"), "2023年十月二十七日")

    def test_rewrites_a_fraction_back_to_front(self):
        self.assertEqual(tn.preprocess_zh_text("1/2"), "二分之一")

    def test_rewrites_a_percentage(self):
        self.assertEqual(tn.preprocess_zh_text("50%"), "百分之五十")

    def test_rewrites_a_negative_number(self):
        self.assertEqual(tn.preprocess_zh_text("-5"), "负五")

    def test_expands_an_attached_unit(self):
        self.assertEqual(tn.preprocess_zh_text("75kg"), "七十五千克")
        self.assertEqual(tn.preprocess_zh_text("3km"), "三千米")

    def test_expands_a_squared_and_cubed_unit(self):
        self.assertEqual(tn.preprocess_zh_text("100m2"), "100平方米")
        self.assertEqual(tn.preprocess_zh_text("5m3"), "五立方米")


class NumberConversionTest(unittest.TestCase):
    def convert(self, text, maxlen=12, language="en"):
        return tn.asr_num2words(text, language, str(RULES_DIR), 0, [], [], {}, maxlen)

    def test_spells_an_integer_with_padding_for_a_spaced_language(self):
        self.assertEqual(self.convert("5"), " five ")

    def test_drops_a_thousands_comma_before_spelling(self):
        self.assertEqual(
            self.convert("1,234"), " one thousand, two hundred and thirty-four "
        )

    def test_spells_a_decimal(self):
        self.assertEqual(self.convert("3.5"), " three point five ")

    def test_ignores_a_trailing_period(self):
        self.assertEqual(self.convert("5."), " five ")

    def test_leaves_a_number_longer_than_the_threshold_alone(self):
        self.assertEqual(self.convert("1234567890123"), "1234567890123")

    def test_the_threshold_is_configurable(self):
        self.assertEqual(self.convert("12345", maxlen=4), "12345")
        self.assertEqual(
            self.convert("12345", maxlen=12),
            " twelve thousand, three hundred and forty-five ",
        )

    def test_does_not_pad_for_a_scriptio_continua_language(self):
        self.assertEqual(self.convert("5", language="zh"), "五")

    def test_reuses_the_cache_for_a_repeated_number(self):
        cache = {}

        tn.asr_num2words("42 and 42", "en", str(RULES_DIR), 0, [], [], cache, 12)

        self.assertEqual(cache, {"42": " forty-two "})


class KnownDefectTest(unittest.TestCase):
    """Behaviour that is wrong but shipped.

    Each test pins what the code does today, so a change shows up as a diff
    instead of a silent shift in evaluation scores. The docstring says what the
    right answer would be. None of these should be "fixed" by editing the
    expectation - they need a decision about the rules themselves.
    """

    def test_a_number_too_long_to_spell_is_deleted_instead_of_kept(self):
        """asr_num2words logs "failed to convert" and leaves the digits in
        place, which reads as "the number survives unconverted". It does not:
        english_word_pattern has no digits in it, so the very next step of the
        pipeline removes them as non-word characters and the number is gone
        from both the reference and the hypothesis. Keeping it would mean
        deciding what an unconvertible number should read as."""
        normalizer = TextNormalization_EN()
        normalizer.config(language="en", debug=0)

        self.assertEqual(normalizer.pipeline("12345678901234 long"), "long")

    def test_a_word_boundary_rule_never_fires_next_to_chinese(self):
        """The five backslash-b anchored unit rules in zh/symbol.map are dead."""
        self.assertEqual(
            tn.tn_replace("五kg", r"\bkg\b", "千克"), "五kg"
        )

    def test_two_adjacent_chinese_numerals_are_read_as_one_number(self):
        """The digit-by-digit rule runs before anything else and cannot tell a
        spelled-out reading from an idiomatic pair, so a rough "three or four"
        is read as thirty-four. Telling them apart needs context this function
        does not have."""
        self.assertEqual(tn.preprocess_zh_text("三四"), "三十四")
        self.assertEqual(tn.preprocess_zh_text("七八"), "七十八")
        self.assertEqual(tn.preprocess_zh_text("一五"), "十五")


if __name__ == "__main__":
    unittest.main()
