"""MIDI service status/reset: the platform MIDI service (CoreMIDI's
MIDIServer on macOS) that every rtmidi port in this app depends on, kept
separate from Qt so it's testable without a display -- same split as
clock.py/midi_io.py from the Qt layer in app.py.

Resetting the MIDI service invalidates every virtual MIDI port already
open in *any* process, not just this one -- rtmidi's port objects don't
automatically reconnect (see midi_io.py's docstring on the same quirk).
Callers that reset it are responsible for reopening their own ports
afterward; this module only does the reset itself.
"""

import os
import subprocess
import sys

import rtmidi

# CoreMIDI (macOS) is the only backend this currently knows how to reset --
# there isn't an equivalent single restartable service on Linux (ALSA) or
# Windows (the OS-level MIDI stack isn't something a user process kills and
# expects to respawn the same way).
MIDI_SERVER_RESET_SUPPORTED = sys.platform == "darwin"

MIDI_SERVER_PROCESS_NAME = "MIDIServer"


def list_ports() -> tuple[list[str], list[str]]:
    """Returns (input_port_names, output_port_names) currently visible to
    the platform MIDI service."""
    return rtmidi.MidiIn().get_ports(), rtmidi.MidiOut().get_ports()


def reset_midi_server() -> bool:
    """Kills macOS's CoreMIDI MIDIServer process; launchd respawns it
    automatically the next time any process makes a CoreMIDI call; no
    action here forces that respawn eagerly. Returns True if a reset was
    attempted (macOS only), False if unsupported on this platform, in
    which case nothing was done. Does not raise if MIDIServer wasn't
    running or `killall` isn't found -- a reset that has nothing to do is
    not a failure."""
    if not MIDI_SERVER_RESET_SUPPORTED:
        return False
    subprocess.run(["killall", "-9", MIDI_SERVER_PROCESS_NAME], check=False,
                    capture_output=True)
    return True


def resolve_app_bundle(path: str) -> str:
    """If `path` is a macOS .app bundle, or is *inside* one (e.g. a file
    dialog let a user pick Contents/MacOS/m8c rather than the bundle
    itself -- native dialog handling of bundles isn't fully reliable
    across Qt/macOS versions), returns the bundle's own root directory.
    Returns `path` unchanged if no ".app" segment is found anywhere in
    it."""
    parts = path.split(os.sep)
    for i, part in enumerate(parts):
        if part.endswith(".app"):
            return os.sep.join(parts[:i + 1])
    return path
