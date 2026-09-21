# LID Model Onboarding Playbook

LID is spoken language identification. The minimal SURE contract is a local
16 kHz mono PCM WAV path and a JSON-serializable language label:

```json
{"audio_path": "fixture/lid/sample.wav"}
{"language": "en", "label": "en"}
```

## FireRedLID reference model

The reference implementation is:

- ModelScope: `FireRedTeam/FireRedLID`
- Upstream runtime: `FireRedTeam/FireRedASR2S`, `fireredasr2s/fireredlid`
- MCP tool name: `identify_language`

The upstream CPU smoke surface is:

```python
from fireredasr2s.fireredlid import FireRedLid, FireRedLidConfig

model = FireRedLid.from_pretrained(
    "/path/to/FireRedLID",
    FireRedLidConfig(use_gpu=False, use_half=False),
)
results = model.process(["utt-1"], ["/path/to/audio.wav"])
# [{"uttid": "utt-1", "lang": "zh mandarin", "confidence": 1.0, ...}]
```

For GPU execution set `use_gpu=True` only after the selected runtime proves a
working CUDA driver. The harness must record the actual device and must not
silently fall back from CUDA to CPU.

Keep the model package and checkpoint files under `sure/models/<model>/` and
record the immutable ModelScope revision in `weights_manifest.json`. Never
commit the checkpoint or a provider cache. The wrapper must load weights lazily
and expose `predict({"audio_path": path})` (or an equivalent
`identify_language` method) without downloading audio implicitly.

The wrapper may return `language`, `lang`, or `label`; the harness normalizes all
three to the required `label` field and preserves `language` for evidence. Empty
or special-token labels are invalid. Do not put the language code in the ASR
`text` field.

The ModelScope snapshot currently exposes `cmvn.ark`, `dict.txt`, and
`model.pth.tar`. Keep the snapshot revision and the checkpoint SHA256 in the
model's `weights_manifest.json`; the known checkpoint digest is
`7dee2a280e9b11d5241a0e3d4fa60ee1520a036a2e8385f17960371cfea10093`.
The current SDK accepts the model's `master` snapshot selector; verify the
file digest after download because ModelScope file-level commit IDs are not
valid snapshot selectors. Use the provider's immutable download/cache policy
rather than copying weights into this repository.

Install Python packages from the official index; substitute your own local
mirror for `-i` if you have one:

```bash
python -m pip install -i https://pypi.org/simple <packages>
```

ModelScope download commands should use the model package's declared cache and
must record the resulting revision and file hashes. If GitHub source preparation
needs a proxy, configure `HTTPS_PROXY` for that setup command only.

## Fixture and evaluation

Use `fixtures/tasks/lid/README.md` and copy two or three representative samples
to `sure/models/<model>/fixture/lid/`. The ground truth uses canonical language
or dialect labels such as `en` and `zh-mandarin`.
Formal evaluation is label-only and never runs a reference LID model:

```text
lid.any.accuracy.lid_label_canonical_v1.classify_v1
```

The evaluator aligns `key<TAB>label` reference and hypothesis files, lowercases
labels, converts spaces/underscores/slashes to hyphens, and computes utterance
accuracy. Reuse the generated prediction bundle for metric-only repairs; do not
rerun model inference just to change label normalization.
