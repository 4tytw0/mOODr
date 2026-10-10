# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy", "alsa-midi"]
# ///
"""m00Dr's bass: a mono reese synth.

Two detuned saws beating against each other (spread a little across L/R so the
scope shows the movement), a sine sub, a low-pass filter with an envelope and a
slow LFO, then soft-clip drive. Listens on ALSA sequencer client "m00Dr-Reese"
(MIDI channel BASS_CHANNEL only) and plays into the m800dr_mix sink via pacat.

    uv run reese.py                # run (the m00dr-reese user service does this)
    uv run reese.py --demo x.wav   # render a short riff offline, for checking levels
"""
import fcntl, subprocess, sys, threading
import numpy as np
from scipy.signal import lfilter

SR, BLOCK = 48000, 256
BASS_CHANNEL = 1                          # 0-based: m00Dr's bass part is MIDI ch2
SINK = "m800dr_mix"

DETUNE = 0.0075     # each saw +/- this ratio (~13 cents): the reese beating
SPREAD = 0.3        # how far the two saws are panned apart (0 = mono)
SUB = 0.7           # sine sub level, kept mono
CUTOFF = 260.0      # Hz, low-pass base cutoff
ENV_AMT = 1400.0    # Hz added by the filter envelope at full velocity
ENV_DECAY = 0.25    # s
LFO_HZ, LFO_OCT = 0.18, 0.6   # slow filter wobble, in octaves
Q = 1.4
DRIVE = 2.5
GLIDE = 0.035       # s, legato portamento
ATTACK, RELEASE = 0.004, 0.12
GAIN = 0.45


class Reese:
    def __init__(self):
        self.lock = threading.Lock()
        self.stack = []            # held notes, last = sounding
        self.freq = self.target = 55.0
        self.gate = False; self.vel = 0.0
        self.env = 0.0; self.fenv = 0.0
        self.ph = np.zeros(3)      # saw 1, saw 2, sub
        self.lfo = 0.0
        self.zi = np.zeros((2, 2))

    def note_on(self, note, vel):
        with self.lock:
            if note in self.stack: self.stack.remove(note)
            if not self.stack: self.freq = 440 * 2 ** ((note - 69) / 12)  # no glide from silence
            self.stack.append(note)
            self.target = 440 * 2 ** ((note - 69) / 12)
            self.gate = True; self.vel = vel / 127; self.fenv = 1.0

    def note_off(self, note):
        with self.lock:
            if note in self.stack: self.stack.remove(note)
            if self.stack: self.target = 440 * 2 ** ((self.stack[-1] - 69) / 12)
            else: self.gate = False

    def all_off(self):
        with self.lock: self.stack.clear(); self.gate = False

    def render(self, n=BLOCK):
        with self.lock:
            gate, vel, target = self.gate, self.vel, self.target
        k = np.arange(1, n + 1)
        f = target + (self.freq - target) * np.exp(-k / (GLIDE * SR)); self.freq = f[-1]
        tau = ATTACK if gate else RELEASE
        env = float(gate) + (self.env - float(gate)) * np.exp(-k / (tau * SR)); self.env = env[-1]
        fenv = self.fenv * np.exp(-k / (ENV_DECAY * SR)); self.fenv = fenv[-1]

        def osc(i, ratio):
            inc = f * ratio / SR
            t = (self.ph[i] + np.cumsum(inc)) % 1.0; self.ph[i] = t[-1]
            return t, inc
        out = []
        for i, r in ((0, 1 - DETUNE), (1, 1 + DETUNE)):
            t, dt = osc(i, r)
            y = 2 * t - 1                                  # saw, with polyBLEP anti-aliasing
            m = t < dt; x = t[m] / dt[m]; y[m] -= x + x - x * x - 1
            m = t > 1 - dt; x = (t[m] - 1) / dt[m]; y[m] -= x * x + x + x + 1
            out.append(y)
        t, _ = osc(2, 1.0); sub = np.sin(2 * np.pi * t)
        a, b = out
        lr = np.stack([(0.5 + SPREAD / 2) * a + (0.5 - SPREAD / 2) * b,
                       (0.5 - SPREAD / 2) * a + (0.5 + SPREAD / 2) * b])

        self.lfo = (self.lfo + LFO_HZ * n / SR) % 1.0
        fc = CUTOFF * 2 ** (LFO_OCT * np.sin(2 * np.pi * self.lfo)) + ENV_AMT * vel * fenv.mean()
        w = 2 * np.pi * min(fc, 0.45 * SR) / SR; al = np.sin(w) / (2 * Q); c = np.cos(w)
        bq = np.array([(1 - c) / 2, 1 - c, (1 - c) / 2]); aq = np.array([1 + al, -2 * c, 1 - al])
        lr, self.zi = lfilter(bq / aq[0], aq / aq[0], lr, axis=1, zi=self.zi)

        y = np.tanh(DRIVE * (lr + SUB * sub)) / np.tanh(DRIVE)
        return (y * env * (0.6 + 0.4 * vel) * GAIN).T.astype("<f4")


def open_midi():
    from alsa_midi import SequencerClient, WRITE_PORT, PortType
    client = SequencerClient("m00Dr-Reese")
    # Keep a reference: alsa-midi deletes the port when its object is garbage-collected.
    client.bass_in = client.create_port("bass in", caps=WRITE_PORT, type=PortType.MIDI_GENERIC | PortType.APPLICATION)
    print(f"MIDI in: ALSA client {client.client_id} 'm00Dr-Reese', port 0 'bass in'", flush=True)
    return client


def midi_loop(client, synth):
    from alsa_midi import NoteOnEvent, NoteOffEvent, ControlChangeEvent
    while True:
        ev = client.event_input()
        if getattr(ev, "channel", None) != BASS_CHANNEL: continue
        if isinstance(ev, NoteOnEvent):
            synth.note_on(ev.note, ev.velocity) if ev.velocity else synth.note_off(ev.note)
        elif isinstance(ev, NoteOffEvent): synth.note_off(ev.note)
        elif isinstance(ev, ControlChangeEvent) and ev.param in (120, 123): synth.all_off()


def demo(path):
    import wave
    s = Reese(); blocks = []
    riff = [(33, 0.5), (33, 0.25), (36, 0.25), (31, 0.5), (28, 1.0)]   # A1 A1 C2 G1 E1
    for note, beats in riff:
        s.note_on(note, 110)
        for i in range(int(beats * 0.5 * SR / BLOCK)):
            if i == int(beats * 0.45 * SR / BLOCK): s.note_off(note)
            blocks.append(s.render())
    for _ in range(int(0.3 * SR / BLOCK)): blocks.append(s.render())
    pcm = (np.clip(np.concatenate(blocks), -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
    print(f"peak {np.abs(np.concatenate(blocks)).max():.2f}")


def main():
    synth = Reese()
    threading.Thread(target=midi_loop, args=(open_midi(), synth), daemon=True).start()
    p = subprocess.Popen(["pacat", "--raw", "--format=float32le", f"--rate={SR}", "--channels=2",
                          f"--device={SINK}", "--latency-msec=15", "--client-name=m00Dr-Reese",
                          "--stream-name=reese bass"], stdin=subprocess.PIPE)
    fcntl.fcntl(p.stdin.fileno(), fcntl.F_SETPIPE_SZ, 4096)   # keep the pipe from adding ~170 ms of latency
    while True:
        p.stdin.write(synth.render().tobytes())               # blocks at real-time pace


if __name__ == "__main__":
    demo(sys.argv[2]) if sys.argv[1:2] == ["--demo"] else main()
