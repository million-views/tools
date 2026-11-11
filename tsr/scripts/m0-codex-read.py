"""S11: read metadata from an existing Codex Unix WebSocket; never start/resume.

Only initialize, initialized, thread/loaded/list and thread/read are sent.
Output is projected and anonymized in memory. No files are written.
"""

import argparse
import base64
import hashlib
import json
from pathlib import Path
import secrets
import socket
import struct
import time


class RpcFailure(Exception):
    pass


def shape(value, depth=0):
    if depth >= 3:
        return type(value).__name__
    if isinstance(value, dict):
        # These fields may contain arbitrary metadata: retain structural keys only.
        return {key if key.isidentifier() and len(key) < 64 else 'REDACTED_KEY':
                shape(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [shape(item, depth + 1) for item in value[:2]]
    return type(value).__name__


class Reader:
    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(3)
        self.sock.connect(str(path))
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        request = ('GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n'
                   'Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n'
                   f'Sec-WebSocket-Key: {key}\r\n\r\n')
        self.sock.sendall(request.encode())
        header = b''
        while not header.endswith(b'\r\n\r\n'):
            header += self.exact(1)
            if len(header) > 16384:
                raise RpcFailure('oversized HTTP header')
        lines = header.decode('latin-1').split('\r\n')
        if lines[0].split()[1] != '101':
            raise RpcFailure('HTTP upgrade rejected')
        headers = {name.strip().lower(): value.strip()
                   for line in lines[1:] if ':' in line
                   for name, value in [line.split(':', 1)]}
        expected = base64.b64encode(hashlib.sha1(
            (key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        if headers.get('sec-websocket-accept') != expected:
            raise RpcFailure('invalid WebSocket accept')
        self.next_id = 0

    def exact(self, n):
        data = bytearray()
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise RpcFailure('connection closed')
            data.extend(chunk)
        return bytes(data)

    def send_frame(self, data, opcode=1):
        mask = secrets.token_bytes(4)
        n = len(data)
        if n < 126:
            header = bytes([0x80 | opcode, 0x80 | n])
        elif n < 65536:
            header = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack('!H', n)
        else:
            header = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack('!Q', n)
        encoded = bytes(value ^ mask[i % 4] for i, value in enumerate(data))
        self.sock.sendall(header + mask + encoded)

    def receive(self):
        message = bytearray()
        while True:
            first, second = self.exact(2)
            opcode = first & 15
            n = second & 127
            if n == 126:
                n = struct.unpack('!H', self.exact(2))[0]
            elif n == 127:
                n = struct.unpack('!Q', self.exact(8))[0]
            if n + len(message) > 32 * 1024 * 1024:
                raise RpcFailure('oversized response')
            mask = self.exact(4) if second & 128 else None
            payload = self.exact(n)
            if mask:
                payload = bytes(v ^ mask[i % 4] for i, v in enumerate(payload))
            if opcode == 8:
                raise RpcFailure('server closed connection')
            if opcode == 9:
                self.send_frame(payload, opcode=10)
                continue
            if opcode == 10:
                continue
            if opcode not in {0, 1}:
                raise RpcFailure('unexpected frame type')
            message.extend(payload)
            if first & 128:
                return json.loads(message)

    def send(self, message):
        self.send_frame(json.dumps(message).encode())

    def request(self, method, params):
        if method not in {'initialize', 'thread/loaded/list', 'thread/read'}:
            raise RpcFailure('method outside read-only probe allowlist')
        self.next_id += 1
        request_id = self.next_id
        self.send({'id': request_id, 'method': method, 'params': params})
        for _ in range(100):
            message = self.receive()
            if message.get('id') == request_id and 'method' not in message:
                if 'error' in message:
                    raise RpcFailure(f'RPC error code {message["error"].get("code")}')
                return message['result']
            if 'id' in message and 'method' in message:
                raise RpcFailure('unexpected server request; no response sent')
        raise RpcFailure('too many unrelated notifications')

    def close(self):
        try:
            self.send_frame(struct.pack('!H', 1000), opcode=8)
        except OSError:
            pass
        finally:
            self.sock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', type=Path, default=Path.home() / '.codex/app-server-control/app-server-control.sock')
    parser.add_argument('--previous-test-session')
    args = parser.parse_args()
    started = time.perf_counter()
    reader = None
    try:
        reader = Reader(args.socket)
        initialized = reader.request('initialize', {'clientInfo': {
            'name': 'tsr_m0_read_probe', 'title': 'tsr read-only spike', 'version': '0.0.0'}})
        reader.send({'method': 'initialized', 'params': {}})
        ids = []
        cursor = None
        for _ in range(20):
            page = reader.request('thread/loaded/list', {'cursor': cursor})
            ids.extend(page['data'])
            cursor = page.get('nextCursor')
            if not cursor:
                break
        folders = {}
        rows = []
        for index, thread_id in enumerate(ids):
            thread = reader.request('thread/read', {'threadId': thread_id, 'includeTurns': False})['thread']
            cwd = thread.get('cwd')
            folder = folders.setdefault(cwd, f'/Users/USER/PROJECT_{len(folders) + 1}')
            rows.append({'thread_id': f'SESSION_{index + 1}',
                         'is_previous_test_session': thread_id == args.previous_test_session,
                         'cwd': folder, 'status_type': thread.get('status', {}).get('type'),
                         'field_names': sorted(thread),
                         'extra_shape': shape(thread.get('extra')),
                         'environments_shape': shape(thread.get('environments')),
                         'pid_field_names': [key for key in thread if 'pid' in key.lower()]})
        previous_status = None
        read_did_not_load_previous = None
        if args.previous_test_session and args.previous_test_session not in ids:
            previous = reader.request('thread/read', {
                'threadId': args.previous_test_session, 'includeTurns': False})['thread']
            previous_status = previous.get('status', {}).get('type')
            again = reader.request('thread/loaded/list', {})
            read_did_not_load_previous = args.previous_test_session not in again['data']
        print(json.dumps({'transport': 'existing_unix_websocket',
                          'initialization_field_names': sorted(initialized),
                          'methods_sent': ['initialize', 'initialized', 'thread/loaded/list', 'thread/read'],
                          'loaded_count': len(ids), 'pagination_complete': not cursor,
                          'previous_test_status': previous_status,
                          'reading_previous_did_not_load_it': read_did_not_load_previous,
                          'threads': rows, 'elapsed_ms': round((time.perf_counter() - started) * 1000, 3)}))
    except (OSError, ValueError, KeyError, TypeError, RpcFailure) as exc:
        # Do not leak unprojected server responses, IDs or local paths on error.
        print(json.dumps({'probe_failed': True, 'error_type': type(exc).__name__,
                          'reason': str(exc) if isinstance(exc, RpcFailure) else 'transport or response inspection failed'}))
        raise SystemExit(1)
    finally:
        if reader is not None:
            reader.close()


if __name__ == '__main__':
    main()
