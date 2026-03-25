import os
from .base import TextNormalization_Base

alphabet_pattern = '\\u4e00-\\u9fff'
diacritic_pattern = ''

class TextNormalization_ZH(TextNormalization_Base):
    def __init__(self):
        super().__init__()
        self.language = "zh"
        self.language_name_en = "Chinese"
        self.language_name_zh = "中文"
        self.alphabet_pattern = alphabet_pattern
        self.diacritic_pattern = diacritic_pattern

        base_dir = os.path.dirname(os.path.abspath(__file__))
        asr_simple_tn_rules_dir = os.path.join(base_dir, "asr_simple_tn_rules", self.language)
        self.map_dir = asr_simple_tn_rules_dir

        self.spaced_writing = False
        self.score_method = "CER"  # 按字符而不是单词计算字错
        self.script = "Chinese"
        
