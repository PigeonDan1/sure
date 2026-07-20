# VC Metric Cache Mismatch

## Trigger

Read this when VC metric evaluation uses
`scripts/run_vc_metric_pipeline_docker.py` but the output JSON has `ok: false`
with errors such as:

- `No such file or directory: 'dnsmos'`
- `EmergentTTS-Eval repo_dir is required for WV-MOS inference`
- `UTMOS-demo repo_dir is required for UTMOS inference`

This often happens after switching VC metrics to a new empty cache directory.

## Root Cause

VC metrics reuse the TTS metric provider stack: semantic ASR, speaker similarity,
DNSSMOS, WV-MOS, and UTMOS. The Docker runner can segment these providers across
known-good metric images, but the cache directory still needs provider resources.

If TTS metrics have already populated:

```text
/hpc_stor03/sjtu_home/junhao.du/.cache/sure-eval/tts-metrics
```

then VC should reuse that cache unless the task explicitly requires rebuilding
all provider resources.

## Required Fix

Do not rerun the VC model. Re-run only the metric wrapper against the existing
converted audio, using the populated TTS metric cache:

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
.venv.hostbak/bin/python scripts/run_vc_metric_pipeline_docker.py \
  --converted-audio <converted.wav> \
  --source-audio <source.wav> \
  --reference-audio <reference.wav> \
  --reference-text '<source text>' \
  --language zh \
  --sample-id <sample-id> \
  --gpu 0 \
  --device cuda:0 \
  --cache-dir /hpc_stor03/sjtu_home/junhao.du/.cache/sure-eval/tts-metrics \
  --work-dir <run_dir>/artifacts/vc_metric_parts_local_uv_tts_cache \
  --output <run_dir>/artifacts/vc_metric_report_local_uv_docker.json
```

## Verification

```bash
.venv.hostbak/bin/python - <<'PY'
import json
from pathlib import Path
p = Path("<run_dir>/artifacts/vc_metric_report_local_uv_docker.json")
d = json.loads(p.read_text())
assert d["ok"] is True, d.get("errors")
assert not d.get("errors"), d.get("errors")
print(sorted(d["metrics"]))
PY
```

## Affected Example

- `src/sure_eval/models_reonboard/runs/Plachtaa__seed-vc`
