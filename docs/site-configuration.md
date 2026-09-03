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
| `storage.approved_models_roots` | yes | Protected model root written only by `/sure_approve` and read by `/sure_infer` |
| `storage.approved_results_roots` | optional | Read-only roots containing human-approved inference results; `/sure_eval` reads them only when configured, otherwise it scores the local `/sure_infer` run |
| `storage.forbidden_output_roots` | yes | Roots where automated `output_dir` writes are forbidden |
| `storage.runtime_root` | yes | Site-owned cache root for content-addressed Model Python runtimes and adapters |
| `datasets.allowed_source_roots` | yes | Key-value map of dataset source key → absolute path accepted by the strict dataset source resolver. Each request specifies a `dataset_source_key` to select which root to use. |
| `datasets.projection_root` | optional | Writable root for generated dataset JSONL indexes and metadata; raw data remains in the allowed source root |
| `execution.surfaces` | yes | Enabled execution surfaces: `local`, `vc`, or both |
| `execution.local_runtimes` | optional | Permitted local runtimes: `python`, `container`, or both; omission remains container-only |
| `execution.vc_project` | for `vc` | Submission project passed to the VC backend |
| `execution.vc_partitions` | when needed | Declared VC partitions for adapters and deployment documentation |
| `execution.vc_partition_priority` | optional | Numeric priority map used by automatic VC selection |
| `network` | optional | Non-secret site endpoints used by private adapters and documentation |
| `network.container_registry` | for `docker-registry` | Registry host or host/path prefix, without a URL scheme |
| `container_delivery.repository_template` | for `docker-registry` | Repository template using `{registry}`, optional `{task}`, and `{model_name}` |

Unknown fields, duplicate list values, unsupported surfaces, and relative paths are rejected. Policy files may contain credential environment-variable names, but never credential values.

Policy v1 accepts exactly one path in each storage root list. The `datasets.allowed_source_roots` field uses a key-value map instead, allowing multiple source directories to be configured with distinct keys. Requests select which key to use via the `dataset_source_key` parameter.

`storage.approved_models_roots[0]` is the single approval-root setting. `/sure_approve` publishes a verified model package only to `<approved_models_roots[0]>/<model>`, and `/sure_infer model=<model>` resolves that exact child directory from the same root. Neither command accepts a per-run approval-root override, so a deployment configures this location once rather than keeping publication and discovery settings in sync manually.

`execution.surfaces` constrains automatic and explicit execution selection. When `vc` is enabled, `execution.vc_project` is required and carries the submission project the VC backend expects. `execution.vc_partitions` documents site choices but does not replace the existing live `vc info -u` authorization check; changing partition authorization semantics is outside this separation phase.

`execution.local_runtimes` is a permission boundary, not a package-manager choice. Enabling `python` permits an explicitly sealed Model Python runtime; it does not make host execution the default and does not permit Python execution on VC.

## Path Semantics

- Every configured root is absolute.
- Approved model and result roots must be protected by a configured forbidden output root. Ordinary `output_dir` writes remain forbidden there; model publication enters the approved model root only through `/sure_approve`.
- The declared runtime root must stay outside forbidden output roots.
- A configured dataset projection root must stay outside forbidden output roots and must not overlap an allowed source root.
- Path authorization resolves existing symlinks before comparison, so alternate names cannot bypass a protected root.
- The policy validator does not create directories and does not require network access. Runtime commands perform their existing availability and permission checks at the same stages as before.
- Sealed Model Python runtimes materialize under `storage.runtime_root/models/<runtime_id>`. The promoted model stores only the portable runtime identity and manifest; `/sure_infer` resolves and verifies the matching site runtime before use.
- The locked Harness Runtime and Evaluation Runtime remain in their repository-local locations. `storage.runtime_root` does not redirect either role.
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
    default: /srv/datasets
  projection_root: /var/lib/sure/dataset-projections

execution:
  surfaces:
    - local
  local_runtimes:
    - python
    - container

network:
  container_registry: registry.example.com

container_delivery:
  repository_template: "{registry}/my-org/sure-{task}-{model_name}"
```

For shared storage, replace these placeholders with roots visible to every host and container that executes a workflow. The projection root is the only writable dataset workspace; source roots are mounted read-only. For a local-only fixture, create temporary model, result, dataset, projection, and runtime roots and point the local policy at them.

A VC-enabled deployment adds its backend project and partition allowlist:

```yaml
execution:
  surfaces:
    - local
    - vc
  vc_project: example-project
  vc_partitions:
    - gpu-example
  vc_default_partition: gpu-example
```

`container_delivery.repository_template` is site data, not an agent decision. Registry-backed workflows resolve it before downloading model weights or building an image. The template must start with `{registry}/`, must include `{model_name}`, and may include `{task}`. `/sure_onboard` uses the resolved repository as its delivery target; `/sure_trans` uses the same target and appends `-source` for its source-image repository.

When `image_version` is omitted, the workflow queries the relevant Registry V2 tag lists, considers only `major.minor.patch` tags, and selects the next patch after the highest existing version; an empty repository starts at `0.1.0`. A query failure blocks instead of guessing a tag. The resolved repositories, version, observed tags, and site-policy identity are recorded in `model_input_resolved.json` or `trans_input_resolved.json`; credentials remain in the Docker credential store and never enter either artifact or the policy.

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
- `site policy is missing network.container_registry` or `container_delivery.repository_template`: configure both fields before selecting `docker-registry` delivery.
- `execution.vc_project is required when the vc surface is enabled`: configure the backend submission project before enabling VC.
- `registry tag query failed`: verify registry reachability, Docker login state, and the resolved repository; do not bypass automatic versioning with a guessed tag.

Custom execution surfaces and site integrations belong in a private adapter that depends on public interfaces. Public core code must never import a private adapter. In this phase all site differences are expressed as `sure.site.policy.v1` data, so no adapter code ships; a provider interface will be introduced as a separately reviewed design change when a behavior cannot be expressed as policy data.
