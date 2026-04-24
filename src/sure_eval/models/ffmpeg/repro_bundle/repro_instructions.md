# FFmpeg Repro Bundle Instructions

1. Read or copy this `repro_bundle/` into a fresh temporary working directory.
2. Verify the bundled executables directly:
   - `./bin/ffmpeg -version`
   - `./bin/ffprobe -version`
3. Recreate the minimal Python runtime only if you want wrapper-level smoke checks:
   - `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m venv .venv`
   - `.venv/bin/python --version`
4. Run the minimal reproduction from the bundle root:
   - `mkdir -p outputs`
   - `./bin/ffmpeg -y -i ./fixtures/en_16k_10s.wav -ac 1 -ar 16000 -c:a pcm_s16le ./outputs/out.wav`
   - `./bin/ffprobe -v error -show_format -show_streams -of json ./outputs/out.wav > ./outputs/probe.json`
5. Success criteria:
   - `outputs/out.wav` exists
   - ffmpeg exits with code 0
   - ffprobe emits valid JSON
   - at least one audio stream exists
   - sample_rate is `16000`
   - channels is `1`
   - codec_name is `pcm_s16le`
   - `format_name` contains `wav`
