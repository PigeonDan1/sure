#!/usr/bin/env bash
set -euo pipefail

# Product entry point: configure an existing SURE checkout in one command.
ROOT_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
cd "$ROOT_DIR"
ROOT_DIR="$PWD"
command -v node >/dev/null || { echo 'SURE requires Node >=22.19.0' >&2; exit 1; }
command -v uv >/dev/null || { echo 'SURE requires uv: https://docs.astral.sh/uv/' >&2; exit 1; }
node -e 'const [major,minor]=process.versions.node.split(".").map(Number); if (major < 22 || (major === 22 && minor < 19)) process.exit(1)' || {
  echo "SURE requires Node >=22.19.0 (found $(node --version))" >&2; exit 1;
}
git submodule update --init --recursive
npm ci --ignore-scripts
npm run sure:doctor
python3 skills/sure/scripts/run.py list >/dev/null
mkdir -p "${AGENTS_HOME:-$HOME/.agents}/skills"
ln -sfn "$ROOT_DIR/skills/sure" "${AGENTS_HOME:-$HOME/.agents}/skills/sure"
if [[ -n "${SURE_INIT_ARGUMENTS:-}" ]]; then
  python3 skills/sure/scripts/run.py init --arguments "$SURE_INIT_ARGUMENTS" --trust
else
  echo "SURE is discovered. Set SURE_INIT_ARGUMENTS to configure a provider and model, then rerun bootstrap.sh."
fi
echo "SURE is ready. Skill: ${AGENTS_HOME:-$HOME/.agents}/skills/sure"
