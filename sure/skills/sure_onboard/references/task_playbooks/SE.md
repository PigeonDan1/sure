# SE Model Onboarding Playbook

This is the speech-enhancement supplement to the model onboarding rules in
`../AGENTS.md`. It applies to noisy-speech-to-enhanced-speech models, including
MetricGAN+ VoiceBank. Read the shared SE fixture index at
`fixtures/tasks/se/README.md` before selecting a smoke input.

## Task Contract

Use `task_type: se`. The public MCP tool is `enhance_speech`. Its required
argument is `audio_path` (the noisy input); `noisy_audio_path` is an accepted
alias for inference compatibility. Accept optional `output_path` so the caller
can keep predictions in its own product directory. Return the generated file
as `audio_path`, not samples embedded in JSON.

The wrapper must check that the input is readable and that the returned audio
exists, is nonempty, and decodes. If the model only accepts a particular sample
rate or channel count, resample/downmix inside the wrapper and document the
output format. Keep runtime downloads and checkpoints in the model-local
ignored runtime or approved model roots, never in a tracked artifact.

## Fixture And Evaluation

An SE fixture has a noisy audio input and a clean `reference_audio` for
evaluation. `reference_audio` belongs in the fixture annotation; it is not a
model input and must not be passed to `enhance_speech`. Stage it alongside the
noisy input and preserve its SHA-256 in the fixture manifest.

Smoke validation proves loading, inference, and the file-output contract. It
does not gate on a quality threshold. Formal SE evaluation should pair the
generated audio with the clean reference and use the registered SE metrics
(for example SI-SDR and STOI). Both paths should refer to the same utterance;
sample-rate and length normalization belong to the evaluation pipeline.

The validation templates assign a distinct `output_path` below
`artifacts/outputs` and check that the wrapper returns that non-empty file.
Do not substitute `reference_audio` when the noisy input is absent. The
MetricGAN+ example's `smoke.py` exercises both producer templates, MCP inference,
and locked SI-SDR/STOI scoring with a noisy-input baseline; see
`../../examples/metricgan_plus_voicebank/README.md` for the command and its
development-only scope.

## Checklist

- Record the selected playbook in the onboarding audit artifact.
- Run a real noisy fixture through the model and decode the enhanced output.
- Verify `enhance_speech(audio_path, output_path)` writes exactly the requested
  output path when `output_path` is supplied.
- Keep the clean reference out of model inputs and include it only in fixture
  annotations and evaluation rows.
- Validate a candidate and create an approval packet before publishing; do not
  treat a successful smoke or an evaluation score as human approval.
