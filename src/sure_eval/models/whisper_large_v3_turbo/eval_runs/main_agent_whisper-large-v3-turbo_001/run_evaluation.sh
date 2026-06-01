#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# SURE-EVAL Execution Surface
# Materialized from: templates/run_single_model.sh
# Template SHA-256: 6078888e0227e5105a86a90d91a6241451c881444391e7bed39409cd74da5a4e
# Run ID: main_agent_whisper-large-v3-turbo_001
# Model: whisper_large_v3_turbo
# Dataset: aishell1_test
# =============================================================================

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../../../" && pwd)"

export REPO_ROOT
export MODEL_NAME="whisper_large_v3_turbo"
export MODEL_DIR="$REPO_ROOT/src/sure_eval/models/$MODEL_NAME"
export RUN_ID="main_agent_whisper-large-v3-turbo_001"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export RUN_DIR="$SCRIPT_DIR"
export TOOL_NAME="transcribe_audio"
export PYTHON_BIN="$MODEL_DIR/.venv/bin/python"
export MAX_SAMPLES="${MAX_SAMPLES:-0}"
export SKIP_VALIDATE_AND_EVAL="${SKIP_VALIDATE_AND_EVAL:-0}"
export NO_RESUME="${NO_RESUME:-0}"
export DEVICE="${DEVICE:-cuda:1}"
export HF_HUB_CACHE="${HF_HUB_CACHE:-$MODEL_DIR/.runtime/hf_cache}"
export RESULTS_DIR="${RESULTS_DIR:-$REPO_ROOT/results/whisper-large-v3-turbo/strict_core}"
export PROTOCOL_ID="${PROTOCOL_ID:-strict_core}"
export DATASETS="aishell1_test"

# Ensure CWD is repo root so relative config paths resolve
cd "$REPO_ROOT"

# Hand off to the canonical template
exec bash "$REPO_ROOT/templates/run_single_model.sh"
