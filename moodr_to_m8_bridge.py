"""Live MIDI passthrough: forwards everything m00Dr sends (notes, CCs,
clock/start/stop) straight through to a Dirtywave M8 (or M8 Headless on a
Teensy) over USB MIDI. Transparent 1:1 forward, no remapping -- point the
M8's own track(s) at m00Dr's output channels from the M8 side: chords on
channel 1, bass on channel 2, arp on channel 3, acid on channel 5 (see
moodr/playback.py's CHORD_CHANNEL/BASS_CHANNEL/ARP_CHANNEL/ACID_CHANNEL).

Run alongside m00Dr, in a separate terminal:

    uv run python moodr_to_m8_bridge.py

Stop with Ctrl+C.

Self-healing on BOTH ends, unconditionally, every RECONNECT_INTERVAL_SECONDS:
both "m00Dr" (a brand new CoreMIDI endpoint every time m00Dr restarts, even
though it keeps the same name) and "M8" (goes stale on a Teensy
unplug/replug) are re-opened every cycle, not just whichever one this
script's first version happened to refresh. An earlier version only
refreshed the source ("m00Dr") and opened the destination ("M8") once at
startup -- meaning it silently kept writing into a dead endpoint after
every Teensy reconnect while still reporting "Connected" (its source side
never noticed anything was wrong), which is why this bridge specifically
needed manual restarts after so many of this session's SD-card swaps and
sleep/wake cycles.

Note: a browser tab connected to m00Dr via WebMIDI (e.g. the Strudel
bridge) appears to claim the only active listener slot on m00Dr's virtual
MIDI port. Don't run this alongside an active Strudel MIDI-bridge session
-- close that tab (or don't press Play there) first.
"""

import time

import rtmidi

SOURCE_PORT_NAME = "m00Dr"
DEST_PORT_NAME = "M8"
RECONNECT_INTERVAL_SECONDS = 3.0


def _open_source() -> rtmidi.MidiIn | None:
    midi_in = rtmidi.MidiIn()
    # timing=False lets 0xF8 clock pulses through too, so the M8 can
    # follow m00Dr's tempo if set to external MIDI clock -- same choice
    # moodr.midi_io.MidiInput makes for the same reason.
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
    print(f"Bridging '{SOURCE_PORT_NAME}' -> '{DEST_PORT_NAME}', "
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
        if midi_out is not None:
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
                print(f"'{SOURCE_PORT_NAME}' not found -- is m00Dr running?")
                source_connected = False

            if midi_out is not None:
                midi_out.close_port()
            midi_out = _open_dest()
            if midi_out is not None:
                if not dest_connected:
                    print(f"Connected to '{DEST_PORT_NAME}'.")
                dest_connected = True
            elif dest_connected:
                print(f"'{DEST_PORT_NAME}' not found -- is the M8/Teensy plugged in?")
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
