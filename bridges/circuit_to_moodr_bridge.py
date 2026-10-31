"""Live MIDI passthrough, the reverse direction of
moodr_to_circuit_bridge.py: forwards everything a Novation Circuit Tracks
sends (MIDI clock, Start/Stop/Continue, notes) over USB straight into
m00Dr's "m00Dr In" virtual port -- the one its "External clock sync"
checkbox listens on. With that checkbox on, m00Dr's MidiClockSlave
already reacts to incoming Start/Stop messages by calling _on_play()/
_on_stop() (see moodr/clock.py and moodr/app.py's _on_sync_mode_toggled),
so once this bridge is running, pressing Play/Stop on the Circuit Tracks
itself starts/stops m00Dr in sync, following its tempo too.

Requires "External clock sync" to be checked in m00Dr *before* starting
this bridge -- that's what creates the "m00Dr In" port in the first
place; it doesn't exist until then.

Run alongside m00Dr and moodr_to_circuit_bridge.py, in its own terminal:

    uv run python circuit_to_moodr_bridge.py

Stop with Ctrl+C.

Self-healing like its counterpart: m00Dr's "m00Dr In" port is a brand new
CoreMIDI endpoint every time m00Dr (re)starts or "External clock sync" is
toggled off then on again, even though it keeps the same name -- an
already-open connection goes silently dead. This bridge re-opens its
destination connection on a fixed interval, unconditionally, so it
self-heals within one interval of any such change with no manual
intervention needed.
"""

import time

import rtmidi

SOURCE_PORT_NAME = "Circuit Tracks MIDI"
DEST_PORT_NAME = "m00Dr In"
RECONNECT_INTERVAL_SECONDS = 3.0


def _open_source() -> rtmidi.MidiIn | None:
    midi_in = rtmidi.MidiIn()
    midi_in.ignore_types(sysex=True, timing=False, active_sense=True)
    ports = midi_in.get_ports()
    if SOURCE_PORT_NAME not in ports:
        return None
    midi_in.open_port(ports.index(SOURCE_PORT_NAME))
    return midi_in


def _open_dest() -> rtmidi.MidiOut | None:
    midi_out = rtmidi.MidiOut()
    ports = midi_out.get_ports()
    if DEST_PORT_NAME not in ports:
        return None
    midi_out.open_port(ports.index(DEST_PORT_NAME))
    return midi_out


def main() -> int:
    midi_in = _open_source()
    if midi_in is None:
        print(f"'{SOURCE_PORT_NAME}' not found -- is the Circuit Tracks plugged in?")
        return 1

    print(f"Bridging '{SOURCE_PORT_NAME}' -> '{DEST_PORT_NAME}', "
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
            midi_out = _open_dest()
            if midi_out is not None:
                if not was_connected:
                    print(f"Connected to '{DEST_PORT_NAME}'.")
                was_connected = True
            elif was_connected:
                print(f"'{DEST_PORT_NAME}' not found -- is m00Dr running with "
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
