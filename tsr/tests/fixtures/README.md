# M0 fixture provenance

Captured on the owner's Mac on 2026-11-10, Ghostty 1.4.0. Exact sanitized
contents were previewed in the conversation before saving. These files are
evidence, not synthesized agent behavior.

| File | Capture and limits |
| --- | --- |
| `s1-layout.tsv` | All 28 panes returned by one read-only AppleScript traversal. Fields: window ordinal, window ID, tab index, tab ID, title, terminal ID, tty, cwd, pid. Enumerations materialized using `get windows`, `get tabs of w`, `get terminals of tb`. |
| `s3-processes.txt` | Selected agent and parent-shell rows from `LC_ALL=C ps -A -ww -o pid=,ppid=,tty=,lstart=,args=`. Other processes omitted for privacy. This is not the entire process table or a complete ancestry tree. |
| `s2-open-files.txt` | Complete concatenated `LC_ALL=C lsof -a -p 98382,45265,60229 -Fpfn` and `lsof -a -p 47743 -Fpfn` outputs. Samples had no observed descendants. Negative evidence: no transcript file is held open. |
| `claude-transcript-excerpt.jsonl` | Two records selected from the first 200 lines of a real recent Claude transcript: initial metadata-only record and first user prompt. Independent of the Claude lsof sample; not claimed to identify that sampled process. |
| `codex-transcript-excerpt.jsonl` | Three records selected from the first 200 lines of a recent real rollout: metadata, user-role environment context, actual user prompt. |
| `pi-transcript-excerpt.jsonl` | Session header and first user message from the newly started pi session. |
| `s2-pi-idle-resume.json` | Stat measurements before and after the owner quit and resumed that pi session without sending a prompt. Nanoseconds stored as strings to preserve precision. |
| `s2-claude-idle-resume.json`, `s2-codex-idle-resume.json` | Equivalent owner-controlled before/after measurements using explicitly supplied session IDs; no prompt was sent after resume. |
| `s2-controlled-resume-open-files.txt`, `s2-pi-resumed-open-files.txt` | Complete lsof field outputs from those user-resumed processes. No observed descendants or open transcript files. |
| `s3-controlled-resume.txt`, `s3-pi-resumed.txt` | Selected ps rows after user-controlled resumes. Claude/Codex retained resume arguments; pi did not expose them. |
| `s4-s7-no-settle.json` | Final JSON report emitted by the first desktop probe. No settle delay; ten slow-startup tabs, eight complete markers. Startup prints early and sleeps 2 s. |
| `s4-s7-settle.json` | Final JSON report from the same probe with 200 ms settling; reuses the same two probe windows and appends ten tabs. Ten complete markers. |
| `s6-restart-dictionary.xml` | Exact excerpt of the installed System Events dictionary. Fragment, not a full XML document. No restart AppleEvent was sent. |
| `s8-uv-python.txt` | uv download completion and Python's JSON report. The successful command was `uv run --no-project --managed-python --python 3.14 python -c 'import curses,json,sys; print(json.dumps({"version":sys.version,"executable":sys.executable,"curses":True}))'`. |
| `s8-uv-tool-dir.txt` | Read-only `uv tool dir` output. The installed tsr environment's location remains to be measured after the private-tag install. |

Anonymization replaces home paths, project directories, titles, prompt text,
account metadata and session/message IDs. Installation-path structure remains
under `/Users/USER`. All transcript IDs use the same valid UUID placeholder;
these excerpts do not preserve message-graph identity. Generic text placeholders
are used rather than invented prompts. `<SYSTEM_CONTEXT>` preserves the leading
tag distinction needed by describe. Window/terminal IDs are placeholders; desktop
reports preserve target/front distinction, but collapse individual tab IDs.
Network endpoints and opaque socket/state hex strings in lsof are redacted too.
PIDs, ttys, timestamps, record types, content structure and measured values remain.

No S5 fixture exists until a fresh-account probe is run. S8's private-tag install
is also pending; successful Python download is not evidence that it succeeded.

## S11 follow-up evidence

These were separately previewed before saving. See `../../docs/s11.md`.

| File | Capture and limits |
| --- | --- |
| `s11-claude-live-registry.json` | Anonymized projection of 11 live registry records matched against UTC ps and transcript IDs. The final probe includes transcript ID validation; timing excludes ps. Credentials and descriptive fields are omitted. |
| `s11-claude-clear.json` | Before/after identity fields from the owner-controlled `/clear` check. Old and new IDs are distinct placeholders; the process identity is unchanged. |
| `s11-codex-locks-before.txt`, `s11-codex-locks-after.txt` | Complete returned lsof field output for the writer-lock directory. Shared aliases across the pair preserve the retained old test lock and newly added lock. Both calls emitted rows with exit 1; exhaustiveness is not claimed. |
| `s11-codex-lock-lifecycle.json` | Probe outcome and owner action, not a synthetic session transcript. |
| `s11-schema.json` | Installed pi package name/version and projected Codex database column names only. No database rows, prompts or account values. |
| `s11-pi-source.txt` | Local installed-package source excerpts showing process-title setup, session writes and RPC child startup. Source inspection, not an executed RPC test. |
| `s11-codex-rpc.json` | Projected metadata from the existing shared daemon via Unix WebSocket. Only initialize/initialized, loaded-list and metadata reads were sent. A read of the old unloaded test session did not load it. Thread fields are names/shapes only; prompts, names, raw paths and IDs are omitted. |
| `s4-single-line.json` | Ten additional slow-startup tabs, single-line markers, no settle delay, 10/10 complete outputs. Same two probe windows, indices 22–31. |
| `s10-argv.json` | Actual ps output for three harmless Python children with known vectors. A one-argument/two-argument pair has identical display text. No agent was simulated. Children were terminated by their parent after capture. |
| `s9-picker-source.txt`, `s9-pi-owner.json` | Installed pi source and compiled Claude/Codex usage excerpts, plus the owner's pi picker cancellation observation. Exact UI folder-heading text was not reported. |
| `s9-claude-codex-owner.json` | Owner-provided Claude/Codex picker screenshots transcribed as UI excerpts, plus reported shell exit statuses. Project label replaced with PROJECT; conversation titles, account details, paths and tab titles omitted. Initial scope controls and cancellation were observed; scope switching and explicit-ID cross-folder resume were not tested. Exact sanitized contents were previewed before saving. |
| `s15-codex-cross-folder-owner.json` | Owner-operated explicit-ID resume from a different project, selecting current directory, with controlled marker history confirmed in a Codex 0.167.1 screenshot. Home/project paths, prompt, marker response and session ID replaced. The owner updated Codex during the check; no cause is assigned to earlier missing history and no earlier-version or passive frontend-mapping claim is made. Exact sanitized contents were previewed before saving. |
| `s16-process-lifecycle.json` | Approved two-tab probe with owner-launched Codex 0.167.1: projected ps identities, tty matches, pre-exec evidence, harmless sleep comparisons and post-quit liveness. Terminal IDs replaced with aliases; no home paths, prompts or session IDs retained. Owner reported A's shell remaining and Enter required to close B. B used shell-level exec, so production parent-shell return was not tested. Manual startup exceeded two minutes; no timeout/concurrency/reuse/actual reservation implementation pass is claimed. Exact sanitized contents were previewed before saving. |
| `s8-local-tag-install.json` | Complete projected report from the isolated local git/tag/subdirectory smoke install, execution and uninstall. The generated package, commit and tag live only in a temporary test repository; no project commit or remote push occurred. |
| `s8-private-remote.txt` | Both read-only tag queries returned this same error with exit 128. The owner then clarified that the repository has not been published yet. |
| `s4-empty-terminal-cwds.tsv`, `s4-empty-cwd.txt` | Read-only cleanup inspection before any closing: 32 terminals had empty AppleScript cwd, while lsof returned their correct probe directories. TSV fields: window ID, tab index, terminal ID, tty, cwd, pid. Empty cwd is an intentionally empty field. Lsof directory aliases collapse the three temporary roots. |
| `s12-boot-time.json` | Native read-only ctypes result compared with the F21 sysctl command. Both returned identical boot seconds/microseconds; no home paths, prompts or session IDs. |
| `s13-lsof-numeric.json` | Live plain/numeric lsof timing comparison on 64 PIDs. Cwd and observed transcript sets matched; other live file maps differed, so no frozen-descriptor claim is made. |
| `s14-bulk-layout.json` | Read-only baseline/bulk layout timings and stable-field equality on 11 windows / 62 panes. No private terminal fields are emitted. |
| `m1-live-save.json` | Final M1 live save/list verification: counts, timings, backup/JSON comparisons and permission modes only. Actual snapshots with private metadata stayed in an isolated temporary state directory, outside fixtures. |
| `v1-verification.json` | Previewed aggregate final capture timing/counts, actual isolated plain-shell picker/restore/cancellation/quoted-command checks, exact/warning availability counts, measured path encoding cases, and actual production package install/reinstall/removal results. No real-agent restore or fresh-account/private SSH/hosted CI pass is claimed. Private snapshots and runtime paths are omitted. |
