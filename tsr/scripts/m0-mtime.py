"""Read-only S2 measurement. Prints no real home path, prompt or session ID."""

import argparse
import json
from pathlib import Path
import re


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('agent', choices=['claude', 'codex', 'pi'])
    parser.add_argument('session_id')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9._:-]{6,128}', args.session_id):
        parser.error('invalid session ID')
    home = Path.home()
    roots = {
        'claude': (home / '.claude/projects', '*/*.jsonl'),
        'codex': (home / '.codex/sessions', '*/*/*/rollout-*.jsonl'),
        'pi': (home / '.pi/agent/sessions', '*/*.jsonl'),
    }
    root, pattern = roots[args.agent]
    files = [p for p in root.glob(pattern) if args.session_id in p.name]
    if len(files) != 1:
        parser.exit(1, f'Expected one transcript, found {len(files)}; check the session ID and default storage location.\n')
    stat = files[0].stat()
    print(json.dumps({'agent': args.agent, 'session_id': 'SESSION_ID',
                      'mtime_ns': str(stat.st_mtime_ns), 'size': stat.st_size}))


if __name__ == '__main__':
    main()
