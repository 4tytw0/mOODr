#!/usr/bin/env python3
"""Streams the Dirtywave M8's USB audio to web browsers on the LAN.

GET /        -> the player page (index.html)
GET /stream  -> endless raw PCM: signed 16-bit little-endian, 48 kHz, stereo

Each listener gets its own `parec` capture of the M8 source, so a listener
dropping off never stalls the others. Standard library only.
"""
import argparse
import http.server
import pathlib
import socket
import subprocess

# The m800Dr Mix bus (PipeWire null sink): M8 + FluidSynth + Open303.
SOURCE = "m800dr_mix.monitor"
RATE, CHANNELS = 48000, 2
CHUNK = RATE * CHANNELS * 2 // 100  # 10 ms of audio per write
PAGE = pathlib.Path(__file__).with_name("index.html")


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = PAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/stream":
            self.stream()
        else:
            self.send_error(404)

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
    print(f"m800Dr audio on http://{args.host}:{args.port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
