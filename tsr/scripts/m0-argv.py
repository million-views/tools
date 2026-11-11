"""S10: real macOS ps output for harmless children with known argv. No files written."""

import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    vectors = [['TWO WORDS'], ['TWO', 'WORDS'], ["quote'arg", 'double"arg', 'slash\\arg', 'space arg']]
    children = []
    try:
        for vector in vectors:
            children.append(subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)', *vector],
                                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL))
        result = subprocess.run(['ps', '-ww', '-p', ','.join(str(p.pid) for p in children),
                                 '-o', 'pid=,args='], capture_output=True, text=True,
                                check=True, timeout=5, env={**os.environ, 'LC_ALL': 'C'})
        rows = {}
        for line in result.stdout.splitlines():
            pid, args = line.strip().split(None, 1)
            rows[int(pid)] = args
        home = str(Path.home())
        print(json.dumps({'known_argv_suffixes': vectors,
                          'ps_raw': result.stdout.replace(home, '/Users/USER'),
                          'different_vectors_same_ps_args': rows[children[0].pid] == rows[children[1].pid],
                          'child_pids': [p.pid for p in children],
                          'scope': 'generic ps serialization; these are Python children, not agents'}))
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)


if __name__ == '__main__':
    main()
