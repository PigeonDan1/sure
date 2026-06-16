# WekWS KWS Tool-Agent Handoff

This directory is the SURE model onboarding artifact for
`daydream-factory/keyword-spot-fsmn-ctc-wenwen`.

Execution flow:

1. Download agent materializes the ModelScope snapshot under
   `.runtime/modelscope_cache/daydream-factory/keyword-spot-fsmn-ctc-wenwen`.
2. Download agent materializes the WekWS source tree under
   `.runtime/source/wekws`.
3. SURE model tool-agent owns everything after download:
   `local_uv_setup.sh` creates the local uv environment, `model.py` wraps
   WekWS streaming inference, and `validate.py` writes validation artifacts.

The local `checkpoints/` directory intentionally stays empty. The authoritative
checkpoint location is the model-local ModelScope cache.

The current fixture is a negative smoke sample copied from an existing Chinese
speech fixture. It validates load, feature extraction, decoding, and output
shape. Replace or add a positive wake-word sample for activation accuracy tests.

Fixture update:

- Positive and negative KWS fixture samples are now extracted from
  `daydream-factory/mobvoi_hotword_dataset`.
- The full audio archive is about 17.9 GB, so the download agent must not fetch
  it wholesale for smoke tests. Use the small resources tgz for metadata and
  range/stream extraction for selected WAV members.
- In this workspace `data/datasets` is a read-only symlink, so the OREF smoke
  dataset is materialized under
  `data/datasets_old/mobvoi_hotword_kws_wenwen` with the same `audio/` +
  `sample.jsonl` structure.

Known upstream compatibility patch:

- The current WekWS `stream_kws_ctc.py` imports `query_token_set`,
  `read_lexicon`, and `read_token` from `tools.make_list`, but the current
  GitHub `tools/make_list.py` no longer defines them. This model-local WekWS
  copy restores those three helper functions using the shipped
  `tokens.txt`/`lexicon.txt` format.
- The FSMN-CTC recipe trains from a dict generated with `token_id - 1`
  (`run_fsmn_ctc.sh` stage 0). The ModelScope package ships the original
  `tokens.txt`, so the model-local `read_token` helper applies the same
  `-1` shift. Without this, keyword tokens are off by one and official positive
  samples will be rejected.
