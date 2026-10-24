# /// script
# requires-python = ">=3.11"
# dependencies = ["python-rtmidi", "websockets"]
# ///
"""Forwards a MIDI controller's CCs from this machine to the m800Dr Mixer.

For when the controller (a Launch Control XL) is plugged into the Mac rather
than the desktop: every CC from the first MIDI input whose name contains
PORT is POSTed to the audio server's /mixer/control, which hands it to
mixer.py exactly as if it had come in over USB there. Fader moves are
coalesced to the latest value per CC every SEND_EVERY seconds, so a fast
sweep costs a few dozen requests rather than hundreds. Waits for the
controller to appear and reconnects if it's unplugged.

Transport: the bottom-left and bottom-right buttons (TRANSPORT_NOTES, the
first and last Track Control buttons on the factory templates) each start and
stop m00Dr, over its web remote (--remote), and both stay lit green while
m00Dr plays and red while it's stopped (off while m00Dr can't be reached). The LED is set with a note-on on
the template's channel, learned from the controller's own messages.

    uv run tools/midi_bridge.py [--port "Launch Control XL"] [--server http://192.168.1.17:8808]
                                [--remote ws://192.168.1.17:8766]

Runs at login from ~/Library/LaunchAgents/com.m800dr.midi-bridge.plist.
"""
import argparse, asyncio, json, threading, time, urllib.request
import rtmidi, websockets

SEND_EVERY = 0.02
TRANSPORT_NOTES = (73, 92)   # bottom row, first and last buttons (Track Control 1 and 8)
# Launch Control XL LED velocities: red + 16 * green, plus 12 (the "copy and
# clear" flags a normal write uses).
LED_OFF, LED_RED, LED_GREEN = 12, 15, 60
FACTORY_CHANNEL = 8       # 0-based: factory template 1 is MIDI ch9, until a message says otherwise


class Transport:
    """m00Dr's play state over its web remote, and the button's LED."""

    def __init__(self, remote):
        self.remote, self.playing, self.channel = remote, None, FACTORY_CHANNEL
        self.midi_out = None
        self.loop = self.clicks = None   # set in run(): presses cross over from rtmidi's thread

    def led(self):
        if self.midi_out is not None and self.midi_out.is_port_open():
            colour = LED_OFF if self.playing is None else LED_GREEN if self.playing else LED_RED
            for note in TRANSPORT_NOTES:
                self.midi_out.send_message([0x90 | self.channel, note, colour])

    def press(self):
        if self.playing is not None and self.loop is not None:
            self.loop.call_soon_threadsafe(self.clicks.put_nowait, "stop" if self.playing else "play")

    def run(self):
        asyncio.run(self._connect())

    async def _connect(self):
        self.loop, self.clicks = asyncio.get_running_loop(), asyncio.Queue()
        while True:
            try:
                async with websockets.connect(self.remote) as ws:
                    print(f"transport: connected to {self.remote}", flush=True)
                    sender = asyncio.create_task(self._send(ws))
                    try:
                        async for text in ws:
                            playing = json.loads(text).get("playing")
                            if playing is not None and playing != self.playing:
                                self.playing = bool(playing); self.led()
                    finally:
                        sender.cancel()
            except (OSError, websockets.WebSocketException) as e:
                print(f"transport: {self.remote} unreachable ({e})", flush=True)
            if self.playing is not None:
                self.playing = None; self.led()
            await asyncio.sleep(3)

    async def _send(self, ws):
        while True:
            await ws.send(json.dumps({"op": "click", "id": await self.clicks.get()}))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", default="Launch Control XL")
    ap.add_argument("--server", default="http://192.168.1.17:8808")
    ap.add_argument("--remote", default="ws://192.168.1.17:8766", help="m00Dr's web remote")
    args = ap.parse_args()
    url = args.server.rstrip("/") + "/mixer/control"

    latest, lock = {}, threading.Lock()   # (status, cc) -> value, waiting to be sent

    transport = Transport(args.remote)
    threading.Thread(target=transport.run, daemon=True).start()

    def on_message(event, _):
        msg, _ = event
        if len(msg) == 3 and msg[0] & 0xF0 in (0x80, 0x90, 0xB0) and msg[0] & 0x0F != transport.channel:
            transport.channel = msg[0] & 0x0F   # the template changed: light it on the new channel
            transport.led()
        if len(msg) == 3 and msg[0] & 0xF0 == 0x90 and msg[1] in TRANSPORT_NOTES and msg[2]:
            transport.press()
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
    midi_in, midi_out = rtmidi.MidiIn(), rtmidi.MidiOut()
    midi_in.ignore_types(sysex=True, timing=True, active_sense=True)
    open_name = None
    while True:
        names = midi_in.get_ports()
        if open_name not in names:
            if open_name:
                print(f"lost {open_name}", flush=True)
                midi_in.close_port(); open_name = None
                transport.midi_out = None; midi_out.close_port()
            match = next((i for i, n in enumerate(names) if args.port.lower() in n.lower()), None)
            if match is not None:
                midi_in.open_port(match)
                midi_in.set_callback(on_message)
                open_name = names[match]
                print(f"forwarding {open_name} -> {url}", flush=True)
                out = next((i for i, n in enumerate(midi_out.get_ports()) if args.port.lower() in n.lower()), None)
                if out is not None:
                    midi_out.open_port(out)
                    transport.midi_out = midi_out
                    transport.led()
        time.sleep(2)


if __name__ == "__main__":
    main()
