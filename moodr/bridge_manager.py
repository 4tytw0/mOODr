"""Manages the optional MIDI bridge scripts (moodr_to_m8_bridge.py,
m8_to_moodr_transport_bridge.py) as child processes of this app, so they
can be started/restarted from the GUI and their uptime displayed. Kept
separate from Qt, same split as clock.py/midi_io.py/midi_status.py.

Only a bridge *started through this module* is tracked here -- one
launched independently in its own terminal (how these scripts were
originally meant to be run) is invisible to it until restarted through
here instead. That's a real limitation, not just a note: this app has no
way to discover or adopt a process it didn't start itself.
"""

import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# (display name, script filename relative to the repo root)
KNOWN_BRIDGE_SCRIPTS = [
    ("m00Dr -> M8 (notes)", "moodr_to_m8_bridge.py"),
    ("M8 -> m00Dr (transport)", "m8_to_moodr_transport_bridge.py"),
]


class ManagedBridge:
    """One bridge script, launched and tracked as a subprocess of this
    app. `sys.executable` (this app's own Python interpreter) is used
    directly rather than `uv run` -- these scripts only need `rtmidi`,
    which is already installed in this app's own environment since
    moodr/midi_io.py depends on it too, so there's no separate
    environment to resolve."""

    def __init__(self, name: str, script_path: Path):
        self.name = name
        self.script_path = script_path
        self._process: subprocess.Popen | None = None
        self._started_at: float | None = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def started_at(self) -> float | None:
        return self._started_at if self.is_running else None

    @property
    def uptime_seconds(self) -> float | None:
        started_at = self.started_at
        return None if started_at is None else time.time() - started_at

    def start(self) -> None:
        if self.is_running or not self.script_path.exists():
            return
        self._process = subprocess.Popen(
            [sys.executable, "-u", str(self.script_path)],
            cwd=self.script_path.parent,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._started_at = time.time()

    def stop(self, timeout: float = 3.0) -> None:
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        self._process = None
        self._started_at = None

    def restart(self) -> None:
        self.stop()
        self.start()


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
    """"Running since 14:32:07 (5m 12s)" or "Not running"."""
    if not bridge.is_running:
        return "Not running"
    clock = time.strftime("%H:%M:%S", time.localtime(bridge.started_at))
    return f"Running since {clock} ({format_duration(bridge.uptime_seconds)})"


def discover_bridges() -> list[ManagedBridge]:
    """Returns a ManagedBridge for each known bridge script that actually
    exists next to this repo's main.py -- silently skips any that don't,
    since these scripts are specific to an M8/Teensy setup, not something
    every m00Dr user has."""
    return [ManagedBridge(name, REPO_ROOT / filename)
            for name, filename in KNOWN_BRIDGE_SCRIPTS
            if (REPO_ROOT / filename).exists()]
