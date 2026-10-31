# /// script
# requires-python = ">=3.11"
# dependencies = ["aiortc==1.9.0", "aiohttp==3.10.10"]
# ///
"""WebRTC stream of the m800Dr Mix: stereo Opus over UDP, for the player page.

The raw /stream is PCM over one TCP connection, so a single lost Wi-Fi packet
stalls everything behind it until it's resent: a stutter, and a buffer big
enough to ride it out. Over WebRTC the audio goes as Opus in RTP over UDP,
and the browser's jitter buffer conceals a lost packet (a few ms of
interpolated audio) instead of waiting for it, and adapts its delay to the
network.

Signalling is one request: the page POSTs its offer to server.py's
/webrtc/offer, which forwards it here (localhost only), and gets the answer
back. Each listener gets its own `parec` capture of the mix, like /stream.

    uv run webrtc.py   # the m800dr-webrtc user service does this
"""
import argparse, asyncio, fractions, json

import av
import aiortc.codecs.opus
from aiohttp import web
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription


class _MusicOpus:
    """aiortc creates its Opus encoders with OPUS_APPLICATION_VOIP, tuned for
    speech, and its binding exposes no encoder ctl to change it. Swap in
    OPUS_APPLICATION_AUDIO (2049 in opus_defines.h), tuned for music; at 48 kHz
    stereo, 20 ms frames, Opus then picks ~99 kbps by itself."""
    OPUS_APPLICATION_VOIP = 2049

    def __init__(self, lib):
        self._lib = lib

    def __getattr__(self, name):
        return getattr(self._lib, name)


aiortc.codecs.opus.lib = _MusicOpus(aiortc.codecs.opus.lib)

SOURCE = "m800dr_mix.monitor"
RATE, CHANNELS = 48000, 2
FRAME = 960                                   # 20 ms: aiortc's Opus packet size
FRAME_BYTES = FRAME * CHANNELS * 2
PAREC = ["parec", "-d", SOURCE, "--raw", "--format=s16le", f"--rate={RATE}",
         f"--channels={CHANNELS}", "--latency-msec=10", "--client-name=m800Dr webrtc"]


class MixTrack(MediaStreamTrack):
    """The mix, 20 ms at a time, paced by parec's real-time capture."""
    kind = "audio"

    def __init__(self):
        super().__init__()
        self.proc, self.pts = None, 0

    async def recv(self):
        if self.proc is None:
            self.proc = await asyncio.create_subprocess_exec(
                *PAREC, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            data = await self.proc.stdout.readexactly(FRAME_BYTES)
        except asyncio.IncompleteReadError:
            self.stop()
            raise
        frame = av.AudioFrame(format="s16", layout="stereo", samples=FRAME)
        frame.planes[0].update(data)
        frame.sample_rate, frame.time_base, frame.pts = RATE, fractions.Fraction(1, RATE), self.pts
        self.pts += FRAME
        return frame

    def stop(self):
        super().stop()
        if self.proc is not None and self.proc.returncode is None:
            self.proc.kill()


peers = set()


async def offer(request):
    params = await request.json()
    pc = RTCPeerConnection()
    peers.add(pc)
    track = MixTrack()
    peer = request.headers.get("X-Forwarded-For", request.remote)

    @pc.on("connectionstatechange")
    async def on_state():
        print(f"{peer}: {pc.connectionState}", flush=True)
        if pc.connectionState in ("failed", "closed"):
            track.stop()
            peers.discard(pc)
            await pc.close()

    await pc.setRemoteDescription(RTCSessionDescription(sdp=params["sdp"], type=params["type"]))
    pc.addTrack(track)   # onto the audio transceiver the page's recvonly offer made
    await pc.setLocalDescription(await pc.createAnswer())   # aiortc gathers its candidates here
    return web.json_response({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})


async def on_shutdown(app):
    await asyncio.gather(*(pc.close() for pc in peers))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8809)
    args = ap.parse_args()
    app = web.Application()
    app.router.add_post("/offer", offer)
    # For server.py's idle check: someone listening keeps the roller and model on.
    app.router.add_get("/listeners", lambda request: web.json_response(
        {"count": sum(pc.connectionState == "connected" for pc in peers)}))
    app.on_shutdown.append(on_shutdown)
    print(f"m800Dr WebRTC signalling on http://{args.host}:{args.port}/offer", flush=True)
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
