# tsr manual acceptance

Run this rehearsal in Ghostty before relying on tsr for a Mac restart. It
checks capture and restore with disposable agent conversations; it does not
restart the Mac or replace the full acceptance matrix in [the spec](spec.md).

## Prepare

- Use a scratch project folder and disposable Claude, Codex and pi sessions.
- Choose a dedicated `XDG_STATE_HOME` path for this run. Set the same value in
  every tab and window below so the test uses one isolated tsr snapshot and
  restore journal. Do not point it at your normal tsr state.
- Open a new Ghostty window with one tab per agent. Start Claude, Codex and pi;
  send each a short, recognizable message that you can check after resuming.

## Run

1. In the test window, run `tsr doctor`, `tsr save`, then `tsr list`. Confirm
   that all panes appear in saved order, folders are correct, and the snapshot
   identifies Claude as exact, Codex as likely, and pi as likely or picker.
   This checks capture and identification (AC-2, AC-3).
2. Quit the three agents and close the test window.
3. In a fresh Ghostty window, run `tsr restore` and select the saved window.
   Check that each agent resumes the conversation containing its recognizable
   message and that tabs appear in saved order (AC-6).
4. In another new window, run `tsr restore`. Confirm the restored sessions are
   disabled as running or `running · restored by tsr` (AC-7, AC-16).

Record the displayed confidence, warnings, restored messages and duplicate
check results. Stop if any agent opens the wrong conversation. After reviewing
the isolated snapshot, remove the dedicated state directory when it is no
longer needed.

This rehearsal covers the real-agent restore path. It does not validate package
installation, fresh-account Automation prompts, hosted CI, or a restart.
