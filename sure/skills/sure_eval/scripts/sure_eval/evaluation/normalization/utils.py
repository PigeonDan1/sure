# -*- coding: utf-8 -*-
#########################################################################
# File: utils.py
# Date: 2025-07-22
# Author: kunyang.peng@aispeech.com
# Description:
#########################################################################

import unicodedata
import re
import logging

# 在 Unicode 标准中，字符被分为不同的类别，unicodedata.category(char) 函数会返回一个表示字符所属类别的字符串，该字符串的首字母代表了大类，其中 C 代表控>制字符类，Z 代表分隔符类。以下为你详细介绍这两类中包含的字符：
# 控制字符类别（C）
# 控制字符通常用于控制设备或文本的格式和显示，它们没有可见的字形，主要用于控制通信、文本处理和设备操作等方面。C 类又细分为以下几个子类：
# Cc（控制字符）
#    ASCII 控制字符：例如 \x00（空字符）、\x07（响铃符）、\x08（退格符）、\x09（水平制表符）、\x0A（换行符）、\x0D（回车符）等。
#    其他控制字符：如 \x1B（转义字符），常用于控制终端的显示格式，例如改变文本颜色、清屏等操作。
# Cf（格式控制字符）
#    零宽空格：\u200B，它在文本中不占据可见的宽度，但可以影响文本的排版和显示，例如在一些需要控制文本断行的地方使用。
#    右至左标记：\u200F，用于指示文本的阅读方向为从右至左，在处理阿拉伯语、希伯来语等从右至左书写的语言时会用到。
# Cs（代理字符）
#    代理字符用于表示 Unicode 中超出基本多文种平面（BMP，范围是 U+0000 - U+FFFF）的字符。例如，高代理范围是 \uD800 - \uDBFF，低代理范围是 \uDC00 - \uDFFF。
# Co（私有使用字符）
#    这些字符是为私有使用保留的，用户可以根据自己的需求定义这些字符的含义和用途。例如，\uE000 - \uF8FF 是基本多文种平面的私有使用区域。
# Cn（未定义字符）
#    表示那些在 Unicode 标准中尚未定义的码位。
#
# 分隔符类别（Z）
# 分隔符用于分隔文本中的不同部分，如段落、句子、单词等。Z 类也有几个子类：
# Zs（空格分隔符）
#    空格：\x20，是最常见的空格字符，用于分隔单词。
#    不间断空格：\u00A0，与普通空格类似，但它不会在该位置换行，常用于避免一些特定的单词或短语被分隔到两行。
#    表意文字空格：\u3000，主要用于中文、日文和韩文等表意文字的排版中，通常表示一个全角空格。
# Zl（行分隔符）
#    \u2028，用于分隔文本中的行，功能类似于换行符，但在某些特定的文本处理场景中有不同的用途。
# Zp（段落分隔符）
#    \u2029，用于分隔文本中的段落。

# 常见零宽字符的 Unicode 编码集合
ZERO_WIDTH_CHARS = {
    '\u200B',  # 零宽空格
    '\u200C',  # 零宽非连接符
    '\u200D',  # 零宽连接符
    '\uFEFF',  # 零宽不换行空格（BOM）
    '\u180B',  # 蒙古文第一变音符（Mongolian Free Variation Selector 1）
    '\u180C',  # 蒙古文第二变音符（Mongolian Free Variation Selector 2） 
    '\u180E',  # 蒙古文元音分隔符
    '\u2060',  # 词连接符
}

def is_zero_width(char):
    """判断单个字符是否为零宽字符"""
    return len(char) == 1 and char in ZERO_WIDTH_CHARS

def replace_invisible_chars(text):
    keep_chars = {'\n', '\t'}
    text2=""
    # 所有unicode定义的不可见的控制字符全部替换
    for char in text:
        if is_zero_width(char): # 零宽字符，有的零宽字符不一定位于C和Z类，例如\u180B \u180C
            #print(f"char = {char}, unicode = {ord(char):04x}, width = 0")
            text2 += '' 
        elif unicodedata.category(char)[0] in {'C', 'Z'} and char not in keep_chars:
            #print(f"char = {char}, unicode = {ord(char):04x}, width > 0")
            text2 += " "
        else:
            text2 += char

    return text2

def str2bool(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')    


def simple_parse_pattern(pattern):
    """解析模式字符串，返回包含所有Unicode码点的集合
    仅支持简单形式的pattern：字符或字符区间，不支持其它任何复杂的正则表达式，例如转义符、通配符。
    """
    code_points = set()

    # 正则表达式匹配单个字符、Unicode值或区间
    pattern_re = re.compile(
        r'(?:'
        r'\\u[0-9a-fA-F]{4}|'  # Unicode值，如\u0020
        r'[^-]'                 # 非'-'的单个字符
        r')'
        r'(?:-(?:'
        r'\\u[0-9a-fA-F]{4}|'  # 区间结束的Unicode值
        r'[^-]'                 # 区间结束的单个字符
        r'))?'                  # 可选的区间部分
    )
    # 遍历所有匹配的部分
    for part in pattern_re.findall(pattern):
        if '-' in part:
            # 处理区间
            start_str, end_str = part.split('-', 1)
            # 解析区间起始码点
            if start_str.startswith('\\u'):
                start = int(start_str[2:], 16)
            else:
                start = ord(start_str)
            # 解析区间结束码点
            if end_str.startswith('\\u'):
                end = int(end_str[2:], 16)
            else:
                end = ord(end_str)
            # 确保起始码点不大于结束码点
            if start > end:
                start, end = end, start
            # 添加区间内所有码点
            for code in range(start, end + 1):
                code_points.add(code)
        else:
            # 处理单个字符或Unicode值
            if part.startswith('\\u'):
                code = int(part[2:], 16)
            else:
                code = ord(part)
            code_points.add(code)
    return code_points

def simple_merge_intervals(code_points):
    """将码点集合合并为连续的区间列表
    仅支持简单形式的pattern：字符或字符区间，不支持其它任何复杂的正则表达式，例如转义符、通配符。
    """
    if not code_points:
        return []
    # 排序码点
    sorted_codes = sorted(code_points)
    intervals = []
    # 初始化第一个区间
    current_start = current_end = sorted_codes[0]
    # 遍历剩余码点，合并连续区间
    for code in sorted_codes[1:]:
        if code == current_end + 1:
            current_end = code
        else:
            intervals.append((current_start, current_end))
            current_start = current_end = code
    # 添加最后一个区间
    intervals.append((current_start, current_end))
    return intervals

def simple_format_interval(interval, use_chars):
    """格式化单个区间为字符串表示
    仅支持简单形式的pattern：字符或字符区间，不支持其它任何复杂的正则表达式，例如转义符、通配符。
    """
    start, end = interval
    # 格式化起始点
    if use_chars and 32 <= start <= 126:
        start_str = chr(start)
    else:
        start_str = f'\\u{start:04x}'
    # 格式化结束点（如果与起始点相同，则不需要结束点）
    if start == end:
        return start_str
    if use_chars and 32 <= end <= 126:
        end_str = chr(end)
    else:
        end_str = f'\\u{end:04x}'
    return f'{start_str}-{end_str}'

def simple_pattern_difference(pattern1, pattern2):
    """计算两个模式的差集，并返回两种形式的结果
    仅支持简单形式的pattern：字符或字符区间，不支持其它任何复杂的正则表达式，例如转义符、通配符。
    """
    # 解析两个模式的码点集合
    set1 = simple_parse_pattern(pattern1)
    set2 = simple_parse_pattern(pattern2)
    # 计算差集
    diff_set = set1 - set2
    # 合并区间
    intervals = simple_merge_intervals(diff_set)
    # 生成两种形式的结果
    mixed = ''.join(simple_format_interval(iv, use_chars=True) for iv in intervals)
    unicode_only = ''.join(simple_format_interval(iv, use_chars=False) for iv in intervals)
    return mixed, unicode_only

def to_unicode_codepoints(items):
    """
    将字符或码点转换为统一的 Unicode 码点表示（例如 '\u2026'）。

    - 字符串：逐字符转换，例如 '…' -> '\u2026'，'...' -> '\u002e\u002e\u002e'
    - 整数：视为码点值，例如 0x2026 -> '\u2026'
    其他类型将抛出 TypeError。
    """

    result = []
    for item in items:
        if isinstance(item, str):
            result.append(''.join(f"\\u{ord(ch):04x}" for ch in item))
        elif isinstance(item, int):
            result.append(f"\\u{item:04x}")
        else:
            raise TypeError(f"Unsupported type in to_unicode_codepoints: {type(item)}")
    return ''.join(result)

