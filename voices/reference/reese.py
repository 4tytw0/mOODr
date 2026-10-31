# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy", "alsa-midi"]
# ///
"""m00Dr's bass: a mono reese synth.

Two detuned saws beating against each other (spread a little across L/R so the
scope shows the movement), a sine sub, a low-pass filter with an envelope and a
slow LFO, then soft-clip drive. Listens on ALSA sequencer client "m00Dr-Reese"
(MIDI channel BASS_CHANNEL) and plays through pw-cat into the m800Dr Mixer.

The beat between the saws follows m00Dr's chords (CHORD_CHANNEL, which the
same port receives): each chord's sensory roughness, from the spectrum of its
notes (harmonic partials, Sethares' model of Plomp-Levelt dissonance), sets
how fast they beat. Stable chords pulse slowly (BEAT_MIN Hz), dissonant or low,
muddy ones beat fast (up to BEAT_MAX Hz), gliding over BEAT_GLIDE as the
progression moves.

    uv run synths/reference/reese.py   # run (no longer a service: kept as the reference for its BPM-locked wobble)
    uv run synths/reference/reese.py --demo x.wav   # render a short riff offline, for checking levels
"""
import fcntl, subprocess, sys, threading
import numpy as np
from scipy.signal import lfilter

SR, BLOCK = 48000, 256
BASS_CHANNEL = 1                          # 0-based: m00Dr's bass part is MIDI ch2
CHORD_CHANNEL = 0                         # m00Dr's chords, MIDI ch1: they set the beat

BEAT_MIN, BEAT_MAX = 0.3, 6.0   # Hz between the two saws, for the most and least stable chords
BEAT_DEFAULT = 1.0              # before any chord has played
ROUGH_LO, ROUGH_HI = 0.3, 0.65  # roughness mapped onto BEAT_MIN..BEAT_MAX (exponentially): a high
                                # triad ~0.3, low or diminished ~0.55, clusters past 0.65
BEAT_GLIDE = 0.25               # s, how fast the beat follows a chord change
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
        self.chord_held = set(); self.chord = ()
        self.beat = self.beat_target = BEAT_DEFAULT

    def chord_note(self, note, on):
        """Tracks the chord channel. A note-on with nothing held starts a new
        chord; later ones join it, so the beat is set once per chord (stabs
        retrigger the same chord and leave it unchanged)."""
        with self.lock:
            if not on:
                self.chord_held.discard(note); return
            chord = (note,) if not self.chord_held else tuple(sorted(set(self.chord) | {note}))
            self.chord_held.add(note)
            if chord != self.chord:
                self.chord = chord
                self.beat_target = beat_for(chord)
                print(f"chord {list(chord)}: beat {self.beat_target:.2f} Hz", flush=True)

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
            gate, vel, target, beat_target = self.gate, self.vel, self.target, self.beat_target
        k = np.arange(1, n + 1)
        beat = beat_target + (self.beat - beat_target) * np.exp(-k / (BEAT_GLIDE * SR)); self.beat = beat[-1]
        f = target + (self.freq - target) * np.exp(-k / (GLIDE * SR)); self.freq = f[-1]
        tau = ATTACK if gate else RELEASE
        env = float(gate) + (self.env - float(gate)) * np.exp(-k / (tau * SR)); self.env = env[-1]
        fenv = self.fenv * np.exp(-k / (ENV_DECAY * SR)); self.fenv = fenv[-1]

        def osc(i, freq):
            inc = freq / SR
            t = (self.ph[i] + np.cumsum(inc)) % 1.0; self.ph[i] = t[-1]
            return t, inc
        out = []
        for i, sign in ((0, -1), (1, 1)):              # beat/2 Hz either side: they beat at `beat` Hz
            t, dt = osc(i, f + sign * beat / 2)
            y = 2 * t - 1                                  # saw, with polyBLEP anti-aliasing
            m = t < dt; x = t[m] / dt[m]; y[m] -= x + x - x * x - 1
            m = t > 1 - dt; x = (t[m] - 1) / dt[m]; y[m] -= x * x + x + x + 1
            out.append(y)
        t, _ = osc(2, f); sub = np.sin(2 * np.pi * t)
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


def roughness(notes, harmonics=8, rolloff=0.88):
    """Sensory roughness of notes sounded together, per pair of notes: Sethares'
    fit of Plomp-Levelt dissonance summed over every pair of partials from
    different notes (each note a harmonic series falling off like a saw)."""
    if len(notes) < 2:
        return 0.0
    k = np.arange(1, harmonics + 1)
    f = np.concatenate([440 * 2 ** ((n - 69) / 12) * k for n in notes])
    a = np.tile(rolloff ** (k - 1), len(notes)); who = np.repeat(np.arange(len(notes)), harmonics)
    i, j = np.triu_indices(len(f), 1); keep = who[i] != who[j]; i, j = i[keep], j[keep]
    s = 0.24 / (0.0207 * np.minimum(f[i], f[j]) + 18.96)
    df = np.abs(f[i] - f[j])
    d = np.minimum(a[i], a[j]) * (np.exp(-3.51 * s * df) - np.exp(-5.75 * s * df))
    return float(d.sum() / (len(notes) * (len(notes) - 1) / 2))


def beat_for(chord):
    """The saws' beat in Hz for a chord: exponential in its roughness, so each
    step of dissonance speeds the pulse by the same factor."""
    x = min(max((roughness(chord) - ROUGH_LO) / (ROUGH_HI - ROUGH_LO), 0.0), 1.0)
    return BEAT_MIN * (BEAT_MAX / BEAT_MIN) ** x


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
        channel = getattr(ev, "channel", None)
        if channel == CHORD_CHANNEL:
            if isinstance(ev, NoteOnEvent): synth.chord_note(ev.note, ev.velocity > 0)
            elif isinstance(ev, NoteOffEvent): synth.chord_note(ev.note, False)
            continue
        if channel != BASS_CHANNEL: continue
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
    # --target 0: left unlinked; synths/links.sh wires it into the mixer's bass strip.
    p = subprocess.Popen(["pw-cat", "--playback", "--raw", "--format=f32", f"--rate={SR}", "--channels=2",
                          "--target=0", f"--latency={BLOCK}",
                          "-P", "{ node.name=m00Dr-Reese media.name=\"reese bass\" }", "-"], stdin=subprocess.PIPE)
    fcntl.fcntl(p.stdin.fileno(), fcntl.F_SETPIPE_SZ, 4096)   # keep the pipe from adding ~170 ms of latency
    while True:
        p.stdin.write(synth.render().tobytes())               # blocks at real-time pace


if __name__ == "__main__":
    demo(sys.argv[2]) if sys.argv[1:2] == ["--demo"] else main()
