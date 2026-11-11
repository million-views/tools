# tsr

Save Ghostty windows, tabs, folders and Claude Code, Codex and pi sessions on
macOS, then restore selected sessions into the window you are using.

Requires Ghostty 1.4+ and Python 3.14+. Runtime dependencies are standard library
only. uv supplies the development environment and the `tsr` entry point.

All four commands are implemented. [Verification](docs/v1.md) distinguishes
passing local checks from the remaining manual and publication checks.

From this directory:

```sh
uv run tsr doctor
uv run tsr save
uv run tsr list
uv run tsr list --json
uv run tsr list --backup
uv run tsr restore
uv run tsr restore --backup
uv run tsr --help
```

`save` reads Ghostty and agent metadata, prints the captured windows and confidence
for every agent, and writes the snapshot. It neither changes terminal contents
nor starts agents. It never writes to agent storage or Ghostty configuration.

Confidence means:

- **exact:** validated live process metadata or an open transcript file;
- **likely:** a validated launch argument or recent-transcript heuristic;
- **picker:** the agent was detected but its session could not be identified.

Check the likely/picker rows before relying on the snapshot. Claude's measured
runtime registry supports exact identification. Codex and pi can still require
fallbacks, including idle-resumed transcripts whose mtimes did not change.

`list` prints the saved folder and command for each pane in saved order. It does
not check live availability or apply restore's ambiguity downgrade. Split panes
have separate rows. `--json` prints the unchanged snapshot, including saved
`resume_argv`; identified pi sessions use absolute session-file paths.

State is stored in `${XDG_STATE_HOME:-~/.local/state}/tsr/`:

- `snapshot.json`: current capture;
- `snapshot.prev.json`: previous successful capture;
- `restored.json`: restore reservations and observed process identities;
- `restore.lock`: coordinates reservation updates;
- `tsr.log`: runtime error tracebacks, rotating at 1 MiB with three backups.

The directory is private (`0700`) and files are `0600`. State includes local paths
and first prompts, so keep it private. New data is staged before replacement.
Capture failures preserve existing snapshots. If a snapshot predates the current
boot, replacement asks for confirmation; `save --yes` answers that confirmation.

To restore, open a Ghostty window and run `tsr restore`. The picker groups panes
under their saved windows. Use arrows or j/k to move, Space to select a tab or
all available rows in a window, Enter to restore, left/right to collapse/expand,
and q/Esc to cancel. Without a selection, Enter restores the highlighted row or
window. Selections across windows keep saved order. Split panes become tabs.
Warnings take priority over long folder/prompt text. Enlarge terminals smaller
than 40 columns by 6 rows before selecting. A selection naming the same session
twice is rejected before any launch; choose one of those rows.

The first selected pane runs in the current tab; the rest append as new tabs.
Selected agents start their planned resume or picker command; plain shells open
in their saved folders.
Ambiguous recent-transcript groups and unidentified sessions open the agent's
own picker. A missing folder disables the row. Frozen format-1 snapshots from
M1 remain readable; null folders remain unsupported, so unresolved folders still
fail save while preserving its previous snapshot.

Verified running sessions and active restore reservations are disabled. When an
agent's current session cannot be verified, affected rows say **may already be
open** and remain selectable. Codex/pi warnings are agent-wide; Claude warnings
cover matching folders/worktrees, conservatively broader when cwd is unknown.
This is a scoped duplicate check, not proof that every selectable session is
closed. Agents outside Ghostty produce uncertainty warnings. The optional Codex
daemon query is deferred.

On Enter, a short file lock protects a fresh process recheck and reservation
write. No lock is held while browsing or launching tabs. Confirmed processes
remain protected while live. Pending rows are renewed during an active batch;
unconfirmed launches lapse after two minutes without renewal.
Per-tab failures release their reservation and the remaining tabs continue.
Picker reservations track the saved row, not a guessed session ID.
Independently launched agents can race a restore
after its final check; tsr coordinates only its own restores.

Install a published release with MacPorts uv and your repository SSH access:

```sh
uv tool install "git+ssh://git@github.com/millionviews/tools@tsr-v0.1.0#subdirectory=tsr"
```

Use a new release tag with `--force` to upgrade. Publication and the project
release tag remain owner-managed; that URL cannot install an unpublished tag.
For local use before publication:

```sh
uv tool install --python 3.14 /absolute/path/to/tools/tsr
```

Run `tsr doctor` to check Python/curses, PATH, Ghostty/Automation, capture
confidence and snapshot status. It warns when the effective `window-save-state`
setting has not been inspected; it does not guess the value or change your
configuration. If it is `always`, Ghostty may reopen empty shells alongside tsr's
restore. Automation permission must allow the requesting app to control Ghostty;
the exact first-prompt attribution still needs fresh-account verification.

Remove the executable with `uv tool uninstall tsr`. Snapshots remain in the
state directory; remove them separately only if you no longer need recovery.
After reinstalling, tsr reads them again. Restart the Mac yourself after checking
`save`/`list`; tsr does not issue restart requests.

Run the fixture and filesystem contract tests from this directory:

```sh
PYTHONPATH=src python3.14 -m unittest discover -s tests -v
```

For a live check that preserves your normal state, choose an isolated state root:

```sh
TSR_CHECK_STATE=$(mktemp -d)
XDG_STATE_HOME="$TSR_CHECK_STATE" uv run tsr save
XDG_STATE_HOME="$TSR_CHECK_STATE" uv run tsr list
```

The live commands still read real terminal/agent metadata and save private paths
and prompts in that temporary root. They do not create or type into terminals.

See [the spec](docs/spec.md) for behavior and [verification](docs/v1.md) for
coverage and remaining acceptance work. [M0](docs/m0.md), [M1](docs/m1.md) and
[S11](docs/s11.md) are historical evidence reports, not current implementation
instructions. See the [changelog](CHANGELOG.md) for notable releases.
Private-repository installation awaits owner publication.
