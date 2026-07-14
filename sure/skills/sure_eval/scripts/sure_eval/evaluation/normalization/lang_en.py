import os
from .base import TextNormalization_Base

alphabet_pattern = 'a-zA-Z'
diacritic_pattern = ''

class TextNormalization_EN(TextNormalization_Base):
    def __init__(self):
        super().__init__()
        self.language = "en"
        self.language_name_en = "English"
        self.language_name_zh = "英语"
        self.alphabet_pattern = alphabet_pattern
        self.diacritic_pattern = diacritic_pattern

        base_dir = os.path.dirname(os.path.abspath(__file__))
        asr_simple_tn_rules_dir = os.path.join(base_dir, "asr_simple_tn_rules", self.language)
        self.map_dir = asr_simple_tn_rules_dir

        self.script = "Latin"
        
        
