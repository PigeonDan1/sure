"""
SURE Benchmark Evaluator.

Unified evaluator for all SURE Benchmark tasks.
Based on evaluation-pipeline/evaluation/evaluator.py
"""

from __future__ import annotations

import os
import re
import string
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from sure_eval.core.logging import get_logger
from sure_eval.evaluation.wenet_compute_cer import Calculator, characterize, normalize, compute_wer

logger = get_logger(__name__)

PUNCT_SET = set(string.punctuation) | {
    '，', '。', '！', '？', '：', '；', '、', '（', '）',
    '“', '”', '‘', '’', '【', '】', '《', '》', '——', '…',
    '\\',
}

TEXT_NORMALIZER_PUNCTS = set([
    '!', '"', '#', '$', '%', '&', '(', ')', '*', '+', ',', '-', '.', '/',
    ':', ';', '=', '?', '@', '[', '\\', ']', '^', '_', '`', '{', '}', '~',
    '、', '。', '！', '，', '；', '？', '：', '「', '」', '︰', '『', '』', '《', '》',
])
TEXT_NORMALIZER_SPACELIST = {' ', '\t', '\r', '\n'}


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


def _strip_eval_punct_text(text: str) -> str:
    """Match evaluation-pipeline clean_marks.strip_all_punct text behavior."""
    cleaned = []
    for ch in text:
        try:
            unicodedata.name(ch)
        except ValueError:
            continue
        if ch.isprintable() and ch not in PUNCT_SET:
            cleaned.append(ch)
    return ''.join(cleaned)


def _strip_eval_punct_file(path: str) -> None:
    """Strip punctuation from key-tab-text files like evaluation-pipeline."""
    file_path = Path(path)
    lines = file_path.read_text(encoding='utf-8').splitlines()
    cleaned_lines = []
    for line in lines:
        if '\t' not in line:
            cleaned_lines.append(line)
            continue
        key, text = line.split('\t', 1)
        cleaned_lines.append(f"{key}\t{_strip_eval_punct_text(text)}")
    file_path.write_text('\n'.join(cleaned_lines) + '\n', encoding='utf-8')


def _stripoff_tags(text: str) -> str:
    if not text:
        return ''
    chars = []
    i = 0
    text_len = len(text)
    while i < text_len:
        if text[i] == '<':
            while i < text_len and text[i] != '>':
                i += 1
            i += 1
        else:
            chars.append(text[i])
            i += 1
    return ''.join(chars)


def _remove_all_puncts(text: str) -> str:
    if not text:
        return ''
    return ''.join([c for c in text if c not in TEXT_NORMALIZER_PUNCTS or c in TEXT_NORMALIZER_SPACELIST])


def _normalize_text(text: str, case_sensitive: bool = False, remove_tag: bool = True, language: str = "en") -> str:
    """Match evaluation-pipeline evaluation/text_normalizer.py behavior."""
    if not text:
        return ''
    if remove_tag:
        text = _stripoff_tags(text)
    text = _remove_all_puncts(text)
    if not case_sensitive:
        text = text.upper()
    text = ' '.join(text.split())
    if language == "zh":
        tokens = []
        buff = []
        for ch in text:
            if _is_chinese_char(ch):
                if buff:
                    tokens.append(''.join(buff))
                    buff = []
                tokens.append(ch)
            elif ch.isspace():
                if buff:
                    tokens.append(''.join(buff))
                    buff = []
            else:
                buff.append(ch)
        if buff:
            tokens.append(''.join(buff))
        text = ' '.join(tokens)
    return text.strip()


def _process_slu_prediction_file(prompt_jsonl: str, predictions_path: str, output_path: str) -> None:
    """Match evaluation-pipeline process_prediction.py behavior in-process."""
    import json
    from collections import defaultdict

    key2prompt: dict[str, tuple[str, dict[str, str]]] = {}
    with open(prompt_jsonl, "r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line.strip())
            prompt = item.get("prompt", "")
            opts: dict[str, str] = {}
            if prompt:
                for prompt_line in prompt.splitlines():
                    match = re.match(r'^\s*([A-Da-d])\.\s*(.*)$', prompt_line)
                    if match:
                        opts[match.group(1).upper()] = match.group(2).strip()
            key2prompt[item["key"]] = (prompt, opts)

    key2full: defaultdict[str, str] = defaultdict(str)
    with open(predictions_path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line or "\t" not in line:
                continue
            key, pred = line.split("\t", 1)
            key2full[key] += " " + pred.strip()

    processed: list[tuple[str, str]] = []
    for key, full_pred in key2full.items():
        if key not in key2prompt or not key2prompt[key][1]:
            processed.append((key, full_pred.strip()))
            continue
        _, opts = key2prompt[key]

        match = re.search(r'\b([A-Da-d])\b', full_pred)
        if match and match.group(1).upper() in opts:
            processed.append((key, opts[match.group(1).upper()]))
            continue

        pred_norm = re.sub(r'\s+', ' ', full_pred.strip().lower())
        for text in opts.values():
            if pred_norm == re.sub(r'\s+', ' ', text.lower()):
                processed.append((key, text))
                break
        else:
            processed.append((key, full_pred.strip()))

    with open(output_path, "w", encoding="utf-8") as handle:
        for key, value in processed:
            handle.write(f"{key}\t{value}\n")


def _ensure_md_eval_on_path() -> None:
    """Expose a local md-eval-22.pl to meeteval when running offline."""
    env_path = os.environ.get("SURE_EVAL_MD_EVAL_PATH")
    candidates = []
    if env_path:
        candidates.append(Path(env_path).expanduser())

    repo_root = Path(__file__).resolve().parents[3]
    candidates.extend(
        [
            repo_root / "md-eval-22.pl",
            repo_root / "tools" / "md-eval-22.pl",
            repo_root / "src" / "sure_eval" / "models" / "diarizen" / "diarizen_src" / "dscore" / "scorelib" / "md-eval-22.pl",
            repo_root.parent / "evaluation-pipeline" / "md-eval-22.pl",
        ]
    )

    for candidate in candidates:
        if candidate.exists():
            executable = candidate
            if not os.access(candidate, os.X_OK):
                wrapper_dir = Path(tempfile.gettempdir()) / "sure_eval_md_eval"
                wrapper_dir.mkdir(parents=True, exist_ok=True)
                executable = wrapper_dir / "md-eval-22.pl"
                executable.write_text(
                    "#!/usr/bin/perl\n"
                    f"exec 'perl', '{candidate}', @ARGV;\n",
                    encoding="utf-8",
                )
                executable.chmod(0o755)
            path_dir = str(executable.parent)
            current_path = os.environ.get("PATH", "")
            if path_dir not in current_path.split(os.pathsep):
                os.environ["PATH"] = path_dir + os.pathsep + current_path
            return


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
        import sure_eval.evaluation.normalization
        default_map_dir = os.path.join(os.path.dirname(sure_eval.evaluation.normalization.__file__), "asr_simple_tn_rules")
        
        class Preprocessor:
            def __init__(self, lang, map_dir=None):
                self.lang = lang
                self.map_dir = map_dir or default_map_dir
            
            def normalize(self, text):
                return asr_num2words(text, self.lang, self.map_dir, debug=False)
        
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
        if self.language == "cs":
            return self._eval_asr_codeswitch(ref_file, hyp_file)

        # For Chinese, use CER (character-level)
        tochar = tochar or (self.language == "zh")
        
        # Normalize files first
        from sure_eval.evaluation.normalization.asr_simple_tn import asr_num2words
        import sure_eval.evaluation.normalization
        
        # Get default map directory
        map_dir = os.path.join(os.path.dirname(sure_eval.evaluation.normalization.__file__), "asr_simple_tn_rules")
        
        ref_norm_handle = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        hyp_norm_handle = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        ref_norm_file = ref_norm_handle.name
        hyp_norm_file = hyp_norm_handle.name
        ref_norm_handle.close()
        hyp_norm_handle.close()
        
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
        
        try:
            with open(ref_norm_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(ref_lines) + '\n')
            with open(hyp_norm_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(hyp_lines) + '\n')

            # Keep ASR scoring aligned with the upstream evaluation-pipeline:
            # normalize text first, then strip punctuation from both ref and hyp.
            _strip_eval_punct_file(ref_norm_file)
            _strip_eval_punct_file(hyp_norm_file)

            # Compute WER/CER
            result = compute_wer(ref_norm_file, hyp_norm_file, tochar=tochar, verbose=verbose)

            # Calculate WER percentage
            if result['all'] != 0:
                wer = (result['sub'] + result['del'] + result['ins']) / result['all']
            else:
                wer = 0.0

            metric_name = "cer" if tochar else "wer"
            result[metric_name] = wer
            result[f"{metric_name}_percent"] = wer * 100
            result["score"] = wer

            return result
        finally:
            Path(ref_norm_file).unlink(missing_ok=True)
            Path(hyp_norm_file).unlink(missing_ok=True)

    def _eval_asr_codeswitch(self, ref_file: str, hyp_file: str) -> Dict[str, Any]:
        """Evaluate code-switching ASR with upstream MER/WER/CER behavior."""
        from sure_eval.evaluation.normalization.asr_simple_tn import asr_num2words
        import sure_eval.evaluation.normalization

        map_dir = os.path.join(os.path.dirname(sure_eval.evaluation.normalization.__file__), "asr_simple_tn_rules")

        class Preprocessor:
            def __init__(self, lang: str):
                self.lang = lang

            def normalize(self, text: str) -> str:
                return asr_num2words(text, self.lang, map_dir=map_dir, debug=False)

        proc_en = Preprocessor('en')
        proc_zh = Preprocessor('zh')
        ref_lines: list[str] = []
        hyp_lines: list[str] = []
        ref_zh_lines: list[str] = []
        ref_en_lines: list[str] = []
        hyp_zh_lines: list[str] = []
        hyp_en_lines: list[str] = []

        with open(ref_file, 'r', encoding='utf-8') as handle:
            ref_file_lines = handle.readlines()
        with open(hyp_file, 'r', encoding='utf-8') as handle:
            hyp_file_lines = handle.readlines()

        for line in ref_file_lines:
            parts = line.strip().split('\t', 1)
            if len(parts) == 2:
                key, text = parts
                tokens = tokenize_codeswitch(text, proc_en, proc_zh)
                ref_lines.append(f"{key}\t{' '.join(tokens)}")
                zh_tokens, en_tokens = split_tokens(tokens)
                ref_zh_lines.append(f"{key}\t{' '.join(zh_tokens)}")
                ref_en_lines.append(f"{key}\t{' '.join(en_tokens)}")

        for line in hyp_file_lines:
            parts = line.strip().split('\t', 1)
            if len(parts) == 2:
                key, text = parts
                tokens = tokenize_codeswitch(text, proc_en, proc_zh)
                hyp_lines.append(f"{key}\t{' '.join(tokens)}")
                zh_tokens, en_tokens = split_tokens(tokens)
                hyp_zh_lines.append(f"{key}\t{' '.join(zh_tokens)}")
                hyp_en_lines.append(f"{key}\t{' '.join(en_tokens)}")

        temp_paths: list[str] = []
        try:
            def write_temp(rows: list[str]) -> str:
                handle = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
                handle.write('\n'.join(rows) + '\n')
                handle.close()
                temp_paths.append(handle.name)
                return handle.name

            ref_norm_file = write_temp(ref_lines)
            hyp_norm_file = write_temp(hyp_lines)
            ref_zh_file = write_temp(ref_zh_lines)
            hyp_zh_file = write_temp(hyp_zh_lines)
            ref_en_file = write_temp(ref_en_lines)
            hyp_en_file = write_temp(hyp_en_lines)

            mer_result = compute_wer(ref_norm_file, hyp_norm_file)
            cer_result = compute_wer(ref_zh_file, hyp_zh_file, tochar=True)
            wer_result = compute_wer(ref_en_file, hyp_en_file)
        finally:
            for path in temp_paths:
                Path(path).unlink(missing_ok=True)

        mer_score = calc_rate(mer_result)
        cer_score = calc_rate(cer_result)
        wer_score = calc_rate(wer_result)
        return {
            "mer": mer_score,
            "wer": wer_score,
            "cer": cer_score,
            "mer_percent": mer_score * 100,
            "wer_percent": wer_score * 100,
            "cer_percent": cer_score * 100,
            "score": mer_score,
            "mer_details": mer_result,
            "wer_details": wer_result,
            "cer_details": cer_result,
        }
    
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
            "bleu_char": score_bleu.score,
            "chrf": score_chrf.score,
            "score": score_bleu.score,
        }
    
    def _eval_slu(self, ref_file: str, hyp_file: str, prompt_jsonl: str | None = None) -> float:
        """Evaluate SLU (semantic understanding accuracy)."""
        # SLU requires special processing
        if not prompt_jsonl:
            logger.warning("[SLU] prompt_jsonl required for SLU evaluation")
            return 0.0
        
        hyp_processed = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        ref_processed = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        hyp_processed.close()
        ref_processed.close()

        try:
            _process_slu_prediction_file(prompt_jsonl, hyp_file, hyp_processed.name)
            _process_slu_prediction_file(prompt_jsonl, ref_file, ref_processed.name)

            ref_dict = {}
            hyp_dict = {}

            with open(ref_processed.name, 'r', encoding='utf-8') as f:
                for line in f:
                    parts = line.strip().split('\t', 1)
                    if len(parts) == 2:
                        key, ans = parts
                        ref_dict[key] = ans.strip().lower()

            with open(hyp_processed.name, 'r', encoding='utf-8') as f:
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
        finally:
            Path(hyp_processed.name).unlink(missing_ok=True)
            Path(ref_processed.name).unlink(missing_ok=True)
    
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
        _ensure_md_eval_on_path()
        
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
        _ensure_md_eval_on_path()
        
        # Normalize STM files
        ref_norm_handle = tempfile.NamedTemporaryFile(mode="w", suffix=".stm", delete=False, encoding="utf-8")
        hyp_norm_handle = tempfile.NamedTemporaryFile(mode="w", suffix=".stm", delete=False, encoding="utf-8")
        ref_norm_stm = ref_norm_handle.name
        hyp_norm_stm = hyp_norm_handle.name
        ref_norm_handle.close()
        hyp_norm_handle.close()
        
        with open(ref_file, 'r', encoding='utf-8') as fin:
            ref_lines = fin.readlines()
        with open(ref_norm_stm, 'w', encoding='utf-8') as fout:
            for line in ref_lines:
                parts = line.strip().split(maxsplit=5)
                if len(parts) == 6:
                    norm_trans = _normalize_text(parts[5], case_sensitive=False, remove_tag=True, language=self.language)
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
                    norm_trans = _normalize_text(parts[5], case_sensitive=False, remove_tag=True, language=self.language)
                    parts[5] = norm_trans
                    parts[3] = str(float(parts[3]))
                    parts[4] = str(float(parts[4]))
                    fout.write(' '.join(parts) + '\n')
        
        try:
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

            return {
                "cpwer": float(avg_cpwer.error_rate),
                "der": avg_der,
                "num_sessions": num_sessions,
            }
        finally:
            Path(ref_norm_stm).unlink(missing_ok=True)
            Path(hyp_norm_stm).unlink(missing_ok=True)
