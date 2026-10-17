# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "alsa-midi"]
# ///
"""m00Dr's chords on SunVox: a DX7-style FM electric piano voiced like a Wurlitzer.

The patch is synths/wurli.sunvox (built by m00Drc/: `uv run python -m
m00drc.build_keys`): two FM layers, a 1:1 reed whose brightness decays fast
and a short 1:2 odd-harmonic bark, through tremolo, a little amp
saturation, a low-pass and a small room.

m00Dr sends its chords here, on MIDI ch7, when its chord sound is "SunVox
Wurli", and on ch1 to FluidSynth otherwise, so only one of the two plays.
Listens on ALSA sequencer client "m00Dr-SunVoxKeys" and plays through pw-cat
into the m800Dr Mixer's chords strip, like reese.py.

    uv run sunvoxkeys.py                 # run (the m00dr-sunvoxkeys user service does this)
    uv run sunvoxkeys.py --demo x.wav    # render a few chords offline, for listening
"""
import fcntl, subprocess, sys, threading
import numpy as np
from sunvoxlib import HERE, NOTECMD_ALL_NOTES_OFF, NOTECMD_NOTE_OFF, find_module, load, open_lib

SR, BLOCK = 48000, 256
KEYS_CHANNEL = 6                          # 0-based: m00Dr's SunVox chords are MIDI ch7
PROJECT = HERE / "synths" / "wurli.sunvox"
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


class SunVoxKeys:
    def __init__(self):
        sv = self.sv = open_lib(SR)
        load(sv, 0, PROJECT)
        self.modules = [find_module(sv, 0, name, PROJECT) for name in INPUT_MODULES]
        self.bark = find_module(sv, 0, BARK_MODULE, PROJECT) - 1   # module number, for ctl calls
        sv.sv_volume(0, VOLUME)
        self.lock = threading.Lock()  # ONE_THREAD: events and rendering must not overlap
        self.tracks = {}              # held MIDI note -> SunVox track
        self.free = list(range(TRACKS))
        self.buf = np.zeros((BLOCK, 2), dtype="<f4")

    def _send(self, track, note, vel=0):
        # A track holds one note, so each input module gets its own block of tracks.
        for i, module in enumerate(self.modules):
            self.sv.sv_send_event(0, i * TRACKS + track, note, vel, module, 0, 0)

    def note_on(self, note, vel):
        with self.lock:
            if note in self.tracks:   # restruck while held: retrigger on its track
                track = self.tracks[note]
            elif self.free:
                track = self.free.pop(0)
            else:                     # all tracks busy: take the oldest held note's
                oldest = next(iter(self.tracks))
                track = self.tracks.pop(oldest)
            self.tracks[note] = track
            lo, hi = BARK_LEVEL   # C.Volume is controller 0; events apply in the order sent
            self.sv.sv_set_module_ctl_value(0, self.bark, 0, round(lo + (hi - lo) * vel / 127), 0)
            self._send(track, note + NOTE_OFFSET, vel + 1)   # SunVox velocity is 1..129

    def note_off(self, note):
        with self.lock:
            track = self.tracks.pop(note, None)
            if track is not None:
                self._send(track, NOTECMD_NOTE_OFF)
                self.free.append(track)

    def all_off(self):
        with self.lock:
            self.tracks.clear(); self.free = list(range(TRACKS))
            self.sv.sv_send_event(0, 0, NOTECMD_ALL_NOTES_OFF, 0, 0, 0, 0)

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
    from alsa_midi import NoteOnEvent, NoteOffEvent, ControlChangeEvent
    while True:
        ev = client.event_input()
        if getattr(ev, "channel", None) != KEYS_CHANNEL: continue
        if isinstance(ev, NoteOnEvent):
            keys.note_on(ev.note, ev.velocity) if ev.velocity else keys.note_off(ev.note)
        elif isinstance(ev, NoteOffEvent): keys.note_off(ev.note)
        elif isinstance(ev, ControlChangeEvent) and ev.param in (120, 123): keys.all_off()


def demo(path):
    """i-VI-III-VII in A minor, a bar each at 100 bpm, then a soft and a hard stab."""
    import wave
    k = SunVoxKeys(); blocks = []
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
    hold(1.0)
    y = np.concatenate(blocks)
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())
    print(f"peak {np.abs(y).max():.2f}")


def main():
    keys = SunVoxKeys()
    threading.Thread(target=midi_loop, args=(open_midi(), keys), daemon=True).start()
    # --target 0: left unlinked; synths/links.sh wires it into the mixer's chords strip.
    p = subprocess.Popen(["pw-cat", "--playback", "--raw", "--format=f32", f"--rate={SR}", "--channels=2",
                          "--target=0", f"--latency={BLOCK}",
                          "-P", "{ node.name=m00Dr-SunVoxKeys media.name=\"sunvox keys\" }", "-"], stdin=subprocess.PIPE)
    fcntl.fcntl(p.stdin.fileno(), fcntl.F_SETPIPE_SZ, 4096)   # keep the pipe from adding ~170 ms of latency
    while True:
        p.stdin.write(keys.render().tobytes())                # blocks at real-time pace


if __name__ == "__main__":
    demo(sys.argv[2]) if sys.argv[1:2] == ["--demo"] else main()
