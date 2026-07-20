# Bad Case: VC Partition Name Mismatch

## Trigger

- `vc submit` fails with:
  - `partition not found: <name>`
- The requested queue was described with a shorthand or human-facing alias, for
  example `3090-data`.

## Affected Step

- `BUILD_ENV` / remote GPU validation submission through `vc submit`.

## Minimum Evidence

1. Capture the failed `vc submit` command and exact error string.
2. Run:

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy vc info -u
```

3. Use the exact partition name shown under `[Partition]`.

## Known Mitigation

Do not submit with shorthand aliases. For the 3090 data queue observed on
2026-06-21, the valid `vc submit -p` value was:

```text
pdgpu-3090-data
```

The shorthand value below failed:

```text
3090-data
```

For minijob queues, do not assume the display queue name is submit-able. On
2026-06-21, submitting directly to `pdgpu-3090-minijob` failed with:

```text
Not allowed submit to minijob queue
```

The accepted submission partition was `pdgpu-3090`, and `vc list` then displayed
the resulting job under `pdgpu-3090-minijob`. Similarly, submitting with
`pdgpu-a10` could appear in `vc list` as `pdgpu-a10-minijob`.

## Verification

After resubmission, run:

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy vc list
```

Confirm the new job appears on the intended exact partition.

## Example Artifacts

- `src/sure_eval/models_reonboard/runs/asr_fireredasr/artifacts/vc_submission_3090_data.json`
- `src/sure_eval/models_reonboard/runs/asr_kimi_audio/artifacts/vc_job_3090_data.json`
