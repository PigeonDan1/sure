# XForge-SURE Bridge Agent Instructions

**Version**: v1.0  
**Scope**: Use XForge resource discovery/collection outputs to prepare SURE datasets and speech model artifacts.  
**Role boundary**: This is the XForge -> SURE bridge guide. It is not the SURE
main-flow agent (`docs/agents/main_flow_agent/AGENTS.md`) and not the SURE model
tool-agent contract (`docs/agents/model_tool_agent/AGENTS.md`), though it hands selected
models to that tool-agent contract.
**Hard boundary**: Do not edit `XForge/skills/*` and do not rewrite existing SURE agent flow files.

---

## 1. Role

You are the bridge executor between XForge and SURE.

XForge is the upstream resource intelligence layer:

```text
discover -> collect -> process
```

SURE is the downstream validation and evaluation layer:

```text
dataset JSONL -> prediction generation -> validation -> evaluation
model checkpoints -> docs/agents/model_tool_agent/AGENTS.md onboarding flow
```

Your job is to create or consume resource manifests, call deterministic bridge
scripts, and leave machine-readable artifacts that SURE can consume.

---

## 2. Non-Negotiable Rules

- Do not edit files under `XForge/skills/`.
- Do not edit `docs/agents/model_tool_agent/AGENTS.md`.
- Do not edit `docs/agents/main_flow_agent/AGENTS.md`.
- Do not invent synthetic data when discovery fails.
- Do not mark a remote model ready unless a deterministic local path has been
  recorded in `weights_manifest.json`.
- Prefer model-local paths:

```text
src/sure_eval/models/<model>/.runtime/modelscope_cache/
src/sure_eval/models/<model>/checkpoints/   # optional explicit local weights
src/sure_eval/models/<model>/artifacts/
```

- For network downloads on AISpeech/HPC machines, prefer running inside the
  intended environment and record the exact command, source id, output path,
  and failure reason if any.

---

## 3. Dataset Bridge Flow

## 3.0 Daily ModelScope Watch

### 3.0.1 ModelScope Matching Contract

ModelScope discovery for both `model` and `dataset` resources must use a
strong-plus-weak matching strategy. Do not regress to only one side.

- Strong match first: query the ModelScope task-page semantics for the target
  task, for example ASR uses:
  - models: `tasks=auto-speech-recognition&type=audio`
  - datasets: `Tags=auto-speech-recognition&dataType=audio`
- Weak fallback second: also query task abbreviations and speech-domain terms
  such as `asr` and `speech`, then accept candidates whose metadata matches
  task keywords through custom tags, name, description, or tags.
- Short ambiguous abbreviations are not sufficient task evidence by themselves.
  For example, `gr` in a resource name does not imply gender recognition unless
  metadata also contains gender semantics or speech/audio-domain evidence.
  `microsoft/Dayhoff-3b-GR-HM` is a known false-positive example: it has
  `task:text-generation` and `custom_tag:protein-generation`, so it must be
  excluded from GR.
- Preserve the match provenance in every candidate:
  - `acquisition_filter.match_source: official_task`
  - `acquisition_filter.match_source: custom_tag_fallback`

This contract applies equally to models and datasets. Some valid ModelScope
speech datasets do not populate `tasks` or `task:auto-speech-recognition`.
Example: `zhifeixie/Voices-in-the-Wild-test-v2` has `tasks: []` but includes
`custom_tag:asr`, `custom_tag:speech`, and `custom_tag:audio`; it must be found
through `custom_tag_fallback` when it is inside the requested time window.

Daily ranking remains:

```text
task match -> requested time window -> report-date candidates first -> downloads
```

Do not invent candidates outside the requested time window. If a known fallback
candidate is missing, first check its `last_modified` timestamp against
`--since-days` before changing matching logic.

For daily human-review reports of task-relevant ModelScope resources, run:

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

The stable report location is:

```text
reports/xforge/modelscope/<YYYY-MM-DD>/summary.md
reports/xforge/modelscope/<YYYY-MM-DD>/summary.json
```

Operational notes:

- Online ModelScope discovery needs network access. Always remove proxy
  variables as shown above. In Codex/AISpeech sandboxed execution, a run may
  fail with `<urlopen error [Errno 1] Operation not permitted>`; rerun with the
  same no-proxy command under approved network execution. If ModelScope returns
  `HTTP Error 502: Bad Gateway`, treat it as transient and rerun before
  concluding that no candidates exist.
- Do not read `summary.json` as `tasks.<task>.models`. The schema is:

```text
tasks.<task>.<resource_type>.recommended
tasks.<task>.<resource_type>.other
```

For example, ASR model recommendations live at
`tasks.asr.model.recommended`, and additional ASR model candidates live at
`tasks.asr.model.other`.
- If the command uses `--resource model`, dataset sections are intentionally
  empty. Use `--resource all` or `--resource dataset` to validate datasets.

Humans select one or more candidate model ids from `summary.md`. Do not download
unselected candidates. Do not auto-download the Top 1 candidate unless the user
explicitly says to use Top 1 as a validation sample. The required pause after
daily discovery is:

```text
MODEL_DOWNLOAD_CONFIRM # ask the user which model id to download
```

For lower-level watcher artifacts, run:

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

If XForge `discover` already produced candidates, pass them instead of online
querying:

```bash
python scripts/xforge_watch_modelscope.py \
  --task ASR \
  --candidates-json data/artifacts/xforge/asr_candidates.json \
  --catalog data/artifacts/xforge/modelscope_catalog.json
```

The watcher writes:

```text
data/artifacts/xforge/modelscope_catalog.json
data/artifacts/xforge/modelscope/manifests/*.json
data/artifacts/xforge/modelscope/handoff/*.handoff.json
```

Read each handoff file:

- `target_agent: sure_tool_agent` means call `scripts/xforge_modelscope_fetch.py`
  for the selected model. That script downloads ModelScope weights under
  `.runtime/modelscope_cache/` when `--no-download` is not used and emits a
  model-local SURE handoff under
  `src/sure_eval/models/<slug>/artifacts/xforge_sure_handoff.json`. It also
  emits the SURE tool-agent request at
  `src/sure_eval/models/<slug>/artifacts/tool_agent_request.json`.
- `target_agent: sure_main_agent` means the dataset is a candidate for SURE
  evaluation. If `status` is `blocked_until_dataset_schema_mapping`, first add
  `raw_jsonl` and `field_mapping` to the dataset manifest.

## Required Execution Protocol for Future Agents

Do not rely on conversation history. Execute this protocol every time.

1. Use `.venv.hostbak`, not `.venv`, for XForge/ModelScope orchestration.
2. Remove proxy variables for every ModelScope network call:

   ```bash
   env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
     .venv.hostbak/bin/python scripts/xforge_daily_modelscope_summary.py ...
   ```

   ```bash
   env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
     .venv.hostbak/bin/python scripts/xforge_modelscope_fetch.py ...
   ```

3. Discovery command for the weekly multi-task report:

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

4. After discovery, verify:

   - `reports/xforge/modelscope/<date>/summary.md` exists.
   - `reports/xforge/modelscope/<date>/summary.json` has `errors: []`.
   - If `errors` contains `HTTP Error 502: Bad Gateway`, rerun the exact same
     no-proxy command before drawing conclusions.
   - Summary JSON uses singular schema keys:
     `tasks.<task>.model.recommended` and
     `tasks.<task>.dataset.recommended`.

5. Stop at `MODEL_DOWNLOAD_CONFIRM`. Do not download the top candidate
   automatically. Show the human the model/dataset candidates and ask which id
   to fetch.
6. For the selected model, run only the canonical fetch command:

   ```bash
   env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
     .venv.hostbak/bin/python scripts/xforge_modelscope_fetch.py \
     --resource model \
     --task <task> \
     --id <org/model> \
     --name <display-name>
   ```

7. After model fetch, verify:

   - `src/sure_eval/models/<slug>/artifacts/weights_manifest.json`
   - `src/sure_eval/models/<slug>/artifacts/local_uv_env.json`
   - `src/sure_eval/models/<slug>/artifacts/tool_agent_request.json`
   - `src/sure_eval/models/<slug>/artifacts/tool_agent_state.json`
   - `tool_agent_state.json` has
     `current_state: DOCKER_BUILD_CONFIRM` and
     `completed_states` containing `VALIDATE_SPEC`, `LOCAL_UV_BOOTSTRAP`, and
     `FETCH_WEIGHTS`.

8. Stop at `DOCKER_BUILD_CONFIRM`. Do not run `docker_build.sh`, `docker run`,
   or enter `BUILD_ENV` until the human confirms the Dockerfile, dependency
   list, image tag, and GPU/runtime plan.

9. For datasets, do not guess schemas. Dataset fetch/download and OREF/SURE
   conversion require explicit raw data location and field mapping
   (`key`, `path`, `target`) before evaluation. Preserve the existing
   DatasetManager OREF parser: generated `sample.jsonl` records must use the
   current `annotation`/`attribute` structure consumed by
   `DatasetManager._convert_oref_jsonl_to_jsonl`.

### 3.1 Required Input Manifest

Write a dataset manifest, usually under:

```text
data/artifacts/xforge/<sure_dataset_name>.dataset.json
```

Example:

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

`field_mapping` maps raw JSONL fields into SURE fields:

| SURE field | Meaning | Required |
|------------|---------|----------|
| `key` | unique utterance/sample id | yes |
| `path` | audio path, absolute or relative to `raw_root` | yes |
| `target` | ground-truth label/transcript | yes |

### 3.2 Collect Local Raw Data

If XForge has already downloaded data, use `provider: local` and run:

```bash
python scripts/xforge_collect_dataset.py \
  --manifest data/artifacts/xforge/demo_asr.dataset.json \
  --raw-root data/datasets/xforge_raw/demo_asr \
  --summary data/datasets/xforge_raw/demo_asr/collect_summary.json
```

Expected output:

```text
data/datasets/xforge_raw/demo_asr/
├── manifest.json
└── collect_summary.json
```

### 3.3 Convert to OREF Local Dataset

Preferred dataset handoff is an OREF local dataset under `data/datasets/` so
SURE-EVAL can discover it through `config/oref_datasets.yaml`.

Run:

```bash
python scripts/xforge_process_to_oref.py \
  --manifest data/artifacts/xforge/demo_asr.dataset.json \
  --datasets-root data/datasets \
  --oref-config config/oref_datasets.yaml \
  --update-registry \
  --summary data/datasets/demo_asr/oref_summary.json
```

Expected OREF local output:

```text
data/datasets/demo_asr/
├── audio/
│   └── sample.wav
├── sample.jsonl
└── oref_summary.json
```

The generated `sample.jsonl` intentionally follows the repository's existing
OREF parser shape:

```json
{
  "sample_id": "utt1",
  "attribute": {"path": "audio/sample.wav", "sample_rate": 16000, "duration": 0},
  "annotation": [{"transcription": {"text": ["hello"]}}],
  "task": "ASR",
  "dataset": "demo_asr"
}
```

Do not change `DatasetManager` parsing rules unless explicitly requested.

### 3.4 Convert to SURE JSONL Compatibility Output

Run:

```bash
python scripts/xforge_process_to_sure.py \
  --manifest data/artifacts/xforge/demo_asr.dataset.json \
  --output data/datasets/xforge_sure/demo_asr.jsonl \
  --summary data/datasets/xforge_sure/demo_asr.summary.json
```

Expected SURE JSONL record:

```json
{
  "key": "utt1",
  "path": "/absolute/path/to/audio.wav",
  "target": "hello",
  "task": "ASR",
  "language": "en",
  "dataset": "demo_asr"
}
```

### 3.5 Hand Off to SURE Evaluation

After conversion, the main SURE flow may use the generated JSONL as a dataset
input. Do not bypass SURE scoring scripts. The downstream route remains:

```text
scripts/materialize_predictions_template.py
scripts/generate_predictions_via_server.py
scripts/validate_prediction_files.py
scripts/evaluate_predictions.py
```

If the dataset must be registered permanently, create a separate change request
for SURE dataset registry updates. Do not silently modify registry/config files.

---

## 4. Speech Model Bridge Flow

### 4.1 Required Input Manifest

Write the model manifest under the target model artifacts directory:

```text
src/sure_eval/models/<model>/artifacts/xforge_model_manifest.json
```

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

HuggingFace example:

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

ModelScope example:

```json
{
  "resource_type": "model",
  "model_name": "paraformer_zh",
  "task_type": "asr",
  "source": {
    "provider": "modelscope",
    "id": "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
  }
}
```

### 4.2 One-Command ModelScope Fetch and SURE Tool-Agent Onboarding

For selected ModelScope models, use the single fetch entrypoint. This is the
one-click chain after human selection:

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
  .venv.hostbak/bin/python scripts/xforge_modelscope_fetch.py \
  --resource model \
  --task asr \
  --id zhifeixie/Mega-ASR \
  --name Mega-ASR
```

For a metadata-only dry run, add `--no-download`. The dry run still creates the
SURE onboarding skeleton but records `weights_manifest_status: missing`. A real
run without `--no-download` downloads the ModelScope snapshot into
`.runtime/modelscope_cache/` and records that path in `weights_manifest.json`.
Do not copy ModelScope snapshots into `checkpoints/`; that directory is only for
explicit local weights/checkpoints supplied outside the provider cache.

Expected model-local output:

```text
src/sure_eval/models/<slug>/
├── config.yaml
├── pyproject.toml
├── requirements-local.txt
├── requirements-xforge.txt
├── local_uv_setup.sh
├── local_uv_validate.sh
├── Dockerfile
├── docker_build.sh
├── docker_validate.sh
├── model.py                 # generated scaffold, tool-agent must complete
├── server.py                # generated MCP scaffold
├── validate.py              # generated static validation scaffold
├── __init__.py
├── model.spec.yaml
├── checkpoints/             # optional explicit local weights; may be empty
├── .runtime/
│   └── modelscope_cache/    # ModelScope provider cache and remote weights
└── artifacts/
    ├── xforge_sure_handoff.json
    ├── tool_agent_request.json
    ├── tool_agent_state.json          # after controller consumes request
    ├── tool_agent_run_report.json     # after controller consumes request
    ├── backend_choice.json
    ├── build_plan.json
    ├── preflight_summary.json
    ├── local_uv_env.json
    ├── local_uv_validation.json       # after local uv bootstrap succeeds
    ├── artifact_manifest.json
    ├── xforge_collect_summary.json
    └── weights_manifest.json        # only after checkpoint materialization
```

`xforge_sure_handoff.json` is the contract between the ModelScope download
agent and `docs/agents/model_tool_agent/AGENTS.md`. It must include:

- `source_agent: xforge_modelscope_fetch`
- `target_agent: sure_model_tool_agent`
- `target_agent_contract: docs/agents/model_tool_agent/AGENTS.md`
- `model_dir`, `checkpoint_dir`, `runtime_dir`, and
  `weights_manifest.resolved_local_model_path`
- `completed_states: ["DISCOVER", "CLASSIFY", "PLAN", "VALIDATE_SPEC"]`
- `next_state: DOCKER_BUILD_CONFIRM`
- `sure_next_state_after_confirmation: BUILD_ENV`
- `requires_user_confirmation_before_build: true`
- `xforge_completed_states: ["FETCH_WEIGHTS"]` only when
  `weights_manifest.json` exists

`tool_agent_request.json` is the structured task input for the downstream SURE
model tool-agent. It must include:

- `target_agent: sure_model_tool_agent`
- `target_agent_contract: docs/agents/model_tool_agent/AGENTS.md`
- `requested_start_state: DOCKER_BUILD_CONFIRM`
- `sure_next_state_after_confirmation: BUILD_ENV`
- `requires_user_confirmation_before_build: true`
- paths to `model.spec.yaml`, `backend_choice.json`, `build_plan.json`,
  `spec_validation.json`, `weights_manifest.json`, and the original XForge
  manifest/handoff
- required actions for `DOCKER_BUILD_CONFIRM`, `BUILD_ENV`, `FETCH_WEIGHTS`,
  `VALIDATE_IMPORT`, `VALIDATE_LOAD`, `VALIDATE_INFER`,
  `VALIDATE_CONTRACT`, and `SAVE_ARTIFACTS`

The fetch command must also call the deterministic controller in
`xforge_sure_bridge.tool_agent_controller` after local uv validation. This is
the programmatic AGENTS.md takeover boundary:

- It consumes `artifacts/tool_agent_request.json`.
- It writes `artifacts/tool_agent_state.json` and
  `artifacts/tool_agent_run_report.json`.
- If `weights_manifest.json`, `spec_validation.json`, and
  `local_uv_env.json` are ready, it records
  `completed_states: ["VALIDATE_SPEC", "LOCAL_UV_BOOTSTRAP", "FETCH_WEIGHTS"]`
  and `current_state: DOCKER_BUILD_CONFIRM`.
- If this was a `--no-download` dry run and `weights_manifest.json` is absent,
  it records `current_state: FETCH_WEIGHTS` and does not enter Docker
  confirmation.
- It never runs Docker by itself; `BUILD_ENV` remains blocked until the user
  confirms the Docker/dependency/image/GPU plan.

The generated `model.py`, `server.py`, `validate.py`, `Dockerfile`, and shell
scripts are scaffolds, but the environment and Docker files must be concrete
enough to run the SURE onboarding loop:

- `requirements-xforge.txt` records the initial model-local Python dependency
  set.
- `requirements-local.txt`, `local_uv_setup.sh`, and `local_uv_validate.sh`
  provide the model-local uv bootstrap used by the SURE tool-agent before
  Docker build confirmation.
- `local_uv_setup.sh` must set `UV_CACHE_DIR` under model-local `.runtime/`
  rather than writing to `~/.cache/uv`, because HPC/sandbox home cache paths may
  be read-only.
- If `python3.12` is not available, the local uv bootstrap may fall back to
  `python3.11` for static tool-agent validation, but this must be recorded in
  `preflight_summary.json` and local uv logs as an environment warning.
- `Dockerfile` builds from the SURE repo root, installs root requirements,
  installs SURE with `pip install --no-deps -e .`, then installs
  `requirements-xforge.txt` into the model-local venv.
- `docker_build.sh` must use a lowercase Docker image tag and write
  `artifacts/build.log`.
- `docker_validate.sh` must mount model-local `checkpoints/`, `.runtime/`, and
  `artifacts/`, then run `validate.py` inside the container.

The bridge must not run `docker_build.sh` automatically. It stops at
`DOCKER_BUILD_CONFIRM` after generating Docker drafts, running/recording
model-local uv bootstrap, and writing static validation artifacts. The next
agent must ask the user before build. These files are still not a claim that the
model is ready for evaluation. The
SURE model tool-agent must inspect the downloaded repository files and
implement/revise model-specific load/infer logic.

Do not mark `evaluation_ready: true` from XForge. SURE readiness requires:

```text
model-specific wrapper implemented
Docker/runtime build succeeds
import/load/infer/contract validation passes
verdict.json records PASS
```

### 4.2.1 Concrete Mega-ASR Example

The end-to-end selected-model command used for the current ASR sample is:

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
  -u ALL_PROXY -u all_proxy \
  .venv.hostbak/bin/python scripts/xforge_modelscope_fetch.py \
  --resource model \
  --task asr \
  --id zhifeixie/Mega-ASR \
  --name Mega-ASR
```

Expected critical outputs:

```text
src/sure_eval/models/zhifeixie__Mega-ASR/.runtime/modelscope_cache/
src/sure_eval/models/zhifeixie__Mega-ASR/artifacts/weights_manifest.json
src/sure_eval/models/zhifeixie__Mega-ASR/artifacts/tool_agent_request.json
src/sure_eval/models/zhifeixie__Mega-ASR/config.yaml
src/sure_eval/models/zhifeixie__Mega-ASR/requirements-xforge.txt
src/sure_eval/models/zhifeixie__Mega-ASR/Dockerfile
src/sure_eval/models/zhifeixie__Mega-ASR/docker_build.sh
src/sure_eval/models/zhifeixie__Mega-ASR/docker_validate.sh
```

After the command finishes, `weights_manifest.json` must point
`resolved_local_model_path` to the actual ModelScope cache path under
`.runtime/modelscope_cache/`, and `config.yaml` must point `MODEL_PATH` to that
resolved local path, not the remote ModelScope id and not a duplicate
`checkpoints/` copy.

The static onboarding check is:

```bash
SURE_XFORGE_STATIC_ONLY=1 \
  .venv.hostbak/bin/python src/sure_eval/models/zhifeixie__Mega-ASR/validate.py
```

### 4.3 Collect and Materialize Local Model-Local Weights

Run:

```bash
.venv.hostbak/bin/python scripts/xforge_collect_model.py \
  --manifest src/sure_eval/models/<model>/artifacts/xforge_model_manifest.json \
  --model-dir src/sure_eval/models/<model> \
  --summary src/sure_eval/models/<model>/artifacts/xforge_collect_summary.json
```

Expected output:

```text
src/sure_eval/models/<model>/
├── checkpoints/
├── .runtime/
└── artifacts/
    ├── weights_manifest.json
    ├── xforge_model_manifest.json
    └── xforge_collect_summary.json
```

`weights_manifest.json` is the contract with SURE `FETCH_WEIGHTS`.

### 4.4 Hand Off to SURE Tool Onboarding

After `xforge_sure_handoff.json` exists, continue with the SURE model
onboarding state machine in `docs/agents/model_tool_agent/AGENTS.md`, but ask the user
before entering `BUILD_ENV`:

```text
DISCOVER
CLASSIFY
PLAN
VALIDATE_SPEC
LOCAL_UV_BOOTSTRAP # model-local .venv for tool-agent static validation
DOCKER_BUILD_CONFIRM # bridge pause; user confirms Docker/dependency/GPU plan
BUILD_ENV
FETCH_WEIGHTS        # may already be completed by XForge weights_manifest.json
VALIDATE_IMPORT
VALIDATE_LOAD
VALIDATE_INFER
VALIDATE_CONTRACT
GENERATE_WRAPPER
SAVE_ARTIFACTS
```

The bridge-generated wrapper files are only a starting scaffold. Wrapper
completion remains a SURE model onboarding responsibility.

---

## 5. What to Ask Kimi-Code to Do

Use this prompt shape when delegating to a code model:

```text
Read xforge_sure_bridge/AGENTS.md and docs/agents/model_tool_agent/playbooks/xforge_sure_bridge.md.
Do not modify XForge/skills or existing SURE agent flow files.

Task:
1. Given <dataset/model source>, create the correct XForge bridge manifest.
2. Run the matching bridge script.
3. Verify generated artifacts exist.
4. For datasets, show the produced SURE JSONL path and sample count.
5. For models, show tool_agent_request.json, weights_manifest.json, and the
   resolved local model path.
6. For models, stop at DOCKER_BUILD_CONFIRM and ask the user before running
   docker_build.sh or entering BUILD_ENV.
```

For dataset-only work:

```text
Use scripts/xforge_collect_dataset.py and scripts/xforge_process_to_sure.py.
Return collect_summary.json, processed summary, and the SURE JSONL path.
```

For daily ModelScope watch work:

```text
Use .venv.hostbak and clear proxy variables:
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
  .venv.hostbak/bin/python scripts/xforge_daily_modelscope_summary.py \
  --tasks asr s2tt slu gr ser --since-days 7 --top-k 3 --max-items 200 --resource all \
  --date <YYYY-MM-DD> --output-root reports/xforge/modelscope
Verify summary.json uses tasks.<task>.model.recommended and tasks.<task>.dataset.recommended.
If errors contains HTTP 502, rerun the exact same no-proxy command.
Ask the human to choose model ids from reports/xforge/modelscope/<date>/summary.md.
For selected model ids, call the no-proxy .venv.hostbak scripts/xforge_modelscope_fetch.py command.
For dataset handoffs, only process datasets whose manifest has raw_jsonl and field_mapping.
Do not guess unknown dataset schemas.
Stop at MODEL_DOWNLOAD_CONFIRM before fetching and at DOCKER_BUILD_CONFIRM after model fetch.
```

For model-only work:

```text
Use scripts/xforge_modelscope_fetch.py for ModelScope sources.
Return xforge_sure_handoff.json, tool_agent_request.json, model.spec.yaml, build_plan.json, config.yaml, weights_manifest.json if present, and the resolved checkpoint path if downloaded.
Then hand off to docs/agents/model_tool_agent/AGENTS.md from DOCKER_BUILD_CONFIRM.
Ask the user before docker_build.sh.
```

---

## 6. Verification Commands

Run these after bridge changes:

```bash
.venv.hostbak/bin/python -m unittest tests/test_xforge_sure_bridge.py
.venv.hostbak/bin/python scripts/xforge_collect_dataset.py --help
.venv.hostbak/bin/python scripts/xforge_process_to_sure.py --help
.venv.hostbak/bin/python scripts/xforge_collect_model.py --help
.venv.hostbak/bin/python scripts/xforge_watch_modelscope.py --help
.venv.hostbak/bin/python scripts/xforge_modelscope_fetch.py --help
```

If the project venv path is different, use any Python `>=3.10` environment with
the project root on `PYTHONPATH`.
