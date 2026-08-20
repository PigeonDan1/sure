# Bad Case: VC Partition Name Mismatch

## Trigger

- `vc submit` fails with:
  - `partition not found: <name>`
- The requested queue was described with a shorthand or human-facing alias.

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

Do not submit with shorthand aliases. Use the exact value reported under
`[Partition]`, for example:

```text
site-gpu-data
```

The shorthand value below failed:

```text
gpu-data
```

For minijob queues, do not assume the display queue name is submit-able.
Submitting directly to a display-only `site-gpu-minijob` may fail with:

```text
Not allowed submit to minijob queue
```

The accepted submission partition may be `site-gpu`, while `vc list` displays
the resulting job under `site-gpu-minijob`. Treat `vc info -u` as the source of
truth for submission names.

## Verification

After resubmission, run:

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy vc list
```

Confirm the new job appears on the intended exact partition.

## Example Artifacts

- `src/sure_eval/models_reonboard/runs/<model>/artifacts/vc_submission.json`
- `src/sure_eval/models_reonboard/runs/<model>/artifacts/vc_job.json`
