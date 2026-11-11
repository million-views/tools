"""Owner-approved M2 desktop acceptance probe; plain shells only.

Uses an isolated synthetic snapshot, never real agent resume commands. Opens a
new window and restores three plain-shell panes; sends input only to its terminal.
Leaves the window open. This is probe tooling, not a production adapter.
"""
import argparse
import json
from pathlib import Path
import shlex
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import tsr


def main():
    root = Path(tempfile.mkdtemp(prefix='tsr-v1-desktop-', dir='/private/tmp'))
    folders = [root / 'first', root / 'quotes " and backslash \\', root / 'third']
    for folder in folders:
        folder.mkdir(mode=0o700)
    store = tsr.Store(root / 'state/tsr')
    panes = [tsr.Pane(f'saved-{i}', '', str(folder)) for i, folder in enumerate(folders)]
    snap = tsr.Snapshot(1, 'desktop-probe', '2026-11-10T00:00:00Z', 1, 'probe', tsr.__version__,
                        {'app':'ghostty','version':'1.4.0'}, (tsr.Window(1,'saved-window',
                        tuple(tsr.Tab(i,str(i),'plain',(p,)) for i,p in enumerate(panes,1)), 'SHELL PROBE'),))
    store.save(snap)
    terminal = tsr.GhosttyMac(tsr.Runner())
    created = terminal.apple('set cfg to new surface configuration from {initial working directory: ' +
                             tsr.apple_string(str(root)) + '}\nset w to new window with configuration cfg\n'
                             'return {id of w, id of focused terminal of selected tab of w}')
    window_id, own = created.split(', ')
    terminal.created.add(own)
    if not terminal.wait_for_shell(own):
        raise RuntimeError('probe shell not ready')
    executable = Path(__file__).resolve().parents[1] / '.venv/bin/tsr'
    command = 'XDG_STATE_HOME=' + shlex.quote(str(root/'state')) + ' ' + shlex.join([str(executable), 'restore'])
    terminal.type_and_run(own, command)
    # Capture can take almost three seconds on a large desktop. No key is sent
    # until ps confirms the restore process on this created terminal's tty.
    deadline = time.monotonic() + 15
    tty = None
    while time.monotonic() < deadline:
        windows = terminal.list_layout()
        window = next(w for w in windows if w.terminal_window_id == window_id)
        pane = next(p for t in window.tabs for p in t.panes if p.terminal_id == own)
        tty = pane.tty
        processes = tsr.parse_processes(terminal.runner.run(['ps','-A','-ww','-o','pid=,ppid=,tty=,lstart=,args=']).stdout)
        if any(p.tty == tty and 'tsr restore' in p.command_text for p in processes.values()):
            break
        time.sleep(.1)
    else:
        raise RuntimeError('restore process not observed; inspect probe window')
    time.sleep(4)
    terminal.apple('send key "enter" to ' + tsr.terminal_ref(own))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        windows = terminal.list_layout()
        window = next(w for w in windows if w.terminal_window_id == window_id)
        if len(window.tabs) == 3:
            break
        time.sleep(.2)
    else:
        raise RuntimeError('expected three plain restore tabs; inspect probe window')
    time.sleep(.5)
    window = next(w for w in terminal.list_layout() if w.terminal_window_id == window_id)
    pids = [p.fg_pid for t in window.tabs for p in t.panes if p.fg_pid]
    files = tsr.parse_open_files(terminal.runner.run(['lsof','-n','-P','-a','-p',','.join(map(str,pids)),'-Fpfn']).stdout)
    actual = [files.cwd.get(p.fg_pid) or p.cwd for t in window.tabs for p in t.panes]
    expected = [str(p) for p in folders]
    leases = tsr.RestoreJournal(store).read()
    report = {'plain_restore_tabs':len(window.tabs),'ordered_cwds_match':actual==expected,
              'current_terminal_preserved':window.tabs[0].panes[0].terminal_id==own,
              'special_character_cwd_matches':actual[1]==expected[1],
              'snapshot_unchanged':store.load().id=='desktop-probe',
              'snapshot_mode':oct((store.directory/'snapshot.json').stat().st_mode & 0o777),
              'reservations':len(leases),'runtime_root':str(root),'window_id':window_id}
    print(json.dumps(report,indent=2))
    if not report['ordered_cwds_match']:
        raise RuntimeError('restored working directories differ; inspect probe window')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true',help='run only after explicit desktop approval')
    args = parser.parse_args()
    if not args.run:
        parser.error('requires --run and explicit desktop approval')
    main()
