"""S11 read-only registry probe; pipe UTC ps output on stdin.

LC_ALL=C TZ=UTC ps -A -ww -o pid=,ppid=,tty=,lstart=,args= |
    python3.14 tsr/scripts/m0-live-claude.py

Only projects fields needed for session identity. Never outputs peer tokens,
socket paths, names, prompts, actual session IDs or actual home/project paths.
This is spike tooling, not tsr implementation.
"""

import json
from pathlib import Path
import re
import sys
import time


def main():
    process_text = sys.stdin.read()
    started = time.perf_counter()
    processes = {}
    for line in process_text.splitlines():
        parts = line.split(None, 8)
        if len(parts) != 9:
            continue
        pid, ppid, tty = parts[:3]
        args = parts[8]
        if not re.match(r'(?:\S*/)?claude(?:\s|$)', args):
            continue
        processes[int(pid)] = {'tty': tty, 'start': ' '.join(parts[3:8]), 'args': args}
    home = Path.home()
    sessions = {}
    folders = {}
    result = []
    for path in sorted((home / '.claude/sessions').glob('*.json')):
        try:
            obj = json.loads(path.read_text())
            pid = obj['pid']
            session = obj['sessionId']
            cwd = obj['cwd']
            proc_start = obj['procStart']
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if pid not in processes or path.stem != str(pid):
            continue
        if not isinstance(session, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{6,128}', session):
            continue
        if not isinstance(cwd, str) or not isinstance(proc_start, str):
            continue
        session_alias = sessions.setdefault(session, f'SESSION_{len(sessions) + 1}')
        cwd_alias = folders.setdefault(cwd, f'/Users/USER/PROJECT_{len(folders) + 1}')
        process = processes[pid]
        argv_match = re.search(r'(?:--resume|-r|--session-id)\s+(\S+)', process['args'])
        transcripts = list((home / '.claude/projects').glob(f'*/{session}.jsonl'))
        transcript_id_matches = False
        if len(transcripts) == 1:
            try:
                with transcripts[0].open() as transcript:
                    for index, line in enumerate(transcript):
                        if index >= 200:
                            break
                        try:
                            record = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(record, dict) and record.get('sessionId') == session:
                            transcript_id_matches = True
                            break
            except OSError:
                pass
        result.append({
            'pid': pid, 'tty': process['tty'], 'session_id': session_alias,
            'cwd': cwd_alias, 'registry_proc_start': proc_start,
            'ps_start_utc': process['start'],
            'start_matches': ' '.join(proc_start.split()) == process['start'],
            'transcript_files': len(transcripts),
            'transcript_session_id_matches': transcript_id_matches,
            'argv_session_matches_registry': argv_match.group(1) == session if argv_match else None,
        })
    print(json.dumps({'observed_claude_processes': len(processes),
                      'live_registry_rows': result,
                      'registry_probe_ms': round((time.perf_counter() - started) * 1000, 3)}))


if __name__ == '__main__':
    main()
