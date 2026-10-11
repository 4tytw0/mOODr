#!/usr/bin/env python3
"""Audio server: streams the m800Dr Mix to web browsers on the LAN.

GET /             -> the player page (index.html)
GET /stream       -> endless raw PCM: signed 16-bit little-endian, 48 kHz, stereo
GET /mixer        -> the mixer page (mixer.html): fader positions, drive, meters
GET /mixer/state  -> mixer.py's live state as JSON (503 if it isn't running)
POST /mixer/control -> a JSON fader move, forwarded to mixer.py's UDP control
                       input ({"strip": name, "fader": 0..1} or {"cc": [status, cc, value]})

Each listener gets its own `parec` capture of the mix, so a listener
dropping off never stalls the others. Standard library only.
"""
import argparse
import http.server
import os
import pathlib
import socket
import subprocess

# The m800Dr Mix bus (PipeWire null sink), fed by mixer.py: M8 + m00Dr's synths.
SOURCE = "m800dr_mix.monitor"
RATE, CHANNELS = 48000, 2
CHUNK = RATE * CHANNELS * 2 // 100  # 10 ms of audio per write
PAGE = pathlib.Path(__file__).with_name("index.html")
MIXER_PAGE = pathlib.Path(__file__).with_name("mixer.html")
MIXER_CONTROL = ("127.0.0.1", 9810)
MIXER_STATE = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "m800dr-mixer.json"


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.path = self.path.split("?", 1)[0]   # /mixer?embed is the same page
        if self.path in ("/", "/index.html"):
            self.send_body(PAGE.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/mixer":
            self.send_body(MIXER_PAGE.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/mixer/state":
            try:
                self.send_body(MIXER_STATE.read_bytes(), "application/json")
            except OSError:
                self.send_error(503, "mixer not running")
        elif self.path == "/stream":
            self.stream()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/mixer/control":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        if not 0 < length <= 4096:
            self.send_error(400)
            return
        body = self.rfile.read(length)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(body, MIXER_CONTROL)
        self.send_response(204)
        self.end_headers()

    def send_body(self, body, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_request(self, code="-", size="-"):
        if self.path not in ("/mixer/state", "/mixer/control"):   # high-rate, not worth a log line
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
        try:
            while chunk := proc.stdout.read1(CHUNK):
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            proc.kill()
            proc.wait()
            self.log_message("listener gone")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8808)
    args = ap.parse_args()
    server = http.server.ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"m800Dr audio server on http://{args.host}:{args.port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
