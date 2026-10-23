# RIR Fixture Index

The RIR task consumes predicted and reference room impulse responses. The
fixture documents three evaluation branches:

- monaural RIRs use NPM, RT30, RT60, DRR, C50, C80, and EDC;
- binaural RIRs add IACC full/early/late, ITD, ILD, IPD, and DOA;
- RIRs with more than two channels add ITD, ILD, IPD, and DOA for every
  channel relative to channel 0, then average the pair errors.

Each fixture row must provide `reference_rir`; a model prediction is supplied
as `prediction.rir_path`, `prediction.prediction_rir`, or
`prediction.audio_path`. Set `channel_layout` to `mono`, `multi_channel`, or
`binaural_lr` when the layout is known. IACC requires exactly two channels;
ITD, ILD, IPD, and DOA accept two or more channels.

The smoke fixture is metadata-only for capability registration. End-to-end
audio tests create temporary deterministic WAV pairs so model checkpoints,
private paths, and generated audio never enter the repository. The canonical
row shape is:

```json
{"key":"rir_mono_001","reference_rir":"reference_mono.wav","sample_rate":16000,"channel_layout":"mono"}
{"key":"rir_binaural_001","reference_rir":"reference_binaural.wav","sample_rate":16000,"channel_layout":"binaural_lr","metadata":{"microphone_spacing_m":0.18}}
```

The model-to-branch examples are recorded in
`rir_smoke/model_examples.yaml`: Rec-RIR is the single-channel example and
NeuBIRE is the two-channel model example. Neither model checkpoint is part of
the fixture.

For the optional 20-row VariableMSpacingTest selection, run
`rir_smoke/prepare_variable_mspacing.py` with the dataset manifest and a
prediction root. Use `--branch single_channel` plus
`--reference-channel-root` for channel-0 scoring. Rec-RIR runs the mono model
independently on each original input channel. NeuBIRE single-channel runs use
`--input-mode duplicate_channel0` so both model inputs are copies of channel 0;
only output channel 0 is scored. To score a binaural estimate from Rec-RIR,
run the mono model independently on the left and right input channels and merge
the two output roots with
`rir_smoke/combine_single_channel_rirs.py`. NeuBIRE can write its two
channels directly to the multi-channel prediction root.
