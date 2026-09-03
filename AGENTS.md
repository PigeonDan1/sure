# Development Rules

## Conversational Style

- Keep answers short and concise
- No emojis in commits, issues, PR comments, or code
- No fluff or cheerful filler text (e.g., "Thanks @user" not "Thanks so much @user!")
- Technical prose only, be direct
- When the user asks a question, answer it first before making edits or running implementation commands.
- When responding to user feedback or an analysis, explicitly say whether you agree or disagree before saying what you changed.

## Code Quality

- Read files in full before wide-ranging changes, before editing files you have not fully inspected, and when asked to investigate or audit. Do not rely on search snippets for broad changes.
- No `any` unless absolutely necessary.
- Inline single-line helpers that have only one call site.
- Check node_modules for external API types; don't guess.
- **No inline imports** (`await import()`, `import("pkg").Type`, dynamic type imports). Top-level imports only.
- Never remove or downgrade code to fix type errors from outdated deps; upgrade the dep instead.
- Use only erasable TypeScript syntax (Node strip-only mode) in code checked by the root config (`packages/*/src`, `packages/*/test`, `packages/coding-agent/examples`): no parameter properties, `enum`, `namespace`/`module`, `import =`, `export =`, or other constructs needing JS emit. Use explicit fields with constructor assignments.
- Always ask before removing functionality or code that appears intentional.
- Do not preserve backward compatibility unless the user asks for it.
- Never hardcode key checks (e.g. `matchesKey(keyData, "ctrl+x")`). Add defaults to `DEFAULT_EDITOR_KEYBINDINGS` or `DEFAULT_APP_KEYBINDINGS` so they stay configurable.
- Never modify `packages/ai/src/models.generated.ts` directly; update `packages/ai/scripts/generate-models.ts` instead, then regenerate. Including the resulting `models.generated.ts` diff is always OK, even if regeneration includes unrelated upstream model metadata changes.

## Commands

- After code changes (not docs): `npm run check` (full output, no tail). Fix all errors, warnings, and infos before committing. Does not run tests.
- Never run `npm run build` or `npm test` unless requested by the user.
- Never run the full vitest suite directly: it includes e2e tests that activate when endpoint/auth env vars are present. For all non-e2e tests, run `./test.sh` from the repo root (it takes no arguments: it always hides `auth.json`, unsets the credential variables, then runs `npm test`). Otherwise run specific tests from the package root: `node node_modules/vitest/dist/cli.js --run test/specific.test.ts` (vitest is installed per-package, not hoisted to the repo root; `npx vitest --run <file>` works too).
- If you create or modify a test file, run it and iterate on test or implementation until it passes.
- For `packages/coding-agent/test/suite/`, use `test/suite/harness.ts` + the faux provider. No real provider APIs, keys, or paid tokens.
- Put issue-specific regressions under `packages/coding-agent/test/suite/regressions/` named `<issue-number>-<short-slug>.test.ts`.
- For ad-hoc scripts, `write` them to a temp file (e.g. `/tmp`), run, edit if needed, remove when done. Don't embed multi-line scripts in `bash` commands.
- Never commit unless the user asks.

## Dependency and Install Security

- Treat npm dep and lockfile changes as reviewed code. Direct external deps stay pinned to exact versions.
- Hydrate/update locally with `npm install --ignore-scripts`; clean/CI-style with `npm ci --ignore-scripts`. Don't run lifecycle scripts unless the user asks.
- If dep metadata changes, refresh `package-lock.json` with `npm install --package-lock-only --ignore-scripts`.
- If `packages/coding-agent/npm-shrinkwrap.json` needs regen, run `node scripts/generate-coding-agent-shrinkwrap.mjs` (verify with `--check` or `npm run check`). New deps with lifecycle scripts require review and an explicit allowlist entry in that script; never add one silently.
- Pre-commit blocks lockfile commits unless `PI_ALLOW_LOCKFILE_CHANGE=1`. Don't bypass unless the user wants the lockfile change committed.

## Git

Multiple pi sessions may be running in this cwd at the same time, each modifying different files. Git operations that touch unstaged, staged, or untracked files outside your own changes will stomp on other sessions' work. Follow these rules:

Committing:

- Only commit files YOU changed in THIS session.
- Stage explicit paths (`git add <path1> <path2>`); never `git add -A` / `git add .`.
- Before committing, run `git status` and verify you are only staging your files.
- `packages/ai/src/models.generated.ts` may always be included alongside your files.
- Message format: `{feat,fix,docs}[(ai,tui,agent,coding-agent)]: <commit message> (optionally multiple lines)`. Message is informative and concise.

Never run (destroys other agents' work or bypasses checks):

- `git reset --hard`, `git checkout .`, `git clean -fd`, `git stash`, `git add -A`, `git add .`, `git commit --no-verify`.

If rebase conflicts occur:

- Resolve conflicts only in files you modified.
- If a conflict is in a file you did not modify, abort and ask the user.
- Never force push.

## Issues and PRs

See `CONTRIBUTING.md` for the contributor gate (auto-close workflows, `lgtm`/`lgtmi`, quality bar).

When reviewing PRs:

- Do not run `gh pr checkout`, `git switch`, or otherwise move the worktree to the PR branch unless the user explicitly asks.
- Use `gh pr view`, `gh pr diff`, `gh api`, and local `git show`/`git diff` against fetched refs to inspect PR metadata, commits, and patches without changing branches.
- If you need PR file contents, fetch/read them into temporary files or use `git show <ref>:<path>` without switching branches.

When creating issues:

- Add `pkg:*` labels for affected packages (`pkg:agent`, `pkg:ai`, `pkg:coding-agent`, `pkg:tui`); use all that apply.

When posting issue/PR comments:

- Write the comment to a temp file and post with `gh issue/pr comment --body-file` (never multi-line markdown via `--body`).
- Keep comments concise, technical, in the user's tone.
- End every AI-posted comment with the AI-generated disclaimer line specified by the originating prompt (e.g. `This comment is AI-generated by `/wr``).

When closing issues via commit:

- Include `fixes #<number>` or `closes #<number>` in the message so merging auto-closes the issue. For multiple issues, repeat the keyword per issue (`closes #1, closes #2`); a shared keyword (`closes #1, #2`) only closes the first.

## Testing pi Interactive Mode with tmux

Run the TUI in a controlled terminal (from the repo root):

```bash
tmux new-session -d -s pi-test -x 80 -y 24
tmux send-keys -t pi-test "./pi-test.sh" Enter
sleep 3 && tmux capture-pane -t pi-test -p     # capture after startup
tmux send-keys -t pi-test "your prompt here" Enter
tmux send-keys -t pi-test Escape               # special keys (also C-o for ctrl+o, etc.)
tmux kill-session -t pi-test
```

## Changelog

Location: `packages/*/CHANGELOG.md` (one per package).

Sections under `## [Unreleased]`: `### Breaking Changes` (API changes requiring migration), `### Added`, `### Changed`, `### Fixed`, `### Removed`.

Rules:

- All new entries go under `## [Unreleased]`. Read the full section first and append to existing subsections; never duplicate them.
- Released version sections (e.g. `## [0.12.2]`) are immutable; never modify them.

Attribution:

- Internal (from issues): `Fixed foo bar ([#123](https://github.com/earendil-works/pi-mono/issues/123))`
- External contributions: `Added feature X ([#456](https://github.com/earendil-works/pi-mono/pull/456) by [@username](https://github.com/username))`

## Releasing

**Lockstep versioning**: all packages share one version; every release updates all together. `patch` = fixes + additions, `minor` = breaking changes. No major releases.

1. **Update CHANGELOGs**: ask the user whether they ran the `/cl` prompt on the latest commit on `main`. If not, they must run `/cl` first to audit and update each package's `[Unreleased]` section before releasing.

2. **Local smoke test**: build an unpublished release and smoke test from outside the repo (so it can't resolve workspace files):
   ```bash
   npm run release:local -- --out /tmp/pi-local-release --force
   cd /tmp

   # Node package install smoke tests
   /tmp/pi-local-release/node/pi --help
   /tmp/pi-local-release/node/pi --version
   /tmp/pi-local-release/node/pi --list-models
   /tmp/pi-local-release/node/pi -p "Say exactly: ok"
   /tmp/pi-local-release/node/pi

   # Bun binary smoke tests
   /tmp/pi-local-release/bun/pi --help
   /tmp/pi-local-release/bun/pi --version
   /tmp/pi-local-release/bun/pi --list-models
   /tmp/pi-local-release/bun/pi -p "Say exactly: ok"
   /tmp/pi-local-release/bun/pi
   ```
   Verify both Node and Bun startup, model/account listing, interactive startup, and at least one real prompt with the intended default provider. The bare commands `/tmp/pi-local-release/node/pi` and `/tmp/pi-local-release/bun/pi` start interactive mode; run each in tmux, submit a prompt, and wait for the model reply before considering the interactive smoke test passed. Failures are release blockers unless the user explicitly accepts the risk.

3. **Run the release script**:
   ```bash
   PI_ALLOW_LOCKFILE_CHANGE=1 npm_config_min_release_age=0 npm run release:patch    # fixes + additions
   PI_ALLOW_LOCKFILE_CHANGE=1 npm_config_min_release_age=0 npm run release:minor    # breaking changes
   ```
   Use `npm_config_min_release_age=0` only for the release command. The repo's normal npm age gate can otherwise block the release lockfile refresh when the current workspace package version was published recently. Review any lockfile or shrinkwrap diffs the release creates before push.

   The release script bumps all package versions, updates changelogs, regenerates release artifacts, runs `npm run check`, commits `Release vX.Y.Z`, tags `vX.Y.Z`, adds fresh `## [Unreleased]` changelog sections, commits `Add [Unreleased] section for next cycle`, then pushes `main` and the tag. Do not rerun the release script after a tag was pushed.

4. **CI publishes npm packages**: pushing the `vX.Y.Z` tag triggers `.github/workflows/build-binaries.yml`. The `publish-npm` job uses npm trusted publishing through GitHub Actions OIDC with environment `npm-publish`; no local `npm publish`, `npm whoami`, OTP, or WebAuthn flow is required.

5. **If CI publish fails**: inspect the failed `publish-npm` job. The publish helper is idempotent and skips package versions already present on npm, so rerun the tag workflow after fixing CI or transient npm issues. Do not rerun `npm run release:patch` or `npm run release:minor` for the same version.

## SURE Harness

This fork adds the SURE evaluation control plane (`/sure_feed`, `/sure_onboard`, `/sure_infer`, `/sure_reval`). The common user entry point is `README.md`; bundled company distributions also carry `private/site/docs/handbook.md`. This section is the maintainer side.

### Skill Package Layout

```text
sure/skills/<skill-name>/
  sure.skill.json   # skill manifest
  SKILL.md          # agent-facing operating manual
  hooks/            # state-machine gates
  scripts/          # deterministic execution
  schemas/          # artifact contracts
  references/       # domain references
  examples/         # usage examples
```

Shared memory code lives outside the skill packages, next to the harness runtime:

```text
sure/runtime/memory/      # digest, gates, publish, index, cli (python, stdlib only) + match.ts / hooks.ts
sure/memory/              # instance data, git-ignored, group-writable in a shared checkout
```

### Targeted Checks

```bash
npm run check:sure-hooks
python3 -m py_compile sure/skills/sure_infer/scripts/*.py
cd sure/skills/sure_onboard/scripts && python3 -m unittest test_runtime_inventory.py
python3 -m unittest discover -s sure/runtime/memory -p "test_*.py"
```

- `test_runtime_inventory.py` imports its siblings without touching `sys.path`, so it only runs from inside that directory.
- `python3 -m unittest sure/skills/sure_infer/scripts/test_protocol_provenance.py` needs an interpreter that has the harness-runtime dependencies (see `sure/runtime/harness/requirements.in`, which pins pydantic). The root `requirements.txt` is PyYAML-only and is not enough: on a bare interpreter this test fails with `ModuleNotFoundError: No module named 'pydantic'`. Skills at runtime use the locked venv that `sure/runtime/harness/bootstrap.py` materializes, not the system Python.
- Run `npm run sure:doctor` after changes that affect setup, skill discovery, or external engine detection.
- `npm run check` covers repo checks only and never runs tests; it is non-mutating, so use `npm run format` when Biome should rewrite files.

SURE test files live in `packages/coding-agent/test/suite/` (`sure-extension`, `sure-feed`, `sure-onboard-state-machine`, `sure-onboard-terminal`, `sure-eval-state-machine`, `sure-eval-runbackend`, `sure-eval-red-lines`, `sure-reval-terminal`, `sure-run-output-dir`, `sure-runtime-binding`, `sure-skill-output-dir`, `sure-memory-match`, `sure-memory-hooks`) plus the init suites under `packages/coding-agent/test/sure/`. Run them from `packages/coding-agent` per the vitest rule in Commands.

### Credential-Free Launchers

The variable names live in one shared file:

```text
scripts/credential-env.txt
```

Add new credential variable names there, sorted alphabetically, names only, never secret values. `pi-test.sh --no-env` and `pi-test.ps1 --no-env` temporarily move `auth.json` out of the agent config directory for that run and restore it on exit; `test.sh` always runs credential-free and takes no flags.

### Runtime Provenance Lifecycle

| Stage | Artifact | Rule |
| --- | --- | --- |
| `/sure_onboard` | `runtime_inventory.json` | Summarize model-level backend, Python, runtime probe, weights manifest, and small evidence links. Do not link checkpoint payloads. |
| `/sure_infer` | `prediction_generation_status.json` | Record the actual MCP server command, working directory, safe env snapshot, explicit tool args, protocol resolver output, and dataset generation status. |
| `/sure_infer` | `protocol.yaml` | Read generation status first, runtime inventory second, model config third, environment fallback last. Keep inference fields separate from evaluation results. |
| `/sure_reval` | `prediction_reuse_manifest.json` | Copy/filter predictions only; do not reuse old metric artifacts. |
| `/sure_reval` | `source_inference_provenance.json` | Link source protocol/status/runtime inventory when available and mark unknown sources explicitly. |

### Design Boundary

| Harness owns | Skill packages own |
| --- | --- |
| Slash-command discovery, run lifecycle, state persistence. | Domain prompts, deterministic scripts. |
| Hook execution, tool gates, final manifest validation. | State machines, schemas, checkpoints. |
| Shared runtime contracts. | Validation rules and repair instructions. |

Do not move task-specific metrics, dataset assumptions, or SURE business logic into the common harness unless the rule is truly shared by every skill.

### Repository Hygiene

Generated paths kept out of Git include:

```gitignore
/.sure/
/data/
/results/
/results_*/
/sure/results/
/sure/skills/sure_infer/results/
/sure/.runtime/
/sure/handoffs/
/sure/memory/
/sure/models/*
```

Never commit API keys, provider tokens, auth files, model weights, checkpoints, large datasets, prediction dumps, metric result dumps, virtual environments, or cache directories.

`sure/external/sure-evaluation` is a Git submodule. When bumping the verified engine, commit the gitlink together with the refreshed `sure/runtime/evaluation/runtime.json` lock (`engine_commit` and `engine_pyproject_sha256`). A gitlink-only commit makes the next `/sure_eval` fail on the locked-runtime check. Full procedure and the submodule contract: `docs/evaluation_engine.md`.

### Public Export

`npm run public:export` requires a clean worktree and projects only files tracked by the current Git index. `public-export.yaml` defines the public exclusions and generic deny rules; the optional `private/site/public-export.overlay.yaml` may only add private deny rules and is itself excluded. The v2 manifest identifies the projected tree without exposing the source commit. Use `--private-attestation-output` with an absolute path outside the repository when a private source-commit mapping is required. New private content must live under `private/`. The exclusion list is closed: `check:site-boundary` fails if `public-export.yaml` gains an entry outside the approved exception set.

### Handbook Copies

When the private company distribution is present, `private/site/docs/handbook.md` is the single source and `private/site/scripts/build-handbook.py` produces the markdown/HTML/PDF copies. It refuses to build from a dirty source unless `--dev` is passed, and dev builds are stamped `dev-*` so they cannot be mistaken for a release copy. See the maintenance note at the end of the private handbook.

## User Override

If the user's instructions conflict with any rule in this document, ask for explicit confirmation before overriding. Only then execute their instructions.
