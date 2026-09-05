# Producer Contracts

## Onboard Docker v1

Require `artifacts/model_input_resolved.json`, `deployment_ready.schema=sure.onboard.deployment_ready.v1`, `package_profile=docker-registry`, terminal-success verdict, ready runtime inventory, passed package gate, pull-verified registry evidence, and exact agreement among target image, digest, and digest-pinned image reference.

## Onboard Python v2

Require `artifacts/model_input_resolved.json`, `deployment_ready.schema=sure.onboard.deployment_ready.v2`, `package_profile=none`, backend `uv`, a hash-locked dependency file, `model_runtime_manifest.json`, model-core hashes, and a matching content-addressed runtime under the active site's `storage.runtime_root`. The site must enable `local` and `python`. This binding is local-only and cannot be submitted to VC.

## Trans Docker v1

Require `artifacts/trans_input_resolved.json`, Docker v1 delivery evidence, `model_payload_manifest.json`, and passed original inference, adapter inference, contract, MCP, and equivalence results. Hash the published payload even when the producer manifest has no per-file hashes.

## Reject

Reject mixed producer evidence, partial or failed verdicts, `docker-local`, API-ready products, Python products without a sealed `uv` runtime, and trans products without successful equivalence. Point failures back to the detected producer and recommend rerunning `/sure_onboard` or `/sure_trans` with the same model inputs.
