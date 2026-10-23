"""Shared MCP response decoding and task-specific prediction projection."""

from __future__ import annotations

import json
import math
from typing import Any

from sure.runtime.evaluation.task_registry import normalize_task, task_profile


def extract_mcp_payload(response: dict[str, Any]) -> Any:
    if "error" in response:
        error = response["error"]
        raise RuntimeError(error.get("message", "Unknown server error") if isinstance(error, dict) else str(error))

    result = response.get("result", {})
    if isinstance(result, dict) and result.get("isError"):
        content = result.get("content") or []
        message = ""
        if content and isinstance(content[0], dict):
            message = str(content[0].get("text") or "")
        raise RuntimeError(message or "Tool call returned isError=true")
    content = result.get("content", []) if isinstance(result, dict) else []
    if not content:
        return result

    text = content[0].get("text", "") if isinstance(content[0], dict) else ""
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return text


def single_line_text(value: Any) -> str:
    return str(value).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def path_text(value: Any, *, field: str) -> str:
    text = str(value)
    if "\r" in text or "\n" in text:
        raise ValueError(f"{field} cannot contain newline characters")
    return text


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return ""


def normalize_prediction_payload(payload: Any, *, task: str) -> tuple[str, dict[str, Any]]:
    task_name = normalize_task(task).upper()
    if isinstance(payload, dict):
        prediction = dict(payload.get("prediction") or {})
        if not prediction:
            prediction = dict(payload)
        if task_name in {"ASR", "S2TT"}:
            field = str(task_profile(task)["io_contract"]["primary_field"])
            candidates = (
                (prediction, field),
                (prediction, "text"),
                (prediction, "transcript"),
                (payload, field),
                (payload, "text"),
                (payload, "transcript"),
            )
            value = next((source[key] for source, key in candidates if key in source), None)
            if value is None and not any(key in source for source, key in candidates):
                raise ValueError(f"{task_name} prediction must provide {field}")
            if isinstance(value, (list, tuple)) and len(value) == 1:
                value = value[0]
            if value is None:
                value = ""
            normalized_value = single_line_text(value)
            return normalized_value, {field: normalized_value, "text": normalized_value}
        if task_name in {"TTS", "VC", "SE", "TSE"}:
            value = (
                prediction.get("audio_path")
                or prediction.get("path")
                or prediction.get("generated_audio")
                or prediction.get("converted_audio")
                or payload.get("audio_path")
                or payload.get("path")
                or ""
            )
            normalized_value = path_text(value, field="audio_path")
            normalized = {"audio_path": normalized_value}
            if task_name == "VC":
                normalized["converted_audio"] = normalized_value
                for key in ("source_audio_path", "reference_audio_path"):
                    if prediction.get(key) is not None:
                        normalized[key] = path_text(prediction[key], field=key)
            elif task_name == "SE":
                normalized["enhanced_audio"] = normalized_value
            elif task_name == "TSE":
                normalized["prediction_audio"] = normalized_value
            for key in ("sample_rate", "duration_ms"):
                if prediction.get(key) is not None:
                    normalized[key] = prediction[key]
            return normalized_value, normalized
        if task_name in {"CLASSIFICATION", "LID", "SER", "GR"}:
            value = first_present(
                prediction.get("label"), prediction.get("language"), prediction.get("lang"),
                payload.get("label"), payload.get("language"), payload.get("lang"),
                prediction.get("text"), payload.get("text"),
            )
            normalized_value = single_line_text(value)
            normalized = {"label": normalized_value}
            if task_name == "LID":
                normalized["language"] = normalized_value
            return normalized_value, normalized
        if task_name == "SLU":
            field = str(task_profile(task)["io_contract"]["primary_field"])
            value = first_present(prediction.get(field), prediction.get("text"), prediction.get("label"), payload.get(field), payload.get("text"), payload.get("label"))
            normalized_value = single_line_text(value)
            normalized = {field: normalized_value, "text": normalized_value}
            if prediction.get("label") is not None:
                normalized["label"] = single_line_text(prediction["label"])
            return normalized_value, normalized
        if task_name in {"SD", "SA_ASR"}:
            if prediction.get("segments") is not None:
                return json.dumps(prediction["segments"], ensure_ascii=False), {"segments": prediction["segments"]}
            value = prediction.get("annotation_path") or prediction.get("annotation") or payload.get("text") or ""
            normalized_value = path_text(value, field="annotation_path")
            return normalized_value, {"annotation": normalized_value}
        if task_name == "KWS":
            if "detected" not in prediction and "detected" not in payload:
                raise ValueError("KWS prediction is missing detected")
            detected_value = prediction.get("detected", payload.get("detected"))
            if isinstance(detected_value, bool):
                detected = detected_value
            elif isinstance(detected_value, int) and detected_value in {0, 1}:
                detected = bool(detected_value)
            elif isinstance(detected_value, str) and detected_value.strip().lower() in {"true", "1", "yes", "detected"}:
                detected = True
            elif isinstance(detected_value, str) and detected_value.strip().lower() in {"false", "0", "no", "rejected"}:
                detected = False
            else:
                raise ValueError("KWS detected must be a boolean or an unambiguous boolean token")
            score_value = prediction.get("score") if "score" in prediction else payload.get("score")
            if score_value is None or score_value == "":
                raise ValueError("KWS score is required and must be a finite number in [0, 1]")
            if isinstance(score_value, bool):
                raise ValueError("KWS score must be numeric")
            score = float(score_value)
            if not math.isfinite(score) or not 0.0 <= score <= 1.0:
                raise ValueError("KWS score must be a finite number in [0, 1]")
            keyword_value = prediction.get("keyword", payload.get("keyword"))
            if keyword_value in (None, ""):
                keyword = None
            elif isinstance(keyword_value, str):
                keyword = keyword_value.strip() or None
            else:
                raise ValueError("KWS keyword must be a string or null")
            if detected and keyword is None:
                raise ValueError("KWS detected=true requires a non-empty keyword")
            normalized = {"detected": detected, "keyword": keyword, "score": score}
            if prediction.get("events") is not None:
                if not isinstance(prediction["events"], list):
                    raise ValueError("KWS events must be a list when present")
                normalized["events"] = prediction["events"]
            projection = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            return projection, normalized
        if task_name == "VAD":
            normalized = {}
            for field in ("speech_segments", "frame_scores"):
                if prediction.get(field) is not None:
                    normalized[field] = prediction[field]
            timestamps = prediction.get("timestamps")
            if "speech_segments" not in normalized and isinstance(timestamps, list):
                normalized["speech_segments"] = [
                    {"start": float(segment[0]), "end": float(segment[1])}
                    for segment in timestamps
                    if isinstance(segment, (list, tuple)) and len(segment) >= 2
                ]
            return json.dumps(normalized, ensure_ascii=False), normalized
        if task_name == "SV":
            embedding = prediction.get("embedding") or payload.get("embedding") or []
            if (
                not isinstance(embedding, list)
                or not embedding
                or any(
                    isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                    for value in embedding
                )
            ):
                raise ValueError("SV embedding must be a non-empty array of finite numbers")
            return json.dumps(embedding), {"embedding": embedding}
        if task_name == "DOA":
            field = str(task_profile(task)["io_contract"]["primary_field"])
            if not isinstance(prediction.get(field), list):
                raise ValueError(f"DOA prediction must provide a {field} array")
            return json.dumps(prediction, ensure_ascii=False), prediction
        normalized_value = single_line_text(payload.get("text", ""))
        return normalized_value, {"text": normalized_value}

    if task_name in {"SV", "DOA"}:
        field = str(task_profile(task)["io_contract"]["primary_field"])
        raise ValueError(f"{task_name} prediction must be a JSON object with {field}")
    if task_name in {"TTS", "VC", "SE", "TSE"}:
        value = path_text(payload, field="audio_path")
    elif task_name in {"SD", "SA_ASR"}:
        value = path_text(payload, field="annotation_path")
    else:
        value = single_line_text(payload)
    if task_name in {"TTS", "VC", "SE", "TSE"}:
        normalized = {"audio_path": value}
        if task_name == "VC":
            normalized["converted_audio"] = value
        elif task_name == "SE":
            normalized["enhanced_audio"] = value
        elif task_name == "TSE":
            normalized["prediction_audio"] = value
        return value, normalized
    if task_name in {"CLASSIFICATION", "LID", "SER", "GR"}:
        normalized = {"label": value}
        if task_name == "LID":
            normalized["language"] = value
        return value, normalized
    if task_name in {"SD", "SA_ASR"}:
        return value, {"annotation": value}
    if task_name in {"S2TT", "SLU"}:
        field = str(task_profile(task)["io_contract"]["primary_field"])
        return value, {field: value, "text": value}
    if task_name == "KWS":
        raise ValueError("KWS prediction must be a JSON object with detected, keyword, and score")
    return value, {"text": value}
