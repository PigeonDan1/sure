# Experience Asset Loss Register

This register tracks experience assets from the original
`docs/agents/model_tool_agent` that must stay active in the harness port.
It focuses on task, fixture, metric, environment, and failure-handling
knowledge. Product-boundary changes such as making Docker/registry/VC optional
are intentional harness decisions and are not treated as lost experience.

## P0 Restores

| Source | Active target | Lost experience | Restore mode | Gate impact |
| --- | --- | --- | --- | --- |
| `contracts/fixture_policy.md` | `references/contracts/fixture_policy.md` | SD/SA-ASR fixture outputs must be MeetEval-loadable annotations; SD should use RTTM for DER; SA-ASR should use STM/CTM/SegLST or equivalent speaker-attributed annotations instead of plain ASR text rows. | Restore with harness paths. | Affects `prepare_fixture`, `validate_contract`, task classification. |
| `task_playbooks/ASR.md` | `references/task_playbooks/ASR.md` | ASR metrics must use SURE `CERMetric`/`WERMetric`; do not reimplement edit distance or rerun inference only to repair metrics. | Restore as metric experience; local smoke remains contract-first. | Affects metric enrichment and bad-case routing. |
| `task_playbooks/KWS.md` | `references/task_playbooks/KWS.md` | KWS metric pipeline, DET semantics, report fields, and WekWS-compatible input modes were downgraded to task-local smoke only. | Restore as optional metric pipeline after smoke output exists. | Affects KWS validation/evaluation handoff. |
| `task_playbooks/SPEECH_UNDERSTANDING.md` | `references/task_playbooks/SPEECH_UNDERSTANDING.md` | SD/SA-ASR support, MeetEval route, S2TT metric inputs, multi-task metric report, and Kimi leading `!` token memory were removed or weakened. | Restore with harness paths and optional evaluation semantics. | Affects task classification, fixture selection, output contract, memory routing. |
| `task_playbooks/TTS.md` | `references/task_playbooks/TTS.md` | TTS metric namespace, `TTSSample`/`TTSMetricPipeline`, `sure-eval metric describe/run`, provider blocker handling, and no-rerun metric repair rules were downgraded. | Restore as metric enrichment after audio contract passes. | Affects TTS evaluation handoff and bad-case routing. |
| `task_playbooks/VC.md` | `references/task_playbooks/VC.md` | VC metric namespace, explicit converted/source/reference roles, shared TTS metric cache reuse, provider blocker handling, and no-rerun metric repair rules were downgraded. | Restore as metric enrichment after converted audio exists. | Affects VC evaluation handoff and bad-case routing. |
| `playbooks/env_uv.md` | `references/playbooks/env_uv.md` | S2TT `sacrebleu` repair, PyTorch CUDA wheel timeout repair, TTS uv pinning, uv cache isolation, `MPLCONFIGDIR`, and `pipefail` practices were removed. | Restore with model-local harness paths. | Affects `build_env`, `validate_env_compat`, retry/replan quality. |

## P1 Restores

| Source | Active target | Lost experience | Restore mode | Gate impact |
| --- | --- | --- | --- | --- |
| `contracts/minimal_validation.md` | `references/contracts/minimal_validation.md` | Docker or wrapper smoke must not treat `sample_output.json` existence as success; it must check explicit pass signals. | Restore as general smoke-validation warning, not a Docker requirement. | Affects wrapper scripts and package profiles. |
| `templates/validate.py` | `references/templates/validate_metric_enrichment.md` | Original template discovered subtask fixtures, wrote ref/hyp files, and called route-backed metric scripts with `report.json` and `pipeline_description.json`. | Preserve as design reference; do not overwrite harness runtime template. | Affects future metric enrichment helper. |
| `memory/*` | `references/memory/*` and context selection | Bad-case memories are present but must be routed by trigger rather than forgotten. | Add routing guidance and keep trigger list explicit. | Affects context selection and repair quality. |

## Not Restored As Default Gate

| Source experience | Harness decision |
| --- | --- |
| `deployment_type == local` must finish Docker, registry pull verification, and VC validation before final passed/tool_ready. | Harness default is `package=none` local-ready. Docker is selected by `package=docker-local` or `package=docker-registry`; VC/HPC remains an external deployment skill. |
| Company-specific image registry and VC command examples. | Keep only as historical examples or optional deployment notes; do not make them required for `/sure_onboard package=none`. |
