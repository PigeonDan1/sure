# MetricGAN+ VoiceBank SE Wrapper

This example adapts [SpeechBrain MetricGAN+ VoiceBank](https://huggingface.co/speechbrain/metricgan-plus-voicebank)
to the SURE `ModelWrapper` and `enhance_speech` MCP contracts. It is a 16 kHz
single-channel speech-enhancement model. The first load downloads weights into
the model-local `.runtime/weights` directory; `SURE_SE_MODEL_CACHE` may point to
an existing local cache. Run it in an environment with PyTorch, Torchaudio,
SpeechBrain 1.0.3, HyperPyYAML, and Ruamel YAML installed.
The local smoke used Python 3.10, PyTorch 2.0.1, Torchaudio 2.0.2, and
Hugging Face Hub 0.15.1. `model.py` writes 16 kHz PCM16 WAV so the pinned
Evaluation Runtime can decode it. The Evaluation Runtime separately locks the
CPU dependencies for SI-SDR and STOI; the model environment is never used for scoring.

The wrapper accepts a noisy `audio_path` and an optional `output_path`, and
returns `{ "audio_path": "/path/to/enhanced.wav" }`. It never reads the clean
reference; that is only for scoring. Without `output_path`, it writes to
`$SURE_SE_OUTPUT_DIR/sure-se` or the system temporary directory's `sure-se`
subdirectory so inference does not need to write into the model bundle.
`server.py` exposes the same contract over
MCP stdio for approved Agent stages. Set `server.command[0]` to the model's
Python runtime when packaging; the example uses `python` as a placeholder.

This is an adapter example, not an approved model package. Run onboarding,
validation, and human approval before using it as an approved inference source.
On the two repository LibriSpeech-noise smoke samples, its SI-SDR was below
the noisy-input baseline; do not infer enhancement quality from a successful
MCP or evaluation run.

## Reproduce the development smoke

From the repository root, select a Python with the model dependencies and an
existing MetricGAN+ cache (`hyperparams.yaml` and `enhance_model.ckpt`):

```bash
HARNESS_PYTHON_BIN=$(python3 sure/runtime/harness/bootstrap.py)
"$HARNESS_PYTHON_BIN" sure/skills/sure_onboard/examples/metricgan_plus_voicebank/smoke.py \
  --model-python /absolute/model-runtime/bin/python \
  --model-cache /absolute/metricgan-weights \
  --output-dir /absolute/new-smoke-directory \
  --metric si_sdr --metric stoi
```

The output directory must be new. The script runs the actual Onboard and Trans
validation templates, the Agent MCP runner on both repository SE samples, and
the pinned evaluator on enhanced and noisy audio. It decodes generated WAVs,
saves commands/logs/scores, and verifies that Approve rejects the incomplete
development bundle. `summary.json` distinguishes this backend smoke from a
completed slash-command run. It does not manufacture an approval decision,
sealed Model Runtime, deployment readiness, or approved-model provenance.

For three additional utterances at 0/5/10 dB synthetic Gaussian noise, including
8 kHz mono and 16 kHz stereo inputs, first generate a new fixture directory:

```bash
/absolute/model-runtime/bin/python \
  sure/skills/sure_onboard/examples/metricgan_plus_voicebank/prepare_extra_smoke.py \
  --output-dir /absolute/new-extra-fixtures
```

Then run `smoke.py` with the same arguments above plus
`--fixture-dir /absolute/new-extra-fixtures` and a new output directory. It runs
Trans once per sample, since that template validates one input per invocation.
The generator uses three distinct, attributed LibriSpeech utterances already
in the repository and records sources, seeds, transforms, and hashes. Both
sets are development fixtures, not a VoiceBank/DEMAND quality benchmark.

Feed can now extract SpeechBrain's documented `from_hparams` / `enhance_batch`
entrypoints and narrow the broad `audio-to-audio` tag to SE:

```bash
"$HARNESS_PYTHON_BIN" sure/skills/sure_feed/scripts/sure_feed_online_discover.py \
  https://huggingface.co/speechbrain/metricgan-plus-voicebank \
  --run-dir /absolute/feed-run --handoff-root /absolute/handoffs
```

If Python reports a certificate verification error while the system client
works, set `SSL_CERT_FILE` to the site's trusted CA bundle for that invocation.
Do not disable TLS verification. Full deployment still requires packaging and
the normal Onboard/Trans → Approve → Infer/Eval lifecycle.
