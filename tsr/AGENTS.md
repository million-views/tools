# tsr working rules

- Read all of `docs/spec.md` before starting. The spec is the source of truth.
- The owner has authorized taking tsr through completion, including M2/M3.
  Continue implementation and verification without milestone approval stops.
  Preserve readability of M1 format-1 snapshots; format 2 permits null cwd. Keep
  outstanding external evidence gaps explicit without inventing successful checks.
- Section 2 contains the only external behavior implementation may rely on.
  If another fact about Ghostty, macOS, Claude Code, Codex, pi or uv is needed,
  stop the dependent work and propose a new spike. Do not infer an interface.
- Record measured spike results in section 2, distinguishing observations,
  negative answers, pending checks and documented mechanisms. State the fallback.
- Use Python 3.14 and the standard library only. Follow section 7: production
  code in `src/tsr/__init__.py`, dependency-free project, `uv_build` backend.
- Never write to `~/.claude`, `~/.codex` or `~/.pi`. Never change Ghostty config.
- Ask before desktop changes, including opening windows/tabs or typing commands.
  Existing approval in the current conversation may authorize a specific probe.
  Type only into terminals created for that probe; never existing user terminals.
- Never restart the Mac during spikes. For S6, inspect and document the mechanism
  without executing a restart request.
- Fixtures must come from the owner's machine. Before saving, anonymize home
  paths (including encoded forms), prompts and session IDs. Preview the exact
  sanitized content to the owner before saving. Never save unsanitized fixtures.
  Product snapshots and private probe runtime state are not fixtures; keep them
  outside `tests/fixtures/` and never commit them.
- Store fixture outputs under `tests/fixtures/`; label excerpts and omitted data.
  Do not fabricate successful evidence or claim an unrun probe passed.
- Show the proposed fixture changes before committing. Do not commit or publish
  without authorization.
- For unavailable environments, provide exact steps and the output to paste back.
- Use this project's ignored `tmp/` directory for scratch notes, drafts and local
  outputs. Keep canonical source, docs and anonymized fixtures outside it.
- Keep milestone reports and evidence current. Completion is now authorized;
  do not stop for another milestone go-ahead. Never count pending acceptance
  checks as completed or treat this authorization as desktop/config permission.
