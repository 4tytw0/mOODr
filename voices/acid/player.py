# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "alsa-midi"]
# ///
"""m00Dr's acid, on SunVox: a headless SunVox library playing a 303-shaped patch.

The patch is acid303.sunvox beside this file (built by `uv run
voices/acid/build.py --acid`): Glide -> mono saw -> resonant 24 dB low-pass
that a Velocity2Ctl kicks open on every note (further on accents) ->
Distortion. Listens on ALSA sequencer
client "m00Dr-SunVox303" (MIDI channel ACID_CHANNEL) and plays through pw-cat
into the m800Dr Mixer's acid strip, like reese.py.

Sound CCs: 7-bit CCs on ACID_CHANNEL move the patch's key controls
(SOUND_CCS), each eased toward its new value over SOUND_GLIDE so a jump in
the knob or in m00Drc's roller (which sends these) sweeps rather than steps.

The 303 page (/acid on server.py) sets the same controls over UDP (CONTROL):
{"cc": 74, "value": 0..127} sets one and holds it, so MIDI CCs for it
(the roller's) are ignored until {"cc": 74, "hold": false} hands it back,
at the last value MIDI sent. The live state is written to LIVE every 100 ms.

m00Dr slides by sending the next note-on before the previous note-off. Each
held note gets its own SunVox track, so a slide reaches the Glide module as two
overlapping notes, which it turns into a pitch sweep instead of a retrigger.

The SunVox library is loaded by sunvoxlib.py (see there for where it lives).

    uv run sunvox303.py                # run (the m00dr-sunvox303 user service does this)
    uv run sunvox303.py --demo x.wav   # render a short riff offline, for checking levels
"""
import fcntl, json, os, pathlib, socket, subprocess, sys, threading, time
import numpy as np
import pathlib as _pathlib, sys as _sys
VOICE = _pathlib.Path(__file__).resolve().parent   # this voice's folder: its patches live here
_sys.path.insert(0, str(VOICE.parent / "_sunvox"))
from sunvoxlib import HERE, NOTECMD_ALL_NOTES_OFF, NOTECMD_NOTE_OFF, ClockFollower, find_module, load, open_lib

SR, BLOCK = 48000, 256
ACID_CHANNEL = 4                          # 0-based: m00Dr's acid part is MIDI ch5
PROJECT = VOICE / "acid303.sunvox"
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
    77: ("Acid drive", "Power", 60, 220),               # saturation into the 10-bit crush
    78: ("Acid grit", "Power", 0, 200),                # harsher foldback distortion, starts clean
    79: ("Acid level", "Volume", 128, 512),            # master level: -6 dB to +6 dB, starts at unity
    80: ("Acid delay", "Wet", 0, 200),                 # echo mix, starts off
    81: ("Acid delay", "Feedback", 0, 230),            # echo repeats, kept short of runaway
    82: ("Acid delay", "Delay", 60, 750),              # echo delay time: a note ratio (DELAY_RATIOS), in ms
    # The producer's requests #1-#3 (CCs MIDI leaves undefined):
    102: ("Acid tube follow", "OUT max", 0, 32768),    # tube: how much the drive follows the envelope
    103: ("Acid spark", "Volume", 0, 200),             # spark: distorted highs (above 150 Hz) mixed in
    104: ("Acid low", "Gain", 4384, 22384),            # low: the band below 150 Hz, -12..+6 dB (raw: 16384 = 0 dB, 1000 a dB)
    105: ("Acid plate", "Wet", 0, 160),                # verb: the plate's wet level
    106: ("Acid shimmer", "Volume", 0, 200),           # shimmer: the octave-up reverb tail
}
# The delay time is a note ratio of m00Dr's clock, not free milliseconds: CC 82 picks one
# of these (beats; 1/16, 1/8, dotted 1/8, 1/4, dotted 1/4, 1/2), turned into ms at the
# tempo, and re-timed when the tempo changes.
DELAY_CC, DELAY_RATIOS, DELAY_NAMES = 82, (0.25, 0.5, 0.75, 1.0, 1.5, 2.0), ("1/16", "1/8", "1/8.", "1/4", "1/4.", "1/2")
DEFAULT_BPM, DELAY_MAX_MS = 120.0, 3000
SOUND_GLIDE = 0.5                         # s, time constant for easing to a new CC value
# What the 303 page calls each one.
SOUND_LABELS = {74: "Cutoff", 71: "Resonance", 75: "Decay", 76: "Accent", 5: "Glide", 77: "Drive", 78: "Grit", 79: "Level",
                80: "Delay", 81: "Feedback", 82: "Time", 102: "Tube", 103: "Spark", 104: "Low", 105: "Verb", 106: "Shimmer"}

CONTROL = ("127.0.0.1", 9811)             # the 303 page, via server.py (mixer.py has 9810)
LIVE = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "m800dr-acid.json"


class SunVox303:
    def __init__(self):
        sv = self.sv = open_lib(SR)
        load(sv, 0, PROJECT)
        self.modules = [find_module(sv, 0, name, PROJECT) for name in INPUT_MODULES]
        sv.sv_volume(0, VOLUME)
        # CC -> [module, ctl, lo, hi, current, target, 7-bit value, held, last MIDI value]
        self.sound = {}
        for cc, (name, ctl_name, lo, hi) in SOUND_CCS.items():
            mod = sv.sv_find_module(0, name.encode())
            ctls = [sv.sv_get_module_ctl_name(0, mod, c).decode()
                    for c in range(sv.sv_get_number_of_module_ctls(0, mod))] if mod >= 0 else []
            if ctl_name not in ctls:
                raise RuntimeError(f"no controller {name!r}/{ctl_name!r} in {PROJECT}")
            ctl = ctls.index(ctl_name)
            now = float(sv.sv_get_module_ctl_value(0, mod, ctl, 0))
            value = min(max(round((now - lo) / (hi - lo) * 127), 0), 127)
            self.sound[cc] = [mod, ctl, lo, hi, now, now, value, False, value]
        self.ease = 1 - np.exp(-BLOCK / (SOUND_GLIDE * SR))
        self.bpm = DEFAULT_BPM
        self.clock = ClockFollower(on_tempo=self._set_tempo)   # for the delay's note ratios
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

    def _set(self, entry, value):
        entry[6] = value
        if entry is self.sound.get(DELAY_CC):   # a note ratio at the clock's tempo
            ratio = DELAY_RATIOS[min(value * len(DELAY_RATIOS) // 128, len(DELAY_RATIOS) - 1)]
            entry[5] = min(DELAY_MAX_MS, ratio * 60000 / self.bpm)
        else:
            entry[5] = entry[2] + (entry[3] - entry[2]) * value / 127

    def _set_tempo(self, bpm):
        self.bpm = bpm
        entry = self.sound.get(DELAY_CC)
        if entry is not None:
            self._set(entry, entry[6])

    def clock_start(self):
        with self.lock:
            self.clock.start()

    def clock_tick(self):
        with self.lock:
            self.clock.tick()

    def control(self, cc, value):
        """A 7-bit CC from MIDI: set that controller's target, unless the 303
        page holds it; render() eases toward it."""
        with self.lock:
            entry = self.sound.get(cc)
            if entry is not None:
                entry[8] = value
                if not entry[7]:
                    self._set(entry, value)

    def hold(self, cc, value=None):
        """From the 303 page: set a controller and hold it against MIDI
        (value 0..127), or with no value hand it back to MIDI's last value."""
        with self.lock:
            entry = self.sound.get(cc)
            if entry is None:
                return
            entry[7] = value is not None
            self._set(entry, entry[8] if value is None else min(max(int(value), 0), 127))

    def state(self):
        with self.lock:
            return {"time": time.time(), "controls": [
                {"cc": cc, "label": SOUND_LABELS[cc], "value": e[6], "held": e[7],
                 "real": round(e[4]), "lo": e[2], "hi": e[3]}
                for cc, e in self.sound.items()]}

    def render(self):
        with self.lock:
            for entry in self.sound.values():
                mod, ctl, _, _, now, target = entry[:6]
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
    from alsa_midi import NoteOnEvent, NoteOffEvent, ControlChangeEvent, ClockEvent, StartEvent
    while True:
        ev = client.event_input()
        if isinstance(ev, ClockEvent): synth.clock_tick(); continue     # the tempo, for the delay
        if isinstance(ev, StartEvent): synth.clock_start(); continue
        if getattr(ev, "channel", None) != ACID_CHANNEL: continue
        if isinstance(ev, NoteOnEvent):
            synth.note_on(ev.note, ev.velocity) if ev.velocity else synth.note_off(ev.note)
        elif isinstance(ev, NoteOffEvent): synth.note_off(ev.note)
        elif isinstance(ev, ControlChangeEvent):
            if ev.param in (120, 123): synth.all_off()
            elif ev.param in SOUND_CCS: synth.control(ev.param, ev.value)


def control_loop(synth):
    """The 303 page's knobs, as JSON datagrams on CONTROL (see the docstring)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(CONTROL)
    while True:
        try:
            msg = json.loads(sock.recv(4096))
            cc = int(msg["cc"])
            synth.hold(cc, None if msg.get("hold") is False else msg["value"])
        except (ValueError, TypeError, KeyError):
            pass


def state_loop(synth):
    LIVE.parent.mkdir(parents=True, exist_ok=True)
    tmp = LIVE.with_suffix(".tmp")
    while True:
        tmp.write_text(json.dumps(synth.state()))
        tmp.replace(LIVE)
        time.sleep(0.1)


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
    threading.Thread(target=control_loop, args=(synth,), daemon=True).start()
    threading.Thread(target=state_loop, args=(synth,), daemon=True).start()
    # --target 0: left unlinked; mix/links.sh wires it into the mixer's acid strip.
    p = subprocess.Popen(["pw-cat", "--playback", "--raw", "--format=f32", f"--rate={SR}", "--channels=2",
                          "--target=0", f"--latency={BLOCK}",
                          "-P", "{ node.name=m00Dr-SunVox303 media.name=\"sunvox 303\" }", "-"], stdin=subprocess.PIPE)
    fcntl.fcntl(p.stdin.fileno(), fcntl.F_SETPIPE_SZ, 4096)   # keep the pipe from adding ~170 ms of latency
    while True:
        p.stdin.write(synth.render().tobytes())               # blocks at real-time pace


if __name__ == "__main__":
    demo(sys.argv[2]) if sys.argv[1:2] == ["--demo"] else main()
