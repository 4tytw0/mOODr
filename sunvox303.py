# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "alsa-midi"]
# ///
"""m00Dr's acid, on SunVox: a headless SunVox library playing a 303-shaped patch.

The patch is synths/acid303.sunvox (built by m00Drc/: `uv run python -m
m00drc.build_project --acid`): Glide -> mono saw -> resonant 24 dB low-pass
that a Velocity2Ctl kicks open on every note (further on accents) ->
Distortion. Listens on ALSA sequencer
client "m00Dr-SunVox303" (MIDI channel ACID_CHANNEL) and plays through pw-cat
into the m800Dr Mixer's acid strip, like reese.py.

Sound CCs: 7-bit CCs on ACID_CHANNEL move the patch's key controls
(SOUND_CCS), each eased toward its new value over SOUND_GLIDE so a jump in
the knob or in m00Drc's roller (which sends these) sweeps rather than steps.

m00Dr slides by sending the next note-on before the previous note-off. Each
held note gets its own SunVox track, so a slide reaches the Glide module as two
overlapping notes, which it turns into a pitch sweep instead of a retrigger.

The SunVox library isn't in git: synths/sunvox_lib/ holds sunvox.so (Linux
x86_64) or sunvox.dylib (macOS), from warmplace.ru/soft/sunvox (sunvox_lib-*.zip).

    uv run sunvox303.py                # run (the m00dr-sunvox303 user service does this)
    uv run sunvox303.py --demo x.wav   # render a short riff offline, for checking levels
"""
import ctypes, fcntl, subprocess, sys, threading
from pathlib import Path
import numpy as np

SR, BLOCK = 48000, 256
ACID_CHANNEL = 4                          # 0-based: m00Dr's acid part is MIDI ch5
HERE = Path(__file__).resolve().parent
PROJECT = HERE / "synths" / "acid303.sunvox"
LIB = HERE / "synths" / "sunvox_lib" / ("sunvox.dylib" if sys.platform == "darwin" else "sunvox.so")
# The modules in PROJECT that take the notes: Glide plays them, Velocity2Ctl
# kicks the filter open on each one (harder on accents).
INPUT_MODULES = ("Acid glide ch5", "Acid accent ch5")
TRACKS = 16                               # SunVox tracks per input module to spread held notes across
VOLUME = 256                              # sv_volume, 0..256
# MIDI note -> SunVox library note. Measured: library note 37 plays 262 Hz
# (middle C, MIDI 60), so MIDI n is library note n - 23.
NOTE_OFFSET = -23

# CC (on ACID_CHANNEL) -> (module, controller, real value at CC 0, at CC 127).
# CC numbers follow General MIDI where there is one (71 resonance, 74
# brightness, 5 portamento time). The ranges keep every corner sounding like a
# 303: the cutoff never fully shuts and the resonance stays short of screaming.
SOUND_CCS = {
    74: ("Acid filter", "Freq", 150, 1200),              # resting cutoff, Hz
    71: ("Acid filter", "Resonance", 700, 1450),
    75: ("Acid filter", "Response", 10, 2),              # filter decay: 10 snaps shut, 2 lingers
    76: ("Acid accent ch5", "OUT max", 9000, 22000),         # how far a hard note kicks the filter (ctl units)
    5:  ("Acid glide ch5", "Response", 900, 300),        # slide time: 900 fast, 300 slow
    77: ("Acid drive", "Power", 20, 140),
}
SOUND_GLIDE = 0.5                         # s, time constant for easing to a new CC value

SV_INIT_FLAG_NO_DEBUG_OUTPUT = 1 << 0
SV_INIT_FLAG_USER_AUDIO_CALLBACK = 1 << 1
SV_INIT_FLAG_AUDIO_FLOAT32 = 1 << 3
SV_INIT_FLAG_ONE_THREAD = 1 << 4
NOTECMD_NOTE_OFF, NOTECMD_ALL_NOTES_OFF = 128, 129


class SunVox303:
    def __init__(self):
        sv = self.sv = ctypes.CDLL(str(LIB))
        sv.sv_load.argtypes = [ctypes.c_int, ctypes.c_char_p]
        sv.sv_find_module.argtypes = [ctypes.c_int, ctypes.c_char_p]
        sv.sv_audio_callback.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_uint32]
        sv.sv_get_ticks.restype = ctypes.c_uint32
        flags = (SV_INIT_FLAG_NO_DEBUG_OUTPUT | SV_INIT_FLAG_USER_AUDIO_CALLBACK
                 | SV_INIT_FLAG_AUDIO_FLOAT32 | SV_INIT_FLAG_ONE_THREAD)
        if sv.sv_init(None, SR, 2, flags) < 0:
            raise RuntimeError("sv_init failed")
        sv.sv_open_slot(0)
        if sv.sv_load(0, str(PROJECT).encode()) != 0:
            raise RuntimeError(f"can't load {PROJECT}")
        self.modules = []
        for name in INPUT_MODULES:
            module = sv.sv_find_module(0, name.encode())
            if module < 0:
                raise RuntimeError(f"no module {name!r} in {PROJECT}")
            self.modules.append(module + 1)  # sv_send_event wants module number + 1
        sv.sv_volume(0, VOLUME)
        # Events are applied at the start of the next sv_audio_callback.
        sv.sv_set_event_t(0, 1, 0)
        sv.sv_get_module_ctl_name.restype = ctypes.c_char_p
        self.sound = {}               # CC -> [module, ctl, lo, hi, current, target]
        for cc, (name, ctl_name, lo, hi) in SOUND_CCS.items():
            mod = sv.sv_find_module(0, name.encode())
            ctls = [sv.sv_get_module_ctl_name(0, mod, c).decode()
                    for c in range(sv.sv_get_number_of_module_ctls(0, mod))] if mod >= 0 else []
            if ctl_name not in ctls:
                raise RuntimeError(f"no controller {name!r}/{ctl_name!r} in {PROJECT}")
            ctl = ctls.index(ctl_name)
            now = float(sv.sv_get_module_ctl_value(0, mod, ctl, 0))
            self.sound[cc] = [mod, ctl, lo, hi, now, now]
        self.ease = 1 - np.exp(-BLOCK / (SOUND_GLIDE * SR))
        self.lock = threading.Lock()  # ONE_THREAD: events and rendering must not overlap
        self.tracks = {}              # held MIDI note -> SunVox track
        self.next_track = 0
        self.buf = np.zeros((BLOCK, 2), dtype="<f4")

    def _send(self, track, note, vel=0):
        # A track holds one note, so each input module gets its own block of tracks.
        for i, module in enumerate(self.modules):
            self.sv.sv_send_event(0, i * TRACKS + track, note, vel, module, 0, 0)

    def note_on(self, note, vel):
        with self.lock:
            if note in self.tracks:   # retrigger: release the old one first
                self._send(self.tracks.pop(note), NOTECMD_NOTE_OFF)
            track = self.next_track
            self.next_track = (self.next_track + 1) % TRACKS
            self.tracks[note] = track
            # SunVox velocity is 1..129.
            self._send(track, note + NOTE_OFFSET, vel + 1)

    def note_off(self, note):
        with self.lock:
            track = self.tracks.pop(note, None)
            if track is not None:
                self._send(track, NOTECMD_NOTE_OFF)

    def all_off(self):
        with self.lock:
            self.tracks.clear()
            self.sv.sv_send_event(0, 0, NOTECMD_ALL_NOTES_OFF, 0, 0, 0, 0)

    def control(self, cc, value):
        """A 7-bit CC: set that controller's target; render() eases toward it."""
        with self.lock:
            entry = self.sound.get(cc)
            if entry is not None:
                lo, hi = entry[2], entry[3]
                entry[5] = lo + (hi - lo) * value / 127

    def render(self):
        with self.lock:
            for entry in self.sound.values():
                mod, ctl, _, _, now, target = entry
                if round(now) != round(target):
                    now += (target - now) * self.ease
                    if abs(target - now) < 0.5: now = target
                    entry[4] = now
                    self.sv.sv_set_module_ctl_value(0, mod, ctl, round(now), 0)
            self.sv.sv_audio_callback(self.buf.ctypes.data, BLOCK, 0, self.sv.sv_get_ticks())
        return self.buf


def open_midi():
    from alsa_midi import SequencerClient, WRITE_PORT, PortType
    client = SequencerClient("m00Dr-SunVox303")
    # Keep a reference: alsa-midi deletes the port when its object is garbage-collected.
    client.acid_in = client.create_port("acid in", caps=WRITE_PORT, type=PortType.MIDI_GENERIC | PortType.APPLICATION)
    print(f"MIDI in: ALSA client {client.client_id} 'm00Dr-SunVox303', port 0 'acid in'", flush=True)
    return client


def midi_loop(client, synth):
    from alsa_midi import NoteOnEvent, NoteOffEvent, ControlChangeEvent
    while True:
        ev = client.event_input()
        if getattr(ev, "channel", None) != ACID_CHANNEL: continue
        if isinstance(ev, NoteOnEvent):
            synth.note_on(ev.note, ev.velocity) if ev.velocity else synth.note_off(ev.note)
        elif isinstance(ev, NoteOffEvent): synth.note_off(ev.note)
        elif isinstance(ev, ControlChangeEvent):
            if ev.param in (120, 123): synth.all_off()
            elif ev.param in SOUND_CCS: synth.control(ev.param, ev.value)


def demo(path):
    """A 303-ish riff: (note, 16ths, slide into the next, accent)."""
    import wave
    s = SunVox303(); blocks = []
    step = 0.125  # a 16th at 120 bpm
    riff = [(36, 1, False, True), (36, 1, False, False), (48, 1, True, False), (43, 1, False, True),
            (36, 2, False, False), (39, 1, True, True), (41, 1, False, False)] * 2
    prev = None
    for note, length, slide, accent in riff:
        s.note_on(note, 127 if accent else 90)
        if prev is not None: s.note_off(prev)        # after the note-on: a slide overlaps
        prev = None
        n = int(length * step * SR / BLOCK)
        for i in range(n):
            if not slide and i == int(n * 0.6): s.note_off(note)
            blocks.append(s.render().copy())
        if slide: prev = note
    if prev is not None: s.note_off(prev)
    for _ in range(int(0.3 * SR / BLOCK)): blocks.append(s.render().copy())
    y = np.concatenate(blocks)
    pcm = (np.clip(y, -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
    print(f"peak {np.abs(y).max():.2f}")


def main():
    synth = SunVox303()
    threading.Thread(target=midi_loop, args=(open_midi(), synth), daemon=True).start()
    # --target 0: left unlinked; synths/links.sh wires it into the mixer's acid strip.
    p = subprocess.Popen(["pw-cat", "--playback", "--raw", "--format=f32", f"--rate={SR}", "--channels=2",
                          "--target=0", f"--latency={BLOCK}",
                          "-P", "{ node.name=m00Dr-SunVox303 media.name=\"sunvox 303\" }", "-"], stdin=subprocess.PIPE)
    fcntl.fcntl(p.stdin.fileno(), fcntl.F_SETPIPE_SZ, 4096)   # keep the pipe from adding ~170 ms of latency
    while True:
        p.stdin.write(synth.render().tobytes())               # blocks at real-time pace


if __name__ == "__main__":
    demo(sys.argv[2]) if sys.argv[1:2] == ["--demo"] else main()
