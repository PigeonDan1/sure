# SURE Fixture Registry

This directory contains the shared, task-scoped smoke fixtures used by
`/sure_feed` and `/sure_onboard`.

The registry is intentionally separate from `sure/external/sure-evaluation`:
evaluation code can evolve through the evaluation submodule, while these small
fixtures remain a stable onboarding contract for model deployment smoke tests.

## Layout

```text
fixtures/tasks/
├── README.md
├── asr/
├── s2tt/
├── ser/
├── slu/
├── gr/
├── sd/
├── sa_asr/
├── speech_understanding/
├── tts/
├── vc/
└── kws/
```

Each task directory owns a `README.md` index. Feed/onboard agents must read the
matching task index before selecting fixture files.

## Harness Mapping

The source project used `src/sure_eval/models/<model>/fixture/<task>/` for
model-local copies. In this harness repo, onboarded model artifacts live under:

```text
sure/models/<model>/fixture/<task>/
```

The shared registry paths stay repo-relative, for example:

```text
fixtures/tasks/asr/qwen3_asr_smoke/asr_en/gt.jsonl
```

`MODEL_INPUT.fixture` should record the selected registry path, then
`/sure_onboard` copies the selected files into the model-local fixture folder.

## Policy

- Prefer `fixtures/tasks/<task>/README.md` and its representative fixture set.
- Select 2-3 samples for phase-1 validation; keep at most 5.
- Do not use arbitrary audio as a task fixture.
- Do not use one ASR sample as proof for every speech-understanding subtask.
- If a task has only an index and no concrete sample, report that gap rather
  than fabricating a fixture.
