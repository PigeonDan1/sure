# Docker Delivery Playbook

Every local `/sure_onboard` success is a registry-backed, digest-pinned container delivery. An upstream Dockerfile is input evidence, not a finished product: adapt it to the model wrapper, weights layout, server command, device requirements, and bounded fixture.

## Required flow

1. Write a model-specific Dockerfile and record its relative path and SHA-256.
2. Run `"$HARNESS_PYTHON_BIN" scripts/describe_harness_runtime.py`, add its exact `COPY --from=sure_harness_runtime` instruction to the Dockerfile, and pass its exact `--build-context` option to Docker.
3. Build one explicit target image tag.
4. Run Model Runtime import, load, inference, contract, and bounded-fixture checks inside that image.
5. Run Harness Runtime imports, dataset preparation, server orchestration, prediction, and prediction-validation checks through the image binding returned by the helper.
6. Record the sample output SHA-256 used by container validation.
7. Push that exact tag to the configured registry.
8. Resolve its registry digest and form `<repository>@sha256:<digest>`.
9. Pull and inspect that immutable reference.
10. Emit matching `docker_build_result.json`, `docker_validation.json`, and `docker_registry_result.json`.

The three documents must agree exactly on image tag, digest, immutable reference, Dockerfile hash, and sample hash. A passing boolean without those bindings is invalid.

## Runtime contract

`runtime_inventory.json` must expose:

- `execution_mode: container_only`;
- the digest-pinned image reference;
- an explicit server command and MCP tool names;
- model/NFS inputs mounted read-only;
- a separate writable result workspace;
- host fallback disabled.
- `model_runtime.python_executable` and `harness_runtime.python_executable` are distinct;
- `harness_runtime.runtime_id` and `lock_sha256` equal the active common Harness Runtime;
- the Harness Runtime manifest and imports are live-probed in the exact digest image.

The image must not depend on host absolute `.venv` paths. Model weights may be baked into the image or mounted from the approved model directory according to the declared policy, but the model mount stays read-only during Eval.

## Failure handling

- Docker daemon unavailable: keep the run partial/blocked; do not declare local success.
- Container checks differ from local checks: repair the Dockerfile or image dependencies and repeat the bounded container checks.
- Registry push or digest resolution fails: retry according to the normal retry policy; never substitute a mutable tag.
- Digest pull/inspect differs from the pushed image: fail packaging and investigate registry/tag mutation.
- GPU unavailable: apply the declared device policy and record the limitation; do not silently switch execution surfaces.
