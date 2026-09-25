# SepFormer WHAM16k SE wrapper

This development example adapts
[SpeechBrain SepFormer WHAM16k enhancement](https://huggingface.co/speechbrain/sepformer-wham16k-enhancement),
revision `90b3c5c3ffe3e04387b566715ab5fff36ec7b9d9`, to `enhance_speech`.
Although its upstream API is named `SepformerSeparation`, this checkpoint
produces one enhanced speech source. The wrapper rejects multi-source models.

Use the same model dependencies as the
[MetricGAN+ example](../metricgan_plus_voicebank/README.md): SpeechBrain 1.0.3,
PyTorch 2.0.1, Torchaudio 2.0.2, and Hugging Face Hub 0.15.1 were used locally.
`SURE_SE_MODEL_CACHE` contains `hyperparams.yaml`, `encoder.ckpt`, `decoder.ckpt`,
and `masknet.ckpt`; otherwise the wrapper downloads to `.runtime/weights`.
No weights or environments are tracked. The tested Python environment is a
development environment, not a sealed Model Runtime.

The wrapper accepts noisy `audio_path` and optional `output_path`. It downmixes
and resamples to 16 kHz, calls `separate_batch`, applies the upstream file API's
peak normalization with a silence guard, and writes mono PCM16 WAV. It returns
`audio_path` and never uses the clean reference as input. Without `output_path`,
generated files go to `$SURE_SE_OUTPUT_DIR/sure-se` or the temporary directory's
`sure-se` subdirectory. `config.yaml` uses a placeholder `python`; bind the
actual Model Runtime interpreter when packaging.

Run the shared development smoke with the locked Harness Python:

```bash
HARNESS_PYTHON_BIN=$(python3 sure/runtime/harness/bootstrap.py)
"$HARNESS_PYTHON_BIN" sure/skills/sure_onboard/examples/metricgan_plus_voicebank/smoke.py \
  --example-dir sure/skills/sure_onboard/examples/sepformer_wham16k \
  --model-python /absolute/model-runtime/bin/python \
  --model-cache /absolute/sepformer-weights \
  --output-dir /absolute/new-sepformer-smoke \
  --metric si_sdr --metric stoi
```

For the three additional utterances, use the fixture generator documented in
the MetricGAN+ example and add `--fixture-dir /absolute/extra-fixtures` with a
new output directory. The same samples, engine, and scoring runtime allow
comparison of the two models.

The smoke exercises Onboard and Trans templates, Infer MCP handling, Agent
inference, SE projection and scoring, and rejection of an incomplete approval
candidate. It does not complete sealing, positive approval, publication, or
the complete slash-command lifecycle. Synthetic smoke scores are not a WHAM!
benchmark result.
