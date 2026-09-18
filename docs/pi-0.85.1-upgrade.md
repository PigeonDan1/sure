# pi 0.85.1 Upgrade

The vendored pi copy under `packages/coding-agent` and `packages/ai` moved from 0.80.3+18 to 0.85.1, and stays there. From this release on, the fork does not follow pi releases: the vendored code is edited directly, and `@earendil-works/pi-agent-core`, `@earendil-works/pi-tui`, `@earendil-works/chord`, and `@earendil-works/pi-telemetry` are pinned to exactly `0.85.1`. The patch inventory lives in [AGENTS.md](../AGENTS.md).

SURE command syntax, state-machine order, artifact schemas, site policy resolution, and the evaluation runtime lock are unchanged.

## What Changed for Users

- `/sure_init` logs in through pi's `AuthInteraction`. The prompts are the same. An API key entered there now takes effect in the current session immediately instead of only after a restart.
- The model selector is pi's current one: it opens on a synchronous snapshot and refreshes in the background, it is searchable, and `Ctrl+S` saves the highlighted model as the default (the key is `app.models.save` and is rebindable). SURE's three-tier provider scope is kept: the selected provider, all providers, or the scoped model list.
- `/thinking` gained a `max` level, and `/sure_init` probes it along with the others.
- Custom gateways named `radius`, `baseten`, or `qwen-token-plan*` are rejected, because those are built-in provider ids in 0.85.1. Rename the gateway.
- Five built-in providers arrived with 0.85.1: `baseten`, `qwen-token-plan`, `qwen-token-plan-cn`, `qwen-token-plan-individual`, and the dynamic `radius`. None were removed.
- Azure's default model is `gpt-5.5`.
- A `powershell` tool for Windows is available; enable it through `--tools` or the `defaultTools` setting.

## What Changed for Maintainers

- coding-agent's vitest runs offline by default: `PI_OFFLINE=1` is set in coding-agent's vitest config, and a test that genuinely needs the network calls `allowNetwork()` from `packages/coding-agent/test/test-network-env.ts`.
- `test.sh` is pi's `env -i` sandbox. It starts the suite from an empty environment and passes through only an allowlist of platform and test variables, so variables outside that list (`PI_OFFLINE`, `NODE_OPTIONS`, proxy variables) do not reach the tests. It still takes no arguments.
- `pi-test.sh` and `pi-test.ps1` run the CLI straight from the TypeScript sources through `node --import packages/coding-agent/test/source-resolver.ts`; tsx is no longer involved. `--no-env` now only moves `auth.json` aside.
- The model catalog is committed and frozen, so pi-ai builds with `npm run build:offline` and the network hydrate scripts must not be run. The rules are in [AGENTS.md](../AGENTS.md).

## What Was Removed

- The vendored `packages/agent` and `packages/tui` are gone; they are now the npm dependencies `@earendil-works/pi-agent-core` and `@earendil-works/pi-tui`. The vendored `packages/orchestrator` is gone with no counterpart. `@earendil-works/chord` and `@earendil-works/pi-telemetry` are new 0.85.1 dependencies rather than replacements: chord for coding-agent, pi-telemetry for pi-ai.
- pi's experimental remote runtime: `src/experimental`, `src/cli/experimental`, `src/client`, their 18 test files, and the example plugin that only demonstrated the plugin system. Its Node source-resolution hook survives as `packages/coding-agent/test/source-resolver.ts`, which is what the launchers import.
- Three pi tests that reached across the workspace into the removed tui package's own sources and helpers. Nothing they covered belongs to `coding-agent`; pi-tui ships its own suite.
- `scripts/credential-env.txt` and the `check:credential-env` script that validated it. The `env -i` allowlist in `test.sh` replaces the deny list.
