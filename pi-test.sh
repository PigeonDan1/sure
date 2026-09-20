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

  echo "Running without stored credentials..."
fi

if ! command -v node >/dev/null 2>&1; then
  echo "node was not found on PATH. Install Node 22.19 or newer (see .nvmrc)."
  exit 1
fi

if ! node -e "const [major, minor] = process.versions.node.split('.').map(Number); process.exit(major > 22 || (major === 22 && minor >= 19) ? 0 : 1);"; then
  echo "Node $(node --version) is too old. This repository needs Node 22.19 or newer (see .nvmrc)."
  exit 1
fi

if [[ "$PWD" != "$SCRIPT_DIR" ]]; then
  echo "Warning: running from $PWD, not the repository root $SCRIPT_DIR." >&2
  echo "Warning: the agent's working directory is the one you started from." >&2
fi

if [[ ! -d "$SCRIPT_DIR/node_modules/@earendil-works/pi-agent-core" ]]; then
  echo "Missing node_modules/@earendil-works/pi-agent-core."
  echo "Run from the repository root:"
  echo "  npm install --ignore-scripts"
  echo "  npm run sure:doctor"
  exit 1
fi

# node is a native Windows binary under Git Bash, so it cannot resolve the MSYS
# spelling of SCRIPT_DIR (/d/repo). Hand it the native path for both the module
# resolution probe and the --import URL.
NATIVE_DIR="$SCRIPT_DIR"
if command -v cygpath >/dev/null 2>&1; then
  NATIVE_DIR="$(cygpath -m "$SCRIPT_DIR")"
fi

if ! node -e "const base = process.argv[1]; for (const p of ['typebox','typebox/compile','typebox/value']) require.resolve(p, { paths: [base] });" "$NATIVE_DIR/packages/coding-agent/src/core/sure" >/dev/null 2>&1; then
  echo "Missing SURE runtime dependency: typebox."
  echo "Run from the repository root:"
  echo "  npm install --ignore-scripts"
  echo "  npm run sure:doctor"
  exit 1
fi

# Node resolves --import as a URL, so a Windows drive letter needs a file:// URL
# and a '#', '%' or '?' in the path would otherwise be read as URL syntax rather
# than as part of the path. Let node spell the URL.
RESOLVER="$NATIVE_DIR/packages/coding-agent/test/source-resolver.ts"
RESOLVER_URL="$(node -e "console.log(require('node:url').pathToFileURL(process.argv[1]).href);" "$RESOLVER")"

node --import "$RESOLVER_URL" "$SCRIPT_DIR/packages/coding-agent/src/cli.ts" ${ARGS[@]+"${ARGS[@]}"}
