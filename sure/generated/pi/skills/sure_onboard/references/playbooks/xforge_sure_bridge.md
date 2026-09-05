# XForge to SURE Bridge

This playbook describes the adapter layer between:

- the `scan_modelscope` unit (in `/sure_feed`)
- the `collect_metadata` unit (in `/sure_feed`)
- the `convert_to_oref` unit (in `/sure_feed`)
- SURE dataset preparation and model onboarding

The bridge does not edit XForge skills and does not rewrite SURE agent flow.
It only converts XForge-style resource manifests into deterministic SURE
artifacts.

This playbook is not the SURE main-flow agent. Main evaluation orchestration
lives in `docs/agents/main_flow_agent/AGENTS.md`; model onboarding/tool-agent behavior
lives in `docs/agents/model_tool_agent/AGENTS.md`. The bridge only prepares selected
ModelScope/XForge resources for those flows.

## Boundary

XForge remains responsible for high-uncertainty resource work:

- finding candidate datasets and speech models
- selecting providers such as HuggingFace, ModelScope, GitHub release, or local paths
- recording evidence, license, and source metadata

The bridge is responsible for deterministic file materialization and handoff:

- copying or collecting selected resources
- converting raw JSONL records into SURE JSONL
- placing remote ModelScope weights under model-local `.runtime/modelscope_cache/`
  and placing only explicit local weights under `checkpoints/`
- writing `weights_manifest.json`
- generating model-local SURE onboarding scaffolds and
  `artifacts/tool_agent_request.json`

SURE remains responsible for validation and execution:

- `scripts/prepare_sure_dataset.py`
- prediction generation scripts
- `docs/agents/model_tool_agent/AGENTS.md` model onboarding states

## Daily ModelScope Watch Flow

The bridge can monitor ModelScope for newly discovered task-relevant resources
without changing XForge or SURE code.

```text
scripts/xforge_watch_modelscope.py
  -> data/artifacts/xforge/modelscope_catalog.json
  -> data/artifacts/xforge/modelscope/manifests/*.json
  -> data/artifacts/xforge/modelscope/handoff/*.handoff.json
```

Example ASR watch. Use the requested window; the current full-flow smoke uses
`--since-days 7`.

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
  .venv.hostbak/bin/python scripts/xforge_watch_modelscope.py \
  --task ASR \
  --since-days 7 \
  --resource all \
  --catalog data/artifacts/xforge/modelscope_catalog.json \
  --manifest-dir data/artifacts/xforge/modelscope/manifests \
  --handoff-dir data/artifacts/xforge/modelscope/handoff \
  --summary data/artifacts/xforge/modelscope/watch_summary.json
```

For offline testing or for handoff from XForge `discover`, pass a candidate
list instead of querying ModelScope:

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
  .venv.hostbak/bin/python scripts/xforge_watch_modelscope.py \
  --task ASR \
  --candidates-json data/artifacts/xforge/asr_candidates.json \
  --catalog data/artifacts/xforge/modelscope_catalog.json
```

Candidate format:

```json
[
  {
    "resource_type": "model",
    "provider": "modelscope",
    "resource_id": "iic/demo-asr-model",
    "name": "demo-asr-model",
    "task": "ASR",
    "updated_at": "2026-06-06T00:00:00Z"
  },
  {
    "resource_type": "dataset",
    "provider": "modelscope",
    "resource_id": "speech/demo-asr-dataset",
    "name": "demo-asr-dataset",
    "task": "ASR",
    "language": "zh",
    "updated_at": "2026-06-06T00:00:00Z"
  }
]
```

The catalog de-duplicates resources by:

```text
provider:resource_type:resource_id
```

Only newly discovered resources emit new manifests/handoff files.

### Handoff Files

Watcher model handoff files target SURE tool onboarding, but they are not the
final SURE handoff. After a human selects a model, run
`scripts/xforge_modelscope_fetch.py`; that command downloads ModelScope weights
under `.runtime/modelscope_cache/`, records the resolved path in
`weights_manifest.json`, and emits the model-local SURE tool-agent request.

```json
{
  "event_type": "xforge_model_discovered",
  "target_agent": "sure_tool_agent",
  "next_state": "FETCH_WEIGHTS",
  "status": "ready_for_model_collect",
  "manifest_path": "data/artifacts/xforge/modelscope/manifests/iic__demo-asr-model.model.json"
}
```

Dataset handoff files target SURE main flow, but generic new datasets are
blocked until a schema mapping is supplied:

```json
{
  "event_type": "xforge_dataset_discovered",
  "target_agent": "sure_main_agent",
  "next_state": "DATASET_SCOPE_UNIT",
  "status": "blocked_until_dataset_schema_mapping",
  "manifest_path": "data/artifacts/xforge/modelscope/manifests/speech__demo-asr-dataset.dataset.json"
}
```

This is intentional. Most public datasets do not expose a uniform audio/text
schema. The bridge will not guess field mappings. Once a dataset manifest has
`raw_jsonl` and `field_mapping`, run `scripts/xforge_process_to_sure.py`.

## Daily ModelScope Summary Flow

The first-version daily workflow does not auto-download recommendations. It
writes a local Markdown report for human review. Use this stable report root;
do not create competing `modelscope_*` report directories.

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
  .venv.hostbak/bin/python scripts/xforge_daily_modelscope_summary.py \
  --tasks asr s2tt slu gr ser \
  --since-days 7 \
  --top-k 3 \
  --max-items 200 \
  --resource all \
  --output-root reports/xforge/modelscope
```

The command writes:

```text
reports/xforge/modelscope/YYYY-MM-DD/summary.md
reports/xforge/modelscope/YYYY-MM-DD/summary.json
reports/xforge/modelscope/YYYY-MM-DD/candidates.json
```

Known operational pitfalls:

- Online ModelScope discovery requires network access. If a sandboxed run
  records `<urlopen error [Errno 1] Operation not permitted>` under Failures,
  rerun the same no-proxy command with approved network execution. Do not
  interpret that failure as "no candidates". If ModelScope returns
  `HTTP Error 502: Bad Gateway`, rerun the same no-proxy command before drawing
  conclusions.
- `summary.json` stores candidates under:

```text
tasks.<task>.<resource_type>.recommended
tasks.<task>.<resource_type>.other
```

For ASR models, read `tasks.asr.model.recommended` and
`tasks.asr.model.other`. Do not read `tasks.asr.models`; that path is not part
of the schema and will look empty.
- A run with `--resource model` intentionally leaves dataset sections empty.
  Use `--resource all` or `--resource dataset` when validating dataset
  discovery.

### ModelScope Matching Rules

ModelScope model and dataset discovery must use the same matching contract:
combine strong official-task matches with weak fallback matches, then record
which path produced the candidate.

Strong match:

- Query the ModelScope task-page semantics for the task.
- For ASR models, preserve the page meaning
  `tasks=auto-speech-recognition&type=audio`.
- For ASR datasets, preserve the page meaning
  `Tags=auto-speech-recognition&dataType=audio`.
- Candidates from this path should carry
  `acquisition_filter.match_source: official_task`.

Weak fallback:

- Also query task abbreviations and speech-domain terms, such as `asr` and
  `speech`.
- Accept candidates when `task_match_score` matches through custom tags, name,
  description, or tags.
- Do not accept short ambiguous abbreviations alone. For GR/gender recognition,
  `gr` must be backed by gender semantics or speech/audio-domain evidence.
  `microsoft/Dayhoff-3b-GR-HM` is a known false-positive example because it is
  `task:text-generation` with `custom_tag:protein-generation`, not gender
  recognition.
- Candidates from this path should carry
  `acquisition_filter.match_source: custom_tag_fallback`.

Do not make models stricter than datasets, or datasets stricter than models.
The two resource types should differ only in the ModelScope page parameters
they preserve in `acquisition_filter.ui_params`.

Important example: `zhifeixie/Voices-in-the-Wild-test-v2` is an ASR-relevant
dataset with `tasks: []`, but it has `custom_tag:asr`, `custom_tag:speech`, and
`custom_tag:audio`. It will not appear from the strong
`search=auto-speech-recognition` path. It must be found by
`custom_tag_fallback` when its `last_modified` timestamp is inside the requested
`--since-days` window.

Ranking is still time-window based:

```text
task match -> requested time window -> report-date candidates first -> downloads
```

If a known candidate is missing, check `last_modified` against `--since-days`
before widening or rewriting the matching rules.

Read `summary.md`, ask the user to choose a model or dataset, then run the fetch
command shown beside the selected candidate. Do not auto-download the Top 1
candidate unless the user explicitly says to use Top 1 as a validation sample.
The required pause after daily discovery is `MODEL_DOWNLOAD_CONFIRM`.

Model example:

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
  .venv.hostbak/bin/python scripts/xforge_modelscope_fetch.py \
  --resource model \
  --task asr \
  --id iic/example-modelscope-model \
  --name example-modelscope-model
```

For remote ModelScope models, the canonical weight location is the provider
cache under `sure/models/<slug>/.runtime/modelscope_cache/`. Do not
copy the same snapshot into `checkpoints/`. `weights_manifest.json` records the
resolved local path and the materialization strategy. `checkpoints/` is reserved
for explicit local weights supplied outside the ModelScope cache.

Dataset example:

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
  .venv.hostbak/bin/python scripts/xforge_modelscope_fetch.py \
  --resource dataset \
  --task asr \
  --id speech/example-modelscope-dataset \
  --no-download
```

Datasets remain blocked until a schema mapping or known adapter is available.
When a raw JSONL schema is known, pass `--schema-mapping` to convert it to SURE
JSONL during fetch.

## Dataset Flow

```text
XForge discover
  -> dataset discovery manifest
XForge collect or scripts/xforge_collect_dataset.py
  -> data/datasets/xforge_raw/<dataset>/manifest.json
scripts/xforge_process_to_oref.py
  -> data/datasets/<dataset>/audio/
  -> data/datasets/<dataset>/sample.jsonl
  -> config/oref_datasets.yaml entry
scripts/xforge_process_to_sure.py
  -> data/datasets/xforge_sure/<dataset>.jsonl  # compatibility output
SURE prepare/evaluate scripts
  -> predictions and metrics
```

### Dataset Manifest

```json
{
  "resource_type": "dataset",
  "dataset_id": "demo/asr",
  "sure_name": "demo_asr",
  "task": "ASR",
  "language": "en",
  "raw_root": "data/datasets/xforge_raw/demo_asr",
  "raw_jsonl": "samples.jsonl",
  "field_mapping": {
    "key": "id",
    "path": "audio",
    "target": "text"
  },
  "source": {
    "provider": "local",
    "id": "/absolute/path/to/raw/data"
  }
}
```

The raw JSONL records must contain real audio references and labels. The bridge
does not synthesize data.

Example raw record:

```json
{"id":"utt1","audio":"audio/sample.wav","text":"hello","language":"en"}
```

Run collection for local sources:

```bash
python scripts/xforge_collect_dataset.py \
  --manifest data/artifacts/xforge/demo_asr.dataset.json \
  --raw-root data/datasets/xforge_raw/demo_asr \
  --summary data/datasets/xforge_raw/demo_asr/collect_summary.json
```

Run conversion to OREF local dataset:

```bash
python scripts/xforge_process_to_oref.py \
  --manifest data/artifacts/xforge/demo_asr.dataset.json \
  --datasets-root data/datasets \
  --oref-config config/oref_datasets.yaml \
  --update-registry \
  --summary data/datasets/demo_asr/oref_summary.json
```

This writes `data/datasets/<dataset>/audio/` and
`data/datasets/<dataset>/sample.jsonl`. The `sample.jsonl` records preserve the
repository's existing OREF parser structure with `annotation` and `attribute`;
do not switch this path to the simplified doc example unless the parser is
explicitly changed.

Optional compatibility conversion to standalone SURE JSONL:

```bash
python scripts/xforge_process_to_sure.py \
  --manifest data/artifacts/xforge/demo_asr.dataset.json \
  --output data/datasets/xforge_sure/demo_asr.jsonl \
  --summary data/datasets/xforge_sure/demo_asr.summary.json
```

The generated SURE JSONL has this shape:

```json
{
  "key": "utt1",
  "path": "/absolute/path/to/audio/sample.wav",
  "target": "hello",
  "task": "ASR",
  "language": "en",
  "dataset": "demo_asr"
}
```

## Speech Model Flow

```text
daily ModelScope summary
  -> reports/xforge/modelscope/<date>/summary.md
human selection
  -> selected ModelScope model id
scripts/xforge_modelscope_fetch.py
  -> sure/models/<slug>/.runtime/modelscope_cache/
  -> sure/models/<slug>/checkpoints/  # optional explicit local weights only
  -> sure/models/<slug>/artifacts/weights_manifest.json
  -> sure/models/<slug>/artifacts/tool_agent_request.json
  -> sure/models/<slug>/model.spec.yaml + config/Docker/wrapper scaffold
SURE model tool-agent
  -> consume tool_agent_request.json
  -> write tool_agent_state.json + tool_agent_run_report.json
  -> stop at DOCKER_BUILD_CONFIRM
  -> ask the user before docker_build.sh / BUILD_ENV
```

## Agent Execution Checklist

Use this checklist when another agent is asked to continue the XForge ->
ModelScope -> SURE bridge work.

1. Read `AGENTS.md`, `xforge_sure_bridge/AGENTS.md`, and this playbook.
2. Use `.venv.hostbak` for XForge/ModelScope commands.
3. Remove proxy variables for ModelScope network commands:

   ```bash
   env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
     .venv.hostbak/bin/python scripts/xforge_daily_modelscope_summary.py ...
   ```

   ```bash
   env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
     .venv.hostbak/bin/python scripts/xforge_modelscope_fetch.py ...
   ```

4. Generate the weekly report:

   ```bash
   env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
     .venv.hostbak/bin/python scripts/xforge_daily_modelscope_summary.py \
     --tasks asr s2tt slu gr ser \
     --since-days 7 \
     --top-k 3 \
     --max-items 200 \
     --resource all \
     --date <YYYY-MM-DD> \
     --output-root reports/xforge/modelscope
   ```

5. Validate discovery output:

   - `summary.md`, `summary.json`, and `candidates.json` exist.
   - `summary.json` has `errors: []`.
   - If ModelScope returns `HTTP 502 Bad Gateway`, rerun the same no-proxy
     command before reporting no candidates.
   - Parse `summary.json` with singular keys:
     `tasks.<task>.model.recommended` and
     `tasks.<task>.dataset.recommended`.

6. Stop at `MODEL_DOWNLOAD_CONFIRM`. Ask the human which model and dataset to
   fetch. Do not auto-download top ranked resources.
7. For a selected model, run:

   ```bash
   env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
     .venv.hostbak/bin/python scripts/xforge_modelscope_fetch.py \
     --resource model \
     --task <task> \
     --id <org/model> \
     --name <display-name>
   ```

8. Validate model handoff:

   ```text
   sure/models/<slug>/checkpoints/
   sure/models/<slug>/.runtime/
   sure/models/<slug>/artifacts/weights_manifest.json
   sure/models/<slug>/artifacts/local_uv_env.json
   sure/models/<slug>/artifacts/tool_agent_request.json
   sure/models/<slug>/artifacts/tool_agent_state.json
   sure/models/<slug>/artifacts/tool_agent_run_report.json
   ```

   `tool_agent_state.json` must record:

   ```text
   current_state: DOCKER_BUILD_CONFIRM
   completed_states includes VALIDATE_SPEC, LOCAL_UV_BOOTSTRAP, FETCH_WEIGHTS
   ```

9. Stop at `DOCKER_BUILD_CONFIRM`. Ask the human before Docker build,
   `docker_validate.sh`, or `BUILD_ENV`.
10. For datasets, do not infer schema from names. Require raw data plus
    `field_mapping` for `key`, `path`, and `target` before SURE JSONL
    conversion.

### One-Command ModelScope Fetch and SURE Tool-Agent Handoff

For a human-selected ModelScope model, this is the canonical command:

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
  .venv.hostbak/bin/python scripts/xforge_modelscope_fetch.py \
  --resource model \
  --task asr \
  --id zhifeixie/Mega-ASR \
  --name Mega-ASR
```

Use `--no-download` only for metadata-only dry runs. A real ModelScope run
downloads the snapshot into `.runtime/modelscope_cache/` and refreshes generated
SURE config so `MODEL_PATH` points to
`weights_manifest.resolved_local_model_path`. It must not create a duplicate
checkpoint tree under `checkpoints/`.

Expected model-local outputs:

```text
sure/models/<slug>/
├── model.spec.yaml
├── config.yaml
├── pyproject.toml
├── requirements-local.txt
├── requirements-xforge.txt
├── local_uv_setup.sh
├── local_uv_validate.sh
├── Dockerfile
├── docker_build.sh
├── docker_validate.sh
├── model.py                 # generated scaffold, SURE tool-agent completes it
├── server.py                # generated MCP scaffold
├── validate.py              # generated static validation scaffold
├── checkpoints/             # optional explicit local weights; may be empty
├── .runtime/
│   └── modelscope_cache/    # ModelScope provider cache and remote weights
└── artifacts/
    ├── weights_manifest.json
    ├── xforge_collect_summary.json
    ├── xforge_sure_handoff.json
    ├── tool_agent_request.json
    ├── tool_agent_state.json
    ├── tool_agent_run_report.json
    ├── backend_choice.json
    ├── build_plan.json
    ├── preflight_summary.json
    ├── local_uv_env.json
    ├── local_uv_validation.json
    └── artifact_manifest.json
```

`tool_agent_request.json` is the direct input for the downstream SURE model
tool-agent. It must point to:

```text
target_agent: sure_model_tool_agent
target_agent_contract: docs/agents/model_tool_agent/AGENTS.md
requested_start_state: DOCKER_BUILD_CONFIRM
sure_next_state_after_confirmation: BUILD_ENV
requires_user_confirmation_before_build: true
```

It must also include paths to `model.spec.yaml`, `backend_choice.json`,
`build_plan.json`, `spec_validation.json`, `weights_manifest.json`, and the
original XForge manifest/handoff. If `weights_manifest.json` is present, the
SURE tool-agent may treat `FETCH_WEIGHTS` as already resolved by XForge, but it
still must validate that the path loads correctly.

After local uv validation, the fetch command calls
`xforge_sure_bridge.tool_agent_controller` to consume
`tool_agent_request.json`. The controller writes `tool_agent_state.json` and
`tool_agent_run_report.json`. With real downloaded weights it records
`completed_states: ["VALIDATE_SPEC", "LOCAL_UV_BOOTSTRAP", "FETCH_WEIGHTS"]`
and stops at `current_state: DOCKER_BUILD_CONFIRM`; with `--no-download` and no
`weights_manifest.json`, it records `current_state: FETCH_WEIGHTS` and requires a
real fetch before Docker confirmation.

The generated `model.py`, `server.py`, `validate.py`, `Dockerfile`, and shell
scripts are scaffolds, but the environment and Docker files must be executable
starting points:

- `requirements-xforge.txt` records initial model-local Python dependencies.
- `requirements-local.txt`, `local_uv_setup.sh`, and `local_uv_validate.sh`
  provide the model-local uv bootstrap for SURE tool-agent static validation.
- `local_uv_setup.sh` must set `UV_CACHE_DIR` under model-local `.runtime/`
  because home cache paths can be read-only on HPC/sandbox machines.
- If `python3.12` is unavailable, local uv bootstrap may fall back to
  `python3.11` for static handoff validation, but this must be recorded as an
  environment warning in `preflight_summary.json` and local uv logs.
- `Dockerfile` builds from the SURE repo root, installs SURE, and installs
  `requirements-xforge.txt` into a model-local venv.
- `docker_build.sh` uses a lowercase Docker image tag and writes
  `artifacts/build.log`.
- `docker_validate.sh` mounts `checkpoints/`, `.runtime/`, and `artifacts/`
  before running `validate.py`.

The bridge must not run `docker_build.sh` automatically. It stops at
`DOCKER_BUILD_CONFIRM` after generating Docker drafts, running/recording
model-local uv bootstrap, and writing static validation artifacts. The next
agent must ask the user before build. These files are not a claim that the model
is ready for evaluation. The SURE model tool-agent must inspect the downloaded
repository files and implement or revise model-specific load/infer logic.

Do not mark `evaluation_ready: true` from XForge. SURE readiness requires:

```text
model-specific wrapper implemented
Docker/runtime build succeeds
import/load/infer/contract validation passes
verdict.json records PASS
```

### Optional Lower-Level Model Manifest

Local source example:

```json
{
  "resource_type": "model",
  "model_name": "demo_asr_model",
  "task_type": "asr",
  "source": {
    "provider": "local",
    "id": "/absolute/path/to/downloaded/model-or-checkpoint"
  }
}
```

HuggingFace source example:

```json
{
  "resource_type": "model",
  "model_name": "whisper_large_v3_turbo",
  "task_type": "asr",
  "source": {
    "provider": "huggingface",
    "id": "openai/whisper-large-v3-turbo"
  }
}
```

ModelScope source example:

```json
{
  "resource_type": "model",
  "model_name": "some_modelscope_asr",
  "task_type": "asr",
  "source": {
    "provider": "modelscope",
    "id": "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
  }
}
```

If the source is not a ModelScope id or a lower-level manifest already exists,
`scripts/xforge_collect_model.py` can still materialize weights:

```bash
.venv.hostbak/bin/python scripts/xforge_collect_model.py \
  --manifest sure/models/demo_asr_model/artifacts/xforge_model_manifest.json \
  --model-dir sure/models/demo_asr_model \
  --summary sure/models/demo_asr_model/artifacts/xforge_collect_summary.json
```

The bridge writes:

```text
sure/models/<model>/
├── checkpoints/
├── .runtime/
└── artifacts/
    ├── weights_manifest.json
    └── xforge_collect_summary.json
```

`weights_manifest.json` records:

- original source provider and id
- `cache_policy: model_local_first`
- checkpoint root
- runtime root
- resolved local model path
- fallback information, if any

## SURE Agent Connection

For datasets, route these scripts before SURE prediction/evaluation scripts:

```text
scripts/xforge_collect_dataset.py
scripts/xforge_process_to_sure.py
scripts/prepare_sure_dataset.py
scripts/materialize_predictions_template.py
scripts/generate_predictions_via_server.py
scripts/validate_prediction_files.py
scripts/evaluate_predictions.py
```

For models, the canonical ModelScope chain writes
`artifacts/tool_agent_request.json`, then the downstream SURE tool-agent follows
`docs/agents/model_tool_agent/AGENTS.md`:

```text
DISCOVER
CLASSIFY
PLAN
VALIDATE_SPEC
LOCAL_UV_BOOTSTRAP # model-local .venv for tool-agent static validation
DOCKER_BUILD_CONFIRM # bridge pause; user confirms Docker/dependency/GPU plan
BUILD_ENV
FETCH_WEIGHTS        # resolved when weights_manifest.json already exists
VALIDATE_IMPORT
VALIDATE_LOAD
VALIDATE_INFER
VALIDATE_CONTRACT
GENERATE_WRAPPER
SAVE_ARTIFACTS
```

Concrete Mega-ASR sample output:

```text
sure/models/zhifeixie__Mega-ASR/.runtime/modelscope_cache/
sure/models/zhifeixie__Mega-ASR/artifacts/weights_manifest.json
sure/models/zhifeixie__Mega-ASR/artifacts/tool_agent_request.json
sure/models/zhifeixie__Mega-ASR/config.yaml
sure/models/zhifeixie__Mega-ASR/requirements-xforge.txt
sure/models/zhifeixie__Mega-ASR/Dockerfile
sure/models/zhifeixie__Mega-ASR/docker_build.sh
sure/models/zhifeixie__Mega-ASR/docker_validate.sh
```

Static scaffold validation:

```bash
SURE_XFORGE_STATIC_ONLY=1 \
  .venv.hostbak/bin/python sure/models/zhifeixie__Mega-ASR/validate.py
```

## Non-Goals

- Do not edit the upstream XForge skill set; this skill package is self-contained (use `/sure_feed` instead).
- Do not change SURE's existing agent contracts just to use this bridge.
- Do not invent synthetic datasets if discovery fails.
- Do not mark remote model ids as ready unless the bridge has materialized or
  recorded a deterministic local checkpoint path.
