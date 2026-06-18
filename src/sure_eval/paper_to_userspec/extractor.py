"""Deterministic paper/repo/model-card extractor for the MVP pre-agent."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .confidence import confidence_for_user_spec, spans_to_paper_evidence_cards
from .schema import SPEC_VERSION

Section = dict[str, Any]


TASK_PATTERNS: list[tuple[str, list[str]]] = [
    ("TTS", ["text-to-speech", "text to speech", "tts", "speech synthesis"]),
    ("SA-ASR", ["speaker-aware asr", "speaker aware asr", "speaker-attributed asr"]),
    ("S2TT", ["speech translation", "speech-to-text translation", "s2tt"]),
    ("SD", ["speaker diarization", "diarization"]),
    ("SER", ["speech emotion recognition", "emotion recognition"]),
    ("Speech Enhancement", ["speech enhancement", "denoising", "noise suppression"]),
    ("Music IR", ["music information retrieval", "music tagging", "beat tracking"]),
    ("VAD", ["voice activity detection", "vad"]),
    ("SV", ["speaker verification", "speaker recognition"]),
    ("VLM", ["vision-language", "visual language", "vlm"]),
    ("GR", ["gender recognition", "gender classification"]),
    ("SLU", ["spoken language understanding", "slu"]),
    ("ASR", ["automatic speech recognition", "speech recognition", "transcription", "asr"]),
]

DATASET_PATTERNS = [
    "LibriSpeech",
    "LibriSpeech-PC",
    "AISHELL-1",
    "AISHELL",
    "Common Voice",
    "CoVoST",
    "VoxCeleb",
    "IEMOCAP",
    "AMI",
    "CALLHOME",
    "MUSAN",
    "DNS",
    "AudioSet",
]

METRIC_PATTERNS = [
    "WER",
    "CER",
    "BLEU",
    "chrF2",
    "chrF",
    "DER",
    "cpWER",
    "cpCER",
    "accuracy",
    "F1",
    "precision",
    "recall",
    "EER",
    "AUC",
    "PESQ",
    "STOI",
    "SI-SDR",
    "DNSMOS",
    "SIM-o",
    "SIM",
    "RTF",
    "CMOS",
    "SMOS",
    "UTMOS",
    "mAP",
]

SECTION_ALIASES: list[tuple[str, list[str]]] = [
    ("abstract", ["abstract"]),
    ("introduction", ["introduction", "intro"]),
    ("background", ["background"]),
    ("related work", ["related work", "prior work"]),
    ("method", ["method", "methods", "approach"]),
    ("methodology", ["methodology"]),
    ("model", ["model", "model architecture"]),
    ("training", ["training", "training details", "training setup"]),
    ("experiments", ["experiments", "experimental setup", "experiment setup"]),
    ("experimental setup", ["experimental setup", "experiment setup"]),
    ("evaluation", ["evaluation", "evaluations"]),
    ("results", ["results", "result"]),
    ("discussion", ["discussion"]),
    ("conclusion", ["conclusion", "conclusions"]),
    ("limitations", ["limitations", "limitation"]),
    ("references", ["references", "bibliography"]),
    ("extracted links", ["extracted links", "links"]),
    ("appendix", ["appendix", "appendices", "supplementary material"]),
]

TASK_SECTION_PRIORITY = [
    "abstract",
    "introduction",
    "method",
    "methodology",
    "model",
    "experiments",
    "experimental setup",
    "evaluation",
    "title",
]

METRIC_SECTIONS = {"experiments", "experimental setup", "evaluation", "results", "abstract"}
REFERENCE_SECTIONS = {"references", "appendix"}
LOW_PRIORITY_MODEL_SECTIONS = {"related work", "background"}
POSITIVE_MODEL_SECTIONS = {"title", "abstract", "introduction", "method", "methodology", "model", "experiments", "experimental setup", "evaluation", "results"}
DATASET_CANDIDATE_SECTIONS = {"abstract", "training", "experiments", "experimental setup", "evaluation", "results", "method", "methodology", "model"}
EVAL_DATASET_SECTIONS = {"experiments", "experimental setup", "evaluation", "results", "abstract"}


@dataclass
class ModelNameCandidate:
    name: str
    source_rule: str
    section_name: str
    start_char: int
    end_char: int
    quote: str
    positive_signals: list[str] = field(default_factory=list)
    negative_signals: list[str] = field(default_factory=list)
    score: float = 0.0
    confidence: float = 0.0


def _quote(text: str, start: int, end: int) -> str:
    left = max(0, start - 70)
    right = min(len(text), end + 70)
    return re.sub(r"\s+", " ", text[left:right]).strip()[:260]


def _span(
    text: str,
    field: str,
    value: Any,
    match: re.Match[str],
    confidence: float,
    source: str = "paper_text",
    sections: list[Section] | None = None,
) -> dict[str, Any]:
    section_name = _section_name_at(sections or [], match.start())
    return {
        "field": field,
        "value": value,
        "source": source,
        "quote": _quote(text, match.start(), match.end()),
        "section_name": section_name,
        "start_char": match.start(),
        "end_char": match.end(),
        "confidence": confidence,
    }


def _span_from_offsets(
    text: str,
    field: str,
    value: Any,
    start: int,
    end: int,
    confidence: float,
    sections: list[Section],
    source: str = "paper_text",
) -> dict[str, Any]:
    return {
        "field": field,
        "value": value,
        "source": source,
        "quote": _quote(text, start, end),
        "section_name": _section_name_at(sections, start),
        "start_char": start,
        "end_char": end,
        "confidence": confidence,
    }


def _first_match(text: str, patterns: list[str], flags: int = re.IGNORECASE) -> re.Match[str] | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return match
    return None


def segment_paper_sections(text: str) -> list[Section]:
    """Split paper text into lightweight academic sections with absolute offsets."""
    headings: list[tuple[int, int, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        line_body = line.rstrip("\r\n")
        section_name = _heading_section_name(line_body)
        if section_name:
            headings.append((offset, offset + len(line), section_name))
        offset += len(line)

    if not headings:
        return [{"section_name": "unknown", "start_char": 0, "end_char": len(text), "text": text}]

    sections: list[Section] = []
    first_start, _first_end, _first_name = headings[0]
    if first_start > 0 and text[:first_start].strip():
        sections.append(
            {
                "section_name": "title",
                "start_char": 0,
                "end_char": first_start,
                "text": text[:first_start],
            }
        )

    for idx, (heading_start, heading_end, section_name) in enumerate(headings):
        end = headings[idx + 1][0] if idx + 1 < len(headings) else len(text)
        sections.append(
            {
                "section_name": section_name,
                "start_char": heading_end,
                "end_char": end,
                "text": text[heading_end:end],
            }
        )
    return sections


def _heading_section_name(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or len(stripped) > 120:
        return None
    stripped = re.sub(r"^#{1,6}\s*", "", stripped).strip()
    stripped = stripped.rstrip(":")
    stripped = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", stripped)
    stripped = re.sub(r"^[A-Z]\.?\s+", "", stripped)
    normalized = re.sub(r"\s+", " ", stripped.lower()).strip()
    for canonical, aliases in SECTION_ALIASES:
        if normalized in aliases:
            return canonical
    return None


def _section_name_at(sections: list[Section], pos: int) -> str:
    for section in sections:
        if section["start_char"] <= pos < section["end_char"]:
            return str(section["section_name"])
    return "unknown"


def _sections_named(sections: list[Section], section_name: str) -> list[Section]:
    return [section for section in sections if section["section_name"] == section_name]


def _append_warning(warnings: list[str], warning: str) -> None:
    if warning not in warnings:
        warnings.append(warning)


def _section_has_any_keyword(text: str, candidates: list[str]) -> bool:
    return any(re.search(rf"\b{re.escape(candidate)}\b", text, re.IGNORECASE) for candidate in candidates)


def _normalize_identity(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _names_similar(left: str | None, right: str | None) -> bool:
    l_norm = _normalize_identity(left)
    r_norm = _normalize_identity(right)
    if not l_norm or not r_norm:
        return False
    return l_norm in r_norm or r_norm in l_norm


def _extract_title(text: str, sections: list[Section]) -> tuple[str, dict[str, Any] | None]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for idx, line in enumerate(lines[:12]):
        if line.lower().startswith("title:"):
            title = line.split(":", 1)[1].strip()
            match = re.search(re.escape(title), text)
            if match:
                return title, _span(text, "source.paper_title", title, match, 0.9, sections=sections)
        if idx == 0 and 6 <= len(line) <= 180 and not line.lower().startswith("abstract"):
            match = re.search(re.escape(line), text)
            if match:
                return line, _span(text, "source.paper_title", line, match, 0.72, sections=sections)
    return "unknown", None


def _extract_model_name(
    text: str,
    case_id: str,
    sections: list[Section],
    *,
    repo_url: str | None = None,
    model_card_url: str | None = None,
    warnings: list[str] | None = None,
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]], bool]:
    candidates = _rank_model_name_candidates(text, case_id, sections, repo_url=repo_url, model_card_url=model_card_url)
    if not candidates:
        fallback = case_id.replace("-", "_")
        if warnings is not None:
            _append_warning(warnings, "model_name_fell_back_to_case_id")
        diagnostics: list[dict[str, Any]] = []
        return fallback, None, diagnostics, False

    chosen = candidates[0]
    diagnostics = [_candidate_to_span(candidate, sections, selected=False) for candidate in candidates[1:]]
    conflict = len(candidates) > 1 and chosen.score - candidates[1].score < 0.15
    if conflict and warnings is not None:
        _append_warning(warnings, "model_name_candidate_conflict")
    if _candidate_low_quality(chosen) and warnings is not None:
        _append_warning(warnings, "model_name_selected_from_low_quality_context")

    selected_span = _candidate_to_span(chosen, sections, selected=True)
    return chosen.name, selected_span, diagnostics, conflict


def _rank_model_name_candidates(
    text: str,
    case_id: str,
    sections: list[Section],
    *,
    repo_url: str | None,
    model_card_url: str | None,
) -> list[ModelNameCandidate]:
    raw_candidates: list[ModelNameCandidate] = []
    raw_candidates.extend(_title_model_candidates(text, sections))
    raw_candidates.extend(_proposal_model_candidates(text, sections))
    raw_candidates.extend(_intro_method_model_candidates(text, sections))
    raw_candidates.extend(_named_model_candidates(text, sections))
    raw_candidates.extend(_url_agreement_candidates(text, sections, repo_url, model_card_url))

    grouped: dict[str, ModelNameCandidate] = {}
    for candidate in raw_candidates:
        if not _looks_like_model_name(candidate.name):
            continue
        key = _normalize_identity(candidate.name)
        if not key:
            continue
        candidate = _score_model_candidate(candidate, text, sections, repo_url, model_card_url)
        current = grouped.get(key)
        if current is None or candidate.score > current.score:
            grouped[key] = candidate
        elif current:
            current.positive_signals.extend(signal for signal in candidate.positive_signals if signal not in current.positive_signals)
            current.negative_signals.extend(signal for signal in candidate.negative_signals if signal not in current.negative_signals)
            current.score = max(current.score, candidate.score)

    if not grouped:
        fallback = ModelNameCandidate(
            name=case_id.replace("-", "_"),
            source_rule="case_id_fallback",
            section_name="unknown",
            start_char=0,
            end_char=0,
            quote="case_id fallback",
            negative_signals=["fallback_without_reliable_paper_candidate"],
            score=0.2,
            confidence=0.25,
        )
        return [fallback]
    return sorted(grouped.values(), key=lambda item: item.score, reverse=True)


def _title_model_candidates(text: str, sections: list[Section]) -> list[ModelNameCandidate]:
    candidates: list[ModelNameCandidate] = []
    title_sections = _sections_named(sections, "title")
    for section in title_sections:
        title_line = next((line.strip() for line in section["text"].splitlines() if line.strip()), "")
        if not title_line:
            continue
        cleaned = re.sub(r"^#{1,6}\s*", "", title_line).strip()
        lead = re.split(r"\s*[:\-–]\s+|\s+for\s+", cleaned, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        for name, rel_start, rel_end in _model_name_tokens(lead):
            start = section["start_char"] + title_line.find(lead) + rel_start
            candidates.append(
                ModelNameCandidate(
                    name=name,
                    source_rule="title_candidate",
                    section_name="title",
                    start_char=start,
                    end_char=start + (rel_end - rel_start),
                    quote=_quote(text, start, start + (rel_end - rel_start)),
                    positive_signals=["title"],
                )
            )
            break
    return candidates


def _proposal_model_candidates(text: str, sections: list[Section]) -> list[ModelNameCandidate]:
    candidates: list[ModelNameCandidate] = []
    patterns = [
        r"\b(?:we|this paper)\s+(?:propose|present|introduce)s?\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?([A-Za-z][A-Za-z0-9._+-]{2,}(?:[- ][A-Za-z0-9._+]+)?)",
        r"\bour\s+proposed\s+([A-Za-z][A-Za-z0-9._+-]{2,}(?:[- ][A-Za-z0-9._+]+)?)",
        r"\b([A-Za-z][A-Za-z0-9._+-]{2,})\s*,\s+a\s+(?:model|framework|system|method)",
        r"\b([A-Za-z][A-Za-z0-9._+-]{2,})\s+is\s+a\s+(?:model|framework|system|method)",
    ]
    for section in _sections_named(sections, "abstract"):
        for pattern in patterns:
            for match in re.finditer(pattern, section["text"], re.IGNORECASE):
                name = _clean_model_name(match.group(1))
                start = section["start_char"] + match.start(1)
                end = start + len(name)
                candidates.append(
                    ModelNameCandidate(
                        name=name,
                        source_rule="abstract_proposal_candidate",
                        section_name="abstract",
                        start_char=start,
                        end_char=end,
                        quote=_quote(text, start, end),
                        positive_signals=["abstract_proposal"],
                    )
                )
    return candidates


def _intro_method_model_candidates(text: str, sections: list[Section]) -> list[ModelNameCandidate]:
    candidates: list[ModelNameCandidate] = []
    allowed = {"introduction", "method", "methodology", "model"}
    for section in sections:
        if section["section_name"] not in allowed:
            continue
        for match in re.finditer(
            r"\b(?:we\s+propose|we\s+present|we\s+introduce|our\s+model|our\s+framework|proposed\s+framework)\s+(?:a\s+|an\s+|the\s+)?([A-Za-z][A-Za-z0-9._+-]{2,}(?:[- ][A-Za-z0-9._+]+)?)",
            section["text"],
            re.IGNORECASE,
        ):
            name = _clean_model_name(match.group(1))
            start = section["start_char"] + match.start(1)
            end = start + len(name)
            candidates.append(
                ModelNameCandidate(
                    name=name,
                    source_rule="intro_method_candidate",
                    section_name=section["section_name"],
                    start_char=start,
                    end_char=end,
                    quote=_quote(text, start, end),
                    positive_signals=["intro_method_proposal"],
                )
            )
    return candidates


def _named_model_candidates(text: str, sections: list[Section]) -> list[ModelNameCandidate]:
    candidates: list[ModelNameCandidate] = []
    patterns = [
        r"\b(?:model|framework|system|method)\s+(?:named|called|known as)\s+([A-Za-z][A-Za-z0-9._+-]{2,})",
        r"\b([A-Za-z][A-Za-z0-9._+-]{2,})\s+(?:model|framework|system)\b",
    ]
    for section in sections:
        if section["section_name"] == "title":
            continue
        for pattern in patterns:
            for match in re.finditer(pattern, section["text"], re.IGNORECASE):
                name = _clean_model_name(match.group(1))
                start = section["start_char"] + match.start(1)
                end = start + len(name)
                candidates.append(
                    ModelNameCandidate(
                        name=name,
                        source_rule="named_model_candidate",
                        section_name=section["section_name"],
                        start_char=start,
                        end_char=end,
                        quote=_quote(text, start, end),
                        positive_signals=["named_model_mention"],
                    )
                )
    return candidates


def _url_agreement_candidates(
    text: str,
    sections: list[Section],
    repo_url: str | None,
    model_card_url: str | None,
) -> list[ModelNameCandidate]:
    candidates: list[ModelNameCandidate] = []
    for url in [repo_url, model_card_url]:
        name = _name_from_url(url)
        if not name:
            continue
        for section in sections:
            if section["section_name"] in REFERENCE_SECTIONS:
                continue
            match = re.search(re.escape(name), section["text"], re.IGNORECASE)
            if match:
                start = section["start_char"] + match.start()
                end = section["start_char"] + match.end()
                candidates.append(
                    ModelNameCandidate(
                        name=section["text"][match.start() : match.end()],
                        source_rule="repo_model_card_agreement_candidate",
                        section_name=section["section_name"],
                        start_char=start,
                        end_char=end,
                        quote=_quote(text, start, end),
                        positive_signals=["repo_or_model_card_agreement"],
                    )
                )
                break
    return candidates


def _score_model_candidate(
    candidate: ModelNameCandidate,
    text: str,
    sections: list[Section],
    repo_url: str | None,
    model_card_url: str | None,
) -> ModelNameCandidate:
    score = 0.0
    section = candidate.section_name
    if section == "title":
        score += 0.55
    if "abstract_proposal" in candidate.positive_signals:
        score += 0.6
    if "intro_method_proposal" in candidate.positive_signals:
        score += 0.35
    if "repo_or_model_card_agreement" in candidate.positive_signals:
        score += 0.25
    if section in LOW_PRIORITY_MODEL_SECTIONS:
        score -= 0.35
        candidate.negative_signals.append("low_priority_section")
    if section in REFERENCE_SECTIONS:
        score -= 0.6
        candidate.negative_signals.append("references_or_appendix")
    context = _quote(text, candidate.start_char, candidate.end_char).lower()
    if _negative_model_context(context):
        score -= 0.45
        candidate.negative_signals.append("prior_work_or_baseline_context")
    if _citation_like_context(context):
        score -= 0.2
        candidate.negative_signals.append("citation_like_context")
    if any(_names_similar(candidate.name, _name_from_url(url)) for url in [repo_url, model_card_url]):
        score += 0.25
        candidate.positive_signals.append("url_basename_similarity")
    distribution = _model_name_distribution(candidate.name, sections)
    if len(distribution & {"title", "abstract", "method", "model", "results", "experiments", "evaluation"}) >= 2:
        score += 0.15
        candidate.positive_signals.append("distributed_in_positive_sections")
    if distribution and distribution <= REFERENCE_SECTIONS | LOW_PRIORITY_MODEL_SECTIONS:
        score -= 0.45
        candidate.negative_signals.append("only_low_quality_sections")
    candidate.score = max(0.0, min(1.0, score))
    candidate.confidence = max(0.2, min(0.92, 0.35 + candidate.score * 0.6))
    return candidate


def _candidate_to_span(candidate: ModelNameCandidate, sections: list[Section], *, selected: bool) -> dict[str, Any]:
    flags = []
    if _candidate_low_quality(candidate):
        if candidate.section_name in LOW_PRIORITY_MODEL_SECTIONS:
            flags.append("related_work_only")
        if candidate.section_name in REFERENCE_SECTIONS:
            flags.append("references_only")
        if "prior_work_or_baseline_context" in candidate.negative_signals:
            flags.append("baseline_only")
    return {
        "field": "model.name" if selected else "model.name.candidate",
        "value": candidate.name,
        "source": "paper_text",
        "quote": candidate.quote,
        "section_name": candidate.section_name,
        "start_char": candidate.start_char,
        "end_char": candidate.end_char,
        "confidence": candidate.confidence,
        "claim_type": "paper_field" if selected else "candidate_field",
        "candidate_status": "selected" if selected else "rejected",
        "source_rule": candidate.source_rule,
        "positive_signals": candidate.positive_signals,
        "negative_signals": candidate.negative_signals,
        "score": candidate.score,
        "quality_flags": flags,
    }


def _candidate_low_quality(candidate: ModelNameCandidate) -> bool:
    return bool(
        candidate.section_name in REFERENCE_SECTIONS
        or candidate.section_name in LOW_PRIORITY_MODEL_SECTIONS
        or "prior_work_or_baseline_context" in candidate.negative_signals
        or "only_low_quality_sections" in candidate.negative_signals
    )


def _model_name_tokens(text: str) -> list[tuple[str, int, int]]:
    tokens = []
    pattern = r"\b(?:[A-Z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*|[a-z]+[0-9][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*|[A-Za-z]+(?:ASR|Net|Former|Speech|Audio|VAD|Vec|LM|BERT|GPT)[A-Za-z0-9._+-]*)\b"
    for match in re.finditer(pattern, text):
        name = _clean_model_name(match.group(0))
        if _looks_like_model_name(name):
            tokens.append((name, match.start(), match.end()))
    return tokens


def _clean_model_name(name: str) -> str:
    name = re.split(r"\s+(?:for|that|which|with|to|on|using|by|from)\b|[,.;:()]", name.strip(), maxsplit=1)[0]
    return name.strip(" .,:;()[]{}\"'")


def _looks_like_model_name(name: str) -> bool:
    if not name or len(name) < 3:
        return False
    if name.lower() in {
        "abstract",
        "introduction",
        "related",
        "recently",
        "baseline",
        "model",
        "framework",
        "system",
        "method",
        "speech",
        "emotion",
        "recognition",
        "study",
        "audio",
        "table",
        "figure",
        "results",
        "repository",
        "github",
    }:
        return False
    return bool(
        re.search(r"[A-Z]", name)
        or re.search(r"\d", name)
        or "-" in name
        or "_" in name
        or re.search(r"(asr|net|former|speech|audio|vad|vec|lm|bert|gpt)$", name, re.IGNORECASE)
    )


def _negative_model_context(context: str) -> bool:
    return bool(
        re.search(
            r"previous work|prior work|existing model|baseline|compared with|compare with|recent work|proposed by|et al\. proposed|unlike|outperforming|from previous work",
            context,
            re.IGNORECASE,
        )
    )


def _citation_like_context(context: str) -> bool:
    return bool(re.search(r"\bet al\.\s*(?:\(\d{4}[a-z]?\))?", context, re.IGNORECASE))


def _model_name_distribution(name: str, sections: list[Section]) -> set[str]:
    distribution = set()
    if not name:
        return distribution
    pattern = re.compile(re.escape(name), re.IGNORECASE)
    for section in sections:
        if pattern.search(section["text"]):
            distribution.add(section["section_name"])
    return distribution


def _name_from_url(url: str | None) -> str | None:
    if not url:
        return None
    cleaned = url.rstrip("/).,")
    parts = [part for part in re.split(r"[/?#]", cleaned) if part]
    if not parts:
        return None
    last = parts[-1]
    if last.lower() in {"tree", "blob", "main", "master", "summary"} and len(parts) > 1:
        last = parts[-2]
    return re.sub(r"\.git$", "", last, flags=re.IGNORECASE)


def _extract_task(
    text: str, sections: list[Section], warnings: list[str]
) -> tuple[str, dict[str, Any] | None]:
    for section in sections:
        if section["section_name"] in REFERENCE_SECTIONS and _find_task_in_section(text, section, sections):
            _append_warning(warnings, "ignored_task_candidate_from_references")

    for section_name in TASK_SECTION_PRIORITY:
        for section in _sections_named(sections, section_name):
            candidate = _find_task_in_section(text, section, sections)
            if candidate:
                return candidate

    for section in sections:
        if section["section_name"] in REFERENCE_SECTIONS:
            continue
        candidate = _find_task_in_section(text, section, sections)
        if candidate:
            return candidate
    return "unknown", None


def _find_task_in_section(
    text: str, section: Section, sections: list[Section]
) -> tuple[str, dict[str, Any] | None] | None:
    section_text = section["text"]
    for task, needles in TASK_PATTERNS:
        for needle in needles:
            match = re.search(rf"\b{re.escape(needle)}\b", section_text, re.IGNORECASE)
            if match:
                start = section["start_char"] + match.start()
                end = section["start_char"] + match.end()
                return task, _span_from_offsets(text, "task.primary_task", task, start, end, 0.84, sections)
    return None


def _collect_keywords(
    text: str,
    candidates: list[str],
    field: str,
    sections: list[Section],
    *,
    allowed_sections: set[str] | None = None,
    exclude_references: bool = False,
    warnings: list[str] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    values: list[str] = []
    spans: list[dict[str, Any]] = []
    for section in sections:
        section_name = section["section_name"]
        if exclude_references and section_name in REFERENCE_SECTIONS:
            if warnings and _section_has_any_keyword(section["text"], candidates):
                _append_warning(warnings, f"ignored_{field.split('.')[-1].rstrip('s')}_from_references")
            continue
        if allowed_sections and section_name not in allowed_sections:
            continue
        for candidate in candidates:
            pattern = rf"\b{re.escape(candidate)}\b"
            if candidate == "mAP":
                pattern = r"\bmAP\b"
            match = re.search(pattern, section["text"], re.IGNORECASE)
            if match and candidate not in values:
                start = section["start_char"] + match.start()
                end = section["start_char"] + match.end()
                values.append(candidate)
                spans.append(_span_from_offsets(text, field, candidate, start, end, 0.75, sections))
    return values, spans


def _collect_dataset_candidates(
    text: str,
    sections: list[Section],
    warnings: list[str],
) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    datasets_by_field: dict[str, list[str]] = {
        "train_datasets": [],
        "pretrain_datasets": [],
        "upstream_initialization_corpus": [],
        "downstream_datasets": [],
        "eval_datasets": [],
        "test_datasets": [],
    }
    spans: list[dict[str, Any]] = []
    seen_candidates: set[tuple[str, str]] = set()

    for section in sections:
        section_name = str(section["section_name"])
        if section_name in REFERENCE_SECTIONS:
            if _section_has_any_keyword(section["text"], DATASET_PATTERNS):
                _append_warning(warnings, "ignored_dataset_from_references")
            continue
        if section_name not in DATASET_CANDIDATE_SECTIONS:
            continue

        for dataset in sorted(DATASET_PATTERNS, key=len, reverse=True):
            pattern = rf"\b{re.escape(dataset)}\b"
            if dataset == "AISHELL":
                pattern = r"\bAISHELL\b(?!-)"
            for match in re.finditer(pattern, section["text"], re.IGNORECASE):
                usage_type = _classify_dataset_usage(section["text"], match, section_name)
                value = dataset
                start = section["start_char"] + match.start()
                end = section["start_char"] + match.end()
                field = "data.dataset_candidates"
                confidence = 0.45
                selected = False
                quality_flags: list[str] = []

                if usage_type in {"eval", "test"}:
                    field = "data.test_datasets" if usage_type == "test" else "data.eval_datasets"
                    confidence = 0.78
                    selected = True
                    bucket = "test_datasets" if usage_type == "test" else "eval_datasets"
                    if value not in datasets_by_field[bucket]:
                        datasets_by_field[bucket].append(value)
                elif usage_type in {"train", "pretrain", "upstream_initialization", "downstream"}:
                    bucket = {
                        "train": "train_datasets",
                        "pretrain": "pretrain_datasets",
                        "upstream_initialization": "upstream_initialization_corpus",
                        "downstream": "downstream_datasets",
                    }[usage_type]
                    field = f"data.{bucket}"
                    confidence = 0.72 if usage_type != "upstream_initialization" else 0.58
                    selected = True
                    if value not in datasets_by_field[bucket]:
                        datasets_by_field[bucket].append(value)
                    if usage_type in {"pretrain", "upstream_initialization"}:
                        quality_flags.append("pretraining_corpus")
                else:
                    quality_flags.append("unknown_usage")

                if usage_type == "upstream_initialization":
                    quality_flags.extend(["upstream_initialization", "not_target_training_data"])

                key = (field, value)
                if key in seen_candidates:
                    continue
                seen_candidates.add(key)
                span = _span_from_offsets(text, field, value, start, end, confidence, sections)
                span.update(
                    {
                        "usage_type": usage_type,
                        "claim_type": "paper_field" if selected else "candidate_field",
                        "candidate_status": "selected" if selected else "rejected",
                        "quality_flags": quality_flags,
                    }
                )
                spans.append(span)

                if field == "data.dataset_candidates":
                    _append_warning(warnings, f"dataset_usage_unknown:{value}")
                if field == "data.eval_datasets" and usage_type not in {"eval", "test"}:
                    _append_warning(warnings, f"eval_dataset_usage_conflict:{value}")
                break

    return datasets_by_field, spans


def _classify_dataset_usage(section_text: str, match: re.Match[str], section_name: str) -> str:
    context = section_text[max(0, match.start() - 140) : min(len(section_text), match.end() + 140)].lower()
    before_context = section_text[max(0, match.start() - 100) : match.start()].lower()
    table_header = section_text[max(0, match.start() - 220) : match.start()].lower()
    pretrain = re.search(
        r"pre[-\s]?train|self[-\s]?supervised|unlabeled|unlabelled|corpus|pre-training corpus",
        context + " " + table_header,
    )
    upstream_initialization = re.search(
        r"initial model|initial models|initialized|initializ(?:e|ed|ation)|upstream model|upstream features|ssl pre[-\s]?trained|pre[-\s]?trained models?|ls-960|ll-60k|mix-94k|\bmeans\b",
        context + " " + table_header,
    )
    upstream_initialization = upstream_initialization or re.search(
        r"baseline|prior work|previous work|table\s+\d+|comparison",
        context + " " + table_header,
    )
    direct_eval_before = re.search(
        r"\b(?:we\s+)?(?:evaluat(?:e|ed|ing)|test(?:ed|ing)?|benchmark(?:ed|ing)?)\b[\s\S]{0,70}\bon\s*$",
        before_context,
    )
    if direct_eval_before:
        if re.search(r"\btest(?:ed|ing)?\b|test set|held[-\s]?out", before_context):
            return "test"
        return "eval"
    strong_upstream_before = re.search(
        r"initial models?|upstream models?|ssl pre[-\s]?trained|ls-960|ll-60k|mix-94k|\bmeans\b",
        before_context,
    )
    if upstream_initialization and strong_upstream_before and re.search(r"\b(?:hours?|corpus|data|dataset|datasets)\b", context):
        return "upstream_initialization"
    if re.search(
        r"\b(?:downstream|linear layer|linear layers|representation ability|task\s+on|tested in the downstream task)\b",
        context,
    ):
        return "downstream"
    if upstream_initialization and (pretrain or re.search(r"\b(?:hours?|corpus|data|dataset|datasets)\b", context)):
        return "upstream_initialization"
    if pretrain:
        return "pretrain"
    if re.search(r"\b(?:validation|valid|dev)\s+(?:set|split|dataset)|development set", context):
        return "validation"
    if section_name in EVAL_DATASET_SECTIONS and re.search(r"\b(?:task|benchmark)\s+on\b|on\s+the\s+(?:mainstream\s+)?[a-z0-9-]+\s+dataset", context):
        return "eval"
    if re.search(r"\b(?:train|training|fine[-\s]?tun(?:e|ing)|supervised training)\b", context):
        return "train"
    if re.search(
        r"\b(?:evaluat(?:e|ed|ion|ing)|test(?:ed|ing)?|benchmark(?:ed|ing)?|downstream|results?|performance|score|metric)\b",
        context,
    ):
        if re.search(r"\btest(?:ed|ing)?\b|test set|held[-\s]?out", context):
            return "test"
        return "eval"
    if section_name in EVAL_DATASET_SECTIONS and re.search(r"\b(?:task|dataset|corpus|benchmark)\b", context):
        return "eval"
    return "unknown"


def _goal_intent(raw_goal: str) -> str:
    goal = raw_goal.lower()
    if "controlled" in goal or "train" in goal:
        return "controlled_training"
    if "evaluate" in goal or "benchmark" in goal:
        return "evaluate"
    if "reproduce" in goal:
        return "reproduce"
    if "compare" in goal:
        return "compare"
    if "onboard" in goal or "tool" in goal:
        return "onboard"
    return "unknown"


def _runtime_hints(text: str, sections: list[Section]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    spans: list[dict[str, Any]] = []
    backend = "unknown"
    for value, pattern in [
        ("uv", r"\buv\b"),
        ("pixi", r"\bpixi\b"),
        ("conda", r"\bconda\b|environment\.ya?ml"),
        ("docker", r"\bdocker\b|dockerfile"),
    ]:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            backend = value
            spans.append(_span(text, "runtime.backend_hint", value, match, 0.72, sections=sections))
            break

    py_match = re.search(r"python\s*(?:version)?\s*(3\.\d+)", text, re.IGNORECASE)
    python_version = py_match.group(1) if py_match else "unknown"
    if py_match:
        spans.append(_span(text, "runtime.python_version", python_version, py_match, 0.74, sections=sections))

    gpu_match = re.search(r"\b(gpu|cuda)\b", text, re.IGNORECASE)
    requires_gpu = bool(gpu_match)
    if gpu_match:
        spans.append(_span(text, "runtime.requires_gpu", True, gpu_match, 0.72, sections=sections))

    system_packages = []
    for pkg in ["ffmpeg", "libsndfile1", "sox"]:
        match = re.search(rf"\b{pkg}\b", text, re.IGNORECASE)
        if match:
            system_packages.append(pkg)
            spans.append(_span(text, "runtime.system_packages", pkg, match, 0.7, sections=sections))

    return (
        {
            "requires_gpu": requires_gpu,
            "python_version": python_version,
            "backend_hint": backend,
            "network_required": False,
            "estimated_storage_gb": "unknown",
            "system_packages": system_packages,
        },
        spans,
    )


def _extract_repo_url(
    text: str, sections: list[Section], repo_url: str | None
) -> tuple[str | None, dict[str, Any] | None]:
    found_urls: list[tuple[str, dict[str, Any]]] = []
    for section in sections:
        if section["section_name"] in REFERENCE_SECTIONS:
            continue
        for match in re.finditer(
            r"https?://(?:www\.)?(?:github\.com|gitlab\.com|huggingface\.co|modelscope\.cn|modelscope\.ai)/[^\s)>\]]+",
            section["text"],
            re.IGNORECASE,
        ):
            value = match.group(0).rstrip(".,")
            start = section["start_char"] + match.start()
            end = section["start_char"] + match.end()
            found_urls.append((value, _span_from_offsets(text, "source.repo_url", value, start, end, 0.9, sections)))
    if found_urls:
        return found_urls[0]
    if repo_url:
        return repo_url, None
    return repo_url, None


def _normalize_url_for_match(url: str | None) -> str:
    cleaned = (url or "").strip().lower().rstrip("/).,")
    cleaned = re.sub(r"^https?://(?:www\.)?", "", cleaned)
    cleaned = re.sub(r"\.git$", "", cleaned)
    return cleaned


def _urls_similar(left: str | None, right: str | None) -> bool:
    l_norm = _normalize_url_for_match(left)
    r_norm = _normalize_url_for_match(right)
    if not l_norm or not r_norm:
        return False
    return l_norm == r_norm or l_norm in r_norm or r_norm in l_norm


def _extract_model_card_url(
    text: str,
    sections: list[Section],
    model_name: str,
    model_card_url: str | None,
    warnings: list[str],
) -> tuple[str | None, str, dict[str, Any] | None]:
    if model_card_url:
        return model_card_url, model_card_url, None

    fallback: tuple[str, dict[str, Any]] | None = None
    for section in sections:
        section_is_reference = section["section_name"] in REFERENCE_SECTIONS
        for match in re.finditer(r"https?://huggingface\.co/[^\s)>\]]+", section["text"]):
            url = match.group(0).rstrip(".,")
            start = section["start_char"] + match.start()
            end = section["start_char"] + match.end()
            if _is_hf_blog_url(url):
                _append_warning(warnings, "ignored_model_card_blog_url")
                continue
            if section_is_reference:
                _append_warning(warnings, "ignored_model_card_url_from_references")
                continue
            if not _is_hf_model_like_url(url):
                continue
            span = _span_from_offsets(text, "source.model_card_url", url, start, end, 0.86, sections)
            if _url_related_to_model(url, model_name):
                return url, url, span
            if fallback is None:
                fallback = (url, span)

    if fallback:
        url, span = fallback
        return url, url, span
    return None, "unknown", None


def _is_hf_blog_url(url: str) -> bool:
    return re.match(r"https?://huggingface\.co/blog(?:/|$)", url, re.IGNORECASE) is not None


def _is_hf_model_like_url(url: str) -> bool:
    match = re.match(r"https?://huggingface\.co/([^/\s]+)/([^/\s#?]+)", url, re.IGNORECASE)
    if not match:
        return False
    org = match.group(1).lower()
    return org not in {"blog", "docs", "papers", "spaces", "datasets"}


def _url_related_to_model(url: str, model_name: str) -> bool:
    if not model_name or model_name == "unknown":
        return True
    normalized_url = re.sub(r"[^a-z0-9]+", "", url.lower())
    normalized_model = re.sub(r"[^a-z0-9]+", "", model_name.lower())
    return bool(normalized_model and normalized_model in normalized_url)


def _collect_metrics(
    text: str, sections: list[Section], warnings: list[str]
) -> tuple[list[str], list[dict[str, Any]]]:
    values: list[str] = []
    spans: list[dict[str, Any]] = []
    for section in sections:
        if section["section_name"] in REFERENCE_SECTIONS:
            if _section_has_any_keyword(section["text"], METRIC_PATTERNS):
                _append_warning(warnings, "ignored_metric_from_references")
            continue
        if section["section_name"] not in METRIC_SECTIONS:
            continue
        for metric in METRIC_PATTERNS:
            pattern = rf"\b{re.escape(metric)}\b"
            if metric == "mAP":
                pattern = r"\bmAP\b"
            for match in re.finditer(pattern, section["text"], re.IGNORECASE):
                if _ignore_metric_match(metric, section["text"], match, warnings):
                    continue
                if metric not in values:
                    start = section["start_char"] + match.start()
                    end = section["start_char"] + match.end()
                    values.append(metric)
                    spans.append(_span_from_offsets(text, "evaluation.metrics", metric, start, end, 0.75, sections))
                break
    return values, spans


def _ignore_metric_match(metric: str, section_text: str, match: re.Match[str], warnings: list[str]) -> bool:
    context = section_text[max(0, match.start() - 35) : min(len(section_text), match.end() + 35)].lower()
    metric_lower = metric.lower()
    if metric_lower == "precision" and re.search(r"\b(?:bfloat16|float16|fp16|fp32|float32|dtype|compute)\s+precision\b", context):
        _append_warning(warnings, "ignored_metric_dtype_precision")
        return True
    if metric == "mAP" and not re.search(r"\b(?:metric|score|evaluation|report|achieves?|improves?|mean average precision)\b", context):
        _append_warning(warnings, "ignored_metric_map_context")
        return True
    if metric_lower == "accuracy" and not re.search(r"\b(?:metric|score|evaluation|reports?|achieves?|improves?|accuracy of|accuracy on)\b", context):
        _append_warning(warnings, "ignored_metric_generic_accuracy")
        return True
    return False


def _extract_license(
    text: str, sections: list[Section], warnings: list[str]
) -> tuple[str, dict[str, Any] | None]:
    pattern = r"\b(MIT|Apache-2\.0|BSD-3-Clause|GPL-3\.0|CC-BY(?:-[A-Z0-9.]+)?)\b"
    for section in sections:
        match = re.search(pattern, section["text"], re.IGNORECASE)
        if not match:
            continue
        if section["section_name"] in REFERENCE_SECTIONS:
            _append_warning(warnings, "ignored_license_from_references")
            continue
        value = match.group(1)
        start = section["start_char"] + match.start()
        end = section["start_char"] + match.end()
        return value, _span_from_offsets(text, "model.license", value, start, end, 0.7, sections)
    return "unknown", None


def extract_user_spec(
    *,
    case_id: str,
    paper_text: str,
    raw_goal: str,
    paper_path: str | None = None,
    paper_text_path: str | None = None,
    repo_url: str | None = None,
    model_card_url: str | None = None,
    extraction_timestamp: str | None = None,
    extracted_from: str = "paper_text",
) -> dict[str, Any]:
    """Extract a conservative user_spec_query object from paper text."""
    evidence: list[dict[str, Any]] = []
    warnings: list[str] = []
    sections = segment_paper_sections(paper_text)

    paper_title, title_span = _extract_title(paper_text, sections)
    if title_span:
        evidence.append(title_span)

    repo_url, repo_span = _extract_repo_url(paper_text, sections, repo_url)
    if repo_span:
        evidence.append(repo_span)

    model_name, model_span, model_diagnostics, model_conflict = _extract_model_name(
        paper_text,
        case_id,
        sections,
        repo_url=repo_url,
        model_card_url=model_card_url,
        warnings=warnings,
    )
    if model_span:
        evidence.append(model_span)
    evidence.extend(model_diagnostics)

    task, task_span = _extract_task(paper_text, sections, warnings)
    if task_span:
        evidence.append(task_span)

    dataset_usage, dataset_spans = _collect_dataset_candidates(paper_text, sections, warnings)
    evidence.extend(dataset_spans)
    metrics, metric_spans = _collect_metrics(paper_text, sections, warnings)
    evidence.extend(metric_spans)

    model_card_url, checkpoint_source, card_span = _extract_model_card_url(
        paper_text,
        sections,
        model_name,
        model_card_url,
        warnings,
    )
    if card_span:
        evidence.append(card_span)

    license_value, license_span = _extract_license(paper_text, sections, warnings)
    if license_span:
        evidence.append(license_span)

    runtime, runtime_spans = _runtime_hints(paper_text, sections)
    evidence.extend(runtime_spans)

    train_words = ["training recipe", "train", "fine-tune", "optimizer", "loss function", "scheduler"]
    has_training = any(word in paper_text.lower() for word in train_words)

    missing_fields: list[str] = []
    for field, value in [
        ("source.paper_title", paper_title),
        ("model.name", model_name),
        ("task.primary_task", task),
        ("source.repo_url", repo_url or ""),
    ]:
        if value in ("", None, "unknown"):
            missing_fields.append(field)

    if task == "TTS":
        input_modality = ["text", "audio"]
        output_type = "audio_path"
    else:
        input_modality = ["audio"] if task not in {"VLM", "utility", "unknown"} else ["unknown"]
        output_type = "json" if task != "unknown" else "unknown"
    local_name = re.sub(r"[^a-z0-9_]+", "_", model_name.lower()).strip("_") or case_id

    timestamp = (
        extraction_timestamp
        or os.environ.get("SURE_PAPER_TO_USERSPEC_TIMESTAMP")
        or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    )

    user_spec = {
        "spec_version": SPEC_VERSION,
        "case_id": case_id,
        "source": {
            "paper_title": paper_title,
            "paper_path": paper_path,
            "paper_text_path": paper_text_path,
            "repo_url": repo_url,
            "model_card_url": model_card_url,
            "extracted_from": extracted_from,
            "extraction_timestamp": timestamp,
        },
        "user_goal": {"intent": _goal_intent(raw_goal), "raw_goal": raw_goal},
        "model": {
            "name": model_name,
            "family": "unknown",
            "checkpoint_source": checkpoint_source,
            "deployment_type": "local",
            "license": license_value,
            "local_sure_model_name": local_name,
        },
        "task": {
            "primary_task": task,
            "secondary_tasks": [],
            "input_modality": input_modality,
            "output_type": output_type,
        },
        "data": {
            "train_datasets": dataset_usage["train_datasets"],
            "pretrain_datasets": dataset_usage["pretrain_datasets"],
            "upstream_initialization_corpus": dataset_usage["upstream_initialization_corpus"],
            "downstream_datasets": dataset_usage["downstream_datasets"],
            "eval_datasets": dataset_usage["eval_datasets"],
            "test_datasets": dataset_usage["test_datasets"],
            "languages": [],
            "split_policy": "unknown",
        },
        "runtime": runtime,
        "evaluation": {
            "metrics": metrics,
            "normalization": "unknown",
            "expected_report": "dry_run_contract_report",
        },
        "sure_routing": {
            "route": "needs_human_input",
            "reason": "routing_not_evaluated",
            "next_artifact": "missing_information_request.json",
            "downstream_flow": "human_review",
        },
        "confidence": {
            "overall": 0.0,
            "overall_percent": 0,
            "extraction": "heuristic",
            "scoring_version": "reproducibility_confidence_v1",
            "paper_evidence_score": 0,
            "decision_hint": "D",
            "human_review_required": False,
            "training_recipe_indicated": has_training,
            "extraction_warnings": warnings,
            "confidence_warnings": [],
            "evidence_card_ids": [],
        },
        "missing_fields": missing_fields,
        "conflict_fields": ["model.name"] if model_conflict else [],
        "evidence_spans": evidence,
    }
    evidence_cards = [*spans_to_paper_evidence_cards(evidence), *_user_provided_url_cards(user_spec, len(evidence) + 1)]
    user_spec["confidence"], _report = confidence_for_user_spec(user_spec, evidence_cards)
    return user_spec


def _user_provided_url_cards(user_spec: dict[str, Any], start_index: int) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    paper_fields = {span.get("field") for span in user_spec.get("evidence_spans", []) if isinstance(span, dict)}
    for field, value in [
        ("source.repo_url", user_spec.get("source", {}).get("repo_url")),
        ("source.model_card_url", user_spec.get("source", {}).get("model_card_url")),
    ]:
        if not value or field in paper_fields:
            continue
        cards.append(
            {
                "id": f"user_ev_{start_index + len(cards):04d}",
                "field": field,
                "claim_type": "user_provided_field",
                "claim_text": f"{field} provided by user",
                "evidence_text": str(value),
                "source_type": "user_provided",
                "source_name": "cli_argument",
                "source_url": str(value) if str(value).startswith("http") else None,
                "section_name": None,
                "confidence": 0.6,
            }
        )
    return cards


def extract_training_signals(user_spec: dict[str, Any]) -> dict[str, Any]:
    """Return fields used by training_conversion_request.json."""
    return {
        "paper_title": user_spec["source"].get("paper_title"),
        "repo_url": user_spec["source"].get("repo_url"),
        "model_name": user_spec["model"].get("name"),
        "training_datasets": user_spec["data"].get("train_datasets", []),
        "pretraining_datasets": user_spec["data"].get("pretrain_datasets", []),
        "upstream_initialization_corpus": user_spec["data"].get("upstream_initialization_corpus", []),
        "downstream_datasets": user_spec["data"].get("downstream_datasets", []),
        "evaluation_datasets": user_spec["data"].get("eval_datasets", []),
        "test_datasets": user_spec["data"].get("test_datasets", []),
        "loss_functions": [],
        "metrics": user_spec["evaluation"].get("metrics", []),
        "optimizer": "unknown",
        "scheduler": "unknown",
        "expected_swift_recipe_fields": [
            "model",
            "dataset",
            "loss",
            "optimizer",
            "scheduler",
            "evaluation",
        ],
        "missing_fields": user_spec.get("missing_fields", []),
        "evidence_spans": user_spec.get("evidence_spans", []),
    }


def source_path_string(path: str | None) -> str | None:
    return str(Path(path)) if path else None
