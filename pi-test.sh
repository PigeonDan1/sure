#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTH_BACKUP=""
AUTH_FILE_TO_RESTORE=""

cleanup() {
  if [[ -n "$AUTH_BACKUP" && -n "$AUTH_FILE_TO_RESTORE" && -f "$AUTH_BACKUP" ]]; then
    mv -f "$AUTH_BACKUP" "$AUTH_FILE_TO_RESTORE"
    echo "Restored auth.json"
  fi
}
trap cleanup EXIT

# Check for --no-env flag
NO_ENV=false
ARGS=()
for arg in "$@"; do
  if [[ "$arg" == "--no-env" ]]; then
    NO_ENV=true
  else
    ARGS+=("$arg")
  fi
done

if [[ "$NO_ENV" == "true" ]]; then
  agent_dir="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"
  auth_file="$agent_dir/auth.json"
  if [[ -f "$auth_file" ]]; then
    AUTH_BACKUP="$auth_file.bak.$$"
    AUTH_FILE_TO_RESTORE="$auth_file"
    mv "$auth_file" "$AUTH_BACKUP"
    echo "Moved auth.json to backup"
  fi

  # Unset API keys (see packages/ai/src/env-api-keys.ts).
  while read -r var; do
    case "$var" in ''|'#'*) continue;; esac
    unset "$var"
  done < "$SCRIPT_DIR/scripts/credential-env.txt"
  echo "Running without API keys..."
fi

if ! node -e "require.resolve('tsx', { paths: ['$SCRIPT_DIR'] });" >/dev/null 2>&1; then
  echo "Missing tsx package."
  echo "Run from the repository root:"
  echo "  npm install --ignore-scripts"
  echo "  npm run sure:doctor"
  exit 1
fi

if ! node -e "const base = '$SCRIPT_DIR/packages/coding-agent/src/core/sure'; for (const p of ['typebox','typebox/compile','typebox/value']) require.resolve(p, { paths: [base] });" >/dev/null 2>&1; then
  echo "Missing SURE runtime dependency: typebox."
  echo "Run from the repository root:"
  echo "  npm install --ignore-scripts"
  echo "  npm run sure:doctor"
  exit 1
fi

cd "$SCRIPT_DIR"
node --import tsx "$SCRIPT_DIR/packages/coding-agent/src/cli.ts" ${ARGS[@]+"${ARGS[@]}"}
