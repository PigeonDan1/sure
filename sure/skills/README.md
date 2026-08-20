# Repository Skills

Put shared Sure skill packages in this directory.

```text
sure/skills/<skill-name>/
  sure.skill.json
  SKILL.md
  hooks/
  scripts/
  schemas/
  examples/
  references/   (optional: contracts, playbooks, and other agent reference material)
```

Runtime discovery also checks `.sure/skills`. Project-local `.sure/skills` packages override repository packages for the same slash command, so use `.sure/skills` for private experiments and `sure/skills` for reviewed shared skills.

## Minimum Package

A minimal package needs:

- `sure.skill.json`: package metadata, command, prompt path, hook declarations, required artifacts, optional UI hints
- `SKILL.md`: instructions the agent follows when the slash command starts
- `hooks/index.ts`: gates for startup, tool calls, final artifacts, and error handling
- `scripts/`: executable implementation code used by the prompt
- `examples/` (optional): minimal input and expected manifest examples; recommended for reviewed shared skills

See `AGENTS.md` at the repository root for the full developer guide.

## Review Rules

- Keep skill-specific logic in the skill package.
- Do not add task-specific phases or metrics to Harness code.
- Use `sure_update_state` or hook `state_patch` for TUI/WebUI display state.
- Use `pre_finish` to reject missing, incomplete, or low-quality artifacts.
- Every successful or incomplete run must end with `sure_finish` and a valid final manifest.
