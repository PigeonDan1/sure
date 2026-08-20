# SURE Harness

SURE Harness is the TUI agent control plane for speech and audio model discovery, onboarding, evaluation, and re-evaluation. Its four workflows are guarded by state machines and machine-readable artifacts.

## Distribution

This repository supports two distributions from one codebase:

- A distribution with `config/site.bundled.yaml` carries a trusted site policy. Users do not create a local policy before running SURE workflows.
- A public or self-hosted distribution does not carry a site policy. Users configure their own storage and execution boundaries before running workflows that consume models, datasets, or promoted results.

The public core, command parameters, state machines, gates, runtime locks, artifact schemas, and evaluation configuration precedence are the same in both distributions. A site policy supplies deployment-specific roots and execution resources; it does not redefine workflow behavior.

Provider credentials and site policy are separate:

- `/sure_init` configures model providers and authentication.
- The site policy configures approved storage roots, forbidden output roots, dataset roots, a declared site runtime cache root, and execution surfaces.

For compatibility, this separation phase does not redirect the existing locked Harness or Evaluation Runtime materialization. Their repository-local locations and runtime binding artifacts remain unchanged; `storage.runtime_root` is validated site metadata for adapters and a separately reviewed future migration.

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

The public distribution contains no private storage paths, internal gateways, cluster defaults, or credentials. Configure the deployment before running `/sure_eval` or `/sure_reval`:

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

## Site Configuration

Configuration sources use this fixed order:

1. Absolute path in `SURE_SITE_POLICY`.
2. Trusted distribution policy at `config/site.bundled.yaml`.
3. Local untracked policy at `config/site.local.yaml`.
4. No policy; resource-dependent workflows are rejected.

An explicit invalid `SURE_SITE_POLICY` never falls back to another source. The complete schema, field semantics, symlink rules, examples, and diagnostics are documented in [docs/site-configuration.md](./docs/site-configuration.md).

Site policy is independent from the evaluation engine configuration. Evaluation still resolves `config=` first, then `SURE_EVAL_CONFIG`, then the evaluation submodule's `config/default.yaml`.

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

## Four SURE Commands

| Command | Purpose | Main product |
| --- | --- | --- |
| `/sure_feed` | Discover a model and create the onboarding handoff | `sure/handoffs/<model>/model_input.yaml` and evidence |
| `/sure_onboard` | Build and validate a runnable model package | wrapper, model package, and `deployment_ready.json` |
| `/sure_eval` | Run inference and deterministic evaluation for an approved model | predictions, protocol, reports, and metric artifacts |
| `/sure_reval` | Reuse approved predictions with an explicit evaluation route | an appended evaluation batch in the local result mirror |

Promotion into configured approved model and result roots remains a human-reviewed operation. Runtime products are staged in repository-local ignored directories or an explicit `output_dir`; SURE workflows do not promote them automatically.

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
