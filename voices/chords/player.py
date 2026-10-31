# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "alsa-midi"]
# ///
"""m00Dr's chords on SunVox: a DX7-style FM electric piano voiced like a Wurlitzer.

The patch is wurli.sunvox beside this file (built by `uv run
voices/chords/build.py`): two FM layers, a 1:1 reed whose brightness decays fast
and a short 1:2 odd-harmonic bark, through tremolo, a little amp
saturation, a low-pass and a small room.

The tremolo follows m00Dr's tempo: m00Dr's master clock sends MIDI Start and
24 clock pulses per beat out of the same port, so the tremolo runs at
TREMOLO_PER_BEAT pulses per beat at the clock's tempo, and its phase is reset
on every beat (TREMOLO_PHASE: loudest on the beat). Without a clock (m00Dr
following an external one, or stopped) it keeps the last tempo it heard.

m00Dr sends its chords here, on MIDI ch7, when its chord sound is "SunVox
Wurli", and on ch1 to FluidSynth otherwise, so only one of the two plays.
Listens on ALSA sequencer client "m00Dr-SunVoxKeys" and plays through pw-cat
into the m800Dr Mixer's chords strip, like reese.py.

    uv run sunvoxkeys.py                 # run (the m00dr-sunvoxkeys user service does this)
    uv run sunvoxkeys.py --demo x.wav    # render a few chords offline, for listening
"""
import fcntl, json, subprocess, sys, threading
import numpy as np
import pathlib as _pathlib, sys as _sys
VOICE = _pathlib.Path(__file__).resolve().parent   # this voice's folder: its patches live here
_sys.path.insert(0, str(VOICE.parent / "_sunvox"))
from sunvoxlib import HERE, NOTECMD_ALL_NOTES_OFF, NOTECMD_NOTE_OFF, ClockFollower, find_module, load, open_lib

SR, BLOCK = 48000, 256
KEYS_CHANNEL = 6                          # 0-based: m00Dr's SunVox chords are MIDI ch7
# The patches (voices/chords/build.py writes them and patches.json: program, inputs,
# note offset, slot volume). Each is loaded into its own slot; a program change on
# KEYS_CHANNEL picks the one new notes go to. Notes already sounding finish where
# they started. The Wurli's bark and tremolo below apply to patches that have
# those modules (only the Wurli today).
PATCHES = VOICE / "patches.json"
INPUT_MODULES = ("Reed", "Bark")          # each note plays on both FM layers
# The Bark layer's level (its C.Volume, 0..256) at velocity 0 and 127, set just
# before each note so a hard hit barks. Matches m00Drc's build_keys.py.
BARK_MODULE, BARK_LEVEL = "Bark", (0, 90)
# Simultaneous notes. SunVox takes events on tracks 0..31 only (measured: a
# note sent on track 32 is silent), and each input module needs its own block.
TRACKS = 32 // len(INPUT_MODULES)
VOLUME = 256                              # sv_volume, 0..256
# MIDI note -> SunVox library note. The FM module tracks pitch two octaves
# apart from the AnalogGenerator that sunvox303.py's -23 was measured on:
# here library note 58 plays 220 Hz (A3, MIDI 57). Measured, not derived.
NOTE_OFFSET = 1

# Tempo-synced tremolo. The LFO's rate is set as a period in ms (see m00Drc's
# build_keys.py). Its "Set phase" restarts the wobble: measured with a 250 ms
# period, 0 puts the quietest point on the reset and 128 the loudest.
TREMOLO_MODULE = "Tremolo"
TREMOLO_PER_BEAT = 2                      # 8th notes: 4 Hz at 120 bpm
TREMOLO_PHASE = 128                       # loudest on the beat
DEFAULT_BPM = 120.0


class SunVoxKeys:
    def __init__(self):
        sv = self.sv = open_lib(SR)
        self.patches = []             # per slot: dict(name, mods, offset, per, bark, lfo)
        self.slot_of = {}             # program -> slot
        for slot, spec in enumerate(json.loads(PATCHES.read_text())["patches"]):
            path = VOICE / spec["file"]
            load(sv, slot, path)
            sv.sv_volume(slot, spec.get("volume", VOLUME))
            mods = [find_module(sv, slot, name, path) for name in spec["inputs"]]
            patch = dict(name=spec["name"], mods=mods, offset=spec.get("note_offset", NOTE_OFFSET),
                         per=32 // len(mods), bark=None, lfo=None)
            if BARK_MODULE in spec["inputs"]:
                patch["bark"] = find_module(sv, slot, BARK_MODULE, path) - 1   # module number, for ctl calls
            lfo = sv.sv_find_module(slot, TREMOLO_MODULE.encode())
            if lfo >= 0:
                ctls = [sv.sv_get_module_ctl_name(slot, lfo, c).decode()
                        for c in range(sv.sv_get_number_of_module_ctls(slot, lfo))]
                patch["lfo"] = (lfo, ctls.index("Freq"), ctls.index("Set phase"))
            self.patches.append(patch)
            self.slot_of[spec["program"]] = slot
        self.slot = 0
        self.clock = ClockFollower(on_tempo=self._set_tempo, on_beat=self._restart_tremolo)
        self.period_ms = 0
        self._set_tempo(DEFAULT_BPM)
        self.lock = threading.Lock()  # ONE_THREAD: events and rendering must not overlap
        self.held = {}                # held MIDI note -> (slot, track)
        self.free = {slot: list(range(p["per"])) for slot, p in enumerate(self.patches)}
        self.buf = np.zeros((BLOCK, 2), dtype="<f4")

    @property
    def name(self):
        return self.patches[self.slot]["name"]

    def program(self, number):
        with self.lock:
            slot = self.slot_of.get(number)
            if slot is None or slot == self.slot:
                return False
            self.slot = slot
            return True

    def _send(self, slot, track, note, vel=0):
        # A track holds one note, so each input module gets its own block of tracks.
        p = self.patches[slot]
        for i, module in enumerate(p["mods"]):
            self.sv.sv_send_event(slot, i * p["per"] + track, note, vel, module, 0, 0)

    def _release(self, note):
        slot, track = self.held.pop(note)
        self._send(slot, track, NOTECMD_NOTE_OFF)
        self.free[slot].append(track)

    def note_on(self, note, vel):
        with self.lock:
            if note in self.held:     # restruck while held: release it where it is
                self._release(note)
            slot, p = self.slot, self.patches[self.slot]
            if self.free[slot]:
                track = self.free[slot].pop(0)
            else:                     # all busy: take this slot's oldest held note's track
                oldest = next(n for n, (s, _) in self.held.items() if s == slot)
                track = self.held.pop(oldest)[1]
            self.held[note] = (slot, track)
            if p["bark"] is not None:  # C.Volume is controller 0; events apply in the order sent
                lo, hi = BARK_LEVEL
                self.sv.sv_set_module_ctl_value(slot, p["bark"], 0, round(lo + (hi - lo) * vel / 127), 0)
            self._send(slot, track, note + p["offset"], vel + 1)   # SunVox velocity is 1..129

    def note_off(self, note):
        with self.lock:
            if note in self.held:
                self._release(note)

    def _set_tempo(self, bpm):
        period = round(60000 / bpm / TREMOLO_PER_BEAT)
        if period != self.period_ms:
            self.period_ms = period
            for slot, p in enumerate(self.patches):
                if p["lfo"]: self.sv.sv_set_module_ctl_value(slot, p["lfo"][0], p["lfo"][1], period, 0)

    def _restart_tremolo(self):
        for slot, p in enumerate(self.patches):
            if p["lfo"]: self.sv.sv_set_module_ctl_value(slot, p["lfo"][0], p["lfo"][2], TREMOLO_PHASE, 0)

    def clock_start(self):
        with self.lock:
            self.clock.start()

    def clock_tick(self, now=None):
        """A MIDI clock pulse: follow the tempo, restart the wobble each beat."""
        with self.lock:
            self.clock.tick(now)

    def all_off(self):
        with self.lock:
            self.held.clear()
            for slot, p in enumerate(self.patches):
                self.free[slot] = list(range(p["per"]))
                self.sv.sv_send_event(slot, 0, NOTECMD_ALL_NOTES_OFF, 0, 0, 0, 0)

    def render(self):
        with self.lock:
            self.sv.sv_audio_callback(self.buf.ctypes.data, BLOCK, 0, self.sv.sv_get_ticks())
        return self.buf


def open_midi():
    from alsa_midi import SequencerClient, WRITE_PORT, PortType
    client = SequencerClient("m00Dr-SunVoxKeys")
    # Keep a reference: alsa-midi deletes the port when its object is garbage-collected.
    client.keys_in = client.create_port("keys in", caps=WRITE_PORT, type=PortType.MIDI_GENERIC | PortType.APPLICATION)
    print(f"MIDI in: ALSA client {client.client_id} 'm00Dr-SunVoxKeys', port 0 'keys in'", flush=True)
    return client


def midi_loop(client, keys):
    from alsa_midi import NoteOnEvent, NoteOffEvent, ControlChangeEvent, ClockEvent, StartEvent, ProgramChangeEvent
    while True:
        ev = client.event_input()
        if isinstance(ev, ClockEvent): keys.clock_tick(); continue
        if isinstance(ev, StartEvent): keys.clock_start(); continue
        if getattr(ev, "channel", None) != KEYS_CHANNEL: continue
        if isinstance(ev, NoteOnEvent):
            keys.note_on(ev.note, ev.velocity) if ev.velocity else keys.note_off(ev.note)
        elif isinstance(ev, NoteOffEvent): keys.note_off(ev.note)
        elif isinstance(ev, ProgramChangeEvent):
            if keys.program(ev.value): print(f"patch: {keys.name}", flush=True)
        elif isinstance(ev, ControlChangeEvent) and ev.param in (120, 123): keys.all_off()


def demo(path):
    """Per patch: i-VI-III-VII in A minor, a bar each at 100 bpm, then a soft and a
    hard stab."""
    import wave
    k = SunVoxKeys(); blocks = []
    for program in sorted(k.slot_of):
      k.program(program)
      _demo_patch(k, blocks)
    hold = lambda seconds: [blocks.append(k.render().copy()) for _ in range(int(seconds * SR / BLOCK))]
    hold(1.0)
    y = np.concatenate(blocks)
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())
    print(f"{len(k.patches)} patches, peak {np.abs(y).max():.2f}")


def _demo_patch(k, blocks):
    bar = 2.4
    chords = [[57, 60, 64, 67], [53, 57, 60, 64], [48, 52, 55, 59], [55, 59, 62, 65]]
    def hold(seconds):
        for _ in range(int(seconds * SR / BLOCK)): blocks.append(k.render().copy())
    for chord in chords:
        for n in chord: k.note_on(n, 90)
        hold(bar * 0.9)
        for n in chord: k.note_off(n)
        hold(bar * 0.1)
    for vel in (50, 120):
        for n in chords[0]: k.note_on(n, vel)
        hold(0.3)
        for n in chords[0]: k.note_off(n)
        hold(0.5)
    hold(1.5)


def main():
    keys = SunVoxKeys()
    threading.Thread(target=midi_loop, args=(open_midi(), keys), daemon=True).start()
    # --target 0: left unlinked; mix/links.sh wires it into the mixer's chords strip.
    p = subprocess.Popen(["pw-cat", "--playback", "--raw", "--format=f32", f"--rate={SR}", "--channels=2",
                          "--target=0", f"--latency={BLOCK}",
                          "-P", "{ node.name=m00Dr-SunVoxKeys media.name=\"sunvox keys\" }", "-"], stdin=subprocess.PIPE)
    fcntl.fcntl(p.stdin.fileno(), fcntl.F_SETPIPE_SZ, 4096)   # keep the pipe from adding ~170 ms of latency
    while True:
        p.stdin.write(keys.render().tobytes())                # blocks at real-time pace


if __name__ == "__main__":
    demo(sys.argv[2]) if sys.argv[1:2] == ["--demo"] else main()
