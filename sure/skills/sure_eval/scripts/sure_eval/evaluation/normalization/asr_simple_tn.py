import sys
import os
import re
import glob
from .logger import logger
from .num2words import num2words as num2words_std # https://github.com/savoirfairelinux/num2words

# why "?:" is necessoary in "()"?
# some leading digits, followd by zero or multiple times of (one comma followd by three digits), 
# followed by zero or one (one point followd by arbitry times of digits )
NUM_REGEX = re.compile(r'\d+(?:,\d\d\d)*(?:\.\d*)?')

langset = ['en', 'zh', 'zh_cn', 'zh_CN']

def is_positive_integer(s):
    s = str(s)
    return s.isdigit() and s != "0"

def num2words_fun(num, lang, to='cardinal', debug=False):
    if check_language(lang):
        if debug:
            logger.debug(f"num2words_std: num = {num}, lang = {lang}, to = {to}")
        result = num2words_std(num, lang=lang, to=to)
        return result
    else:
        logger.debug(f"ERROR: Failed to translate num {num}")
        return str(num)
    

def check_language(language, debug=False):
    if (language not in langset):
        logger.debug(f"WARNING: num2words does not support language {language}")
        return False
    else:
        return True

def get_n2w_map(map_file, language=None):
    if not os.path.exists(map_file):
        print("error: no such map file: ", map_file, file=sys.stderr)
        return
    n2w_map = []
    with open(map_file, 'r') as fm:
        for line in fm:
            line = re.sub(r'\s+', ' ', line.strip()) # 忽略多余的空格和tab
            line = line.split('#', 1)[0] # 删除#开头的注释
            if (len(line) == 0): # empty line
                continue
            line2 = line.split('|') # 优先以'|'分割
            if (len(line2) != 2):
                line2 = line.split() # 其次以空格或tab分割
                if (len(line2) != 2):
                    print("error map file format, map_file = ", map_file, " len = ", len(line2), " line = ", line, file=sys.stderr)
                    exit()
            if language in ["ja", "ar", "zh", "zh_cn", "zh_CN"]:
                n2w_map.append({line2[0].strip() : line2[1].strip()}) # 不要引入额外的空格
            else:
                n2w_map.append({line2[0].strip() : " " + line2[1].strip() + " "})
    #print(n2w_map, file=sys.stderr)
    return n2w_map

def tn_replace(text, key, value):
    pattern = None
    if key[0:2] == "\\b" or key[-2:] == "\\b": # 目前仅支持在首尾加单词边界符
        pattern = r'{}'.format(key)
        #print(f"key = {key}", file=sys.stderr)
        #print(f"pattern = {pattern}", file=sys.stderr)
        #print(f"value = {value}", file=sys.stderr)
        #print(f"text = {text}", file=sys.stderr)
        text = re.sub(pattern, value, text)
    else:
        text = text.replace(key, value)
    return text

def preprocess_zh_text(text):
    # 0. 归一化不规范的中文数字表达（如"一零"→"十"，"一五"→"十五"）
    # 这处理的是逐位读法，如10读作"一零"，15读作"一五"
    def normalize_chinese_digits(match):
        digit_str = match.group(0)
        # 将中文数字转换为阿拉伯数字，再转回规范的中文数字
        digit_map = {'零': '0', '一': '1', '二': '2', '三': '3', '四': '4', 
                   '五': '5', '六': '6', '七': '7', '八': '8', '九': '9'}
        try:
            arabic_num = ''.join([digit_map.get(ch, ch) for ch in digit_str])
            num = int(arabic_num)
            if 10 <= num <= 99:
                return num2words_std(num, lang='zh_CN')
            else:
                return digit_str
        except:
            return digit_str
    
    # 匹配中文数字的逐位读法（如"一零"、"一五"、"二零"等）
    # 只匹配2位中文数字，避免匹配更长或更短的
    text = re.sub(r'([一二三四五六七八九零])([一二三四五六七八九零])', normalize_chinese_digits, text)
    
    # 1. 阿拉伯数字转中文数字（处理时间、年龄等场景）
    def arabic_to_chinese_num(match):
        num_str = match.group(0)
        try:
            num = int(num_str)
            # 对于小于100的数字，转换为中文数字
            if num < 100:
                return num2words_std(num, lang='zh_CN')
            else:
                return num_str  # 大数字保持阿拉伯数字
        except:
            return num_str
    
    # 匹配独立的数字（避免匹配日期中的数字）
    text = re.sub(r'(?<!\d)(\d{1,2})(?!\d)', arabic_to_chinese_num, text)
    
    # 2. Decomposed Units (NFKC artifacts): ℃ -> °C, ㎡ -> m2
    text = re.sub(r'°\s*C', '摄氏度', text)
    text = re.sub(r'(\d+(?:\.\d+)?)\s*m2(?![a-zA-Z0-9])', r'\1平方米', text)
    text = re.sub(r'(\d+(?:\.\d+)?)\s*m3(?![a-zA-Z0-9])', r'\1立方米', text)

    # 3. Dates: 2023-10-27 or 2023/10/27 -> 2023年10月27日
    text = re.sub(r'(\d{4})\s*[-/]\s*(\d{1,2})\s*[-/]\s*(\d{1,2})', r'\1年\2月\3日', text)
    
    # 4. Fractions: 1/2 -> 2分之1
    # Lookbehind/ahead to avoid matching parts of dates or other patterns if necessary
    # Assuming simple 1/2 format for fractions in ASR output
    text = re.sub(r'(?<!\d[/-])(\d+)\s*/\s*(\d+)(?![/-]\d)', r'\2分之\1', text)
    
    # 5. Percent: 50% -> 百分之50
    text = re.sub(r'(\d+(?:\.\d+)?)\s*%', r'百分之\1', text)
    
    # 6. Negative: -5 -> 负5
    # Avoid matching ranges like 5-10 (digit-digit) or models iPhone-15 (letter-digit)
    # But allow "在-5" (Chinese-digit)
    text = re.sub(r'(?<![0-9a-zA-Z])\s*-\s*(\d+)', r'负\1', text)
    
    # 7. Units (Attached): 3m, 75kg
    unit_map = {
        'kg': '千克', 'km': '千米', 'cm': '厘米', 'mm': '毫米',
        'ml': '毫升', 'l': '升', 'm': '米'
    }
    def replace_unit(match):
        val = match.group(1)
        unit = match.group(2)
        return val + unit_map.get(unit, unit)
        
    text = re.sub(r'(\d+(?:\.\d+)?)\s*(kg|km|cm|mm|ml|l|m)(?![a-zA-Z])', replace_unit, text)
    
    return text

def preprocess_en_text(text):
    # 0. Special Symbols: ' (feet)
    # Must be handled before or carefully to not conflict with quotes (though ' is usually single quote)
    # Only replace ' if preceded by digit
    text = re.sub(r'(\d+)\'', r'\1 feet', text)

    # 1. Ordinals: 1st, 2nd, 3rd, 4th -> first, second, third, fourth
    def replace_ordinal(match):
        num = match.group(1)
        # Check if the suffix matches the number (heuristic)
        # 1 -> st, 2 -> nd, 3 -> rd, others -> th (except 11, 12, 13)
        # But here we just trust the text and convert
        return num2words_std(num, lang='en', to='ordinal')
    
    text = re.sub(r'\b(\d+)(st|nd|rd|th)\b', replace_ordinal, text, flags=re.IGNORECASE)

    # 2. Currency: $12.50, $190
    def replace_currency(match):
        val = match.group(1).replace(',', '')
        try:
            return num2words_std(val, lang='en', to='currency', currency='USD')
        except:
            return match.group(0) # Fallback

    text = re.sub(r'\$(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)', replace_currency, text)

    # 3. Decades: 1990s -> nineteen nineties
    def replace_decade(match):
        year = match.group(1)
        # Convert 1990 -> nineteen ninety
        # Then pluralize last word
        text_year = num2words_std(year, lang='en', to='year')
        if text_year.endswith('y'):
            return text_year[:-1] + 'ies'
        else:
            return text_year + 's'
            
    text = re.sub(r'\b(\d{4})s\b', replace_decade, text)

    # 4. Phone Numbers: 555-0199 or 123-456-7890
    # Read digit by digit
    def replace_phone(match):
        digits = match.group(0)
        # Replace hyphens with spaces? Or silence?
        # Read digits.
        # 555-0199 -> five five five zero one nine nine
        # Clean text
        digits_clean = digits.replace('-', ' ')
        res = []
        for char in digits_clean:
            if char.isdigit():
                res.append(num2words_std(char, lang='en'))
            else:
                res.append(char)
        return ' '.join(res)

    # Pattern: 3 digits - 4 digits (and optional area code)
    text = re.sub(r'\b\d{3}-\d{3}-\d{4}\b', replace_phone, text)
    text = re.sub(r'\b\d{3}-\d{4}\b', replace_phone, text)

    # 5. Negative numbers: -5 -> minus five
    # Avoid matching ranges or phone numbers (already handled but be careful)
    # Lookbehind for start of line or space
    text = re.sub(r'(?<![\d\w])-\s*(\d+(?:\.\d+)?)', r'minus \1', text)

    return text

def asr_num2words(
    text, 
    language, 
    map_dir, 
    debug, 
    other_map_sorted=None, 
    digit_map_sorted=None, 
    cached_num_map=None,
    normalize_digit_maxlen=12
):
    # 打印看num2words是否加载成功
    #if debug:
    #    module = sys.modules[num2words.__module__]
    #    print(module.__file__, file=sys.stderr)
    if debug:
        logger.debug(f"normalize_digit_maxlen: {normalize_digit_maxlen}")
    
    text_ori = text

    if 'tts' in language:
        language = re.sub(r'_tts$', '', language)

    lang = language
    if language == "zh":
        lang = "zh_CN" # 使用简体中文

    # 0. Preprocess
    if lang in ['zh', 'zh_CN', 'zh_cn']:
        text = preprocess_zh_text(text)
    elif lang == 'en':
        text = preprocess_en_text(text)
    

    # 1. 先用 other 类映射替换 text
    if other_map_sorted is not None:
        pairs = other_map_sorted
    else:
        # fallback: 兼容老接口
        pairs = []
        map_files = glob.glob(os.path.join(map_dir, "*.map"))
        for mf in map_files:
            if os.path.basename(mf) != "digit.map":
                pairs.extend(load_and_sort_map(mf))
        pairs.sort(key=lambda x: len(x[0]), reverse=True)
    text2 = text
    for key, value in pairs:
        text2 = tn_replace(text2, key, value)
    if debug and text != text2:
        logger.debug(f"map_i: {text}")
        logger.debug(f"map_o: {text2}")
    text = text2

    # 2. digit.map
    if digit_map_sorted is not None:
        n2w_map_sorted = digit_map_sorted
    else:
        # fallback: 兼容老接口
        lang_map_file = os.path.join(map_dir, language, "digit.map")
        root_map_file = os.path.join(map_dir, "digit.map")
        if os.path.exists(lang_map_file):
            map_file = lang_map_file
        elif os.path.exists(root_map_file):
            map_file = root_map_file
        else:
            map_file = None

        n2w_map = get_n2w_map(map_file, language) if map_file else []
        n2w_map_sorted = []
        if n2w_map:
            for item in n2w_map:
                for k, v in item.items():
                    n2w_map_sorted.append((k, v))
            n2w_map_sorted.sort(key=lambda x: len(x[0]), reverse=True)

    # 3. 缓存
    if cached_num_map is None:
        cached_num_map = {}

    # 正则匹配找到所有的数字
    matches = list(re.finditer(NUM_REGEX, text)) # 保存为列表
    if False:
        print("num of matched digital strings = ", len(matches), file=sys.stderr)
    pre_pos = 0
    text2 = []

    if len(matches) > 0:
        is_supported_lang = check_language(lang)

    i = 0
    for m in matches:
        if False and debug:
            print("\rProgress: {}".format(i), end='', file=sys.stderr)
        #print(m)
        i = i + 1
        num = m.group()
        num_raw = num
        cur_start = m.start()
        cur_end = m.end()
        if cur_start < pre_pos:
            print("Error: cur_start < pre_start!", file=sys.stderr)
            sys.exit(1)

        num = num.rstrip('.') # 删除末尾的点号

        # 特殊兜底的数字映射：长度大于4的，或0开头的数字串通常按0-9发音
        if ((len(num) < normalize_digit_maxlen and len(num) > 4 and num.isdigit() and (10 ** (len(num)-1) != int(num))) or num[0] == '0') and len(n2w_map_sorted) > 0:
            if len(num) > normalize_digit_maxlen:
                if debug or len(num) > 12:
                    logger.warning(f"number length > threshold {normalize_digit_maxlen}, failed to convert \"{num}\"")
            elif num_raw in cached_num_map:
                num = cached_num_map[num_raw]
            else:
                for key, value in n2w_map_sorted:
                    num = num.replace(key, value)
                cached_num_map[num_raw] = num

            if debug:
                logger.debug(f"fun_i: digit.map {num_raw}")
                logger.debug(f"fun_o: digit.map {num}")

        # numbers, decimals
        # 走num2words
        else:   
            num = num.replace(',', '') # 删除千位逗号
            if len(num) > normalize_digit_maxlen: # abnormal number (12位以上abs会出错)
                if debug or len(num) > 12:
                    logger.warning(f"number length > threshold {normalize_digit_maxlen}, failed to convert \"{num}\"")
            elif is_supported_lang:            
                
                if num_raw in cached_num_map:
                    num = cached_num_map[num_raw]
                else:
                    def str_to_number(value):
                        try:
                            #if debug:
                            #    logger.debug(f"try str2int: {value}")
                            #    value2 = int(value)
                            #    logger.debug(f"try str2int: {value2}")
                            value = int(value)
                        except ValueError:
                            #if debug:
                            #    logger.debug(f"try str2float: {value}")
                            value = float(value)
                            #if debug:
                            #    logger.debug(f"try str2float: {value}")
                        return value
                    #print(f'num = {num}')
                    #print(f'lang = {lang}')
                    #print(f'num2words')
                    num = str_to_number(num)
                    #print(f'num = {num}')
                    if debug:
                        logger.debug(f"before_num2words: {num}")
                    num = num2words_fun(num, lang=lang, to='cardinal', debug=debug)
                    if debug:
                        logger.debug(f"after_num2words: {num}")
                    #print(f'num = {num}')
                    
                    if language in ['zh', 'zh_CN', 'zh_cn']:
                        num = num # 无需空格
                    else:
                        num = " " + num + " "
                        
                    cached_num_map[num_raw] = num
            else:
                pass # ignore

        # concatenate three segments of strings
        text2.append(text[pre_pos:cur_start] + num)
        pre_pos = cur_end
    text2.append(text[pre_pos:]) # the tail
    text = ''.join([item for item in text2])

    if debug and text != text_ori:
        logger.debug(f"fun_i: {text_ori}")
        logger.debug(f"fun_o: {text}")

    return text

