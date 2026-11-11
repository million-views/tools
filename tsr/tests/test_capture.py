"""Capture contract tests: real M0 formats, deterministic decisions, private state.

Each test owns a distinct public outcome: successful capture/inspection, atomic
backup failure, precondition preservation, identification precedence, or grouping.
Synthetic cases live in test code; committed machine fixtures are never invented.
"""

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import UTC, datetime
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import tsr

FIXTURES = Path(__file__).parent / "fixtures"
SID = "00000000-0000-4000-8000-000000000001"


class FixtureRunner(tsr.Runner):
    def __init__(self, layout, processes, opened):
        self.outputs = {"osascript": tsr.CommandResult("TSR_VERSION\t1.4.0\n" + layout),
                        "ps": tsr.CommandResult(processes), "lsof": tsr.CommandResult(opened)}
        self.calls = []

    def boot_time(self):
        return 1791418416

    def run(self, argv, *, timeout=5):
        self.calls.append(argv)
        return self.outputs[argv[0]]


class CaptureContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tsr-test-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve() / "home"
        self.cwd = self.home / "PROJECT"
        self.cwd.mkdir(parents=True)
        self.env = {"HOME": str(self.home), "XDG_STATE_HOME": str(self.home / "state")}
        self.store = tsr.state_store(self.home, self.env)
        self.claude_file = self.home / ".claude/projects/never-decode-this" / f"{SID}.jsonl"
        self.codex_file = self.home / ".codex/sessions/2026/11/10" / f"rollout-2026-11-10T00-00-00-{SID}.jsonl"
        self.pi_file = self.home / ".pi/agent/sessions/--encoded--" / f"2026-11-10_{SID}.jsonl"
        for name, path in (("claude", self.claude_file), ("codex", self.codex_file), ("pi", self.pi_file)):
            path.parent.mkdir(parents=True)
            path.write_text((FIXTURES / f"{name}-transcript-excerpt.jsonl").read_text().replace("/Users/USER", str(self.home)))
            os.utime(path, (1794310000, 1794310000))
        processes = (FIXTURES / "s3-processes.txt").read_text().replace("/Users/USER", str(self.home))
        self.processes = tsr.parse_processes(processes)
        registry = self.home / ".claude/sessions/98382.json"
        registry.parent.mkdir(parents=True)
        registry.write_text(json.dumps({"pid": 98382, "sessionId": SID,
                                        "cwd": str(self.cwd), "procStart": self.processes[98382].start_utc}))
        layout = "\n".join(line for line in (FIXTURES / "s1-layout.tsv").read_text().splitlines()
                           if line.split("\t")[-1] in {"98382", "45265", "47743", "54222"})
        layout = layout.replace("/Users/USER", str(self.home))
        opened = (FIXTURES / "s2-open-files.txt").read_text().replace("/Users/USER", str(self.home))
        self.runner = FixtureRunner(layout, processes, opened)

    def cli(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = tsr.main(args, runner=self.runner, environment=self.env, home=self.home)
        return code, stdout.getvalue(), stderr.getvalue()

    def snapshot(self):
        return tsr.capture(self.runner, self.home, self.env,
                           now=datetime(2026, 11, 10, 12, tzinfo=UTC), snapshot_id="snapshot-one")

    def test_save_and_list_capture_real_formats_without_touching_agent_storage(self):
        before = {p: p.read_bytes() for p in self.home.rglob("*.json*")}
        code, out, err = self.cli(["save"])
        self.assertEqual(code, 0, err)
        self.assertIn("macOS may ask", err)
        self.assertIn("3 windows · 4 tabs · 3 agent sessions", out)
        snapshot = self.store.load()
        agents = {p.agent.name: p.agent for w in snapshot.windows for t in w.tabs for p in t.panes if p.agent}
        self.assertEqual((agents["claude"].confidence, agents["claude"].method), ("exact", "live-metadata"))
        self.assertEqual((agents["codex"].confidence, agents["pi"].confidence), ("likely", "likely"))
        self.assertEqual(agents["pi"].resume_argv, ("pi", "--session", str(self.pi_file)))
        self.assertTrue(all(a.first_prompt == "PROMPT_PLACEHOLDER" for a in agents.values()))
        self.assertEqual([c[0] for c in self.runner.calls], ["osascript", "ps", "lsof"])
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(self.store.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.store.directory / "snapshot.json").stat().st_mode & 0o777, 0o600)
        self.runner.calls.clear()
        code, out, err = self.cli(["list", "--json"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["id"], snapshot.id)
        self.assertFalse(self.runner.calls)
        code, out, err = self.cli(["list"])
        self.assertEqual(code, 0, err)
        self.assertIn("claude --resume " + SID, out)
        self.assertIn("pi --session", out)

    def test_backup_rotation_and_failed_commit_preserve_both_original_snapshots(self):
        one = self.snapshot()
        two, three = replace(one, id="snapshot-two"), replace(one, id="snapshot-three")
        self.store.save(one)
        self.store.save(two)
        self.assertEqual(self.store.load(backup=True).id, "snapshot-one")
        original = {p.name: p.read_bytes() for p in self.store.directory.iterdir()}
        real_replace = os.replace

        def fail_current_once(src, dst):
            if Path(dst).name == "snapshot.json":
                raise OSError("simulated final rename failure")
            return real_replace(src, dst)

        with patch("tsr.os.replace", side_effect=fail_current_once):
            with self.assertRaises(OSError):
                self.store.save(three)
        self.assertEqual({p.name: p.read_bytes() for p in self.store.directory.iterdir()}, original)
        self.store.save(three)
        self.assertEqual(self.store.load().id, "snapshot-three")
        self.assertEqual(self.store.load(backup=True).id, "snapshot-two")

    def test_old_boot_default_declines_and_yes_explicitly_replaces(self):
        old = replace(self.snapshot(), boot_time=1)
        self.store.save(old)
        self.runner.calls.clear()
        with patch("builtins.input", return_value=""):
            code, out, err = self.cli(["save"])
        self.assertEqual(code, 0, err)
        self.assertIn("Kept existing", out)
        self.assertEqual(self.store.load().id, old.id)
        self.assertFalse((self.store.directory / "snapshot.prev.json").exists())
        with patch("builtins.input", side_effect=AssertionError("--yes must not prompt")):
            code, out, err = self.cli(["save", "--yes"])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.store.load(backup=True).id, old.id)

    def test_precondition_failures_never_change_snapshot_or_backup(self):
        snapshot = self.snapshot()
        self.store.save(snapshot)
        self.store.save(replace(snapshot, id="current"))
        original = {p.name: p.read_bytes() for p in self.store.directory.iterdir()}
        cases = [
            (tsr.CommandResult("TSR_ERROR\tNOT_RUNNING\n"), "Ghostty is not running"),
            (tsr.CommandResult("TSR_VERSION\t1.3.9\n"), "1.4 or newer"),
            (tsr.CommandResult("", "not authorized (-1743)", 1), "Automation"),
            (tsr.CommandResult("TSR_VERSION\t1.4.0\n"), "nothing to save"),
        ]
        for result, expected in cases:
            with self.subTest(expected=expected):
                self.runner.outputs["osascript"] = result
                code, out, err = self.cli(["save"])
                self.assertEqual(code, 3)
                self.assertIn(expected, err)
                self.assertNotIn("Traceback", err)
                self.assertEqual({p.name: p.read_bytes() for p in self.store.directory.iterdir()}, original)

    def test_empty_terminal_cwd_uses_the_same_lsof_batch(self):
        self.runner.outputs["osascript"] = tsr.CommandResult(
            "TSR_VERSION\t1.4.0\n1\tw\t1\tt\tshell\tsurface\t/dev/ttys031\t\t48681\n")
        self.runner.outputs["lsof"] = tsr.CommandResult(f"p48681\nfcwd\nn{self.cwd}\n")
        snapshot = self.snapshot()
        self.assertEqual(snapshot.windows[0].tabs[0].panes[0].cwd, str(self.cwd))
        self.assertEqual(len(self.runner.calls), 3)
        # S17: the nonempty terminal getter can retain the pre-exec folder.
        self.runner.outputs["osascript"] = tsr.CommandResult(
            f"TSR_VERSION\t1.4.0\n1\tw\t1\tt\tshell\tsurface\t/dev/ttys031\t{self.home}\t48681\n")
        self.assertEqual(self.snapshot().windows[0].tabs[0].panes[0].cwd, str(self.cwd))
        self.store.save(snapshot)
        original = (self.store.directory / "snapshot.json").read_bytes()
        self.runner.outputs["osascript"] = tsr.CommandResult(
            "TSR_VERSION\t1.4.0\n1\tw\t1\tt\tshell\tsurface\t/dev/ttys031\t\t48681\n")
        self.runner.outputs["lsof"] = tsr.CommandResult("")
        code, out, err = self.cli(["save"])
        self.assertEqual(code, 0, err)
        self.assertIn("(folder unknown)", out)
        self.assertIn("1 tab saved without a folder", out)
        saved = self.store.load()
        self.assertEqual(saved.format, 2)
        self.assertIsNone(saved.windows[0].tabs[0].panes[0].cwd)
        paths = tsr.parse_open_files('p1\nfcwd\nn/tmp/back\\\\slash\nf4\nn/tmp/unicode-\\xc3\\xa9\n'
                                     'p2\nfcwd\nn/tmp/control^A\n')
        self.assertEqual(paths.cwd[1], '/tmp/back\\slash')
        self.assertEqual(paths.paths[1], ('/tmp/unicode-é',))
        self.assertNotIn(2, paths.cwd)
        self.assertEqual(paths.uncertain_cwd, {2})

    def test_format_two_unknown_folders_round_trip_and_slots_mix_without_rewriting(self):
        original = self.snapshot()
        panes = [p for w in original.windows for t in w.tabs for p in t.panes]
        agent_pane = next(p for p in panes if p.agent)
        shell_pane = next(p for p in panes if not p.agent)
        changed = replace(original, format=2, windows=(replace(original.windows[0], tabs=(
            replace(original.windows[0].tabs[0], panes=(replace(agent_pane, cwd=None),
                                                         replace(shell_pane, cwd=None))),)),))
        loaded = tsr.Snapshot.from_json(changed.to_json())
        self.assertEqual([p.cwd for p in loaded.windows[0].tabs[0].panes], [None, None])
        self.assertIsNotNone(loaded.windows[0].tabs[0].panes[0].agent)
        invalid_v1 = json.loads(changed.to_json())
        invalid_v1["format"] = 1
        with self.assertRaisesRegex(tsr.TsrError, "malformed"):
            tsr.Snapshot.from_json(json.dumps(invalid_v1))
        future = json.loads(changed.to_json())
        future["format"] = 3
        with self.assertRaisesRegex(tsr.TsrError, "upgrade"):
            tsr.Snapshot.from_json(json.dumps(future))

        v1 = replace(original, format=1)
        v2 = changed
        self.store.save(v1)
        self.store.save(v2)
        current_bytes = (self.store.directory / "snapshot.json").read_bytes()
        backup_bytes = (self.store.directory / "snapshot.prev.json").read_bytes()
        self.assertEqual(self.store.load().format, 2)
        self.assertEqual(self.store.load(backup=True).format, 1)
        self.assertEqual((self.store.directory / "snapshot.json").read_bytes(), current_bytes)
        self.assertEqual((self.store.directory / "snapshot.prev.json").read_bytes(), backup_bytes)
        self.store.save(v1)
        self.store.save(v2)
        self.assertEqual(self.store.load().format, 2)
        self.assertEqual(self.store.load(backup=True).format, 1)
        self.store.save(v2)
        self.store.save(v1)
        self.assertEqual(self.store.load().format, 1)
        self.assertEqual(self.store.load(backup=True).format, 2)

    def test_save_unknown_folder_keeps_snapshot_and_displays_warning_and_null_json(self):
        shell_pid = 54222
        layout = self.runner.outputs["osascript"].stdout
        fields = next(line for line in layout.splitlines() if line.endswith("\t54222"))
        empty_shell = "\t".join(fields.split("\t")[:-2] + ["", str(shell_pid)])
        self.runner.outputs["osascript"] = tsr.CommandResult(layout.replace(fields, empty_shell))
        opened = self.runner.outputs["lsof"].stdout
        self.runner.outputs["lsof"] = tsr.CommandResult("".join(
            "p" + chunk for chunk in opened.split("p") if not chunk.startswith(f"{shell_pid}\n")))
        code, out, err = self.cli(["save"])
        self.assertEqual(code, 0, err)
        self.assertIn("(folder unknown)", out)
        self.assertIn("1 tab saved without a folder: window 1, tab 1 (it can't be restored)", out)
        snapshot = self.store.load()
        self.assertEqual(snapshot.format, 2)
        unknown = next(p for w in snapshot.windows for t in w.tabs for p in t.panes if p.cwd is None)
        self.assertIsNone(unknown.agent)
        code, out, err = self.cli(["list"])
        self.assertEqual(code, 0, err)
        row = out.split("(folder unknown)")[0].splitlines()[-1]
        self.assertNotIn("cd --", row)
        code, out, err = self.cli(["list", "--json"])
        panes = [p for w in json.loads(out)["windows"] for t in w["tabs"] for p in t["panes"]]
        unknown = [p for p in panes if p["cwd"] is None]
        self.assertEqual(len(unknown), 1)
        self.assertIsNone(unknown[0]["agent"])

    def test_ambiguous_lsof_control_cwd_is_saved_as_unknown(self):
        layout = "1\tw\t1\tt\tshell\tsurface\t/dev/ttys031\t\t54222\n"
        self.runner.outputs["osascript"] = tsr.CommandResult("TSR_VERSION\t1.4.0\n" + layout)
        self.runner.outputs["ps"] = tsr.CommandResult("54222 1 ttys031 Mon Nov 10 10:00:00 2026 -zsh\n")
        self.runner.outputs["lsof"] = tsr.CommandResult("p54222\nfcwd\nn/tmp/control^A\n")
        snapshot = self.snapshot()
        self.assertIsNone(snapshot.windows[0].tabs[0].panes[0].cwd)

    def test_runtime_change_overrides_stale_argv_and_bad_registry_degrades(self):
        other = "11111111-1111-4111-8111-111111111111"
        new_file = self.claude_file.with_name(other + ".jsonl")
        new_file.write_text(self.claude_file.read_text().replace(SID, other))
        registry = self.home / ".claude/sessions/98382.json"
        obj = json.loads(registry.read_text())
        obj["sessionId"] = other
        registry.write_text(json.dumps(obj))
        snapshot = self.snapshot()
        agent = next(p.agent for w in snapshot.windows for t in w.tabs for p in t.panes
                     if p.agent and p.agent.name == "claude")
        self.assertEqual((agent.session_id, agent.method), (other, "live-metadata"))
        registry.write_text("[]")
        snapshot = self.snapshot()
        agent = next(p.agent for w in snapshot.windows for t in w.tabs for p in t.panes
                     if p.agent and p.agent.name == "claude")
        self.assertEqual((agent.session_id, agent.confidence, agent.method), (SID, "likely", "argv"))

    def test_missing_transcripts_use_folder_pickers(self):
        for path in (self.claude_file, self.codex_file, self.pi_file):
            path.unlink()
        snapshot = self.snapshot()
        agents = {p.agent.name: p.agent for w in snapshot.windows for t in w.tabs for p in t.panes if p.agent}
        self.assertEqual(agents["codex"].resume_argv, ("codex", "resume"))
        self.assertEqual(agents["pi"].resume_argv, ("pi", "--resume"))
        self.assertTrue(all(agents[n].confidence == "picker" for n in ("codex", "pi")))
        self.assertEqual(agents["claude"].confidence, "likely")  # explicit validated argv still supplies its ID

    def test_idle_resumed_old_transcripts_do_not_get_guessed(self):
        for path, pid in ((self.codex_file, 45265), (self.pi_file, 47743)):
            started = self.processes[pid].started_at
            os.utime(path, (started - 10, started - 10))
        snapshot = self.snapshot()
        agents = {p.agent.name: p.agent for w in snapshot.windows for t in w.tabs for p in t.panes if p.agent}
        self.assertEqual(agents["codex"].confidence, "picker")
        self.assertEqual(agents["pi"].confidence, "picker")
        self.assertEqual(agents["claude"].confidence, "exact")

    def test_open_file_identity_excludes_claude_subagents_and_rejects_external_pi_files(self):
        adapter = tsr.ClaudeAdapter(self.home)
        nested = self.claude_file.parent / "subagents" / f"{SID}.jsonl"
        nested.parent.mkdir()
        shutil.copyfile(self.claude_file, nested)
        self.assertIsNone(adapter.session_from_open_files((str(nested),)))
        self.assertEqual(adapter.session_from_open_files((str(self.claude_file),)).session_id, SID)
        pi = tsr.PiAdapter(self.home, {})
        external = self.home / "outside.jsonl"
        shutil.copyfile(self.pi_file, external)
        self.assertIsNone(pi.session_from_argv("pi --session " + str(external)))
        spaced = self.pi_file.with_name("space name.jsonl")
        shutil.copyfile(self.pi_file, spaced)
        self.assertIsNone(pi.session_from_argv("pi --session " + str(spaced)))

    def test_future_snapshot_refused_and_agent_state_directory_rejected(self):
        self.store.ensure_directory()
        path = self.store.directory / "snapshot.json"
        path.write_text('{"format": 3}')
        code, out, err = self.cli(["list", "--json"])
        self.assertEqual(code, 1)
        self.assertIn("upgrade", err)
        self.assertEqual(path.read_text(), '{"format": 3}')
        for root in (".claude", ".codex", ".pi"):
            self.env["XDG_STATE_HOME"] = str(self.home / root)
            code, out, err = self.cli(["save"])
            self.assertEqual(code, 1)
            self.assertIn("overlaps agent storage", err)
            self.assertFalse((self.home / root / "tsr").exists())

    def test_corrupt_agent_data_and_capture_exceptions_are_one_line_errors(self):
        obj = json.loads(self.snapshot().to_json())
        agent = next(p["agent"] for w in obj["windows"] for t in w["tabs"] for p in t["panes"] if p["agent"])
        agent["resume_argv"] = [7]
        self.store.ensure_directory()
        (self.store.directory / "snapshot.json").write_text(json.dumps(obj))
        code, out, err = self.cli(["list"])
        self.assertEqual(code, 1)
        self.assertIn("malformed", err)
        self.assertNotIn("Traceback", err)
        original = (self.store.directory / "snapshot.json").read_bytes()
        with patch.object(self.runner, "run", side_effect=RuntimeError("transport failed")):
            code, out, err = self.cli(["save"])
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)
        self.assertEqual((self.store.directory / "snapshot.json").read_bytes(), original)
        self.assertIn("RuntimeError", (self.store.directory / "tsr.log").read_text())
        self.assertEqual((self.store.directory / "tsr.log").stat().st_mode & 0o777, 0o600)

    def test_custom_pi_session_directory_uses_the_supplied_home_and_direct_files(self):
        root = self.home / "custom-sessions"
        root.mkdir()
        path = root / self.pi_file.name
        shutil.copyfile(self.pi_file, path)
        adapter = tsr.PiAdapter(self.home, {"PI_CODING_AGENT_SESSION_DIR": "~/custom-sessions"})
        candidates = adapter.candidates(str(self.cwd), 0)
        self.assertEqual([c.ref.session_file for c in candidates], [str(path)])
        self.assertEqual(adapter.resume_argv(candidates[0].ref), ("pi", "--session", str(path)))

    def test_describe_skips_tool_and_system_records_caps_prompt_and_stops_at_200_lines(self):
        header, user = [json.loads(line) for line in self.claude_file.read_text().splitlines()]
        tool = {**user, "message": {"role": "user", "content": [{"type": "tool_result", "content": "tool output"}]}}
        context = {**user, "message": {"role": "user", "content": "<context>system material</context>"}}
        prompt = {**user, "message": {"role": "user", "content": "real\n" + "x" * 250}}
        self.claude_file.write_text("\n".join(json.dumps(o) for o in (header, tool, context, prompt)))
        adapter = tsr.ClaudeAdapter(self.home)
        description = adapter.describe(tsr.SessionRef("claude", SID, str(self.claude_file)))
        self.assertEqual(description.first_prompt, "real " + "x" * 195)
        self.claude_file.write_text("\n".join(json.dumps(o) for o in [header, *([context] * 199), prompt]))
        self.assertIsNone(adapter.describe(tsr.SessionRef("claude", SID, str(self.claude_file))).first_prompt)


class DecisionContract(unittest.TestCase):
    def process(self, pid, parent=1, start=0, command="claude", tty="s001"):
        return tsr.Process(pid, parent, tty, "Tue Nov 10 00:00:00 2026", start, command)

    def test_layout_real_fixture_preserves_window_tab_and_split_order(self):
        _, windows = tsr.parse_layout((FIXTURES / "s1-layout.tsv").read_text())
        self.assertEqual(len(windows), 9)
        self.assertEqual(sum(len(t.panes) for w in windows for t in w.tabs), 28)
        self.assertEqual([t.index for t in windows[0].tabs], list(range(1, 10)))
        _, split = tsr.parse_layout("1\tw\t1\tt\ttitle\tp1\t/dev/ttys001\t/tmp\t1\n"
                                    "1\tw\t1\tt\ttitle\tp2\tttys002\t/tmp\t2\n")
        self.assertEqual([p.terminal_id for p in split[0].tabs[0].panes], ["p1", "p2"])
        self.assertEqual([p.tty for p in split[0].tabs[0].panes], ["s001", "s002"])

    def test_outermost_match_wins_through_nonmatching_children_and_servers_are_excluded(self):
        adapters = (tsr.ClaudeAdapter(Path("/fake")), tsr.CodexAdapter(Path("/fake")))
        processes = {1: self.process(1), 2: self.process(2, 1, command="/bin/sh"),
                     3: self.process(3, 2, command="/vendor/bin/codex")}
        pane = tsr.Pane("surface", "s001", "/tmp")
        selected = tsr.select_agents((pane,), processes, adapters)
        self.assertEqual(selected["surface"][0].pid, 1)
        self.assertFalse(adapters[1].matches("/vendor/bin/codex app-server --listen unix://"))

    def test_recent_pairing_uses_newest_pool_then_first_timestamp_and_reserves_known_ids(self):
        known = tsr.SessionRef("claude", "known-session")
        a = tsr.Observation("known", "claude", "/project", self.process(1),
                            (tsr.Identification(known, "likely", "argv"),))
        b = tsr.Observation("early", "claude", "/project", self.process(2, start=10), ())
        c = tsr.Observation("late", "claude", "/project", self.process(3, start=20), ())
        pool = tuple(tsr.Transcript(tsr.SessionRef("claude", sid, "/" + sid), "/project", first,
                                   modified, tsr.Description())
                     for sid, first, modified in (("old-session", 1, 1), ("known-session", 5, 100),
                                                  ("second-session", 30, 90), ("first-session", 25, 80)))
        result = tsr.identify((a, c, b), {("claude", "/project"): pool})
        self.assertEqual(result["early"].ref.session_id, "first-session")
        self.assertEqual(result["late"].ref.session_id, "second-session")
        self.assertEqual(result["known"].ref, known)
        for count in (0, 1):
            with self.subTest(candidates=count):
                result = tsr.identify((b, c), {("claude", "/project"): pool[-count:] if count else ()})
                self.assertEqual(sum(i.confidence == "picker" for i in result.values()), 2 - count)

    def test_labels_prefer_repositories_then_first_tab_and_suffix_duplicates(self):
        panes = (tsr.Pane("a", "s1", "/repo/a"), tsr.Pane("b", "s2", "/outside"),
                 tsr.Pane("c", "s3", "/outside"))
        window = tsr.Window(1, "w", (tsr.Tab(1, "t", "title", panes),))
        result = tsr.label_windows((window, replace(window, index=2)),
                                   {"/repo/a": "/repo", "/outside": None})
        self.assertEqual([w.label for w in result], ["repo", "repo (2)"])
        tied = replace(window, tabs=(tsr.Tab(1, "t", "title", panes[:2]),))
        self.assertEqual(tsr.label_windows((tied,), {"/repo/a": None, "/outside": None})[0].label, "a")
        partly_unknown = replace(window, tabs=(tsr.Tab(1, "t", "title",
            (replace(panes[0], cwd=None), panes[1], panes[2])),))
        self.assertEqual(tsr.label_windows((partly_unknown,), {"/outside": None})[0].label, "outside")
        all_unknown = replace(window, tabs=(tsr.Tab(1, "t", "title",
            tuple(replace(pane, cwd=None) for pane in panes)),))
        self.assertEqual(tsr.label_windows((all_unknown,), {})[0].label, "Window 1")
        self.assertEqual(tsr.label_windows((tsr.Window(3, "empty", ()),), {})[0].label, "Window 3")


if __name__ == "__main__":
    unittest.main()
