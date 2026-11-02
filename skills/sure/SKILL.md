---
name: sure
description: One-command setup and guarded end-to-end SURE model workflow.
---

# SURE

This is the complete operating manual for the external SURE bridge. The bridge
is a thin client over the native harness: every workflow keeps native hooks,
locks, approval gates, correlated evidence, and process cancellation.

## One-time bootstrap

Run this block from the checkout root (replace the clone URL if your fork is
already available):

```bash
git clone <SURE_REPOSITORY_URL> sure && cd sure
git submodule update --init --recursive
node --version && uv --version          # Node >=22.19; uv is required
./skills/sure/scripts/bootstrap.sh      # npm ci, doctor, discovery, symlink
```

To configure and validate a provider/model in the same command, set the native
`/sure_init` arguments before bootstrap. Credentials remain in the normal
environment or OAuth store:

```bash
SURE_INIT_ARGUMENTS='--option openai --model gpt-5.5' ./skills/sure/scripts/bootstrap.sh
```

Without `SURE_INIT_ARGUMENTS`, bootstrap checks discovery and leaves provider
initialization for `run.py init`.

The agent can perform cloning, submodule setup, dependency installation,
doctor, symlink creation, and runtime download. A human must complete OAuth
browser login when a provider requires it and provision credentials or secret
management access. Never put a key in arguments or prompt files.

The symlink is supported on Unix. Windows needs a developer-mode or
administrator-created directory junction; this limitation is currently
unverified by CI.

## Credentials

The bridge rejects `--api-key` by design. Use the native environment path below
(or native login, which writes the provider's OAuth token to the pi auth store):

| Provider(s) | Exact environment variable(s) | One-time human action | Persistent OAuth storage |
| --- | --- | --- | --- |
| OpenAI, Azure OpenAI | `OPENAI_API_KEY`, `AZURE_OPENAI_API_KEY` | provision key, or `/login` for OAuth | `~/.pi/agent/auth.json` |
| Anthropic | `ANTHROPIC_API_KEY`, `ANTHROPIC_OAUTH_TOKEN`, `ANTHROPIC_AUTH_TOKEN` | provision key, or `/login anthropic` | `~/.pi/agent/auth.json` |
| OpenRouter, xAI, Groq, NVIDIA, DeepSeek, Cerebras | `OPENROUTER_API_KEY`, `XAI_API_KEY`, `GROQ_API_KEY`, `NVIDIA_API_KEY`, `DEEPSEEK_API_KEY`, `CEREBRAS_API_KEY` | provision key; `/login` where supported | `~/.pi/agent/auth.json` |
| Vercel, Z.ai, MiniMax, Moonshot, Mistral | `AI_GATEWAY_API_KEY`, `ZAI_API_KEY`, `MINIMAX_API_KEY`, `MOONSHOT_API_KEY`, `MISTRAL_API_KEY` | provision key | `~/.pi/agent/auth.json` |
| GitHub Copilot | `COPILOT_GITHUB_TOKEN` | `/login github-copilot` once | `~/.pi/agent/auth.json` |
| Hugging Face, Together, Fireworks, Baseten | `HF_TOKEN`, `TOGETHER_API_KEY`, `FIREWORKS_API_KEY`, `BASETEN_API_KEY` | provision key | `~/.pi/agent/auth.json` |

Environment credentials are read ambiently for the process and are never copied
into receipts. OAuth credentials persist in the pi agent auth store; the exact
location can be overridden by the normal pi configuration directory.
OAuth login is interactive and must be completed by a human; the bridge cannot
perform it.

## Invoke workflows

Use the absolute bridge path from any directory:

```bash
python skills/sure/scripts/run.py list
python skills/sure/scripts/run.py init --arguments 'provider=openai model=gpt-5.5' --trust
python skills/sure/scripts/run.py feed --arguments 'model_input_path=/data/model.yaml' --trust
```

For long or quoted input use `--args-file`; use `--output-dir` for every
workflow that produces artifacts. `--dry-run` validates discovery and shows the
native request without starting a run. `--trust` is only for a checkout the
user trusts. The command returns JSON and writes a durable receipt under
`.sure/agent-invocations/<invocation_id>/receipt.json`. Completed native runs
also expose `receipt_dir/artifacts` as a symlink to the authoritative
`.sure/runs/<run_id>/artifacts` directory, so a calling agent or user can inspect
or repair evidence and then resume the reported run when supported.

A typical end-to-end sequence is:

```text
init → feed → onboard (or trans) → approve → infer → eval
```

Approval is two phase: create the review packet, show it to the user, then pass
their explicit decision through the native approval workflow. Existing approved
models may start at `infer`; existing prediction bundles may start at `eval`.

## Receipt contract

The machine-readable contract is
[`schemas/sure.agent.receipt.v1.schema.json`](schemas/sure.agent.receipt.v1.schema.json).
`status` is `running`, `planned`, `success`, `failed`, `blocked`, `incomplete`,
`cancelled`, or `timed_out`. Exit codes are 0 for `success` and `planned`, 1
for `failed`, `blocked`, or `incomplete`, 124 for `timed_out`, and 130 (or
128+signal on Unix) for `cancelled`; invalid invocation limits return 1/2
before a native run. Programmatic callers must require `status == success` and
check `evidence_path`, `run_id`, `manifest_path`, and every relative `products`
entry. A successful receipt also includes SHA-256 hashes and `trace_summary`;
these are evidence, not a substitute for reading metric artifacts.

Requests use `sure.agent.request.v1`: `invocation_id` is exactly 32 lowercase
hex characters; `command` is `sure_init`, `sure_resume`, or a discovered
`sure_*` workflow; and `arguments` is opaque. Credentials and `output_dir` are
forbidden inside `arguments`: credentials use ambient environment variables or
native login, while `output_dir` is the typed field. `init` and `resume` omit
`output_dir`; resume arguments contain exactly one run ID. Native responses must
echo the same invocation ID and command.

## Worked fixture example

The following redacted receipts show the actual bridge shape. Paths are shortened
for readability; each command writes its own receipt directory.

```json
{"schema":"sure.agent.receipt.v1","invocation_id":"11111111111111111111111111111111","command":"/sure_init","status":"success","exit_code":0}
{"schema":"sure.agent.receipt.v1","invocation_id":"22222222222222222222222222222222","command":"/sure_feed","status":"success","exit_code":0,"run_id":"20261031-feed-a1b2","evidence_path":".../feed/result.json","products":["model_input_resolved.json"]}
{"schema":"sure.agent.receipt.v1","invocation_id":"33333333333333333333333333333333","command":"/sure_onboard","status":"success","exit_code":0,"run_id":"20261031-onboard-c3d4","evidence_path":".../onboard/result.json"}
{"schema":"sure.agent.receipt.v1","invocation_id":"44444444444444444444444444444444","command":"/sure_approve","status":"incomplete","exit_code":1,"run_id":"20261031-approve-e5f6","error":"Human approval is required","evidence_path":".../approve/result.json"}
{"schema":"sure.agent.receipt.v1","invocation_id":"55555555555555555555555555555555","command":"/sure_infer","status":"success","exit_code":0,"run_id":"20261031-infer-1122","evidence_path":".../infer/result.json","products":["predictions.jsonl","protocol.yaml"]}
{"schema":"sure.agent.receipt.v1","invocation_id":"66666666666666666666666666666666","command":"/sure_eval","status":"success","exit_code":0,"run_id":"20261031-eval-3344","evidence_path":".../eval/result.json","products":["metrics.json","report.html"]}
```

For `blocked`, inspect `error` and `next_action`; for `failed`, inspect the
receipt's `error`, native `run.json`, and evidence artifacts; for `incomplete`,
preserve `run_id` and call `resume` after satisfying the human checkpoint. Do
not infer success from fluent stdout or from a fixture test.
