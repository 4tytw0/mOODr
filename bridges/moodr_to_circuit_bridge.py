"""Live MIDI passthrough: forwards everything m00Dr sends (notes, CCs,
clock/start/stop) straight through to a Novation Circuit Tracks over USB
MIDI. m00Dr's own channel layout (chords=ch1/3, bass=ch3/4 depending on
the "Ch 3/4" toggle) already matches Circuit Tracks' own track channels,
so this is a transparent 1:1 forward -- no remapping needed. Run alongside
m00Dr, in a separate terminal:

    uv run python moodr_to_circuit_bridge.py

Stop with Ctrl+C.

Self-healing: m00Dr's virtual MIDI port is a brand new CoreMIDI endpoint
every time m00Dr (re)starts, even though it keeps the same name -- an
already-open connection to the old one goes silently dead and does not
reconnect itself. Restarting this bridge after every single m00Dr restart
got old fast, so instead it just re-opens its connection on a fixed
interval, unconditionally, which is cheap and self-heals within one
interval of an m00Dr restart with no manual intervention needed.

Note: a browser tab connected to m00Dr via WebMIDI (e.g. the Strudel
bridge) appears to claim the only active listener slot on m00Dr's virtual
MIDI port. Don't run this alongside an active Strudel MIDI-bridge session
-- close that tab (or don't press Play there) first.
"""

import time

import rtmidi

SOURCE_PORT_NAME = "m00Dr"
DEST_PORT_NAME = "Circuit Tracks MIDI"
RECONNECT_INTERVAL_SECONDS = 3.0


def _open_source() -> rtmidi.MidiIn | None:
    midi_in = rtmidi.MidiIn()
    # timing=False lets 0xF8 clock pulses through too, so the Circuit
    # Tracks can follow m00Dr's tempo if set to external MIDI clock --
    # same choice moodr.midi_io.MidiInput makes for the same reason.
    midi_in.ignore_types(sysex=True, timing=False, active_sense=True)
    ports = midi_in.get_ports()
    if SOURCE_PORT_NAME not in ports:
        return None
    midi_in.open_port(ports.index(SOURCE_PORT_NAME))
    return midi_in


def main() -> int:
    midi_out = rtmidi.MidiOut()
    out_ports = midi_out.get_ports()
    if DEST_PORT_NAME not in out_ports:
        print(f"'{DEST_PORT_NAME}' not found -- is the Circuit Tracks plugged in? "
              f"Available: {out_ports}")
        return 1
    midi_out.open_port(out_ports.index(DEST_PORT_NAME))

    def on_message(event, _data):
        message, _delta_time = event
        midi_out.send_message(message)

    print(f"Bridging '{SOURCE_PORT_NAME}' -> '{DEST_PORT_NAME}', "
          f"reconnecting every {RECONNECT_INTERVAL_SECONDS}s. Ctrl+C to stop.")
    midi_in: rtmidi.MidiIn | None = None
    was_connected = False
    try:
        while True:
            if midi_in is not None:
                midi_in.cancel_callback()
                midi_in.close_port()
            midi_in = _open_source()
            if midi_in is not None:
                midi_in.set_callback(on_message)
                if not was_connected:
                    print(f"Connected to '{SOURCE_PORT_NAME}'.")
                was_connected = True
            elif was_connected:
                print(f"'{SOURCE_PORT_NAME}' not found -- is m00Dr running?")
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
