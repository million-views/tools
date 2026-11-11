# tsr — Terminal Session Restore: Build Spec (v1: Ghostty on macOS)

Nov 9, 2026 · @Hema

## 1. Purpose and scope

Build `tsr`: a single-file, stdlib-only Python 3.14 tool that saves every Ghostty window, its tabs, and the Claude Code, Codex and pi sessions running in them, and after a restart lets the user rebuild those windows one at a time from an interactive picker.

This spec is written for a coding agent (Claude Code or Codex) to implement. Every behavior that depends on an external tool cites a verified fact in section 2. Anything not verified is a spike in section 12 with a designed fallback. **Do not invent behavior of Ghostty, macOS, Claude Code, Codex or pi that isn't in section 2. If you need something that isn't there, stop and add a spike.**

### The one use case

The user has many Ghostty windows with many agent sessions running, and has to restart the Mac.

1. Before restarting: `tsr save`. It records each window, its tabs in order, each known folder, and the agent session running in it. A folder it cannot determine is saved as unknown.
2. After restarting: open a fresh Ghostty window and run `tsr restore`. A picker shows the saved windows with their tabs.
3. Pick a window or some of its tabs. tsr rebuilds them in the current window: the first selected tab runs in this tab, the rest open as new tabs after it, each resuming its session in its folder.
4. Open another window and repeat. Restore never duplicates a session it can verify as running; for sessions it can't verify, it says so before you pick them. v1 covers sessions running in Ghostty for the current user, plus sessions tsr restored itself (section 6).

### Goals

1. Capture is one command and finishes in under 3 seconds for 10 windows and 50 tabs.
2. Restore is window-by-window, under the user's control. Restore never duplicates a session it can verify as running; for sessions it can't verify, it says so before you pick them. Unverified cases say "may already be open"; they are never presented as safe.
3. The user can verify the capture before restarting: `save` prints what it found and how confident it is about each session.
4. Installing, upgrading and removing are single uv commands; tsr needs no setup step of its own (section 7).
5. The design admits new terminals and agents as adapters, without changing commands (section 8).

### Non-goals for v1

- Terminals other than Ghostty on macOS, and Linux.
- Automatic or scheduled saving, crash recovery, and shutdown hooks (section 2, F18 to F20 explain why shutdown hooks can't work).
- Snapshot history: one current snapshot plus one backup.
- Recreating split layouts: split panes come back as separate tabs (F11).
- Wrapping agent commands, shell aliases, shell integration, agent hooks.
- Relocating sessions whose folder was moved or deleted.

### Hard constraints

- **Python 3.14+, standard-library runtime only.** `uv_build` is the build backend, not a runtime dependency.
- **One module**, `src/tsr/__init__.py`, plus a `pyproject.toml` with no dependencies (section 7).
- **Read-only toward agents.** Never write to `~/.claude`, `~/.codex` or `~/.pi`.
- **Type only into terminals tsr just created. In its own terminal, tsr replaces itself with the command (exec) instead of typing.** Never into other existing terminals.
- **Ghostty 1.4 or newer** (needed for the terminal `tty` property, F9).

## 2. Verified facts

External behavior must be supported by these sources and dated measurements.
Sections cite facts by ID. Correct a disproven fact here before changing the
design that depends on it. Product choices belong in the design sections, not
as additional verified facts.

### Ghostty on macOS

| ID | Fact | Source |
| --- | --- | --- |
| F1 | AppleScript (1.3+) object model: application → windows → tabs → terminals. Window: `id`, `name`, `selected tab`. Tab: `id`, `name`, `index`, `selected`, `focused terminal`. Terminal: `id`, `name`, `working directory`. | [Ghostty AppleScript docs](https://ghostty.org/docs/features/applescript) |
| F2 | Commands: `new surface configuration`, `new window with configuration`, `new tab in <window> with configuration` (returns the tab), `split`, `focus`, `activate window`, `select tab`, `input text … to <terminal>`, `send key "enter" to <terminal>`. | [Ghostty AppleScript docs](https://ghostty.org/docs/features/applescript) |
| F3 | Surface configuration fields: `initial working directory`, `command`, `initial input`, `wait after command`, `environment variables` (list of `KEY=VALUE`), `font size`. | [Ghostty AppleScript docs](https://ghostty.org/docs/features/applescript) |
| F4 | `input text` sends paste-style input; Ghostty's own examples follow it with `send key "enter"` to run a command. | [Ghostty AppleScript docs](https://ghostty.org/docs/features/applescript) |
| F5 | Automation is gated by macOS TCC permission prompts; `macos-applescript = false` disables AppleScript. `osascript -e 'tell application "Ghostty" to get version'` verifies it works. | [Ghostty AppleScript docs](https://ghostty.org/docs/features/applescript) |
| F6 | `window-save-state = always` saves windows on every exit; `default` saves only on forced termination unless set system-wide. Restoring working directories requires Ghostty shell integration. | [ghostty(1)](https://man.archlinux.org/man/ghostty.1) |
| F7 | The terminal `id` is a UUID persisted through `window-save-state` restore. | [ccr README](https://github.com/andresCamp/ccr), [HXavS fork issue](https://github.com/HXavS/ghostty/issues/1) |
| F8 | `GHOSTTY_SURFACE_ID` is a runtime number, not the AppleScript UUID, and isn't visible from AppleScript. Match terminals to processes by `tty` instead. | [claude-pet PR #32](https://github.com/ZephTu/claude-pet/pull/32) |
| F9 | Ghostty 1.4 adds `pid` (of the foreground process) and `tty` to the AppleScript terminal class. | [1.4 release notes](https://ghostty.org/docs/install/release-notes/1-4-0) |
| F10 | Apps built on libghostty (e.g. cmux) are separate apps with their own windows and tabs, and may set Ghostty-like environment variables. Identify the owning app by process, not by environment. | [cmux README](https://github.com/manaflow-ai/cmux), [revdiff PR #209](https://github.com/umputun/revdiff/pull/209) |
| F11 | The AppleScript model lists a tab's terminals but exposes no split geometry (direction or sizes). | [Ghostty AppleScript docs](https://ghostty.org/docs/features/applescript) |

### Agents

| ID | Fact | Source |
| --- | --- | --- |
| F12 | `claude --resume <id>` only finds a session from the directory (or its worktrees) where it started; elsewhere: "No conversation found". | [cc-session-alias](https://github.com/bigwhite/cc-session-alias), [issue #14252](https://github.com/anthropics/claude-code/issues/14252) |
| F13 | Claude transcripts: `~/.claude/projects/<encoded-path>/<session-id>.jsonl`; conversation records can contain `sessionId`, `cwd`, `gitBranch`, `timestamp`, `type`. Not every line contains these: the local S2 sample begins with a `last-prompt` record containing only `type`, `leafUuid` and `sessionId` (F30). The path encoding is lossy: never decode it. | [JSONL format reference](https://claude-dev.tools/docs/jsonl-format), local S2 fixture |
| F14 | `codex resume <SESSION_ID>` resumes a specific session; its picker and `--last` are filtered by the current directory. | [Codex session management (third-party)](https://codex.danielvaughan.com/2026/03/30/codex-cli-thread-search-session-management), [PR #6232](https://forge.lthn.ai/core/core-agent-ide/commit/526eb3ff82b7404a32acf2671977eaa888a3d021) |
| F15 | Codex rollouts: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`; the first record is session metadata including working directory and git branch. The format isn't a stable interface. | [Codex lifecycle article](https://codex.danielvaughan.com/2026/04/11/codex-cli-session-lifecycle-resume-fork-rollouts), [Codex hooks docs](https://developers.openai.com/codex/hooks) |
| F16 | `pi --session <absolute-file-path>` opens a specific session file. Do not generalize cross-directory behavior to bare IDs: installed pi 1.5.1's source prompts to fork an ID found in another project (F39). Sessions: `~/.pi/agent/sessions/--<path>--/<timestamp>_<id>.jsonl` (overridable by `PI_CODING_AGENT_DIR`, `PI_CODING_AGENT_SESSION_DIR`); first line `{"type":"session","version":3,"id":…,"cwd":…}`. | [pi sessions](https://pi.dev/docs/latest/tree), [pi session format](https://pi.dev/docs/latest/session-format), [pi usage](https://cdn.jsdelivr.net/npm/@mariozechner/pi-coding-agent@0.73.1/docs/usage.md), installed-source check F39 |

### macOS and Python

| ID | Fact | Source |
| --- | --- | --- |
| F17 | Python 3.14's standard library includes `curses`, `tomllib`, `json`, `subprocess` and `uuid.uuid7()`. | [Python uuid docs](https://docs.python.org/el/3/library/uuid.html) |
| F18 | LogoutHook is deprecated in favor of LaunchAgents, runs as root, and there is one per system. | [Offset](https://github.com/aysiu/offset), [theevilbit](https://theevilbit.github.io/beyond/beyond_0022) |
| F19 | macOS may kill LaunchAgents during shutdown without sending SIGTERM, so a signal-trapping agent can't be relied on. | [shutdown-sync](https://github.com/naeem-gitonga/shutdown-sync) |
| F20 | AppleScript run from a logout-trap script has been reported to fail with error -10810. | [bruienne gist](https://gist.github.com/bruienne/0cce2d93687985c8df14) |
| F21 | macOS base system provides `ps` (including `-t <tty>` and `-o lstart=`), `lsof` (`-a -p <pid> -d cwd -Fn` for a process's working directory), and `sysctl -n kern.boottime`. | macOS base system |
| F22 | MacPorts packages uv (port `devel/uv`, 0.11.x) and Python 3.14 (port `python314`, 3.14.4, built with ncurses and sqlite3). | [MacPorts uv](https://ports.macports.org/api/v1/ports/uv/), [MacPorts python314](https://ports.macports.org/api/v1/ports/python314/) |

### Local measurements (2026-11-10, Ghostty 1.4.0)

These are observations on this machine, not guarantees for other versions. Raw
outputs below are anonymized; transcript fixtures are explicitly labeled excerpts.
The reproducible procedure and pending checks are in [m0.md](m0.md).

| ID | Fact | Source |
| --- | --- | --- |
| F23 | S1: AppleScript `tty` and the shell's `tty` command both returned `/dev/ttys035`; `ps -o tty= -p $$` returned `ttys035`. All 20 desktop-probe tabs had matching normalized tty values. Normalize by removing `/dev/` and then a leading `tty`, retaining `s035`. Trailing-device-number fallback is unnecessary for these samples. | [Layout](../tests/fixtures/s1-layout.tsv), [shell comparisons](../tests/fixtures/s4-s7-no-settle.json), [repeat](../tests/fixtures/s4-s7-settle.json) |
| F24 | S2: sampled running Claude, Codex and pi processes, including user-controlled idle-resumed processes, held no `.jsonl` transcript open and had no observed descendants. Open-file identification is unavailable in these samples: fall through to argv, recent-transcript, then picker; do not label them exact. On idle resume, Claude's mtime and size changed; Codex and pi preserved both. Old idle-resumed Codex/pi transcripts can fail the recent-transcript cutoff. | [Initial open files](../tests/fixtures/s2-open-files.txt), [resumed Claude/Codex](../tests/fixtures/s2-controlled-resume-open-files.txt), [resumed pi](../tests/fixtures/s2-pi-resumed-open-files.txt), [Claude mtime](../tests/fixtures/s2-claude-idle-resume.json), [Codex mtime](../tests/fixtures/s2-codex-idle-resume.json), [pi mtime](../tests/fixtures/s2-pi-idle-resume.json) |
| F25 | S3: sampled Claude argv is `claude --resume <id>` (also observed `claude`); Codex argv is a native binary under the npm package's `vendor/aarch64-apple-darwin/bin/codex`, preserving `resume <id>` after a controlled resume. Pi's process title/argv is `pi`, while lsof's executable entry is Node; after resuming with `--session <id>`, ps still showed only `pi`, so its argv strategy cannot recover that ID in this sample. No wrapper/child pair appeared. Match these forms; retain outermost matching without claiming wrappers/nesting were tested. | [Processes](../tests/fixtures/s3-processes.txt), [resumed Claude/Codex](../tests/fixtures/s3-controlled-resume.txt), [resumed pi](../tests/fixtures/s3-pi-resumed.txt), [executables](../tests/fixtures/s2-open-files.txt) |
| F26 | S4: with isolated zsh startup that prints early then sleeps 2 s, terminal `pid` reported `/bin/sleep` and later `-/bin/zsh`. Polling plus immediate paste/Enter produced the complete marker in 8/10 tabs; adding a 200 ms settle delay after shell detection produced it in 10/10 new tabs. Use the settle-delay fallback. The probe's printf argument contains a quoted newline; the two missing markers' precise cause was not isolated. This verifies the tested startup, not every prompt framework. | [No settle](../tests/fixtures/s4-s7-no-settle.json), [200 ms settle](../tests/fixtures/s4-s7-settle.json), [probe](../scripts/m0-desktop.py) |
| F27 | S6 (mechanism inspection only): the installed System Events scripting dictionary defines `restart` (`fndrrest`) with optional boolean `state saving preference`. Candidate request: `tell application "System Events" to restart with state saving preference`. No restart request was executed. Root requirements, Automation prompts and apps' quit behavior remain unverified; until resolved, use the designed fallback of omitting `--restart` and printing "Saved. Restart when ready." | `/System/Library/CoreServices/System Events.app/Contents/Resources/SystemEvents.sdef`, [excerpt](../tests/fixtures/s6-restart-dictionary.xml) |
| F28 | S7: with a separate probe window confirmed first/front before each creation, `new tab in <target>` appended at the target's existing count + 1 in all 20 trials. The target window ID remained unchanged; indices progressed 2–21. No activate-target fallback is needed for this sample. | [First ten](../tests/fixtures/s4-s7-no-settle.json), [second ten](../tests/fixtures/s4-s7-settle.json) |
| F29 | S8 interpreter check: local MacPorts uv is 0.13.4; it downloaded managed CPython 3.14.8, ran it, and imported `curses`. The subsequent isolated tagged-subdirectory/entry-point/PATH/uninstall probe passed (F40). The private SSH and default-user PATH checks still await publication. Neither the separate-repository nor MacPorts-Python fallback has been triggered by a negative result. | [Managed Python output](../tests/fixtures/s8-uv-python.txt), [local install](../tests/fixtures/s8-local-tag-install.json), [remaining procedure](m0.md) |
| F30 | S2 describe samples: Claude's first ordinary user record has `message.content` as a string; pi v3 has `type=message`, `message.role=user`, and a list of `{type: text, text: ...}` content blocks. Codex 0.167.0 has a `session_meta` payload with `id`, `session_id`, `cwd` and `git` (empty here), followed by `response_item` messages with `payload.role=user` and `input_text` content blocks. A user-role environment-context record precedes the actual prompt; skip that context when describing. Missing branch is null. These samples do not establish other versions' schemas. | [Claude](../tests/fixtures/claude-transcript-excerpt.jsonl), [Codex](../tests/fixtures/codex-transcript-excerpt.jsonl), [pi](../tests/fixtures/pi-transcript-excerpt.jsonl) |
| F31 | S8 tooling inspection: local `uv tool dir` returned `~/.local/share/uv/tools`. The isolated smoke installation later used `<temporary tool root>/tsr/bin/python` successfully (F40). The published production package/default-user environment still needs its private-tag install probe. | [Default root](../tests/fixtures/s8-uv-tool-dir.txt), [isolated install](../tests/fixtures/s8-local-tag-install.json) |
| F32 | S11: all 11 sampled live Claude processes had `~/.claude/sessions/<pid>.json` records containing `pid`, `sessionId`, `cwd`, `procStart` and `version`. Three distinct sessions shared one cwd. Registry `procStart` matched `LC_ALL=C TZ=UTC ps ... lstart=` for all 11 after whitespace normalization. Each mapped to one transcript with a matching recorded session ID. The final probe took 9.298 ms including metadata/transcript checks, excluding ps. Versions observed in the registry were 2.1.316–2.1.322. Treat this as a version-sensitive runtime interface, validating PID and process start before trusting it. | [Live projection](../tests/fixtures/s11-claude-live-registry.json), [read-only probe](../scripts/m0-live-claude.py), [procedure and limits](s11.md) |
| F33 | S11: the owner resumed a known Claude session, then used `/clear` in the same PID 52702. Its live registry changed session ID and retained the same process start; ps still named the old ID in `--resume`. The original registry ID matched the owner-supplied session ID. This supports runtime metadata ahead of argv for Claude; a transcript existed for the new registry ID at the subsequent full probe. In-session `/resume`, exit cleanup and deliberate PID reuse have not been tested. | [Controlled clear](../tests/fixtures/s11-claude-clear.json), [subsequent live projection](../tests/fixtures/s11-claude-live-registry.json) |
| F34 | S11: three Codex session-named `thread-writer-locks/<id>.lock` files were held by shared daemon PID 32138, not the sampled CLI PIDs. After the owner ran `/new` and sent a harmless prompt in test CLI PID 52277, the daemon retained the old test session lock and added a fourth; frontend argv still named the old session. These handles are not an exact current-session-to-terminal mapping. Both lsof probes returned exit 1 with handle rows, so the returned set is not claimed exhaustive. Inspected `threads` and `logs` schemas had no PID column; logs had `process_uuid`, with no verified PID link. | [Before locks](../tests/fixtures/s11-codex-locks-before.txt), [after locks](../tests/fixtures/s11-codex-locks-after.txt), [controlled action](../tests/fixtures/s11-codex-lock-lifecycle.json), [schema projection](../tests/fixtures/s11-schema.json) |
| F35 | S11: the installed pi is `@earendil-works/pi-coding-agent` 1.5.1. Its CLI source sets `process.title = APP_NAME`; session persistence uses synchronous file writes, closes explicit file descriptors, and uses `appendFileSync` for later records. This is consistent with S2's missing idle transcript handle. Its RPC client starts a separate process in `--mode rpc`; this does not establish an attach API for the existing interactive process. No verified passive PID-to-current-session interface was found in the inspected core source and local runtime files. This is an investigation limit, not proof that none can exist. | [Installed-source excerpts](../tests/fixtures/s11-pi-source.txt), [package version](../tests/fixtures/s11-schema.json), [S2 idle handles](../tests/fixtures/s2-pi-resumed-open-files.txt), [scope](s11.md) |
| F36 | S11 follow-up: a stdlib WebSocket client connected to the existing default Codex Unix control socket. `thread/loaded/list` returned three loaded IDs, including idle threads; `thread/read` returned metadata/status without a PID field. Reading the previous test session reported `notLoaded`; a subsequent list confirmed it stayed unloaded. The final probe took 2.977 ms. This establishes loaded-ID evidence for that daemon, but does not identify frontend terminals or establish coverage of other servers/standalone CLI versions. No start/resume/turn methods were sent. Whether tsr uses this evidence remains an owner decision (section 14). | [Machine output](../tests/fixtures/s11-codex-rpc.json), [read-only probe](../scripts/m0-codex-read.py), [official protocol](https://learn.chatgpt.com/docs/app-server) |
| F37 | S4 follow-up: ten fresh slow-starting zsh tabs received complete single-line markers with zero settle delay (10/10). The prior 8/10 result used a quoted newline; it cannot establish that startup timing alone caused the missing markers. Retain the 200 ms conservative fallback from F26, but keep multiline input reliability explicitly limited. S7 appending also held for these ten trials (indices 22–31). | [Single-line report](../tests/fixtures/s4-single-line.json) |
| F38 | S10: two harmless Python children with distinct vectors `["TWO WORDS"]` and `["TWO", "WORDS"]` produced identical `ps args` text on this Mac. Quotes/backslashes were also displayed without an invertible argument encoding. `shlex.split(ps_args)` cannot in general recover original argv. This tests ps transport, not a simulated agent. Parse only explicitly validated unambiguous resume patterns; an ambiguous pi path must fall through. | [Real child output](../tests/fixtures/s10-argv.json), [probe](../scripts/m0-argv.py) |
| F39 | S9: installed pi 1.5.1 parses bare `--resume`/`-r` as the picker; its selector defaults to current-folder scope and provides an all-sessions loader. The owner ran `pi --resume` and reported that cancellation quits pi; the exact heading was not explicitly reported. Source distinguishes absolute/path-like `--session` arguments (open directly) from globally found bare IDs (prompt to fork). Prefer absolute session-file paths. Owner-run Claude/Codex picker checks are recorded separately in F48. | [Installed source/usage](../tests/fixtures/s9-picker-source.txt), [owner observation](../tests/fixtures/s9-pi-owner.json) |
| F40 | S8: an isolated temporary git repository containing section 7's layout installed successfully via `git+file://...@<tag>#subdirectory=tsr`, using uv-managed Python 3.14.8 and curses. Its entry point used tagged code despite a later worktree edit; scoped PATH lookup and uninstall passed. The private SSH/default-user PATH installation was not tested by this probe. Its original URL used an incorrect GitHub owner slug; the corrected URL is in section 7. | [Local tagged install](../tests/fixtures/s8-local-tag-install.json), [historical private endpoint output](../tests/fixtures/s8-private-remote.txt), [isolated probe](../scripts/m0-install.py) |
| F41 | S4 cleanup inspection: all 32 isolated zsh probe terminals returned an empty AppleScript `working directory`, despite a configured initial directory. Read-only lsof cwd records for their live shell PIDs returned the correct three probe directories. The getter therefore cannot be assumed populated for every shell configuration. Include foreground PIDs for panes with missing cwd in the existing lsof batch and use that cwd as the fallback; never invent a folder. | [Terminal projection](../tests/fixtures/s4-empty-terminal-cwds.tsv), [lsof cwd records](../tests/fixtures/s4-empty-cwd.txt) |
| F42 | S12: stdlib ctypes called macOS `sysctlbyname("kern.boottime", old_value, old_length, NULL, 0)` read-only and returned the same seconds/microseconds as the F21 command. The observed timeval was 16 bytes (`long` seconds, 32-bit microseconds plus alignment). Validate return code and returned size; do not use uninitialized values on failure. This supplies boot time without a fourth subprocess. | [Native/command comparison](../tests/fixtures/s12-boot-time.json) |
| F43 | S13: installed lsof help defines `-n` as no host names and `-P` as no port names. On 64 live PIDs, plain/numeric-output reads took 0.155/0.059 s; cwd maps and observed transcript-path sets agreed. Other absolute-file maps differed between successive live reads, so this is not a frozen-descriptor snapshot guarantee. Use numeric output to avoid unrelated name resolution. | [Measured comparison](../tests/fixtures/s13-lsof-numeric.json), installed `lsof -h` |
| F44 | S14: bulk `properties of every tab of w` and `properties of every terminal of tab id ... of w` returned the same ordered window/tab/terminal IDs, indices, ttys and cwd as the original traversal for 11 windows / 62 panes. Layout time improved from 2.867 to 2.357 s. Titles and foreground PIDs were excluded from equality because they can change during capture. Use the measured bulk reads within the one layout subprocess. | [Comparison](../tests/fixtures/s14-bulk-layout.json) |
| F45 | CHECK 2: five read-only recheck-data probes over 1,197 live processes took 170.732, 151.101, 146.870, 148.186 and 143.688 ms (median 148.186 ms). Each included one UTC ps call and validation of all 11 live Claude runtime identities using the M1 adapter, including its transcript checks. `restored.json` was absent. No AppleScript, lsof, lock, reservation write or M2 code was used. This measures the read path, not the complete future critical section or a populated reservation scan. | Live measurement, 2026-11-10; reproducible read-only method below |
| F46 | CHECK 3, partial: a fresh read of the two existing S4/S7 probe windows found 32 terminals, 32 live foreground shell PIDs, 32 matching normalized ps/terminal ttys and 32 available UTC process-start times. These were shells, not agent launches; no per-tab reservation timestamp was recorded in S4/S7. This confirms tty association for those created terminals, but does not verify an agent starting after a reservation or its full lifecycle. | Read-only Ghostty layout plus UTC ps, 2026-11-10; S4/S7 creation procedure in [m0-desktop.py](../scripts/m0-desktop.py) |
| F47 | CHECK 3 counterexample: a harmless headless Python child was observed by UTC ps, then a reservation time was recorded, then the child executed `/bin/sleep`. ps observed the sleep executable with the same PID and unchanged process start as before exec. Thus a replacement process need not start after a reservation recorded before exec. This is an actual exec measurement, not a real-agent or Ghostty handoff test. | Headless stdlib subprocess/os.execl probe, 2026-11-10; no desktop or agent storage changes |
| F48 | S9 owner-run interactive checks: `claude --resume` showed `Resume session`, the current project label, `Ctrl+A to show all projects`, `Ctrl+B to only show current branch` and `Esc to cancel`. Esc returned to the shell; the owner reported `echo $?` as 1. `codex resume` showed `Resume a previous session`, filter Cwd selected with All offered, Active status and Updated sort selected; footer labeled Esc as new and Ctrl+C as quit. Ctrl+C returned to the shell; the owner reported exit 0. Scope switching and explicit-ID cross-folder resume were not exercised by these observations. | Owner screenshots and exit-code reports; [sanitized transcription](../tests/fixtures/s9-claude-codex-owner.json) |
| F49 | S15 / CHECK 1: the owner resumed a disposable Codex session by explicit ID from another project folder. The UI offered session directory versus current directory; choosing current directory was followed by a controlled marker-history check. The final screenshot showed Codex 0.167.1 in the other folder with the marker prompt and response, and the owner confirmed their presence. Explicit-ID resume therefore supports cross-folder conversation continuity in this measured version; apply agent-wide Codex warnings. The owner updated Codex during the check; the cause of the earlier missing history and behavior of the prior version are not established. This does not supply a passive frontend/session mapping. | Owner screenshots and confirmation; [sanitized observation](../tests/fixtures/s15-codex-cross-folder-owner.json) |
| F50 | S16: two approved new Ghostty tabs were associated with owner-launched idle Codex 0.167.1 processes by normalized tty. A's normally launched Codex PID started after the pre-creation reservation. B's shell-level exec handoff preserved the pre-exec PID and UTC start while changing the executable to Codex; its start preceded the handoff reservation. Ghostty reported B's parent PID, so its `pid` alone did not identify that agent. After owner quit, both recorded agent PID/start identities were absent, A retained its original zsh, and B's terminal was absent after the owner reported pressing Enter to close it. No tools closed either tab. | [Anonymized running/exit observations](../tests/fixtures/s16-process-lifecycle.json), [probe](../scripts/m0-reservation.py) |
| F51 | S16 timing limit: both newly created probe shells had ps start epochs in the same second as their pre-creation reservations, but the second-resolution ps values were numerically earlier than the fractional reservation times. Strict numeric start-after-reservation therefore cannot in general order events within one second. Both harmless in-tab Python-to-sleep execs also preserved PID/start. The owner-assisted Codex setup took over two minutes, so this was not a production launch-latency, two-minute timeout or actual restored.json lifecycle test. Claude/pi handoffs, deliberate PID/tty reuse and concurrency were not exercised. | [Timing values and limitations](../tests/fixtures/s16-process-lifecycle.json) |
| F52 | Git documents a worktree `.git` file containing `gitdir: <path>`, and a `commondir` file whose relative path is interpreted from that git directory. These permit read-only comparison of common repository identity without spawning git. Compare the folder's relative path within its worktree too; unrelated sibling folders are not thereby the same cwd. | [Official Git repository layout](https://git-scm.com/docs/gitrepository-layout) |
| F53 | S17: actual restore created three plain-shell tabs in saved order. After current-tab `chdir` and exec of the user's login bash, Ghostty's nonempty working-directory getter still reported the pre-restore directory, while the foreground process's lsof cwd reported the intended `first` directory. A fresh subsequent layout read confirmed the mismatch. Prefer readable foreground cwd over the getter, not only when the getter is empty. | Owner-approved isolated desktop restore and read-only follow-up; [probe](../scripts/v1-desktop.py), [verification](v1.md) |
| F54 | S17: local lsof field output doubles literal backslashes, emits newline/tab as C escapes and UTF-8 bytes under C locale as hexadecimal escapes. A controlled seven-directory probe measured those forms and preserved literal backslash-n distinctly. Literal `^A` and a control-byte representation can be ambiguous without other evidence; do not decode caret forms into an invented path. The local lsof manual documents C/control/hex output forms. | [Measured encoding outputs](../tests/fixtures/v1-verification.json); `/usr/share/man/man8/lsof.8`, OUTPUT section |
| F55 | Final local implementation checks: capture returned 12 windows / 65 tabs in 2.780 s with exactly osascript, ps and lsof. Actual Ghostty restored three plain-shell tabs in order, preserved the first terminal, handled a quoted/backslash folder, cancelled on q without changing state, and executed a harmless quoted marker command. Live availability blocked 11 exact Claude rows and warned on 3 Codex rows. No real agent was launched through tsr by these checks. | [Anonymized measurements](../tests/fixtures/v1-verification.json), [coverage/limits](v1.md) |
| F56 | The production package, copied into a temporary git repository/tag, installed via uv's local tagged-subdirectory transport with managed Python 3.14.8/curses. Scoped PATH and doctor passed. Uninstall retained a snapshot; reinstall read it; final uninstall removed the isolated executable. The private SSH/default-user install path was not tested. | [Measurements](../tests/fixtures/v1-verification.json), [reproducible probe](../scripts/v1-install.py) |

CHECK 2 method: time five sequential runs of the M1 Runner's
`ps -A -ww -o pid=,ppid=,tty=,lstart=,args=` with `LC_ALL=C TZ=UTC`, parse
processes, and call the Claude adapter's `session_from_runtime(pid, start_utc)`
for each matched Claude process. Check/read the configured tsr `restored.json`
if present; stop the clock after those reads. The same adapter instance was used
across samples. Emit aggregate counts/timings only, without paths, prompts or
session IDs. A median of about 0.15 s supports a responsive recheck read path on
this desktop; it does not measure the full lock/write critical section. Later
reservation correctness tests are listed in [v1.md](v1.md).

| Pending spike | Result so far | Fallback / next action |
| --- | --- | --- |
| S5 | Not run: needs a fresh macOS user account. Existing permission on this account does not reveal first-prompt attribution or repetition. | Run the exact read-only permission probe in [m0.md](m0.md); keep the requesting-app wording provisional. Repeat installed `tsr save` at M3. |
| S8 | Local tagged-subdirectory packaging, managed Python/curses, scoped PATH and uninstall passed (F40). Private SSH and default-user PATH await the owner's manual publication and a real package tag. | Follow [m0.md](m0.md); neither the separate-repository nor MacPorts-Python fallback is warranted by this result. |
| S11 (Codex/pi) | Claude's mapping is measured (F32–F33). Codex provides a verified loaded-ID set from one daemon (F36), but frontend association and other-server coverage remain unresolved. Pi has no verified passive mapping (F35). | Apply the scoped warning policy in section 6. Do not implement the daemon check pending the owner decision in section 14; the recommendation is warning only. |
| S16 broader acceptance | Real Codex tty association, pre-exec PID/start continuity and exit detection are measured (F50). M2 adopts the bounded evidence rule in section 4. | Normal real-agent handoff through tsr and other-agent launcher variants still require the manual acceptance matrix in [v1.md](v1.md); the strict timestamp rule was not used. |

## 3. Commands and UX rules

Four commands. Installing and removing tsr is uv's job (section 7). Nothing else is user-facing in v1.

| Command | Job |
| --- | --- |
| `tsr save [--yes]` | Capture all Ghostty windows, tabs, folders and agent sessions; print what was captured. Replaces the current snapshot and keeps the previous one as a backup. Print "Saved. Restart when ready." The proposed `--restart` is deferred under S6's fallback (F27). |
| `tsr restore [--backup]` | Open the picker (section 6) and rebuild saved windows or tabs into the window tsr runs in. `--backup` uses the previous snapshot. |
| `tsr list [--json] [--backup]` | Print the saved windows and tabs without the picker, with each known folder and recovery command, so a window can be rebuilt by hand if needed. Unknown folders show `(folder unknown)` and have no recovery command; JSON contains null cwd. |
| `tsr doctor` | Check what tsr depends on (section 7). |

Plus `tsr --help` and `tsr --version`. Exit codes: `0` success, `1` runtime error, `2` usage error, `3` precondition not met (not in Ghostty, Ghostty too old, Automation denied, no snapshot), each with a one-line fix.

`save --yes` bypasses only the stale-snapshot replacement confirmation.

### What `save` prints

```text
Saved 3 windows · 9 tabs · 6 agent sessions   (Mon 10 Nov 18:42)

Window 1 · api
  1  Claude  refactor auth middleware      ~/projects/api          exact
  2  Codex   add rate limiter tests        ~/projects/api          likely
  3  shell                                 ~/projects/api/infra
Window 2 · web
  1  pi      landing page copy             ~/projects/web          likely
  2  Claude  (session not identified)      ~/projects/web          picker
…
```

The last column says how the session was identified (section 5): **exact** (validated live process metadata, or the agent process has that transcript open), **likely** (from the command line or recent activity), or **picker** (agent found, session not identified; restore opens that agent's own picker for that folder). Anything other than "exact" is the user's cue to check before restarting. Restore retains command-line likely resumes and single-pane recent-transcript likely resumes; multiple recent-transcript panes in the same agent/folder group are downgraded to `ambiguous · opens <agent>'s picker` (section 6). These are derived display/execution rules, not new snapshot confidence values.

When a pane's folder is unknown, save prints `(folder unknown)` for that pane,
keeps the rest of the snapshot, and ends its summary with a warning naming the
affected window and tab and stating it can't be restored, for example:
`1 tab saved without a folder: window 2, tab 3 (it can't be restored)`. Save
still fails and preserves snapshots for all other capture errors.

### UX rules

1. **One snapshot, one backup.** `save` moves the current snapshot to the backup slot before writing. If the current snapshot is from before the last boot, `save` asks for confirmation before replacing it, so a mistaken `save` after the restart can't push the real snapshot out of both slots.
2. **Windows are the unit.** Every view groups tabs under their saved window, in saved order. A window is labeled by its main project (section 4).
3. **Restore targets the window tsr runs in.** The first selected tab runs in the current tab; the others open as new tabs after it, in saved order. Restoring into a window that already has other tabs is allowed; tabs are appended.
4. **Picking means resuming.** Selected agent tabs start their resume command immediately. Plain shell tabs open in their folder.
5. **Verified duplicates are blocked; uncertainty is shown.** Restore never duplicates a session it can verify as running; for sessions it can't verify, it says so before you pick them. Exact live identities running in Ghostty and confirmed tsr-launched processes are blocked. Pending tsr reservations are temporarily blocked. Unidentified live processes produce `may already be open` on the rows covered by the agent's scope rule (section 6), including processes located `outside Ghostty`. No unverified row is described as "not running".
6. **Missing or unknown folders are disabled**, with the reason. A null-cwd row
   is shown as `folder unknown` and cannot be selected.
7. **No surprises after restart.** Everything `restore` will do is visible in the picker before Enter: which tabs, in what order, and why any are disabled.
8. **Never block on tsr.** If tsr fails for any reason, Ghostty and the agents work exactly as without it.

## 4. Snapshot format and storage

A snapshot is one JSON file describing windows → tabs → panes, written in terminal-neutral terms so other terminal adapters can produce and consume it later (section 8).

### Files

| Path | Contents |
| --- | --- |
| `~/.local/state/tsr/snapshot.json` | Current snapshot |
| `~/.local/state/tsr/snapshot.prev.json` | Previous snapshot (backup) |
| `~/.local/state/tsr/restored.json` | tsr-owned reservation/lifecycle records: identity, state, terminal ID and reserved-at timestamp, plus observed process identity when confirmed; see below. No 60-second pruning rule. This file may evolve independently of the snapshot schema. |
| `~/.local/state/tsr/restore.lock` | Exclusive `fcntl.flock` used only for the bounded recheck-and-reserve critical section and coordinated reservation updates; never held while the picker waits or while tabs launch |
| `~/.local/state/tsr/tsr.log` | Rotating log, 1 MB × 3 |

Respect `XDG_STATE_HOME`. Directory mode `0700`, files `0600`. Every write goes to a temp file in the same directory, then `os.replace`, so a crash never leaves a half-written snapshot. After capture, stage the new snapshot, a copy of current for the backup, and the old backup for rollback. Install the backup before replacing current; leave current in place until its final atomic replacement. A failed final replacement rolls back the backup. This avoids a missing-current window; if rollback also fails, retain the old backup's secure temporary file and report its location.

### Schema (formats 1 and 2)

Format 1 remains readable unchanged: every pane `cwd` must be a nonempty string.
Format 2 has the same schema, except `cwd` may be null when the folder cannot be
determined reliably. All new saves write format 2. Readers accept formats 1 and
2 without migrating or rewriting either file; current and backup slots may use
different formats. A format-3 snapshot is refused with the upgrade message.
Nothing else changes between formats, and `restored.json` remains independent.

```json
{
  "format": 2,
  "id": "0196f0a2-7c1e-7d3a-9b2f-1c2d3e4f5a6b",
  "saved_at": "2026-11-10T17:42:05Z",
  "boot_time": 1762700000,
  "host": "example-mac",
  "tsr_version": "0.2.0",
  "terminal": { "app": "ghostty", "version": "1.4.0" },
  "windows": [
    {
      "index": 1,
      "label": "api",
      "terminal_window_id": "…",
      "tabs": [
        {
          "index": 1,
          "title": "claude",
          "panes": [
            {
              "terminal_id": "8C1F…",
              "tty": "s003",
              "cwd": "/Users/USER/projects/api",
              "agent": {
                "name": "claude",
                "pid": 41233,
                "command_text": "claude",
                "argv": null,
                "session_id": "00000000-0000-4000-8000-000000000001",
                "session_file": "/Users/USER/.claude/projects/PROJECT/00000000-0000-4000-8000-000000000001.jsonl",
                "confidence": "exact",
                "method": "live-metadata",
                "first_prompt": "refactor auth middleware",
                "git_branch": "main",
                "resume_argv": ["claude", "--resume", "00000000-0000-4000-8000-000000000001"]
              }
            }
          ]
        }
      ]
    }
  ]
}
```

Rules:

- `agent` is `null` for a plain shell pane.
- In format 2, `cwd` is an absolute path or null. Null means unknown and is
  never replaced by another directory. Format 1 rejects null cwd.
- `command_text` is the process display text captured from ps. `argv` is null
  unless an independent source supplies actual argument boundaries; never claim
  `shlex.split(command_text)` recovers the original vector (F38). Identification
  inspects validated command-text patterns. Restore constructs a validated
  canonical adapter command; it never executes raw process text or arbitrary
  saved executable/flags.
- `confidence` is `exact`, `likely` or `picker`; `method` is `live-metadata`, `argv`, `open-file`, `recent-transcript` or `none` (section 5).
- `resume_argv` records the command computed at save time for inspection/manual
  recovery. Restore validates the reference and constructs the adapter's canonical
  resume command, or downgrades to its picker. Picker commands are
  `["claude", "--resume"]`, `["codex", "resume"]`, `["pi", "--resume"]`.
  Neither validation nor ambiguity downgrades rewrite the snapshot.
- For pi, identified resume commands use the validated absolute session-file path, not a bare ID that can prompt to fork in another folder (F16, F39).
- `first_prompt` is single-line, at most 200 characters. `boot_time` is the seconds field of kern.boottime, read through the validated native API (F42), equivalent to the F21 command.
- Readers refuse a snapshot whose `format` is newer than they understand, with a message to upgrade tsr.

### Restore reservations (separate from the snapshot)

`restored.json` is a tsr-owned JSON list. Each reservation records a unique
reservation ID, `agent`, `session_id` and/or `session_file` when known, the saved
pane identity for a picker/plain row, `state`, `terminal_id` (null until
creation returns it for a new tab), and `reserved_at` as UTC Unix seconds. The current-tab
reservation starts with its known terminal ID. Capture may add the normalized
tty and the observed PID, process start and boot identity needed to reject stale
or reused processes. None of these fields is added to the snapshot schema.
Picker reservations contain the agent and saved row key but no guessed session
ID/file: the selected conversation is not known until the agent's picker is used.

State starts as `reserved` (shown as `starting`). On a subsequent live capture,
a uniquely matching agent process under the confirmation rule below changes it
to `running` (shown as `running · restored by tsr`).
Keep blocking while the recorded process identity remains live; elapsed time
alone does not expire a confirmed running record. On a later check, a pending
record with no matching process and no renewal for two minutes lapses; its row becomes selectable with
`restored earlier, not seen running`. That label is an observation, not proof of
absence; retain any applicable `may already be open` warning.

Creating a tab or waiting for its shell failing releases its reservation
immediately. Known input/exec launch failures also release the affected
reservation. A previously confirmed process that is no longer live no longer
blocks the row. Keep enough lapse history to display the specified label;
retain lapsed labels for up to seven days. Never age-prune confirmed live rows.
Read-modify-write updates use the same bounded state lock and private atomic
writes. The two-minute rule and states are product decisions; S16 measured the
underlying Codex association and exit evidence, not a reservation implementation.

F50–F51 rule out strict fractional start-after-reservation comparisons. Use this
confirmation rule:
for the current tab, record tsr's pre-exec PID/start and known terminal/tty, then
require that recorded live process identity (or a matching live descendant)
and exactly one expected agent on the tty after handoff. For a new tab, require
its returned terminal ID in the current layout and a matching agent on its tty;
the second-resolution ps start must be at least `floor(reserved_at)`. The
ambiguous fractional second is accepted only together with the newly created
terminal association, never from a cached tty name alone. Store confirmed
PID/start/tty and boot identity; reject changed identities or a different boot.
No exact session ID is inferred from process association alone.

Before typing into a new agent tab, readiness polling records that created
terminal's shell PID/start/tty into the reservation as well. The ps-only recheck
can then validate a matching live agent descendant even if its picker layout
predates the tab. A saved tty without the matching process identity is not
enough to confirm an otherwise unknown terminal.

At each launch, renew still-valid pending reservations under the short lock so
a long sequential batch does not expire its remaining rows. Never revive a lost,
lapsed or already expired reservation; fail/release that row and require retry.
Do not renew records for already launched tabs. Plain shells reserve their saved
row during the batch as well, so concurrent shell selections are coordinated;
without an agent identity those records follow the unconfirmed lapse rule.

S16 measured a shell-level Codex exec, not production parent-shell return.
Broader real-agent acceptance remains listed in [v1.md](v1.md).

### Window labels

For each window, find the git repository root of each known pane `cwd` by walking up to a directory containing `.git` (no subprocess). Ignore null cwd panes. The label is the basename of the most common root, ties broken by the first tab's. With no repositories, use the basename of the most common known `cwd`; when all panes have null cwd or there are no panes, use `Window <n>`. If two windows have the same label, append a disambiguating suffix in window order.

## 5. Save: how capture works

`tsr save` makes exactly three external calls, one each to `osascript`, `ps` and `lsof`, then reads a few transcript files. Budget: 3 s for 10 windows and 50 tabs.

### Steps

1. **Preconditions.** The layout script first tests `application "Ghostty" is running` (doesn't launch Ghostty), then gets its version before reading 1.4 properties. Below 1.4 → exit 3 (F9). Automation error `-1743` → exit 3 with the System Settings path (F5). These checks share the one layout subprocess; boot time uses the read-only native API (F42).
2. **Layout: one AppleScript call.** Iterate `windows` → bulk tab properties → bulk terminal properties (F44) and return one line per terminal with ASCII-9-separated fields: window index, window `id`, tab `index`, tab `id`, tab `name`, terminal `id`, `tty`, `working directory`, `pid` (F1, F9). Use ASCII character 9 explicitly because Ghostty also defines a `tab` class. Replace tabs and newlines inside names with spaces before joining. Window order is AppleScript's (front to back); keep it.
3. **Processes: one `ps` call.** `ps -A -ww -o pid=,ppid=,tty=,lstart=,args=` (F21). Run ps with `LC_ALL=C TZ=UTC` and lsof with `LC_ALL=C`; lstart is the five tokens after tty. UTC permits comparison with Claude's registry `procStart` (F32). Normalize tty names by removing a `/dev/` prefix and then a leading `tty` (`/dev/ttys003` and `ttys003` → `s003`; F23).
4. **Find each pane's agent.** For each terminal, take the processes on its tty. Ask each agent adapter whether a process's raw `command_text` identifies its agent (section 8); do not reconstruct argv by shell parsing (F38). If several match on one tty, take the outermost (the one with no matching ancestor). No match → plain shell pane; whatever else runs there (an editor, a dev server) is not restored.
5. **Folders and open files: one `lsof` call** over all agent PIDs and their descendants (a launcher, such as a Node wrapper, may leave the transcript to a child process), plus terminal foreground PIDs: `lsof -n -P -a -p <pid,pid,…> -Fpfn` (F43). Read each agent's working directory from its own `fcwd` entry, and the open files of the agent and its descendants. Plain panes prefer their foreground process's readable lsof cwd, then Ghostty's getter: the getter can be empty (F41) or nonempty but stale after exec (F53). Decode the measured lsof C/hex/backslash forms (F54). If neither source yields a reliable absolute folder, including ambiguous caret/control representations, record null cwd; never substitute tsr's cwd or HOME. Preserve the rest of the capture and warn in the save summary.
6. **Identify sessions** with the strategies below, tried in order per agent pane; the first that succeeds wins.
7. **Describe sessions.** Read the first prompt and branch from the identified transcript (adapter `describe`, section 8). Read at most the first 200 lines. Claude: the first line with type user whose content is text not starting with < and isn't a tool result; skip metadata-only records (F13). pi: the first line with type message and message.role user, reading text blocks (F30). Codex: `response_item` with `payload.type=message`, `payload.role=user`, and `input_text` text blocks; skip environment-context text starting with < (F30). Use null when the prompt or branch is unclear.
8. **Write and print.** Compute labels (section 4), move the old snapshot to the backup slot (asking first if it's from before the last boot, section 3), write the new one, print the summary (section 3). No Ghostty windows → exit 3 "nothing to save" and leave existing snapshots untouched.

### Session identification strategies

| Order | Method | How | Confidence |
| --- | --- | --- | --- |
| 0 | `live-metadata` | Claude: read `~/.claude/sessions/<pid>.json`; require its PID and filename to match the live matched Claude process, and its `procStart` to match that process's UTC ps start after whitespace normalization. Validate session ID and resolve its top-level transcript without decoding project paths. Require the transcript to exist and its recorded session ID to agree. Malformed, missing or mismatched records fall through. Do not read peer-key files or save peer tokens, socket paths or names. Codex/pi have no verified equivalent yet. | exact (F32–F33) |
| 1 | `open-file` | The agent process has its transcript open: a file matching the adapter's transcript pattern among the open files of the agent or its descendants (for Claude, only a top-level `~/.claude/projects/*/<uuid>.jsonl`, not files in subfolders). The S2 samples had no such open files; retain this strategy opportunistically, without promising exact identification (F24). | exact |
| 2 | `argv` | The command line names the session: claude `--resume <id>`, `-r <id>` or `--session-id <id>`; codex `resume <id>`; pi `--session <path\|id>`. Only "likely", because an in-session `/clear`, `/new` or `/resume` changes the session without changing the command line. | likely |
| 3 | `recent-transcript` | Group still-unidentified panes by agent and folder (N panes). Candidates: that agent's transcripts whose recorded `cwd` equals the folder (F13, F15, F16), modified after the earliest of those processes started, and not already claimed. Take the N most recently modified candidates (fewer if there aren't N) and pair them with processes in order: earliest process start with earliest first timestamp in the transcript. Panes left without a candidate fall through to none. Compare folders after os.path.realpath on both sides. | likely |
| 4 | `none` | Nothing matched. Restore runs the agent's own folder-scoped picker (F12, F14, F16). | picker |

Never decode Claude's project directory names (F13): find candidates by reading the `cwd` field inside transcripts, limited to files modified since the earliest relevant process start. Check modification times with os.stat first and open only files that pass; for Codex, only look in date folders on or after the earliest start date.

S2's sampled agents did not keep transcripts open (F24). S11 supplies an exact
live-metadata route for the sampled Claude versions (F32–F33); Codex and pi still
fall through to `likely` or `picker`, and `doctor` must show that limitation.
An idle resumed Codex or pi transcript can predate the process start; do not loosen the
mtime cutoff and call that exact. Fall through to argv or picker.

Capture keeps storing these existing methods, confidence values and resume argv
without schema changes. Restore derives ambiguity by counting saved
`recent-transcript` panes with the same agent and canonical folder: more than
one means the paired rows open the agent's own picker; one retains a likely
resume (section 6). This downgrade never promotes confidence or claims that
ordering proved the conversation identity. Format 1 does not record how many
candidate transcripts were examined; a singleton saved group cannot establish
that only one candidate existed. The specified snapshot-count rule is the
operational distinction; "one candidate" is not a new claim of verified evidence.

### `--restart`

S6 has identified a candidate mechanism without executing it (F27). Runtime
permission and quit behavior are unverified. The current fallback is to omit
`--restart` from the implemented CLI and print "Saved. Restart when ready."
after save. The original proposed `--restart` remains deferred; do not
implement it from dictionary evidence alone or run it during M0.

## 6. Restore: picker, planner, executor

`tsr restore` runs in a Ghostty tab, shows the saved windows, and rebuilds the user's selection into the current window. It has three parts with a clean boundary between them: the **picker** produces a selection, the **planner** turns it into steps, the **executor** carries them out.

### Preconditions

1. Load the snapshot (`--backup` → the previous one). None → exit 3 "nothing saved yet".
2. Find the current terminal: `os.ttyname(0)`, normalized (section 5), matched against the layout call's `tty` values. Not found → exit 3 "run tsr restore inside a Ghostty window". Keep its window `id` and terminal `id`.
3. Capture the live state with save's steps 2 to 6 (section 5), without saving a snapshot. Retain the system-wide ps observations as well as the Ghostty-associated panes. Use exact live identity for blocking; unidentifiable processes supply warnings, not evidence of absence. Read tsr's reservation records and reconcile them with the captured terminal ttys and process identities (section 4).
4. The picker holds no lock. On Enter, perform the bounded recheck-and-reserve operation below before launching anything. v1 covers the current user's sessions running in Ghostty plus sessions tsr restored itself. Agent processes in the same ps output that cannot be associated with Ghostty are treated as unidentified live processes located `outside Ghostty`; apply the warning scope below, without another call or a promise to identify other terminals.

### Availability of each saved pane

| State | Shown as | Selectable |
| --- | --- | --- |
| Session matches an exact live identity running in Ghostty (same agent and session ID or file; currently validated Claude live-metadata, F32–F33) | `running · window 2, tab 1` | No |
| A tsr reservation has a confirmed matching agent process whose recorded process identity is still live | `running · restored by tsr` | No |
| A tsr reservation is awaiting a matching process, for at most two minutes | `starting` | No |
| Folder no longer exists | `folder missing` | No |
| Folder unknown (format 2 null cwd) | `folder unknown` | No |
| Unidentified live process covers this row under the agent's warning scope | `may already be open` (include `outside Ghostty` when applicable), plus its resume/picker label below | Yes, unless independently blocked |
| Confidence `likely`, method `recent-transcript`, more than one such saved pane in the same agent/folder group | `ambiguous · opens <agent>'s picker` | Yes, unless independently blocked |
| Confidence `picker` | `opens <agent>'s picker` | Yes, unless independently blocked |
| Confidence `likely`, method `argv`, or a singleton saved recent-transcript group | `likely` | Yes, unless independently blocked |
| Confidence `exact`, with no block | No confidence label; show `may already be open` if applicable | Yes, unless independently blocked |
| Reservation lapsed without observing its agent | `restored earlier, not seen running`, plus any applicable warning | Yes, unless independently blocked |
| Plain shell | folder only | Yes |

Blocking rules take precedence. Warning scope applies to rows whose current
running status cannot be verified, even when the snapshot's saved confidence
was exact. Unverified cases always say `may already be open`; neither an empty
match nor a failed check is shown as `not running` or safe.

An unidentified live process means a matched agent process without a verified
current session association, including likely/picker capture results. For the
current user, retain these from the existing system-wide ps call even when their
tty is absent from the Ghostty layout. Collapse wrappers with the same outermost
matching rule used in capture; exclude known daemon/server command forms from
interactive-agent matches. This supplies uncertainty warnings only; proper
support for other terminals remains future adapter work (section 8).

| Agent resume scope | Rows warned for an unidentified live process |
| --- | --- |
| Claude, original folder or its worktrees (F12) | That agent's unverified saved rows in the same canonical folder or a verified related worktree. Use recorded cwd where available; an unavailable cwd is uncertainty, not evidence that folders differ. |
| pi, absolute session-file path (F16, F39) | All of that agent's unverified saved rows, regardless of folder |
| Codex, explicit-ID resume supports another folder (F49) | All of that agent's unverified saved rows, regardless of folder; the bare picker's initial Cwd filter (F48) does not narrow this warning scope |

Use the documented read-only Git gitdir/commondir relationship (F52) plus the
folder's relative path within each worktree to compare corresponding folders.
Do not merge unrelated sibling folders just because they share a repository.
When cwd is unavailable in the restricted recheck, retain matching-process
capture evidence where available; otherwise broaden Claude uncertainty instead
of treating missing evidence as a proven scope exclusion.

Do not implement or use the Codex daemon loaded-ID query as a blocking rule.
Its disposition remains an owner decision in section 14; recommendation:
warning (`loaded in Codex server`). F36 is evidence for one daemon's loaded set,
not complete live frontend identity or global absence.

### Recheck and reserve on Enter

1. Take an exclusive `fcntl.flock` on `restore.lock` in tsr's private state
   directory. Use a bounded wait of three seconds; on timeout exit with
   `another restore is in progress`. No lock is held during picker interaction.
2. Under the lock, recheck availability of the selected rows only. Read one
   system-wide UTC ps sample, validated Claude live-metadata and `restored.json`;
   do not call AppleScript or lsof. Identity suffices for blocking; fresh window
   location is not required. Use already captured terminal/tty associations and
   confirmed reservation process identities where available; do not invent a
   new terminal association. Reuse the captured folder evidence for warnings
   when the restricted recheck cannot establish a fresh folder. F45 measured the
   read path at 148.186 ms median; this is not a completed M2 lock/write benchmark.
3. If a selected row is now blocked, or its displayed availability/reason has
   changed, release the lock and return to the picker with the new reason.
   Launch nothing until the user reviews and selects again. Reconciliation may
   update reservation records during this Enter check, even if no launch follows.
4. Otherwise write reservations for all selected agent sessions/picker/plain rows
   atomically while still holding the lock, then release it before launch.
   Known identities use agent plus ID/file; unidentified rows use the saved pane
   identity so two restores cannot reserve the same saved picker/plain row. This does
   not identify which session the user will choose inside the agent's picker.

The recheck catches exact Claude identity changes where runtime metadata is
available. Sessions independently launched by the user while the picker is open
are outside the guarantee except where that recheck catches them. A process may
also start after the recheck; tsr's lock coordinates tsr restores, not independent
agent launchers. No external agent-wide lock is assumed.

### Picker

Plain `curses` on the terminal tsr runs in; no other UI dependency.

```text
Saved Mon 10 Nov 18:42 · restoring into this window

 ▾ api                      window 1 · 3 of 4 available
     [x] Claude  refactor auth middleware      ~/projects/api
     [ ] Codex   add rate limiter tests        ~/projects/api        likely
      ─  pi      migrate to pg 17              running · restored by tsr
     [ ] shell                                 ~/projects/api/infra
 ▸ web                      window 2 · 2 of 2 available
 ▸ notes                    window 3 · 0 of 1 available

 ↑↓ move · Space select · Enter restore · ←→ collapse/expand · q quit
```

- Windows are listed in saved order. The first window with available tabs starts expanded; the rest start collapsed. The cursor starts on that window's row.
- **Space** on a tab toggles it; on a window, toggles all its available tabs. **Enter** restores the selection, which may span several saved windows (kept in saved window order, then tab order); with nothing selected, it restores the highlighted row (a window row means all its available tabs). **q** or **Esc** launches nothing; before Enter it also leaves state untouched. `j`/`k` also move.
- Disabled rows are dimmed, can't be selected, and always say why.
- Columns: agent, first prompt (or `(no prompt)`), folder relative to `~`, and a note. Truncate the prompt first when narrow.
- Split panes appear as separate rows in their saved order and are restored as separate tabs (F11).
- Warning notes and ambiguity downgrades are visible before Enter. A warning
  leaves a row selectable and never implies it was verified safe.
- Shorten the prompt first, then the folder, to keep the reason visible. Below
  40 columns or 6 rows, require resizing or cancellation before selection.

### Planner

Input: the selected panes in saved order, the current window `id`, the current terminal `id`. Output: an ordered list of steps, which the executor carries out.

1. **Current tab:** the first selected pane, in saved order, takes the current tab, whether it's an agent or a plain shell. This keeps the window's tab order exactly as saved.
2. **New tabs:** every other selected pane becomes a new tab in the current window, in saved order.
3. **Command per pane:** validate commands, then use `shlex.join(resume_argv)` for exact and unambiguous likely agent panes. Command-line likely rows resume the named session; a singleton saved recent-transcript group retains its likely resume. When multiple saved `recent-transcript` panes share an agent and canonical folder, downgrade each to the agent's own folder-scoped picker. Confidence `picker` also opens that picker. Derive this from the whole snapshot, not just selected rows, so selecting one ambiguous pane does not erase its ambiguity. Do not rewrite the saved argv or confidence. Plain shells get no command in a new tab; in the current tab, a plain shell pane means changing to its folder and starting the user's login shell (`$SHELL -l`).

The snapshot-count derivation preserves saved snapshots; it cannot recover the original
candidate count (section 5). A singleton is an unambiguous group for this product
rule, still labeled likely, not evidence of exact identity.
Reject a selection that would launch the same named session twice before
reserving or opening any tabs. Picker rows have no known launch identity and
remain subject to the uncertainty policy.

### Executor

1. Launch only after the selected rows' recheck-and-reserve transaction succeeds.
   For each new tab, in order:
   - Run one AppleScript call: `new surface configuration` with `initial working directory` set to the pane's folder (F3), then `new tab in` the current window (referenced in AppleScript as first window whose id is "\<window id>", i.e. by its `id`) `with configuration` (F2); return the new tab's focused terminal `id`.
   - For an agent pane, as soon as creation returns, add the new terminal ID to its reservation in
     `restored.json` using the short state-update lock; do not hold the lock
     while polling or typing.
   - For agent panes: poll that terminal's `pid` (F9) every 100 ms until it's a shell: the basename of its executable, without a leading -, is the basename of $SHELL or one of zsh, bash, fish, sh (checked with `ps`); wait another 200 ms for settling, then `input text` the command and `send key "enter"` to it (F2, F4, F26). Include settling in the 3 s readiness budget; a shell not ready in time fails that tab. The tested fallback improved marker delivery from 8/10 to 10/10 (F26).
   - A subsequent live capture checks terminal tty, matching agent and recorded
     process identity; record its PID/start evidence (section 4). F50 confirms
     the measured Codex association; apply section 4's adopted time-resolution
     and exec rules instead of the disproven strict fractional comparison.
2. If creating a tab or waiting for its shell fails, remove that reservation
   immediately, record the failure and continue with the rest. Known input
   failures also release the reservation. A missing agent that was not directly
   observed to fail follows the two-minute lapse rule. Do not erase reservations
   for other selected rows when one fails.
3. Print one summary line distinguishing completed new tabs from the pending
   handoff: `Restored 3 new tabs · handing off current tab · 1 failed (web/2:
   <reason>)`. A failed current handoff prints its own error and releases its
   reservation.
4. **Current tab last:** for an agent pane, its reservation already contains the
   current terminal ID; apply section 4's adopted CHECK 3 evidence rule for
   process confirmation. `focus` the current terminal, `os.chdir` to the pane's folder, and
   `os.execvp` the planned resume/picker command (or $SHELL -l for a plain shell
   pane). If that fails, release its reservation if present, print the error and exit 1.
   The command replaces tsr in this tab. When the user later quits it, the tab's
   shell is back in the folder where they ran `tsr restore`.

All AppleScript strings are escaped (section 9). The executor never types into any terminal other than ones it just created.

## 7. Installation and doctor

tsr has no install command of its own: uv installs, upgrades and removes it, and provides Python 3.14. tsr lives in the shared `million-views/tools` repository, in its own `tsr/` folder.

### Installing, upgrading, removing

| Job | Command |
| --- | --- |
| Prerequisite (once) | `sudo port install uv` (F22) |
| Install a release | `uv tool install "git+ssh://git@github.com/million-views/tools@tsr-v0.2.0#subdirectory=tsr"` |
| Move to another release | the same command with the new tag and `--force` |
| Uninstall | `uv tool uninstall tsr` (snapshots stay; remove them with `rm -rf ~/.local/state/tsr`) |
| Run from a checkout, for development | `uv run tsr …` inside `tsr/` |

uv runs as the user, so it fetches the private repository with the user's own git credentials. S8 confirmed uv-managed Python 3.14 with `curses` locally (F29); validate the corrected private git subdirectory/tag install and installed PATH behavior before relying on it.

### Packaging

`tsr/pyproject.toml` declares `name = "tsr"`, a `version`, `requires-python = ">=3.14"`, `dependencies = []`, the entry point `[project.scripts] tsr = "tsr:main"`, and the `uv_build` build backend. The whole program is `tsr/src/tsr/__init__.py`. Releases are git tags of the form `tsr-v<version>`, and the tag must match the version in `pyproject.toml`.

### First run

- `save` creates `~/.local/state/tsr` (mode `0700`) if it doesn't exist.
- When the state directory is absent, save/restore/doctor print a heads-up before
  contacting Ghostty. macOS may ask the requesting app for Automation permission.
  Whether a prompt appears and which app it names remain S5 measurements; do not
  promise it first appears during save if doctor was run earlier.

### `tsr doctor`

One line per check: `✓`, `⚠` (works, with a caveat) or `✗` (broken), with the fix after each `⚠` or `✗`. Exit 0 when there are no `✗`.

| Check | Result | Fix shown |
| --- | --- | --- |
| Python 3.14+ | ✗ if older | Reinstall with uv |
| `curses` importable | ✗ if not; the picker can't run (spike S8) | Reinstall with a uv-managed Python 3.14 |
| `tsr` on `PATH` | ⚠ if the first `tsr` on `PATH` isn't the uv-installed one | Remove the other copy, or put uv's tool directory first |
| Ghostty running | ⚠ if not | Open Ghostty and re-run |
| Ghostty 1.4+ | ✗ if older (F9) | Update Ghostty |
| Automation permission | ✗ on `-1743` (F5) | System Settings → Privacy & Security → Automation → allow the app that runs tsr (usually Ghostty itself) to control Ghostty (spike S5) |
| Session identification | Runs a capture without saving and reports how many running sessions are `exact`, `likely` and `picker`; ⚠ if any aren't `exact` | Lists those tabs, so the user knows which to check before restarting |
| `window-save-state` | ⚠ when effective value is uninspected; never guess it | If `always` (F6), Ghostty may reopen old shells too. Inspect the setting yourself; tsr does not change it |
| Snapshot | Shows age and counts; ⚠ if there is none | `tsr save` before restarting |

## 8. Architecture and extension points

v1 ships one terminal adapter (Ghostty on macOS) and three agent adapters, but the core never names Ghostty or a specific agent. New terminals, agents and identification methods are added as adapters without changing commands or the snapshot format.

```text
           ┌──────────── cli: save · restore · list · doctor ──────────────────────────────────┐
           │                                                                                   │
  capture ─┼─► TerminalAdapter.list_layout()      restore ─► picker ─► planner ─► executor     │
           │   AgentAdapter.matches()/describe()                 ▲          │          │       │
           │   identification strategies [...]                   │          ▼          ▼       │
           │            │                                   snapshot   Step list   TerminalAdapter
           │            ▼                                     store                 .open_tab() …
           └──────► snapshot store (JSON, formats 1/2) ◄────────┘                                │
                                                                                                   │
  Runner (every subprocess call: osascript, ps, lsof) ─── replaced by fakes in tests ─────────────┘
```

The program is one module, organized in this order so it can be split later: `runner`, `model` (dataclasses), `store`, `macos` (ps, lsof, boot time), `terminal_ghostty_mac`, `agent_claude`, `agent_codex`, `agent_pi`, `identify`, `capture`, `picker`, `planner`, `executor`, `doctor`, `cli`.

### Interfaces

```python
class TerminalAdapter(Protocol):
    name: str                                   # "ghostty"
    version: str
    capabilities: frozenset[str]                # {"list_layout", "open_tab", "type_into"}
    shell_processes: dict[str, Process]          # readiness records terminal ID → verified shell identity
    def list_layout(self) -> tuple[Window, ...]: ...  # includes running/version/permission validation; core locates tty in this captured layout
    def open_tab(self, window_id: str, cwd: str) -> str: ...  # returns new terminal_id
    def wait_for_shell(self, terminal_id: str, timeout: float) -> bool: ...
    def type_and_run(self, terminal_id: str, text: str) -> None: ...
    def focus(self, terminal_id: str) -> None: ...

class AgentAdapter(Protocol):
    name: str                                   # "claude" | "codex" | "pi"
    display: str                                # "Claude"
    resume_scope: str                           # "folder" or "global" warning scope
    def matches(self, command_text: str) -> bool: ...
    def session_from_runtime(self, pid: int, start_utc: str) -> SessionRef | None: ...  # validated read-only live metadata; None if unsupported
    def session_from_argv(self, command_text: str) -> SessionRef | None: ...  # method name means command-line evidence, not reconstructed argv
    def session_from_open_files(self, paths: tuple[str, ...]) -> SessionRef | None: ...
    def candidates(self, cwd: str, since: float) -> tuple[Transcript, ...]: ...
    def describe(self, ref: SessionRef) -> Description: ...      # first_prompt, git_branch
    def resume_argv(self, ref: SessionRef | None) -> tuple[str, ...]: ...  # None → folder picker
    def valid_ref(self, ref: SessionRef) -> bool: ...  # validate before executing; no guessed command reconstruction
    def runtime_cwd(self, pid: int, start_utc: str) -> str | None: ...  # read-only validated warning scope evidence
```

Capture tries live metadata, open files and command-line evidence in order,
delegating reads to the agent adapter. Pure identification then pairs remaining
recent transcripts or falls back to picker. Frozen dataclasses represent the
snapshot, process evidence, availability, picker state and planned steps.
Restore keeps both the saved reference and the planned launch reference; an
ambiguity downgrade clears the latter without changing the snapshot.

### How future scenarios plug in

| Scenario | Change needed |
| --- | --- |
| A new agent | One `AgentAdapter` plus fixture transcripts |
| A new terminal (iTerm2, WezTerm, kitty, tmux, cmux…) | One `TerminalAdapter` supplying app-owned layout and readiness evidence. Do not identify the app from Ghostty-like environment variables (F10). |
| Restore into a different terminal than the one saved from | None in the planner; the executor uses the target terminal's adapter |
| Exact session IDs from agent hooks | One more identification strategy that reads hook records, inserted first |
| Scheduled saving | A scheduler that runs `tsr save`; `save` itself doesn't change |
| Snapshot history | Store keeps more files; `restore` and `list` take a snapshot argument |

Subprocess/native-platform calls go through `Runner`. Adapters own agent file
reads and per-invocation caches/readiness records; they have no global state.

## 9. Errors, security, performance

### Errors

- **Save never destroys a good snapshot.** Any failure during capture leaves `snapshot.json` and `snapshot.prev.json` untouched.
- **Partial capture is still saved.** If one pane's identification or description fails, record it with confidence `picker` and continue; the summary shows it.
- **Unknown folders do not fail capture.** Save writes format 2 with null cwd,
  shows `(folder unknown)` and warns that the row can't be restored. All other
  capture failures preserve both snapshots. Never invent a folder.
- **Restore continues past failures.** A failed tab is reported in the summary line; the remaining steps still run (section 6).
- **Reservations are not success claims.** Failed tab creation/shell readiness
  releases the affected reservation immediately. Unconfirmed launches lapse
  after two minutes; confirmed running records require a still-live process
  identity. Lock contention times out after three seconds with
  `another restore is in progress`.
- **Every `osascript` call has a timeout** (default 5 s; 15 s for the layout call with many windows). A timeout is reported as "Ghostty didn't answer" with the doctor hint.
- **Error messages are one line plus a fix**, never a traceback. Tracebacks go to `tsr.log`; `TSR_DEBUG=1` also prints them.

### Security and privacy

- Snapshots contain folder paths and first prompts. They stay in `~/.local/state/tsr` (`0700`/`0600`) and never leave the machine. tsr makes no network calls.
- **AppleScript strings:** escape backslash and double quote in every interpolated value. Never build scripts by unescaped concatenation.
- **Shell commands typed into tabs:** build with `shlex.join`. Validate before use: Claude and Codex session IDs must match `^[A-Za-z0-9._:-]{6,128}$`; pi session files must be absolute, end in `.jsonl`, and sit under a known pi sessions directory (F16). Anything else is downgraded to `picker`.
- **Typing** only goes to terminals the executor just created and has confirmed are at a shell (section 6).

### Performance

| Path | Budget |
| --- | --- |
| `tsr save`, 10 windows / 50 tabs | 3 s |
| `tsr restore` to picker shown | 3 s (it runs the same capture as save) |
| Each restored tab, from creation to command typed | 3 s max (shell wait) |
| `tsr list` | 300 ms |

Import `curses` only in the picker. Keep to one `osascript`, one `ps` and one `lsof` call per capture.

## 10. Acceptance criteria

Each is a test the implementation must pass on a real Mac with Ghostty 1.4. The reference setup for AC-2 onward: 3 windows, 9 tabs, including 2 Claude, 2 Codex and 1 pi session, one tab split into two panes, and at least two plain shell tabs.

### Install

- **AC-1** On a Mac with only MacPorts' `uv`, `uv tool install` from the repository tag installs tsr with Python 3.14, and `tsr doctor` then exits 0. The first `tsr save` shows the heads-up line and the Automation prompt.

### Save

- **AC-2** `tsr save` lists all 3 windows in front-to-back order with labels, their tabs in order, the split as two rows, and a confidence for every agent pane. It finishes within 3 s.
- **AC-3** Every session marked `exact` matches the session the agent itself reports (e.g. Claude's or pi's session info), checked by hand for all five.
- **AC-4** Saving twice leaves the first snapshot in the backup slot.
- **AC-5** With Ghostty not running, `save` exits 3 and leaves existing snapshots untouched.

### Restore

- **AC-6** After a restart, in a fresh Ghostty window, `tsr restore` shows the saved windows. Enter on window 1 makes the current tab take its first selected pane and opens the others after it in saved order. Exact, command-line likely and singleton recent-transcript likely rows execute their planned identified resume in the saved folder; likely rows retain that label. Ambiguous/picker rows follow AC-10, and plain shells open their folders. Where agent-reported identity is available, compare it with the saved identity; do not call a likely row exact because its command ran.
- **AC-7** Running `tsr restore` again straight away shows pending reservations as `starting` and confirmed live launched processes as `running · restored by tsr`; those rows cannot be selected. An unconfirmed reservation lapses after two minutes and becomes selectable with `restored earlier, not seen running`, retaining any uncertainty warning. A confirmed process still running after two minutes remains blocked. Exited or stale/reused process identities do not keep blocking indefinitely.
- **AC-8** In a second new window, restoring window 2 works the same way, independently of window 1.
- **AC-9** Quitting the agent in a restored new tab leaves that tab at a shell prompt in its folder.
- **AC-10** A pane saved with confidence `picker`, or downgraded because multiple saved recent-transcript panes share its agent/folder group, opens that agent's own picker in the right folder. Ambiguous rows show `ambiguous · opens <agent>'s picker`. Selecting only one row from the ambiguous group does not bypass the downgrade. Snapshot format, confidence and saved argv remain unchanged.
- **AC-11** A pane whose folder was deleted is shown as `folder missing` and can't be selected.
- **AC-12** `tsr restore --backup` restores from the previous snapshot.

### Failure handling and removal

- **AC-13** Run outside Ghostty (e.g. in Terminal.app), `tsr restore` exits 3 with "run tsr restore inside a Ghostty window".
- **AC-14** With Automation denied, every command that needs it exits 3 with the System Settings path, without a traceback.
- **AC-15** `uv tool uninstall tsr` removes the program and leaves the snapshots; reinstalling picks them up again.

### Scoped duplicate prevention and reservation lifecycle

- **AC-16** An exact live Claude identity running in Ghostty blocks its matching
  saved session, with its location/reason shown, even if the saved confidence
  was only likely. A stale argv naming another session is not exact evidence.
- **AC-17** An unidentified running Codex or pi process produces
  `may already be open` on exactly the unverified rows required by section 6's
  agent scope; it does not become a duplicate block. Codex (F49) and pi warnings
  are agent-wide. Verify the same warnings
  for processes located `outside Ghostty`, with that location included. Verify
  Claude's folder/worktree warning scope separately. No unverified row is labeled
  `not running` or safe.
- **AC-18** Two simultaneous restores selecting the same reference window
  (including its agent sessions) produce no duplicate tabs: only one reserves
  the selected sessions; the other rechecks under the lock and returns to the
  picker with the blocking reason, or exits after the bounded lock wait with
  `another restore is in progress`. The picker itself holds no lock.
- **AC-19** A failed tab creation or shell wait releases only that tab's
  reservation immediately; a later restore can select it. Other selected rows
  retain their records and continue launching. Verify known input/current-tab
  exec failure cleanup too.
- **AC-20** M2 reads unchanged M1 format-1 current and backup snapshots, derives
  ambiguity without adding schema fields, and leaves those files unchanged
  during restore.
- **AC-21** When a plain or agent pane has no reliable folder, save succeeds,
  writes format 2 with null cwd for that pane, preserves the rest of the snapshot
  and prints the unknown-folder summary warning. Listing shows `(folder unknown)`
  with no command; restore shows a disabled `folder unknown` row in saved
  position. Partial and all-null window labels ignore null panes and fall back
  to `Window <n>` when none have a known cwd.
- **AC-22** Readers accept format-1 and format-2 current/backup snapshots in
  either mixed-slot order without rewriting either file. Format 1 with null cwd
  is malformed; format 2 round-trips null cwd for plain and agent panes; format
  3 is refused with the upgrade message.

## 11. Testing plan

Tests use `unittest` only. Everything below runs in CI on macOS with Python 3.14, without Ghostty or real agents; the acceptance criteria (section 10) cover the real thing by hand.

### Seams

- **`Runner` fake:** maps a command (`osascript` script, `ps`, `lsof`) to canned output, records what was called, and can return errors (`-1743`, timeouts).
- **Fixtures:** real outputs captured during the spikes (section 12), anonymized: the AppleScript layout dump, `ps` and `lsof` output, and one transcript per agent.
- **Fake home:** a temp `HOME` with fixture transcripts under `.claude`, `.codex` and `.pi`.
- **Clock:** injectable wall clock for reservations/two-minute lapse and process
  start comparisons; monotonic clock for the bounded lock/shell waits.

### Suites

| Suite | Covers |
| --- | --- |
| Layout parsing | AppleScript dump → windows, tabs, panes; names with tabs and newlines; tty normalization (`ttys003` ↔ `s003`) |
| Agent detection | Each adapter's `matches` against captured `args`, including npm/node launchers and nested agents on one tty |
| Identification | Each strategy alone and in order: open file, argv (including after `/clear`), recent transcript with N panes and N, more or fewer candidates, fallback to picker |
| Describe | First prompt and branch per agent from fixture transcripts; system-tag lines skipped |
| Snapshot store | Atomic write, backup rotation, refusal of newer `format`, file modes |
| Labels | Repository detection, ties, duplicates suffixed |
| Availability | Exact live blocking, confirmed tsr-restored processes, pending/lapsed reservations, folder missing/unknown, picker/ambiguous/likely/plain rows, and scope-specific uncertainty including outside Ghostty |
| Planner | First selected pane to the current tab (agent or shell), the rest as new tabs in saved order, a selection spanning several windows |
| Executor | Script text per step (escaping), shell wait polling, continue after failure, reservation terminal-ID updates, final `exec` (patched), and immediate per-row cleanup on tab/shell/input/exec failure |
| Restore lock and lifecycle | Picker takes no lock; bounded exclusive flock; selected-row recheck without AppleScript/lsof; atomic reservations before launch; competing restores cannot reserve the same session; return to picker on newly blocked rows; pending to running via tty/agent/start evidence; live confirmed process persists beyond two minutes; lapse, exit, PID/tty reuse and failure cleanup; never infer session identity from picker-row reservation alone |
| Snapshot compatibility | Read format 1 and format 2 independently in either state slot; format 1 requires nonempty cwd; format 2 round-trips null cwd; reject format 3; do not rewrite snapshots during reads/restores; derive ambiguity from full groups |
| Picker | Selection logic driven without a real terminal: Space on window and tab, Enter with and without selection, disabled rows ignored |
| Doctor | Each doctor result, including the curses and PATH checks |

Transport tests cover escaping and command generation. Pure tests cover selection
and planning; filesystem/process contracts cover reservation and failure behavior.

## 12. Spikes to run first

These confirm things the design assumes but no source states outright. Run them on a real Mac before writing production code; each has a fallback already designed, so none blocks the build. Save the raw outputs as test fixtures and record results as rows in section 2.

| ID | Question | How to test | If the answer is no |
| --- | --- | --- | --- |
| S1 | What do AppleScript `tty` and `ps` print for the same terminal, and does normalizing both to the part after `tty` match them? | In a Ghostty 1.4 tab, compare `tty`, `ps -o tty= -p $$` and the AppleScript value | Match on the trailing device number instead |
| S2 | Does each agent keep its transcript file open while running? Does an idle resumed session's transcript get modified at startup? Save one transcript per agent as a fixture for describe. | `lsof -p <pid>` on running claude, codex and pi; check mtimes after resuming without typing | Without open files, sessions are `likely` at best; document it in `save`'s output and `doctor` |
| S3 | What do the agent processes look like in `ps` (native binary vs node script, wrapper plus child)? | Capture `ps -A -ww -o pid=,ppid=,tty=,args=` with each agent running, installed the usual way | Adjust each adapter's `matches` rule; outermost-match rule handles wrappers |
| S4 | Does typing wait correctly for a new tab's shell: is `pid` the shell soon after `new tab`, and does `input text` + `send key "enter"` then run the command reliably, including with slow shell startup (e.g. prompt frameworks that draw early)? | Open 10 tabs in a loop with a deliberately slow `.zshrc`. Cleanup follow-up: inspect the installed dictionary's `close <terminal>` command; after owner approval, verify probe window IDs, cwd and idle shell PIDs, close only their terminals, and verify removal. | Add a short settle delay after the shell appears; as a last resort use the surface configuration's `command` with `wait after command` |
| S5 | Which app does the Automation prompt name when `osascript` runs inside Ghostty and targets Ghostty, and does it appear once or per process? | Fresh macOS user account; install with uv, then run `tsr save` | Adjust the first-run heads-up line and doctor's fix text |
| S6 | What restart request mechanism is documented, and what remains unverified about no-root execution and apps' normal quit handling? | Inspect the installed System Events dictionary and document the candidate. Never execute a restart during M0. Record runtime permissions and quit behavior as unverified (F27). | Drop `--restart`; `save` prints "Saved. Restart when ready." |
| S7 | Does `new tab in <window>` add the tab at the end of that window, even when another window is in front, and is the window `id` stable while tsr runs? | Script with two windows, the target in the back | Activate the target window before creating tabs |
| S8 | Does `uv tool install "git+ssh://…/tools@<tag>#subdirectory=tsr"` install from the private repo at a tag, put `tsr` on PATH, and run it on a uv-managed Python 3.14 that includes `curses`? | On a Mac with only MacPorts' uv: install from a test tag, then `uv run --python 3.14 python -c "import curses"` | If curses is missing, require MacPorts' `python314` via `--python /opt/local/bin/python3.14`; if subdirectory installs fail, give tsr its own repo |
| S9 | What are the installed agents' bare picker flags and folder scope? | Inspect installed parser/entry-point source. Interactive checks must be owner-run or separately approved and must not be represented as passed from static source alone. | Keep uncertain picker behavior explicitly unverified; do not invent flags or scope. |
| S10 | Does ps preserve argv boundaries for whitespace/quotes, and does process-title rewriting hide launch arguments? | Capture a harmless Python child with known argv on this Mac and compare ps text with the known vector. Use the observed pi title rewrite as a separate agent-specific case; do not claim the Python child simulates an agent. | Treat ps as display text rather than an invertible argv serialization; avoid shlex reconstruction for ambiguous paths and fall through safely. |
| S11 | Is there a read-only source associating a live agent PID with its current session, including idle resume, multiple sessions in one folder and in-session switches? | Authorized follow-up: inspect local runtime metadata, installed source and read-only database schemas. Compare candidate mappings with known live PIDs and owner-supplied IDs. Do not invoke agents, alter storage/configuration, or switch sessions without desktop approval. Preview sanitized evidence before saving. | Retain likely/picker identification; do not claim a universal no-duplicates guarantee. Record per-agent gaps and propose the minimum design decision required. |
| S12 | Can stdlib ctypes read kern.boottime through macOS sysctlbyname without spawning a fourth subprocess? | Compare a read-only native call (new value null, length zero) with the F21 sysctl command on this Mac. Check returned structure size and seconds. | Report the platform precondition failure; do not silently hide another subprocess in AppleScript. |
| S13 | Can lsof numeric network/port output avoid resolution overhead without changing cwd/file-path capture? | Inspect installed help, then compare plain and numeric-output batch reads on the live captured PIDs. Measure timings and compare cwd and transcript paths. | Retain the original command and report measured performance without claiming the target passed. |
| S14 | Can bulk tab/terminal property reads reduce AppleScript latency without changing layout? | Compare the existing read-only layout script with bulk property records, preserving window/tab/terminal IDs, order, tty and cwd. Measure both on the same live layout. | Keep the validated existing traversal and disclose the measured timing. |
| S15 (complete) | Does explicit Codex resume cross folders, and what folder/worktree evidence supports warnings? | Owner-confirmed marker continuity passed for Codex 0.167.1 (F49); the Git relationship is documented in F52 and contract-tested. Missing-cwd rechecks retain or broaden uncertainty. | Codex warnings are agent-wide; missing folder evidence never proves availability. |
| S16 (CHECK 3 measured; strict rule answered no) | Does a tsr-created terminal's tty plus agent process start reliably confirm a reserved launch and its lifecycle, including the current-tab exec case? | Owner-approved two-tab probe: normal Codex launch in A, shell-level exec via a recording Python helper in B, owner quit, then read-only exit checks. Tty association and pre-exec PID/start continuity worked for Codex 0.167.1; both recorded identities disappeared on exit (F50). Strict start-after-reservation failed for exec and same-second timing (F51). The isolated probe shells initially lacked nvm PATH; temporary exports fixed only those tabs. B required Enter to close after owner quit. | The adopted confirmation rule and remaining limits are in section 4. This probe did not exercise M2 or its actual reservation file. Validate the normal tsr-under-parent-shell handoff, agent launcher variants and stale/reuse cases before claiming that broader coverage; keep existing format-1 snapshots readable. |
| S17 (complete) | Can the Ghostty cwd be stale after exec, and how does lsof encode paths? | Actual plain-shell restore/read-only follow-up found a stale nonempty getter and measured seven path cases (F53–F54). Foreground-cwd preference and decoding then produced all three expected folders. | Ambiguous caret/control forms yield null cwd and a save warning rather than an invented path. |

## 13. Milestones and definition of done

Current implementation: all four commands (`save`, `list`, `restore`, `doctor`)
are implemented. Thirty-eight contract tests pass after the format-2 change. The last live capture returned 12
windows / 65 tabs in 2.780 s; actual Ghostty picker/plain-shell restore and the
production local-tag install/reinstall/uninstall checks passed (F55–F56).
See [v1 verification](v1.md) for the acceptance matrix and precise limits.

Implementation status and remaining acceptance:

| Area | Implemented | Remaining verification or work |
| --- | --- | --- |
| M0 evidence | Local probes, source checks and anonymized fixtures through S17 | Fresh-account S5, private S8 after a release tag, pi's exact heading and broader real-agent acceptance |
| M1 capture/list | Private atomic snapshots/backups, adapters, labels, confidence, save/list; format-2 null cwd and mixed format readers; S17 foreground-cwd/encoding correction | Exact reference desktop and independent agent-reported IDs remain manual acceptance |
| M2 restore | Availability, curses picker, pure planner, executor, locking/reservations and failure cleanup; contract tests and live plain-shell/typing checks | Full real-agent restore acceptance and live reference multi-window/race checks; Codex daemon query stays deferred |
| M3 packaging/doctor | Doctor, section 7 package/entry point, actual isolated tagged production install/reinstall/uninstall, README and macOS CI configuration | Validate release-tag installation, fresh-account prompt and hosted CI; restart remains omitted |

Implementation is delivered; the full definition of done below is not yet
satisfied. Manual acceptance, fresh-account checks, private publication and
hosted CI cannot be replaced by local substitutes. Release tags are owner-managed.

### Decisions (2026-11-10)

- **A / Q2:** replace unconditional duplicate prevention with verified blocking
  and explicit scoped `may already be open` warnings (sections 1, 3, 6 and 10).
  The Codex daemon query is excluded pending the separate owner decision below.
- **B — adopted as snapshot format 2:** format 1 remains readable with its
  original nonempty-cwd requirement; new saves use format 2, where cwd may be
  null. Readers accept mixed current/backup formats without migration (sections
  3–6, 9–11 and AC-21/AC-22).
- **Q1:** retain exact/command-line likely resumes; downgrade multi-pane saved
  recent-transcript groups to the agent's picker. Derive ambiguity from the full
  snapshot, without changing confidence or schema (sections 3–6, AC-6/AC-10).
  Format 1 cannot recover original candidate counts; singleton saved groups
  remain likely under the specified count rule.
- **Q3:** cover the current user's Ghostty plus tsr-restored sessions; use
  system-wide ps observations outside Ghostty as uncertainty warnings, not
  other-terminal identity support (sections 1, 3, 6 and AC-17).
- **CHECK 1 / Codex scope resolved:** owner-confirmed cross-folder conversation
  continuity on Codex 0.167.1 (F49) selects agent-wide Codex uncertainty warnings
  (section 6 and AC-17). Do not infer behavior of the pre-update version or
  exact frontend identity from that result.
- **Q4:** no picker lock; bounded exclusive recheck-and-reserve lock on Enter,
  no AppleScript/lsof in that recheck, then launch outside the lock. Replace
  60-second starting records with reservations, confirmed live process identity,
  two-minute unconfirmed lapse and immediate failure cleanup. Independent-launch
  races remain outside the guarantee except where recheck catches them
  (sections 4, 6, 9, 11; AC-7/AC-18/AC-19). CHECK 2 passed for the read path.
  CHECK 3 measured Codex association/exit, but rejected the strict timestamp rule;
  section 4 records the implemented bounded evidence rule and limits (F50–F51).
- **Completion authorization:** implement M2/M3 without milestone approval stops.
  Defer the optional Codex daemon query while its owner decision is open; retain
  Decision B's format-2 behavior. Keep desktop permission and read-only
  agent/configuration constraints. Local implementation verification is F55–F56;
  remaining external/manual acceptance is listed in [v1.md](v1.md).

### Definition of done for v1

- Every acceptance criterion in section 10 passes on a real Mac with Ghostty 1.4 and all three agents.
- All suites in section 11 pass in CI on macOS with Python 3.14.
- Performance budgets in section 9 are met.
- A README covers installing with uv, the save-restart-restore flow, what the snapshot stores, and uninstall.
- Section 2 matches what was verified during the build, including spike results.

## 14. Remaining owner decision and external evidence

### Codex daemon loaded-ID query — owner decision open

The query is deferred in v1. Any future integration needs an owner choice:

- Defer it.
- Block on a matching loaded ID.
- Use it as a warning: `loaded in Codex server`.
- Drop it.

**Recommendation on record: warning.** Loaded server state does not identify a
frontend terminal. F34 established retained writer locks after `/new`, not that
those same IDs stayed in the loaded-thread list. F36 separately sampled loaded
threads and an unloaded old test session. Do not state that post-`/new` loaded-ID
retention was verified from the lock observation; measure it separately if an
option relies on that behavior. No socket/absence/error result proves a session
is free. See [S11](s11.md) for the evidence and limits.

### External evidence still required

- **S5:** fresh macOS account to measure Automation attribution and repetition;
  exact steps and requested outputs are in [m0.md](m0.md). Current-account
  permission cannot answer this.
- **S8:** the owner must publish the repository and supply a real package tag
  before private SSH/default-PATH installation can be verified. Local tagged
  subdirectory packaging passed (F40); no remote or release was created here.
- **S9:** Claude/Codex initial picker displays and cancellation are now observed
  (F48): Claude Esc exits 1; Codex Ctrl+C quits with exit 0, while Esc is labeled
  new. Their scope-switch options were displayed but not exercised. Pi's exact
  initial heading remains unverified (F39). Explicit-ID cross-folder Codex resume
  was subsequently verified separately in F49.
- **S6:** retain the documented no-restart fallback. Dictionary inspection is
  not evidence of runtime permission or application quit behavior (F27).
- **S16 / CHECK 3 follow-up:** real Codex association, preserved pre-exec identity
  and exit detection are measured (F50–F51). The strict timestamp rule failed;
  section 4 implements replacement evidence. Normal real-agent parent-shell return,
  other-agent launch forms and deliberate stale/reuse cases remain unverified.
  Full real-agent restore acceptance remains manual; approved plain-shell and
  harmless-command desktop checks passed (F55). Never restart the Mac.
