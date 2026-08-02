#!/usr/bin/env bash
set -e

AUTH_FILE="$HOME/.pi/agent/auth.json"
AUTH_BACKUP="$HOME/.pi/agent/auth.json.bak"

# Restore auth.json on exit (success or failure)
cleanup() {
    if [[ -f "$AUTH_BACKUP" ]]; then
        mv "$AUTH_BACKUP" "$AUTH_FILE"
        echo "Restored auth.json"
    fi
}
trap cleanup EXIT

# Move auth.json out of the way
if [[ -f "$AUTH_FILE" ]]; then
    mv "$AUTH_FILE" "$AUTH_BACKUP"
    echo "Moved auth.json to backup"
fi

# Skip local LLM tests (ollama, lmstudio)
export PI_NO_LOCAL_LLM=1

# Unset API keys (see packages/ai/src/stream.ts getEnvApiKey).
# The variable list lives in scripts/credential-env.txt so test.sh and
# pi-test.ps1 can't drift apart again.
while read -r var; do
  case "$var" in ''|'#'*) continue;; esac
  unset "$var"
done < "$(dirname "$0")/scripts/credential-env.txt"

echo "Running tests without API keys..."
npm test
