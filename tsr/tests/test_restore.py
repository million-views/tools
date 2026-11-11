"""Restore contracts: selection, safe effects, reservations and real file locks.

Synthetic scenarios stay in test code; the S16 machine fixture independently
guards exec continuity and timestamp precision. No real desktop/agent launches.
"""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import tsr

SID = "00000000-0000-4000-8000-000000000001"


def info(name="codex", sid=SID, method="argv", confidence="likely", argv=None):
    return tsr.AgentInfo(name, 77, name, sid, None, confidence, method, "prompt", None,
                         argv or (name, "resume", sid))


class RestoreContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.folder = str(self.home)
        self.adapters = tsr.default_agents(self.home, {})
        self.store = tsr.Store(self.home / "state")
        self.journal = tsr.RestoreJournal(self.store)

    def snapshot(self, panes):
        return tsr.Snapshot(1, "saved", "2026-11-10T00:00:00Z", 1, "test", "0.1.0",
                            {"app": "ghostty", "version": "1.4.0"},
                            (tsr.Window(1, "old-w", tuple(tsr.Tab(i, str(i), "title", (p,))
                             for i, p in enumerate(panes, 1)), "project"),))

    def pane(self, key="p", agent=None, cwd=None):
        return tsr.Pane(key, "s001", cwd or self.folder, agent=agent)

    def rows(self, panes):
        return tsr.restore_rows(self.snapshot(panes), self.adapters, "/bin/zsh")

    def lease(self, row, **changes):
        return {"id": "lease", "row": row.key, "agent": row.ref.agent if row.ref else None,
                "session_id": row.ref.session_id if row.ref else None, "session_file": None,
                "state": "reserved", "reserved_at": 100.8, "boot_time": 1,
                "terminal_id": "created", "new_terminal": True, **changes}

    def test_frozen_snapshot_ambiguous_group_downgrades_even_for_one_selected_pane(self):
        a = info(method="recent-transcript")
        snap = self.snapshot((self.pane("a", a), self.pane("b", replace(a, session_id="another-session"))))
        original = snap.to_json()
        restored = tsr.Snapshot.from_json(original)
        rows = tsr.restore_rows(restored, self.adapters, "/bin/zsh")
        steps = tsr.plan(rows, {rows[1].key}, "current-window", "current-terminal")
        self.assertEqual(steps[0].row.argv, ("codex", "resume"))
        self.assertIn("ambiguous", steps[0].row.note)
        self.assertEqual(snap.to_json(), original)
        singleton = self.rows((self.pane("a", a),))[0]
        self.assertEqual(singleton.argv, ("codex", "resume", SID))
        # Stored executable/flags are never trusted, even in an otherwise valid snapshot.
        malicious = info(argv=("sh", "-c", "touch /unwanted"))
        self.assertEqual(self.rows((self.pane("a", malicious),))[0].argv, ("codex", "resume", SID))
        self.assertEqual(self.rows((self.pane("a", info(sid="--unsafe")),))[0].argv, ("codex", "resume"))
        bad_pi = replace(info("pi"), session_file=str(self.home / "outside.jsonl"))
        self.assertEqual(self.rows((self.pane("a", bad_pi),))[0].argv, ("pi", "--resume"))

    def test_availability_exact_blocks_and_uncertainty_scope_is_agent_specific(self):
        panes = (self.pane("c", info("claude", confidence="exact")),
                 self.pane("c2", info("claude", sid="second-session")),
                 self.pane("other", info("claude", sid="third-session"), "/other"),
                 self.pane("x", info()), self.pane("x2", info(sid="different-session"), "/other"),
                 self.pane("pi", info("pi")), self.pane("missing", None, "/missing"))
        rows = self.rows(panes)
        proc = tsr.Process(10, 1, "s001", "start", 100, "claude")
        agents = (tsr.LiveAgent("claude", proc, tsr.SessionRef("claude", SID), self.folder, "window 2, tab 1", "live", True),
                  tsr.LiveAgent("claude", replace(proc, pid=11), None, self.folder, "window 2, tab 2", "live2", True),
                  tsr.LiveAgent("codex", replace(proc, pid=12), None, "/unrelated", "outside Ghostty", None),
                  tsr.LiveAgent("pi", replace(proc, pid=13), None, "/unrelated", "outside Ghostty", None))
        states = tsr.availability(rows, agents, [], now=110, folders={self.folder, "/other"}, repositories={})
        self.assertFalse(states[rows[0].key].selectable)
        self.assertEqual(states[rows[0].key].reason, "running · window 2, tab 1")
        self.assertIn("may already be open", states[rows[1].key].reason)
        self.assertNotIn("may already be open", states[rows[2].key].reason)
        for row in rows[3:6]:
            self.assertTrue(states[row.key].selectable)
            self.assertIn("may already be open · outside Ghostty", states[row.key].reason)
        self.assertEqual(states[rows[-1].key].reason, "folder missing")
        unknown = replace(agents[1], cwd=None)
        states = tsr.availability(rows, (unknown,), [], now=110, folders={self.folder, "/other"}, repositories={})
        self.assertIn("may already be open", states[rows[2].key].reason)

    def test_s16_exec_identity_and_new_terminal_second_precision_then_exit_and_reuse(self):
        evidence = json.loads((Path(__file__).parent / "fixtures/s16-process-lifecycle.json").read_text())
        row = self.rows((self.pane("created", info()),))[0]
        for label in ("A", "B"):
            with self.subTest(label=label):
                tab = evidence["created_tabs"][label]
                observed = tab["agent"]
                proc = tsr.Process(observed["pid"], observed["ppid"], "s001", observed["start_utc"],
                                   observed["start_epoch"], "codex")
                live = tsr.LiveState(self.snapshot((row.pane,)), {proc.pid: proc},
                                     (tsr.LiveAgent("codex", proc, None, self.folder, "window 1", "created"),))
                lease = self.lease(row, reserved_at=tab.get("pre_exec", {}).get("reserved_at", tab["tab_reserved_at"]))
                if label == "B":
                    lease.update(new_terminal=False, handoff_pid=proc.pid, handoff_start=proc.start_utc)
                active = tsr.reconcile_leases([lease], live, now=max(lease["reserved_at"] + 121, proc.started_at + 1))
                self.assertEqual(active[0]["state"], "running")
                if label == "B":
                    # A picker opened before this terminal existed can still
                    # verify the recorded process identity without AppleScript.
                    cached = replace(live, snapshot=replace(live.snapshot, windows=()))
                    stale_layout_lease = {**lease, 'tty':'s001'}
                    self.assertEqual(tsr.reconcile_leases([stale_layout_lease], cached,
                        now=lease['reserved_at']+121)[0]['state'], 'running')
                later = tsr.reconcile_leases(active, live, now=lease["reserved_at"] + 3600)
                self.assertEqual(later[0]["state"], "running")
                gone = tsr.reconcile_leases(active, replace(live, processes={}, agents=()), now=lease["reserved_at"] + 122)
                self.assertEqual(gone[0]["state"], "lapsed")
                reused = replace(proc, start_utc="different process start")
                changed = tsr.reconcile_leases(active, replace(live, processes={proc.pid: reused}), now=lease["reserved_at"] + 122)
                self.assertEqual(changed[0]["state"], "lapsed")
        live = tsr.LiveState(self.snapshot((row.pane,)), {}, ())
        lease = self.lease(row)
        self.assertEqual(tsr.reconcile_leases([lease], live, now=220.79)[0]["state"], "reserved")
        self.assertEqual(tsr.reconcile_leases([lease], live, now=220.8)[0]["state"], "lapsed")
        # An unknown terminal must not be confirmed from a stored tty name alone.
        orphan = {**lease, "terminal_id": "gone"}
        self.assertEqual(tsr.reconcile_leases([orphan], live, now=120)[0]["state"], "reserved")

    def test_pending_running_and_lapsed_records_have_distinct_selection_behavior(self):
        row = self.rows((self.pane("p", info()),))[0]
        for state, now, allowed, reason in (("reserved", 101, False, "starting"),
                                            ("running", 10000, False, "running · restored by tsr"),
                                            ("lapsed", 230, True, "restored earlier, not seen running")):
            with self.subTest(state=state):
                available = tsr.availability((row,), (), [self.lease(row, state=state)], now=now,
                                             folders={self.folder}, repositories={})[row.key]
                self.assertEqual(available.selectable, allowed)
                self.assertIn(reason, available.reason)

    def test_null_cwd_rows_remain_in_position_and_are_disabled_without_duplicate_warnings(self):
        unknown_shell = self.pane("shell", cwd=self.folder)
        unknown_agent = replace(self.pane("agent", info()), cwd=None)
        unknown_shell = replace(unknown_shell, cwd=None)
        rows = self.rows((unknown_shell, unknown_agent, self.pane("known", info(sid="other-session"))))
        availability = tsr.availability(rows, (
            tsr.LiveAgent("codex", tsr.Process(99, 1, "outside", "s", 1, "codex"),
                          None, None, "outside Ghostty", None),), [], now=10,
            folders={self.folder}, repositories={})
        self.assertEqual([r.pane.terminal_id for r in rows], ["shell", "agent", "known"])
        self.assertEqual(availability[rows[0].key].reason, "folder unknown")
        self.assertEqual(availability[rows[1].key].reason, "folder unknown")
        self.assertFalse(availability[rows[0].key].selectable)
        self.assertFalse(availability[rows[1].key].selectable)
        self.assertIn("may already be open", availability[rows[2].key].reason)

    def test_picker_window_selection_disabled_rows_and_cross_window_saved_order(self):
        panes = (self.pane("a"), self.pane("b", info()), self.pane("c"))
        snap = self.snapshot(panes[:2])
        snap = replace(snap, windows=(*snap.windows, tsr.Window(2, "w2", (tsr.Tab(1, "t", "", (panes[2],)),), "other")))
        rows = tsr.restore_rows(snap, self.adapters, "/bin/zsh")
        available = {r.key: tsr.PaneAvailability(i != 1, "starting" if i == 1 else "") for i, r in enumerate(rows)}
        state = tsr.PickerState(frozenset({1}))
        state, _ = tsr.picker_event(state, "space", rows, available)
        self.assertEqual(state.selected, {rows[0].key})
        state, _ = tsr.picker_event(state, "left", rows, available)
        state, _ = tsr.picker_event(state, "down", rows, available)
        state, _ = tsr.picker_event(state, "space", rows, available)
        _, selected = tsr.picker_event(state, "enter", rows, available)
        steps = tsr.plan(rows, selected, "target", "own")
        self.assertEqual([(s.kind, s.row.pane.terminal_id) for s in steps], [("current", "a"), ("new", "c")])
        _, selected = tsr.picker_event(tsr.PickerState(frozenset({1}), cursor=2), "enter", rows, available)
        self.assertIsNone(selected)
        _, selected = tsr.picker_event(tsr.PickerState(frozenset({1})), "enter", rows, available)
        self.assertEqual(selected, {rows[0].key})

    def test_real_file_lock_contention_is_bounded_and_file_modes_are_private(self):
        script = """import sys,tsr
from pathlib import Path
try:
    with tsr.RestoreJournal(tsr.Store(Path(sys.argv[1]))).lock(timeout=.05): pass
except tsr.TsrError as exc:
    print(exc); sys.exit(7)
"""
        with self.journal.lock():
            result = subprocess.run([sys.executable, "-c", script, str(self.store.directory)], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn("another restore is in progress", result.stdout)
        result = subprocess.run([sys.executable, "-c", script, str(self.store.directory)], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.store.directory / "restore.lock").stat().st_mode & 0o777, 0o600)

    def test_one_selection_cannot_reserve_the_same_named_session_twice(self):
        rows = self.rows((self.pane('first', info()), self.pane('second', info())))
        live = tsr.LiveState(self.snapshot(tuple(r.pane for r in rows)), {}, ())

        class Runner(tsr.Runner):
            def run(inner, argv, **kwargs):
                return tsr.CommandResult('')

        with self.assertRaisesRegex(tsr.TsrError, 'same session'):
            tsr.reserve(rows, {r.key for r in rows}, self.journal, Runner(), live,
                        self.adapters, 'own')
        self.assertEqual(self.journal.read(), [])
        ambiguous = self.rows((self.pane('first', info(method='recent-transcript')),
                               self.pane('second', info(method='recent-transcript'))))
        ids, _, _ = tsr.reserve(ambiguous, {r.key for r in ambiguous}, self.journal,
                               Runner(), live, self.adapters, 'own')
        self.assertEqual(len(ids), 2)
        self.assertTrue(all(item['agent'] == 'codex' and item['session_id'] is None
                            and item['session_file'] is None for item in self.journal.read()))

    def test_gather_keeps_an_agent_on_a_different_tty_from_its_agent_ancestor(self):
        parent = tsr.Process(10, 1, 's001', 'start', 10, 'claude')
        child = replace(parent, pid=11, ppid=10, tty='s002')
        nested = replace(parent, pid=12, ppid=10)
        live = tsr.gather_live(self.snapshot((self.pane(),)),
                              {p.pid:p for p in (parent, child, nested)}, self.adapters, {})
        self.assertEqual({a.process.pid for a in live.agents}, {10,11})

    def test_narrow_picker_keeps_uncertainty_visible_before_enter(self):
        import curses
        rows = self.rows((self.pane('p', info(), '/' + 'long-folder/' * 12),))
        available = {rows[0].key:tsr.PaneAvailability(True, 'may already be open')}

        class Screen:
            def __init__(inner): inner.lines={}
            def erase(inner): inner.lines.clear()
            def keypad(inner, value): pass
            def getmaxyx(inner): return (8,50)
            def addnstr(inner, y, x, text, limit, style): inner.lines[y]=text[:limit]
            def refresh(inner): pass
            def getch(inner): return ord('q')

        screen=Screen()
        with patch.object(curses,'wrapper',side_effect=lambda fn:fn(screen)), patch.object(curses,'curs_set'), \
                patch('tsr.sys.stdin.isatty',return_value=True), patch('tsr.sys.stdout.isatty',return_value=True):
            self.assertEqual(tsr.pick(rows,available,self.home,'saved'),set())
        row_text = next(text for text in screen.lines.values() if '[ ] codex' in text)
        self.assertIn('may already be open',row_text)

    def test_new_uncertainty_on_enter_returns_to_picker_before_launch(self):
        snap = self.snapshot((self.pane('own', info()),))
        self.store.save(snap)
        before = tsr.LiveState(self.snapshot((self.pane('own'),)), {}, ())

        class Runner(tsr.Runner):
            def run(inner, argv, **kwargs):
                return tsr.CommandResult('999 1 ttys001 Tue Nov 10 00:00:00 2026 codex\n')

        key = self.rows((self.pane('own', info()),))[0].key
        with patch('tsr.os.ttyname',return_value='/dev/ttys001'), \
                patch('tsr.inspect_live',return_value=before), \
                patch('tsr.pick',side_effect=[{key},set()]) as picker, \
                patch('tsr.execute',return_value=0) as execute:
            self.assertEqual(tsr.restore(self.store,Runner(),self.home,{'SHELL':'/bin/zsh'}),0)
        execute.assert_not_called()
        self.assertEqual(picker.call_count,2)
        self.assertIn('may already be open',picker.call_args.args[1][key].reason)
        self.assertEqual(self.journal.read(),[])

    def test_enter_rechecks_changed_claude_identity_without_applescript_or_lsof(self):
        row = self.rows((self.pane("own", info("claude")),))[0]
        snap = self.snapshot((self.pane("own"),))
        start = "Tue Nov 10 00:00:00 2026"
        registry = self.home / '.claude/sessions/999.json'
        registry.parent.mkdir(parents=True)
        registry.write_text(json.dumps({'pid':999,'sessionId':SID,'cwd':self.folder,'procStart':start}))
        transcript = self.home / '.claude/projects/project' / (SID+'.jsonl')
        transcript.parent.mkdir(parents=True)
        transcript.write_text(json.dumps({'sessionId':SID,'cwd':self.folder,'type':'user',
            'message':{'role':'user','content':'synthetic test prompt'}})+'\n')
        calls = []

        class Runner(tsr.Runner):
            def run(inner, argv, **kwargs):
                calls.append(argv[0])
                if argv[0] != 'ps': raise AssertionError('recheck must not read layout/open files')
                return tsr.CommandResult('999 1 ttys001 '+start+' claude\n')

        ids, live, states = tsr.reserve((row,), {row.key}, self.journal, Runner(),
            tsr.LiveState(snap,{},()), self.adapters, 'own')
        self.assertEqual(ids,{})
        self.assertEqual(calls,['ps'])
        self.assertFalse(states[row.key].selectable)
        self.assertIn('running',states[row.key].reason)
        self.assertEqual(self.journal.read(),[])

    def test_long_batch_renews_pending_only_and_never_revives_expired_reservations(self):
        row = self.rows((self.pane("p", info()),))[0]
        with self.journal.lock():
            self.journal.write([self.lease(row,id='pending'),self.lease(row,id='launched',terminal_id='new')])
        with patch('tsr.time.time',return_value=200):
            self.journal.prepare_launch('pending',{'pending'})
        data = {x['id']:x for x in self.journal.read()}
        self.assertEqual(data['pending']['reserved_at'],200)
        self.assertEqual(data['launched']['reserved_at'],100.8)
        with patch('tsr.time.time',return_value=321):
            with self.assertRaises(tsr.TsrError):
                self.journal.prepare_launch('pending',{'pending'})
        self.assertEqual(self.journal.read(),list(data.values()))

    def test_worktree_scope_preserves_relative_folder_and_does_not_merge_siblings(self):
        repo=self.home/'repo'; linked=self.home/'linked'
        common=repo/'.git'; admin=common/'worktrees/linked'
        admin.mkdir(parents=True); linked.mkdir()
        (admin/'commondir').write_text('../..\n')
        (linked/'.git').write_text('gitdir: '+str(admin)+'\n')
        for folder in (repo/'src',linked/'src',linked/'other'):
            folder.mkdir()
        self.assertEqual(tsr.common_repository(str(repo/'src')),tsr.common_repository(str(linked/'src')))
        self.assertNotEqual(tsr.common_repository(str(repo/'src')),tsr.common_repository(str(linked/'other')))

    def test_simultaneous_processes_reserve_only_one_launch_and_preserve_m1_snapshots(self):
        snap = self.snapshot((self.pane("p", info()),))
        self.store.save(snap)
        original = (self.store.directory / "snapshot.json").read_bytes()
        script = """import sys,tsr
from pathlib import Path
store=tsr.Store(Path(sys.argv[1])); snap=store.load()
rows=tsr.restore_rows(snap,tsr.default_agents(Path(sys.argv[2]),{}),'/bin/zsh')
class Runner(tsr.Runner):
    def run(self,argv,**kwargs): return tsr.CommandResult('')
ids,live,status=tsr.reserve(rows,{rows[0].key},tsr.RestoreJournal(store),Runner(),tsr.LiveState(snap,{},()),tsr.default_agents(Path(sys.argv[2]),{}),'own')
if ids:
    with (store.directory/'launches').open('a') as f: f.write('launch\\n')
print(len(ids))
"""
        processes = [subprocess.Popen([sys.executable, "-c", script, str(self.store.directory), self.folder],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        results = [p.communicate(timeout=10) for p in processes]
        self.assertEqual([p.returncode for p in processes], [0, 0], results)
        self.assertEqual(sorted(out.strip() for out, _ in results), ["0", "1"])
        self.assertEqual((self.store.directory / "launches").read_text(), "launch\n")
        self.assertEqual((self.store.directory / "snapshot.json").read_bytes(), original)
        self.assertEqual((self.store.directory / "restored.json").stat().st_mode & 0o777, 0o600)

    def test_executor_continues_after_failure_releases_only_failed_and_executes_current_last(self):
        rows = self.rows((self.pane("current", info()), self.pane("bad", info(sid="bad-session")),
                          self.pane("good", info(sid="good-session"))))
        ids = {r.key: str(i) for i, r in enumerate(rows)}
        with self.journal.lock():
            self.journal.write([self.lease(r, id=ids[r.key]) for r in rows])
        events = []

        class Terminal:
            def __init__(inner):
                inner.shell_processes = {}

            def open_tab(inner, window, folder):
                events.append("create")
                return "bad" if events.count("create") == 1 else "good"
            def wait_for_shell(inner, target, timeout):
                return target != "bad"
            def type_and_run(inner, target, command):
                events.append((target, command))
            def focus(inner, target):
                events.append(("focus", target))

        class ExecObserved(BaseException):
            pass

        def exec_observe(executable, argv):
            events.append(("exec", argv))
            raise ExecObserved()

        with patch("tsr.time.time", return_value=101), patch("tsr.os.chdir") as chdir, patch("tsr.os.execvp", side_effect=exec_observe), redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(ExecObserved):
                tsr.execute(tsr.plan(rows, set(ids), "window", "own"), Terminal(), self.journal, ids)
        self.assertEqual(events[-2:], [("focus", "own"), ("exec", ["codex", "resume", SID])])
        self.assertIn(("good", "codex resume good-session"), events)
        self.assertIn("1 failed", output.getvalue())
        chdir.assert_called_once_with(self.folder)
        self.assertEqual([x["id"] for x in self.journal.read()], ["2"])

    def test_cli_backup_cancellation_outside_terminal_and_doctor_errors(self):
        snap = self.snapshot((self.pane(),))
        env = {"HOME": self.folder, "XDG_STATE_HOME": str(self.home / "xdg"), "SHELL": "/bin/zsh"}
        store = tsr.state_store(self.home, env)
        store.save(snap); store.save(replace(snap, id="newer"))
        before = {p.name: p.read_bytes() for p in store.directory.iterdir()}
        live = tsr.LiveState(snap, {}, ())
        with patch("tsr.os.ttyname", return_value="/dev/ttys001"), patch("tsr.inspect_live", return_value=live), patch("tsr.pick", return_value=set()) as picker:
            self.assertEqual(tsr.main(["restore", "--backup"], environment=env, home=self.home), 0)
            self.assertEqual(picker.call_args.args[0][0].key.split('/')[0], "saved")
        self.assertFalse((store.directory / "restore.lock").exists())
        self.assertEqual({p.name: p.read_bytes() for p in store.directory.iterdir()}, before)
        with patch("tsr.os.ttyname", side_effect=OSError), redirect_stderr(io.StringIO()) as err:
            self.assertEqual(tsr.main(["restore"], environment=env, home=self.home), 3)
        self.assertIn("run tsr restore inside", err.getvalue())
        with patch("tsr.inspect_live", side_effect=tsr.TsrError("Automation denied; " + tsr.AUTOMATION_FIX, 3)), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(tsr.main(["doctor"], environment=env, home=self.home), 3)
        self.assertIn("✗ Ghostty/Automation", out.getvalue())
        self.assertIn(tsr.AUTOMATION_FIX, out.getvalue())


class GhosttyRestoreContract(unittest.TestCase):
    def test_transport_escapes_values_waits_for_shell_and_never_types_into_existing_terminal(self):
        class Runner(tsr.Runner):
            def __init__(self): self.scripts=[]
            def run(self, argv, **kwargs):
                if argv[0] == 'ps': return tsr.CommandResult('123 1 ttys001 Tue Nov 10 00:00:00 2026 -/bin/zsh\n')
                self.scripts.append(argv[-1])
                return tsr.CommandResult('new-id' if 'new tab in' in argv[-1] else '123')
        runner = Runner()
        terminal = tsr.GhosttyMac(runner)
        with self.assertRaises(tsr.TsrError): terminal.type_and_run('existing', 'echo unsafe')
        target = terminal.open_tab('win"\\id', '/tmp/a"\\b')
        with self.assertRaises(tsr.TsrError): terminal.type_and_run(target, 'echo early')
        self.assertTrue(terminal.wait_for_shell(target))
        terminal.type_and_run(target, "printf '%s' '$HOME'")
        self.assertIn('initial working directory: "/tmp/a\\"\\\\b"', runner.scripts[0])
        self.assertIn('whose id is "win\\"\\\\id"', runner.scripts[0])
        self.assertIn('input text "printf \'%s\' \'$HOME\'"', runner.scripts[-1])
        self.assertIn('send key "enter"', runner.scripts[-1])


if __name__ == '__main__':
    unittest.main()
