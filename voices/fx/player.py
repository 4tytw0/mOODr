# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy", "soundfile", "alsa-midi"]
# ///
"""FX on fader 4: noise risers, downlifters, an impact, an atmosphere, and your
own foley/atmos samples, played on m00Dr's clock.

Listens on ALSA sequencer client "m00Dr-FX" (MIDI channel FX_CHANNEL; m00Dr's
clock arrives on the same port, from its note output) and plays through pw-cat
into the m800Dr Mixer's fx strip, like breaks.py. m00Drc's roller sends the
notes: a riser into each drop, the atmosphere through a breakdown, a
downlifter into the next Roll.

Notes (FX_CHANNEL):
  60 / 61 / 62  noise riser over 2 / 4 / 8 bars at the clock's tempo: band-passed
                noise swept from RISER_SWEEP[0] up to [1] Hz, swelling, cut at its end
  64 / 65       downlifter over 2 / 4 bars: the riser backwards, fading out
  67            impact: a sub boom falling in pitch, and a short noise burst
  72            atmosphere, while held: low wind (filtered noise, drifting), faded
                in on note-on and out on note-off over ATMOS_FADE seconds
  96 and up     live samples: slot note-96, LIVE/NN.wav described in LIVE/index.json
                ({"NN": {"name": ..., "loop": bool}}), read on note-on; m00Drc's
                producer puts library samples there (tools/prep_sample.py)
  84 and up     samples from synths/fx.json ({"samples": [{"note": 84, "file":
                "rain.wav", "loop": false}]}, files in synths/fx/, 48 kHz): one-shots,
                or loops while held
Velocity scales each sound's level. CC 123 (all notes off) fades everything out.

    uv run fx.py                  # run (the m00dr-fx user service does this)
    uv run fx.py --demo x.wav     # render a riser into an impact, the atmosphere, a downlifter
"""
import fcntl, json, subprocess, sys, threading
from pathlib import Path
import numpy as np, soundfile as sf
from scipy.signal import lfilter

SR, BLOCK = 48000, 256
FX_CHANNEL = 9                            # 0-based: MIDI ch10 (ch9 is the Launch Control XL's)
HERE = Path(__file__).resolve().parent
INDEX = HERE / "synths" / "fx.json"
LIVE, LIVE_NOTE = Path.home() / ".local/state/m00drc/live-fx", 96
CLOCKS_PER_16TH = 6
RISER_NOTES = {60: 2, 61: 4, 62: 8}       # note -> bars
DOWN_NOTES = {64: 2, 65: 4}
IMPACT_NOTE, ATMOS_NOTE, SAMPLE_BASE = 67, 72, 84
RISER_SWEEP = (300.0, 8000.0)             # Hz, band-pass centre from start to end
RISER_Q = 1.6
RISER_LEVEL = 0.3                         # peak, before velocity
ATMOS_LEVEL, ATMOS_FADE = 0.22, 2.0
FADE = 240                                # samples (5 ms) at a cut


def bandpass(f, q):
    """RBJ band-pass (constant 0 dB peak) coefficients at centre f."""
    w = 2 * np.pi * min(f, SR * 0.45) / SR
    alpha = np.sin(w) / (2 * q)
    b = np.array([alpha, 0.0, -alpha]); a = np.array([1 + alpha, -2 * np.cos(w), 1 - alpha])
    return b / a[0], a / a[0]


def lowpass(f, q=0.707):
    w = 2 * np.pi * min(f, SR * 0.45) / SR
    alpha = np.sin(w) / (2 * q); c = np.cos(w)
    b = np.array([(1 - c) / 2, 1 - c, (1 - c) / 2]); a = np.array([1 + alpha, -2 * c, 1 - alpha])
    return b / a[0], a / a[0]


def impact():
    """The impact, rendered once: a boom from 110 down to 38 Hz, and a noise burst."""
    t = np.arange(int(SR * 1.6)) / SR
    freq = 38 + 72 * np.exp(-t / 0.08)
    boom = np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t / 0.45)
    b, a = lowpass(2500)
    burst = lfilter(b, a, np.random.default_rng(7).standard_normal(len(t))) * np.exp(-t / 0.06) * 0.6
    y = np.tanh(1.4 * (boom + burst)) * 0.8
    return np.stack([y, y], axis=1).astype(np.float32)


class Sweep:
    """A riser (up) or downlifter (down): band-passed stereo noise over `length` samples."""
    def __init__(self, length, up, gain, rng):
        self.length, self.up, self.gain, self.pos, self.rng = length, up, gain, 0, rng
        self.zi = [np.zeros(2), np.zeros(2)]
        self.done = False

    def render(self, n):
        t = min(1.0, self.pos / self.length)
        p = t if self.up else 1 - t
        f = RISER_SWEEP[0] * (RISER_SWEEP[1] / RISER_SWEEP[0]) ** p   # exponential sweep
        amp = (p ** 2 if self.up else (1 - t) ** 1.5) * self.gain * RISER_LEVEL
        b, a = bandpass(f, RISER_Q)
        out = np.empty((n, 2), dtype=np.float32)
        for ch in range(2):
            y, self.zi[ch] = lfilter(b, a, self.rng.standard_normal(n), zi=self.zi[ch])
            out[:, ch] = y * amp * 1.6          # make-up for the band-pass, short of clipping its peaks
        left = self.length - self.pos
        if left < n:                            # cut at the end, with a short fade
            ramp = np.clip((left - np.arange(n)) / FADE, 0, 1)
            out *= ramp[:, None]
            self.done = True
        self.pos += n
        return out


class Atmos:
    """Low wind: noise through a drifting low-pass, faded in and out."""
    def __init__(self, rng):
        self.rng, self.level, self.target, self.gain, self.phase = rng, 0.0, 0.0, 1.0, 0.0
        self.zi = [np.zeros(2), np.zeros(2)]

    def render(self, n):
        if self.level == 0.0 and self.target == 0.0:
            return None
        self.phase += n / SR
        f = 500 + 350 * np.sin(2 * np.pi * self.phase / 7.0) + 150 * np.sin(2 * np.pi * self.phase / 2.3)
        b, a = lowpass(f, 1.2)
        out = np.empty((n, 2), dtype=np.float32)
        for ch in range(2):
            y, self.zi[ch] = lfilter(b, a, self.rng.standard_normal(n), zi=self.zi[ch])
            out[:, ch] = y
        step = n / (SR * ATMOS_FADE)
        start = self.level
        self.level = min(self.target, start + step) if self.target > start else max(self.target, start - step)
        env = np.linspace(start, self.level, n, dtype=np.float32)
        swell = 0.8 + 0.2 * np.sin(2 * np.pi * self.phase / 11.0)
        return out * (env * ATMOS_LEVEL * self.gain * swell * 2.0)[:, None]


class FX:
    def __init__(self):
        self.lock = threading.Lock()
        self.rng = np.random.default_rng()
        self.sixteenth = SR * 0.125           # output samples per 16th, from the clock (120 bpm until known)
        self.pulse_times: list[int] = []
        self.now = 0
        self.sweeps: list[Sweep] = []
        self.voices = []                      # [audio, position, gain, loop, note, fading countdown or None]
        self.atmos = Atmos(self.rng)
        self.impact = impact()
        self.samples = {}
        if INDEX.exists():
            for entry in json.loads(INDEX.read_text()).get("samples", []):
                audio, rate = sf.read(INDEX.parent / "fx" / entry["file"], dtype="float32", always_2d=True)
                assert rate == SR, f"{entry['file']} is {rate} Hz"
                audio = audio if audio.shape[1] == 2 else np.repeat(audio[:, :1], 2, axis=1)
                self.samples[entry["note"]] = (audio, bool(entry.get("loop")))
        self.buf = np.zeros((BLOCK, 2), dtype="<f4")

    # MIDI thread ------------------------------------------------------------
    def clock(self):
        with self.lock:
            self.pulse_times = (self.pulse_times + [self.now])[-25:]
            if len(self.pulse_times) == 25 and self.pulse_times[-1] > self.pulse_times[0]:
                self.sixteenth = (self.pulse_times[-1] - self.pulse_times[0]) / 4

    def _load_live(self, slot):
        try:
            entry = json.loads((LIVE / "index.json").read_text())[f"{slot:02d}"]
            audio, rate = sf.read(LIVE / f"{slot:02d}.wav", dtype="float32", always_2d=True)
        except (OSError, ValueError, KeyError, RuntimeError) as e:
            print(f"live sample {slot}: {e}", flush=True)
            return None
        if rate != SR:
            print(f"live sample {slot} is {rate} Hz", flush=True)
            return None
        audio = audio if audio.shape[1] == 2 else np.repeat(audio[:, :1], 2, axis=1)
        print(f"live sample {slot}: {entry.get('name', '?')}{' (loop)' if entry.get('loop') else ''}", flush=True)
        return audio, bool(entry.get("loop"))

    def note_on(self, note, velocity):
        gain = velocity / 127
        live = self._load_live(note - LIVE_NOTE) if note >= LIVE_NOTE else None   # outside the lock
        with self.lock:
            bars = RISER_NOTES.get(note) or DOWN_NOTES.get(note)
            if bars:
                length = int(bars * 16 * self.sixteenth)
                self.sweeps.append(Sweep(length, note in RISER_NOTES, gain, self.rng))
                print(f"{'riser' if note in RISER_NOTES else 'downlifter'}: {bars} bars ({length / SR:.1f}s)", flush=True)
            elif note == IMPACT_NOTE:
                self.voices.append([self.impact, 0, gain, False, note, None])
            elif note == ATMOS_NOTE:
                self.atmos.target, self.atmos.gain = 1.0, gain
            elif live is not None:
                self.voices.append([live[0], 0, gain, live[1], note, None])
            elif note in self.samples:
                audio, loop = self.samples[note]
                self.voices.append([audio, 0, gain, loop, note, None])

    def note_off(self, note):
        with self.lock:
            if note == ATMOS_NOTE:
                self.atmos.target = 0.0
            for v in self.voices:
                if v[4] == note and v[3] and v[5] is None:
                    v[5] = FADE * 20              # a held loop fades over 100 ms

    def all_off(self):
        with self.lock:
            self.atmos.target = 0.0
            for s in self.sweeps:
                s.length = min(s.length, s.pos + FADE)
            for v in self.voices:
                v[5] = v[5] if v[5] is not None else FADE * 20

    # Audio thread -------------------------------------------------------------
    def render(self):
        with self.lock:
            out = np.zeros((BLOCK, 2), dtype=np.float32)
            for s in self.sweeps:
                out += s.render(BLOCK)
            self.sweeps = [s for s in self.sweeps if not s.done]
            a = self.atmos.render(BLOCK)
            if a is not None:
                out += a
            keep = []
            for v in self.voices:
                audio, pos, gain, loop, note, fading = v
                idx = np.arange(pos, pos + BLOCK)
                if loop:
                    idx %= len(audio)
                n = BLOCK if loop else max(0, min(BLOCK, len(audio) - pos))
                chunk = audio[idx[:n]] * gain
                if fading is not None:
                    chunk = chunk * np.clip((fading - np.arange(n)) / (FADE * 20), 0, 1)[:, None]
                    v[5] = fading - n
                out[:n] += chunk
                v[1] = pos + n
                if (loop or v[1] < len(audio)) and (v[5] is None or v[5] > 0):
                    keep.append(v)
            self.voices = keep
            self.now += BLOCK
            np.tanh(out, out=out)             # soft clip: rounds a sweep's peaks, leaves quiet sounds alone
            self.buf[:] = out
        return self.buf


def open_midi():
    from alsa_midi import SequencerClient, WRITE_PORT, PortType
    client = SequencerClient("m00Dr-FX")
    # Keep a reference: alsa-midi deletes the port when its object is garbage-collected.
    client.fx_in = client.create_port("fx in", caps=WRITE_PORT, type=PortType.MIDI_GENERIC | PortType.APPLICATION)
    print(f"MIDI in: ALSA client {client.client_id} 'm00Dr-FX', port 0 'fx in'", flush=True)
    return client


def midi_loop(client, fx):
    from alsa_midi import ClockEvent, ControlChangeEvent, NoteOffEvent, NoteOnEvent
    while True:
        ev = client.event_input()
        if isinstance(ev, ClockEvent): fx.clock(); continue
        if getattr(ev, "channel", None) != FX_CHANNEL: continue
        if isinstance(ev, NoteOnEvent):
            fx.note_on(ev.note, ev.velocity) if ev.velocity else fx.note_off(ev.note)
        elif isinstance(ev, NoteOffEvent): fx.note_off(ev.note)
        elif isinstance(ev, ControlChangeEvent) and ev.param in (120, 123): fx.all_off()


def demo(path):
    """At 120 bpm: a 2-bar riser into an impact, 4 bars of atmosphere, a 2-bar downlifter."""
    fx, blocks = FX(), []
    def run(seconds):
        for _ in range(int(seconds * SR / BLOCK)):
            blocks.append(fx.render().copy())
    fx.note_on(60, 127); run(4.0)
    fx.note_on(IMPACT_NOTE, 127); fx.note_on(ATMOS_NOTE, 110); run(8.0)
    fx.note_off(ATMOS_NOTE); fx.note_on(64, 127); run(4.5)
    y = np.concatenate(blocks)
    sf.write(path, y, SR, subtype="PCM_16")
    seg = lambda a, b: 20 * np.log10(max(np.sqrt(np.mean(y[int(a * SR):int(b * SR)] ** 2)), 1e-9))
    print(f"peak {np.abs(y).max():.2f}; riser rms by second: "
          + " ".join(f"{seg(s, s + 1):.0f}" for s in range(4)) + " dB; impact+atmos "
          + f"{seg(4, 5):.0f} dB, atmos alone {seg(9, 12):.0f} dB, downlifter {seg(12, 13):.0f} -> {seg(15, 16):.0f} dB")


def main():
    fx = FX()
    threading.Thread(target=midi_loop, args=(open_midi(), fx), daemon=True).start()
    # --target 0: left unlinked; synths/links.sh wires it into the mixer's fx strip.
    p = subprocess.Popen(["pw-cat", "--playback", "--raw", "--format=f32", f"--rate={SR}", "--channels=2",
                          "--target=0", f"--latency={BLOCK}",
                          "-P", "{ node.name=m00Dr-FX media.name=\"fx\" }", "-"], stdin=subprocess.PIPE)
    fcntl.fcntl(p.stdin.fileno(), fcntl.F_SETPIPE_SZ, 4096)   # keep the pipe from adding ~170 ms of latency
    while True:
        p.stdin.write(fx.render().tobytes())                  # blocks at real-time pace


if __name__ == "__main__":
    if sys.argv[1:2] == ["--demo"]:
        demo(sys.argv[2])
    else:
        main()
