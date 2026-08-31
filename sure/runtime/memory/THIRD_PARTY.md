# Third-party code adapted into sure/runtime/memory/

All four sources are MIT licensed. Each adapted function carries a one-line attribution comment;
the full permission notices are reproduced here.

- hermes-agent (NousResearch), Copyright (c) 2025 Nous Research: `paths.atomic_replace` / `paths.atomic_write_bytes`
  (utils.py atomic write with the Windows rename retry) and `paths.memory_lock` (tools/memory_tool.py `_file_lock`),
  `paths.read_jsonl` (tools/skill_ledger.py tolerant reader).
- OpenHands software-agent-sdk, Copyright (c) 2026 OpenHands contributors: `index.render_index_md` budget
  truncation shape (openhands/sdk/context/memory.py `_truncate_top`, direction reversed).
- TencentDB-Agent-Memory, Copyright (C) 2026 Tencent: `match.ts` `applyRecallBudget` / `truncateRecallLine` /
  `normalizeBudgetLimit` (src/core/hooks/auto-recall.ts).
- anthropic-sdk-python, Copyright (c) 2023 Anthropic, PBC: `cli._contained` (`_validate_path`, the
  resolve() + startswith(root + sep) containment check used before `cli export` writes into a clone).

MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and
to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions
of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED
TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
DEALINGS IN THE SOFTWARE.
