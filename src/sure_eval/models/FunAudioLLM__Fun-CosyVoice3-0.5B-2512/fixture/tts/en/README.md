# Fun-CosyVoice3 TTS Fixture

This model-local fixture preserves the upstream zero-shot prompt audio required
by the official CosyVoice3 minimal callable path. The binary audio is copied
from:

```text
.runtime/source/CosyVoice/asset/zero_shot_prompt.wav
```

The metadata in `gt.jsonl` supplies the prompt text, target synthesis text, and
language used by `validate.py`.
