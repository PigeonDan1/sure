import codecs,os,sys,io
import argparse
import unicodedata
import re
from .logger import logger
from .utils import replace_invisible_chars, simple_pattern_difference
from .asr_simple_tn import asr_num2words, get_n2w_map

# 原则上，将字符分成以下几类：
# (1) 字母表字符：alphabet_pattern 该语种字符集
# (2) 英文字符： english_word_pattern / english_letter_pattern
#     ASCII字符：包含英文字符和数字
# (3) 标点符号: marks_pattern
# (4) 辅助字符：diacritic_pattern 仅部分语种有，例如希伯来语
# (5) 非法字符：其余字符为非法字符

alphabet_pattern='a-zA-Z' # 由各语种单独定义
diacritic_pattern=''        # 由各语种单独定义

# ascii字符的pattern
ascii_pattern='\\u0020-\\u007f'

# 英文字符的pattern
english_word_pattern='\\sa-zA-Z\\x27\\x2d' # 单引号\x27, dash- \x2d 
english_letter_pattern='a-zA-Z'

# 标点符号的pattern：标点替换为空格或空
marks_pattern_category = {
    "latin"      : '\\u00a0\\u00a1\\u00aa\\u00ab\\u00b0\\u00b4\\u00b7\\u00ba\\u00bb\\u00bf', # 拉丁语系常见标点：¡ª«°´·º»¿
    "combine"    : '\\u0300-\\u036F', # Combining Diacritical Marks，ˋ、ˊ、¨、～。给已有字符加修饰（如重音、鼻音、长音），支持拉丁语系、希腊语、法语等（如 “è”=“e”+\u0300）。
    "hebrew"     : '\\u05f3-\\u05f4',  # Hebrew Marks. ׳、״
    "armenian"   : '\\u055a-\\u055f\\u0589',  # ՚՛՜՝՞՟։ 
    "arabic"     : '\\u0600-\\u061f\\u0656-\\u065f\\u066a-\\u066d\\u06d4', # Arabic Marks. ،؍؛؟۔ https://unicodeplus.com/script/Arab
    "devanagari" : '\\u0964-\\u0965', #  । ॥ 
    "telugu"     : '\\u0c64-\\u0c65', # 
    "lao"        : '\\u0e4f\\u0e5a\\u0e5b', # ๏ ๚ ๛
    "sinhala"    : '\\u0df4', # ෴
    "myanmar"    : '\\u104a-\\u104f',  #  ၊ ။ ၌ ၍ ၎ ၏
    "amharic"    : '\\u1361-\\u1368',  #  ፡።  ፣ ፤ ፥ ፦ ፧፨
    "khmer"      : '\\u17d4-\\u17d7',  # ។ ៕ ៖ ៗ
    "common"     : '\\u2000-\\u206F\\uA788-\\uA78C\\uA78F', # 包含各种空格（如全角空格）、省略号、破折号等，所有语言通用（如…= 省略号）
    "math"       : '\\u2212\\u2213\\u2214\\u2217\\u2219\\u2219\\u22C5\\u22CF', # 数学符号 −、±、∓
    "drawing"    : '\\u2500-\\u257F', # 用于绘制表格、边框（如 “─”= 横线，“│”= 竖线），所有场景通用（如文本表格）。 
    "japanese"   : '\\u3001-\\u3003\\u3008-\\u301F\\u3030\\u303b\\u309d\\u309e\\u30fb\\u30fd\\u30fe', # Japanese Marks. 、。〃〈〉「」〰〻ゝゞ・ㇽㇾ
    "full_width" : '\\uFF01-\\uFF0F\\uFF1A-\\uFF20\\uFF3B-\\uFF40\\uFF5B-\\uFF65', # 全角标点 
}

marks_pattern = ''.join(marks_pattern_category.values()) 
# see: https://www.fuhaoku.net/blocks

# 以下函数需要被某些语种的Pattern定义时调用，放在类外面
# NFKC正规化 
def fun_normalize_nfkc(text: str, debug: int = 0):
    """
    Unicode 的 NFKC 正规化（Normalization Form KC），Compatibility Decomposition, followed by Canonical Composition（兼容性分解后进行标准组合），
    核心作用是将 “视觉或功能上等效但编码不同” 的字符统一为标准形式，解决 “同形异码” 问题。
    具体会做以下 4 类关键处理：
    - 带圈数字（如 ①，Unicode U+2460）→ 拆分为基础数字 1（U+0031）+ 圆圈修饰（但最终会被进一步处理为纯数字 1）
    - 全角字母（如 Ａ，Unicode U+FF21）→ 拆分为半角基础字母 A（U+0041）
    - 宽体符号（如 ＋，Unicode U+FF0B）→ 拆分为标准符号 +（U+002B）
    - 连字字符（如 ﬁ，Unicode U+FB01，“f” 和 “i” 的连写）→ 拆分为基础字母 f（U+0066）+ i（U+0069）
    - 基础字符 e（U+0065）+ 组合尖音符 ´（U+0301）→ 合并为预组合字符 é（U+00E9）
    - 移除无实际视觉显示的控制字符如 零宽空格（U+200B）    
    - 罗马数字符号（如 Ⅳ，U+2163，表示 “4”）→ 拆分为标准数字 4（U+0034）；
    - 特殊符号变体（如 ⅓，U+2153，表示 “1/3”）→ 拆分为标准字符 1（U+0031）+ /（U+002F）+ 3（U+0033）；
    - 不同编码的空格（如非断行空格 U+00A0）→ 统一为标准空格 U+0020（部分场景保留，但多数兼容场景会归一）。
    """
    text2 = unicodedata.normalize("NFKC", text)

    # NFKC会先分解再预组合，有时候预组合不回去。必须经过NFC预组合回去
    # 例如泰语的"กำ" (U+0e01 U+0e33) 经NFKC处理后为"กํา" (U+0e01 U+0e4d U+0e32),
    # 视觉上不一样了(但某些字体下仍然视觉一样)
    # 手动映射（兜底），这个方案很丑，但是目前没搞懂正规化的机制，只能如此打补丁
    manual_mapping = { 
        '\u0e4d\u0e32' : '\u0e33' ,
    }
    for decomposed, composed in manual_mapping.items():
        text2 = text2.replace(decomposed, composed)

    if debug > 0 and text != text2:
        logger.debug(f"fun_i: {text}")
        logger.debug(f"fun_o: {text2}")
    if debug == 2 and text != text2:
        logger.debug(f"fun_i: ")
        for ch in text: logger.debug(f"char = {ch} \t unicode = {ord(ch):04x}")
        logger.debug(f"fun_o: ")
        for ch in text2: logger.debug(f"char = {ch} \t unicode = {ord(ch):04x}")

    return text2

def fun_convert_special_numbers_to_arabic(text: str, debug: int = 0) -> str:
    """
    将全球特殊数字字符转换为西阿拉伯数字:
    阿拉伯语    ٠ ١ ٢ ٣ ٤ ٥ ٦ ٧ ٨ ٩ 0 1 2 3 4 5 6 7 8 9
    波斯语 / 乌尔都语   ۰ ۱ ۲ ۳ ۴ ۵ ۶ ۷ ۸ ۹ 0 1 2 3 4 5 6 7 8 9
    印地语  ० १ २ ३ ४ ५ ६ ७ ८ ९ 0 1 2 3 4 5 6 7 8 9
    泰语    ๐ ๑ ๒ ๓ ๔ ๕ ๖ ๗ ๘ ๙ 0 1 2 3 4 5 6 7 8 9
    藏语    ༠ ༡ ༢ ༣ ༤ ༥ ༦ ༧ ༨ ༩ 0 1 2 3 4 5 6 7 8 9
    孟加拉语    ০ ১ ২ ৩ ৪ ৫ ৬ ৭ ৮ ৯ 0 1 2 3 4 5 6 7 8 9    
    """
    special_num_map = {
        # 阿拉伯语数字（U+0660-U+0669）
        '\u0660': '0', '\u0661': '1', '\u0662': '2', '\u0663': '3', '\u0664': '4', '\u0665': '5', '\u0666': '6', '\u0667': '7', '\u0668': '8', '\u0669': '9',
        # 波斯语/乌尔都语数字（U+06F0-U+06F9）
        '\u06F0': '0', '\u06F1': '1', '\u06F2': '2', '\u06F3': '3', '\u06F4': '4', '\u06F5': '5', '\u06F6': '6', '\u06F7': '7', '\u06F8': '8', '\u06F9': '9',
        # 印地语/天城文数字（U+0966-U+096F）
        '\u0966': '0', '\u0967': '1', '\u0968': '2', '\u0969': '3', '\u096A': '4', '\u096B': '5', '\u096C': '6', '\u096D': '7', '\u096E': '8', '\u096F': '9',
        # 孟加拉语数字（U+09E6-U+09EF）
        '\u09E6': '0', '\u09E7': '1', '\u09E8': '2', '\u09E9': '3', '\u09EA': '4', '\u09EB': '5', '\u09EC': '6', '\u09ED': '7', '\u09EE': '8', '\u09EF': '9',
        # 泰语/老挝语数字（U+0E50-U+0E59）
        '\u0E50': '0', '\u0E51': '1', '\u0E52': '2', '\u0E53': '3', '\u0E54': '4', '\u0E55': '5', '\u0E56': '6', '\u0E57': '7', '\u0E58': '8', '\u0E59': '9',
        # 藏语数字（U+0F20-U+0F29）
        '\u0F20': '0', '\u0F21': '1', '\u0F22': '2', '\u0F23': '3', '\u0F24': '4', '\u0F25': '5', '\u0F26': '6', '\u0F27': '7', '\u0F28': '8', '\u0F29': '9',
        # 旁遮普语数字（U+0A66-U+0A6F）
        '\u0A66': '0', '\u0A67': '1', '\u0A68': '2', '\u0A69': '3', '\u0A6A': '4', '\u0A6B': '5', '\u0A6C': '6', '\u0A6D': '7', '\u0A6E': '8', '\u0A6F': '9',
        # 马拉雅拉姆语数字（U+0D66-U+0D6F）
        '\u0D66': '0', '\u0D67': '1', '\u0D68': '2', '\u0D69': '3', '\u0D6A': '4', '\u0D6B': '5', '\u0D6C': '6', '\u0D6D': '7', '\u0D6E': '8', '\u0D6F': '9',
        # 僧伽罗语数字（U+0DE6-U+0DEF）
        '\u0DE6': '0', '\u0DE7': '1', '\u0DE8': '2', '\u0DE9': '3', '\u0DEA': '4', '\u0DEB': '5', '\u0DEC': '6', '\u0DED': '7', '\u0DEE': '8', '\u0DEF': '9'
    }

    text2 = text.translate(str.maketrans(special_num_map))
    if debug > 0 and text != text2:
        logger.debug(f"fun_i: {text}")
        logger.debug(f"fun_o: {text2}")

    return text2



# 公共正则、辅助函数、基类
class TextNormalization_Base:
    def __init__(self):
        # 这里可以初始化通用的正则表达式、变量等
        self.is_tts_pipeline = False
        self.language = "xx"
        self.language_name_en = "XXX"
        self.language_name_zh = "XX语"
        self.alphabet_pattern = alphabet_pattern
        self.diacritic_pattern = diacritic_pattern
        self.ascii_pattern = ascii_pattern
        self.english_word_pattern = english_word_pattern
        self.english_letter_pattern = english_letter_pattern
        self.marks_pattern = marks_pattern

        # 获取同目录下的asr_simple_tn_rules目录
        base_dir = os.path.dirname(os.path.abspath(__file__))
        asr_simple_tn_rules_dir = os.path.join(base_dir, "asr_simple_tn_rules", self.language)
        self.map_dir = asr_simple_tn_rules_dir

        # 定义所有配置项的默认值
        self.spaced_writing = True # 绝大多数语种为有间隔书写语言（英文：Spaced Writing System 或 Segmental Writing System），无间隔书写语言仅包含中文、日语、泰语、老挝语、缅甸语等。
        self.score_method = "WER"  # 但少数语种建议按(CER, Character Error Rate)而不是WER(Word Error Rate)计算字错
        self.script = ""           # 每个语种的书写文字分类(latin, arabic, cyrillic, devanagari, brahmic, cj, ethiopic), https://worldschoolbooks.com/the-most-widely-used-scripts/ 
        self.allow_leading_single_quote = False # 荷兰语、豪萨语允许开头出现单引号
        self.debug = False
        self.case = ""
        self.normalize_text = True
        self.keep_empty_lines = True
        self.keep_english_letters = True
        self.remove_lines = False            # 默认不会整行删除
        self.remove_brackets = False
        self.remove_diacritic = True
        self.remove_not_ascii_lang_mark = True
        self.remove_not_word = True
        self.remove_dashes = False
        self.remove_single_quotes = False
        self.normalize_digit_maxlen = 12

        # 统计值
        self.num_removed_lines = 0

        self.cached_num_map = {}
        self.digit_map_sorted = []
        self.other_map_sorted = []

    def _load_maps(self):

        if not self.map_dir:
            return

        # 1. 扫描所有 .map 文件
        import glob, os
        map_files = glob.glob(os.path.join(self.map_dir, "*.map"))
        digit_map_file = None
        other_map_files = []
        for mf in map_files:
            if os.path.basename(mf) == "digit.map":
                digit_map_file = mf
            else:
                other_map_files.append(mf)

        # 2. 读取映射并排序
        def load_and_sort_map(map_file, language):
            n2w_map = get_n2w_map(map_file, language)
            if not n2w_map:
                return []
            pairs = []
            for item in n2w_map:
                for k, v in item.items():
                    pairs.append((k, v))
            pairs.sort(key=lambda x: len(x[0]), reverse=True)
            return pairs

        # 3. 先用 other 类映射替换 text
        self.other_map_sorted = []
        for mf in other_map_files:
            self.other_map_sorted.extend(load_and_sort_map(mf, self.language))
        self.other_map_sorted.sort(key=lambda x: len(x[0]), reverse=True)

        # 4. digit.map
        self.digit_map_sorted = []
        if digit_map_file:
            n2w_map = get_n2w_map(digit_map_file)
            if n2w_map:
                for item in n2w_map:
                    for k, v in item.items():
                        self.digit_map_sorted.append((k, v))
                self.digit_map_sorted.sort(key=lambda x: len(x[0]), reverse=True)

    def get_language():
        return [self.language, self.language_name_en, self.language_name_zh, self.spaced_writing, self.score_method]

    def config(self, **kwargs):
        self.init_regex_patterns()

        # 遍历所有传入参数，覆盖默认值
        for key, value in kwargs.items():
            if value is not None:
                setattr(self, key, value)

        # 加载map_dir中的映射文件
        self._load_maps()

        if self.debug > 0: 
            print("所有参数：", flush=True, file=sys.stderr)
            max_key_len = max(len(str(k)) for k in self.__dict__.keys())
            for k, v in self.__dict__.items():
                print(f"  --{k:<{max_key_len}}    {v}", flush=True, file=sys.stderr)

    def init_regex_patterns(self):
        """
        一次性初始化所有正则表达式
        """
        global RE_LINE_CONTAINS_INVALID_CHAR, RE_LINE_NO_LETTER, RE_LINE_NO_LOCAL_LETTER, RE_LINE_FOUR_CONSECUTIVE_LETTERS
        global RE_CHAR_NOT_ASCII_LANG_MARK, RE_CHAR_DIACRITIC, RE_CHAR_NOT_WORD, RE_BRACKETS_CONTENT

        # alphabet_pattern 应当排除 marks_pattern
        if True: # 此处可以添加暂不支持转义字符的语种
            is_debug = False
            if is_debug: 
                logger.info(f"alphabet_pattern = {self.alphabet_pattern}")
                logger.info(f"marks_pattern = {self.marks_pattern}")
            alphabet_pattern_unicode, _ = simple_pattern_difference(self.alphabet_pattern, self.marks_pattern)
            if is_debug: 
                logger.info(f"new alphabet_pattern = {alphabet_pattern_unicode}")
            self.alphabet_pattern = alphabet_pattern_unicode

        # 1. 行包含非法字符
        pattern_a = r'[^' + self.ascii_pattern + self.alphabet_pattern + self.marks_pattern + self.diacritic_pattern + ']'
        #logger.info("RE_LINE_CONTAINS_INVALID_CHAR = " + pattern_a)
        RE_LINE_CONTAINS_INVALID_CHAR = re.compile(pattern_a)

        # 2. 行不包含字母：不含任何字母表字符、英文字符
        pattern_b = r'^[^' + self.english_letter_pattern + self.alphabet_pattern + ']*$'
        #logger.info("RE_LINE_NO_LETTER = " + pattern_b)
        RE_LINE_NO_LETTER = re.compile(pattern_b)

        # 3. 行不包含字母表字母
        pattern_c = r'^[^' + self.alphabet_pattern + ']*$'
        #logger.info("RE_LINE_NO_LOCAL_LETTER = " + pattern_c)
        RE_LINE_NO_LOCAL_LETTER = re.compile(pattern_c)

        # 4. 行包含4个连续相同字母
        pattern_d = r'([' + self.alphabet_pattern + '])\\1\\1\\1'
        #logger.info("RE_LINE_FOUR_CONSECUTIVE_LETTERS = " + pattern_d)
        RE_LINE_FOUR_CONSECUTIVE_LETTERS = re.compile(pattern_d)

        # 5. 字符非ascii/语言/标点
        pattern_chars_a = r'[^' + self.ascii_pattern + self.alphabet_pattern + self.marks_pattern + ']'
        #logger.info("RE_CHAR_NOT_ASCII_LANG_MARK = " + pattern_chars_a)
        RE_CHAR_NOT_ASCII_LANG_MARK = re.compile(pattern_chars_a)

        # 6. 字符非英文单词/语言字母
        pattern_chars_b = r'[^' + self.english_word_pattern + self.alphabet_pattern + ']'
        #logger.info("RE_CHAR_NOT_WORD = " + pattern_chars_b)
        RE_CHAR_NOT_WORD = re.compile(pattern_chars_b)

        # 7. 辅助字符
        if self.diacritic_pattern:
            d_pattern = r'[' + self.diacritic_pattern + ']'
            if self.debug > 0: 
                logger.info("RE_CHAR_DIACRITIC = " + d_pattern)
            RE_CHAR_DIACRITIC = re.compile(d_pattern)

        # 8. 括号及内容
        pattern_brackets = r'\([^()]*\)'
        #logger.info("RE_BRACKETS_CONTENT = " + pattern_brackets)
        RE_BRACKETS_CONTENT = re.compile(pattern_brackets)


    # 整行删除：若匹配pattern
    def fun_remove_lines_pattern(self, text: str, pattern: re.Pattern):
        # 类型检查
        if not isinstance(pattern, re.Pattern):
            logger.error(f"pattern 必须是 re.Pattern 类型，而不是 {type(pattern).__name__}")
            raise TypeError("异常退出！")

        if self.remove_lines:
            match = pattern.search(text)
            if match:  # 匹配到了非法字符，整行删除
                if self.debug > 0:
                    ch = match.group()
                    logger.debug(
                        f"matched: '{ch}', unicode: {ord(ch):04x}, line: {text}"
                    )
                self.num_removed_lines += 1
                return ""  # 删除整行

        return text


    # 删除匹配pattern的字符
    def fun_remove_chars_pattern(self, text: str, pattern: re.Pattern, replacement:str):
        # 类型检查
        if not isinstance(pattern, re.Pattern):
            logger.error(f"pattern 必须是 re.Pattern 类型，而不是 {type(pattern).__name__}")
            raise TypeError(f"异常退出！")
            
        # 所有其它非法字符均替换
        text2 = pattern.sub(replacement, text) # 一般替换为空格或空

        if self.debug > 0 and text != text2:
            logger.debug(f"fun_i: {text}")
            logger.debug(f"fun_o: {text2}")
        if self.debug == 2 and text != text2:
            logger.debug(f"pattern: {pattern}")
            logger.debug(f"text: {text}")
            logger.debug(f"text: ")
            for ch in text: logger.debug(f"char = {ch} \t unicode = {ord(ch):04x}")
            logger.debug(f"alphabet_pattern: {self.alphabet_pattern}")
            for ch in self.alphabet_pattern: logger.debug(f"char = {ch} \t unicode = {ord(ch):04x}")

        return text2

    def fun_remove_chars_dashes(self, text: str):
        """ 
        清除短横:
        remove_dashes = 0: 清除不充当单词连接符的非法短横为空格
        remove_dashes = 1: 清除全部短横为空格 （部分语种可能需要将短横替换为空，可以重载此函数）
        """
        text2 = text
        if self.remove_dashes:
            # remove all dashes          
            text2 = re.sub(r'-+', " ", text2)
        else:
            # remove dashes that not a word connector
            text2 = re.sub(r'-+', r"-", text2)
            text2 = re.sub(r'([\s\'])[-]+', r"\g<1> ", text2)
            text2 = re.sub(r'[-]+([\s\'])', r" \g<1>", text2)
            text2 = re.sub(r'^-', r"", text2)
            text2 = re.sub(r'-$', r"", text2)

        text2 = re.sub(r'[ ]+', r" ", text2)
        
        if self.debug > 0 and text != text2:
            logger.debug(f"fun_i: {text}")
            logger.debug(f"fun_o: {text2}")
        return text2

    def fun_remove_chars_single_quotes(self, text: str):
        """ 
        清除单引号: 部分语种允许单引号开头
        remove_single_quotes = 0: 清除不充当单词连接符的非法单引号为空格
        remove_single_quotes = 1: 清除全部单引号为空格 
        """
        text2 = text
        if self.remove_single_quotes:
            # remove all single quotes          
            text2 = re.sub(r'\'+', " ", text2)
        else:
            # remove single quotes that not a word connector
            text2 = re.sub(r'\'+', r"'", text2)
            text2 = re.sub(r'\'+([\s-])', r" \g<1>", text2)
            if self.allow_leading_single_quote:
                text2 = re.sub(r'([-])\'+', r"\g<1> ", text2) # remove single quotes after dash
            else:
                text2 = re.sub(r'([\s-])\'+', r"\g<1> ", text2) # remove single quotes after dash or space
                text2 = re.sub(r'^\'', r"", text2) 
            text2 = re.sub(r'\'$', r"", text2)

        text2 = re.sub(r'[ ]+', r" ", text2)
        
        if self.debug > 0 and text != text2:
            logger.debug(f"fun_i: {text}")
            logger.debug(f"fun_o: {text2}")
        return text2

    def fun_convert_case(self, text: str):
        """ 
        德语、土耳其语、阿塞拜疆语必须重载实现！
        """
        
        if self.case == "upper":
            text2 = text.upper()
        elif self.case == "lower":
            text2 = text.lower()
        else:
            text2 = text

        # 特殊语种建议才打开日志
        #if self.debug > 0 and text != text2:
        #    logger.debug(f"fun_i: {text}")
        #    logger.debug(f"fun_o: {text2}")
        return text2

    def fun_remove_english_letters(self, text: str):
        """ 
        越南语必须重载实现！
        """
        if self.keep_english_letters:
            return text
        else:
            if self.keep_empty_lines:
                text2 = re.sub(r'[a-zA-Z]', r" ", text) # 替换为空格
            else:
                text2 = re.sub(r'^.*[a-zA-Z].*$', r"", text) # 删除整行

            if self.debug > 0 and text != text2:
                logger.debug(f"fun_i: {text}")
                logger.debug(f"fun_o: {text2}")
            return text2

    # 数据清理的pipeline
    # 多数步骤之间没有必然的顺序，但是正则化必须在阿拉伯数字的去除之前
    def pipeline(self, text):

        text = text.strip()
        
        # 替换不标准的控制字符为空格
        # 下一步的NFKC 正规化仅会将非断行空格（U+00A0）和全角空格（U+3000）转换为普通空格（U+0020），
        # 其他功能特殊的空格（如半角空格、零宽空格等）仍需此步处理。
        if not text: return text
        text = replace_invisible_chars(text)
        
        # NFKC正规化
        if not text: return text
        text = fun_normalize_nfkc(text, debug=self.debug)
        
        # 删除辅助字符
        if not text: return text
        if self.remove_diacritic and self.diacritic_pattern:
            text = self.fun_remove_chars_pattern(text, RE_CHAR_DIACRITIC, "")
        
        # 替换特殊的0-9专用字符到正常的0-9
        if not text: return text
        text = fun_convert_special_numbers_to_arabic(text, debug=self.debug)
        
        # 可选：非法字符的行，整行删除
        if not text: return text
        text = self.fun_remove_lines_pattern(text, RE_LINE_CONTAINS_INVALID_CHAR)
        
        # 替换（非ascii/字母表/标点）字符为空格
        if not text: return text
        if self.remove_not_ascii_lang_mark:
            text = self.fun_remove_chars_pattern(text, RE_CHAR_NOT_ASCII_LANG_MARK, " " * self.spaced_writing)
        
        # 简单正则化（如有更复杂需求可扩展），这一步必须将数字正规化，否则后续会被清除掉。
        if self.debug > 0:
            logger.debug(f"tn_begin: {text}")
        if not text: return text
        text = asr_num2words(
            text, 
            self.language, 
            self.map_dir, 
            self.debug, 
            self.other_map_sorted, 
            self.digit_map_sorted, 
            self.cached_num_map,
            self.normalize_digit_maxlen
        )
        if self.debug > 0:
            logger.debug(f"tn_end  : {text}")

        # 可选：删除整行（全部是标点符号等）
        if not text: return text
        text = self.fun_remove_lines_pattern(text, RE_LINE_NO_LETTER)

        # 可选：删除括号及内容
        if not text: return text
        if self.remove_brackets:
            text = self.fun_remove_chars_pattern(text, RE_BRACKETS_CONTENT, " " * self.spaced_writing)
        
        # 删除非单词的字符
        if not text: return text
        if self.remove_not_word:
            text = self.fun_remove_chars_pattern(text, RE_CHAR_NOT_WORD, " " * self.spaced_writing)
        
        # 可选：删除英文字母
        if not text: return text
        if not self.keep_english_letters:
            text = self.fun_remove_english_letters(text)
        
        # 可选：删除短横
        if not text: return text
        text = self.fun_remove_chars_dashes(text)
        
        # 可选：删除非法的单引号
        if not text: return text
        text = self.fun_remove_chars_single_quotes(text)

        # 删除整行：包含超过连续4个字符以上的单词
        if not text: return text
        text = self.fun_remove_lines_pattern(text, RE_LINE_FOUR_CONSECUTIVE_LETTERS)

        # 可选：大小写转换
        if not text: return text
        text = self.fun_convert_case(text)
        
        # 最后清除多余的空格，并输出到最终文件
        if not text: return text
        text = ' '.join(text.split())

        return text
