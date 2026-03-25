"""WER/CER computation (from WeNet)."""

from __future__ import annotations

import re
import sys
import unicodedata
from typing import Dict, List, Set, Tuple

# Constants
remove_tag = True
spacelist = [' ', '\t', '\r', '\n']
puncts = [
    '!', ',', '?', '、', '。', '！', '，', '；', '？', '：', '「', '」', '︰', '『', '』',
    '《', '》'
]


def characterize(string: str) -> List[str]:
    """Convert string to character list."""
    res = []
    i = 0
    while i < len(string):
        char = string[i]
        if char in puncts:
            i += 1
            continue
        cat1 = unicodedata.category(char)
        if cat1 == 'Zs' or cat1 == 'Cn' or char in spacelist:
            i += 1
            continue
        if cat1 == 'Lo':  # letter-other
            res.append(char)
            i += 1
        else:
            sep = ' '
            if char == '<':
                sep = '>'
            j = i + 1
            while j < len(string):
                c = string[j]
                if ord(c) >= 128 or (c in spacelist) or (c == sep):
                    break
                j += 1
            if j < len(string) and string[j] == '>':
                j += 1
            res.append(string[i:j])
            i = j
    return res


def stripoff_tags(x: str) -> str:
    """Remove XML-like tags from text."""
    if not x:
        return ''
    chars = []
    i = 0
    T = len(x)
    while i < T:
        if x[i] == '<':
            while i < T and x[i] != '>':
                i += 1
            i += 1
        else:
            chars.append(x[i])
            i += 1
    return ''.join(chars)


def normalize(
    sentence: List[str],
    ignore_words: Set[str],
    cs: bool,
    split: Dict[str, List[str]] | None = None
) -> List[str]:
    """Normalize sentence."""
    new_sentence = []
    for token in sentence:
        x = token
        if not cs:
            x = x.upper()
        if x in ignore_words:
            continue
        if remove_tag:
            x = stripoff_tags(x)
        if not x:
            continue
        if split and x in split:
            new_sentence += split[x]
        else:
            new_sentence.append(x)
    return new_sentence


class Calculator:
    """WER/CER calculator using edit distance."""

    def __init__(self):
        self.data: Dict[str, Dict[str, int]] = {}
        self.space: List[List[Dict]] = []
        self.cost = {'cor': 0, 'sub': 1, 'del': 1, 'ins': 1}

    def calculate(self, lab: List[str], rec: List[str]) -> Dict:
        """Calculate edit distance between lab and rec."""
        # Make copies to avoid modifying original lists
        lab = [''] + list(lab)
        rec = [''] + list(rec)
        
        # Initialize space matrix
        while len(self.space) < len(lab):
            self.space.append([])
        for row in self.space:
            for element in row:
                element['dist'] = 0
                element['error'] = 'non'
            while len(row) < len(rec):
                row.append({'dist': 0, 'error': 'non'})
        
        # Initialize borders
        for i in range(len(lab)):
            self.space[i][0]['dist'] = i
            self.space[i][0]['error'] = 'del'
        for j in range(len(rec)):
            self.space[0][j]['dist'] = j
            self.space[0][j]['error'] = 'ins'
        self.space[0][0]['error'] = 'non'

        # Initialize token stats
        for token in lab:
            if token not in self.data and len(token) > 0:
                self.data[token] = {'all': 0, 'cor': 0, 'sub': 0, 'ins': 0, 'del': 0}
        for token in rec:
            if token not in self.data and len(token) > 0:
                self.data[token] = {'all': 0, 'cor': 0, 'sub': 0, 'ins': 0, 'del': 0}

        # Computing edit distance
        for i, lab_token in enumerate(lab):
            for j, rec_token in enumerate(rec):
                if i == 0 or j == 0:
                    continue
                min_dist = sys.maxsize
                min_error = 'none'
                
                dist = self.space[i - 1][j]['dist'] + self.cost['del']
                error = 'del'
                if dist < min_dist:
                    min_dist = dist
                    min_error = error
                
                dist = self.space[i][j - 1]['dist'] + self.cost['ins']
                error = 'ins'
                if dist < min_dist:
                    min_dist = dist
                    min_error = error
                
                if lab_token == rec_token:
                    dist = self.space[i - 1][j - 1]['dist'] + self.cost['cor']
                    error = 'cor'
                else:
                    dist = self.space[i - 1][j - 1]['dist'] + self.cost['sub']
                    error = 'sub'
                if dist < min_dist:
                    min_dist = dist
                    min_error = error
                
                self.space[i][j]['dist'] = min_dist
                self.space[i][j]['error'] = min_error

        # Tracing back
        result = {'lab': [], 'rec': [], 'all': 0, 'cor': 0, 'sub': 0, 'ins': 0, 'del': 0}
        i = len(lab) - 1
        j = len(rec) - 1
        while True:
            if self.space[i][j]['error'] == 'cor':
                if len(lab[i]) > 0:
                    self.data[lab[i]]['all'] += 1
                    self.data[lab[i]]['cor'] += 1
                    result['all'] += 1
                    result['cor'] += 1
                result['lab'].insert(0, lab[i])
                result['rec'].insert(0, rec[j])
                i -= 1
                j -= 1
            elif self.space[i][j]['error'] == 'sub':
                if len(lab[i]) > 0:
                    self.data[lab[i]]['all'] += 1
                    self.data[lab[i]]['sub'] += 1
                    result['all'] += 1
                    result['sub'] += 1
                result['lab'].insert(0, lab[i])
                result['rec'].insert(0, rec[j])
                i -= 1
                j -= 1
            elif self.space[i][j]['error'] == 'del':
                if len(lab[i]) > 0:
                    self.data[lab[i]]['all'] += 1
                    self.data[lab[i]]['del'] += 1
                    result['all'] += 1
                    result['del'] += 1
                result['lab'].insert(0, lab[i])
                result['rec'].insert(0, "")
                i -= 1
            elif self.space[i][j]['error'] == 'ins':
                if len(rec[j]) > 0:
                    self.data[rec[j]]['ins'] += 1
                    result['ins'] += 1
                result['lab'].insert(0, "")
                result['rec'].insert(0, rec[j])
                j -= 1
            elif self.space[i][j]['error'] == 'non':
                break
            else:
                break
        
        return result

    def overall(self) -> Dict[str, int]:
        """Get overall statistics."""
        result = {'all': 0, 'cor': 0, 'sub': 0, 'ins': 0, 'del': 0}
        for token in self.data:
            result['all'] += self.data[token]['all']
            result['cor'] += self.data[token]['cor']
            result['sub'] += self.data[token]['sub']
            result['ins'] += self.data[token]['ins']
            result['del'] += self.data[token]['del']
        return result

    def cluster(self, data: List[str]) -> Dict[str, int]:
        """Get statistics for a cluster of tokens."""
        result = {'all': 0, 'cor': 0, 'sub': 0, 'ins': 0, 'del': 0}
        for token in data:
            if token in self.data:
                result['all'] += self.data[token]['all']
                result['cor'] += self.data[token]['cor']
                result['sub'] += self.data[token]['sub']
                result['ins'] += self.data[token]['ins']
                result['del'] += self.data[token]['del']
        return result


def compute_wer(
    ref_file: str,
    hyp_file: str,
    ignore_words: Set[str] | None = None,
    case_sensitive: bool = False,
    tochar: bool = False,
    split: Dict[str, List[str]] | None = None,
    verbose: int = 0,
) -> Dict[str, int]:
    """
    Compute WER/CER between reference and hypothesis files.
    
    Args:
        ref_file: Path to reference file (format: "key text")
        hyp_file: Path to hypothesis file (format: "key text")
        ignore_words: Set of words to ignore
        case_sensitive: Whether to be case sensitive
        tochar: Whether to compute CER (character-level) instead of WER
        split: Dictionary of word splits
        verbose: Verbosity level
        
    Returns:
        Dictionary with 'all', 'cor', 'sub', 'ins', 'del' counts
    """
    calculator = Calculator()
    ignore_words = ignore_words or set()
    
    if not case_sensitive:
        ignore_words = set([w.upper() for w in ignore_words])
    
    # Load hypothesis
    rec_set: Dict[str, List[str]] = {}
    with open(hyp_file, 'r', encoding='utf-8') as fh:
        for line in fh:
            if tochar:
                array = characterize(line)
            else:
                array = line.strip().split()
            if len(array) == 0:
                continue
            fid = array[0]
            rec_set[fid] = normalize(array[1:], ignore_words, case_sensitive, split)

    # Compute WER
    results = []
    for line in open(ref_file, 'r', encoding='utf-8'):
        if tochar:
            array = characterize(line)
        else:
            array = line.rstrip('\n').split()
        if len(array) == 0:
            continue
        fid = array[0]
        if fid not in rec_set:
            continue
        lab = normalize(array[1:], ignore_words, case_sensitive, split)
        rec = rec_set[fid]
        result = calculator.calculate(lab, rec)
        results.append(result)
        
        if verbose:
            if result['all'] != 0:
                wer = float(result['ins'] + result['sub'] + result['del']) * 100.0 / result['all']
            else:
                wer = 0.0
            print(f'utt: {fid}')
            print(f'WER: {wer:.2f} % N={result["all"]} C={result["cor"]} S={result["sub"]} D={result["del"]} I={result["ins"]}')

    overall = calculator.overall()
    if verbose:
        if overall['all'] != 0:
            wer = float(overall['ins'] + overall['sub'] + overall['del']) * 100.0 / overall['all']
        else:
            wer = 0.0
        print(f'Overall -> {wer:.2f} % N={overall["all"]} C={overall["cor"]} S={overall["sub"]} D={overall["del"]} I={overall["ins"]}')

    return overall
