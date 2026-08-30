# SURE

> An agent-driven harness for system-level reproducible model evaluation.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/harness-terminal-dark.svg">
  <img src="docs/assets/harness-terminal.svg" alt="One SURE session in the interactive terminal: /sure_init, /sure_feed, /sure_onboard, then /sure_eval ending in a persisted run report" width="100%">
</picture>

*One discover, onboard, evaluate session, replayed as a self-contained SVG animation. Regenerate with `node scripts/generate-readme-terminal.mjs`.*

SURE is an agent-based, system-level reproducible evaluation harness built on [Pi](https://github.com/badlogic/pi-mono), with an interactive terminal UI. It turns model discovery, local deployment, environment adaptation, inference, evaluation, and re-evaluation into integrated workflows instead of leaving users to connect them by hand.

SURE works with coding agents including OpenAI Codex, Kimi Code, GitHub Copilot, Anthropic Claude, OpenAI, and custom OpenAI-compatible gateways such as DeepSeek.

With SURE, one slash command starts each stage. The agent researches the model, prepares its runtime, generates or repairs PyTorch and Transformers inference code when appropriate, validates the deployment, and evaluates the model on versioned datasets through a deterministic evaluation pipeline.

## Why Use SURE

A model score is useful only when people can establish exactly how it was produced. In a conventional evaluation, the loading code, package versions, inference parameters, dataset preparation, normalization, metric implementation, and reports often live in different scripts, environments, or notebooks. The final number may be easy to publish but difficult to reproduce or compare fairly.

SURE gives you both automation and experimental credibility:

- **Deploy and evaluate with integrated commands.** Start a complete discovery, adaptation, deployment, or evaluation stage with one slash command instead of manually assembling scripts and environments.
- **Reproduce the whole system, not just the score.** Every result is tied to the inference implementation, model and Harness Runtime, immutable deployment identity, parameters, dataset version, evaluation engine commit, exact pipeline, and reports.
- **Make results credible and comparable.** Two results can be compared against the concrete framework, configuration, inputs, normalization, and metric route that produced them, rather than against an unexplained number.
- **Keep automation accountable.** Agents handle research and adaptation, while deterministic state machines, schemas, smoke tests, and runtime-specific checks reject incomplete or inconsistent work.

The central promise is simple: SURE reduces deployment and evaluation work without trading away reproducibility. Convenience comes with a complete evidence chain.

## From Model to Result

```text
Model repository       Runnable deployment       Predictions + metrics       New evaluation route
       |                        |                         |                            |
  /sure_feed  ---------->  /sure_onboard  ---------->  /sure_eval  ---------->  /sure_reval
```

| Workflow | What the agent does | Reproducible product |
| --- | --- | --- |
| Discover | Researches a Hugging Face, ModelScope, or GitHub model and resolves its capabilities, runtime, fixtures, and I/O contract | Canonical `model_input.yaml` plus source evidence |
| Onboard | Adapts and validates the model, generates a runnable inference package when needed, and seals local models as a digest-pinned OCI image or content-addressed Python runtime | Portable model bundle plus `deployment_ready.json` |
| Evaluate | Runs approved inference, resolves a deterministic evaluation route, and computes metrics | Predictions, protocol, reports, metrics, and sample-level evidence |
| Re-evaluate | Reuses approved predictions with an explicit pipeline | A new evaluation batch without repeating inference |

For common PyTorch and Transformers models, the onboarding agent can synthesize missing load and inference adapters from upstream documentation and source evidence. Every generated path must still pass environment, selected-runtime, interface, and artifact-contract gates before it can be marked ready.

Promotion is intentionally human-reviewed. SURE stages model bundles and evaluation results, but it never silently copies them into configured approved storage. This keeps convenience from weakening the experiment's trust boundary.

## Choose How to Run SURE

- **Use it online.** OpenBench provides an integrated SURE service and free evaluation compute through the [hosted evaluation platform](https://sure-eval.com/). Machine availability and quotas are managed by the platform.
- **Deploy it privately.** Run SURE on your own machine or infrastructure when models, datasets, credentials, or results must remain inside your environment. Follow the self-hosted setup below.

The public repository contains no internal storage paths, gateways, cluster defaults, credentials, model weights, or datasets. These are supplied by the selected deployment through its local site policy.

## Quick Start

The following path was designed for a Bash-compatible Linux environment.

### 1. Prerequisites

- Node.js 22.19 or newer and npm
- Git
- Python 3.11 and [uv](https://docs.astral.sh/uv/)
- Credentials for at least one supported coding-agent provider
- Docker and access to an OCI registry when using the `docker-registry` profile or VC execution

The registry must support authenticated push and digest-pinned pull from the machine that runs SURE. API-only onboarding and local `package=none` Python execution do not require a model image.

### 2. Clone and install

```bash
git clone --recurse-submodules --branch harness-tui-agent https://github.com/PigeonDan1/sure.git sure-harness
cd sure-harness
npm install --ignore-scripts
```

Do not omit `--recurse-submodules`: SURE pins its evaluation engine as a Git submodule.

### 3. Create a local site policy

A site policy declares which model, result, dataset, runtime, and execution locations this installation may use. The following creates a self-contained local policy under ignored directories in this checkout:

```bash
sure_site_root="$(pwd)/.sure/site"
mkdir -p \
  "$sure_site_root/approved/models" \
  "$sure_site_root/approved/results" \
  "$sure_site_root/runtime" \
  "$sure_site_root/datasets"

cat > config/site.local.yaml <<EOF
schema: sure.site.policy.v1
site_id: local
policy_version: 1

storage:
  approved_models_roots:
    - "$sure_site_root/approved/models"
  approved_results_roots:
    - "$sure_site_root/approved/results"
  forbidden_output_roots:
    - "$sure_site_root/approved"
  runtime_root: "$sure_site_root/runtime"

datasets:
  allowed_source_roots:
    - "$sure_site_root/datasets"

execution:
  surfaces:
    - local
  local_runtimes:
    - python
    - container
EOF

npm run sure:site-info
npm run sure:site-check
npm run sure:doctor
```

`sure:site-info` should report `configured: true` and `source: local`; `sure:site-check` must pass. Before provider and dataset setup, `sure:doctor` may warn about missing Pi authentication or evaluation data.

For shared storage or additional execution surfaces, start from [`config/site.example.yaml`](./config/site.example.yaml) and read the [site configuration guide](./docs/site-configuration.md). Keep `config/site.local.yaml` local, or set `SURE_SITE_POLICY` to an absolute policy path outside the repository.

### 4. Start the TUI and configure an agent

```bash
./pi-test.sh
```

Inside the TUI, trust this checkout if prompted, then initialize SURE:

```text
/trust
/sure_init
```

`/sure_init` lets you choose a built-in provider or define a custom OpenAI-compatible gateway, authenticate, select a model, and verify a real round trip. Initialization is complete when the TUI reports `Switched to provider/model`.

## Use the Workflows

Run the commands below inside the SURE TUI. Parameters follow the `key=value` form.

### Discover a model with `/sure_feed`

Use a direct model URL when you already know what you want to evaluate:

```text
/sure_feed url=https://modelscope.cn/models/Qwen/Qwen3-ASR-1.7B max_models=1
```

SURE researches the repository, classifies the task, resolves fixtures and runtime requirements, and writes:

```text
sure/handoffs/Qwen__Qwen3-ASR-1.7B/
  model_input.yaml
  artifacts/
```

Discovery does not download large weights by default. See [`/sure_feed`](./sure/skills/sure_feed/SKILL.md) for search, filtering, Hugging Face, GitHub, download, and watch-mode parameters.

### Build a reproducible deployment with `/sure_onboard`

Consume the handoff by its normalized directory name:

```text
/sure_onboard model=Qwen__Qwen3-ASR-1.7B
```

For a local model, SURE researches the upstream implementation, materializes an isolated build environment, generates or repairs the adapter, and validates import, load, inference, and the tool contract. The selected package profile then seals either a digest-pinned OCI image or a content-addressed Model Python runtime. A successful bundle ends with:

```text
sure/models/Qwen__Qwen3-ASR-1.7B/artifacts/deployment_ready.json
```

`deployment_ready.json` is a readiness marker, not an approval. Review the complete model directory, then copy that directory into one of the configured `approved_models_roots`. `/sure_eval` only accepts the exact directory name below an approved root.

Omitting `package` keeps the default `docker-registry` profile. For a self-hosted local evaluation that does not need Docker, select the Python profile explicitly:

```text
/sure_onboard model=Qwen__Qwen3-ASR-1.7B package=none
```

The Python profile requires the `uv` backend, a hash-locked requirements file, and `python` in the site's `execution.local_runtimes`. SURE seals a portable runtime identity into the promoted model bundle and resolves the matching runtime below `storage.runtime_root` during `/sure_eval`. It never accepts an arbitrary host Python or model-local `.venv`, and Python deployments cannot be submitted to VC. See [`/sure_onboard`](./sure/skills/sure_onboard/SKILL.md) for API models, explicit handoff paths, device selection, and repair flows.

### Evaluate an approved model with `/sure_eval`

The following is a runnable ASR example, not a boundary on the SURE harness. Place the dataset below an `allowed_source_roots` entry using this source layout:

```text
<allowed-root>/<group>/<store>/ds_pool/<dataset>/
  raws/sample/*.wav
  sample_files/v1.0.0/sample.jsonl
  sample_files/v1.0.0/ds.jsonl
```

For a minimal ASR dataset, `sample.jsonl` contains one JSON object per audio file:

```json
{"sample_id":"utt1","attribute":{"path":"utt1.wav","size":32044,"sample_rate":16000,"duration":1000,"raw_data_format":"wav","channels":1},"annotation":[{"transcription":{"text":["hello world"]}}]}
```

`ds.jsonl` declares the dataset language:

```json
{"audio":{"speech":{"language":"en"}}}
```

`attribute.path` resolves below `raws/sample`; `size` is the file size in bytes and `duration` is in milliseconds. Keep these values consistent with the audio file because source conversion fails closed on missing or inconsistent samples.

Then run a bounded local evaluation. Replace the model and dataset with paths that exist in your site policy:

```text
/sure_eval model=Qwen__Qwen3-ASR-1.7B datasets=/absolute/allowed/root/group/store/ds_pool/example@v1.0.0 execution=local device=cpu max_samples=3 metrics=wer
```

- `model` is the exact approved model directory name, never a path or alias.
- `datasets` is an absolute source path below an allowed dataset root. Add `@<version>` when the source has multiple versions.
- `max_samples=3` is appropriate for a first bounded run; omit it or use `0` for the full dataset.
- The dataset metadata determines the task. Do not pass a task name as the source of truth.

The run stages predictions, `protocol.yaml`, route provenance, reports, metrics, and sample-level artifacts under `sure/results/` for review. See [`/sure_eval`](./sure/skills/sure_eval/SKILL.md) for CUDA, multiple datasets, protocols, execution surfaces, and output locations.

### Re-evaluate approved predictions with `/sure_reval`

After an evaluation result has been reviewed and promoted to an `approved_results_roots` entry, select a different exact evaluation pipeline without rerunning model inference:

```text
/sure_reval model=Qwen__Qwen3-ASR-1.7B datasets=aishell1__v1.0.2 pipeline_id=asr.zh.cer.identity_norm_v1.wenet_cer_v1
```

Re-evaluation requires the complete approved dataset set and an exact pipeline ID. It deliberately rejects `source`, `metrics`, and `max_samples`, because changing those would weaken prediction identity or turn the full re-evaluation into a sample run. See [`/sure_reval`](./sure/skills/sure_reval/SKILL.md) for the full contract.

## Evaluation Engine

The SURE harness is designed to orchestrate different evaluation tasks and engines. Its built-in evaluation plugin, [`sure-evaluation`](https://github.com/PigeonDan1/sure-evaluation), is pinned at `sure/external/sure-evaluation` and currently provides a broad catalog covering recognition, translation, diarization, generation, enhancement, classification, understanding, and keyword spotting workflows.

Each metric is an explicit pipeline of versioned nodes. You can select different normalization, transformation, transcription, and scoring implementations for the same reported metric, while SURE preserves the exact node chain, engine commit, inputs, parameters, aggregate report, and sample-level report. See the [evaluation engine boundary](./docs/evaluation_engine.md) for runtime locking and configuration precedence.

## Configuration and Privacy Boundary

Provider credentials and site policy are separate:

- `/sure_init` configures the coding-agent provider and authentication.
- The site policy configures approved storage roots, forbidden output roots, dataset roots, a runtime cache root, and allowed execution surfaces.

Site-policy sources use this fixed precedence:

1. Absolute path in `SURE_SITE_POLICY`.
2. Trusted distribution policy at `config/site.bundled.yaml`.
3. Local untracked policy at `config/site.local.yaml`.
4. No policy; resource-dependent workflows fail closed.

An invalid explicit policy never falls back to another source. Evaluation configuration is separate and resolves `config=` first, then `SURE_EVAL_CONFIG`, then the pinned engine's `config/default.yaml`.

Source code, skills, schemas, documentation, and small fixtures belong in Git. Credentials, local policies, model weights, datasets, run state, and generated results do not. See [`.gitignore`](./.gitignore) and [`AGENTS.md`](./AGENTS.md).

## Contributing

Contributions are welcome at the boundary that owns the behavior:

- Add or improve a model workflow skill in this repository. Start with the [skill package guide](./sure/skills/README.md) and follow [`AGENTS.md`](./AGENTS.md).
- Add an evaluation task that does not yet exist, a new metric, a different normalization tool, or another evaluation route in [`sure-evaluation`](https://github.com/PigeonDan1/sure-evaluation). Follow its [contribution guides](https://github.com/PigeonDan1/sure-evaluation/tree/main/docs).
- Report harness defects or documentation gaps in this repository with the smallest reproducible case and the relevant run artifacts, excluding secrets and large payloads.

Together, let us make model evaluation fair, comparable, and reproducible.

## License

[MIT](./LICENSE)
