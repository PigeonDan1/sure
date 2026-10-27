# Reversible session context folding

The optional context-folding extension reduces older model input without changing the original session messages or calling a compression model. Its state comes from the current session branch, so the same mechanism continues across user prompts, SURE skills, reloads, and session resumption.

Enable it before starting the agent:

```bash
SURE_CONTEXT_FOLDING=1 PI_OFFLINE=1 ./pi-test.sh
```

In PowerShell:

```powershell
$env:SURE_CONTEXT_FOLDING = "1"
$env:PI_OFFLINE = "1"
powershell -ExecutionPolicy Bypass -File .\pi-test.ps1
```

It is disabled by default. `--no-extensions` also disables it. Folding suspends when tool selection excludes or deactivates `context_restore`, so omissions always have an available recovery tool. No separate model or credentials are needed.

## Agent signals

The extension adds stable system instructions and labels tool results with their original assistant session-entry ID:

```text
[context-turn a1b2c3d4]
```

Before tool calls, the main agent supplies these two text lines in its normal response:

```text
PROGRESS: Verify the selected runtime and CUDA version
STALE: a1b2c3d4, e5f6a7b8
```

`PROGRESS` is an optional short description of the current step. `STALE` names any earlier complete tool turns the agent no longer needs; `-` means none. A standalone `STALE:` first line is also valid. Neither the current response nor the referenced turn needs `PROGRESS`. A turn without progress uses a deterministic tool-name and argument index when folded. Delayed references are accepted, while unknown and future IDs are ignored. Signals must start the assistant's text; quoted metadata after ordinary prose is ignored.

One turn is an assistant tool-call message and all its tool results. Parallel tool calls are handled together. Ordinary user messages, assistant replies without tools, and unrelated custom messages remain in the model input. Large compressible turns receive ID labels; short confirmations remain unchanged to avoid label overhead.

## Completed skill handoffs

The SURE adapter appends a `context-folding.handoff` declaration only when `sure_finish` has accepted a run whose persisted status is `success`. Missing manifests, blocked gates, incomplete runs, and failures produce no handoff. The declaration identifies that run's invocation messages and completed tool turns, retaining the command, arguments, success summary, manifest path, and declared artifact paths. Resumed invocations are scoped to their own message ranges.

The generic renderer validates the declaration against the active branch and complete successful finish result. Once the finish turn passes the protected recent window, it can replace the old invocation instructions with this handoff. Declared earlier tool turns can also be folded at a chunk boundary, subject to the same error, image, pin, and savings protections. Other user instructions are retained. This uses persisted completion facts without calling a summary model; detailed domain evidence remains in the artifacts and original session.

## Rendering and cache behavior

The renderer has three operations:

- Fold a completed turn released by `STALE` or a verified handoff into a short progress or tool-index note with its archive ID. This removes the assistant's message and every result in the batch from model input together.
- Replace exact duplicate outputs or repeated contiguous line blocks with references to earlier raw copies that remain visible. Matching requires the same tool name and exact bytes, including spaces and line endings. Block matching starts from eight identical lines and extends through the identical span; changed prefixes, suffixes, and lines remain visible. References never point to omitted blocks or other references.
- Replace a completed skill's invocation instructions with its verified handoff and original source ID.

An operation must save at least 512 content characters after its replacement note. Error results, images, deferred-tool load points, restore results, and caller-pinned turns are protected. An identical tool call re-executed after its earlier turn became stale and eligible for folding is also protected.

The default chunk size is 3. With `t` completed tool turns and chunk size `k`, the eligible prefix ends at `max(0, k * floor(t / k) - k)`. Thus the last 3 to 5 complete tool turns remain in full at the default setting. Completion signals are snapshotted at the last chunk boundary. With unchanged configuration and pins, the rendered prefix remains identical between boundary advances; restoring appends a result rather than editing old messages. Folding boundaries still invalidate the affected provider-cache suffix.

Set `SURE_CONTEXT_FOLDING_CHUNK_SIZE` to an integer from 1 to 64 to change the chunk size. Invalid environment values fall back to 3. The renderer API also accepts `pinnedTurnIds`; extension integrations can supply `getPinnedTurnIds(ctx)`. Pin changes can immediately restore a turn's full representation, prioritizing preservation over prefix stability.

## Restore

The `context_restore` tool accepts up to three IDs:

```json
{"turn_ids":["a1b2c3d4"]}
```

It returns the original tool arguments and exact stored tool-output content, or the original invocation instructions for an ID declared in a valid handoff. It does not execute commands again and does not expose opaque provider reasoning signatures as tool text. The full original assistant messages, including provider replay metadata, remain unchanged in the session archive. Restored tool results are never folded or deduplicated.

Restoration is limited to complete turns on the active session branch. It can retrieve turns hidden by ordinary compaction because that compaction preserves the session entries. An unrelated branch or session ID is rejected. Content already truncated by the original tool cannot be recovered beyond the stored output, and restoring large results increases the next model input.

## Metrics and verification

Each completed model response appends a version 2 `context-folding.metrics` custom session entry containing the chunk size, eligible boundary ID, completed/folded/deduplicated counts, block and invocation counts, accepted/ignored STALE-reference diagnostics, original and rendered content-character counts, and provider-reported usage. `staleReferences` counts all syntactically valid IDs; `ignoredStaleReferences` counts unknown or future IDs. Per-turn decisions distinguish `folded`, `protected`, `recent`, `not_marked_stale`, `insufficient_savings`, and `context_transformed`. These are counts for that request's rendered view, not newly performed operations or cumulative savings. Metrics entries do not enter model input. Character counts exclude the system prompt, tool definitions, images, and opaque provider signatures; they are not tokenizer measurements or billing estimates.

Compare fresh input, cache reads/writes, output, task validation results, restore frequency, and latency under matched initial conditions. Smaller rendered inputs can still increase cost if frequent cache invalidation shifts enough traffic from cached to fresh input. This implementation does not claim a measured token, price, or quality improvement for SURE yet.

The generic implementation lives in `packages/coding-agent/src/core/context-folding*.ts`. `core/sure/context-handoff.ts` supplies SURE completion facts. Domain rules such as which onboard checkpoint is complete and which artifact fields must remain visible belong to the skill packages; they can supply pins and completion information without adding model-specific deployment rules to the common renderer.
