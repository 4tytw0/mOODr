#!/usr/bin/env python3
"""Audio server: streams the m800Dr Mix to web browsers on the LAN.

GET /             -> the player page (index.html)
GET /stream       -> endless raw PCM: signed 16-bit little-endian, 48 kHz, stereo
POST /webrtc/offer -> a WebRTC offer (JSON {sdp, type}), forwarded to webrtc.py
                      (localhost WEBRTC); its answer comes back. The page's
                      WEBRTC mode streams the mix as Opus over UDP that way.
GET /mixer        -> the mixer page (mixer.html): fader positions, drive, meters
GET /mixer/state  -> mixer.py's live state as JSON (503 if it isn't running)
POST /mixer/control -> a JSON fader move, forwarded to mixer.py's UDP control
                       input ({"strip": name, "fader": 0..1} or {"cc": [status, cc, value]})
GET /rig          -> the rig's state as JSON: on, roller, model loaded, who's here,
                     and whether this client may switch it
POST /rig         -> {"on": true|false}: starts the m00Dr side of the rig (synths,
                     m00Dr, then Play) or stops it (Stop, the roller, the synths,
                     the model). Only from this machine and NetBird peers.
POST /presence    -> {"id": ...}: an open player page, every PRESENCE_EVERY s
GET /hexagram     -> the roller's current section as JSON: its lines (bottom first),
                     which of them change next, and the section (503 before a Roll)
GET /acid         -> the 303 page (acid.html): the acid patch's sound controls
GET /acid/state   -> sunvox303.py's live state as JSON (503 if it isn't running)
POST /acid/control -> a JSON knob move, forwarded to sunvox303.py's UDP control
                      input ({"cc": n, "value": 0..127} holds it, {"cc": n, "hold": false} lets go)

Each listener gets its own `parec` capture of the mix, so a listener
dropping off never stalls the others.

Idle: the roller (and with it the model behind llama-swap) only runs while
someone is here: an open player page, a /stream listener or a WebRTC one.
After IDLE_GRACE seconds with nobody, the roller stops (putting the mix back)
and the model is unloaded; when someone comes back to a rig that's on, the
roller starts again. Standard library only (WebRTC itself
is webrtc.py, a separate service).
"""
import argparse
import base64
import http.server
import ipaddress
import json
import os
import pathlib
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

# The m800Dr Mix bus (PipeWire null sink), fed by mixer.py: M8 + m00Dr's synths.
SOURCE = "m800dr_mix.monitor"
RATE, CHANNELS = 48000, 2
CHUNK = RATE * CHANNELS * 2 // 100  # 10 ms of audio per write
PAGE = pathlib.Path(__file__).with_name("index.html")
WEBRTC = "http://127.0.0.1:8809/offer"   # webrtc.py's signalling
RUNTIME = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
# Panel pages: /name, /name/state and /name/control, each backed by a process
# that takes UDP control datagrams and writes its live state to a JSON file.
# name -> (page, UDP control address, state file, what the 503 says).
PANELS = {
    "mixer": (pathlib.Path(__file__).with_name("mixer.html"), ("127.0.0.1", 9810),
              RUNTIME / "m800dr-mixer.json", "mixer not running"),
    "acid": (pathlib.Path(__file__).with_name("acid.html"), ("127.0.0.1", 9811),
             RUNTIME / "m800dr-acid.json", "sunvox303 not running"),
}
HEXAGRAM = RUNTIME / "m00drc-hexagram.json"   # written by the roller each section


# The m00Dr side of the rig, started and stopped as a whole. The mixer, this
# server and the stream stay up, so the page can always turn it back on.
RIG_UNITS = ["m00dr-synth-links", "m00dr-fluidsynth", "m00dr-sunvox303", "m00dr-sunvoxbass",
             "m00dr-sunvoxdrums", "m00dr-sunvoxkeys", "m00dr-breaks", "m00dr-fx", "m00dr"]
ROLLER = "m00drc-roller"
# Who may switch the rig: this machine and NetBird peers, not everyone on the Wi-Fi.
CONTROL_NETS = [ipaddress.ip_network(n) for n in ("127.0.0.0/8", "::1/128", "100.64.0.0/10", "fd7a:3ded:ef08::/48")]
M00DR_REMOTE = ("127.0.0.1", 8766)       # m00Dr's web remote (WebSocket)
LLAMA_SWAP = "http://127.0.0.1:8080"
WEBRTC_LISTENERS = "http://127.0.0.1:8809/listeners"
PRESENCE_EVERY, IDLE_GRACE = 15, 180      # s: the page's heartbeat, and how long nobody before the roller stops

presence: dict[str, float] = {}           # page id -> last heartbeat (monotonic)
raw_listeners = 0
state_lock = threading.Lock()
rig_busy = threading.Lock()               # one start or stop at a time


def systemctl(*args) -> bool:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, timeout=90).returncode == 0


def active(unit) -> bool:
    return systemctl("is-active", "-q", unit)


def m00dr_click(control) -> None:
    """Clicks one of m00Dr's buttons ("play", "stop") over its web remote: a
    WebSocket handshake and one masked text frame, standard library only."""
    key = base64.b64encode(os.urandom(16)).decode()
    with socket.create_connection(M00DR_REMOTE, timeout=3) as s:
        s.sendall(("GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        if b" 101 " not in s.recv(1024).split(b"\r\n", 1)[0]:
            raise OSError("m00Dr's web remote didn't upgrade")
        payload, mask = json.dumps({"op": "click", "id": control}).encode(), os.urandom(4)
        s.sendall(bytes([0x81, 0x80 | len(payload)]) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
        time.sleep(0.3)


def models_loaded() -> list[str]:
    try:
        with urllib.request.urlopen(LLAMA_SWAP + "/running", timeout=2) as r:
            return [m["model"] for m in json.load(r).get("running", [])]
    except (OSError, ValueError):
        return []


def unload_models() -> None:
    try:
        urllib.request.urlopen(LLAMA_SWAP + "/unload", timeout=10).read()
    except OSError:
        pass


def here() -> int:
    """Open pages, raw listeners and WebRTC listeners right now."""
    now = time.monotonic()
    with state_lock:
        for k in [k for k, t in presence.items() if now - t > PRESENCE_EVERY * 3]:
            del presence[k]
        n = len(presence) + raw_listeners
    try:
        with urllib.request.urlopen(WEBRTC_LISTENERS, timeout=2) as r:
            n += json.load(r)["count"]
    except (OSError, ValueError, KeyError):
        pass
    return n


def rig(on: bool) -> None:
    with rig_busy:
        if on:
            systemctl("start", *RIG_UNITS)
            for _ in range(60):              # m00Dr's window and its web remote take a few seconds
                try:
                    m00dr_click("play")
                    break
                except OSError:
                    time.sleep(1)
            print("rig: started", flush=True)
        else:
            try:
                m00dr_click("stop")
            except OSError:
                pass
            systemctl("stop", ROLLER)        # first, so it puts the mix back
            systemctl("stop", *RIG_UNITS)
            unload_models()
            print("rig: stopped, model unloaded", flush=True)


def watch_idle() -> None:
    """Stops the roller (and unloads the model) when nobody's here; starts it again when they're back."""
    last_seen = time.monotonic()
    while True:
        time.sleep(10)
        if rig_busy.locked():
            continue
        if here():
            last_seen = time.monotonic()
            if active("m00dr") and not active(ROLLER):
                systemctl("start", ROLLER)
                print("idle: someone's here, roller started", flush=True)
        elif time.monotonic() - last_seen > IDLE_GRACE and active(ROLLER):
            systemctl("stop", ROLLER)
            unload_models()
            print(f"idle: nobody here for {IDLE_GRACE}s, roller stopped and model unloaded", flush=True)


class Handler(http.server.BaseHTTPRequestHandler):
    def may_control(self) -> bool:
        try:
            ip = ipaddress.ip_address(self.client_address[0].removeprefix("::ffff:"))
        except ValueError:
            return False
        return any(ip in net for net in CONTROL_NETS)

    def do_GET(self):
        self.path = self.path.split("?", 1)[0]   # /mixer?embed is the same page
        if self.path in ("/", "/index.html"):
            self.send_body(PAGE.read_bytes(), "text/html; charset=utf-8")
        elif self.path.strip("/") in PANELS:
            self.send_body(PANELS[self.path.strip("/")][0].read_bytes(), "text/html; charset=utf-8")
        elif self.path.endswith("/state") and self.path[1:-6] in PANELS:
            _, _, state, missing = PANELS[self.path[1:-6]]
            try:
                self.send_body(state.read_bytes(), "application/json")
            except OSError:
                self.send_error(503, missing)
        elif self.path == "/stream":
            self.stream()
        elif self.path == "/hexagram":
            try:
                self.send_body(HEXAGRAM.read_bytes(), "application/json")
            except OSError:
                self.send_error(503, "no hexagram yet")
        elif self.path == "/rig":
            self.send_body(json.dumps({
                "on": active("m00dr"), "roller": active(ROLLER), "busy": rig_busy.locked(),
                "models": models_loaded(), "here": here(), "control": self.may_control(),
            }).encode(), "application/json")
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/webrtc/offer":
            self.webrtc_offer()
            return
        if self.path in ("/rig", "/presence"):
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length)) if 0 < length <= 256 else {}
            except ValueError:
                body = {}
            if self.path == "/presence":
                with state_lock:
                    presence[str(body.get("id", self.client_address[0]))[:64]] = time.monotonic()
            elif not self.may_control():
                self.send_error(403, "rig control is for this machine and NetBird peers only")
                return
            elif isinstance(body.get("on"), bool) and not rig_busy.locked():
                threading.Thread(target=rig, args=(body["on"],), daemon=True).start()
            self.send_response(204)
            self.end_headers()
            return
        panel = self.path[1:-8] if self.path.endswith("/control") else None
        if panel not in PANELS:
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        if not 0 < length <= 4096:
            self.send_error(400)
            return
        body = self.rfile.read(length)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(body, PANELS[panel][1])
        self.send_response(204)
        self.end_headers()

    def webrtc_offer(self):
        length = int(self.headers.get("Content-Length", 0))
        if not 0 < length <= 65536:
            self.send_error(400)
            return
        request = urllib.request.Request(
            WEBRTC, self.rfile.read(length), method="POST",
            headers={"Content-Type": "application/json", "X-Forwarded-For": self.client_address[0]})
        try:
            with urllib.request.urlopen(request, timeout=10) as res:
                self.send_body(res.read(), "application/json")
        except (urllib.error.URLError, OSError):
            self.send_error(503, "webrtc.py not running")

    def send_body(self, body, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_request(self, code="-", size="-"):
        if not self.path.endswith(("/state", "/control", "/presence", "/rig")):   # high-rate, not worth a log line
            super().log_request(code, size)

    def stream(self):
        cmd = [
            "parec", "-d", SOURCE, "--raw", "--format=s16le",
            f"--rate={RATE}", f"--channels={CHANNELS}",
            "--latency-msec=10", "--client-name=m800Dr web stream",
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Audio-Format", f"s16le;rate={RATE};channels={CHANNELS}")
        self.end_headers()
        self.log_message("listener connected")
        global raw_listeners
        with state_lock:
            raw_listeners += 1
        try:
            while chunk := proc.stdout.read1(CHUNK):
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with state_lock:
                raw_listeners -= 1
            proc.kill()
            proc.wait()
            self.log_message("listener gone")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8808)
    args = ap.parse_args()
    server = http.server.ThreadingHTTPServer((args.host, args.port), Handler)
    threading.Thread(target=watch_idle, daemon=True).start()
    server.daemon_threads = True
    print(f"m800Dr audio server on http://{args.host}:{args.port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
