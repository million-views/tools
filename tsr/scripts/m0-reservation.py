"""S16 spike only: approved new tabs, harmless probes, owner-run agent launch.

No production restore code. Never types an agent command or touches agent files.
Runtime measurements live in a private temporary directory, not in fixtures.
Preview anonymized observations before saving any fixture.
"""

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time


def run(argv):
    return subprocess.run(argv, check=True, text=True, capture_output=True,
                          timeout=15, env={**os.environ, "LC_ALL": "C", "TZ": "UTC"}).stdout.strip()


def quoted(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def apple(body):
    return run(["osascript", "-e", 'tell application "Ghostty"\n' + body + '\nend tell'])


def term_ref(identifier):
    return '(first terminal whose id is ' + quoted(identifier) + ')'


def processes():
    result = {}
    for line in run(["ps", "-A", "-ww", "-o", "pid=,ppid=,tty=,lstart=,args="]).splitlines():
        parts = line.split(None, 8)
        if len(parts) != 9:
            continue
        pid, parent, tty = parts[:3]
        start = ' '.join(parts[3:8])
        result[int(pid)] = {
            "pid": int(pid), "ppid": int(parent), "tty": normalize(tty),
            "start_utc": start,
            "start_epoch": datetime.strptime(start, "%a %b %d %H:%M:%S %Y").replace(tzinfo=UTC).timestamp(),
            "command_text": parts[8],
        }
    return result


def normalize(tty):
    return tty.removeprefix('/dev/').removeprefix('tty')


def write_private(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def identity(identifier):
    fields = apple('return {tty of ' + term_ref(identifier) + ', pid of ' + term_ref(identifier) + '}').split(', ')
    return normalize(fields[0]), int(fields[1])


def prepare():
    root = Path(tempfile.mkdtemp(prefix='tsr-s16-', dir='/private/tmp'))
    window = apple('return id of first window')
    state = {"root": str(root), "window_id": window, "tabs": {}}
    state_path = root / 'probe.json'
    for label in ('A', 'B'):
        startup = root / label
        startup.mkdir(mode=0o700)
        (startup / '.zshrc').write_text("PROMPT='TSR S16 " + label + "> '\n")
        reserved = time.time()
        config = ('new surface configuration from {initial working directory: ' + quoted(str(Path.cwd())) +
                  ', command: "/bin/zsh -d", environment variables: {' + quoted('ZDOTDIR=' + str(startup)) + '}}')
        created = apple('set cfg to ' + config + '\nset tb to new tab in (first window whose id is ' +
                        quoted(window) + ') with configuration cfg\nreturn {id of focused terminal of tb, index of tb}')
        terminal_id, index = created.split(', ')
        state['tabs'][label] = {"terminal_id": terminal_id, "tab_index": int(index), "reserved_at": reserved}
        write_private(state_path, state)
        deadline = time.monotonic() + 3
        while True:
            tty, pid = identity(terminal_id)
            process = processes().get(pid)
            if process and Path(process['command_text'].split()[0]).name.lstrip('-') in {'zsh', 'bash', 'fish', 'sh'}:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('new probe shell did not become ready')
            time.sleep(.1)
        time.sleep(.2)
        state['tabs'][label].update({"tty": tty, "initial_shell": process})
        write_private(state_path, state)
        # Only a harmless sleep handoff goes through automated terminal input.
        command = shlex.join([sys.executable, str(Path(__file__).resolve()), 'handoff', str(state_path), label, 'sleep'])
        apple('input text ' + quoted(command) + ' to ' + term_ref(terminal_id) +
              '\nsend key "enter" to ' + term_ref(terminal_id))
        record_path = root / (label + '-sleep.json')
        deadline = time.monotonic() + 5
        while not record_path.exists():
            if time.monotonic() >= deadline:
                raise RuntimeError('harmless handoff record missing')
            time.sleep(.05)
        record = json.loads(record_path.read_text())
        observed = None
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            candidate = processes().get(record['pid'])
            if candidate and candidate['command_text'].split()[0].endswith('sleep'):
                observed = candidate
                break
            time.sleep(.05)
        state['tabs'][label]['sleep_observed'] = observed
        state['tabs'][label]['sleep_pre_exec'] = record
        write_private(state_path, state)
        time.sleep(1.2)
    print(json.dumps({"runtime_state": str(state_path), "tabs": {
        label: {"index": tab['tab_index'], "tty": tab['tty'],
                "tty_matches_shell": tab['tty'] == tab['initial_shell']['tty'],
                "sleep_exec_observed": tab['sleep_observed'] is not None,
                "sleep_pid_start_preserved": bool(tab['sleep_observed'] and
                    tab['sleep_observed']['start_utc'] == tab['sleep_pre_exec']['start_utc']),
                "sleep_strictly_after_reservation": bool(tab['sleep_observed'] and
                    tab['sleep_observed']['start_epoch'] > tab['sleep_pre_exec']['reserved_at'])}
        for label, tab in state['tabs'].items()}}, indent=2))


def handoff(state_path, label, target):
    # codex is allowed here only through the owner's explicit shell command.
    state = json.loads(state_path.read_text())
    tty = normalize(os.ttyname(0))
    if tty != state['tabs'][label]['tty']:
        raise RuntimeError('run this command only in its designated S16 probe tab')
    before = processes()[os.getpid()]
    record = {**before, "reserved_at": time.time(), "target": target}
    write_private(Path(state['root']) / (label + '-' + target + '.json'), record)
    if target == 'sleep':
        os.execvp('/bin/sleep', ['sleep', '1'])
    os.execvp('codex', ['codex'])


def observe(state_path):
    state = json.loads(state_path.read_text())
    live = processes()
    report = {}
    baseline_path = Path(state['root']) / 'running-observation.json'
    baseline = json.loads(baseline_path.read_text()) if baseline_path.exists() else {}
    for label, tab in state['tabs'].items():
        try:
            tty, foreground = identity(tab['terminal_id'])
            terminal_exists = True
        except subprocess.CalledProcessError:
            # An owner-quit exec tab may have disappeared. Cached tty alone
            # cannot establish identity if that tty has since been reused.
            tty, foreground, terminal_exists = tab['tty'], None, False
        rows = []
        for process in live.values():
            if process['tty'] != tty:
                continue
            command = process['command_text'].split()[0]
            name = Path(command).name.lstrip('-')
            if name not in {'codex', 'node', 'zsh', 'bash', 'fish', 'sh', 'Python', 'python3.14'}:
                continue
            rows.append({key: process[key] for key in ('pid', 'ppid', 'tty', 'start_utc', 'start_epoch')})
            rows[-1].update({"executable_basename": name,
                             "after_tab_reservation": process['start_epoch'] > tab['reserved_at']})
        path = Path(state['root']) / (label + '-codex.json')
        handoff_record = json.loads(path.read_text()) if path.exists() else None
        recorded_agents = []
        for old in baseline.get(label, {}).get('processes', []):
            if old['executable_basename'] != 'codex':
                continue
            current = live.get(old['pid'])
            recorded_agents.append({"pid": old['pid'], "same_pid_and_start_live": bool(
                current and current['start_utc'] == old['start_utc']),
                "still_codex_on_same_tty": bool(current and current['start_utc'] == old['start_utc']
                    and current['tty'] == old['tty']
                    and Path(current['command_text'].split()[0]).name == 'codex')})
        report[label] = {"terminal_exists": terminal_exists,
                         "tty_still_matches": terminal_exists and tty == tab['tty'],
                         "foreground_pid": foreground, "processes": rows,
                         "owner_handoff": handoff_record,
                         "recorded_agents": recorded_agents}
    # Full observations remain in private runtime state; only projections are fixtures.
    write_private(Path(state['root']) / 'latest-observation.json', report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'handoff', 'observe'])
    parser.add_argument('state', type=Path, nargs='?')
    parser.add_argument('label', choices=['A', 'B'], nargs='?')
    parser.add_argument('target', choices=['sleep', 'codex'], nargs='?')
    args = parser.parse_args()
    if args.mode == 'prepare':
        prepare()
    elif args.mode == 'handoff' and args.state and args.label and args.target:
        handoff(args.state, args.label, args.target)
    elif args.mode == 'observe' and args.state:
        observe(args.state)
    else:
        parser.error('state/label/target required for this mode')
