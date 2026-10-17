# RIR onboarding and evaluation

RIR model adapters expose an `estimate_rir` tool. The input contains an
`audio_path` and the output must include a newly generated `rir_path` and
`sample_rate`; the clean/reference RIR is never passed to the model. The
evaluation bridge writes one `samples_jsonl` row per sample:

```json
{
  "sample_id": "rir_001",
  "prediction_rir": "predicted.wav",
  "reference_rir": "reference.wav",
  "sample_rate": 16000,
  "channel_layout": "mono"
}
```

Use the single-channel chain for Rec-RIR outputs. It validates one channel and
supports `rir_nmse`, `rir_corr`, `rt60_mae`, `rir_drr_mae`, `rir_c50_mae`, and
`rir_c80_mae`. Use the multi-channel chain for NeuBIRE-MINT outputs. Its
channel-wise metrics accept two or more channels, while IACC, ITD, ILD, and IPD
require exactly two channels labelled `binaural_lr`. Add
`metadata.microphone_spacing_m` only when `rir_doa_mae` is selected.

When a single-channel model is used on a binaural input, invoke it separately
for channel 0 and channel 1, then merge the two mono `rir_path` files as L/R
before selecting spatial metrics. The single-channel smoke score itself uses
channel 0 only and must remain a mono row. NeuBIRE's single-channel adapter is
the exception to the model input shape: duplicate channel 0 into both input
channels before inference, then score output channel 0 only.

The model adapter may use a model-native tool internally, but it must map the
generated RIR to `prediction.rir_path` (or `prediction.prediction_rir`) before
the bridge runs. Do not include model weights, generated RIRs, or private
absolute paths in a fixture or approval artifact.

The bounded examples are listed in
`fixtures/tasks/rir/rir_smoke/model_examples.yaml`; the fixture audio used by
tests is generated in a temporary directory.
