# ffmpeg repro instructions

1. Copy `repro_bundle/` into a fresh temporary working directory.
2. Confirm `bin/ffmpeg` and `bin/ffprobe` are executable.
3. Read `environment_manifest.json` and `minimal_repro_spec.json` for the fixed command path.
4. Run `./bin/ffmpeg -y -i ./fixtures/en_16k_10s.wav -ac 1 -ar 16000 -c:a pcm_s16le ./tmp/out.wav`.
5. Run `./bin/ffprobe -v error -show_format -show_streams -of json ./tmp/out.wav`.
6. Repro is successful when the output file exists and the probe JSON shows a mono 16kHz pcm_s16le wav stream.
