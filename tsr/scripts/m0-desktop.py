"""Approved S4/S7 probes only. Opens two windows and ten tabs; never restarts.

Run only after the owner explicitly approves desktop changes. Leaves probe
windows open for inspection. All commands target terminals created by this run.
Outputs a JSON report to stdout; inspect and anonymize it before saving fixtures.
"""

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time


def apple_string(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def run(argv):
    return subprocess.run(argv, text=True, capture_output=True, timeout=15,
                          check=True, env={**os.environ, "LC_ALL": "C"}).stdout.strip()


def apple(script):
    return run(["osascript", "-e", 'tell application "Ghostty"\n' + script + '\nend tell'])


def window_ref(window_id):
    return '(first window whose id is ' + apple_string(window_id) + ')'


def terminal_ref(terminal_id):
    return '(first terminal whose id is ' + apple_string(terminal_id) + ')'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--target')
    parser.add_argument('--front')
    parser.add_argument('--settle', type=float, default=0)
    parser.add_argument('--single-line', action='store_true', help='isolate startup timing from quoted-newline paste')
    args = parser.parse_args()
    if bool(args.target) != bool(args.front):
        parser.error('--target and --front must be supplied together')
    root = Path(tempfile.mkdtemp(prefix="tsr-m0-", dir="/private/tmp"))
    (root / ".zshrc").write_text(
        "printf 'TSR early startup output\\n'\n/bin/sleep 2\nPROMPT='TSR probe> '\n"
    )
    config = ('new surface configuration from {initial working directory: ' +
              apple_string(str(root)) + ', command: "/bin/zsh -d", ' +
              'environment variables: {' + apple_string('ZDOTDIR=' + str(root)) + '}}')
    target = args.target or apple('set cfg to ' + config + '\nset w to new window with configuration cfg\nreturn id of w')
    front = args.front or apple('set cfg to ' + config + '\nset w to new window with configuration cfg\nreturn id of w')
    report = {"slow_startup_seconds": 2, "settle_seconds": args.settle,
              "input_shape": "single-line" if args.single_line else "quoted-newline",
              "tabs": [], "probe_directory": str(root)}
    print(json.dumps({"created_target": target, "created_front": front}), flush=True)
    for i in range(10):
        apple('activate window ' + window_ref(front))
        before = apple('return {id of first window, count tabs of ' + window_ref(target) + '}')
        started = time.monotonic()
        created = apple('set cfg to ' + config + '\nset tb to new tab in ' +
                        window_ref(target) + ' with configuration cfg\nreturn {id of tb, id of focused terminal of tb, index of tb}')
        tab_id, terminal_id, index = created.split(', ')
        term = terminal_ref(terminal_id)
        samples = []
        ready = False
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            pid = apple('return pid of ' + term)
            try:
                executable = run(['ps', '-p', pid, '-o', 'comm='])
            except subprocess.CalledProcessError:
                executable = ''
            samples.append({"elapsed": round(time.monotonic() - started, 3),
                            "pid": pid, "executable": executable})
            if Path(executable).name.lstrip('-') in {'zsh', 'bash', 'fish', 'sh'}:
                ready = True
                break
            time.sleep(0.1)
        result = root / ('result_' + str(i))
        command = ('printf ' + shlex.quote('TSR_S4_' + str(i) + '\n') + ' > ' +
                   shlex.quote(str(result)) + '; tty >> ' + shlex.quote(str(result)) +
                   '; ps -o tty= -p $$ >> ' + shlex.quote(str(result)))
        if args.single_line:
            command = ("printf '%s\\n' " + shlex.quote('TSR_S4_' + str(i)) + ' > ' +
                       shlex.quote(str(result)) + '; tty >> ' + shlex.quote(str(result)) +
                       '; ps -o tty= -p $$ >> ' + shlex.quote(str(result)))
        if ready:
            time.sleep(args.settle)
            apple('input text ' + apple_string(command) + ' to ' + term +
                  '\nsend key "enter" to ' + term)
        deadline = time.monotonic() + 4
        while not result.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        # Allow the other two short commands to finish after the first creates it.
        time.sleep(0.2)
        output = result.read_text() if result.exists() else None
        after = apple('return {id of ' + window_ref(target) + ', count tabs of ' + window_ref(target) + '}')
        row = {"iteration": i, "front_and_count_before": before,
               "created_tab": tab_id, "index": int(index), "shell_found": ready,
               "samples": samples, "terminal_tty": apple('return tty of ' + term),
               "command_output": output, "target_and_count_after": after}
        report['tabs'].append(row)
        print(json.dumps(row), flush=True)
    print(json.dumps({"report": report}), flush=True)


if __name__ == '__main__':
    main()
