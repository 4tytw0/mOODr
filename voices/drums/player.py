# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "alsa-midi"]
# ///
"""m00Dr's drums on SunVox: three generated kits, picked by program change.

The kits are drumkit-*.sunvox beside this file, listed with their note map in
drumkits.json (both built by `uv run
voices/drums/build.py`). Each kit is loaded into its own SunVox slot. A
program change 0..3 on DRUM_CHANNEL picks the kit the next hits go to, and
the old kit's tails ring out. Listens on ALSA sequencer client
"m00Dr-SunVoxDrums" and plays through pw-cat into the m800Dr Mixer's drums
strip, like reese.py.

m00Dr sends its drums here, on MIDI ch6, when its drum source is a SunVox
kit, and on ch4 to FluidSynth's soundfont kits otherwise. So only one of the
two plays at a time without any re-linking.

Hits are one-shots: each voice module has its own SunVox track, and a new hit
retriggers it. Note-offs are ignored, except that a closed hat chokes an open
one (the kit's "chokes").

    uv run sunvoxdrums.py                    # run (the m00dr-sunvoxdrums user service does this)
    uv run sunvoxdrums.py --demo out_dir/    # render a short groove per kit, for listening
"""
import fcntl, json, os, subprocess, sys, threading
import numpy as np
import pathlib as _pathlib, sys as _sys
VOICE = _pathlib.Path(__file__).resolve().parent   # this voice's folder: its patches live here
_sys.path.insert(0, str(VOICE.parent / "_sunvox"))
from sunvoxlib import HERE, NOTECMD_ALL_NOTES_OFF, NOTECMD_NOTE_OFF, find_module, load, open_lib

SR, BLOCK = 48000, 256
DRUM_CHANNEL = 5                          # 0-based: m00Dr's SunVox drums are MIDI ch6
KITS = VOICE / "drumkits.json"
VOLUME = 256                              # sv_volume per slot, 0..256
SAMPLER_LOOP_TYPE = 2                     # sv_sampler_par's parameter id for the loop type
SAMPLES = _pathlib.Path.home() / "samples"  # the sample library, for kits whose samples aren't in git


def plain_wav(wav):
    """A copy of `wav` with only its format and audio (in the runtime dir): SunVox's loader
    rejects some WAVs over extra chunks, e.g. a sampler loop ("smpl") after the data."""
    import wave
    out = _pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "m00dr-samples" / wav.name
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(wav)) as src, wave.open(str(out), "wb") as dst:
        dst.setparams(src.getparams()); dst.writeframes(src.readframes(src.getnframes()))
    return out


class Kit:
    def __init__(self, sv, slot, spec):
        path = VOICE / spec["file"]
        load(sv, slot, path)
        sv.sv_volume(slot, VOLUME)
        self.sv, self.slot, self.name = sv, slot, spec["name"]
        modules = sorted({module for hits in spec["voices"].values() for module, _ in hits})
        # One SunVox track per module, so a module's next hit retriggers it.
        self.track = {module: i for i, module in enumerate(modules)}
        self.number = {module: find_module(sv, slot, module, path) for module in modules}
        # Kits built from samples outside git (see build.py's 909): load each into its Sampler.
        # A missing file leaves that voice silent rather than stopping the drums.
        for module, rel in spec.get("samples", {}).items():
            wav = SAMPLES / rel
            mod = self.number[module] - 1   # find_module gives the number + 1 that events take
            if not wav.exists() or (sv.sv_sampler_load(slot, mod, str(wav).encode(), -1) < 0
                                    and sv.sv_sampler_load(slot, mod, str(plain_wav(wav)).encode(), -1) < 0):
                print(f"kit {spec['name']}: no sample for {module} ({wav})", flush=True)
            else:
                # One-shots: these GM-kit WAVs carry sampler loop points (a "smpl" chunk)
                # that SunVox honours, so a hit would ring on forever. Loop type 0 = none.
                sv.sv_sampler_par(slot, mod, 0, SAMPLER_LOOP_TYPE, 0, 1)
        self.voices = {int(key): hits for key, hits in spec["voices"].items()}
        self.chokes = {int(key): [int(k) for k in keys] for key, keys in spec.get("chokes", {}).items()}

    def hit(self, note, vel):
        for choked in self.chokes.get(note, ()):
            for module, _ in self.voices.get(choked, ()):
                self.sv.sv_send_event(self.slot, self.track[module], NOTECMD_NOTE_OFF, 0,
                                      self.number[module], 0, 0)
        for module, lib_note in self.voices.get(note, ()):
            # SunVox velocity is 1..129.
            self.sv.sv_send_event(self.slot, self.track[module], lib_note, vel + 1,
                                  self.number[module], 0, 0)

    def all_off(self):
        self.sv.sv_send_event(self.slot, 0, NOTECMD_ALL_NOTES_OFF, 0, 0, 0, 0)


class SunVoxDrums:
    def __init__(self):
        self.sv = open_lib(SR)
        specs = json.loads(KITS.read_text())["kits"]
        self.kits = [Kit(self.sv, slot, spec) for slot, spec in enumerate(specs)]
        self.kit = 0
        self.lock = threading.Lock()  # ONE_THREAD: events and rendering must not overlap
        self.buf = np.zeros((BLOCK, 2), dtype="<f4")

    def program(self, number):
        with self.lock:
            if 0 <= number < len(self.kits) and number != self.kit:
                self.kit = number
                print(f"kit {number}: {self.kits[number].name}", flush=True)

    def note_on(self, note, vel):
        with self.lock:
            self.kits[self.kit].hit(note, vel)

    def all_off(self):
        with self.lock:
            for kit in self.kits:
                kit.all_off()

    def render(self):
        # The library mixes every open slot, so a kit switched away from rings out.
        with self.lock:
            self.sv.sv_audio_callback(self.buf.ctypes.data, BLOCK, 0, self.sv.sv_get_ticks())
        return self.buf


def open_midi():
    from alsa_midi import SequencerClient, WRITE_PORT, PortType
    client = SequencerClient("m00Dr-SunVoxDrums")
    # Keep a reference: alsa-midi deletes the port when its object is garbage-collected.
    client.drums_in = client.create_port("drums in", caps=WRITE_PORT, type=PortType.MIDI_GENERIC | PortType.APPLICATION)
    print(f"MIDI in: ALSA client {client.client_id} 'm00Dr-SunVoxDrums', port 0 'drums in'", flush=True)
    return client


def midi_loop(client, drums):
    from alsa_midi import NoteOnEvent, ControlChangeEvent, ProgramChangeEvent
    while True:
        ev = client.event_input()
        if getattr(ev, "channel", None) != DRUM_CHANNEL: continue
        if isinstance(ev, NoteOnEvent) and ev.velocity: drums.note_on(ev.note, ev.velocity)
        elif isinstance(ev, ProgramChangeEvent): drums.program(ev.value)
        elif isinstance(ev, ControlChangeEvent) and ev.param in (120, 123): drums.all_off()


def demo(out_dir):
    """Two bars of four-on-the-floor house per kit, one WAV each."""
    import wave
    from pathlib import Path
    step = 0.125  # a 16th at 120 bpm
    bar = [[36, 42], [42], [46], [42], [36, 38, 42], [42], [46], [42, 38]] * 2
    d = SunVoxDrums()   # the library initialises once per process
    for number in range(len(d.kits)):
        d.program(number); blocks = []
        for notes in bar * 2:
            for note in notes: d.note_on(note, 120 if note in (36, 38) else 90)
            for _ in range(int(step * SR / BLOCK)): blocks.append(d.render().copy())
        for _ in range(int(0.5 * SR / BLOCK)): blocks.append(d.render().copy())
        y = np.concatenate(blocks)
        path = Path(out_dir) / f"drums-{number}-{d.kits[number].name.lower()}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
            w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())
        print(f"{path}: peak {np.abs(y).max():.2f}")


def main():
    drums = SunVoxDrums()
    threading.Thread(target=midi_loop, args=(open_midi(), drums), daemon=True).start()
    # --target 0: left unlinked; mix/links.sh wires it into the mixer's drums strip.
    p = subprocess.Popen(["pw-cat", "--playback", "--raw", "--format=f32", f"--rate={SR}", "--channels=2",
                          "--target=0", f"--latency={BLOCK}",
                          "-P", "{ node.name=m00Dr-SunVoxDrums media.name=\"sunvox drums\" }", "-"], stdin=subprocess.PIPE)
    fcntl.fcntl(p.stdin.fileno(), fcntl.F_SETPIPE_SZ, 4096)   # keep the pipe from adding ~170 ms of latency
    while True:
        p.stdin.write(drums.render().tobytes())               # blocks at real-time pace


if __name__ == "__main__":
    demo(sys.argv[2]) if sys.argv[1:2] == ["--demo"] else main()
