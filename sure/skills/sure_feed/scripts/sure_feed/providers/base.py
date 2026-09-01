from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from sure_feed.fixture_registry import select_fixture_for_task


NETWORK_ERROR_TYPES = (TimeoutError, socket.timeout, urllib.error.URLError)


class ProviderError(RuntimeError):
    """Provider failed for a non-retryable reason."""


class ProviderNetworkError(ProviderError):
    """Provider endpoint was unreachable or transiently unavailable."""


@dataclass(frozen=True)
class ProviderRequest:
    source: str
    query: str
    task: str
    max_models: int


def slugify(value: str) -> str:
    value = value.strip().replace("/", "__")
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return value.strip("._-") or "model"


def canonical_task(task: str) -> str:
    value = task.strip().lower().replace("_", "-")
    if value in {"", "auto", "unknown"}:
        return "auto"
    aliases = {
        "automatic-speech-recognition": "asr",
        "auto-speech-recognition": "asr",
        "speech-to-text": "asr",
        "audio-text-to-text": "speech_understanding",
        "audio-to-text": "asr",
        "speech-understanding": "speech_understanding",
        "speech understanding": "speech_understanding",
        "speaker-attributed-asr": "sa_asr",
        "speaker-attributed asr": "sa_asr",
        "speaker-aware-asr": "sa_asr",
        "speaker-aware asr": "sa_asr",
        "speaker-attributed speech recognition": "sa_asr",
        "transcribe-diarize": "sa_asr",
        "transcribe diarize": "sa_asr",
        "speech-translation": "s2tt",
        "speech-to-text-translation": "s2tt",
        "text-to-speech": "tts",
        "speech-synthesis": "tts",
        "voice-conversion": "vc",
        "keyword-spotting": "kws",
        "wake-word": "kws",
        "emotion-recognition": "ser",
        "speech-emotion-recognition": "ser",
        "speaker-diarization": "sd",
        "gender-recognition": "gr",
        "voice-activity-detection": "vad",
        "voice activity detection": "vad",
        "speech-activity-detection": "vad",
        "speech activity detection": "vad",
    }
    return aliases.get(value, value.replace("-", "_") if value == "sa-asr" else value)


TASK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "asr": (
        "asr",
        "automatic-speech-recognition",
        "auto-speech-recognition",
        "speech recognition",
        "speech-recognition",
        "automatic speech recognition",
        "speech-to-text",
        "speech to text",
        "transcribe",
        "transcription",
        "whisper",
        "paraformer",
    ),
    "s2tt": ("s2tt", "speech translation", "speech-to-text translation", "speech translate"),
    "ser": ("ser", "speech emotion", "speaker emotion", "emotion-recognition"),
    "slu": ("slu", "spoken language understanding", "speech understanding"),
    "gr": ("gender recognition", "speaker gender", "gender-recognition"),
    "sd": ("speaker diarization", "diarization", "speaker-diarization"),
    "sa_asr": (
        "sa-asr",
        "sa_asr",
        "speaker-attributed asr",
        "speaker attributed asr",
        "speaker-aware asr",
        "speaker aware asr",
        "speaker-attributed speech recognition",
        "transcribe-diarize",
        "transcribe diarize",
        "transcription diarization",
    ),
    "tts": ("tts", "text-to-speech", "text to speech", "speech synthesis"),
    "vc": ("voice conversion", "voice-conversion", "timbre conversion", "speech conversion"),
    "kws": ("kws", "keyword spotting", "wake word", "wake-word"),
    "vad": (
        "vad",
        "voice activity detection",
        "voice-activity-detection",
        "speech activity detection",
        "speech-activity-detection",
        "silero vad",
        "silero-vad",
    ),
}


PIPELINE_TO_TASK = {
    "automatic-speech-recognition": "asr",
    "audio-text-to-text": "speech_understanding",
    "audio-to-text": "asr",
    "text-to-speech": "tts",
    "text-to-audio": "tts",
    "audio-to-audio": "vc",
    "voice-activity-detection": "vad",
    "speech-activity-detection": "vad",
}

BROAD_PIPELINE_TASKS = {"speech_understanding"}

COMMON_IMPORT_PREFIXES = (
    "from dataclasses",
    "from pathlib",
    "from tqdm",
    "from typing",
    "import argparse",
    "import dataclasses",
    "import hydra",
    "import json",
    "import logging",
    "import numpy",
    "import os",
    "import random",
    "import re",
    "import sys",
    "import torch",
    "import librosa",
    "import soundfile",
)

POLICY_DEFAULT_PYTHON_VERSION = "3.10"


def http_get_json(url: str, headers: dict[str, str] | None = None, timeout: int = 20) -> Any:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in {500, 502, 503, 504}:
            raise ProviderNetworkError(f"{url} returned HTTP {exc.code}") from exc
        raise ProviderError(f"{url} returned HTTP {exc.code}") from exc
    except NETWORK_ERROR_TYPES as exc:
        raise ProviderNetworkError(f"{url} is unreachable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ProviderError(f"{url} did not return valid JSON: {exc}") from exc


def http_get_text(url: str, headers: dict[str, str] | None = None, timeout: int = 20, max_bytes: int = 100_000) -> str:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(max_bytes).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code in {404, 401, 403}:
            return ""
        if exc.code in {500, 502, 503, 504}:
            raise ProviderNetworkError(f"{url} returned HTTP {exc.code}") from exc
        raise ProviderError(f"{url} returned HTTP {exc.code}") from exc
    except NETWORK_ERROR_TYPES:
        return ""


def append_query(endpoint: str, path: str, params: dict[str, Any]) -> str:
    base = endpoint.rstrip("/") + "/" + path.lstrip("/")
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    return f"{base}?{query}" if query else base


def evidence(source: str, field: str, value: Any, strength: str = "medium", url: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"source": source, "field": field, "value": value, "strength": strength}
    if url:
        item["url"] = url
    return item


def _candidate_haystack(candidate: dict[str, Any]) -> dict[str, str]:
    return {
        "tags": " ".join(str(item) for item in candidate.get("tags") or []),
        "description": str(candidate.get("description") or ""),
        "model_id": str(candidate.get("model_id") or ""),
        "repo": str(candidate.get("repo") or ""),
        "model_card": str(candidate.get("model_card_text") or candidate.get("readme") or ""),
        "tasks": " ".join(str(item) for item in candidate.get("tasks") or []),
    }


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def _without_negated_vad(text: str) -> str:
    return re.sub(
        r"(?:\bno|\bwithout(?:\s+a)?|\bdisabled?)\s*[-_]?\s*"
        r"(?:vad|voice[- ]activity[- ]detection|speech[- ]activity[- ]detection)\b",
        "",
        text.lower(),
    )


def _keyword_matches(task: str, text: str, keyword: str) -> bool:
    if task == "vad":
        lowered = _without_negated_vad(text)
        if keyword == "vad":
            return re.search(r"(?<![a-z0-9])vad(?![a-z0-9])", lowered) is not None
        return keyword.lower() in lowered
    return keyword.lower() in text.lower()


def _strong_vad_match(candidate: dict[str, Any]) -> tuple[str, Any] | None:
    haystack = _candidate_haystack(candidate)
    semantic_terms = (
        "voice activity detection",
        "voice-activity-detection",
        "speech activity detection",
        "speech-activity-detection",
    )
    transcription_terms = (
        "automatic speech recognition",
        "speech-to-text",
        "speech to text",
        "transcribe",
        "transcription",
    )
    combined = "\n".join(haystack.values()).lower()
    for field in ("model_id", "repo"):
        value = haystack[field]
        identity = re.sub(r"[^a-z0-9]+", "", value.lower().rstrip("/"))
        if identity.endswith("vad") and not identity.endswith("novad"):
            return field, value
    for field in ("tasks", "tags"):
        value = haystack[field]
        lowered = _without_negated_vad(value)
        if _contains_any(lowered, semantic_terms) or _keyword_matches("vad", lowered, "vad"):
            return field, value
    cleaned = _without_negated_vad(combined)
    if _contains_any(cleaned, semantic_terms) and not _contains_any(cleaned, transcription_terms):
        return "model_card", haystack["model_card"] or haystack["description"]
    return None


def _narrow_task_from_research(
    candidate: dict[str, Any],
    broad_task: str,
    target: str,
    source: str,
    url: str,
) -> tuple[str | None, float, list[dict[str, Any]], str | None]:
    """Resolve broad provider task tags using model-id/tag/card evidence.

    Provider taxonomies such as HuggingFace's audio-text-to-text are useful
    evidence, but too broad to be the final SURE task when the card/tags expose
    a narrower capability.
    """
    haystack = _candidate_haystack(candidate)
    combined = "\n".join(haystack.values()).lower()
    transcription_terms = (
        "asr",
        "automatic speech recognition",
        "speech-to-text",
        "speech to text",
        "transcribe",
        "transcription",
        "timestamp-asr",
    )
    diarization_terms = (
        "speaker diarization",
        "diarization",
        "diarisation",
        "diarize",
        "diarise",
        "transcribe-diarize",
        "transcribe diarize",
    )
    speaker_attributed_terms = (
        "speaker-attributed",
        "speaker attributed",
        "speaker-aware asr",
        "who spoke what",
    )
    has_transcription = _contains_any(combined, transcription_terms)
    has_diarization = _contains_any(combined, diarization_terms)
    has_speaker_attribution = _contains_any(combined, speaker_attributed_terms)

    ev: list[dict[str, Any]] = []
    if broad_task in BROAD_PIPELINE_TASKS:
        pipeline_tag = str(candidate.get("pipeline_tag") or "")
        ev.append(evidence(source, "pipeline_tag", pipeline_tag, "strong", url))
        ev.append(evidence(source, "task_narrowing.broad_task", broad_task, "strong", url))

    if has_transcription and (has_diarization or has_speaker_attribution):
        if target in {"auto", "sa_asr", "speech_understanding"}:
            for field in ("model_id", "tags", "model_card"):
                value = haystack.get(field, "")
                lowered = value.lower()
                if _contains_any(lowered, transcription_terms) and (
                    _contains_any(lowered, diarization_terms) or _contains_any(lowered, speaker_attributed_terms)
                ):
                    ev.append(evidence(source, field, value[:500], "strong" if field != "model_card" else "medium", url))
            ev.append(
                evidence(
                    source,
                    "task_narrowing.final_task",
                    "sa_asr",
                    "strong",
                    url,
                )
            )
            return "sa_asr", 0.98, ev, "research_narrowing"

    if has_diarization and target in {"auto", "sd", "speech_understanding"}:
        ev.append(evidence(source, "task_narrowing.final_task", "sd", "strong", url))
        return "sd", 0.93, ev, "research_narrowing"

    if has_transcription and target in {"auto", "asr", "speech_understanding"}:
        ev.append(evidence(source, "task_narrowing.final_task", "asr", "medium", url))
        return "asr", 0.88, ev, "research_narrowing"

    if broad_task in BROAD_PIPELINE_TASKS:
        ev.append(evidence(source, "task_narrowing.no_narrower_task", True, "medium", url))
    return None, 0.0, ev, None


def infer_task(candidate: dict[str, Any], requested_task: str) -> tuple[bool, str, float, list[dict[str, Any]], str]:
    target = canonical_task(requested_task)
    ev: list[dict[str, Any]] = []
    source = str(candidate.get("source") or candidate.get("provider") or "manual")
    url = str(candidate.get("source_url") or candidate.get("repo") or "")

    pipeline_tag = str(candidate.get("pipeline_tag") or "")
    pipeline_task = PIPELINE_TO_TASK.get(pipeline_tag)
    if pipeline_task in BROAD_PIPELINE_TASKS:
        narrowed, score, narrowing_ev, match_source = _narrow_task_from_research(candidate, pipeline_task, target, source, url)
        if narrowed:
            return True, narrowed, score, narrowing_ev, match_source or "research_narrowing"
        if target == "auto":
            ev.extend(narrowing_ev)
            return True, pipeline_task, 0.78, ev, "pipeline_tag_with_research"
    if target == "auto" and pipeline_task:
        ev.append(evidence(source, "pipeline_tag", pipeline_tag, "strong", url))
        return True, pipeline_task, 0.95, ev, "pipeline_tag"
    if pipeline_task == target:
        ev.append(evidence(source, "pipeline_tag", pipeline_tag, "strong", url))
        return True, target, 0.95, ev, "pipeline_tag"

    if target == "vad" and pipeline_task == "asr":
        ev.append(evidence(source, "pipeline_tag", pipeline_tag, "strong", url))
        return False, target, 0.0, ev, "pipeline_tag_conflict"

    structured_tasks = [
        (task_value, canonical_task(str(task_value)))
        for task_value in candidate.get("tasks") or []
    ]
    if any(task == "asr" for _, task in structured_tasks) and any(
        task == "vad" for _, task in structured_tasks
    ):
        task_value = next(value for value, task in structured_tasks if task == "asr")
        ev.append(evidence(source, "tasks", task_value, "strong", url))
        if target == "auto":
            return True, "asr", 0.9, ev, "tasks"
        if target == "vad":
            return False, target, 0.0, ev, "tasks_conflict"

    if target in {"auto", "vad"}:
        vad_match = _strong_vad_match(candidate)
        if vad_match is not None:
            field, value = vad_match
            ev.append(evidence(source, field, value, "strong", url))
            return True, "vad", 0.94, ev, field

    for task_value in candidate.get("tasks") or []:
        text = str(task_value).lower()
        canonical = canonical_task(text)
        if target == "auto" and canonical != "auto":
            ev.append(evidence(source, "tasks", task_value, "strong", url))
            return True, canonical, 0.9, ev, "tasks"
        if canonical_task(text) == target or any(
            _keyword_matches(target, text, keyword) for keyword in TASK_KEYWORDS.get(target, ())
        ):
            ev.append(evidence(source, "tasks", task_value, "strong", url))
            return True, target, 0.9, ev, "tasks"

    haystack_fields = _candidate_haystack(candidate)
    if target == "auto":
        for task_name, keywords in TASK_KEYWORDS.items():
            for field, value in haystack_fields.items():
                lowered = value.lower()
                for keyword in keywords:
                    if _keyword_matches(task_name, lowered, keyword):
                        strength = "medium" if field == "tags" else "weak"
                        ev.append(evidence(source, field, keyword, strength, url))
                        return True, task_name, 0.72 if strength == "medium" else 0.55, ev, field
        return False, "auto", 0.0, ev, ""

    keywords = TASK_KEYWORDS.get(target, (target,))
    for field, value in haystack_fields.items():
        lowered = value.lower()
        for keyword in keywords:
            if _keyword_matches(target, lowered, keyword):
                strength = "medium" if field == "tags" else "weak"
                ev.append(evidence(source, field, keyword, strength, url))
                return True, target, 0.72 if strength == "medium" else 0.55, ev, field

    return False, target, 0.0, ev, ""


def task_defaults(task: str) -> dict[str, Any]:
    normalized = canonical_task(task)
    if normalized == "tts":
        return {
            "io_contract": {
                "input_type": "text",
                "output_type": "json",
                "primary_field": "audio_path",
                "required_fields": ["audio_path"],
                "nonempty_fields": ["audio_path"],
                "json_serializable": True,
            },
            "infer_test": "model.synthesize('Hello, this is a short SURE smoke test.')",
        }
    if normalized == "vc":
        return {
            "io_contract": {
                "input_type": "audio_pair",
                "output_type": "json",
                "primary_field": "audio_path",
                "required_fields": ["audio_path"],
                "nonempty_fields": ["audio_path"],
                "json_serializable": True,
            },
            "infer_test": (
                "model.convert("
                "'fixtures/tasks/vc/seed_vc_zh_smoke/zh/ZH_B00001_S00000_W000000.mp3', "
                "'fixtures/tasks/vc/seed_vc_zh_smoke/zh/ZH_B00000_S00000_W000002.mp3')"
            ),
        }
    if normalized == "vad":
        return {
            "io_contract": {
                "input_type": "audio_path",
                "output_type": "json",
                "primary_field": "speech_segments",
                "required_fields": ["speech_segments"],
                "nonempty_fields": ["speech_segments"],
                "json_serializable": True,
            },
            "infer_test": "model.detect_speech('fixtures/tasks/vad/librispeech_vad_smoke/librispeech_vad_001.wav')",
        }
    return {
        "io_contract": {
            "input_type": "audio_path",
            "output_type": "json",
            "primary_field": "text",
            "required_fields": ["text"],
            "nonempty_fields": ["text"],
            "json_serializable": True,
        },
        "infer_test": "model.transcribe('fixtures/tasks/asr/qwen3_asr_smoke/asr_en/sample_1_367-130732-0006.wav')",
    }


def extract_code_blocks(markdown: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    for match in re.finditer(r"```([A-Za-z0-9_-]*)\s*\n(.*?)```", markdown or "", re.S):
        blocks.append({"lang": match.group(1).lower(), "code": match.group(2).strip()})
    return blocks


def _balanced_statement(lines: list[str], start: int) -> str:
    statement: list[str] = []
    balance = 0
    for line in lines[start:]:
        if not line.strip() and statement:
            break
        statement.append(line.rstrip())
        balance += line.count("(") + line.count("[") + line.count("{")
        balance -= line.count(")") + line.count("]") + line.count("}")
        if statement and balance <= 0 and not line.rstrip().endswith("\\"):
            break
    return "\n".join(statement).strip()


def _first_python_import(blocks: list[dict[str, str]], library_name: str | None) -> str | None:
    imports: list[str] = []
    preferred_terms = [term for term in {library_name, (library_name or "").replace("-", "_")} if term]
    for block in blocks:
        if block["lang"] not in {"python", "py", ""}:
            continue
        for line in block["code"].splitlines():
            stripped = line.strip()
            if re.match(r"^(from\s+[A-Za-z0-9_.]+\s+import\s+.+|import\s+[A-Za-z0-9_.]+)", stripped):
                imports.append(stripped)
                if any(term and term in stripped for term in preferred_terms):
                    return stripped
    for line in imports:
        if not line.startswith(COMMON_IMPORT_PREFIXES):
            return line
    return imports[0] if imports else None


def _first_python_statement(blocks: list[dict[str, str]], patterns: tuple[str, ...]) -> str | None:
    for block in blocks:
        if block["lang"] not in {"python", "py", ""}:
            continue
        lines = block["code"].splitlines()
        for idx, line in enumerate(lines):
            if re.match(r"^\s*(from\s+[A-Za-z0-9_.]+\s+import\s+.+|import\s+[A-Za-z0-9_.]+)", line):
                continue
            if any(pattern in line for pattern in patterns):
                return _balanced_statement(lines, idx)
    return None


def _first_shell_command(blocks: list[dict[str, str]], patterns: tuple[str, ...]) -> str | None:
    for block in blocks:
        if block["lang"] not in {"bash", "shell", "sh", "console", ""}:
            continue
        lines = block["code"].splitlines()
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if _is_setup_shell_command(stripped):
                continue
            if any(pattern in stripped for pattern in patterns):
                return _balanced_statement(lines, idx)
    return None


def _is_setup_shell_command(command: str) -> bool:
    stripped = re.sub(r"^(?:\$|!|%)\s*", "", command.strip().lower())
    setup_patterns = (
        r"^(?:python[0-9.]*\s+-m\s+venv|uv\s+venv|virtualenv\b)",
        r"^(?:pip[0-9.]*\s+install|python[0-9.]*\s+-m\s+pip\s+install)",
        r"^(?:conda|mamba|micromamba)\s+(?:create|install|env\s+create)\b",
        r"^(?:apt|apt-get|yum|dnf|apk|brew)\s+install\b",
        r"^git\s+clone\b",
        r"^cd\s+",
        r"^(?:export|source)\s+",
    )
    return any(re.search(pattern, stripped) for pattern in setup_patterns)


def _regex_value(text: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.S)
        if match:
            return match.group(1).strip()
    return None


def _field_evidence(source: str, field: str, value: Any, strength: str, url: str | None, evidence_field: str) -> dict[str, Any]:
    item = evidence(source, evidence_field, value, strength, url)
    item["model_input_field"] = field
    return item


def _runtime_evidence_text(candidate: dict[str, Any], card_text: str) -> str:
    values = [
        str(candidate.get("model_id") or ""),
        str(candidate.get("repo") or ""),
        str(candidate.get("description") or ""),
        str(card_text or ""),
        " ".join(str(item) for item in candidate.get("tags") or []),
        " ".join(str(item) for item in candidate.get("tasks") or []),
    ]
    return "\n".join(values).lower()


def _derive_runtime_strategy(
    candidate: dict[str, Any],
    card_text: str,
    source: str,
    evidence_url: str | None,
    field_evidence: list[dict[str, Any]],
) -> dict[str, Any] | None:
    combined = _runtime_evidence_text(candidate, card_text)
    strategy: dict[str, Any] | None = None

    if "sherpa-onnx" in combined or "sherpa_onnx" in combined:
        strategy = {
            "type": "cli_or_library",
            "framework": "sherpa-onnx",
            "load_surface": "load ONNX checkpoint files from the model repository with sherpa-onnx",
            "inference_surface": "run sherpa-onnx ASR inference through its Python API or CLI on the selected SURE fixture",
            "evidence_terms": [term for term in ("sherpa-onnx", "onnx") if term in combined],
        }
    elif "onnxruntime" in combined or re.search(r"\bonnx\b", combined):
        strategy = {
            "type": "cli_or_library",
            "framework": "onnxruntime",
            "load_surface": "load ONNX checkpoint files from the model repository with an ONNX runtime",
            "inference_surface": "run ONNX runtime inference through a model-specific wrapper on the selected SURE fixture",
            "evidence_terms": [term for term in ("onnxruntime", "onnx") if term in combined],
        }

    if strategy:
        field_evidence.append(
            _field_evidence(
                source,
                "runtime_strategy",
                strategy,
                "medium",
                evidence_url,
                "provider.runtime_tags",
            )
        )
    return strategy


def _runtime_strategy_entrypoint(field: str, runtime_strategy: dict[str, Any], task_type: str) -> str:
    framework = str(runtime_strategy.get("framework") or "model runtime")
    if field == "import_test":
        return f"policy_resolved: use the {framework} runtime import or CLI surface selected during /sure_onboard"
    if field == "load_test":
        return f"policy_resolved: {runtime_strategy.get('load_surface') or f'load model weights with {framework}'}"
    if field == "infer_test":
        return (
            "policy_resolved: "
            f"{runtime_strategy.get('inference_surface') or f'run {task_type} inference with {framework} on the selected SURE fixture'}"
        )
    return f"policy_resolved: use {framework} runtime strategy"


def _derive_entrypoints(
    candidate: dict[str, Any],
    blocks: list[dict[str, str]],
    task_type: str,
    runtime_strategy: dict[str, Any] | None,
    source: str,
    evidence_url: str | None,
    missing_or_weak: list[str],
    field_evidence: list[dict[str, Any]],
) -> dict[str, str]:
    explicit_hints = {
        field: candidate.get(field)
        for field in ("import_test", "load_test", "infer_test")
        if isinstance(candidate.get(field), str) and str(candidate.get(field)).strip()
    }
    library_name = candidate.get("library_name")
    import_test = explicit_hints.get("import_test") or _first_python_import(blocks, str(library_name) if library_name else None)
    load_test = explicit_hints.get("load_test") or _first_python_statement(
        blocks,
        (".from_pretrained(", "load_model(", "load_pretrained(", "pipeline("),
    )
    infer_test = explicit_hints.get("infer_test") or _first_python_statement(
        blocks,
        (
            ".generate(",
            "generate_transcription(",
            ".synthesize(",
            ".transcribe(",
            "transcribe(",
            ".convert(",
            ".infer(",
            ".detect_speech(",
            "get_speech_timestamps(",
            "pipeline(",
        ),
    )
    if infer_test and load_test and infer_test == load_test:
        infer_test = None
    cli_test = _first_shell_command(
        blocks,
        ("python ", "python\\", "curl ", "vllm ", "sgl-omni ", "--model", "--model-name-or-path", " transcribe"),
    )
    entrypoints: dict[str, str] = {}
    for field, value, evidence_field in (
        ("import_test", import_test, "model_card.python_import"),
        ("load_test", load_test, "model_card.python_load"),
        ("infer_test", infer_test or cli_test, "model_card.python_or_cli_infer"),
    ):
        if value:
            entrypoints[field] = value
            field_evidence.append(
                _field_evidence(
                    "local" if field in explicit_hints else source,
                    f"entrypoints.{field}",
                    value,
                    "strong",
                    None if field in explicit_hints else evidence_url,
                    "reference_model_spec.entrypoints" if field in explicit_hints else evidence_field,
                )
            )
        elif runtime_strategy:
            value = _runtime_strategy_entrypoint(field, runtime_strategy, task_type)
            entrypoints[field] = value
            field_evidence.append(
                _field_evidence(
                    "local",
                    f"entrypoints.{field}",
                    value,
                    "medium",
                    None,
                    "sure_policy.runtime_strategy",
                )
            )
        else:
            entrypoints[field] = f"UNRESOLVED: no {field} found in retrieved model documentation"
            missing_or_weak.append(f"missing:entrypoints.{field}")
    if runtime_strategy:
        entrypoints["runtime_notes"] = [
            "Provider evidence did not expose a clean Python import/load/infer split; /sure_onboard should follow runtime_strategy.",
        ]
    return entrypoints


def _derive_environment(
    card_text: str,
    blocks: list[dict[str, str]],
    source: str,
    evidence_url: str | None,
    missing_or_weak: list[str],
    field_evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    text = card_text or ""
    install_text = "\n".join(block["code"] for block in blocks if block["lang"] in {"bash", "shell", "sh", "console", ""})
    python_version = _regex_value(install_text, (r"python\s*=\s*([0-9]+(?:\.[0-9]+)*)", r"python(?:\s+version)?\s*[:>= ]+\s*([0-9]+(?:\.[0-9]+)*)"))
    preferred_backend: str | None = None
    if re.search(r"\bconda\s+create\b|\bconda\s+activate\b", install_text):
        preferred_backend = "conda"
    elif re.search(r"\bpip\s+install\b|python\s+-m\s+pip", install_text):
        preferred_backend = "pip"
    elif "Dockerfile" in text or re.search(r"\bdocker\s+(build|run)\b", install_text):
        preferred_backend = "docker"
    system_packages: list[str] = []
    lowered = text.lower()
    if "ffmpeg" in lowered:
        system_packages.append("ffmpeg")
    if "soundfile" in lowered or "libsndfile" in lowered or "sf.write" in text:
        system_packages.append("libsndfile1")
    requires_gpu: bool | None = None
    if re.search(r"\b(cuda|gpu|bfloat16|float16|accelerate|nvidia)\b", lowered):
        requires_gpu = True
    elif re.search(r"\bcpu\b", lowered):
        requires_gpu = False

    if preferred_backend:
        field_evidence.append(_field_evidence(source, "environment_hint.preferred_backend", preferred_backend, "strong", evidence_url, "model_card.install"))
    else:
        preferred_backend = "uv"
        missing_or_weak.append("missing:environment_hint.preferred_backend")
    python_version_source = "upstream_documented" if python_version else "sure_policy_default"
    if python_version:
        field_evidence.append(_field_evidence(source, "environment_hint.python_version", python_version, "strong", evidence_url, "model_card.install"))
    else:
        python_version = POLICY_DEFAULT_PYTHON_VERSION
        field_evidence.append(
            _field_evidence(
                "local",
                "environment_hint.python_version",
                python_version,
                "medium",
                None,
                "sure_policy.python_version_default",
            )
        )
    if requires_gpu is None:
        requires_gpu = True
        missing_or_weak.append("missing:environment_hint.requires_gpu")
    else:
        field_evidence.append(_field_evidence(source, "environment_hint.requires_gpu", requires_gpu, "medium", evidence_url, "model_card.runtime_hints"))
    field_evidence.append(_field_evidence(source, "environment_hint.system_packages", system_packages, "medium", evidence_url, "model_card.runtime_imports"))
    return {
        "preferred_backend": preferred_backend,
        "python_version": str(python_version),
        "python_version_source": python_version_source,
        "requires_gpu": bool(requires_gpu),
        "system_packages": system_packages,
    }


def _derive_fixture_and_contract(
    task_type: str,
    candidate: dict[str, Any],
    card_text: str,
    entrypoints: dict[str, str],
    source: str,
    evidence_url: str | None,
    missing_or_weak: list[str],
    field_evidence: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    text_source = "\n".join([card_text or "", entrypoints.get("infer_test", ""), entrypoints.get("load_test", "")])
    text_value = _regex_value(text_source, (r'text\s*=\s*"([^"]+)"', r"text\s*=\s*'([^']+)'", r"--text\s+\"([^\"]+)\""))
    audio_value = _regex_value(
        text_source,
        (
            r'audio\s*=\s*"([^"]+)"',
            r"audio\s*=\s*'([^']+)'",
            r'audio_path\s*=\s*"([^"]+)"',
            r"audio_path\s*=\s*'([^']+)'",
            r'file=@?"?([^"\\\s]+\.wav)"?',
        ),
    )
    prompt_text = _regex_value(text_source, (r'prompt_text\s*=\s*"([^"]+)"', r"prompt_text\s*=\s*'([^']+)'", r"--prompt-text\s+\"([^\"]+)\""))
    has_reference_audio = bool(re.search(r"prompt[_-]audio|reference[_-]audio|prompt_audio_path|/path/to/reference", text_source))
    outputs_audio = bool(re.search(r"sf\.write|soundfile\.write|output\.wav|clone\.wav|result\[['\"]audio['\"]\]", text_source))
    outputs_text = bool(re.search(r"transcrib|result\[['\"]text['\"]|\"text\"", text_source.lower()))

    provider_fixture_hint: dict[str, Any] = {}
    if audio_value:
        provider_fixture_hint["audio"] = audio_value
    if text_value and canonical_task(task_type) == "tts":
        provider_fixture_hint["text"] = text_value
    if prompt_text:
        provider_fixture_hint["prompt_text"] = prompt_text
    if has_reference_audio:
        provider_fixture_hint["requires_reference_audio"] = True

    fixture, registry_contract, fixture_issues, registry_evidence = select_fixture_for_task(task_type, candidate)
    field_evidence.extend(registry_evidence)
    if fixture:
        if provider_fixture_hint:
            fixture["provider_fixture_hint"] = provider_fixture_hint
            field_evidence.append(
                _field_evidence(
                    source,
                    "fixture.provider_hint",
                    provider_fixture_hint,
                    "medium",
                    evidence_url,
                    "model_card.inference_inputs",
                )
            )
    else:
        missing_or_weak.extend(fixture_issues or ["missing:fixture"])
        fixture = {
            "fixture_source": "unresolved",
            "task_specific": True,
            "fallback_allowed": False,
        }
        if provider_fixture_hint:
            fixture["provider_fixture_hint"] = provider_fixture_hint

    normalized_task = canonical_task(task_type)
    if normalized_task in {"sd", "sa_asr", "vad"}:
        io_contract = registry_contract
    elif outputs_audio or normalized_task in {"tts", "vc"}:
        input_type = "text_with_reference_audio" if fixture.get("text") and fixture.get("reference_audio") else "text"
        if normalized_task == "vc":
            input_type = "audio_pair"
        io_contract = {
            "input_type": input_type,
            "output_type": "json",
            "primary_field": "audio_path",
            "required_fields": ["audio_path"],
            "nonempty_fields": ["audio_path"],
            "json_serializable": True,
        }
    elif outputs_text or normalized_task == "asr":
        io_contract = {
            "input_type": "audio_path",
            "output_type": "json",
            "primary_field": "text",
            "required_fields": ["text"],
            "nonempty_fields": ["text"],
            "json_serializable": True,
        }
    else:
        io_contract = registry_contract
        if not registry_contract:
            io_contract = task_defaults(task_type)["io_contract"]
            missing_or_weak.append("missing:io_contract")
    field_evidence.append(
        _field_evidence(
            source,
            "io_contract.provider_output_hint",
            io_contract,
            "strong" if "missing:io_contract" not in missing_or_weak else "weak",
            evidence_url,
            "model_card.inference_outputs",
        )
    )
    return fixture, io_contract


def _derive_phase1_targets(entrypoints: dict[str, str], missing_or_weak: list[str], field_evidence: list[dict[str, Any]], source: str, evidence_url: str | None) -> list[str]:
    targets: list[str] = []
    if not entrypoints.get("import_test", "").startswith("UNRESOLVED"):
        targets.append("import package exactly as shown in retrieved model documentation")
    if not entrypoints.get("load_test", "").startswith("UNRESOLVED"):
        targets.append("load model/checkpoint using the documented loading API")
    if not entrypoints.get("infer_test", "").startswith("UNRESOLVED"):
        targets.append("run documented inference on a SURE smoke fixture")
    if len(targets) < 3:
        missing_or_weak.append("missing:phase1_runtime_target")
    else:
        uses_policy = any(str(value).startswith("policy_resolved:") for value in entrypoints.values() if isinstance(value, str))
        field_evidence.append(
            _field_evidence(
                "local" if uses_policy else source,
                "phase1_runtime_target",
                targets,
                "medium" if uses_policy else "strong",
                None if uses_policy else evidence_url,
                "sure_policy.runtime_strategy" if uses_policy else "model_card.quick_start",
            )
        )
    return targets or ["UNRESOLVED: retrieved documentation did not contain enough runtime steps"]


def synthesize_model_input(candidate: dict[str, Any], task: str) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    task_type = canonical_task(task)
    model_id = str(candidate["model_id"])
    source = str(candidate.get("source") or "manual")
    missing_or_weak: list[str] = []
    field_evidence: list[dict[str, Any]] = []
    card_text = str(candidate.get("model_card_text") or candidate.get("readme") or candidate.get("description") or "")
    evidence_url = str(candidate.get("model_card_url") or candidate.get("source_url") or candidate.get("repo") or "")
    blocks = extract_code_blocks(card_text)
    weights_source = candidate.get("weights_source")
    if not weights_source:
        if source in {"huggingface", "modelscope"}:
            weights_source = source
        elif source == "github":
            weights_source = candidate.get("github_weights_source") or "release_or_pypi"
            if not candidate.get("github_weights_source"):
                missing_or_weak.append("missing:weights.source")
        else:
            weights_source = "local"
    field_evidence.append(_field_evidence(source, "repo.url", candidate.get("repo") or candidate.get("source_url") or "", "strong", evidence_url, "provider.repo"))
    field_evidence.append(_field_evidence(source, "repo.commit", candidate.get("commit"), "medium", evidence_url, "provider.commit"))
    field_evidence.append(_field_evidence(source, "weights.source", weights_source, "strong" if weights_source else "weak", evidence_url, "provider.weights_source"))

    runtime_strategy = _derive_runtime_strategy(candidate, card_text, source, evidence_url, field_evidence)
    entrypoints = _derive_entrypoints(candidate, blocks, task_type, runtime_strategy, source, evidence_url, missing_or_weak, field_evidence)
    environment_hint = _derive_environment(card_text, blocks, source, evidence_url, missing_or_weak, field_evidence)
    fixture, io_contract = _derive_fixture_and_contract(task_type, candidate, card_text, entrypoints, source, evidence_url, missing_or_weak, field_evidence)
    phase1_runtime_target = _derive_phase1_targets(entrypoints, missing_or_weak, field_evidence, source, evidence_url)

    model_input = {
        "model_id": model_id,
        "model_name": slugify(model_id),
        "task_type": task_type,
        "deployment_type": "local",
        "repo": {
            "url": str(candidate.get("repo") or candidate.get("source_url") or ""),
            "commit": candidate.get("commit"),
        },
        "weights": {
            "source": weights_source,
            "local_path": candidate.get("local_path"),
            "required": True,
            "cache_policy": "model_local_first",
            "local_dir_name": "checkpoints",
        },
        "environment_hint": environment_hint,
        "phase1_runtime_target": phase1_runtime_target,
        "entrypoints": entrypoints,
        "fixture": fixture,
        "io_contract": io_contract,
    }
    if runtime_strategy:
        model_input["runtime_strategy"] = runtime_strategy
    if not model_input["repo"]["url"]:
        missing_or_weak.append("missing:repo.url")
    return model_input, missing_or_weak, field_evidence


def yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    ambiguous_plain_scalar = bool(
        re.fullmatch(r"[-+]?(?:\d+|\d+\.\d+)(?:[eE][-+]?\d+)?", text)
        or text.lower() in {"true", "false", "null", "none", "yes", "no", "on", "off"}
    )
    if (
        text == ""
        or "\n" in text
        or "\r" in text
        or ambiguous_plain_scalar
        or any(ch in text for ch in ":#[]{}&*!|>'\"%@`")
        or text.strip() != text
    ):
        return json.dumps(text, ensure_ascii=False)
    return text


def to_yaml(value: Any, indent: int = 0) -> str:
    pad = " " * indent
    if isinstance(value, dict):
        if not value:
            return f"{pad}{{}}"
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, dict) and not item:
                lines.append(f"{pad}{key}: {{}}")
            elif isinstance(item, list) and not item:
                lines.append(f"{pad}{key}: []")
            elif isinstance(item, (dict, list)):
                lines.append(f"{pad}{key}:")
                lines.append(to_yaml(item, indent + 2))
            else:
                lines.append(f"{pad}{key}: {yaml_scalar(item)}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return f"{pad}[]"
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}-")
                lines.append(to_yaml(item, indent + 2))
            else:
                lines.append(f"{pad}- {yaml_scalar(item)}")
        return "\n".join(lines)
    return f"{pad}{yaml_scalar(value)}"
