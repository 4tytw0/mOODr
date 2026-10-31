# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "alsa-midi"]
# ///
"""m00Dr's bass on SunVox: a reese-like saw pair and a sine sub, mixed into a fuzz.

The patch is fuzzbass.sunvox beside this file (built by `uv run
voices/bass/build.py`): a saw with its second oscillator detuned ~12 cents (the
reese's beat) and a sine an octave below, both into hard-clipping fuzz, then
a low-pass. Listens on ALSA sequencer client "m00Dr-SunVoxBass" (MIDI channel
BASS_CHANNEL, m00Dr's bass part) and plays through pw-cat into the m800Dr
Mixer's bass strip, in place of reese.py.

The saws' beat pulses with m00Dr's tempo: the two saws beat at the difference
of their frequencies, so for each note the second's detune is set to make
that difference give one swell per beat at the tempo of m00Dr's MIDI clock (which
arrives on the same port). A note-on restarts both saws in phase, so the
swell starts on the note. Without a clock it keeps the last tempo heard.

Mono, last-note priority: a new note retriggers, and releasing it falls back
to a note still held.

Two CCs on the bass channel, sent by m00Drc's arranger:
  * WOBBLE_CC picks the swell rate from WOBBLES (swells per beat; 64 is the
    default one per beat), applied to the note sounding at once.
  * GATE_CC picks a gate pattern from GATES: 16 bits, the first 16th on top,
    one bar of the clock. On a 16th whose bit is set the held note re-attacks;
    on one that isn't it's silent. 0 is no gate: the bass plays legato.
  * GATE_BITS_CCS carry a gate pattern of the sender's own, 6 + 5 + 5 bits
    (bits 15-10, 9-5, 4-0), applied when the last of the three arrives.
  * WOBBLE_RATE_CC sets the swell rate directly: 2 ** (value / 16) / 8 swells
    per beat (0 = 1/8, 48 = 1, 96 and up = WOBBLE_MAX).
  CC 121 (reset all controllers) puts both back.

    uv run sunvoxbass.py                 # run (the m00dr-sunvoxbass user service does this)
    uv run sunvoxbass.py --demo x.wav    # render a short line offline, for listening
"""
import fcntl, math, subprocess, sys, threading
import numpy as np
import pathlib as _pathlib, sys as _sys
VOICE = _pathlib.Path(__file__).resolve().parent   # this voice's folder: its patches live here
_sys.path.insert(0, str(VOICE.parent / "_sunvox"))
from sunvoxlib import HERE, NOTECMD_ALL_NOTES_OFF, NOTECMD_NOTE_OFF, ClockFollower, find_module, load, open_lib

SR, BLOCK = 48000, 256
BASS_CHANNEL = 1                          # 0-based: m00Dr's bass part is MIDI ch2
PROJECT = VOICE / "fuzzbass.sunvox"
VOLUME = 256                              # sv_volume, 0..256
# Module -> MIDI note to library note. Measured: played directly (no Glide in
# front, unlike the acid), both generators sound MIDI n at library note n - 11.
# The sub sits at the saws' pitch, as reese.py's does.
VOICES = {"Saws": -11, "Sub": -11}
# The saws' beat, in beat cycles per beat of m00Dr's tempo. Through the fuzz a
# beat cycle swells twice (measured: the tone brightens both when the saws line
# up and clip hardest and when they're opposite and sound an octave up), so
# half a cycle per beat is one swell per beat. Osc2's detune is in 1/64
# semitones, stored as 1000 + that (see m00Drc's build_bass.py).
SAWS, BEAT_PER_BEAT, DEFAULT_BPM = "Saws", 0.5, 120.0
# CC value -> swell rate, as a multiple of BEAT_PER_BEAT: 0..127 spread over the list.
WOBBLES = (0.25, 0.5, 1.0, 2.0, 4.0)
WOBBLE_CC, GATE_CC = 22, 23
GATE_BITS_CCS, WOBBLE_RATE_CC, WOBBLE_MAX = (25, 26, 27), 28, 8.0
GATES = (None,                      # 0: legato
         0xAAAA,                    # 1: 8ths
         0xFFFF,                    # 2: 16ths
         0x2222,                    # 3: offbeats
         0x8888,                    # 4: quarters
         0x9249,                    # 5: dotted 8ths (3+3+3+3+4 feel)
         0xB6DB,                    # 6: gallop
         0x8A28)                    # 7: broken (a two-step kick line)
CLOCKS_PER_16TH = 6


class SunVoxBass:
    def __init__(self):
        sv = self.sv = open_lib(SR)
        load(sv, 0, PROJECT)
        self.voices = [(find_module(sv, 0, name, PROJECT), offset) for name, offset in VOICES.items()]
        self.saws = find_module(sv, 0, SAWS, PROJECT) - 1
        ctls = [sv.sv_get_module_ctl_name(0, self.saws, c).decode()
                for c in range(sv.sv_get_number_of_module_ctls(0, self.saws))]
        self.osc2 = ctls.index("Osc2")
        self.bpm = DEFAULT_BPM
        self.clock = ClockFollower(on_tempo=self._set_tempo)
        sv.sv_volume(0, VOLUME)
        self.lock = threading.Lock()  # ONE_THREAD: events and rendering must not overlap
        self.stack = []               # held notes, last = sounding
        self.wobble = 1.0             # times BEAT_PER_BEAT (WOBBLE_CC)
        self.gate = None              # 16-bit pattern (GATE_CC), None = legato
        self.gated_off = False        # the gate has the held note silenced
        self.gate_bits = [0, 0, 0]    # GATE_BITS_CCS as they arrive
        self.buf = np.zeros((BLOCK, 2), dtype="<f4")

    def _detune(self, note):
        """Sets osc2 so the saws beat BEAT_PER_BEAT times per beat at `note`."""
        freq = 440 * 2 ** ((note - 69) / 12)
        beat_hz = self.bpm / 60 * BEAT_PER_BEAT * self.wobble
        units = min(1000, round(64 * 12 * math.log2(1 + beat_hz / freq)))
        self.sv.sv_set_module_ctl_value(0, self.saws, self.osc2, 1000 + units, 0)

    def _set_tempo(self, bpm):
        if round(bpm, 1) != round(self.bpm, 1):
            self.bpm = bpm
            if self.stack: self._detune(self.stack[-1])

    def clock_start(self):
        with self.lock:
            self.clock.start()

    def clock_tick(self, now=None):
        with self.lock:
            self.clock.tick(now)
            pulse = self.clock.count - 1
            if self.gate is None or pulse % CLOCKS_PER_16TH or not self.stack:
                return
            if self.gate >> (15 - pulse // CLOCKS_PER_16TH % 16) & 1:
                self._play(self.stack[-1], self.vel)    # re-attack on the step
                self.gated_off = False
            elif not self.gated_off:
                self._release()
                self.gated_off = True

    def control(self, cc, value):
        with self.lock:
            if cc == WOBBLE_CC:
                self.wobble = WOBBLES[min(value * len(WOBBLES) // 128, len(WOBBLES) - 1)]
                if self.stack: self._detune(self.stack[-1])
            elif cc == GATE_CC:
                self.gate = GATES[value % len(GATES)]
                if self.gate is None and self.gated_off and self.stack:   # back to legato
                    self._play(self.stack[-1], self.vel)
                self.gated_off = False
            elif cc in GATE_BITS_CCS:
                self.gate_bits[GATE_BITS_CCS.index(cc)] = value
                if cc == GATE_BITS_CCS[-1]:
                    hi, mid, lo = self.gate_bits
                    self.gate = (hi & 63) << 10 | (mid & 31) << 5 | lo & 31
                    if self.gated_off and self.stack:
                        self._play(self.stack[-1], self.vel)
                    self.gated_off = False
            elif cc == WOBBLE_RATE_CC:
                self.wobble = min(WOBBLE_MAX, 2 ** (value / 16) / 8)
                if self.stack: self._detune(self.stack[-1])
            elif cc == 121:
                self.wobble, self.gate = 1.0, None
                if self.stack: self._detune(self.stack[-1])

    def _play(self, note, vel):
        self._detune(note)    # sent first, so the note starts with it
        # One track per voice: a new note on it replaces the one sounding.
        for track, (module, offset) in enumerate(self.voices):
            self.sv.sv_send_event(0, track, note + offset, vel + 1, module, 0, 0)   # velocity 1..129

    def _release(self):
        for track, (module, _) in enumerate(self.voices):
            self.sv.sv_send_event(0, track, NOTECMD_NOTE_OFF, 0, module, 0, 0)

    def note_on(self, note, vel):
        with self.lock:
            if note in self.stack: self.stack.remove(note)
            self.stack.append(note)
            self.vel = vel
            self.gated_off = False
            self._play(note, vel)

    def note_off(self, note):
        with self.lock:
            if note not in self.stack: return
            sounding = self.stack[-1] == note
            self.stack.remove(note)
            if not sounding: return
            if self.stack: self._play(self.stack[-1], self.vel)
            else: self._release()

    def all_off(self):
        with self.lock:
            self.stack.clear()
            self.sv.sv_send_event(0, 0, NOTECMD_ALL_NOTES_OFF, 0, 0, 0, 0)

    def render(self):
        with self.lock:
            self.sv.sv_audio_callback(self.buf.ctypes.data, BLOCK, 0, self.sv.sv_get_ticks())
        return self.buf


def open_midi():
    from alsa_midi import SequencerClient, WRITE_PORT, PortType
    client = SequencerClient("m00Dr-SunVoxBass")
    # Keep a reference: alsa-midi deletes the port when its object is garbage-collected.
    client.bass_in = client.create_port("bass in", caps=WRITE_PORT, type=PortType.MIDI_GENERIC | PortType.APPLICATION)
    print(f"MIDI in: ALSA client {client.client_id} 'm00Dr-SunVoxBass', port 0 'bass in'", flush=True)
    return client


def midi_loop(client, bass):
    from alsa_midi import NoteOnEvent, NoteOffEvent, ControlChangeEvent, ClockEvent, StartEvent
    while True:
        ev = client.event_input()
        if isinstance(ev, ClockEvent): bass.clock_tick(); continue
        if isinstance(ev, StartEvent): bass.clock_start(); continue
        if getattr(ev, "channel", None) != BASS_CHANNEL: continue
        if isinstance(ev, NoteOnEvent):
            bass.note_on(ev.note, ev.velocity) if ev.velocity else bass.note_off(ev.note)
        elif isinstance(ev, NoteOffEvent): bass.note_off(ev.note)
        elif isinstance(ev, ControlChangeEvent) and ev.param in (120, 123): bass.all_off()
        elif isinstance(ev, ControlChangeEvent): bass.control(ev.param, ev.value)


def demo(path):
    """A1 A1 C2 G1 E1, like reese.py's demo riff, at 120 bpm."""
    import wave
    b = SunVoxBass(); blocks = []
    for note, beats in [(33, 0.5), (33, 0.25), (36, 0.25), (31, 0.5), (28, 1.0)] * 2:
        b.note_on(note, 110)
        n = int(beats * 0.5 * SR / BLOCK)
        for i in range(n):
            if i == int(n * 0.9): b.note_off(note)
            blocks.append(b.render().copy())
    for _ in range(int(0.3 * SR / BLOCK)): blocks.append(b.render().copy())
    y = np.concatenate(blocks)
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())
    print(f"peak {np.abs(y).max():.2f}")


def main():
    bass = SunVoxBass()
    threading.Thread(target=midi_loop, args=(open_midi(), bass), daemon=True).start()
    # --target 0: left unlinked; mix/links.sh wires it into the mixer's bass strip.
    p = subprocess.Popen(["pw-cat", "--playback", "--raw", "--format=f32", f"--rate={SR}", "--channels=2",
                          "--target=0", f"--latency={BLOCK}",
                          "-P", "{ node.name=m00Dr-SunVoxBass media.name=\"sunvox bass\" }", "-"], stdin=subprocess.PIPE)
    fcntl.fcntl(p.stdin.fileno(), fcntl.F_SETPIPE_SZ, 4096)   # keep the pipe from adding ~170 ms of latency
    while True:
        p.stdin.write(bass.render().tobytes())                # blocks at real-time pace


if __name__ == "__main__":
    demo(sys.argv[2]) if sys.argv[1:2] == ["--demo"] else main()
