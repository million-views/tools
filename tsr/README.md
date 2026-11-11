# tsr

Save and restore Ghostty tabs, folders and Claude Code, Codex and pi sessions
on macOS.

Requires Ghostty 1.4 or newer. Install a release with uv and access to the
private repository:

```sh
uv tool install "git+ssh://git@github.com/million-views/tools@tsr-v0.2.0#subdirectory=tsr"
```

Run `tsr doctor` first. Then, before a restart, save and review the capture:

```sh
tsr save
tsr list
```

In a fresh Ghostty window, run `tsr restore` and choose a saved window or tab.
The first restored pane uses the current tab; additional panes open as tabs in
saved order. Use arrows or j/k to move, Space to select, Enter to restore, and
q or Esc to cancel. Split panes restore as separate tabs.

Check the confidence shown by `save`: **exact** is validated identity,
**likely** is inferred from the command line or recent activity, and **picker**
means the agent will open its own session picker. A pane with an unknown folder
is saved and shown in `list`, but cannot be restored. Format-1 snapshots remain
readable; new saves use format 2.

Restore blocks sessions it can verify as running. When it cannot verify a
session, it shows **may already be open** and leaves the row selectable. Review
those warnings before choosing. Agents running outside Ghostty may also produce
warnings. tsr coordinates its own restores; an independently started agent can
race the final availability check.

Snapshots and restore state live in `${XDG_STATE_HOME:-~/.local/state}/tsr/`.
The private files include local folders and conversation prompts. Keep this
directory private. `tsr save` never changes Ghostty configuration or writes to
agent storage. Restart the Mac yourself after checking the saved list; tsr does
not request a restart.

Use `tsr list --backup` or `tsr restore --backup` to inspect or restore the
previous save. Remove the command with `uv tool uninstall tsr`; snapshots remain
available if you reinstall later.

## More

- [Manual acceptance guide](docs/acceptance.md)
- [Development guide](docs/development.md)
- [Behavior specification](docs/spec.md)
- [Implementation and verification](docs/v1.md)
- [Changelog](CHANGELOG.md)
- [Agent contribution rules](AGENTS.md)
