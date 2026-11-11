"""Save, inspect and restore Ghostty sessions. Python 3.14, standard library only."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import ctypes
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from itertools import islice
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Protocol
import uuid

__version__ = "0.3.0"
FORMAT = 2
SESSION_ID = re.compile(r"[A-Za-z0-9._:-]{6,128}\Z")
AUTOMATION_FIX = "System Settings → Privacy & Security → Automation → allow the requesting app to control Ghostty"


# runner: the only process/native-platform boundary.
class TsrError(Exception):
    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str = ""
    returncode: int = 0


class Runner:
    def run(self, argv: list[str], *, timeout: float = 5) -> CommandResult:
        try:
            result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout,
                                    env={**os.environ, "LC_ALL": "C", "TZ": "UTC"})
        except FileNotFoundError as exc:
            raise TsrError(f"{argv[0]} is unavailable; install the macOS prerequisites", 3) from exc
        except subprocess.TimeoutExpired as exc:
            message = "Ghostty didn't answer; retry or run tsr doctor" if argv[0] == "osascript" else f"{argv[0]} timed out; retry capture"
            raise TsrError(message) from exc
        return CommandResult(result.stdout, result.stderr, result.returncode)

    def boot_time(self) -> int:
        if sys.platform != "darwin":
            raise TsrError("save requires macOS and Ghostty 1.4 or newer", 3)

        class Timeval(ctypes.Structure):
            _fields_ = [("seconds", ctypes.c_long), ("microseconds", ctypes.c_int32)]

        value = Timeval()
        size = ctypes.c_size_t(ctypes.sizeof(value))
        try:
            function = ctypes.CDLL(None, use_errno=True).sysctlbyname
        except (AttributeError, OSError) as exc:
            raise TsrError("native macOS boot-time API is unavailable; update tsr on this platform", 3) from exc
        function.argtypes = [ctypes.c_char_p, ctypes.c_void_p,
                            ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
        function.restype = ctypes.c_int
        if function(b"kern.boottime", ctypes.byref(value), ctypes.byref(size), None, 0) != 0:
            raise TsrError("couldn't read macOS boot time; check process permissions and retry")
        if size.value != ctypes.sizeof(value) or value.seconds <= 0:
            raise TsrError("macOS returned an unsupported boot-time format; update tsr")
        return value.seconds


# model: immutable capture data; no effects.
@dataclass(frozen=True)
class SessionRef:
    agent: str
    session_id: str | None = None
    session_file: str | None = None


@dataclass(frozen=True)
class Description:
    first_prompt: str | None = None
    git_branch: str | None = None


@dataclass(frozen=True)
class AgentInfo:
    name: str
    pid: int
    command_text: str
    session_id: str | None
    session_file: str | None
    confidence: str
    method: str
    first_prompt: str | None
    git_branch: str | None
    resume_argv: tuple[str, ...]
    argv: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Pane:
    terminal_id: str
    tty: str
    cwd: str | None
    fg_pid: int = 0
    agent: AgentInfo | None = None


@dataclass(frozen=True)
class Tab:
    index: int
    terminal_tab_id: str
    title: str
    panes: tuple[Pane, ...]


@dataclass(frozen=True)
class Window:
    index: int
    terminal_window_id: str
    tabs: tuple[Tab, ...]
    label: str = ""


@dataclass(frozen=True)
class Snapshot:
    format: int
    id: str
    saved_at: str
    boot_time: int
    host: str
    tsr_version: str
    terminal: dict[str, str]
    windows: tuple[Window, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> Snapshot:
        try:
            obj = json.loads(text)
            if not isinstance(obj, dict) or type(obj.get("format")) is not int:
                raise ValueError("missing format")
            if obj["format"] > FORMAT:
                raise TsrError("snapshot format is newer than this tsr; upgrade tsr")
            if obj["format"] not in {1, 2}:
                raise ValueError("unsupported format")
            windows = []
            for w in obj["windows"]:
                if not isinstance(w["label"], str) or type(w["index"]) is not int:
                    raise ValueError("invalid window")
                tabs = []
                for t in w["tabs"]:
                    panes = []
                    for p in t["panes"]:
                        a = p.get("agent")
                        if a is not None:
                            if not isinstance(a, dict) or not isinstance(a.get("name"), str):
                                raise ValueError("invalid agent")
                            if (not isinstance(a.get("resume_argv"), list) or not a["resume_argv"] or
                                    not all(isinstance(arg, str) for arg in a["resume_argv"])):
                                raise ValueError("invalid resume command")
                            if a.get("confidence") not in {"exact", "likely", "picker"}:
                                raise ValueError("invalid confidence")
                            a = AgentInfo(**{**a, "resume_argv": tuple(a["resume_argv"]),
                                             "argv": tuple(a["argv"]) if a.get("argv") else None})
                        cwd = p["cwd"]
                        if obj["format"] == 1 and (not isinstance(cwd, str) or not cwd):
                            raise ValueError("missing folder")
                        if obj["format"] == 2 and cwd is not None and (not isinstance(cwd, str) or not cwd):
                            raise ValueError("invalid folder")
                        panes.append(Pane(p["terminal_id"], p["tty"], cwd, p.get("fg_pid", 0), a))
                    tabs.append(Tab(t["index"], t.get("terminal_tab_id", ""), t["title"], tuple(panes)))
                windows.append(Window(w["index"], w["terminal_window_id"], tuple(tabs), w["label"]))
            datetime.fromisoformat(obj["saved_at"].replace("Z", "+00:00"))
            if type(obj["boot_time"]) is not int:
                raise ValueError("invalid boot time")
            return cls(obj["format"], obj["id"], obj["saved_at"], obj["boot_time"],
                       obj["host"], obj["tsr_version"], obj["terminal"], tuple(windows))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise TsrError("snapshot is malformed; try tsr list --backup") from exc


@dataclass(frozen=True)
class Process:
    pid: int
    ppid: int
    tty: str
    start_utc: str
    started_at: float
    command_text: str


@dataclass(frozen=True)
class OpenFiles:
    cwd: dict[int, str]
    paths: dict[int, tuple[str, ...]]
    uncertain_cwd: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Transcript:
    ref: SessionRef
    cwd: str | None
    first_timestamp: float
    mtime: float
    description: Description


@dataclass(frozen=True)
class TranscriptFacts:
    session_id: str | None = None
    cwd: str | None = None
    first_timestamp: float = 0.0
    prompt: str | None = None
    branch: str | None = None

    @property
    def complete(self) -> bool:
        return bool(self.session_id and self.cwd and self.first_timestamp and self.prompt is not None)


# store: only tsr-owned paths are writable; new current is always staged first.
class Store:
    def __init__(self, directory: Path, protected: tuple[Path, ...] = ()):
        self.directory = directory
        resolved = directory.resolve()
        if any(resolved.is_relative_to(root.resolve()) for root in protected):
            raise TsrError("tsr state directory overlaps agent storage; choose a different XDG_STATE_HOME")

    def ensure_directory(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)

    def load(self, *, backup: bool = False, required: bool = True) -> Snapshot | None:
        path = self.directory / ("snapshot.prev.json" if backup else "snapshot.json")
        try:
            return Snapshot.from_json(path.read_text())
        except FileNotFoundError as exc:
            if required:
                message = "no backup snapshot; save again to create a backup" if backup else "nothing saved yet; run tsr save before restarting"
                raise TsrError(message, 3) from exc
            return None

    def _stage(self, data: bytes) -> Path:
        fd, filename = tempfile.mkstemp(prefix=".tsr-", dir=self.directory)
        path = Path(filename)
        try:
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path

    def save(self, snapshot: Snapshot) -> None:
        data = snapshot.to_json().encode()
        self.ensure_directory()
        current = self.directory / "snapshot.json"
        previous = self.directory / "snapshot.prev.json"
        staged: list[Path] = []
        rollback: Path | None = None
        rotated = False
        try:
            new = self._stage(data)
            staged.append(new)
            if current.exists():
                old = self._stage(current.read_bytes())
                staged.append(old)
                if previous.exists():
                    rollback = self._stage(previous.read_bytes())
                    staged.append(rollback)
                os.replace(old, previous)
                rotated = True
            try:
                os.replace(new, current)
            except OSError:
                if rotated:
                    try:
                        if rollback is not None:
                            os.replace(rollback, previous)
                        else:
                            previous.unlink()
                    except OSError as exc:
                        if rollback is not None:
                            staged.remove(rollback)
                        recovery = f"original backup retained at {rollback}" if rollback else "current snapshot is unchanged"
                        raise TsrError(f"snapshot commit failed; {recovery}; check filesystem permissions") from exc
                raise
        finally:
            for path in staged:
                path.unlink(missing_ok=True)


# macos: deterministic parsers are independent of command execution.
def normalize_tty(value: str) -> str:
    value = value.removeprefix("/dev/")
    return value.removeprefix("tty")


def parse_processes(text: str) -> dict[int, Process]:
    result = {}
    for line in text.splitlines():
        fields = line.split(None, 8)
        if len(fields) != 9:
            continue
        pid, ppid, tty = fields[:3]
        start = " ".join(fields[3:8])
        try:
            timestamp = datetime.strptime(start, "%a %b %d %H:%M:%S %Y").replace(tzinfo=UTC).timestamp()
            process = Process(int(pid), int(ppid), normalize_tty(tty), start, timestamp, fields[8])
        except ValueError:
            continue
        result[process.pid] = process
    return result


def parse_open_files(text: str) -> OpenFiles:
    cwd: dict[int, str] = {}
    paths: dict[int, list[str]] = defaultdict(list)
    pid, descriptor = None, None
    uncertain = set()
    for line in text.splitlines():
        if line.startswith("p"):
            try:
                pid = int(line[1:])
            except ValueError:
                pid = None
            descriptor = None
        elif line.startswith("f"):
            descriptor = line[1:]
        elif line.startswith("n") and pid is not None:
            value = decode_lsof_path(line[1:])
            if descriptor == "cwd":
                if value is None:
                    uncertain.add(pid)
                else:
                    cwd[pid] = value
            elif value and value.startswith("/"):
                paths[pid].append(value)
    return OpenFiles(cwd, {pid: tuple(items) for pid, items in paths.items()}, frozenset(uncertain))


def decode_lsof_path(value: str) -> str | None:
    # F54: field output is escaped display text, not a literal pathname.
    if re.search(r"\^[@A-Z\[\]\\^_?]", value):
        return None
    controls = {"b": 8, "f": 12, "r": 13, "n": 10, "t": 9, "\\": 92}
    out = bytearray()
    i = 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            code = value[i + 1]
            if code in controls:
                out.append(controls[code])
                i += 2
                continue
            if code == "x" and re.fullmatch(r"[0-9a-fA-F]{2}", value[i + 2:i + 4]):
                out.append(int(value[i + 2:i + 4], 16))
                i += 4
                continue
            return None
        out.extend(value[i].encode("utf-8", "surrogateescape"))
        i += 1
    return out.decode("utf-8", "surrogateescape")


def descendants(pid: int, processes: dict[int, Process]) -> frozenset[int]:
    children: dict[int, list[int]] = defaultdict(list)
    for process in processes.values():
        children[process.ppid].append(process.pid)
    found, pending = set(), [pid]
    while pending:
        current = pending.pop()
        if current in found:
            continue
        found.add(current)
        pending.extend(children[current])
    return frozenset(found)


# terminal_ghostty_mac: status, version and layout share one read-only script.
LAYOUT_SCRIPT = r'''
on clean_field(value)
    set value to value as text
    set AppleScript's text item delimiters to {ASCII character 9, ASCII character 10, ASCII character 13}
    set parts to text items of value
    set AppleScript's text item delimiters to " "
    set value to parts as text
    set AppleScript's text item delimiters to ""
    return value
end clean_field
if not (application "Ghostty" is running) then return "TSR_ERROR" & (ASCII character 9) & "NOT_RUNNING"
tell application "Ghostty"
    set app_version to version
    set rows to "TSR_VERSION" & (ASCII character 9) & app_version & (ASCII character 10)
    considering numeric strings
        if app_version is less than "1.4" then return rows
    end considering
    set window_index to 0
    repeat with w in (get windows)
        set window_index to window_index + 1
        set window_id to id of w
        repeat with tab_fields in (get properties of every tab of w)
            set tab_identifier to id of tab_fields
            set tb to tab id tab_identifier of w
            repeat with terminal_fields in (get properties of every terminal of tb)
                set fields to {window_index, window_id, index of tab_fields, id of tab_fields, my clean_field(name of tab_fields), id of terminal_fields, tty of terminal_fields, working directory of terminal_fields, pid of terminal_fields}
                set AppleScript's text item delimiters to ASCII character 9
                set rows to rows & (fields as text) & (ASCII character 10)
                set AppleScript's text item delimiters to ""
            end repeat
        end repeat
    end repeat
    return rows
end tell
'''


def parse_layout(text: str) -> tuple[str, tuple[Window, ...]]:
    version = ""
    windows: dict[tuple[int, str], dict[tuple[int, str, str], list[Pane]]] = {}
    for line in text.splitlines():
        if not line:
            continue
        fields = line.split("\t")
        if fields[0] == "TSR_ERROR":
            raise TsrError("Ghostty is not running; open Ghostty and retry", 3)
        if fields[0] == "TSR_VERSION":
            version = fields[1] if len(fields) == 2 else ""
            continue
        if len(fields) != 9:
            raise TsrError("Ghostty returned malformed layout fields; check titles/folders and retry")
        wi, wid, ti, tid, title, terminal_id, tty, cwd, pid = fields
        try:
            window_key = (int(wi), wid)
            tab_key = (int(ti), tid, title)
            pane = Pane(terminal_id, normalize_tty(tty), cwd, int(pid))
        except ValueError as exc:
            raise TsrError("Ghostty returned invalid terminal identifiers; retry capture") from exc
        windows.setdefault(window_key, {}).setdefault(tab_key, []).append(pane)
    if version:
        numbers = re.match(r"(\d+)\.(\d+)", version)
        if not numbers or tuple(map(int, numbers.groups())) < (1, 4):
            raise TsrError("Ghostty 1.4 or newer is required; update Ghostty", 3)
    result = tuple(Window(wi, wid, tuple(Tab(ti, tid, title, tuple(panes))
                                       for (ti, tid, title), panes in sorted(tabs.items())))
                   for (wi, wid), tabs in windows.items())
    return version, result


class GhosttyMac:
    name = "ghostty"
    capabilities = frozenset({"list_layout", "open_tab", "type_into"})

    def __init__(self, runner: Runner):
        self.runner = runner
        self.version = ""
        self.created: set[str] = set()
        self.ready: set[str] = set()
        self.shell_processes: dict[str, Process] = {}

    def apple(self, body: str, *, timeout: float = 5) -> str:
        result = self.runner.run(["osascript", "-e", 'tell application "Ghostty"\n' + body + '\nend tell'], timeout=timeout)
        if result.returncode:
            if "-1743" in result.stderr:
                raise TsrError(f"Automation permission denied; {AUTOMATION_FIX}", 3)
            raise TsrError("Ghostty command failed; retry or run tsr doctor")
        return result.stdout.strip()

    def open_tab(self, window_id: str, cwd: str) -> str:
        identifier = self.apple('set cfg to new surface configuration from {initial working directory: ' +
                                apple_string(cwd) + '}\nset tb to new tab in (first window whose id is ' +
                                apple_string(window_id) + ') with configuration cfg\nreturn id of focused terminal of tb')
        if not identifier:
            raise TsrError("Ghostty did not return a new terminal ID; inspect the new tab")
        self.created.add(identifier)
        return identifier

    def wait_for_shell(self, terminal_id: str, timeout: float = 3) -> bool:
        if terminal_id not in self.created:
            raise TsrError("refusing to type into a terminal not created by this restore")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            raw = self.apple('return pid of ' + terminal_ref(terminal_id), timeout=max(.01, remaining))
            if raw.isdecimal():
                result = self.runner.run(["ps", "-p", raw, "-o", "pid=,ppid=,tty=,lstart=,comm="],
                                         timeout=max(.01, deadline - time.monotonic()))
                process = parse_processes(result.stdout).get(int(raw))
                name = Path(process.command_text).name.lstrip("-") if process else ""
                if not result.returncode and name in {"zsh", "bash", "fish", "sh", Path(os.environ.get("SHELL", "/bin/zsh")).name}:
                    if deadline - time.monotonic() < .2:
                        return False
                    time.sleep(.2)
                    self.ready.add(terminal_id)
                    self.shell_processes[terminal_id] = process
                    return True
            time.sleep(min(.1, max(0, deadline - time.monotonic())))
        return False

    def type_and_run(self, terminal_id: str, text: str) -> None:
        if terminal_id not in self.created or terminal_id not in self.ready:
            raise TsrError("refusing to type before a newly created terminal's shell is ready")
        self.apple('input text ' + apple_string(text) + ' to ' + terminal_ref(terminal_id) +
                   '\nsend key "enter" to ' + terminal_ref(terminal_id))

    def focus(self, terminal_id: str) -> None:
        self.apple('focus ' + terminal_ref(terminal_id))

    def list_layout(self) -> tuple[Window, ...]:
        result = self.runner.run(["osascript", "-e", LAYOUT_SCRIPT], timeout=15)
        if result.returncode:
            if "-1743" in result.stderr:
                raise TsrError(f"Automation permission denied; {AUTOMATION_FIX}", 3)
            raise TsrError("couldn't read Ghostty layout; check Automation permission and Ghostty AppleScript settings")
        self.version, windows = parse_layout(result.stdout)
        if not self.version:
            raise TsrError("Ghostty did not report its version; update Ghostty and retry", 3)
        if not windows:
            raise TsrError("nothing to save; open a Ghostty terminal and retry", 3)
        return windows


class TerminalAdapter(Protocol):
    name: str
    version: str
    capabilities: frozenset[str]
    shell_processes: dict[str, Process]

    def list_layout(self) -> tuple[Window, ...]: ...
    def open_tab(self, window_id: str, cwd: str) -> str: ...
    def wait_for_shell(self, terminal_id: str, timeout: float = 3) -> bool: ...
    def type_and_run(self, terminal_id: str, text: str) -> None: ...
    def focus(self, terminal_id: str) -> None: ...


def apple_string(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def terminal_ref(identifier: str) -> str:
    return '(first terminal whose id is ' + apple_string(identifier) + ')'


# agent adapters: transcripts and runtime files are read only.
def home_path(value: str | Path, home: Path) -> Path:
    text = str(value)
    if text == "~":
        return home
    if text.startswith("~/"):
        return home / text[2:]
    return Path(value)


def timestamp(value: object) -> float:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=parsed.tzinfo or UTC).timestamp()
        except ValueError:
            pass
    return 0.0


def prompt_text(content: object) -> str | None:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        if any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content):
            return None
        text = " ".join(block.get("text", "") for block in content
                        if isinstance(block, dict) and block.get("type") in {"text", "input_text"}
                        and isinstance(block.get("text"), str))
    else:
        return None
    if not text.strip() or text.lstrip().startswith("<"):
        return None
    return " ".join("".join(c if c.isprintable() else " " for c in text).split())[:200] or None


def transcript_facts(name: str, obj: dict, facts: TranscriptFacts) -> TranscriptFacts:
    """Purely fold one decoded agent record into the transcript summary."""
    kind = obj.get("type")
    record = obj.get("payload", {}) if name == "codex" else obj
    if not isinstance(record, dict):
        return facts
    session_id, cwd, branch = facts.session_id, facts.cwd, facts.branch
    identity_record = (name == "claude" or name == "codex" and kind == "session_meta" or
                       name == "pi" and kind == "session")
    if identity_record:
        sid = record.get("sessionId") if name == "claude" else record.get("id") or record.get("session_id")
        if session_id is None and isinstance(sid, str) and SESSION_ID.fullmatch(sid):
            session_id = sid
        if cwd is None and isinstance(record.get("cwd"), str):
            cwd = record["cwd"]
        git = record.get("git")
        branch_value = record.get("gitBranch") or (git.get("branch") if isinstance(git, dict) else None)
        if branch is None and isinstance(branch_value, str):
            branch = branch_value
    first = facts.first_timestamp or timestamp(record.get("timestamp")) or timestamp(obj.get("timestamp"))
    message = obj.get("message") if name != "codex" else record
    user = ((name == "claude" and kind == "user") or
            (name == "pi" and kind == "message") or
            (name == "codex" and kind == "response_item"))
    prompt = facts.prompt
    if prompt is None and user and isinstance(message, dict) and message.get("role") == "user":
        prompt = prompt_text(message.get("content"))
    return TranscriptFacts(session_id, cwd, first, prompt, branch)


def build_transcript(name: str, path: Path, mtime: float, facts: TranscriptFacts) -> Transcript | None:
    """Turn parsed transcript facts into the domain value without reading files or clocks."""
    if facts.session_id is None:
        return None
    return Transcript(SessionRef(name, facts.session_id, str(path)), facts.cwd,
                      facts.first_timestamp or mtime, mtime,
                      Description(facts.prompt, facts.branch))


def read_records(path: Path):
    with path.open() as stream:
        for line in islice(stream, 200):
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                yield value


class AgentAdapter(Protocol):
    name: str
    display: str
    resume_scope: str

    def matches(self, command_text: str) -> bool: ...
    def session_from_runtime(self, pid: int, start_utc: str) -> SessionRef | None: ...
    def session_from_argv(self, command_text: str) -> SessionRef | None: ...
    def session_from_open_files(self, paths: tuple[str, ...]) -> SessionRef | None: ...
    def candidates(self, cwd: str, since: float) -> tuple[Transcript, ...]: ...
    def describe(self, ref: SessionRef) -> Description: ...
    def resume_argv(self, ref: SessionRef | None) -> tuple[str, ...]: ...
    def valid_ref(self, ref: SessionRef) -> bool: ...
    def runtime_cwd(self, pid: int, start_utc: str) -> str | None: ...


class TranscriptAgent:
    name = ""
    display = ""
    resume_scope = "global"

    def __init__(self, home: Path, roots: tuple[Path, ...]):
        self.home, self.roots = home, roots
        self._cache: dict[Path, tuple[int, Transcript | None]] = {}

    def matches(self, command_text: str) -> bool:
        return re.match(rf"(?:\S*/)?{re.escape(self.name)}(?:\s|$)", command_text) is not None

    def valid_path(self, path: Path) -> bool:
        resolved = path.resolve()
        return path.is_absolute() and path.suffix == ".jsonl" and any(
            resolved.is_relative_to(root.resolve()) for root in self.roots)

    def paths(self, since: float | None = None):
        for root in self.roots:
            yield from root.glob("*/*.jsonl")

    def transcript(self, path: Path) -> Transcript | None:
        if not self.valid_path(path):
            return None
        stat = path.stat()
        cached = self._cache.get(path)
        if cached and cached[0] == stat.st_mtime_ns:
            return cached[1]
        facts = TranscriptFacts()
        for obj in read_records(path):
            facts = transcript_facts(self.name, obj, facts)
            if facts.complete:
                break
        result = build_transcript(self.name, path, stat.st_mtime, facts)
        self._cache[path] = (stat.st_mtime_ns, result)
        return result

    def find(self, session_id: str) -> SessionRef | None:
        if not SESSION_ID.fullmatch(session_id):
            return None
        matches = []
        for path in self.paths():
            if session_id not in path.name:
                continue
            transcript = self.transcript(path)
            if transcript and transcript.ref.session_id == session_id:
                matches.append(transcript.ref)
        return matches[0] if len(matches) == 1 else None

    def session_from_runtime(self, pid: int, start_utc: str) -> SessionRef | None:
        return None

    def runtime_cwd(self, pid: int, start_utc: str) -> str | None:
        return None

    def valid_ref(self, ref: SessionRef) -> bool:
        return bool(ref.session_id and SESSION_ID.fullmatch(ref.session_id) and not ref.session_id.startswith("-"))

    def session_from_open_files(self, paths: tuple[str, ...]) -> SessionRef | None:
        refs = set()
        for item in paths:
            path = Path(item)
            if self.valid_path(path):
                transcript = self.transcript(path)
                if transcript:
                    refs.add(transcript.ref)
        return next(iter(refs)) if len(refs) == 1 else None

    def candidates(self, cwd: str, since: float) -> tuple[Transcript, ...]:
        found = []
        for path in self.paths(since):
            try:
                if path.stat().st_mtime < since:
                    continue
                transcript = self.transcript(path)
                if transcript and transcript.cwd and os.path.realpath(transcript.cwd) == cwd:
                    found.append(transcript)
            except (OSError, ValueError, TypeError):
                continue
        return tuple(found)

    def describe(self, ref: SessionRef) -> Description:
        transcript = self.transcript(Path(ref.session_file)) if ref.session_file else None
        return transcript.description if transcript else Description()


class ClaudeAdapter(TranscriptAgent):
    name, display = "claude", "Claude"
    resume_scope = "folder"

    def __init__(self, home: Path):
        super().__init__(home, (home / ".claude/projects",))

    def valid_path(self, path: Path) -> bool:
        if not super().valid_path(path):
            return False
        try:
            relative = path.resolve().relative_to(self.roots[0].resolve())
            uuid.UUID(path.stem)
            return len(relative.parts) == 2
        except ValueError:
            return False

    def session_from_runtime(self, pid: int, start_utc: str) -> SessionRef | None:
        obj = json.loads((self.home / ".claude/sessions" / f"{pid}.json").read_text())
        if not isinstance(obj, dict):
            return None
        if obj.get("pid") != pid or " ".join(str(obj.get("procStart", "")).split()) != start_utc:
            return None
        sid = obj.get("sessionId")
        return self.find(sid) if isinstance(sid, str) else None

    def runtime_cwd(self, pid: int, start_utc: str) -> str | None:
        obj = json.loads((self.home / ".claude/sessions" / f"{pid}.json").read_text())
        if (isinstance(obj, dict) and obj.get("pid") == pid and
                " ".join(str(obj.get("procStart", "")).split()) == start_utc and
                isinstance(obj.get("cwd"), str) and Path(obj["cwd"]).is_absolute()):
            return os.path.realpath(obj["cwd"])
        return None

    def session_from_argv(self, command_text: str) -> SessionRef | None:
        match = re.search(r"(?:^|\s)(?:--resume|-r|--session-id)\s+([A-Za-z0-9._:-]{6,128})(?=\s|$)", command_text)
        if not match or match[1].startswith("-"):
            return None
        return self.find(match[1]) or SessionRef(self.name, match[1])

    def resume_argv(self, ref: SessionRef | None) -> tuple[str, ...]:
        return ("claude", "--resume", ref.session_id) if ref and ref.session_id else ("claude", "--resume")


class CodexAdapter(TranscriptAgent):
    name, display = "codex", "Codex"

    def __init__(self, home: Path):
        super().__init__(home, (home / ".codex/sessions",))

    def matches(self, command_text: str) -> bool:
        if not super().matches(command_text):
            return False
        return re.match(r"(?:\S*/)?codex\s+(?:app-server|exec-server)(?:\s|$)", command_text) is None

    def valid_path(self, path: Path) -> bool:
        if not super().valid_path(path):
            return False
        parts = path.resolve().relative_to(self.roots[0].resolve()).parts
        return len(parts) == 4 and path.name.startswith("rollout-")

    def paths(self, since: float | None = None):
        minimum = datetime.fromtimestamp(since, UTC).date() if since is not None else date.min
        root = self.roots[0]
        for folder in root.glob("*/*/*"):
            try:
                year, month, day = map(int, folder.relative_to(root).parts)
                if date(year, month, day) >= minimum:
                    yield from folder.glob("rollout-*.jsonl")
            except ValueError:
                continue

    def session_from_argv(self, command_text: str) -> SessionRef | None:
        match = re.match(r"(?:\S*/)?codex\s+resume\s+([A-Za-z0-9._:-]{6,128})(?=\s|$)", command_text)
        if not match or match[1].startswith("-"):
            return None
        return self.find(match[1]) or SessionRef(self.name, match[1])

    def resume_argv(self, ref: SessionRef | None) -> tuple[str, ...]:
        return ("codex", "resume", ref.session_id) if ref and ref.session_id else ("codex", "resume")


class PiAdapter(TranscriptAgent):
    name, display = "pi", "pi"

    def __init__(self, home: Path, environment: dict[str, str]):
        agent = home_path(environment.get("PI_CODING_AGENT_DIR") or home / ".pi/agent", home)
        override = environment.get("PI_CODING_AGENT_SESSION_DIR")
        root = home_path(override, home) if override else agent / "sessions"
        super().__init__(home, (root.resolve(),))

    def paths(self, since: float | None = None):
        for root in self.roots:
            yield from root.glob("*.jsonl")
            yield from root.glob("*/*.jsonl")

    def valid_ref(self, ref: SessionRef) -> bool:
        return bool(ref.session_file and self.valid_path(Path(ref.session_file)))

    def session_from_argv(self, command_text: str) -> SessionRef | None:
        match = re.search(r"(?:^|\s)--session\s+(\S+)\s*$", command_text)
        if not match:
            return None
        value = match[1]
        if value.startswith("/"):
            path = Path(value)
            if self.valid_path(path):
                transcript = self.transcript(path)
                return transcript.ref if transcript else None
            return None
        return self.find(value)

    def resume_argv(self, ref: SessionRef | None) -> tuple[str, ...]:
        return ("pi", "--session", ref.session_file) if ref and ref.session_file else ("pi", "--resume")


# identify: pure choices, given the evidence gathered by adapters.
@dataclass(frozen=True)
class Identification:
    ref: SessionRef | None
    confidence: str
    method: str


@dataclass(frozen=True)
class Observation:
    terminal_id: str
    agent: str
    cwd: str
    process: Process
    evidence: tuple[Identification, ...]


def outermost_agents(processes: dict[int, Process], adapters: tuple[AgentAdapter, ...]) -> tuple[tuple[Process, AgentAdapter], ...]:
    matches = [(p, a) for p in processes.values() for a in adapters if a.matches(p.command_text)]
    matched_ttys = {p.pid: p.tty for p, _ in matches}
    outer = []
    for process, adapter in matches:
        ancestor, seen = process.ppid, {process.pid}
        while ancestor in processes and ancestor not in seen:
            if matched_ttys.get(ancestor) == process.tty:
                break
            seen.add(ancestor)
            ancestor = processes[ancestor].ppid
        else:
            outer.append((process, adapter))
    return tuple(outer)


def select_agents(panes: tuple[Pane, ...], processes: dict[int, Process],
                  adapters: tuple[AgentAdapter, ...]) -> dict[str, tuple[Process, AgentAdapter]]:
    by_tty: dict[str, list[tuple[Process, AgentAdapter]]] = defaultdict(list)
    for process, adapter in outermost_agents(processes, adapters):
        by_tty[process.tty].append((process, adapter))
    return {pane.terminal_id: min(by_tty[pane.tty], key=lambda pair: (pair[0].started_at, pair[0].pid))
            for pane in panes if by_tty[pane.tty]}


def identify(observations: tuple[Observation, ...],
             candidates: dict[tuple[str, str], tuple[Transcript, ...]]) -> dict[str, Identification]:
    result = {o.terminal_id: o.evidence[0] for o in observations if o.evidence}
    def identity(ref: SessionRef) -> tuple[str, str]:
        return ref.agent, ref.session_id or ref.session_file or ""

    claimed = {identity(i.ref) for i in result.values() if i.ref is not None}
    groups: dict[tuple[str, str], list[Observation]] = defaultdict(list)
    for observation in observations:
        if observation.terminal_id not in result:
            groups[(observation.agent, observation.cwd)].append(observation)
    for key, group in groups.items():
        available = [t for t in candidates.get(key, ()) if identity(t.ref) not in claimed]
        available.sort(key=lambda t: (t.mtime, t.ref.session_file or ""), reverse=True)
        unique = set()
        deduplicated = []
        for transcript in available:
            key_id = identity(transcript.ref)
            if key_id not in unique:
                unique.add(key_id)
                deduplicated.append(transcript)
        available = deduplicated
        newest = sorted(available[:len(group)], key=lambda t: (t.first_timestamp, t.ref.session_file or ""))
        ordered = sorted(group, key=lambda o: (o.process.started_at, o.process.pid))
        for observation, transcript in zip(ordered, newest):
            result[observation.terminal_id] = Identification(transcript.ref, "likely", "recent-transcript")
            claimed.add(identity(transcript.ref))
    for observation in observations:
        result.setdefault(observation.terminal_id, Identification(None, "picker", "none"))
    return result


# capture: filesystem/process orchestration; pure selection above owns the rules.
def default_terminal(runner: Runner) -> TerminalAdapter:
    return GhosttyMac(runner)


def default_agents(home: Path, environment: dict[str, str]) -> tuple[AgentAdapter, ...]:
    return ClaudeAdapter(home), CodexAdapter(home), PiAdapter(home, environment)


def read_direct_identification(adapter: AgentAdapter, process: Process,
                               paths: tuple[str, ...]) -> Identification | None:
    """Read identity sources in priority order; failures degrade to the next source."""
    for method, confidence, read in (
        ("live-metadata", "exact", lambda: adapter.session_from_runtime(process.pid, process.start_utc)),
        ("open-file", "exact", lambda: adapter.session_from_open_files(paths)),
        ("argv", "likely", lambda: adapter.session_from_argv(process.command_text)),
    ):
        try:
            ref = read()
            if ref:
                return Identification(ref, confidence, method)
        except (OSError, ValueError, TypeError, KeyError):
            logging.getLogger("tsr").debug("Session evidence unavailable", exc_info=True)
    return None


def repository_root(cwd: str) -> str | None:
    path = Path(cwd)
    for parent in (path, *path.parents):
        if (parent / ".git").exists():
            return str(parent)
    return None


def label_windows(windows: tuple[Window, ...], roots: dict[str, str | None]) -> tuple[Window, ...]:
    counts: Counter[str] = Counter()
    result = []
    for window in windows:
        cwds = [p.cwd for t in window.tabs for p in t.panes if p.cwd]
        folders = [roots[cwd] for cwd in cwds if roots[cwd]] or cwds
        label = Path(Counter(folders).most_common(1)[0][0]).name if folders else f"Window {window.index}"
        label = label or f"Window {window.index}"
        counts[label] += 1
        suffix = f" ({counts[label]})" if counts[label] > 1 else ""
        result.append(replace(window, label=label + suffix))
    return tuple(result)


def capture(runner: Runner, home: Path, environment: dict[str, str], *,
            now: datetime | None = None, snapshot_id: str | None = None,
            terminal: TerminalAdapter | None = None,
            adapters: tuple[AgentAdapter, ...] | None = None) -> Snapshot:
    boot = runner.boot_time()
    terminal = terminal or default_terminal(runner)
    windows = terminal.list_layout()
    ps = runner.run(["ps", "-A", "-ww", "-o", "pid=,ppid=,tty=,lstart=,args="])
    if ps.returncode:
        raise TsrError("couldn't read processes; check process permissions and retry")
    processes = parse_processes(ps.stdout)
    adapters = adapters if adapters is not None else default_agents(home, environment)
    panes = tuple(p for w in windows for t in w.tabs for p in t.panes)
    selected = select_agents(panes, processes, adapters)
    pids = {p.fg_pid for p in panes if p.fg_pid > 0}
    for process, _ in selected.values():
        pids.update(descendants(process.pid, processes))
    if not pids:
        raise TsrError("terminal processes are still starting; retry capture")
    output = runner.run(["lsof", "-n", "-P", "-a", "-p", ",".join(map(str, sorted(pids))), "-Fpfn"])
    if output.returncode and not output.stdout:
        raise TsrError("couldn't read terminal folders; check process permissions and retry")
    opened = parse_open_files(output.stdout)
    folders = {}
    observations = []
    for pane in panes:
        match = selected.get(pane.terminal_id)
        pid = match[0].pid if match else pane.fg_pid
        folder = None
        if match:
            process, adapter = match
            try:
                folder = adapter.runtime_cwd(process.pid, process.start_utc)
            except (OSError, ValueError, TypeError, KeyError):
                pass
        if pid not in opened.uncertain_cwd:
            folder = folder or opened.cwd.get(pid) or pane.cwd
        if folder and Path(folder).is_absolute():
            folder = os.path.realpath(folder)
        else:
            folder = None
        folders[pane.terminal_id] = folder
        if not match:
            continue
        process, adapter = match
        paths = tuple(path for pid in descendants(process.pid, processes) for path in opened.paths.get(pid, ()))
        direct = read_direct_identification(adapter, process, paths)
        observations.append(Observation(pane.terminal_id, adapter.name, folder or "", process,
                                        (direct,) if direct else ()))
    candidates = {}
    for adapter in adapters:
        groups: dict[str, list[Observation]] = defaultdict(list)
        for o in observations:
            if o.agent == adapter.name and o.cwd and not o.evidence:
                groups[o.cwd].append(o)
        for folder, group in groups.items():
            try:
                candidates[(adapter.name, folder)] = adapter.candidates(folder, min(o.process.started_at for o in group))
            except (OSError, ValueError, TypeError, KeyError):
                candidates[(adapter.name, folder)] = ()
    identifications = identify(tuple(observations), candidates)
    captured = []
    for window in windows:
        tabs = []
        for tab in window.tabs:
            captured_panes = []
            for pane in tab.panes:
                info = None
                if pane.terminal_id in selected:
                    process, adapter = selected[pane.terminal_id]
                    chosen = identifications[pane.terminal_id]
                    try:
                        description = adapter.describe(chosen.ref) if chosen.ref else Description()
                    except (OSError, ValueError, TypeError, KeyError):
                        chosen, description = Identification(None, "picker", "none"), Description()
                    ref = chosen.ref
                    info = AgentInfo(adapter.name, process.pid, process.command_text,
                                     ref.session_id if ref else None, ref.session_file if ref else None,
                                     chosen.confidence, chosen.method, description.first_prompt,
                                     description.git_branch, adapter.resume_argv(ref))
                captured_panes.append(replace(pane, cwd=folders[pane.terminal_id], agent=info))
            tabs.append(replace(tab, panes=tuple(captured_panes)))
        captured.append(replace(window, tabs=tuple(tabs)))
    roots = {folder: repository_root(folder) for folder in set(folders.values()) if folder}
    labeled = label_windows(tuple(captured), roots)
    saved = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    return Snapshot(FORMAT, snapshot_id or str(uuid.uuid7()), saved, boot, socket.gethostname(),
                    __version__, {"app": terminal.name, "version": terminal.version}, labeled)


# restore: immutable state; shell-prepared facts feed the pure planner/availability rules.
@dataclass(frozen=True)
class LiveAgent:
    name: str
    process: Process
    ref: SessionRef | None
    cwd: str | None
    location: str
    terminal_id: str | None
    folder_bound: bool = False


@dataclass(frozen=True)
class LiveState:
    snapshot: Snapshot
    processes: dict[int, Process]
    agents: tuple[LiveAgent, ...]


class CaptureRunner(Runner):
    """Collect the existing capture reads for restore, without additional calls."""
    def __init__(self, runner: Runner):
        self.runner = runner
        self.processes: dict[int, Process] = {}
        self.folders: dict[int, str] = {}

    def run(self, argv: list[str], *, timeout: float = 5) -> CommandResult:
        result = self.runner.run(argv, timeout=timeout)
        if argv[0] == "ps":
            self.processes = parse_processes(result.stdout)
        elif argv[0] == "lsof":
            self.folders = parse_open_files(result.stdout).cwd
        return result

    def boot_time(self) -> int:
        return self.runner.boot_time()


def runtime_folder(adapter: AgentAdapter, process: Process) -> str | None:
    try:
        return adapter.runtime_cwd(process.pid, process.start_utc)
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


def gather_live(snapshot: Snapshot, processes: dict[int, Process], adapters: tuple[AgentAdapter, ...],
                folders: dict[int, str], previous: LiveState | None = None) -> LiveState:
    locations = {p.tty: (p, f"window {w.index}, tab {t.index}")
                 for w in snapshot.windows for t in w.tabs for p in t.panes}
    previous_folders = {(a.process.pid, a.process.start_utc): a.cwd for a in previous.agents} if previous else {}
    agents = []
    # Include outside-Ghostty processes and independent agents on the same tty.
    for process, adapter in outermost_agents(processes, adapters):
        pane, location = locations.get(process.tty, (None, "outside Ghostty"))
        ref = None
        if pane is not None:
            if previous is not None or not (pane.agent and pane.agent.pid == process.pid and pane.agent.confidence == "exact"):
                try:
                    ref = adapter.session_from_runtime(process.pid, process.start_utc)
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            if not ref and pane.agent and pane.agent.pid == process.pid and pane.agent.confidence == "exact":
                # During a fresh full capture, exact open-file evidence is valid.
                # A ps-only recheck must not recycle an old open-file identity.
                if previous is None:
                    ref = SessionRef(adapter.name, pane.agent.session_id, pane.agent.session_file)
        folder = folders.get(process.pid) or runtime_folder(adapter, process)
        folder = folder or previous_folders.get((process.pid, process.start_utc))
        agents.append(LiveAgent(adapter.name, process, ref, folder, location,
                                pane.terminal_id if pane else None, adapter.resume_scope == "folder"))
    return LiveState(snapshot, processes, tuple(agents))


def inspect_live(runner: Runner, home: Path, environment: dict[str, str], terminal: TerminalAdapter,
                 adapters: tuple[AgentAdapter, ...]) -> LiveState:
    reads = CaptureRunner(runner)
    snapshot = capture(reads, home, environment, terminal=terminal, adapters=adapters)
    return gather_live(snapshot, reads.processes, adapters, reads.folders)


def recheck_live(runner: Runner, previous: LiveState, adapters: tuple[AgentAdapter, ...]) -> LiveState:
    result = runner.run(["ps", "-A", "-ww", "-o", "pid=,ppid=,tty=,lstart=,args="])
    if result.returncode:
        raise TsrError("couldn't recheck processes; no sessions were launched; retry")
    return gather_live(previous.snapshot, parse_processes(result.stdout), adapters, {}, previous)


def same_session(left: SessionRef, right: SessionRef) -> bool:
    return left.agent == right.agent and bool(
        left.session_id and right.session_id and left.session_id == right.session_id or
        left.session_file and right.session_file and left.session_file == right.session_file)


@dataclass(frozen=True)
class RestoreRow:
    key: str
    window: int
    tab: int
    pane_index: int
    label: str
    pane: Pane
    argv: tuple[str, ...]
    note: str
    launch_ref: SessionRef | None = None

    @property
    def ref(self) -> SessionRef | None:
        a = self.pane.agent
        return SessionRef(a.name, a.session_id, a.session_file) if a else None


@dataclass(frozen=True)
class ResumeOption:
    valid: bool
    argv: tuple[str, ...]


@dataclass(frozen=True)
class AgentRestoreInput:
    display: str
    picker_argv: tuple[str, ...]
    options: dict[tuple[str | None, str | None], ResumeOption]


def plan_restore_rows(snapshot: Snapshot, shell: str,
                      agent_inputs: dict[str, AgentRestoreInput],
                      recent_counts: Counter) -> tuple[RestoreRow, ...]:
    """Build restore rows from captured facts; this function performs no I/O."""
    rows = []
    for wi, w in enumerate(snapshot.windows):
        for ti, t in enumerate(w.tabs):
            for pi, p in enumerate(t.panes):
                key = f"{snapshot.id}/{wi}/{ti}/{pi}"
                argv, note = (shell, "-l"), ""
                launch_ref = None
                if p.agent:
                    a = p.agent
                    agent = agent_inputs.get(a.name)
                    if agent is None:
                        argv, note = (), "agent unsupported; upgrade tsr"
                    else:
                        ref_key = (a.session_id, a.session_file)
                        option = agent.options.get(ref_key)
                        ambiguous = bool(p.cwd and a.method == "recent-transcript" and
                                         recent_counts[(a.name, p.cwd)] > 1)
                        picker = a.confidence == "picker" or ambiguous or not option or not option.valid
                        if not picker:
                            launch_ref = SessionRef(a.name, a.session_id, a.session_file)
                        argv = agent.picker_argv if picker else option.argv
                        # Never execute arbitrary saved executable/flags: accept only
                        # the adapter's validated canonical command or its downgrade.
                        if picker:
                            note = ("ambiguous · " if ambiguous else "") + f"opens {agent.display}'s picker"
                        elif a.confidence != "exact":
                            note = "likely"
                rows.append(RestoreRow(key, w.index, t.index, pi + 1, w.label, p, argv, note, launch_ref))
    return tuple(rows)


def restore_rows(snapshot: Snapshot, adapters: tuple[AgentAdapter, ...], shell: str) -> tuple[RestoreRow, ...]:
    """Gather adapter-dependent validation, then invoke the pure row planner."""
    registry = {adapter.name: adapter for adapter in adapters}
    refs: dict[str, set[tuple[str | None, str | None]]] = defaultdict(set)
    for window in snapshot.windows:
        for tab in window.tabs:
            for pane in tab.panes:
                if pane.agent:
                    refs[pane.agent.name].add((pane.agent.session_id, pane.agent.session_file))
    agent_inputs = {}
    for name, adapter in registry.items():
        picker_argv = adapter.resume_argv(None)
        options = {}
        for session_id, session_file in refs.get(name, ()):
            ref = SessionRef(name, session_id, session_file)
            valid = adapter.valid_ref(ref)
            options[(session_id, session_file)] = ResumeOption(
                valid, adapter.resume_argv(ref) if valid else picker_argv)
        agent_inputs[name] = AgentRestoreInput(adapter.display, picker_argv, options)
    canonical_counts = Counter((pane.agent.name, os.path.realpath(pane.cwd))
                               for window in snapshot.windows for tab in window.tabs for pane in tab.panes
                               if pane.cwd and pane.agent and pane.agent.method == "recent-transcript")
    recent_counts = Counter()
    for window in snapshot.windows:
        for tab in window.tabs:
            for pane in tab.panes:
                if pane.cwd and pane.agent and pane.agent.method == "recent-transcript":
                    recent_counts[(pane.agent.name, pane.cwd)] = canonical_counts[
                        (pane.agent.name, os.path.realpath(pane.cwd))]
    return plan_restore_rows(snapshot, shell, agent_inputs, recent_counts)


def common_repository(folder: str) -> str | None:
    """Read Git's gitdir/commondir files; no git process and no mutations."""
    root = repository_root(folder)
    if not root:
        return None
    directory = Path(root) / ".git"
    try:
        if directory.is_file():
            text = directory.read_text().strip()
            if not text.startswith("gitdir: "):
                return None
            directory = (Path(root) / text[8:]).resolve()
        common = directory / "commondir"
        shared = (directory / common.read_text().strip()).resolve() if common.exists() else directory.resolve()
        # Corresponding folders across worktrees share scope; unrelated
        # subfolders of one repository do not become the same cwd.
        return str(shared) + "\0" + str(Path(folder).resolve().relative_to(Path(root).resolve()))
    except (OSError, ValueError):
        return None


@dataclass(frozen=True)
class PaneAvailability:
    selectable: bool
    reason: str = ""


def lease_matches(row: RestoreRow, lease: dict) -> bool:
    if lease.get("row") == row.key:
        return True
    return bool(row.ref and same_session(row.ref, SessionRef(lease.get("agent", ""),
                                                          lease.get("session_id"), lease.get("session_file"))))


def availability(rows: tuple[RestoreRow, ...], agents: tuple[LiveAgent, ...], leases: list[dict], *,
                 now: float, folders: set[str], repositories: dict[str, str | None]) -> dict[str, PaneAvailability]:
    result = {}
    for row in rows:
        notes = []
        warnings = []
        blocked = ""
        if not row.argv:
            blocked = row.note
        elif not row.pane.cwd:
            blocked = "folder unknown"
        elif row.pane.cwd not in folders:
            blocked = "folder missing"
        if row.pane.cwd and row.ref:
            for agent in agents:
                if agent.ref and same_session(row.ref, agent.ref):
                    blocked = "running · " + agent.location
                    break
        for lease in (leases if row.pane.cwd else ()):
            if not lease_matches(row, lease):
                continue
            if lease["state"] == "running":
                blocked = "running · restored by tsr"
            elif lease["state"] == "reserved" and now - lease["reserved_at"] < 120:
                blocked = "starting"
            else:
                notes.append("restored earlier, not seen running")
        if row.pane.cwd and row.ref and not blocked:
            for agent in agents:
                if agent.name != row.ref.agent or agent.ref:
                    continue
                # Unknown cwd broadens uncertainty rather than claiming exclusion.
                same_folder = (not agent.cwd or os.path.normpath(agent.cwd) == os.path.normpath(row.pane.cwd) or
                               bool(repositories.get(agent.cwd) and
                                    repositories.get(agent.cwd) == repositories.get(row.pane.cwd)))
                if not agent.folder_bound or same_folder:
                    warning = "may already be open"
                    if agent.location == "outside Ghostty":
                        warning += " · outside Ghostty"
                    warnings.append(warning)
        note = " · ".join(dict.fromkeys(part for part in [*warnings, *notes, row.note] if part))
        result[row.key] = PaneAvailability(not blocked, blocked or note)
    return result


@dataclass(frozen=True)
class Step:
    kind: str
    row: RestoreRow
    window_id: str
    terminal_id: str = ""


def plan(rows: tuple[RestoreRow, ...], selected: set[str], window_id: str, terminal_id: str) -> tuple[Step, ...]:
    ordered = [row for row in rows if row.key in selected]
    return tuple(Step("current" if i == 0 else "new", row, window_id, terminal_id if i == 0 else "")
                 for i, row in enumerate(ordered))


class RestoreJournal:
    def __init__(self, store: Store):
        self.store = store

    @contextmanager
    def lock(self, timeout: float = 3):
        self.store.ensure_directory()
        fd = os.open(self.store.directory / "restore.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        try:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TsrError("another restore is in progress; retry shortly")
                    time.sleep(min(.05, max(0, deadline - time.monotonic())))
            yield
        finally:
            os.close(fd)

    def read(self) -> list[dict]:
        try:
            data = json.loads((self.store.directory / "restored.json").read_text())
        except FileNotFoundError:
            return []
        except ValueError as exc:
            raise TsrError("restore state is malformed; inspect restored.json before retrying") from exc
        if not isinstance(data, list):
            raise TsrError("restore state is malformed; inspect restored.json before retrying")
        for item in data:
            if (not isinstance(item, dict) or not isinstance(item.get("id"), str) or
                    not isinstance(item.get("row"), str) or item.get("state") not in {"reserved", "running", "lapsed"} or
                    type(item.get("reserved_at")) not in {int, float} or not 0 <= item["reserved_at"] < float("inf")):
                raise TsrError("restore state is unsupported; inspect restored.json before retrying")
        return data

    def write(self, data: list[dict]) -> None:
        path = self.store._stage((json.dumps(data, indent=2) + "\n").encode())
        try:
            os.replace(path, self.store.directory / "restored.json")
        finally:
            path.unlink(missing_ok=True)

    def update(self, identifier: str, **changes) -> None:
        with self.lock():
            data = self.read()
            self.write([{**item, **changes} if item["id"] == identifier else item for item in data])

    def release(self, identifier: str) -> None:
        with self.lock():
            self.write([item for item in self.read() if item["id"] != identifier])

    def prepare_launch(self, identifier: str, pending: set[str]) -> None:
        # A large sequential restore must not let its still-pending reservations
        # lapse. Never revive an expired/lost lease another restore could own.
        with self.lock():
            data = self.read()
            now = time.time()
            item = next((item for item in data if item["id"] == identifier), None)
            if not item or item["state"] != "reserved" or now - item["reserved_at"] >= 120:
                raise TsrError("restore reservation expired; retry this row")
            self.write([{**item, "reserved_at": now} if item["id"] in pending and
                        item["state"] == "reserved" and now - item["reserved_at"] < 120 else item for item in data])


def reconcile_leases(data: list[dict], live: LiveState, *, now: float) -> list[dict]:
    """Process association is evidence, never an inferred current session ID."""
    terminals = {p.terminal_id: p.tty for w in live.snapshot.windows for t in w.tabs for p in t.panes}
    result = []
    for original in data:
        item = dict(original)
        if item.get("boot_time") != live.snapshot.boot_time:
            item["state"] = "lapsed"
        elif item["state"] == "running":
            p = live.processes.get(item.get("pid"))
            if not (p and p.start_utc == item.get("process_start") and p.tty == item.get("tty") and
                    any(a.process.pid == p.pid and a.name == item.get("agent") for a in live.agents)):
                item["state"] = "lapsed"
        elif item["state"] == "reserved":
            tty = terminals.get(item.get("terminal_id")) or item.get("tty")
            matches = [a for a in live.agents if tty and a.process.tty == tty and a.name == item.get("agent")]
            confirmed = []
            for a in matches:
                p = a.process
                parent = live.processes.get(item.get("handoff_pid"))
                handoff = bool(parent and parent.start_utc == item.get("handoff_start") and
                               parent.tty == tty and p.pid in descendants(parent.pid, live.processes))
                # ps lstart is quantized to a second. This is eligible evidence
                # only for a terminal created after this reservation, not a tty
                # looked up by cached name or an arbitrary existing terminal.
                created = (item.get("new_terminal") and item.get("terminal_id") in terminals and
                           p.started_at >= int(item["reserved_at"]))
                if handoff or created:
                    confirmed.append(p)
            if len(confirmed) == 1:
                p = confirmed[0]
                item.update(state="running", pid=p.pid, process_start=p.start_utc, tty=p.tty)
            elif now - item["reserved_at"] >= 120:
                item["state"] = "lapsed"
        result.append(item)
    # Keep a bounded week of lapse labels; never time-prune live confirmed rows.
    return [item for item in result if item["state"] != "lapsed" or now - item["reserved_at"] < 7 * 86400]


@dataclass(frozen=True)
class PickerState:
    expanded: frozenset[int]
    selected: frozenset[str] = frozenset()
    cursor: int = 0


def picker_items(rows: tuple[RestoreRow, ...], state: PickerState) -> tuple[tuple[int, str | None], ...]:
    result = []
    for window in dict.fromkeys(r.window for r in rows):
        result.append((window, None))
        if window in state.expanded:
            result.extend((window, r.key) for r in rows if r.window == window)
    return tuple(result)


def picker_event(state: PickerState, key: str, rows: tuple[RestoreRow, ...],
                 available: dict[str, PaneAvailability]) -> tuple[PickerState, set[str] | None]:
    items = picker_items(rows, state)
    if not items:
        return state, set() if key in {"enter", "q", "escape"} else None
    cursor = min(state.cursor, len(items) - 1)
    window, identifier = items[cursor]
    allowed = {r.key for r in rows if available[r.key].selectable}
    group = {r.key for r in rows if r.window == window} & allowed
    target = ({identifier} & allowed) if identifier else group
    selected = set(state.selected) & allowed
    expanded = set(state.expanded)
    if key in {"q", "escape"}:
        return state, set()
    if key == "enter":
        return state, selected or target or None
    if key in {"down", "j"}:
        cursor = min(cursor + 1, len(items) - 1)
    elif key in {"up", "k"}:
        cursor = max(cursor - 1, 0)
    elif key == "space":
        selected = selected - target if target <= selected else selected | target
    elif key == "right":
        expanded.add(window)
    elif key == "left":
        expanded.discard(window)
        collapsed = picker_items(rows, replace(state, expanded=frozenset(expanded)))
        cursor = collapsed.index((window, None))
    return PickerState(frozenset(expanded), frozenset(selected), cursor), None


def pick(rows: tuple[RestoreRow, ...], available: dict[str, PaneAvailability], home: Path,
         saved_at: str) -> set[str]:
    import curses  # Deliberately lazy: save/list never require curses or a tty.
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise TsrError("restore picker requires an interactive Ghostty terminal", 3)
    first = next((r.window for r in rows if available[r.key].selectable), rows[0].window if rows else 0)
    initial = PickerState(frozenset({first}))
    items = picker_items(rows, initial)
    initial = replace(initial, cursor=items.index((first, None)) if items else 0)

    def interact(screen):
        state = initial
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        screen.keypad(True)
        while True:
            screen.erase()
            height, width = screen.getmaxyx()

            def line(y, text, style=0):
                if 0 <= y < height and width > 1:
                    try:
                        screen.addnstr(y, 0, visible(text), width - 1, style)
                    except curses.error:
                        pass

            if height < 6 or width < 40:
                line(0, "Enlarge terminal to at least 40 × 6.")
                line(1, "q or Esc cancels.")
                screen.refresh()
                if screen.getch() in {ord('q'), 27}:
                    return set()
                continue

            line(0, f"Saved {saved_at} · restoring into this window")
            items = picker_items(rows, state)
            capacity = max(1, height - 4)
            offset = max(0, state.cursor - capacity + 1)
            by_key = {r.key: r for r in rows}
            for i, (window, identifier) in enumerate(items[offset:offset + capacity], offset):
                style = curses.A_REVERSE if i == state.cursor else 0
                if identifier is None:
                    group = [r for r in rows if r.window == window]
                    count = sum(available[r.key].selectable for r in group)
                    symbol = "▾" if window in state.expanded else "▸"
                    text = f"{symbol} {group[0].label} · window {window} · {count} of {len(group)} available"
                else:
                    row, status = by_key[identifier], available[identifier]
                    mark = "[x]" if identifier in state.selected else "[ ]" if status.selectable else " ─ "
                    a = row.pane.agent
                    name = a.name if a else "shell"
                    prompt = (a.first_prompt or "(no prompt)") if a else ""
                    prefix = f"   {mark} {name:<6} "
                    budget = max(0, width - 1 - len(prefix))
                    note = visible(status.reason)[:budget]
                    folder = visible(display_folder(row.pane.cwd, home)) if row.pane.cwd else ""
                    folder_room = max(0, budget - len(note) - (2 if note else 0))
                    if len(folder) > folder_room:
                        folder = '…' + folder[-(folder_room - 1):] if folder_room > 1 else ''
                    tail = (folder + ('  ' + note if note else '')).strip()
                    prompt_room = max(0, budget - len(tail) - 2)
                    text = prefix + visible(prompt)[:prompt_room] + ('  ' if prompt_room else '') + tail
                    if not status.selectable:
                        style |= curses.A_DIM
                line(2 + i - offset, text, style)
            line(height - 2, "↑↓/jk move · Space select · Enter restore · ←→ expand/collapse · q quit")
            line(height - 1, "Unverified sessions may already be open; warnings remain selectable.")
            screen.refresh()
            key = screen.getch()
            names = {curses.KEY_UP: "up", curses.KEY_DOWN: "down", curses.KEY_LEFT: "left",
                     curses.KEY_RIGHT: "right", 10: "enter", 13: "enter", curses.KEY_ENTER: "enter",
                     27: "escape", 32: "space"}
            name = names.get(key, chr(key) if 0 <= key < 256 else "resize")
            state, selection = picker_event(state, name, rows, available)
            if selection is not None:
                return selection
    try:
        return curses.wrapper(interact)
    except curses.error as exc:
        raise TsrError("couldn't open the picker; use a larger Ghostty terminal with a valid TERM", 3) from exc


def row_availability(rows: tuple[RestoreRow, ...], live: LiveState, leases: list[dict], now: float):
    folders = {r.pane.cwd for r in rows if r.pane.cwd and Path(r.pane.cwd).is_dir()}
    all_folders = folders | {a.cwd for a in live.agents if a.cwd}
    repositories = {folder: common_repository(folder) for folder in all_folders}
    return availability(rows, live.agents, leases, now=now, folders=folders, repositories=repositories)


def reserve(rows: tuple[RestoreRow, ...], selected: set[str], journal: RestoreJournal,
            runner: Runner, previous: LiveState, adapters: tuple[AgentAdapter, ...], terminal_id: str, *,
            shown: dict[str, PaneAvailability] | None = None) -> tuple[dict[str, str], LiveState, dict[str, PaneAvailability]]:
    ordered = [row for row in rows if row.key in selected]
    known = []
    for row in ordered:
        if row.launch_ref:
            if any(same_session(row.launch_ref, ref) for ref in known):
                raise TsrError("selection names the same session more than once; select only one of those rows")
            known.append(row.launch_ref)
    with journal.lock():
        live = recheck_live(runner, previous, adapters)
        now = time.time()
        leases = reconcile_leases(journal.read(), live, now=now)
        available = row_availability(rows, live, leases, now)
        blocked = any(key not in available or not available[key].selectable for key in selected)
        changed = shown is not None and any(available.get(key) != shown.get(key) for key in selected)
        if blocked or changed:
            journal.write(leases)
            return {}, live, available
        ids = {}
        own = live.processes.get(os.getpid())
        for i, row in enumerate(ordered):
            # Plain-shell rows also reserve their saved row while this batch is
            # in flight, so simultaneous shell-only selections are serialized.
            identifier = str(uuid.uuid7())
            ids[row.key] = identifier
            ref = row.launch_ref
            lease = {"id": identifier, "row": row.key, "agent": row.pane.agent.name if row.pane.agent else None,
                     "session_id": ref.session_id if ref else None,
                     "session_file": ref.session_file if ref else None,
                     "state": "reserved", "terminal_id": terminal_id if i == 0 else None,
                     "new_terminal": i != 0, "reserved_at": now, "boot_time": live.snapshot.boot_time}
            if i == 0 and own:
                lease.update(handoff_pid=own.pid, handoff_start=own.start_utc, tty=own.tty)
            leases.append(lease)
        journal.write(leases)
        return ids, live, available


def execute(steps: tuple[Step, ...], terminal: TerminalAdapter, journal: RestoreJournal,
            identifiers: dict[str, str]) -> int:
    if not steps:
        return 0
    restored, failures = 0, []
    current = steps[0]
    pending = set(identifiers.values())
    try:
        for step in steps[1:]:
            identifier = identifiers[step.row.key]
            try:
                journal.prepare_launch(identifier, pending)
                started = time.monotonic()
                target = terminal.open_tab(step.window_id, step.row.pane.cwd)
                journal.update(identifier, terminal_id=target)
                if step.row.pane.agent:
                    remaining = 3 - (time.monotonic() - started)
                    if remaining <= 0 or not terminal.wait_for_shell(target, remaining):
                        raise TsrError("shell not ready within 3 seconds; inspect the new tab")
                    shell = terminal.shell_processes.get(target)
                    if shell:
                        journal.update(identifier, tty=shell.tty, handoff_pid=shell.pid, handoff_start=shell.start_utc)
                    terminal.type_and_run(target, shlex.join(step.row.argv))
                restored += 1
            except BaseException as exc:
                journal.release(identifier)
                if not isinstance(exc, Exception):
                    raise
                reason = str(exc) if isinstance(exc, (TsrError, OSError)) else "terminal operation failed"
                failures.append(f"{step.row.label}/{step.row.tab}: {visible(reason)}")
            finally:
                pending.discard(identifier)
        # Report before exec flushes away this process; current handoff failures
        # receive their own one-line error and release below.
        detail = " · " + str(len(failures)) + " failed (" + "; ".join(failures) + ")" if failures else ""
        journal.prepare_launch(identifiers[current.row.key], pending)
        print(f"Restored {restored} new tabs · handing off current tab{detail}", flush=True)
        terminal.focus(current.terminal_id)
        try:
            os.chdir(current.row.pane.cwd)
            os.execvp(current.row.argv[0], list(current.row.argv))
        except OSError as exc:
            raise TsrError(f"couldn't launch current tab ({current.row.argv[0]}); check its folder and executable, then retry") from exc
        raise TsrError("exec returned unexpectedly; retry current tab")
    except BaseException:
        # Remove pending records that never reached an attempted launch. Keep
        # successful new agent tabs protected when another step is interrupted.
        journal.release(identifiers[current.row.key])
        with journal.lock():
            data = journal.read()
            own_ids = set(identifiers.values())
            journal.write([item for item in data if not (
                item["id"] in own_ids and not item.get("terminal_id"))])
        raise


def restore(store: Store, runner: Runner, home: Path, environment: dict[str, str], *,
            backup: bool = False, window: int | None = None) -> int:
    snapshot = store.load(backup=backup)
    if window is not None and not any(
            saved_window.index == window for saved_window in snapshot.windows):
        raise TsrError(f"snapshot has no window {window}; check tsr list", 3)
    try:
        tty = normalize_tty(os.ttyname(0))
    except OSError as exc:
        raise TsrError("run tsr restore inside a Ghostty window", 3) from exc
    terminal = default_terminal(runner)
    adapters = default_agents(home, environment)
    live = inspect_live(runner, home, environment, terminal, adapters)
    location = next(((w.terminal_window_id, p.terminal_id) for w in live.snapshot.windows
                     for t in w.tabs for p in t.panes if p.tty == tty), None)
    if not location:
        raise TsrError("run tsr restore inside a Ghostty window", 3)
    shell = environment.get("SHELL") or "/bin/zsh"
    if not Path(shell).is_absolute():
        raise TsrError("SHELL must name an absolute executable; correct SHELL and retry", 3)
    rows = restore_rows(snapshot, adapters, shell)
    if window is not None:
        rows = tuple(row for row in rows if row.window == window)
    journal = RestoreJournal(store)
    # Reading/interactive cancellation does not create state or take a lock.
    leases = reconcile_leases(journal.read(), live, now=time.time())
    available = row_availability(rows, live, leases, time.time())
    while True:
        selected = pick(rows, available, home, snapshot.saved_at)
        if not selected:
            return 0
        identifiers, live, available = reserve(rows, selected, journal, runner, live, adapters, location[1], shown=available)
        if not identifiers:
            continue
        return execute(plan(rows, selected, *location), terminal, journal, identifiers)


def doctor(store: Store, runner: Runner, home: Path, environment: dict[str, str]) -> int:
    failed = False
    precondition_code = 0

    def report(mark, check, detail):
        nonlocal failed
        failed |= mark == "✗"
        print(f"{mark} {check}: {visible(detail)}")

    supported = sys.version_info >= (3, 14)
    report("✓" if supported else "✗", "Python",
           sys.version.split()[0] if supported else "Python 3.14+ required; reinstall with uv")
    try:
        import curses
        report("✓", "curses", "importable")
    except ImportError:
        report("✗", "curses", "reinstall with uv-managed Python 3.14")
    executable = shutil.which("tsr", path=environment.get("PATH", os.defpath))
    expected = Path(sys.executable).parent / "tsr"
    correct = executable and Path(executable).resolve() == expected.resolve()
    report("✓" if correct else "⚠", "PATH", executable if correct else "check command -v tsr; put uv's tool bin directory first")
    try:
        adapters = default_agents(home, environment)
        live = inspect_live(runner, home, environment, default_terminal(runner), adapters)
        report("✓", "Ghostty", "running · " + live.snapshot.terminal["version"])
        report("✓", "Automation", "layout read succeeded")
        counts = Counter(p.agent.confidence for w in live.snapshot.windows for t in w.tabs for p in t.panes if p.agent)
        review_count = counts["likely"] + counts["picker"]
        review_groups = []
        for w in live.snapshot.windows:
            tabs = []
            for t in w.tabs:
                panes = [p for p in t.panes if p.agent and p.agent.confidence != "exact"]
                if panes:
                    tabs.append((t.index, panes))
            review_groups.append((w.index, tabs))
        review_groups = [(window_index, tabs) for window_index, tabs in review_groups if tabs]
        if review_count:
            report("⚠", "Session identification",
                   f"{counts['exact']} exact · {review_count} to review ({counts['likely']} likely · {counts['picker']} picker)")
            print("  Exact sessions are counted above but aren't listed individually.")
            print("  Review before restarting:")
            for window_number, (window_index, tabs) in enumerate(review_groups, 1):
                print(f"    {window_number}. Window {window_index}")
                for tab_number, (tab_index, panes) in enumerate(tabs, 1):
                    sessions = ", ".join(f"{pane.agent.name} ({pane.agent.confidence})" for pane in panes)
                    print(f"      {tab_number}. Tab {tab_index} · {sessions} · verify before restart")
        else:
            report("✓", "Session identification", f"{counts['exact']} exact · none need review")
    except TsrError as exc:
        warning = "not running" in str(exc) or "nothing to save" in str(exc)
        if not warning and exc.code == 3:
            precondition_code = 3
        report("⚠" if warning else "✗", "Ghostty/Automation", str(exc))
    ghostty = shutil.which("ghostty", path=environment.get("PATH", os.defpath))
    if not ghostty:
        report("⚠", "window-save-state", "couldn't inspect; Ghostty CLI not found on PATH")
    else:
        try:
            config = runner.run([ghostty, "+show-config", "--no-pager"])
            if config.returncode != 0:
                raise TsrError("Ghostty config query failed")
            matches = re.findall(r"^\s*window-save-state\s*=\s*(\S+)\s*$", config.stdout, re.MULTILINE)
            mode = matches[-1] if matches else "default"
            if mode == "always":
                report("⚠", "window-save-state", "always · Ghostty may reopen saved windows on exit")
            elif mode == "never":
                report("✓", "window-save-state", "never · Ghostty window-state saving is disabled")
            elif mode == "default":
                report("⚠", "window-save-state", "default · macOS policy applies; check System Settings → Desktop & Dock → Close windows when quitting an application")
            else:
                report("⚠", "window-save-state", f"couldn't inspect; unsupported value {mode!r}")
        except TsrError:
            report("⚠", "window-save-state", "couldn't inspect; Ghostty config query failed")
    try:
        snapshot = store.load(required=False)
        if snapshot:
            minutes = max(0, int((time.time() - timestamp(snapshot.saved_at)) / 60))
            tabs = sum(len(w.tabs) for w in snapshot.windows)
            report("✓", "Snapshot", f"saved {snapshot.saved_at} · {minutes} minutes ago · {len(snapshot.windows)} windows · {tabs} tabs")
        else:
            report("⚠", "Snapshot", "none; run tsr save before restarting")
    except TsrError as exc:
        report("✗", "Snapshot", str(exc))
    return precondition_code or (1 if failed else 0)


# cli rendering: no terminal control sequences from captured text.
def visible(value: str) -> str:
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in value)


def display_folder(folder: str, home: Path) -> str:
    try:
        relative = Path(folder).relative_to(home)
        return "~" if not relative.parts else "~/" + str(relative)
    except ValueError:
        return folder


def render(snapshot: Snapshot, home: Path, *, saved: bool = False, brief: bool = False) -> str:
    tabs = sum(len(w.tabs) for w in snapshot.windows)
    agents = sum(p.agent is not None for w in snapshot.windows for t in w.tabs for p in t.panes)
    when = datetime.fromisoformat(snapshot.saved_at.replace("Z", "+00:00")).astimezone().strftime("%a %d %b %H:%M")
    lines = [f"{'Saved' if saved else 'Snapshot'} {len(snapshot.windows)} windows · {tabs} tabs · {agents} agent sessions   ({when})", ""]
    for window in snapshot.windows:
        lines.append(f"Window {window.index} · {visible(window.label)}")
        for tab in window.tabs:
            for index, pane in enumerate(tab.panes, 1):
                number = str(tab.index) + (f".{index}" if len(tab.panes) > 1 else "")
                agent = pane.agent
                name = {"claude": "Claude", "codex": "Codex", "pi": "pi"}.get(agent.name, agent.name) if agent else "shell"
                prompt = (agent.first_prompt or ("(session not identified)" if agent.confidence == "picker" else "(no prompt)")) if agent else ""
                note = agent.confidence if agent else ""
                folder = visible(display_folder(pane.cwd, home)) if pane.cwd else "(folder unknown)"
                if brief:
                    lines.append(f"  {number:>3}  {name:<6}  {folder}  {note}".rstrip())
                    continue
                lines.append(f"  {number:>3}  {name:<6}  {visible(prompt)}  {folder}  {note}".rstrip())
                if not saved and pane.cwd:
                    command = f"cd -- {shlex.quote(pane.cwd)}"
                    if agent:
                        command += " && " + shlex.join(agent.resume_argv)
                    lines.append("       " + visible(command))
    return "\n".join(lines)


def state_store(home: Path, environment: dict[str, str]) -> Store:
    base = home_path(environment.get("XDG_STATE_HOME") or home / ".local/state", home)
    if not base.is_absolute():
        raise TsrError("XDG_STATE_HOME must be absolute; set it to an absolute state directory")
    protected = [home / name for name in (".claude", ".codex", ".pi")]
    for key in ("PI_CODING_AGENT_DIR", "PI_CODING_AGENT_SESSION_DIR"):
        if environment.get(key):
            protected.append(home_path(environment[key], home))
    return Store(base / "tsr", tuple(protected))


def log_failure(store: Store, debug: bool) -> None:
    try:
        store.ensure_directory()
        for name in ("tsr.log", "tsr.log.1", "tsr.log.2", "tsr.log.3"):
            if (store.directory / name).is_symlink():
                return

        class PrivateHandler(RotatingFileHandler):
            def _open(self):
                fd = os.open(self.baseFilename, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                os.fchmod(fd, 0o600)
                return os.fdopen(fd, "a", encoding="utf-8")

        handler = PrivateHandler(store.directory / "tsr.log", maxBytes=1024 * 1024, backupCount=3)
        try:
            record = logging.LogRecord("tsr", logging.ERROR, __file__, 0, "Command failed", (), sys.exc_info())
            handler.emit(record)
        finally:
            handler.close()
    except OSError:
        pass
    if debug:
        import traceback
        traceback.print_exc()


def main(argv: list[str] | None = None, *, runner: Runner | None = None,
         environment: dict[str, str] | None = None, home: Path | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tsr",
        description="Save, inspect and restore Ghostty agent sessions on macOS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Command usage:\n"
            "  tsr save [--yes]\n"
            "  tsr list [--json] [--backup] [--brief]\n"
            "  tsr restore [--backup] [--window N]\n"
            "  tsr doctor\n\n"
            "Run 'tsr <command> --help' for option details."
        ),
    )
    parser.add_argument("--version", action="version", version=f"tsr {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    save = commands.add_parser("save", help="capture Ghostty windows and sessions")
    save.add_argument("--yes", action="store_true", help="confirm replacement of a snapshot from before this boot")
    listing = commands.add_parser("list", help="inspect saved windows, tabs and sessions")
    listing.add_argument("--json", action="store_true", help="print the snapshot as JSON")
    listing.add_argument("--backup", action="store_true", help="inspect the previous snapshot")
    listing.add_argument(
        "--brief", action="store_true",
        help="omit prompts and recovery commands from text output")
    restoring = commands.add_parser("restore", help="pick saved sessions to restore into this Ghostty window")
    restoring.add_argument("--backup", action="store_true", help="restore the previous snapshot")
    restoring.add_argument(
        "--window", type=int, metavar="N",
        help="show only saved window N (number from tsr list) in the picker")
    commands.add_parser("doctor", help="check prerequisites, capture confidence and saved state")
    args = parser.parse_args(argv)
    environment = dict(os.environ) if environment is None else environment
    home = home or Path(environment.get("HOME") or Path.home())
    store = None
    try:
        store = state_store(home, environment)
        if args.command == "list":
            snapshot = store.load(backup=args.backup)
            print(snapshot.to_json(), end="") if args.json else print(render(snapshot, home, brief=args.brief))
            return 0
        if not store.directory.exists():
            print("macOS may ask to let the requesting app control Ghostty; allow it to capture your terminals.", file=sys.stderr)
        if args.command == "restore":
            return restore(store, runner or Runner(), home, environment,
                           backup=args.backup, window=args.window)
        if args.command == "doctor":
            return doctor(store, runner or Runner(), home, environment)
        snapshot = capture(runner or Runner(), home, environment)
        old = store.load(required=False)
        if old and old.boot_time < snapshot.boot_time and not args.yes:
            try:
                answer = input("Existing snapshot predates this boot. Replace it? [y/N] ")
            except EOFError:
                answer = ""
            if answer.strip().lower() not in {"y", "yes"}:
                print("Kept existing snapshot.")
                return 0
        store.save(snapshot)
        print(render(snapshot, home, saved=True))
        print("Saved. Restart when ready.")
        unknown = sorted({(w.index, t.index) for w in snapshot.windows for t in w.tabs
                          if any(p.cwd is None for p in t.panes)})
        if unknown:
            labels = ", ".join(f"window {window}, tab {tab}" for window, tab in unknown)
            noun = "tab" if len(unknown) == 1 else "tabs"
            print(f"{len(unknown)} {noun} saved without a folder: {labels} (it can't be restored)")
        return 0
    except KeyboardInterrupt:
        print("tsr: interrupted; inspect tsr list before retrying", file=sys.stderr)
        return 1
    except Exception as exc:
        code = exc.code if isinstance(exc, TsrError) else 1
        if store is not None and code == 1:
            log_failure(store, environment.get("TSR_DEBUG") == "1")
        message = str(exc) if isinstance(exc, TsrError) else "couldn't access capture/state data; check permissions and retry"
        print("tsr: " + visible(message), file=sys.stderr)
        return code
