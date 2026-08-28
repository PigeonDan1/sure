---
name: sure-trans
description: Transform an existing Dockerfile, model path, and inference entrypoint into the digest-pinned container-only model bundle consumed by SURE Eval. Use when a model already has a delivery environment and inference code but does not yet implement the SURE ModelWrapper and MCP contracts.
---

# /sure_trans

Convert an existing model delivery into the same Eval-ready contract produced by `/sure_onboard`. Preserve the supplied source files, materialize a source image, add a generated adapter layer, validate original and adapted inference, push an immutable image, and seal `sure/models/<model_name>/`.

## Parameters

| Parameter | Required | Meaning |
| --- | --- | --- |
| `dockerfile` | yes | Existing Dockerfile absolute path. |
| `model` | yes | Existing model file or directory absolute path. |
| `inference_entrypoint` | yes | Existing inference entrypoint absolute path. `inference_code` is an alias. |
| `framework` | yes | `pytorch_transformers`; accept `pytorch`, `torch`, or `transformers` as aliases. |
| `build_context` | no | Default to the Dockerfile parent directory. |
| `source_image_policy` | no | `auto` (default), `load`, or `build`. `auto` tries a tar below `build_context`, then falls back to Dockerfile build. |
| `image_tar` | no | Explicit image archive absolute path. It must be inside `build_context`. |
| `model_name` | no | Default to the model path basename. |
| `task_type` | no | Infer from evidence; require an explicit value when ambiguous. |
| `fixture` | no | Absolute smoke input path. Otherwise select an unambiguous `examples/smoke.*` file from the build context. |
| `device` | no | `auto` (default), `cuda`, or `cpu`. `cpu` validates with local Docker only; `cuda` and GPU-capable `auto` submit VC jobs to the dedicated partition `<vc_default_partition>`. |
| `model_mount_target` | no | Default to `/models/<model_name>`. |
| `model_stage_policy` | no | `auto` (default), `copy`, or `hardlink`; materialize the model payload into the final bundle. |
| `vc_partition` | no | VC partition for GPU validation; default and site requirement `<vc_default_partition>`. |
| `vc_memory_gb` | no | VC memory request in GiB; default 32. `<vc_default_partition>` caps each GPU at 32 GiB, so do not exceed it there. |
| `vc_gpus` | no | VC GPU count; default 1. |
| `image_version` | no | Registry version tag for `<container_registry>/hpc/ai_asr-<model_name>:<version>`; default `0.1.0`. Bump on content change because the registry rejects tag reuse. |
| `max_retries` | no | Default 3. |

Example:

```text
/sure_trans dockerfile=/path/to/Dockerfile model=/path/to/model inference_entrypoint=/path/to/infer.py framework=pytorch_transformers task_type=asr
```

## Boundaries

- Treat `inference_entrypoint` as an entrypoint, not a complete dependency bundle.
- Treat the Docker build context, model path, declared support paths, and installed packages as the only allowed dependency roots.
- Do not scan filesystem roots or silently adopt same-named files from shared storage.
- Do not modify the supplied Dockerfile, model, or inference source in place.
- Keep model data outside the image, materialize it into `sure/models/<model_name>/`, and mount that approved bundle read-only.
- Treat MCP as the model invocation protocol. CPU validation runs in local Docker; GPU-touching validation submits VC jobs to `<vc_default_partition>`.
- Require the primary model to use PyTorch Transformers. Auxiliary preprocessing may use native binaries or ONNX Runtime when recorded as a support dependency.
- Attempt framework conversion only through a concrete deterministic converter. Compare original and converted inference; block when conversion is unavailable or non-equivalent.

## Workflow

Advance only after the current unit produces its declared artifact.

| # | Unit | Product |
| --- | --- | --- |
| 1 | `load_trans_input` | `trans_input_resolved.json` |
| 2 | `inspect_dependencies` | `inference_dependency_report.json` |
| 3 | `detect_framework` | `framework_detection.json` |
| 4 | `prepare_fixture` | `fixture_manifest.json` |
| 5 | `build_source_image` | `source_image_result.json` |
| 6 | `validate_env_compat` | `execution_compat.json` |
| 7 | `validate_original_inference` | `original_inference_result.json` |
| 8 | `stage_model_payload` | `model_payload_manifest.json` |
| 9 | `generate_adapter` | `adapter_manifest.json` |
| 10 | `build_adapter_image` | `adapter_image_result.json` |
| 11 | `validate_import` | `import_result.json` |
| 12 | `validate_load` | `load_result.json` |
| 13 | `validate_infer` | `infer_result.json` |
| 14 | `validate_contract` | `contract_result.json` |
| 15 | `validate_mcp` | `mcp_result.json` |
| 16 | `validate_equivalence` | `equivalence_result.json` |
| 17 | `package_container` | `docker_registry_result.json` |
| 18 | `write_runtime_inventory` | `runtime_inventory.json` |
| 19 | `verdict` | `verdict.json` |
| 20 | `finalize_model_bundle` | `deployment_ready.json` |

## Deterministic Scripts

Run harness scripts from this skill directory with `HARNESS_PYTHON_BIN`.

Resolve the inputs first:

```bash
"$HARNESS_PYTHON_BIN" scripts/materialize_trans_inputs.py \
  --dockerfile <absolute-Dockerfile> \
  --model <absolute-model-path> \
  --inference-entrypoint <absolute-inference-file> \
  --framework pytorch_transformers \
  --task-type <task> \
  --device <auto|cuda|cpu> \
  --vc-partition <partition> \
  --vc-memory-gb <gib> \
  --vc-gpus <count> \
  --image-version <version> \
  --run-dir <run_dir> \
  --repo-root <repo_root>
```

Forward every user-provided optional parameter from the slash command into this invocation. Omitted `--vc-*` and `--image-version` flags resolve to `<vc_default_partition>`, 32 GiB, 1 GPU, and `0.1.0`.

Inspect the static dependency closure:

```bash
"$HARNESS_PYTHON_BIN" scripts/inspect_dependencies.py --run-dir <run_dir>
"$HARNESS_PYTHON_BIN" scripts/detect_framework.py --run-dir <run_dir>
"$HARNESS_PYTHON_BIN" scripts/prepare_fixture.py --run-dir <run_dir>
```

Materialize the source image with the resolved policy:

```bash
"$HARNESS_PYTHON_BIN" scripts/run_docker_build.py \
  --run-dir <run_dir> \
  --produces <run_dir>/artifacts/source_image_result.json
```

With `source_image_policy=auto`, the runner recursively searches only below `build_context` for `.tar`, `.tar.gz`, or `.tgz` files. An explicit `image_tar` wins; otherwise candidates are ranked deterministically using in-context `delivery.json`, `SHA256SUMS`, and adjacent `image-inspect.json` evidence. Paths declared outside the current build context and symlinked archives are ignored.

The runner verifies any declared archive checksum, executes `docker load --input <tar>`, and confirms the loaded tag and live image ID with `docker image inspect`. If discovery, checksum, load, or inspection fails, `auto` executes `docker build --progress plain --file <Dockerfile> --tag <generated-tag> <build_context>`. `load` blocks instead of falling back; `build` skips archive discovery. Commands, logs, attempts, archive hash, Dockerfile hash, and the final live image identity are recorded in `source_image_result.json`.

Static analysis is evidence, not proof. Build the source image, create `execution_compat.json` with `status=pending`, and let the gate run `run_execution_compat.py`. It probes Python, Torch, Transformers, CUDA, and BF16 inside the source image.

Execution surfaces split by device:

- `device=cpu`: the probe runs in local Docker without `--gpus`; `execution_surface=local_docker`.
- `device=cuda` or GPU-capable `auto`: the gate pushes the source image to the registry as `<container_registry>/hpc/ai_asr-<model_name>-source:<version>` and submits the probe through `vc submit` on `<vc_default_partition>`; `execution_surface=vc` with `vc_partition`, `vc_job_id`, `vc_memory_gb`, `vc_gpus`, and `vc_submit_command` recorded.
- `auto` with a model that does not require CUDA falls back to a local CPU probe only after the VC CUDA probe fails or times out; the fallback evidence is recorded in `fallback` and `execution_surface` stays `vc`. When `vc` is unavailable or the partition is not permitted, the gate blocks with a clear repair instead of silently falling back.

For original and adapter smoke units, write the stage artifact with a real `run_command`. The original inference and adapter inference artifacts also need `input`, the staged fixture the command consumes (`staged_path` from `fixture_manifest.json`), and the MCP artifact needs `tool_name`, the tool the adapter exposes. The gate executes the command through `run_trans_validate.py`, captures stdout/stderr and exit status, and only then writes the matching pass field. A manually written `status=passed` is not sufficient. A required field the artifact omits blocks the unit and spends a retry before the command ever runs, so write them all in one go.

The four adapter stages share one validation directory. `validate.py` reads `SURE_VALIDATE_ARTIFACTS_DIR` for everything it writes and reads, and the contract stage reads back the `sample_output.json` the infer stage wrote there. Mount **one** host directory for all of `import`, `load`, `infer`, `contract` and point the variable at it:

```bash
-v <run_dir>/artifacts/adapter_validation:/validation:rw -e SURE_VALIDATE_ARTIFACTS_DIR=/validation
```

Giving each stage its own directory makes the contract stage fail with `Missing sample output` every time, however well inference went, and each attempt spends a gate retry.

When the model is validated on GPU, `run_command` must be a `docker run ...` list (with `-v`/`-e`/`--entrypoint`/`-w` flags); the gate translates it into a VC job with the same mounts, environment, and command. `--mount` and unknown flags are rejected. On `device=cpu` a plain list or shell string also works.

When `--entrypoint` is omitted, the translation resolves the image ENTRYPOINT/CMD from the local Docker daemon via `docker image inspect` and applies the same docker semantics (entrypoint + positional args, or entrypoint + image CMD when no args are given). An explicit `--entrypoint` always wins. If the image is not present locally, the gate blocks with a repair telling the agent to add `--entrypoint` explicitly or load the image.

After original inference passes, materialize the model payload into the final model bundle:

```bash
"$HARNESS_PYTHON_BIN" scripts/stage_model_payload.py --run-dir <run_dir>
```

`auto` attempts hardlinks and falls back to copies. The final approved model directory must contain the actual payload because `/sure_eval` mounts only that directory; an external absolute model path is not an executable handoff.

Scaffold the adapter after original inference passes:

```bash
"$HARNESS_PYTHON_BIN" scripts/scaffold_adapter.py --run-dir <run_dir>
```

Replace the generated `adapter/model.py` scaffold with a model-specific wrapper. Prefer direct Python import and persistent model loading. Reject a per-sample subprocess that reloads the model unless no persistent integration exists and the user explicitly accepts the limitation.

## Adapter Contract

Implement:

```python
class ModelWrapper:
    def load(self) -> None: ...
    def predict(self, input_data): ...
    def healthcheck(self) -> dict: ...
```

Keep `server.py` protocol-only. Use stdin/stdout JSON-RPC, write logs to stderr, and expose the task tool declared in `config.yaml`. For ASR, expose `transcribe_audio` with `audio_path` and return a JSON-serializable object containing non-empty `text`.

The adapter image always bakes `/opt/sure_trans/mcp_smoke.py` (copied by `scaffold_adapter.py`). All MCP protocol verification runs that deterministic driver: it spawns `server.py`, drives `initialize` / `tools/list` / `tools/call` / `shutdown` over stdin with bounded deadlines, and writes `mcp_smoke.json` evidence. Never write ad-hoc MCP test scripts, and never start the server bare without driving requests — a bare server waits on stdin forever. The MCP stdout channel must stay a pure JSON-RPC stream: the generated `server.py` redirects model-library stdout to stderr during `tools/call`, and `mcp_smoke.py` skips stray non-JSON stdout lines while reading responses (recording them as `stdout_junk_*` evidence) — model loading progress prints must never corrupt the protocol.

Equivalence is decided by the gate, not by the command. Write `equivalence_result.json` with `baseline_output` and `adapter_output` as the **paths** of the two recorded output files (the original inference output and the adapter's `sample_output.json`), never the transcript text itself. The gate opens both, reads the adapter `io_contract` primary field out of each (falling back to the whole file when it is not JSON), compares them under `comparison_policy` (`normalized_whitespace` by default, or `exact`), and records what it read as `comparison_evidence`. An exit code alone never proves equivalence: a `/bin/true` command once carried this gate to passed while neither file was opened.

## Image Packaging

1. Materialize the source image with `run_docker_build.py`; default `auto` loads an in-context image tar first and falls back to a deterministic Dockerfile build.
2. Use `adapter/Dockerfile.sure` to layer `/opt/sure_trans/model.py`, `server.py`, `config.yaml`, `model.spec.yaml`, `__init__.py`, `validate.py`, and `mcp_smoke.py` onto the source image.
3. Mount the staged `sure/models/<model_name>/` bundle read-only at `model_mount_target` for load, infer, MCP, and pull-verification tests.
4. Validate import, persistent load, real inference, output contract, MCP initialize/list/call, and equivalence with original inference as separate gates.
5. Push the adapter image, resolve `sha256:...`, pull the exact `repository@sha256:...` reference, and repeat the MCP smoke test. The deployment registry for this site is `<container_registry>` (registered as an insecure registry in the local Docker daemon, so plain-HTTP push/pull works; credentials live in `~/.docker/config.json`). Tag the image as `<container_registry>/hpc/ai_asr-<model_name>:<version>`; the registry enforces this naming spec server-side and rejects other names (`hpc` namespace, `ai_asr-` prefix, version tag). When the model was validated on GPU, the post-pull MCP smoke must itself run on VC through `mcp_smoke.py`; submit the **tag** with `--expect-digest` (see the VC section below — `vc submit` rejects digest-pinned references) and record its `vc_job_id`, `vc_partition=<vc_default_partition>`, `exit_code=0`, `image_ref`, the `resolved_digest` the submission proved, and the log path as `post_pull_smoke` in `docker_registry_result.json`, keeping `mcp_smoke.json` evidence next to that log path (the registry gate checks `resolved_digest` against `target_image_digest` and the initialize/tools/list/tools/call evidence).

The source image is pushed before unit 6 and the adapter image before unit 11 by the gate scripts; both record `registry_ref` and `registry_push` evidence into `source_image_result.json` and `adapter_image_result.json` respectively. The unit 17 post-pull smoke reuses the same registry name without repushing.

Naming, image boundary, tag increment, and push-failure recovery conventions live in `references/image_packaging.md`; on conflict, this section and the gates win.

## VC Execution

`<vc_default_partition>` and `<container_registry>` are site policy values, not constants: `execution.vc_default_partition` and `network.container_registry` in `config/site.bundled.yaml` (or `config/site.local.yaml`). Read them with `npm run sure:site-info`; never hardcode a site value in this skill.

GPU-touching work never runs `docker run --gpus all` on the login node. Gates submit to `<vc_default_partition>` through `scripts/vc_exec.py`; the same CLI drives the unit 17 post-pull MCP smoke:

```bash
"$HARNESS_PYTHON_BIN" scripts/vc_exec.py \
  --image <container_registry>/hpc/ai_asr-<model_name>:<version> \
  --expect-digest sha256:<digest> \
  --command "python /opt/sure_trans/mcp_smoke.py --audio /fixture/smoke.wav --tool <tool_name> --produces <run_dir>/artifacts/vc_logs/post_pull_smoke/mcp_smoke.json" \
  --mount <bundle_dir>:/models/<model_name>:ro \
  --mount <run_dir>/fixture:/fixture:ro \
  --partition <vc_default_partition> \
  --gpus 1 --memory-gb 48 --cpus 8 \
  --log-dir <run_dir>/artifacts/vc_logs/post_pull_smoke \
  --produces <run_dir>/artifacts/vc_logs/post_pull_smoke.json
```

- `vc submit` takes `repo:tag` only: it answers `镜像不存在` to every `repo@sha256:...` reference, however well that digest pulls with docker. Submit the tag and pass `--expect-digest`; `vc_exec.py` pulls the tag, reads back the manifest digest the registry serves for it, and refuses to submit when it is not the pinned one. It records `image_ref` and `resolved_digest`, which is what the registry gate checks against `target_image_digest`. Copy both into `docker_registry_result.json` under `post_pull_smoke`. Never hand a digest-pinned reference to `vc submit`, and never write `resolved_digest` by hand.
- Defaults: 1 GPU, 32 GiB, 8 CPUs, 1800 s poll timeout. `vc_memory_gb` and `vc_gpus` from the slash command override the memory/GPU defaults; the partition defaults to `<vc_default_partition>`.
- Every submitted job wraps its container command in `timeout --kill-after=15 <seconds>` (default 1200 s, `--command-timeout-seconds` on the CLI). A hung command is killed and still writes `exit_code` (124), so the submit host never waits for a file that will never appear; exit 124 surfaces a targeted repair.
- Never submit a raw `vc submit` and then hand-roll `sleep`/`while` polling loops in bash. Re-running a job always goes through `scripts/vc_exec.py`, which polls the `exit_code` file internally and records `vc info --job` / `vc logs` diagnostics.
- Mount preparation is deterministic: the gate creates missing bind-mount host sources as the submitting user before `vc submit` (the vc platform would otherwise create them as `nobody`, which the job uid cannot write); a missing `:ro` source blocks, and an existing unwritable directory blocks with a repair telling the agent to recreate the empty scratch dir or point the mount at a user-owned path. Job-side `Permission denied` on an output mount surfaces the same repair.
- `vc submit` requires the quota project; the gates pass `--project hpc` automatically (override with `--project` on the CLI).
- Job evidence lands under `artifacts/vc_logs/<stage>/`: `inner.sh`, `stdout.log`, `stderr.log`, `exit_code`, `vc_job.log`. Push logs live at `artifacts/vc_logs/source_push.log` and `adapter_push.log`.
- The submit host polls the `exit_code` file written by the in-job wrapper; `vc info --job` and `vc logs` output is diagnostic evidence only.
- Never submit real VC jobs outside this skill's gates or a `/sure_trans` run the user started.

Memory sizing is enforced deterministically:

- `<vc_default_partition>` caps 32 GiB RAM per GPU. Before submitting a model-loading
  validation (original inference, load, infer, contract, MCP, equivalence), the
  gate compares the payload size with 2x loading headroom against
  `vc_memory_gb` and blocks with the exact fix (`vc_gpus=2 vc_memory_gb=64`).
- When a job fails with exit 137 / `OOMKilled` / `std::bad_alloc` / `Killed`,
  the gate repairs with the RAM sizing fix; `CUDA out of memory` repairs with
  the VRAM guidance (reduce batch/beam, bf16, or shard).

## Eval Handoff

Generate `runtime_inventory.json` with schema `sure.onboard.runtime_inventory.v2`:

```bash
"$HARNESS_PYTHON_BIN" scripts/write_runtime_inventory.py --run-dir <run_dir> --python-executable <container-python> --tool-name <tool>
"$HARNESS_PYTHON_BIN" scripts/write_verdict.py --run-dir <run_dir>
```

Verify:

- `status=ready`
- `policy.eval_runtime=container_only`
- `policy.host_python_fallback=false`
- `policy.image_override_allowed=false`
- `container_runtime.target_image_ref` to a digest-pinned image
- `container_runtime.server_command` to the adapter MCP server
- `container_runtime.mount_policy.nfs_models_read_only=true`

Write a successful `verdict.json`, then run:

```bash
"$HARNESS_PYTHON_BIN" scripts/finalize_trans_bundle.py --run-dir <run_dir>
```

This seals the already-staged model payload, adapter, and small evidence under `sure/models/<model_name>/`. The sealed bundle matches the `/sure_onboard` product layout: wrapper set plus `Dockerfile.sure` at the bundle root, `fixture/<task>/` with `gt.jsonl`, and `artifacts/` carrying `package_gate.json` (`sure.onboard.package_gate.v2`), `artifact_manifest.json` (`sure.onboard.artifact_manifest.v1`), `runtime_inventory.json`, `verdict.json`, `docker_registry_result.json`, and `deployment_ready.json` (`sure.onboard.deployment_ready.v1`, written identically to the run directory). The terminal gate re-verifies hashes, bundle identity, portable paths, the Dockerfile hash, and the digest-pinned execution policy.

The generated `validate.py` keeps the same CLI contract as `/sure_onboard`: `--stage import|load|infer|contract|all`, writing `<stage>_result.json` and, during infer, `sample_output.json` into `SURE_VALIDATE_ARTIFACTS_DIR`, then validating that sample against the filled `io_contract` in the contract stage — from the same directory. The adapter image does not bake in a Harness Runtime; `runtime_inventory.harness_runtime.required=false`, so `/sure_eval` mounts the locked common Harness Runtime from the repository.

After completion, run evaluation locally or through VC without changing the model protocol:

```text
/sure_eval model=<model_name> execution=local
/sure_eval model=<model_name> execution=vc
```

## Stopping Without a Bundle

When one of the Failure Rules fires, the run stops where it is; it does not
finish successfully and it does not write a readiness marker by hand. Seal the
run as blocked instead, from wherever it stopped:

```bash
"$HARNESS_PYTHON_BIN" scripts/finalize_trans_bundle.py --run-dir <run_dir> \
  --blocked "<what stopped the run>"
```

That writes `artifacts/deployment_ready.json` with `status=blocked`, the reason,
hashes of whatever terminal evidence exists, and
`execution_policy.container_only=false`. Nothing is staged into
`sure/models/<model_name>/`. Then call `sure_finish` with `status=failed` or
`status=incomplete`; the pre-finish hook requires that marker and refuses a
non-success finish that still claims readiness.

A gate script may rerun and replace the artifact you wrote for its unit. When
that happens the advance message says so; re-read the file before acting on
what you recorded.

## Failure Rules

- Block on unresolved Docker `COPY`/`ADD` sources or undeclared external file paths.
- Block when original inference cannot load the supplied model.
- Block when framework conversion is required but not proven equivalent.
- Block when the adapter reloads a large model for every sample without explicit acceptance.
- Block when MCP output differs from original inference on the fixture: the equivalence gate compares the two recorded output files itself and fails on a mismatch even when the command exited 0.
- Block when the MCP gate has no `mcp_smoke.json` protocol evidence (initialize/tools/list/tools/call all passed with non-empty text); placeholder `run_command` values such as `/bin/true` or `print(...)` are rejected.
- Block when registry push, digest resolution, exact pull, or post-pull MCP validation fails.
- Block when `vc submit` fails, the partition is not permitted, the GPU probe cannot complete, or the post-pull smoke does not exit 0.
- Block when the model payload exceeds the RAM budget (2x headroom) of `vc_memory_gb`; raise `vc_gpus`/`vc_memory_gb` instead of trimming validation.
- Stop after `max_retries` changed-artifact failures; unchanged artifacts do not consume another retry.
