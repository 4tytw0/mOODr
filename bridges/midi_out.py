# /// script
# requires-python = ">=3.11"
# dependencies = ["python-rtmidi"]
# ///
"""Live MIDI passthrough: forwards everything m00Dr sends (notes, CCs,
clock/start/stop) to another MIDI port -- by default a Novation Circuit
Tracks over USB, whose track channels already match m00Dr's layout
(chords=ch1/3, bass=ch3/4 depending on the "Ch 3/4" toggle), so it's a
transparent 1:1 forward with no remapping. Run alongside m00Dr:

    uv run bridges/midi_out.py                          # m00Dr -> Circuit Tracks
    uv run bridges/midi_out.py --to "IAC Driver Bus 1"  # m00Dr -> any port
    uv run bridges/midi_out.py --list                   # what's there

Ports match on part of their name (--from/--to), so the same names work
with CoreMIDI on the Mac and ALSA on Linux ("m00Dr:m00Dr 128:0").

Somewhere else on the network: m00Dr runs on the desktop, so to play a
device or a DAW on the Mac, put an RTP-MIDI session between them. On the
Mac, Audio MIDI Setup > MIDI Studio > Network: create a session and enable
it; on the desktop, run rtpmidid (it joins sessions it finds and shows up
as an ALSA port). Then, on the desktop:

    uv run bridges/midi_out.py --to rtpmidid

and on the Mac the session is a CoreMIDI port: a DAW can use it directly,
or forward it on to a USB device with

    uv run bridges/midi_out.py --from "Network Session 1" --to "Circuit Tracks"

Stop with Ctrl+C.

Self-healing: m00Dr's virtual MIDI port is a brand new endpoint every time
m00Dr (re)starts, even though it keeps the same name -- an already-open
connection to the old one goes silently dead and does not reconnect
itself. So the source is re-opened on a fixed interval, unconditionally,
which is cheap and self-heals within one interval of an m00Dr restart.

Note: a browser tab connected to m00Dr via WebMIDI (e.g. the Strudel
bridge) appears to claim the only active listener slot on m00Dr's virtual
MIDI port. Don't run this alongside an active Strudel MIDI-bridge session
-- close that tab (or don't press Play there) first.
"""

import argparse
import time

import rtmidi

SOURCE_PORT_NAME = "m00Dr"
DEST_PORT_NAME = "Circuit Tracks MIDI"
RECONNECT_INTERVAL_SECONDS = 3.0


def find_port(ports: list[str], name: str) -> int | None:
    """The first port whose name contains `name`, exact matches first."""
    if name in ports:
        return ports.index(name)
    return next((i for i, port in enumerate(ports) if name.lower() in port.lower()), None)


def _open_source(name: str) -> rtmidi.MidiIn | None:
    midi_in = rtmidi.MidiIn()
    # timing=False lets 0xF8 clock pulses through too, so the destination
    # can follow m00Dr's tempo if set to external MIDI clock -- same choice
    # moodr.midi_io.MidiInput makes for the same reason.
    midi_in.ignore_types(sysex=True, timing=False, active_sense=True)
    index = find_port(midi_in.get_ports(), name)
    if index is None:
        return None
    midi_in.open_port(index)
    return midi_in


def main() -> int:
    parser = argparse.ArgumentParser(description="Forward m00Dr's MIDI to another port.")
    parser.add_argument("--from", dest="source", default=SOURCE_PORT_NAME,
                        help=f"source port, by part of its name (default {SOURCE_PORT_NAME!r})")
    parser.add_argument("--to", dest="dest", default=DEST_PORT_NAME,
                        help=f"destination port, by part of its name (default {DEST_PORT_NAME!r})")
    parser.add_argument("--list", action="store_true", help="list the MIDI ports and exit")
    args = parser.parse_args()

    midi_out = rtmidi.MidiOut()
    out_ports = midi_out.get_ports()
    if args.list:
        print("inputs: ", rtmidi.MidiIn().get_ports())
        print("outputs:", out_ports)
        return 0
    index = find_port(out_ports, args.dest)
    if index is None:
        print(f"'{args.dest}' not found -- is it plugged in? Available: {out_ports}")
        return 1
    midi_out.open_port(index)
    dest = out_ports[index]

    def on_message(event, _data):
        message, _delta_time = event
        midi_out.send_message(message)

    print(f"Bridging '{args.source}' -> '{dest}', "
          f"reconnecting every {RECONNECT_INTERVAL_SECONDS}s. Ctrl+C to stop.")
    midi_in: rtmidi.MidiIn | None = None
    was_connected = False
    try:
        while True:
            if midi_in is not None:
                midi_in.cancel_callback()
                midi_in.close_port()
            midi_in = _open_source(args.source)
            if midi_in is not None:
                midi_in.set_callback(on_message)
                if not was_connected:
                    print(f"Connected to '{args.source}'.")
                was_connected = True
            elif was_connected:
                print(f"'{args.source}' not found -- is m00Dr running?")
                was_connected = False
            time.sleep(RECONNECT_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        if midi_in is not None:
            midi_in.cancel_callback()
            midi_in.close_port()
        midi_out.close_port()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
