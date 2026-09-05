import sys,os
import importlib
import re
from .logger import logger
from .lang_en import TextNormalization_EN
from .lang_zh import TextNormalization_ZH


LANG_CLASSES = {
    'en': TextNormalization_EN(),
    'zh': TextNormalization_ZH(),
}

def text_normalization(
    input_file: str,
    output_file: str,
    **kwargs
):
    """
    Clean text file according to the specified method and language.

    Args:
        input_file (str): Path to the input file.
        output_file (str): Path to the output file.
        language (str): Language code.
        keep_empty_lines (bool, optional): Whether to keep empty lines. Defaults to True.
        debug (int, optional): Whether to print debug info. Defaults to False.
    """

    language = kwargs.get('language', 'en')
    with_id_opt = kwargs.get("with_id_opt")
    keep_empty_lines = kwargs.get("keep_empty_lines")
    debug = kwargs.get("debug")

    if language not in LANG_CLASSES:
        raise ValueError(f"Not supported language: {language}")

    normalizer = LANG_CLASSES[language]

    if debug > 0: 
        print(kwargs, file=sys.stderr)
    normalizer.config(**kwargs)

    if input_file and os.path.exists(input_file):
        infile = open(input_file, 'r', encoding='utf-8-sig')
        need_close_infile = True
    else:
        infile = sys.stdin
        need_close_infile = False

    if output_file:
        outfile = open(output_file, 'w', encoding='utf-8')
        need_close_outfile = True
    else:
        outfile = sys.stdout
        need_close_outfile = False

    try:
        for line in infile:
            line = line.strip()
            #print(f"line = {line}", file=sys.stderr)

            if with_id_opt == 1:
                if not line and keep_empty_lines == 0:  # 忽略空行
                    continue

                parts = line.split(maxsplit=1)
                if len(parts) < 2:
                    index = parts[0]
                    text = ""
                else:
                    index, text = parts
            else:
                text = line
                
            # 文本归一化处理：核心代码就这一行，其他都是外围常规读写处理和索引处理
            normalized_text = normalizer.pipeline(text)

            if debug > 0 and text != normalized_text:
                logger.debug(f"text_i: {text}")
                logger.debug(f"text_o: {normalized_text}")

            if with_id_opt == 1:
                outfile.write(f"{index} {normalized_text}\n")
            else:
                if keep_empty_lines == 1 or normalized_text:
                    outfile.write(f"{normalized_text}\n")
        outfile.flush()
    finally:
        if need_close_infile:
            infile.close()
        if need_close_outfile:
            outfile.close()


