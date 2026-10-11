# /// script
# requires-python = ">=3.11"
# dependencies = ["python-rtmidi"]
# ///
"""Forwards a MIDI controller's CCs from this machine to the m800Dr Mixer.

For when the controller (a Launch Control XL) is plugged into the Mac rather
than the desktop: every CC from the first MIDI input whose name contains
PORT is POSTed to the audio server's /mixer/control, which hands it to
mixer.py exactly as if it had come in over USB there. Fader moves are
coalesced to the latest value per CC every SEND_EVERY seconds, so a fast
sweep costs a few dozen requests rather than hundreds. Waits for the
controller to appear and reconnects if it's unplugged.

    uv run tools/midi_bridge.py [--port "Launch Control XL"] [--server http://192.168.1.17:8808]

Runs at login from ~/Library/LaunchAgents/com.m800dr.midi-bridge.plist.
"""
import argparse, json, threading, time, urllib.request
import rtmidi

SEND_EVERY = 0.02


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", default="Launch Control XL")
    ap.add_argument("--server", default="http://192.168.1.17:8808")
    args = ap.parse_args()
    url = args.server.rstrip("/") + "/mixer/control"

    latest, lock = {}, threading.Lock()   # (status, cc) -> value, waiting to be sent

    def on_message(event, _):
        msg, _ = event
        if len(msg) == 3 and msg[0] & 0xF0 == 0xB0:
            with lock:
                latest[(msg[0], msg[1])] = msg[2]

    def sender():
        while True:
            time.sleep(SEND_EVERY)
            with lock:
                batch = list(latest.items()); latest.clear()
            for (status, cc), value in batch:
                body = json.dumps({"cc": [status, cc, value]}).encode()
                try:
                    urllib.request.urlopen(urllib.request.Request(url, body, method="POST"), timeout=2)
                except OSError as e:
                    print(f"POST failed: {e}", flush=True)

    threading.Thread(target=sender, daemon=True).start()
    midi_in = rtmidi.MidiIn()
    midi_in.ignore_types(sysex=True, timing=True, active_sense=True)
    open_name = None
    while True:
        names = midi_in.get_ports()
        if open_name not in names:
            if open_name:
                print(f"lost {open_name}", flush=True)
                midi_in.close_port(); open_name = None
            match = next((i for i, n in enumerate(names) if args.port.lower() in n.lower()), None)
            if match is not None:
                midi_in.open_port(match)
                midi_in.set_callback(on_message)
                open_name = names[match]
                print(f"forwarding {open_name} -> {url}", flush=True)
        time.sleep(2)


if __name__ == "__main__":
    main()
