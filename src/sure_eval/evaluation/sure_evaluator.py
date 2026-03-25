"""
SURE Benchmark Evaluator.

Unified evaluator for all SURE Benchmark tasks.
Based on evaluation-pipeline/evaluation/evaluator.py
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from sure_eval.core.logging import get_logger
from sure_eval.evaluation.wenet_compute_cer import Calculator, characterize, normalize, compute_wer

logger = get_logger(__name__)


def _is_chinese_char(ch: str) -> bool:
    return '\u4e00' <= ch <= '\u9fff'


def _strip_punct(text: str) -> str:
    keep = set('-./%')
    return ''.join(
        ch for ch in text
        if not (unicodedata.category(ch).startswith('P') and ch not in keep)
    )


def _strip_punct_all(text: str) -> str:
    keep = set()
    return ''.join(
        ch for ch in text
        if not (unicodedata.category(ch).startswith('P') and ch not in keep)
    )


def calc_rate(result_dict: Dict[str, int]) -> float:
    n = result_dict['all']
    s = result_dict['sub']
    d = result_dict['del']
    i = result_dict['ins']
    if n == 0:
        return 0.0
    return (s + d + i) / n


def split_tokens(tokens: List[str]) -> Tuple[List[str], List[str]]:
    zh = [t for t in tokens if all(_is_chinese_char(c) for c in t)]
    en = [t for t in tokens if not any(_is_chinese_char(c) for c in t)]
    return zh, en


def tokenize_codeswitch(text: str, proc1, proc2) -> List[str]:
    """Tokenize code-switching text (Chinese + English)."""
    from sure_eval.evaluation.normalization import text_normalization
    
    text = _strip_punct(text)
    tokens = []
    buff = []
    for ch in text:
        if _is_chinese_char(ch):
            if buff:
                en_token = proc1.normalize(''.join(buff))
                tokens.append(en_token.upper())
                buff = []
            zh_token = proc2.normalize(ch)
            tokens.append(zh_token)
        elif ch.isalnum():
            buff.append(ch)
        else:
            if buff:
                en_token = proc1.normalize(''.join(buff))
                tokens.append(en_token.upper())
                buff = []
    if buff:
        en_token = proc1.normalize(''.join(buff))
        tokens.append(en_token.upper())
    return tokens


class SUREEvaluator:
    """
    Unified evaluator for SURE Benchmark.
    
    Supports tasks:
    - ASR (WER/CER)
    - SER (emotion recognition accuracy)
    - GR (gender recognition accuracy)
    - S2TT (BLEU/chrF2)
    - SLU (semantic understanding accuracy)
    - SD (speaker diarization DER)
    - SA-ASR (multi-speaker ASR cpWER + DER)
    """
    
    TASK_MAP = {
        "asr": "asr_wer",
        "ser": "ser_eval",
        "gr": "gr_eval",
        "s2tt": "s2tt_eval",
        "slu": "slu_eval",
        "sd": "sd_eval",
        "sa-asr": "sa_asr_eval",
    }
    
    def __init__(
        self,
        language: str = "en",
        ser_mapping: Dict[str, int] | None = None,
        gr_mapping: Dict[str, int] | None = None,
    ):
        self.language = language
        self.ser_mapping = ser_mapping or {"neu": 0, "hap": 1, "ang": 2, "sad": 3}
        self.gr_mapping = gr_mapping or {"man": 0, "woman": 1}
        
        # Initialize preprocessor
        from sure_eval.evaluation.normalization.asr_simple_tn import asr_num2words
        self._preprocessor = None
        self._asr_num2words = asr_num2words
    
    def _get_preprocessor(self, lang: str):
        """Get text preprocessor for language."""
        from sure_eval.evaluation.normalization.asr_simple_tn import asr_num2words
        
        class Preprocessor:
            def __init__(self, lang, map_dir=None):
                self.lang = lang
                self.map_dir = map_dir
            
            def normalize(self, text):
                return asr_num2words(text, self.lang, self.map_dir)
        
        return Preprocessor(lang)
    
    def _normalize_label(self, label: str) -> str:
        """Normalize classification label."""
        label = label.strip()
        label = _strip_punct_all(label)
        label = label.lower()
        
        synonyms = {
            # Emotion
            "happy": "hap",
            "happiness": "hap",
            "neutral": "neu",
            "angry": "ang",
            "anger": "ang",
            "sadness": "sad",
            "sad": "sad",
            # Gender
            "male": "man",
            "m": "man",
            "man": "man",
            "female": "woman",
            "f": "woman",
            "woman": "woman",
        }
        return synonyms.get(label, label)
    
    def evaluate(
        self,
        task: str,
        ref_file: str,
        hyp_file: str,
        **kwargs
    ) -> Any:
        """
        Evaluate a task.
        
        Args:
            task: Task name (asr, ser, gr, s2tt, slu, sd, sa-asr)
            ref_file: Reference file path
            hyp_file: Hypothesis file path
            **kwargs: Task-specific arguments
            
        Returns:
            Evaluation result (format varies by task)
        """
        task_lower = task.lower()
        task_name = self.TASK_MAP.get(task_lower, task_lower)
        
        if task_name == "asr_wer":
            return self._eval_asr(ref_file, hyp_file, **kwargs)
        elif task_name == "ser_eval":
            return self._eval_ser(ref_file, hyp_file)
        elif task_name == "gr_eval":
            return self._eval_gr(ref_file, hyp_file)
        elif task_name == "s2tt_eval":
            return self._eval_s2tt(ref_file, hyp_file)
        elif task_name == "slu_eval":
            return self._eval_slu(ref_file, hyp_file, **kwargs)
        elif task_name == "sd_eval":
            return self._eval_sd(ref_file, hyp_file, **kwargs)
        elif task_name == "sa_asr_eval":
            return self._eval_sa_asr(ref_file, hyp_file, **kwargs)
        else:
            raise ValueError(f"Unknown task: {task}")
    
    def _eval_asr(
        self,
        ref_file: str,
        hyp_file: str,
        tochar: bool = False,
        verbose: int = 0,
    ) -> Dict[str, Any]:
        """
        Evaluate ASR (WER/CER).
        
        Args:
            ref_file: Reference file (key\ttext)
            hyp_file: Hypothesis file (key\ttext)
            tochar: Compute CER instead of WER
            verbose: Verbosity level
            
        Returns:
            Dict with 'all', 'cor', 'sub', 'ins', 'del', 'wer'
        """
        # For Chinese, use CER (character-level)
        tochar = tochar or (self.language == "zh")
        
        # Normalize files first
        from sure_eval.evaluation.normalization.asr_simple_tn import asr_num2words
        import sure_eval.evaluation.normalization
        
        # Get default map directory
        map_dir = os.path.join(os.path.dirname(sure_eval.evaluation.normalization.__file__), "asr_simple_tn_rules")
        
        ref_norm_file = "/tmp/tmp_ref_norm.txt"
        hyp_norm_file = "/tmp/tmp_hyp_norm.txt"
        
        ref_lines = []
        with open(ref_file, 'r', encoding='utf-8') as f:
            ref_file_lines = f.readlines()
        with open(hyp_file, 'r', encoding='utf-8') as f:
            hyp_file_lines = f.readlines()
        
        for line in ref_file_lines:
            parts = line.strip().split('\t', 1)
            if len(parts) == 2:
                key, text = parts
                text_norm = asr_num2words(text, self.language, map_dir=map_dir, debug=False)
                ref_lines.append(f"{key}\t{text_norm}")
        
        hyp_lines = []
        for line in hyp_file_lines:
            parts = line.strip().split('\t', 1)
            if len(parts) == 2:
                key, text = parts
                text_norm = asr_num2words(text, self.language, map_dir=map_dir, debug=False)
                hyp_lines.append(f"{key}\t{text_norm}")
        
        with open(ref_norm_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(ref_lines) + '\n')
        with open(hyp_norm_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(hyp_lines) + '\n')
        
        # Compute WER/CER
        result = compute_wer(ref_norm_file, hyp_norm_file, tochar=tochar, verbose=verbose)
        
        # Calculate WER percentage
        if result['all'] != 0:
            wer = (result['sub'] + result['del'] + result['ins']) / result['all']
        else:
            wer = 0.0
        
        result['wer'] = wer
        result['wer_percent'] = wer * 100
        
        # Cleanup
        os.remove(ref_norm_file)
        os.remove(hyp_norm_file)
        
        return result
    
    def _eval_ser(self, ref_file: str, hyp_file: str) -> float:
        """Evaluate SER (emotion recognition accuracy)."""
        ref_labels = []
        hyp_labels = []
        
        with open(ref_file, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split('\t', 1)
                if len(parts) == 2:
                    key, label = parts
                    ref_labels.append(self._normalize_label(label))
        
        with open(hyp_file, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split('\t', 1)
                if len(parts) == 2:
                    key, label = parts
                    norm_label = self._normalize_label(label)
                    mapped = None
                    if norm_label.isdigit():
                        for k, v in self.ser_mapping.items():
                            if str(v) == norm_label:
                                mapped = k
                                break
                    else:
                        mapped = norm_label
                    hyp_labels.append(mapped)
        
        valid = [(r, h) for r, h in zip(ref_labels, hyp_labels) if r is not None and h is not None]
        if not valid:
            logger.warning("[SER] No valid labels for accuracy calculation.")
            return 0.0
        
        correct = sum(1 for r, h in valid if r == h)
        acc = correct / len(valid)
        logger.info(f"[SER] Accuracy: {acc:.4f} ({correct}/{len(valid)})")
        return acc
    
    def _eval_gr(self, ref_file: str, hyp_file: str) -> float:
        """Evaluate GR (gender recognition accuracy)."""
        ref_labels = []
        hyp_labels = []
        
        with open(ref_file, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split('\t', 1)
                if len(parts) == 2:
                    key, label = parts
                    ref_labels.append(self._normalize_label(label))
        
        with open(hyp_file, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split('\t', 1)
                if len(parts) == 2:
                    key, label = parts
                    norm_label = self._normalize_label(label)
                    mapped = None
                    if norm_label.isdigit():
                        for k, v in self.gr_mapping.items():
                            if str(v) == norm_label:
                                mapped = k
                                break
                    else:
                        mapped = norm_label
                    hyp_labels.append(mapped)
        
        valid = [(r, h) for r, h in zip(ref_labels, hyp_labels) if r is not None and h is not None]
        if not valid:
            logger.warning("[GR] No valid labels for accuracy calculation.")
            return 0.0
        
        correct = sum(1 for r, h in valid if r == h)
        acc = correct / len(valid)
        logger.info(f"[GR] Accuracy: {acc:.4f} ({correct}/{len(valid)})")
        return acc
    
    def _eval_s2tt(self, ref_file: str, hyp_file: str) -> Dict[str, float]:
        """Evaluate S2TT (BLEU/chrF2)."""
        from sacrebleu.metrics import BLEU, CHRF
        
        ref_lines = []
        hyp_lines = []
        
        with open(ref_file, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split('\t', 1)
                if len(parts) == 2:
                    key, text = parts
                    ref_lines.append(text)
        
        with open(hyp_file, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split('\t', 1)
                if len(parts) == 2:
                    key, text = parts
                    hyp_lines.append(text)
        
        # Use appropriate tokenizer
        if self.language.lower() in ["zh", "ch", "chinese"]:
            bleu = BLEU(tokenize='zh')
            chrf = CHRF(word_order=2)
        elif self.language.lower() in ["en", "english"]:
            bleu = BLEU(tokenize='13a')
            chrf = CHRF(word_order=2)
        else:
            bleu = BLEU(tokenize='none')
            chrf = CHRF(word_order=2)
        
        score_bleu = bleu.corpus_score(hyp_lines, [ref_lines])
        score_chrf = chrf.corpus_score(hyp_lines, [ref_lines])
        
        logger.info(f"[S2TT] BLEU = {score_bleu.score:.2f}")
        logger.info(f"[S2TT] chrF2 = {score_chrf.score:.2f}")
        
        return {
            "bleu": score_bleu.score,
            "chrf": score_chrf.score,
        }
    
    def _eval_slu(self, ref_file: str, hyp_file: str, prompt_jsonl: str | None = None) -> float:
        """Evaluate SLU (semantic understanding accuracy)."""
        # SLU requires special processing
        if not prompt_jsonl:
            logger.warning("[SLU] prompt_jsonl required for SLU evaluation")
            return 0.0
        
        # Process predictions using process_prediction.py logic
        # For now, simple exact match after normalization
        ref_dict = {}
        hyp_dict = {}
        
        with open(ref_file, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split('\t', 1)
                if len(parts) == 2:
                    key, ans = parts
                    ref_dict[key] = ans.strip().lower()
        
        with open(hyp_file, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split('\t', 1)
                if len(parts) == 2:
                    key, ans = parts
                    hyp_dict[key] = ans.strip().lower()
        
        total = 0
        correct = 0
        for key in ref_dict:
            if key in hyp_dict:
                total += 1
                if ref_dict[key] == hyp_dict[key]:
                    correct += 1
        
        if total == 0:
            logger.warning("[SLU] No valid pairs for accuracy calculation.")
            return 0.0
        
        acc = correct / total
        logger.info(f"[SLU] Accuracy: {acc:.4f} ({correct}/{total})")
        return acc
    
    def _eval_sd(
        self,
        ref_file: str,
        hyp_file: str,
        collar: float = 0.25,
    ) -> Dict[str, Any]:
        """Evaluate SD (speaker diarization DER)."""
        try:
            import meeteval
        except ImportError:
            logger.error("meeteval not installed. Install with: pip install meeteval")
            return {"der": 0.0, "num_sessions": 0}
        
        logger.info(f"[SD] Running DER evaluation with collar={collar}s")
        ref = meeteval.io.load(ref_file)
        hyp = meeteval.io.load(hyp_file)
        result_der = meeteval.der.dscore(ref, hyp, collar=collar)
        
        total_error_rate = 0.0
        total_sessions = 0
        for session, der in result_der.items():
            logger.info(
                f"DER for {session}: {float(der.error_rate):.4f} "
                f"(missed: {float(der.missed_speaker_time):.4f}, "
                f"fa: {float(der.falarm_speaker_time):.4f}, "
                f"ser: {float(der.speaker_error_time):.4f})"
            )
            total_error_rate += float(der.error_rate)
            total_sessions += 1
        
        avg_der = total_error_rate / total_sessions if total_sessions > 0 else 0.0
        logger.info(f"[SD] Average DER: {avg_der:.4f}")
        
        return {
            "der": avg_der,
            "num_sessions": total_sessions,
        }
    
    def _eval_sa_asr(
        self,
        ref_file: str,
        hyp_file: str,
        collar: float = 0.5,
    ) -> Dict[str, Any]:
        """Evaluate SA-ASR (multi-speaker ASR cpWER + DER)."""
        try:
            import meeteval
        except ImportError:
            logger.error("meeteval not installed. Install with: pip install meeteval")
            return {"cpwer": 0.0, "der": 0.0, "num_sessions": 0}
        
        # Normalize STM files
        ref_norm_stm = "/tmp/tmp_ref_sa_asr_norm.stm"
        hyp_norm_stm = "/tmp/tmp_hyp_sa_asr_norm.stm"
        
        from sure_eval.evaluation.normalization.asr_simple_tn import asr_num2words
        import sure_eval.evaluation.normalization
        map_dir = os.path.join(os.path.dirname(sure_eval.evaluation.normalization.__file__), "asr_simple_tn_rules")
        
        with open(ref_file, 'r', encoding='utf-8') as fin:
            ref_lines = fin.readlines()
        with open(ref_norm_stm, 'w', encoding='utf-8') as fout:
            for line in ref_lines:
                parts = line.strip().split(maxsplit=5)
                if len(parts) == 6:
                    norm_trans = asr_num2words(parts[5], self.language, map_dir=map_dir, debug=False)
                    parts[5] = norm_trans
                    parts[3] = str(float(parts[3]))
                    parts[4] = str(float(parts[4]))
                    fout.write(' '.join(parts) + '\n')
        
        with open(hyp_file, 'r', encoding='utf-8') as fin:
            hyp_lines = fin.readlines()
        with open(hyp_norm_stm, 'w', encoding='utf-8') as fout:
            for line in hyp_lines:
                parts = line.strip().split(maxsplit=5)
                if len(parts) == 6:
                    norm_trans = asr_num2words(parts[5], self.language, map_dir=map_dir, debug=False)
                    parts[5] = norm_trans
                    parts[3] = str(float(parts[3]))
                    parts[4] = str(float(parts[4]))
                    fout.write(' '.join(parts) + '\n')
        
        ref = meeteval.io.load(ref_norm_stm)
        hyp = meeteval.io.load(hyp_norm_stm)
        
        result_cpwer = meeteval.wer.cpwer(ref, hyp)
        avg_cpwer = meeteval.wer.combine_error_rates(result_cpwer.values())
        
        result_der = meeteval.der.dscore(ref, hyp, collar=collar)
        total_der = 0.0
        num_sessions = 0
        for session, der in result_der.items():
            total_der += float(der.error_rate)
            num_sessions += 1
        
        avg_der = total_der / num_sessions if num_sessions > 0 else 0.0
        
        logger.info(f"[SA-ASR] cpWER: {avg_cpwer.error_rate:.4f}")
        logger.info(f"[SA-ASR] DER: {avg_der:.4f}")
        
        # Cleanup
        os.remove(ref_norm_stm)
        os.remove(hyp_norm_stm)
        
        return {
            "cpwer": float(avg_cpwer.error_rate),
            "der": avg_der,
            "num_sessions": num_sessions,
        }
