# TTA Fixture Index

The TTA task consumes text captions and produces generated audio. The engine
manifest declares three input contracts:

- distribution metrics (FAD, FD, KL, IS via the audioldm_eval backend) require
  `prediction_audio` and `reference_audio` rows;
- `clap_score` requires `prediction_audio` plus an inline `caption` and scores
  text-audio alignment;
- AudioBox aesthetics (`audiobox_ce`, `audiobox_cu`, `audiobox_pc`,
  `audiobox_pq`) require `prediction_audio` only.

A model prediction is supplied as `prediction.audio_path` or
`prediction.prediction_audio`. The smoke fixture is metadata-only for
capability registration: it carries captions but no audio, so end-to-end audio
runs must supply generated predictions and, for the distribution metrics,
user-provided reference audio. The caption-only metrics (`clap_score` and the
AudioBox aesthetics family) work without reference audio. The canonical row
shape is:

```json
{"key":"tta_smoke_001","task":"TTA","language":"any","text":"A dog barking loudly in a park","caption":"A dog barking loudly in a park"}
```

Do not commit model checkpoints, generated audio, or private absolute paths
into this fixture.
