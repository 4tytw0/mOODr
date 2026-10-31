# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy", "JACK-Client"]
# ///
"""m800Dr Mixer: a stereo strip per m00Dr part (and the breaks), on MIDI faders.

A JACK client (PipeWire's libjack) "m800Dr-Mixer" with inputs <strip>_L/_R,
a MIDI input "faders", a MIDI output "controls" and outputs out_L/_R.
mix/links.sh wires the voices into it, any hardware MIDI controller into
"faders", "controls" into m00Dr's control input, and out_L/_R into the
m800dr_mix sink that server.py streams. Every CC that isn't a fader goes out
on "controls" -- e.g. the knob that morphs m00Dr's drum pattern.

Each strip has a MIDI channel (its m00Dr part's: chords 1, bass 2, arp 3,
drums 4, acid 5) and a fader number, the same as the channel except for the
drums, which sit on fader 7 (column 7 of the controller, knobs included), and
breaks.py's breaks on channel and fader 8, and fx.py's risers and atmospheres on channel 10, fader 4. A strip moves on CC FADER_CC on its channel,
or on CC FADER_CCS[fader-1] on any channel: the Novation Launch Control XL's
factory templates send faders 1-8 as CC 77-84.

Faders also move over UDP on CONTROL (localhost; server.py forwards the
/mixer page's drags and tools/midi_bridge.py's controller on the Mac), one
JSON object per datagram: {"cc": [status, cc, value]} like a MIDI CC, or
{"strip": name, "fader": 0..1}. A move without "by" is a hand (the controller,
the /mixer page) and sets the strip's ceiling; one with "by" (m00Drc's arranger
or producer) is held at or under that ceiling. Below KNEE the
fader is plain volume, reaching unity at KNEE; above it the strip blends into
an asymmetric tanh fuzz that gets harder the higher the fader goes.

Sidechain: the drums strip's low end (mostly the kick), after its fader,
ducks the tonal strips and the fx (SIDECHAIN_DUCKED) -- instant attack, SIDECHAIN_RELEASE
recovery, down by up to SIDECHAIN amount (0-1 of their level) on a full-level
kick. The amount moves on CC SIDECHAIN_CC, the /mixer page's slider, or
{"sidechain": 0..1} on CONTROL. The breaks aren't ducked: they're drums too.

Fader positions and the sidechain amount survive restarts (SAVED); the live
state, with meters, is in LIVE for server.py's /mixer page.
Master limiter: the very last stage before out_L/R (so before the stream, the
recorder and any speakers). A -1 dBFS peak ceiling and a LIMIT_RMS cap on the
sustained level. Nothing reaches it from MIDI, UDP or the page: it's the one
part of the mix that m00Drc's producer can't move. LIVE reports its reduction.

    uv run mixer.py   # the m800dr-mixer user service does this
"""
import collections, json, math, os, pathlib, socket, threading, time
import jack
import numpy as np
from scipy.signal import butter, lfilter

STRIPS = [  # (name, MIDI channel, fader)
    ("chords", 1, 1), ("bass", 2, 2), ("arp", 3, 3), ("fx", 10, 4), ("acid", 5, 5), ("drums", 4, 7), ("breaks", 8, 8)]
FADER_CC = 7          # CC 7 = channel volume
FADER_CCS = list(range(77, 85))   # Launch Control XL faders 1-8, on its template's channel
KNEE = 0.8            # fader position of unity gain; above it the strip distorts
DRIVE_MAX = 16.0      # tanh pre-gain at the top of the fader
FUZZ_LEVEL = 0.45     # fuzz path level (it's dense, so it reads louder than this)
BIAS = 0.3            # tanh offset: asymmetric clipping adds even harmonics
DC_POLE = 0.9987      # DC blocker (~10 Hz): the bias leaves an offset behind

SIDECHAIN_KEY = "drums"
SIDECHAIN_DUCKED = ("chords", "bass", "arp", "acid", "fx")
SIDECHAIN_DEFAULT = 0.6   # full duck = 1 - 0.6 = 0.4x, about -8 dB
SIDECHAIN_CC = 35         # Launch Control XL middle knob, drums column 7 (factory layout, assumed)
SIDECHAIN_LPF = 150.0     # Hz: key on the kick, not the hats
SIDECHAIN_FULL = 0.25     # key level (after the low-pass) that ducks by the full amount
SIDECHAIN_RELEASE = 0.15  # s, exponential recovery: the pump
SIDECHAIN_SMOOTH = 0.002  # s, rounds off the instant attack so the duck doesn't click

# Master limiter (see the docstring): no control reaches these.
LIMIT_PEAK = 10 ** (-1.0 / 20)       # -1 dBFS peak ceiling
LIMIT_RMS = 10 ** (-9.0 / 20)        # loudest sustained level let through (RMS over LIMIT_RMS_WINDOW)
LIMIT_RMS_WINDOW, LIMIT_RELEASE = 0.3, 0.25   # s

CONTROL = ("127.0.0.1", 9810)
LIVE = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "m800dr-mixer.json"
SAVED = pathlib.Path.home() / ".local/state/m800dr/mixer.json"


def gain(x):
    return min(x / KNEE, 1.0) ** 2


def drive(x):
    return max(x - KNEE, 0.0) / (1 - KNEE)


class Strip:
    def __init__(self, client, name, channel, number, fader):
        self.name, self.channel, self.number = name, channel, number
        self.ins = [client.inports.register(f"{name}_{s}") for s in "LR"]
        self.fader = fader                       # target, set from MIDI
        self.ceiling = None                      # the last hand position: machine moves stay under it
        self.g, self.d = gain(fader), drive(fader)   # values reached at the end of the last block
        self.zi = np.zeros((2, 1))
        self.peak = 0.0

    def process(self, n):
        """This block's output, after fader and drive (ducking comes later)."""
        g1, d1 = gain(self.fader), drive(self.fader)
        g = np.linspace(self.g, g1, n, dtype=np.float32)   # ramp per block: no zipper noise
        d = np.linspace(self.d, d1, n, dtype=np.float32)
        x = np.stack([p.get_array() for p in self.ins])
        if self.d or d1:
            k = 1 + (DRIVE_MAX - 1) * d
            fuzz = np.tanh(k * x + BIAS) - np.tanh(BIAS)
            x = (1 - d) * x + d * FUZZ_LEVEL * fuzz
        x, self.zi = lfilter([1, -1], [1, -DC_POLE], x, axis=1, zi=self.zi)
        self.g, self.d = g1, d1
        return x * g


class Sidechain:
    """Gain curve for the ducked strips, from the key strip's output."""

    def __init__(self, sr, amount):
        self.amount = amount
        self.b, self.a = butter(2, SIDECHAIN_LPF, fs=sr)
        self.zi = np.zeros(2)
        self.r = np.exp(-1 / (SIDECHAIN_RELEASE * sr))
        self.env = 0.0
        self.p = np.exp(-1 / (SIDECHAIN_SMOOTH * sr))
        self.szi = np.array([self.p])   # smoother starts settled at unity gain
        self.decay = {}   # block size -> (r^k, r^-k), k = 1..n
        self.min_gain = 1.0   # deepest duck since the state was last written

    def gain(self, key):
        n = key.shape[1]
        if n not in self.decay:
            k = np.arange(1, n + 1)
            self.decay[n] = (self.r ** k, self.r ** -k)
        down, up = self.decay[n]
        low, self.zi = lfilter(self.b, self.a, key.mean(0), zi=self.zi)
        # Peak envelope with instant attack and exponential release, vectorised:
        # env[n] = r^n * max(env_in, max over k<=n of |x[k]| * r^-k).
        env = down * np.maximum.accumulate(np.maximum(np.abs(low) * up, self.env))
        self.env = float(env[-1])
        g = 1 - self.amount * np.minimum(env / SIDECHAIN_FULL, 1.0)
        g, self.szi = lfilter([1 - self.p], [1, -self.p], g, zi=self.szi)
        self.min_gain = min(self.min_gain, float(g.min()))
        return g


def load_faders():
    try:
        return json.loads(SAVED.read_text())
    except (OSError, ValueError):
        return {}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


class Limiter:
    """Block-wise master limiter: gain falls at once to keep both the peak and the
    sustained (RMS) level under their ceilings, and recovers over LIMIT_RELEASE.
    A hard clip at the ceiling catches what the in-block ramp lets past."""

    def __init__(self, rate):
        self.rate, self.gain, self.ms = rate, 1.0, 0.0
        self.lowest = 1.0                 # the lowest gain since the meter last read it

    def process(self, x):
        n = x.shape[1]
        a = math.exp(-n / (LIMIT_RMS_WINDOW * self.rate))
        self.ms = a * self.ms + (1 - a) * float((x ** 2).mean())
        target = min(1.0, LIMIT_PEAK / (float(np.abs(x).max()) + 1e-12),
                     LIMIT_RMS / (math.sqrt(self.ms) + 1e-12))
        if target >= self.gain:           # recover
            target += (self.gain - target) * math.exp(-n / (LIMIT_RELEASE * self.rate))
        ramp = np.linspace(self.gain, target, n)
        self.gain = target
        self.lowest = min(self.lowest, target)
        return np.clip(x * ramp, -LIMIT_PEAK, LIMIT_PEAK)


def main():
    client = jack.Client("m800Dr-Mixer", no_start_server=True)
    saved = load_faders()
    strips = [Strip(client, n, ch, f, saved.get(n, KNEE)) for n, ch, f in STRIPS]
    sidechain = Sidechain(client.samplerate, saved.get("_sidechain", SIDECHAIN_DEFAULT))
    key = next(s for s in strips if s.name == SIDECHAIN_KEY)
    ducked = [s for s in strips if s.name in SIDECHAIN_DUCKED]
    by_channel = {s.channel - 1: s for s in strips}
    by_fader = {s.number - 1: s for s in strips}
    midi = client.midi_inports.register("faders")
    controls = client.midi_outports.register("controls")
    # Non-fader CCs from the UDP control thread, written out by process().
    pending = collections.deque(maxlen=256)
    outs = [client.outports.register(f"out_{s}") for s in "LR"]
    by_name = {s.name: s for s in strips}
    last_cc = [None]   # last unmapped CC, shown on the page to help map a new controller

    def on_cc(data):
        if len(data) == 3 and data[0] & 0xF0 == 0xB0:
            ch, cc, val = data[0] & 0x0F, data[1], data[2]
            if cc == SIDECHAIN_CC:
                sidechain.amount = min(max(val, 0), 127) / 127
                return True
            if cc == FADER_CC:
                s = by_channel.get(ch)
            elif cc in FADER_CCS:
                s = by_fader.get(FADER_CCS.index(cc))
            else:
                s = None
            if s:
                s.fader = min(max(val, 0), 127) / 127
                s.ceiling = s.fader   # a hand on the fader
                return True
            last_cc[0] = (ch + 1, cc, val)
        return False

    def control_loop():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(CONTROL)
        while True:
            try:
                msg = json.loads(sock.recv(4096))
                if "cc" in msg:
                    data = bytes(int(b) & 0xFF for b in msg["cc"])
                    if not on_cc(data):
                        pending.append(data)
                elif "sidechain" in msg:
                    sidechain.amount = min(max(float(msg["sidechain"]), 0.0), 1.0)
                elif msg.get("strip") in by_name:
                    strip = by_name[msg["strip"]]
                    level = min(max(float(msg["fader"]), 0.0), 1.0)
                    if msg.get("by"):    # m00Drc's arranger or producer: under the hand's ceiling
                        strip.fader = level if strip.ceiling is None else min(level, strip.ceiling)
                    else:                # the /mixer page or the Mac's controller: a hand
                        strip.fader = strip.ceiling = level
            except (ValueError, TypeError, KeyError):
                pass

    threading.Thread(target=control_loop, daemon=True).start()

    limiter = Limiter(client.samplerate)

    @client.set_process_callback
    def process(n):
        controls.clear_buffer()
        for offset, data in midi.incoming_midi_events():
            data = bytes(data)   # a cffi buffer, whose items are 1-byte bytes, not ints
            if not on_cc(data) and len(data) == 3 and data[0] & 0xF0 == 0xB0:
                controls.write_midi_event(offset, data)
        while pending:
            controls.write_midi_event(0, pending.popleft())
        out = {s: s.process(n) for s in strips}
        duck = sidechain.gain(out[key])
        for s in ducked:
            out[s] = out[s] * duck
        for s, y in out.items():
            s.peak = max(s.peak, float(np.abs(y).max()))
        mix = np.tanh(sum(out.values()))   # bus saturation: transparent when quiet, rounds off overs
        mix = limiter.process(mix)          # the last stage: nothing but this file sets it
        for port, ch in zip(outs, mix):
            port.get_array()[:] = ch

    with client:
        print(f"m800Dr-Mixer up: {client.samplerate} Hz, strips "
              + ", ".join(f"{s.name}=ch{s.channel}/f{s.number}" for s in strips), flush=True)
        saved_at = {s.name: s.fader for s in strips} | {"_sidechain": sidechain.amount}
        while True:
            time.sleep(0.1)
            duck, sidechain.min_gain = sidechain.min_gain, 1.0
            state = {"knee": KNEE, "cc": FADER_CC, "last_cc": last_cc[0], "time": time.time(),
                     "sidechain": sidechain.amount, "sidechain_cc": SIDECHAIN_CC,
                     "sidechain_key": SIDECHAIN_KEY, "strips": [],
                     "limiter_db": round(20 * math.log10(max(limiter.lowest, 1e-6)), 1)}
            limiter.lowest = 1.0
            for s in strips:
                peak, s.peak = s.peak, 0.0
                state["strips"].append({"name": s.name, "channel": s.channel, "number": s.number,
                                        "fader": s.fader, "ceiling": s.ceiling,
                                        "gain": gain(s.fader), "drive": drive(s.fader),
                                        "peak": peak,
                                        "duck": duck if s in ducked else None})
            write_json(LIVE, state)
            faders = {s.name: s.fader for s in strips} | {"_sidechain": sidechain.amount}
            if faders != saved_at:
                write_json(SAVED, faders)
                saved_at = faders


if __name__ == "__main__":
    main()
