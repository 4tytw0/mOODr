# /// script
# requires-python = ">=3.11"
# dependencies = ["python-rtmidi"]
# ///
"""Live MIDI passthrough, the reverse direction of midi_out.py: forwards
everything another MIDI port sends (MIDI clock, Start/Stop/Continue,
notes) into m00Dr's "m00Dr In" virtual port -- the one its "External clock
sync" checkbox listens on. By default the source is a Novation Circuit
Tracks over USB. With that checkbox on, m00Dr's MidiClockSlave already
reacts to incoming Start/Stop messages by calling _on_play()/_on_stop()
(see moodr/clock.py and moodr/app.py's _on_sync_mode_toggled), so once
this bridge is running, pressing Play/Stop on the device itself starts/
stops m00Dr in sync, following its tempo too.

    uv run bridges/midi_in.py                            # Circuit Tracks -> m00Dr
    uv run bridges/midi_in.py --from "Network Session 1" # any port -> m00Dr
    uv run bridges/midi_in.py --list                     # what's there

Ports match on part of their name, as in midi_out.py; its docstring covers
reaching m00Dr on the desktop from a device on the Mac (an RTP-MIDI session).

Requires "External clock sync" to be checked in m00Dr *before* starting
this bridge -- that's what creates the "m00Dr In" port in the first
place; it doesn't exist until then.

Stop with Ctrl+C.

Self-healing like its counterpart: m00Dr's "m00Dr In" port is a brand new
endpoint every time m00Dr (re)starts or "External clock sync" is toggled
off then on again, even though it keeps the same name -- an already-open
connection goes silently dead. This bridge re-opens its destination
connection on a fixed interval, unconditionally, so it self-heals within
one interval of any such change with no manual intervention needed.
"""

import argparse
import time

import rtmidi

from midi_out import find_port

SOURCE_PORT_NAME = "Circuit Tracks MIDI"
DEST_PORT_NAME = "m00Dr In"
RECONNECT_INTERVAL_SECONDS = 3.0


def _open_source(name: str) -> rtmidi.MidiIn | None:
    midi_in = rtmidi.MidiIn()
    midi_in.ignore_types(sysex=True, timing=False, active_sense=True)
    index = find_port(midi_in.get_ports(), name)
    if index is None:
        return None
    midi_in.open_port(index)
    return midi_in


def _open_dest(name: str) -> rtmidi.MidiOut | None:
    midi_out = rtmidi.MidiOut()
    index = find_port(midi_out.get_ports(), name)
    if index is None:
        return None
    midi_out.open_port(index)
    return midi_out


def main() -> int:
    parser = argparse.ArgumentParser(description="Forward another port's MIDI into m00Dr.")
    parser.add_argument("--from", dest="source", default=SOURCE_PORT_NAME,
                        help=f"source port, by part of its name (default {SOURCE_PORT_NAME!r})")
    parser.add_argument("--to", dest="dest", default=DEST_PORT_NAME,
                        help=f"destination port, by part of its name (default {DEST_PORT_NAME!r})")
    parser.add_argument("--list", action="store_true", help="list the MIDI ports and exit")
    args = parser.parse_args()

    if args.list:
        print("inputs: ", rtmidi.MidiIn().get_ports())
        print("outputs:", rtmidi.MidiOut().get_ports())
        return 0
    midi_in = _open_source(args.source)
    if midi_in is None:
        print(f"'{args.source}' not found -- is it plugged in?")
        return 1

    print(f"Bridging '{args.source}' -> '{args.dest}', "
          f"reconnecting the destination every {RECONNECT_INTERVAL_SECONDS}s. "
          f"Ctrl+C to stop.")
    midi_out: rtmidi.MidiOut | None = None
    was_connected = False

    def on_message(event, _data):
        if midi_out is not None:
            message, _delta_time = event
            midi_out.send_message(message)

    midi_in.set_callback(on_message)

    try:
        while True:
            if midi_out is not None:
                midi_out.close_port()
            midi_out = _open_dest(args.dest)
            if midi_out is not None:
                if not was_connected:
                    print(f"Connected to '{args.dest}'.")
                was_connected = True
            elif was_connected:
                print(f"'{args.dest}' not found -- is m00Dr running with "
                      f"External clock sync checked?")
                was_connected = False
            time.sleep(RECONNECT_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        midi_in.cancel_callback()
        midi_in.close_port()
        if midi_out is not None:
            midi_out.close_port()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
