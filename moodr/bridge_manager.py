"""Manages the optional MIDI bridge scripts (moodr_to_m8_bridge.py,
m8_to_moodr_transport_bridge.py, and the Circuit pair) so they can be
started, restarted and stopped from the GUI and their uptime displayed.
Kept separate from Qt, same split as clock.py/midi_io.py/midi_status.py.

Two kinds of bridge process are handled, and the difference matters:

* one this app started itself, tracked as a `subprocess.Popen` child; and
* one **started outside this app** -- the way M8-SETUP.md actually tells
  you to run them, `uv run python -u moodr_to_m8_bridge.py` in its own
  terminal. These used to be completely invisible here, which was a real
  problem rather than a cosmetic one: a bridge can survive a MIDIServer
  reset without crashing and still hold a permanently stale CoreMIDI
  connection, so the one process most in need of a restart was the one
  this app could not touch. Worse, "Restart" would have happily launched a
  second copy alongside it.

External bridges are found by scanning the process table (`scan_processes`)
for Python processes running a known bridge script. That scan is also what
makes `is_running` honest, so `start()` can refuse to create a duplicate.
"""

import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# (display name, script filename relative to the repo root)
KNOWN_BRIDGE_SCRIPTS = [
    ("m00Dr -> M8 (notes)", "moodr_to_m8_bridge.py"),
    ("M8 -> m00Dr (transport)", "m8_to_moodr_transport_bridge.py"),
    ("m00Dr -> Circuit (notes)", "moodr_to_circuit_bridge.py"),
    ("Circuit -> m00Dr (transport)", "circuit_to_moodr_bridge.py"),
]

# How long a process this app didn't start gets to exit after SIGTERM
# before it's SIGKILLed, and how often it's polled in between.
EXTERNAL_STOP_TIMEOUT = 2.0
EXTERNAL_POLL_INTERVAL = 0.05


@dataclass(frozen=True)
class ExternalProcess:
    """A bridge process running outside this app. `started_at` is a wall
    clock time derived from ps's elapsed-time column, or None if that
    column couldn't be parsed."""

    pid: int
    command: str
    started_at: float | None


# ps reports elapsed time as [[DD-]HH:]MM:SS.
_ETIME_PATTERN = re.compile(r"^(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)$")


def parse_etime(text: str) -> float | None:
    """Seconds from ps's elapsed-time format, or None if unrecognised."""
    match = _ETIME_PATTERN.match(text.strip())
    if match is None:
        return None
    days, hours, minutes, seconds = (int(group or 0) for group in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def scan_processes() -> list[tuple[int, int, str, str]]:
    """(pid, ppid, etime, command) for every process on the machine, from
    one `ps` call. Returns [] rather than raising if ps is unavailable or
    misbehaves -- failing to find external bridges should degrade this
    feature, not break the dialog that displays it.

    One call costs roughly 30ms on a ~900-process machine, which is why
    callers share a single scan across all bridges (`refresh_all`) instead
    of each bridge scanning for itself.
    """
    try:
        completed = subprocess.run(
            ["ps", "-Ao", "pid=,ppid=,etime=,command="],
            capture_output=True, text=True, timeout=5.0, check=False)
    except (OSError, subprocess.SubprocessError):
        return []

    rows = []
    for line in completed.stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        pid_text, ppid_text, etime, command = parts
        try:
            pid, ppid = int(pid_text), int(ppid_text)
        except ValueError:
            continue
        rows.append((pid, ppid, etime, command))
    return rows


def ancestor_pids(rows: list[tuple[int, int, str, str]]) -> set[int]:
    """This process and every process that launched it, walked up the ppid
    chain. Nothing in this set may ever be killed as a "bridge": if m00Dr
    was itself started from a shell whose command line happens to mention a
    bridge script, killing an ancestor would take down that terminal -- or
    m00Dr itself -- instead of a bridge."""
    parents = {pid: ppid for pid, ppid, _etime, _command in rows}
    chain: set[int] = set()
    pid = os.getpid()
    while pid > 0 and pid not in chain:
        chain.add(pid)
        pid = parents.get(pid, 0)
    return chain


def looks_like_bridge_process(command: str, script_name: str) -> bool:
    """Whether a ps command line is a Python process running `script_name`.

    This has to be strict, because this module *kills* what it matches.
    The test is on argv[0] specifically -- the command must *be* a Python
    interpreter (or the script itself, for a shebang launch) with the
    script among its arguments -- rather than on "python" and the script
    name appearing anywhere in the line.

    The loose version was tried first and was genuinely dangerous rather
    than merely imprecise: a shell running a one-liner that mentions both,
    such as `zsh -c '... python ... moodr_to_m8_bridge.py ...'`, matched it,
    and stopping the bridge killed that shell. An editor holding the file
    open, or a `tail` of its log, would have gone the same way.

    `uv run python -u moodr_to_m8_bridge.py` shows up as two processes: the
    `uv` wrapper (argv[0] "uv", not matched) and the real interpreter it
    spawns (matched). Killing the interpreter is what matters; the wrapper
    exits along with it.
    """
    parts = command.split()
    if not parts:
        return False
    executable = parts[0]
    # Shebang launch: the script itself is the executable.
    if executable.endswith(script_name):
        return True
    if "python" not in os.path.basename(executable).lower():
        return False
    return any(argument.endswith(script_name) for argument in parts[1:])


def _pid_alive(pid: int) -> bool:
    """Signal 0 existence check. A process that has exited but not yet
    been reaped by its parent (a zombie) still reads as alive here; that's
    only reachable when the parent is itself stuck, since a shell or
    terminal reaps promptly, and the worst case is waiting out
    EXTERNAL_STOP_TIMEOUT before a redundant SIGKILL."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours to signal
    return True


def _signal_pid(pid: int, sig: int) -> bool:
    """Best-effort signal. False if the process is already gone or belongs
    to another user."""
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        return False
    return True


class ManagedBridge:
    """One bridge script. `sys.executable` (this app's own Python
    interpreter) is used to launch it rather than `uv run` -- these
    scripts only need `rtmidi`, which is already installed in this app's
    own environment since moodr/midi_io.py depends on it too, so there's
    no separate environment to resolve."""

    def __init__(self, name: str, script_path: Path):
        self.name = name
        self.script_path = script_path
        self._process: subprocess.Popen | None = None
        self._started_at: float | None = None
        # Last known external processes, from refresh_external(). Never
        # queried implicitly by is_running/started_at: a property that
        # silently shells out to ps would be called once per bridge per
        # GUI timer tick.
        self._external: tuple[ExternalProcess, ...] = ()

    # -- state ---------------------------------------------------------

    @property
    def started_by_this_app(self) -> bool:
        """Whether this app's own child process is alive."""
        return self._process is not None and self._process.poll() is None

    @property
    def external_processes(self) -> tuple[ExternalProcess, ...]:
        """Bridge processes this app didn't start, as of the last
        refresh_external() call."""
        return self._external

    @property
    def is_running(self) -> bool:
        """True if this bridge is running at all, whoever started it."""
        return self.started_by_this_app or bool(self._external)

    @property
    def started_at(self) -> float | None:
        if self.started_by_this_app:
            return self._started_at
        known = [process.started_at for process in self._external
                 if process.started_at is not None]
        return min(known) if known else None

    @property
    def uptime_seconds(self) -> float | None:
        started_at = self.started_at
        return None if started_at is None else time.time() - started_at

    def refresh_external(self, table: list[tuple[int, int, str, str]] | None = None) -> None:
        """Re-reads which copies of this bridge are running outside this
        app. Pass a shared `table` from scan_processes() when refreshing
        several bridges at once. The bridge child this app spawned is
        excluded, so a bridge it started is never also counted as external,
        and so is this process's whole ancestry (see ancestor_pids)."""
        rows = scan_processes() if table is None else table
        mine = ancestor_pids(rows)
        if self._process is not None:
            mine.add(self._process.pid)

        found = []
        now = time.time()
        for pid, _ppid, etime, command in rows:
            if pid in mine or not looks_like_bridge_process(command, self.script_path.name):
                continue
            elapsed = parse_etime(etime)
            found.append(ExternalProcess(
                pid=pid, command=command,
                started_at=None if elapsed is None else now - elapsed))
        self._external = tuple(found)

    # -- control -------------------------------------------------------

    def start(self) -> None:
        """Starts the bridge, unless one is already running. The check
        includes a live scan for external copies, so clicking Start while
        a terminal-launched bridge is up can't quietly create a second one
        fighting it for the same MIDI ports."""
        if not self.script_path.exists():
            return
        self.refresh_external()
        if self.is_running:
            return
        self._spawn()

    def _spawn(self) -> None:
        self._process = subprocess.Popen(
            [sys.executable, "-u", str(self.script_path)],
            cwd=self.script_path.parent,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._started_at = time.time()

    def stop(self, timeout: float = 3.0,
             external_timeout: float = EXTERNAL_STOP_TIMEOUT) -> None:
        """Stops every copy of this bridge -- this app's own child and any
        started outside it."""
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            self._process = None
            self._started_at = None
        self._stop_external(external_timeout)

    def _stop_external(self, timeout: float) -> None:
        """SIGTERM, then SIGKILL, every bridge process this app didn't
        start. These aren't children of this process, so there's no
        waitpid to call and liveness has to be polled instead."""
        pending = [process.pid for process in self._external]
        if not pending:
            return
        for pid in pending:
            _signal_pid(pid, signal.SIGTERM)

        deadline = time.monotonic() + timeout
        while pending and time.monotonic() < deadline:
            pending = [pid for pid in pending if _pid_alive(pid)]
            if pending:
                time.sleep(EXTERNAL_POLL_INTERVAL)
        for pid in pending:
            _signal_pid(pid, signal.SIGKILL)
        self._external = ()

    def restart(self) -> None:
        """Stops everything running for this bridge and starts a fresh
        copy owned by this app. Deliberately calls _spawn() rather than
        start(): stop() has just cleared the field, and start()'s
        re-scan could still catch a just-killed process mid-exit and
        decide not to start anything."""
        self.stop()
        if self.script_path.exists():
            self._spawn()


# -- module-level helpers --------------------------------------------------


def refresh_all(bridges: list[ManagedBridge]) -> None:
    """Refreshes every bridge's external-process view from one shared ps
    scan, rather than one scan per bridge."""
    table = scan_processes()
    for bridge in bridges:
        bridge.refresh_external(table)


def format_duration(seconds: float) -> str:
    """"5m 12s" / "2h 03m" / "42s" style, for a bridge's uptime."""
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def format_status(bridge: ManagedBridge) -> str:
    """"Running since 14:32:07 (5m 12s)", with " (outside m00Dr)" appended
    when this app didn't start it, or "Not running"."""
    if not bridge.is_running:
        return "Not running"
    origin = "" if bridge.started_by_this_app else " (outside m00Dr)"
    started_at = bridge.started_at
    if started_at is None:
        return f"Running{origin}"
    clock = time.strftime("%H:%M:%S", time.localtime(started_at))
    return f"Running since {clock} ({format_duration(bridge.uptime_seconds)}){origin}"


def discover_bridges() -> list[ManagedBridge]:
    """Returns a ManagedBridge for each known bridge script that actually
    exists next to this repo's main.py -- silently skips any that don't,
    since these scripts are specific to an M8/Teensy or Circuit setup, not
    something every m00Dr user has."""
    return [ManagedBridge(name, REPO_ROOT / filename)
            for name, filename in KNOWN_BRIDGE_SCRIPTS
            if (REPO_ROOT / filename).exists()]
