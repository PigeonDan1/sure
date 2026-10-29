# Native workflow calls

All examples use `python skills/sure/scripts/run.py` from the harness checkout;
an external caller can use the script's absolute path. Replace placeholders from
the selected native manual, not from guesses. `--trust` means the caller trusts
this checkout. JSON arguments are passed as a single native slash-command string.

```bash
# Native provider login must already exist; choose an actual available model.
python skills/sure/scripts/run.py init --arguments '--option codex --model <model-id>' --trust

# Source discovery, followed by the canonical handoff produced by feed.
python skills/sure/scripts/run.py feed --arguments '<model-url>' --trust
python skills/sure/scripts/run.py onboard --arguments 'model_input_path=<absolute-model-input.yaml>' --trust

# Adapt an existing implementation instead of onboarding it from scratch.
python skills/sure/scripts/run.py trans --args-file /absolute/path/to/transform-request.txt --trust

# Create the immutable review packet. Return it to the user before approval.
python skills/sure/scripts/run.py approve --arguments 'model_dir=<absolute-model-dir>' --trust
# Only after the user explicitly approves this concrete packet:
python skills/sure/scripts/run.py approve --arguments 'mode=approve review_manifest=<absolute-review-packet> decision=approve' --trust

# Inference and scoring are separate. Use the produced source run, not invented paths.
python skills/sure/scripts/run.py infer --arguments 'model=<approved-model> datasets=<source-path> execution=local' --trust
python skills/sure/scripts/run.py eval --arguments 'model=<approved-model> datasets=<dataset__version> source=<infer-run> metrics=<metric>' --trust

# The agent-chain contract is defined in sure/skills/sure_agent_eval/SKILL.md.
python skills/sure/scripts/run.py agent_eval --args-file /absolute/path/to/agent-request.txt --trust

# Resume only the exact run the harness reports as resumable.
python skills/sure/scripts/run.py resume --arguments '<run-id>' --trust
```

Use `--output-dir ABSOLUTE_PATH` for a new workflow's native `result.json` and
artifact mirror. The native site policy validates the path. Initialization and
resume instead use their native manifest/run locations; their invocation logs
and receipt still get a fresh directory under `.sure/agent-invocations/`.

The wrapper exits 0 only with current persisted success evidence. A process that
exits 0 without a result is reported as `incomplete`; an old success file is never
accepted for a new invocation. If the harness blocks on missing prerequisites,
auth, runtime setup or an approval gate, read the receipt and logs, report the
concrete blocker, and continue only within the user's authorization.
