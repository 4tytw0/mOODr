"""Transport bridge: forwards the M8's own MIDI clock/Start/Stop/Continue
to m00Dr's slave-clock input ("m00Dr In"), so pressing Play on the M8
itself starts and tempo-syncs m00Dr -- the reverse direction of
moodr_to_m8_bridge.py, which carries m00Dr's generated notes to the M8.

Run alongside m00Dr (with its "External clock sync" checkbox ON) and
moodr_to_m8_bridge.py, each in its own terminal:

    uv run python m8_to_moodr_transport_bridge.py

Stop with Ctrl+C.

Only forwards Timing Clock (0xF8) and Start/Continue/Stop (0xFA/0xFB/0xFC)
-- the M8's MIDI port also carries whatever else it sends, but m00Dr's
MidiClockSlave only reacts to those four message types anyway, so this
filters to just them to keep the transport port's traffic predictable.

Self-healing on BOTH ends, unconditionally, every RECONNECT_INTERVAL_SECONDS:
both "M8" (goes stale on a Teensy unplug/replug) and "m00Dr In" (a brand new
CoreMIDI endpoint every time m00Dr restarts, even though it keeps the same
name) are re-opened every cycle, not just whichever one this script's first
version happened to refresh. An earlier version only refreshed the source
("M8") and opened the destination ("m00Dr In") once at startup -- meaning
it silently kept writing into a dead endpoint after every single m00Dr
restart while still reporting "Connected" (its source side never noticed
anything was wrong). Confirmed via direct MIDI sniffing on 2026-09-19: the
M8's transport messages were reaching this bridge's source, but m00Dr never
produced any note output in response, with a mismatch between how long this
bridge process had been running and how many times m00Dr had been restarted
since being the tell.
"""

import time

import rtmidi

SOURCE_PORT_NAME = "M8"
DEST_PORT_NAME = "m00Dr In"
RECONNECT_INTERVAL_SECONDS = 3.0

TRANSPORT_STATUSES = {0xF8, 0xFA, 0xFB, 0xFC}  # Clock, Start, Continue, Stop


def _open_source() -> rtmidi.MidiIn | None:
    midi_in = rtmidi.MidiIn()
    # Let clock/start/stop through; sysex and active-sense aren't needed here.
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
    print(f"Bridging '{SOURCE_PORT_NAME}' transport -> '{DEST_PORT_NAME}', "
          f"reconnecting every {RECONNECT_INTERVAL_SECONDS}s. Ctrl+C to stop.")

    midi_in: rtmidi.MidiIn | None = None
    midi_out: rtmidi.MidiOut | None = None
    source_connected = False
    dest_connected = False

    def on_message(event, _data):
        message, _delta_time = event
        # midi_out is looked up fresh from the enclosing scope on every call,
        # so a reconnect elsewhere in the loop is picked up automatically --
        # this closure is never holding a stale reference of its own.
        if midi_out is not None and message and message[0] in TRANSPORT_STATUSES:
            midi_out.send_message(message)

    try:
        while True:
            if midi_in is not None:
                midi_in.cancel_callback()
                midi_in.close_port()
            midi_in = _open_source()
            if midi_in is not None:
                midi_in.set_callback(on_message)
                if not source_connected:
                    print(f"Connected to '{SOURCE_PORT_NAME}'.")
                source_connected = True
            elif source_connected:
                print(f"'{SOURCE_PORT_NAME}' not found -- is the M8/Teensy plugged in?")
                source_connected = False

            if midi_out is not None:
                midi_out.close_port()
            midi_out = _open_dest()
            if midi_out is not None:
                if not dest_connected:
                    print(f"Connected to '{DEST_PORT_NAME}'.")
                dest_connected = True
            elif dest_connected:
                print(f"'{DEST_PORT_NAME}' not found -- is m00Dr running with "
                      f"'External clock sync' checked?")
                dest_connected = False

            time.sleep(RECONNECT_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        if midi_in is not None:
            midi_in.cancel_callback()
            midi_in.close_port()
        if midi_out is not None:
            midi_out.close_port()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
