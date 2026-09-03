#!/usr/bin/env python3
"""Tests for the ASR text-normalization pipeline.

Run directly:
    cd sure/skills/sure_infer/scripts && python test_text_normalization.py
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sure_eval.evaluation.normalization import LANG_CLASSES, text_normalization  # noqa: E402
from sure_eval.evaluation.normalization.asr_simple_tn import get_n2w_map  # noqa: E402
from sure_eval.evaluation.normalization.base import (  # noqa: E402
    fun_convert_special_numbers_to_arabic,
    fun_normalize_nfkc,
)
from sure_eval.evaluation.normalization.lang_en import TextNormalization_EN  # noqa: E402
from sure_eval.evaluation.normalization.lang_zh import TextNormalization_ZH  # noqa: E402
from sure_eval.evaluation.normalization.utils import (  # noqa: E402
    replace_invisible_chars,
    simple_format_interval,
    simple_merge_intervals,
    simple_pattern_difference,
    str2bool,
    to_unicode_codepoints,
)

SCRIPTS_DIR = str(Path(__file__).resolve().parent)

DEGREE = "°"


def run_under_non_utf8_locale(snippet: str) -> subprocess.CompletedProcess:
    """Run a snippet in a child whose default encoding is not UTF-8.

    `open()` resolves its default encoding in C, below anything mock can reach,
    so a non-UTF-8 locale is the only way to make a missing `encoding=` visible.
    On Linux that needs the C locale with both UTF-8 escape hatches disabled;
    on Windows the ANSI code page is already non-UTF-8.
    """
    env = {
        **os.environ,
        "PYTHONUTF8": "0",
        "PYTHONCOERCECLOCALE": "0",
        "LC_ALL": "C",
        "LANG": "C",
        "PYTHONIOENCODING": "utf-8",
    }
    return subprocess.run(
        [sys.executable, "-c", snippet],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


class MapFileEncodingTest(unittest.TestCase):
    """Rule files are UTF-8 and must not be decoded through the process locale."""

    def test_reads_a_non_ascii_rule_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            map_file = Path(tmp) / "symbol.map"
            map_file.write_bytes(f"{DEGREE} | degrees\n".encode("utf-8"))

            self.assertEqual(get_n2w_map(str(map_file), "en"), [{DEGREE: " degrees "}])

    def test_reads_a_non_ascii_rule_under_a_non_utf8_locale(self):
        with tempfile.TemporaryDirectory() as tmp:
            map_file = Path(tmp) / "symbol.map"
            map_file.write_bytes(f"{DEGREE} | degrees\n".encode("utf-8"))

            # The snippet itself has to stay pure ASCII: under the C locale the
            # child decodes its own command line as ASCII, so a literal degree
            # sign here would kill it before it ever opens the map file.
            snippet = (
                "import sys\n"
                f"sys.path.insert(0, {ascii(SCRIPTS_DIR)})\n"
                "from sure_eval.evaluation.normalization.asr_simple_tn import get_n2w_map\n"
                f"print(list(get_n2w_map({ascii(str(map_file))}, 'en')[0]) == [{ascii(DEGREE)}])\n"
            )
            result = run_under_non_utf8_locale(snippet)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "True", msg=result.stderr)

    def test_shipped_english_symbol_rules_keep_the_degree_sign(self):
        rules = (
            Path(SCRIPTS_DIR)
            / "sure_eval/evaluation/normalization/asr_simple_tn_rules/en/symbol.map"
        )

        keys = [key for entry in get_n2w_map(str(rules), "en") for key in entry]

        self.assertIn(f"{DEGREE}C", keys)

    def test_shipped_chinese_symbol_rules_load(self):
        rules = (
            Path(SCRIPTS_DIR)
            / "sure_eval/evaluation/normalization/asr_simple_tn_rules/zh/symbol.map"
        )

        entries = get_n2w_map(str(rules), "zh")

        self.assertIn({"℃": "摄氏度"}, entries)


class LanguageIsolationTest(unittest.TestCase):
    """Configuring one language must not reach into another one's normalizer."""

    MIXED = "中文abc123"

    def test_chinese_output_survives_a_later_english_config(self):
        chinese, english = LANG_CLASSES["zh"], LANG_CLASSES["en"]
        chinese.config(language="zh", debug=0)
        before = chinese.pipeline(self.MIXED)

        english.config(language="en", debug=0)

        self.assertEqual(chinese.pipeline(self.MIXED), before)

    def test_chinese_keeps_its_own_characters_after_an_english_config(self):
        chinese, english = LANG_CLASSES["zh"], LANG_CLASSES["en"]
        chinese.config(language="zh", debug=0)
        english.config(language="en", debug=0)

        self.assertEqual(chinese.pipeline(self.MIXED), "中文abc一百二十三")


class Str2BoolTest(unittest.TestCase):
    def test_reads_the_documented_true_spellings(self):
        for value in ("yes", "true", "T", "y", "1"):
            with self.subTest(value=value):
                self.assertIs(str2bool(value), True)

    def test_reads_the_documented_false_spellings(self):
        for value in ("no", "false", "F", "n", "0"):
            with self.subTest(value=value):
                self.assertIs(str2bool(value), False)

    def test_passes_none_and_bools_straight_through(self):
        self.assertIsNone(str2bool(None))
        self.assertIs(str2bool(True), True)
        self.assertIs(str2bool(False), False)

    def test_rejects_a_value_that_is_neither(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            str2bool("maybe")


class NfkcNormalizationTest(unittest.TestCase):
    def test_folds_compatibility_forms_to_their_plain_characters(self):
        cases = {
            "①": "1",  # circled one
            "ＡＢ": "AB",  # full-width letters
            "＋": "+",  # full-width plus
            "ﬁ": "fi",  # fi ligature
            "Ⅳ": "IV",  # roman numeral four
            " ": " ",  # no-break space
            "　": " ",  # ideographic space
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(fun_normalize_nfkc(text), expected)

    def test_recomposes_a_base_letter_and_its_combining_accent(self):
        self.assertEqual(fun_normalize_nfkc("é"), "é")

    def test_recomposes_the_thai_sara_am_that_nfkc_pulls_apart(self):
        """NFKC decomposes U+0E33 and does not put it back; the manual map does."""
        self.assertEqual(fun_normalize_nfkc("กำ"), "กำ")

    def test_leaves_a_zero_width_space_alone(self):
        """Zero-width characters are `replace_invisible_chars`' job, not NFKC's."""
        self.assertEqual(fun_normalize_nfkc("​"), "​")


class SpecialDigitToArabicTest(unittest.TestCase):
    def test_converts_every_documented_script(self):
        cases = {
            "١٢": "12",  # Arabic-Indic
            "۳": "3",  # Extended Arabic-Indic
            "३": "3",  # Devanagari
            "১": "1",  # Bengali
            "๑": "1",  # Thai
            "༡": "1",  # Tibetan
            "੦": "0",  # Gurmukhi
            "൧": "1",  # Malayalam
            "෦": "0",  # Sinhala
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(fun_convert_special_numbers_to_arabic(text), expected)

    def test_leaves_text_without_foreign_digits_untouched(self):
        self.assertEqual(fun_convert_special_numbers_to_arabic("abc123"), "abc123")


class InvisibleCharTest(unittest.TestCase):
    def test_drops_zero_width_characters_without_leaving_a_gap(self):
        for char in ("​", "‌", "‍", "﻿", "᠋", "᠎", "⁠"):
            with self.subTest(char=char):
                self.assertEqual(replace_invisible_chars(f"a{char}b"), "ab")

    def test_turns_other_control_and_separator_characters_into_a_space(self):
        for char in ("", " ", " "):
            with self.subTest(char=char):
                self.assertEqual(replace_invisible_chars(f"a{char}b"), "a b")

    def test_keeps_tab_and_newline(self):
        self.assertEqual(replace_invisible_chars("a\tb\nc"), "a\tb\nc")


class PatternDifferenceTest(unittest.TestCase):
    def test_removes_a_single_code_point_from_a_range(self):
        mixed, unicode_only = simple_pattern_difference("a-e", "c")

        self.assertEqual(mixed, "a-bd-e")
        self.assertEqual(unicode_only, "\\u0061-\\u0062\\u0064-\\u0065")

    def test_spells_printable_ascii_as_characters_and_the_rest_as_escapes(self):
        mixed, unicode_only = simple_pattern_difference("a-zA-Z", "\\u00a0")

        self.assertEqual(mixed, "A-Za-z")
        self.assertEqual(unicode_only, "\\u0041-\\u005a\\u0061-\\u007a")

    def test_keeps_non_ascii_ranges_escaped_in_both_forms(self):
        mixed, unicode_only = simple_pattern_difference("\\u4e00-\\u4e05", "\\u4e02")

        self.assertEqual(mixed, "\\u4e00-\\u4e01\\u4e03-\\u4e05")
        self.assertEqual(mixed, unicode_only)

    def test_an_empty_pattern_yields_an_empty_difference(self):
        self.assertEqual(simple_pattern_difference("", "a"), ("", ""))

    def test_merges_only_adjacent_code_points(self):
        self.assertEqual(simple_merge_intervals({1, 2, 3, 7, 8, 20}), [(1, 3), (7, 8), (20, 20)])

    def test_merging_nothing_yields_no_intervals(self):
        self.assertEqual(simple_merge_intervals(set()), [])

    def test_a_one_character_interval_is_written_without_a_dash(self):
        self.assertEqual(simple_format_interval((65, 65), use_chars=True), "A")


class CodepointFormattingTest(unittest.TestCase):
    def test_formats_a_character_as_an_escape(self):
        self.assertEqual(to_unicode_codepoints(["…"]), "\\u2026")

    def test_formats_an_integer_as_the_same_escape(self):
        self.assertEqual(to_unicode_codepoints([0x2026]), "\\u2026")

    def test_expands_every_character_of_a_longer_string(self):
        self.assertEqual(to_unicode_codepoints(["ab"]), "\\u0061\\u0062")

    def test_rejects_a_type_it_cannot_read(self):
        with self.assertRaises(TypeError):
            to_unicode_codepoints([1.5])


def english_normalizer(**options):
    normalizer = TextNormalization_EN()
    normalizer.config(language="en", debug=0, **options)
    return normalizer


def chinese_normalizer(**options):
    normalizer = TextNormalization_ZH()
    normalizer.config(language="zh", debug=0, **options)
    return normalizer


class EnglishPipelineTest(unittest.TestCase):
    def setUp(self):
        self.normalizer = english_normalizer()

    def test_spells_out_a_bare_number(self):
        self.assertEqual(self.normalizer.pipeline("I have 3 cats"), "I have three cats")

    def test_spells_out_a_decimal_point(self):
        self.assertEqual(self.normalizer.pipeline("3.14 pi"), "three point one four pi")

    def test_drops_thousands_separators_before_spelling_the_number(self):
        self.assertEqual(
            self.normalizer.pipeline("1,234,567 items"),
            "one million two hundred and thirty-four thousand five hundred and sixty-seven items",
        )

    def test_reads_a_leading_zero_run_digit_by_digit(self):
        self.assertEqual(self.normalizer.pipeline("007 agent"), "zero zero seven agent")

    def test_spells_out_an_ordinal(self):
        self.assertEqual(self.normalizer.pipeline("I am 1st"), "I am first")
        self.assertEqual(self.normalizer.pipeline("the 2nd time"), "the second time")

    def test_spells_out_a_dollar_amount(self):
        self.assertEqual(
            self.normalizer.pipeline("it costs $12.50"), "it costs twelve dollars fifty cents"
        )

    def test_spells_out_a_decade(self):
        self.assertEqual(self.normalizer.pipeline("the 1990s"), "the nineteen nineties")

    def test_reads_a_phone_number_digit_by_digit(self):
        self.assertEqual(
            self.normalizer.pipeline("call 555-0199"), "call five five five zero one nine nine"
        )

    def test_reads_a_full_phone_number_digit_by_digit(self):
        self.assertEqual(
            self.normalizer.pipeline("call 123-456-7890"),
            "call one two three four five six seven eight nine zero",
        )

    def test_reads_a_negative_number_as_minus(self):
        self.assertEqual(self.normalizer.pipeline("it is -5 degrees"), "it is minus five degrees")

    def test_reads_a_foot_mark_after_a_digit(self):
        self.assertEqual(self.normalizer.pipeline("he is 6' tall"), "he is six feet tall")

    def test_applies_the_shipped_symbol_rules(self):
        self.assertEqual(
            self.normalizer.pipeline("25°C outside"), "twenty-five degrees Celsius outside"
        )
        self.assertEqual(self.normalizer.pipeline("100% sure"), "one hundred percent sure")
        self.assertEqual(self.normalizer.pipeline("a & b"), "a and b")

    def test_applies_the_shipped_time_rules(self):
        self.assertEqual(
            self.normalizer.pipeline("10:00 a.m. meeting"), "ten o'clock am meeting"
        )

    def test_keeps_a_hyphen_that_joins_two_words(self):
        self.assertEqual(self.normalizer.pipeline("well-known word"), "well-known word")

    def test_keeps_an_apostrophe_inside_a_word(self):
        self.assertEqual(self.normalizer.pipeline("it's fine"), "it's fine")

    def test_strips_a_dangling_hyphen(self):
        self.assertEqual(self.normalizer.pipeline("-lead"), "lead")
        self.assertEqual(self.normalizer.pipeline("trail-"), "trail")
        self.assertEqual(self.normalizer.pipeline("x - y"), "x y")

    def test_strips_a_dangling_apostrophe(self):
        self.assertEqual(self.normalizer.pipeline("'lead"), "lead")
        self.assertEqual(self.normalizer.pipeline("trail'"), "trail")

    def test_collapses_runs_of_whitespace(self):
        self.assertEqual(self.normalizer.pipeline("  extra   spaces  "), "extra spaces")

    def test_an_input_of_only_punctuation_normalizes_to_nothing(self):
        for text in ("", "   ", "!!!"):
            with self.subTest(text=text):
                self.assertEqual(self.normalizer.pipeline(text), "")


class ChinesePipelineTest(unittest.TestCase):
    def setUp(self):
        self.normalizer = chinese_normalizer()

    def test_spells_out_a_small_number_in_chinese(self):
        self.assertEqual(self.normalizer.pipeline("我有3只猫"), "我有三只猫")

    def test_spells_out_a_larger_number_in_chinese(self):
        self.assertEqual(self.normalizer.pipeline("ABC混合123"), "ABC混合一百二十三")

    def test_applies_the_shipped_chinese_symbol_rules(self):
        self.assertEqual(self.normalizer.pipeline("气温25℃"), "气温二十五摄氏度")

    def test_reads_a_date(self):
        self.assertEqual(
            self.normalizer.pipeline("日期2023-10-27"), "日期二千零二十三年十月二十七日"
        )

    def test_reads_a_percentage(self):
        self.assertEqual(self.normalizer.pipeline("增长50%"), "增长百分之五十")

    def test_reads_a_decimal_percentage_without_dropping_the_point(self):
        self.assertEqual(self.normalizer.pipeline("下降12.5%"), "下降百分之十二点五")

    def test_reads_a_decimal_without_dropping_the_point(self):
        self.assertEqual(self.normalizer.pipeline("价格99.5元"), "价格九十九点五元")

    def test_reads_a_fraction(self):
        self.assertEqual(self.normalizer.pipeline("1/4的学生缺席"), "四分之一的学生缺席")

    def test_reads_a_negative_number(self):
        self.assertEqual(self.normalizer.pipeline("温度-5度"), "温度负五度")

    def test_reads_an_attached_unit(self):
        self.assertEqual(self.normalizer.pipeline("重75kg"), "重七十五千克")

    def test_folds_full_width_digits_before_spelling_them(self):
        self.assertEqual(self.normalizer.pipeline("全角１２３"), "全角一百二十三")

    def test_folds_arabic_indic_digits_before_spelling_them(self):
        self.assertEqual(self.normalizer.pipeline("阿拉伯١٢٣"), "阿拉伯一百二十三")

    def test_drops_ideographic_spaces(self):
        self.assertEqual(self.normalizer.pipeline("　全角空格　"), "全角空格")

    def test_does_not_insert_spaces_between_chinese_tokens(self):
        """`spaced_writing` is False for Chinese, so removals collapse to nothing."""
        self.assertEqual(self.normalizer.pipeline("测试。逗号，句号"), "测试逗号句号")


class PipelineOptionTest(unittest.TestCase):
    def test_remove_brackets_drops_the_bracketed_span(self):
        self.assertEqual(english_normalizer().pipeline("a (b) c"), "a b c")
        self.assertEqual(english_normalizer(remove_brackets=True).pipeline("a (b) c"), "a c")

    def test_remove_dashes_also_splits_compound_words(self):
        self.assertEqual(
            english_normalizer(remove_dashes=True).pipeline("well-known x - y"), "well known x y"
        )

    def test_remove_single_quotes_also_splits_contractions(self):
        self.assertEqual(
            english_normalizer(remove_single_quotes=True).pipeline("it's fine"), "it s fine"
        )

    def test_case_folds_in_both_directions(self):
        self.assertEqual(english_normalizer(case="upper").pipeline("Hello"), "HELLO")
        self.assertEqual(english_normalizer(case="lower").pipeline("Hello"), "hello")
        self.assertEqual(english_normalizer().pipeline("Hello"), "Hello")

    def test_remove_lines_drops_a_line_carrying_a_foreign_script(self):
        self.assertEqual(english_normalizer(remove_lines=True).pipeline("hello 中文"), "")
        self.assertEqual(english_normalizer().pipeline("hello 中文"), "hello")

    def test_remove_lines_drops_a_line_with_four_identical_letters(self):
        self.assertEqual(english_normalizer(remove_lines=True).pipeline("aaaa test"), "")
        self.assertEqual(english_normalizer().pipeline("aaaa test"), "aaaa test")

    def test_remove_lines_counts_what_it_dropped(self):
        normalizer = english_normalizer(remove_lines=True)

        for text in ("!!!", "ok", "???"):
            normalizer.pipeline(text)

        self.assertEqual(normalizer.num_removed_lines, 2)

    def test_remove_not_word_off_keeps_stray_punctuation(self):
        self.assertEqual(english_normalizer(remove_not_word=False).pipeline("a#b"), "a#b")
        self.assertEqual(english_normalizer().pipeline("a#b"), "a b")


class FileEntryPointTest(unittest.TestCase):
    def normalize_file(self, body: str, **options) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "in.txt"
            target = Path(tmp) / "out.txt"
            source.write_text(body, encoding="utf-8")

            text_normalization(str(source), str(target), debug=0, **options)

            return target.read_text(encoding="utf-8")

    def test_normalizes_one_line_per_line(self):
        result = self.normalize_file(
            "Hello 5\nWorld\n", language="en", with_id_opt=0, keep_empty_lines=1
        )

        self.assertEqual(result, "Hello five\nWorld\n")

    def test_keeps_the_utterance_id_in_front(self):
        result = self.normalize_file(
            "utt1 Hello 5\nutt2 World\n", language="en", with_id_opt=1, keep_empty_lines=1
        )

        self.assertEqual(result, "utt1 Hello five\nutt2 World\n")

    def test_keep_empty_lines_decides_whether_an_emptied_line_survives(self):
        body = "Hello\n!!!\nWorld\n"

        kept = self.normalize_file(body, language="en", with_id_opt=0, keep_empty_lines=1)
        dropped = self.normalize_file(body, language="en", with_id_opt=0, keep_empty_lines=0)

        self.assertEqual(kept, "Hello\n\nWorld\n")
        self.assertEqual(dropped, "Hello\nWorld\n")

    def test_rejects_a_language_without_a_normalizer(self):
        with self.assertRaises(ValueError):
            self.normalize_file("x\n", language="fr", with_id_opt=0, keep_empty_lines=1)

    def test_reads_a_utf8_bom_without_leaking_it_into_the_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "in.txt"
            target = Path(tmp) / "out.txt"
            source.write_bytes("﻿Hello\n".encode("utf-8"))

            text_normalization(
                str(source), str(target), language="en", with_id_opt=0,
                keep_empty_lines=1, debug=0,
            )

            self.assertEqual(target.read_text(encoding="utf-8"), "Hello\n")


if __name__ == "__main__":
    unittest.main()
