"""Shared schema constants for the Paper_to_UserSpec MVP."""

from __future__ import annotations

SPEC_VERSION = "0.1"

USER_GOAL_INTENTS = {
    "onboard",
    "evaluate",
    "reproduce",
    "compare",
    "controlled_training",
    "unknown",
}

DEPLOYMENT_TYPES = {"local", "api", "unknown"}

TASK_TYPES = {
    "ASR",
    "S2TT",
    "SD",
    "SA-ASR",
    "SER",
    "Speech Enhancement",
    "Music IR",
    "VAD",
    "SV",
    "TTS",
    "VLM",
    "GR",
    "SLU",
    "utility",
    "unknown",
}

BACKEND_HINTS = {"uv", "pixi", "conda", "docker", "api", "unknown"}

ROUTES = {
    "tool_onboarding",
    "main_flow_evaluation",
    "controlled_training_conversion",
    "needs_human_input",
}

USER_SPEC_TOP_LEVEL_FIELDS = [
    "spec_version",
    "case_id",
    "source",
    "user_goal",
    "model",
    "task",
    "data",
    "runtime",
    "evaluation",
    "sure_routing",
    "confidence",
    "missing_fields",
    "conflict_fields",
    "evidence_spans",
]

REQUIRED_SOURCE_FIELDS = [
    "paper_title",
    "paper_path",
    "paper_text_path",
    "repo_url",
    "model_card_url",
    "extracted_from",
    "extraction_timestamp",
]

REQUIRED_MODEL_INPUT_FIELDS = [
    "model_id",
    "model_name",
    "task_type",
    "deployment_type",
    "repo.url",
    "repo.commit",
    "weights.source",
    "weights.local_path",
    "weights.required",
    "weights.cache_policy",
    "weights.local_dir_name",
    "environment_hint.preferred_backend",
    "environment_hint.python_version",
    "environment_hint.requires_gpu",
    "environment_hint.system_packages",
    "phase1_runtime_target",
    "entrypoints.import_test",
    "entrypoints.load_test",
    "entrypoints.infer_test",
    "fixture.audio",
    "fixture.task_specific",
    "fixture.fallback_allowed",
    "io_contract.input_type",
    "io_contract.output_type",
    "io_contract.primary_field",
    "io_contract.required_fields",
    "io_contract.nonempty_fields",
    "io_contract.json_serializable",
]

TASK_IO_COMPATIBILITY = {
    "ASR": {
        "input_type": {"audio_path"},
        "output_type": {"json"},
        "primary_field": {"text", "transcript"},
        "required_any": {"text", "transcript"},
        "nonempty_any": {"text", "transcript"},
    },
    "S2TT": {
        "input_type": {"audio_path"},
        "output_type": {"json"},
        "primary_field": {"translation", "text"},
    },
    "SD": {
        "input_type": {"audio_path"},
        "output_type": {"json"},
        "primary_field": {"segments"},
        "required_any": {"segments"},
    },
    "SA-ASR": {
        "input_type": {"audio_path"},
        "output_type": {"json"},
        "primary_field": {"segments", "transcript"},
    },
    "SER": {
        "input_type": {"audio_path"},
        "output_type": {"json"},
        "primary_field": {"label", "emotion"},
    },
    "VAD": {
        "input_type": {"audio_path"},
        "output_type": {"json"},
        "primary_field": {"segments"},
    },
    "SV": {
        "input_type": {"audio_path"},
        "output_type": {"json"},
        "primary_field": {"score", "decision"},
    },
    "TTS": {
        "input_type": {"json", "text"},
        "output_type": {"audio_path", "json"},
        "primary_field": {"audio_path", "generated_audio_path"},
        "required_any": {"audio_path", "generated_audio_path"},
        "nonempty_any": {"audio_path", "generated_audio_path"},
    },
    "Speech Enhancement": {
        "input_type": {"audio_path"},
        "output_type": {"json"},
        "primary_field": {"enhanced_audio_path"},
    },
    "Music IR": {
        "input_type": {"audio_path"},
        "output_type": {"json"},
        "primary_field": {"features", "labels"},
    },
    "GR": {
        "input_type": {"audio_path"},
        "output_type": {"json"},
        "primary_field": {"label"},
    },
    "SLU": {
        "input_type": {"audio_path"},
        "output_type": {"json"},
        "primary_field": {"intent", "slots", "answer", "label"},
    },
    "VLM": {
        "input_type": {"json"},
        "output_type": {"json"},
        "primary_field": {"answer"},
    },
    "utility": {
        "input_type": {"json", "audio_path"},
        "output_type": {"json"},
        "primary_field": set(),
    },
}

METRIC_COMPATIBILITY = {
    "ASR": {"WER", "CER"},
    "S2TT": {"BLEU", "chrF", "chrF2"},
    "SD": {"DER", "JER"},
    "SA-ASR": {"cpWER", "cpCER", "DER"},
    "SER": {"accuracy", "Accuracy", "F1", "UAR"},
    "GR": {"accuracy", "F1"},
    "SLU": {"accuracy", "Intent Accuracy", "Slot F1", "F1"},
    "VAD": {"F1", "precision", "recall"},
    "SV": {"EER", "accuracy", "AUC"},
    "TTS": {"WER", "SIM-o", "SIM", "RTF", "CMOS", "SMOS", "UTMOS"},
    "Speech Enhancement": {"PESQ", "STOI", "SI-SDR", "DNSMOS"},
    "Music IR": {"accuracy", "F1", "mAP"},
}

HIGH_CONFIDENCE_FIELDS = {
    "source.paper_title",
    "model.name",
    "task.primary_task",
    "runtime.backend_hint",
    "evaluation.metrics",
}
