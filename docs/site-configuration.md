# Site Configuration

The SURE site policy separates portable workflow logic from deployment-specific storage and execution resources. It does not alter command syntax, state-machine order, evaluation configuration precedence, runtime locks, or artifact schemas.

## Configure a Public Deployment

```bash
cp config/site.example.yaml config/site.local.yaml
# Edit config/site.local.yaml.
npm run sure:site-check
npm run sure:site-info
```

`config/site.local.yaml` is ignored by Git. Do not commit it. An advanced deployment can keep the file outside the checkout and set an absolute path:

```bash
export SURE_SITE_POLICY=/absolute/path/to/site-policy.yaml
npm run sure:site-check
```

An explicit missing, relative, unreadable, or invalid path fails closed. It does not fall back to a bundled or local policy.

## Source Precedence

| Priority | Source | Intended use |
| --- | --- | --- |
| 1 | `SURE_SITE_POLICY` absolute path | CI, tests, and advanced deployment management |
| 2 | `config/site.bundled.yaml` | Trusted policy shipped by a private distribution |
| 3 | `config/site.local.yaml` | Public and self-hosted local configuration |
| 4 | No source | Help and site information only; resource workflows fail closed |

`config/site.example.yaml` and `sure/site/public.example.yaml` are templates. They are never implicit production defaults.

## Schema Fields

The machine-readable contract is [sure/site/policy.schema.json](../sure/site/policy.schema.json).

| Field | Required | Meaning |
| --- | --- | --- |
| `schema` | yes | Must be `sure.site.policy.v1` |
| `site_id` | yes | Stable lowercase deployment identifier |
| `policy_version` | yes | Must be `1` |
| `storage.approved_models_roots` | yes | Read-only roots containing human-approved model packages |
| `storage.approved_results_roots` | yes | Read-only roots containing human-approved evaluation results |
| `storage.forbidden_output_roots` | yes | Roots where automated `output_dir` writes are forbidden |
| `storage.runtime_root` | yes | Declared site-owned cache root for adapters and future runtime placement |
| `datasets.allowed_source_roots` | yes | Roots accepted by the strict dataset source resolver |
| `execution.surfaces` | yes | Enabled execution surfaces: `local`, `vc`, or both |
| `execution.vc_partitions` | when needed | Declared VC partitions for adapters and deployment documentation |
| `execution.vc_partition_priority` | optional | Numeric priority map used by automatic VC selection |
| `network` | optional | Non-secret site endpoints used by private adapters and documentation |

Unknown fields, duplicate list values, unsupported surfaces, and relative paths are rejected. Policy files may contain credential environment-variable names, but never credential values.

Policy v1 accepts exactly one path in each root list. The list shape leaves room for a future multi-root contract, but the current workflow preserves its historical single-root selection semantics.

`execution.surfaces` constrains automatic and explicit execution selection. `execution.vc_partitions` documents site choices but does not replace the existing live `vc info -u` authorization check; changing partition authorization semantics is outside this separation phase.

## Path Semantics

- Every configured root is absolute.
- Approved model and result roots must be protected by a configured forbidden output root.
- The declared runtime root must stay outside forbidden output roots.
- Path authorization resolves existing symlinks before comparison, so alternate names cannot bypass a protected root.
- The policy validator does not create directories and does not require network access. Runtime commands perform their existing availability and permission checks at the same stages as before.
- This compatibility phase does not redirect the locked Harness Runtime or Evaluation Runtime. They continue to materialize in their pre-existing repository-local locations; changing that behavior requires a separate differential migration.
- Container deployments must mount configured roots at paths that preserve the command's existing host/container path contract.

## Minimal Local Example

```yaml
schema: sure.site.policy.v1
site_id: my-lab
policy_version: 1

storage:
  approved_models_roots:
    - /srv/sure/models
  approved_results_roots:
    - /srv/sure/results
  forbidden_output_roots:
    - /srv/sure
  runtime_root: /var/cache/sure/runtime

datasets:
  allowed_source_roots:
    - /srv/datasets

execution:
  surfaces:
    - local
```

For shared storage, replace these placeholders with roots visible to every host and container that executes a workflow. For a local-only fixture, create temporary model, result, dataset, and runtime roots and point the local policy at them.

## Diagnostics

| Command | Scope |
| --- | --- |
| `npm run sure:site-info` | Shows whether a policy is selected and reports source metadata and checksum |
| `npm run sure:site-check` | Validates schema and cross-field storage boundaries without mutation |
| `npm run sure:doctor` | Checks harness/runtime prerequisites beyond the policy contract |

Common repairs:

- `SURE_SITE_POLICY must be an absolute path`: use a fully qualified path or unset it to use the normal distribution source.
- `unknown field`: remove the field or update the policy to the current schema.
- `must be protected by a forbidden output root`: add the approved root's parent to `forbidden_output_roots`.
- `site policy is not configured`: copy the example to `config/site.local.yaml`, edit it, and rerun `sure:site-check`.

Custom execution surfaces and site integrations belong in a private adapter that depends on public interfaces. Public core code must never import a private adapter. In this phase all site differences are expressed as `sure.site.policy.v1` data, so no adapter code ships; a provider interface will be introduced as a separately reviewed design change when a behavior cannot be expressed as policy data.
