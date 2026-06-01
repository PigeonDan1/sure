# ffmpeg repro bundle

This directory contains the minimal reproduction bundle for the SURE-EVAL `ffmpeg` utility integration.

## Files

- `repro_manifest.json`  
  Top-level bundle entry. Summarizes model info, repro status, backend, tested commit, and file paths.

- `environment_manifest.json`  
  Records the validated environment, backend choice, runtime assumptions, and executable discovery policy.

- `environment_snapshot.txt`  
  Snapshot of the actual validated environment, including executable paths and version outputs.

- `weights_manifest.json`  
  Records weight requirements. For ffmpeg, this is not applicable.

- `minimal_repro_spec.json`  
  Defines the minimal reproduction path, exact commands, required inputs, expected outputs, and contract checks.

- `fixture_manifest.json`  
  Describes the bundled fixture, its role, checksum, and assumptions.

- `repro_instructions.md`  
  Human-readable reproduction steps.

- `sample_output.json`  
  Example successful output from a validated run.

- `repro_validation.log`  
  Stage-by-stage repro replay log.

- `repro_verdict.json`  
  Final structured repro result.

- `bin/`  
  Bundled executables used for reproduction:
  - `ffmpeg`
  - `ffprobe`

- `fixtures/`  
  Bundled input fixture used for minimal reproduction:
  - `en_16k_10s.wav`

## Required inputs

The minimal repro depends on:

- `bin/ffmpeg`
- `bin/ffprobe`
- `fixtures/en_16k_10s.wav`

## Success condition

Repro succeeds when the bundled ffmpeg command runs successfully, produces an output WAV file, and ffprobe confirms the output matches the expected contract.
