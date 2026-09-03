# SURE Harness

SURE Harness is an agent-based, system-level harness for reproducible model onboarding and evaluation. Built on the pi agent framework, it provides a TUI workflow that coding agents such as Codex, DeepSeek, and Kimi Code can operate through structured slash commands.

SURE turns model deployment, environment adaptation, inference-code generation, and dataset evaluation into one auditable workflow. The resulting runtime identity, dependency locks, parameters, model binding, predictions, metric route, and reports remain available as structured evidence, so an experiment is easier to reproduce, compare, and trust than an undocumented sequence of shell commands.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/harness-terminal-dark.svg">
  <img src="docs/assets/harness-terminal.svg" alt="One SURE session in the interactive terminal: /sure_init, /sure_feed, /sure_onboard, /sure_approve, /sure_infer, then /sure_eval ending in a persisted run report" width="100%">
</picture>

*One discover, onboard, approve, infer, evaluate session, replayed as a self-contained SVG animation. Regenerate with `node scripts/generate-readme-terminal.mjs`.*

## Distribution

This repository supports two distributions from one codebase:

- A distribution with `config/site.bundled.yaml` carries a trusted site policy. Users do not create a local policy before running SURE workflows.
- A public or self-hosted distribution does not carry a site policy. Users configure their own storage and execution boundaries before running workflows that consume models, datasets, or promoted results.

The public core, command parameters, state machines, gates, runtime locks, artifact schemas, and evaluation configuration precedence are the same in both distributions. A site policy supplies deployment-specific roots and execution resources; it does not redefine workflow behavior.

Provider credentials and site policy are separate:

- `/sure_init` configures model providers and authentication.
- The site policy configures approved storage roots, forbidden output roots, dataset roots, a declared site runtime cache root, execution surfaces, and optional container delivery naming.

The locked Harness and Evaluation runtimes remain repository-local. A sealed local Model Python runtime is content-addressed below `storage.runtime_root`; its portable identity is stored with the model bundle and resolved against the active site at inference time.

## Quick Start

Node.js 22.19 or newer, Git, Python 3, and the repository submodules are required.

### Bundled site policy

Use this path when the distribution contains `config/site.bundled.yaml`:

```bash
git clone --recurse-submodules <repository-url> sure-harness
cd sure-harness
npm install --ignore-scripts
npm run sure:site-info
npm run sure:site-check
npm run sure:doctor
PI_OFFLINE=1 ./pi-test.sh
```

`sure:site-info` must report `configured: true` and `source: bundled`. Do not copy the public example over the bundled policy. A bundled distribution may include site-specific operating documentation below `private/`.

### Public/self-hosted site policy

The public distribution contains no private storage paths, internal gateways, cluster defaults, or credentials. Configure the deployment before running `/sure_infer` or `/sure_eval`:

```bash
git clone --recurse-submodules --branch harness-tui-agent https://github.com/PigeonDan1/sure.git sure-harness
cd sure-harness
npm install --ignore-scripts
cp config/site.example.yaml config/site.local.yaml
# Edit every path and execution surface in config/site.local.yaml.
npm run sure:site-check
npm run sure:doctor
PI_OFFLINE=1 ./pi-test.sh
```

`config/site.local.yaml` is ignored by Git and must remain local. Advanced deployments can instead set `SURE_SITE_POLICY` to an absolute path outside the repository.

Without a site policy, generic CLI help and `npm run sure:site-info` remain available. A command that requires site resources fails closed and points back to this Quick Start and [the site configuration guide](./docs/site-configuration.md); it never invents public defaults.

### Local execution profiles

Docker registry delivery remains the default for local models and is required for VC execution. A self-hosted site that does not need Docker can explicitly permit sealed local Python execution:

```yaml
execution:
  surfaces:
    - local
  local_runtimes:
    - python
```

Then select the Python profile during onboarding and local execution during inference:

```text
/sure_onboard model=<model> package=none
/sure_approve model_dir=/path/to/sure/models/<model>
/sure_approve mode=approve review_manifest=/path/to/review_packet.json decision=approve
/sure_infer model=<approved-model> datasets=<source-path> execution=local
/sure_eval model=<approved-model> datasets=<dataset> metrics=<metric>
```

The two approval calls are intentionally separate: the first creates an immutable candidate and review packet; the second requires an explicit human decision and publishes the verified package. This path currently requires the `uv` backend and a hash-locked requirements file. SURE materializes a content-addressed Model Runtime below the configured `storage.runtime_root`, seals its portable manifest into the approved model bundle, and verifies the runtime plus model-core hashes before inference. A model-local `.venv` or arbitrary host Python is never accepted as an Eval runtime. Omitting `package` keeps the Docker registry default.

## Site Configuration

Configuration sources use this fixed order:

1. Absolute path in `SURE_SITE_POLICY`.
2. Trusted distribution policy at `config/site.bundled.yaml`.
3. Local untracked policy at `config/site.local.yaml`.
4. No policy; resource-dependent workflows are rejected.

An explicit invalid `SURE_SITE_POLICY` never falls back to another source. The complete schema, field semantics, symlink rules, examples, and diagnostics are documented in [docs/site-configuration.md](./docs/site-configuration.md).

Configure `storage.approved_models_roots[0]` once. `/sure_approve` publishes approved packages only to that root, and `/sure_infer model=<name>` resolves the exact child directory from the same root. There is no command-level approval-root override.

For `docker-registry` delivery, the active site policy supplies the registry and repository template. SURE resolves the image destination and version before planning, rather than allowing an agent to invent a namespace; registry credentials remain in the deployment's Docker credential store.

Site policy is independent from the evaluation engine configuration. `/sure_infer` resolves its `config=` parameter first, then `SURE_EVAL_CONFIG`, then the evaluation submodule's `config/default.yaml`. `/sure_eval` rejects an explicit `config=`; it always scores the source bundle as resolved.

## Verify Installation

```bash
npm run sure:site-info
npm run sure:site-check
npm run sure:doctor
npm run check
```

- `sure:site-info` reports the selected source, `site_id`, policy version, path, and SHA256 without printing credentials.
- `sure:site-check` validates the policy contract and storage-boundary relationships. It does not create directories or contact production services.
- `sure:doctor` checks the local harness installation and runtime prerequisites.
- `npm run check` runs repository-wide static checks.

## Six SURE Commands

| Command | Purpose | Main product |
| --- | --- | --- |
| `/sure_feed` | Discover a model and create the onboarding handoff | `sure/handoffs/<model>/model_input.yaml` and evidence |
| `/sure_onboard` | Build and validate a runnable model package | wrapper, model package, and `deployment_ready.json` |
| `/sure_trans` | Transform an existing Dockerfile, model, and inference entrypoint into the standard Eval runtime contract | adapter wrapper, digest-pinned image, and `deployment_ready.json` |
| `/sure_approve` | Audit a completed model package, bind an explicit human decision, and publish it atomically | review packet, approval decision, and `approval_ready.json` |
| `/sure_infer` | Run an approved model over the selected datasets | an inference bundle: predictions, `protocol.yaml`, generation status, and reference projections |
| `/sure_eval` | Score an inference bundle with the evaluation engine, without running the model | an appended `evaluation_runs/<batch>/` with metric artifacts and `eval_run_report.json` |

When a run's turn ends without `sure_finish`, because the model service failed or the session went away, `/sure_resume` picks that run back up from its checkpoint in the same run directory instead of starting over. It takes the most recent resumable run in the project, or `/sure_resume <run-id>` for a specific one.

Model publication is a two-run, human-gated `/sure_approve` workflow; ordinary workflow outputs cannot write into protected roots. Promotion of evaluation results remains a separate human-reviewed operation. Runtime products are otherwise staged in repository-local ignored directories or an explicit `output_dir`.

## Repository Content

Source code, skills, schemas, documentation, and small fixtures belong in Git. Credentials, local policy, model weights, datasets, run state, and generated results do not. See [.gitignore](./.gitignore) and [AGENTS.md](./AGENTS.md).

`sure/external/sure-evaluation` is pinned as a Git submodule. See [docs/evaluation_engine.md](./docs/evaluation_engine.md) for the evaluator boundary and route selection model.

## Documentation

- [docs/site-configuration.md](./docs/site-configuration.md): full site policy schema, source precedence, and diagnostics
- [docs/evaluation_engine.md](./docs/evaluation_engine.md): evaluation engine boundary, routes, and runtime locking
- [AGENTS.md](./AGENTS.md): maintainer-facing development and hygiene guide
- Distributions with a bundled site policy may also carry additional site-specific documentation under a private directory that is not part of the public export.

## License

[MIT](./LICENSE)
