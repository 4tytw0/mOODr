"""A bank of SunVox patches for one mixer strip: one library session, one slot
per patch, a program change picks which patch new notes go to.

The patches are listed in the voice folder's patches.json (written by its
build.py): {"bank": B, "patches": [{"program": P, "name": ..., "file": ...,
"inputs": [module names]}]}. m00Dr picks a sound with bank select (CC 0) then a
program change, GS style; a program change only switches when the last CC 0 was
this bank's B (or the file has no "bank"). Notes already sounding finish on the
patch they started on.

One session renders every slot into one stereo output, so a bank is one mixer
strip. Different strips are different sessions (different processes today).

    run(VOICE_DIR, channel=2, client="m00Dr-SunVoxArp", port="arp in", node="m00Dr-SunVoxArp")
    python <player.py> --demo out.wav     # every patch, a short phrase each, offline
"""
import fcntl, json, subprocess, sys, threading, wave
from pathlib import Path

import numpy as np
from sunvoxlib import NOTECMD_ALL_NOTES_OFF, NOTECMD_NOTE_OFF, find_module, load, open_lib

SR, BLOCK = 48000, 256
NOTE_OFFSET = 1    # MIDI note n -> library note n + 1 plays pitch n (patches are tuned that way)
VOLUME = 256       # sv_volume per slot, 0..256
TRACKS = 32        # SunVox takes events on tracks 0..31


class Bank:
    def __init__(self, voice_dir: Path):
        meta = json.loads((voice_dir / "patches.json").read_text())
        self.bank_number = meta.get("bank")
        sv = self.sv = open_lib(SR)
        self.patches = []                    # per slot: (name, [module numbers + 1])
        self.slot_of = {}                    # program -> slot
        for slot, spec in enumerate(meta["patches"]):
            path = voice_dir / spec["file"]
            load(sv, slot, path)
            sv.sv_volume(slot, VOLUME)
            self.patches.append((spec["name"], [find_module(sv, slot, m, path) for m in spec["inputs"]]))
            self.slot_of[spec["program"]] = slot
        self.current = 0
        self.cc0 = None
        self.lock = threading.Lock()          # ONE_THREAD: events and rendering must not overlap
        self.held = {}                        # MIDI note -> (slot, track)
        self.free = {slot: list(range(TRACKS // len(mods))) for slot, (_, mods) in enumerate(self.patches)}
        self.buf = np.zeros((BLOCK, 2), dtype="<f4")

    @property
    def name(self):
        return self.patches[self.current][0]

    def bank_select(self, value):
        self.cc0 = value

    def program(self, number):
        if self.bank_number is not None and self.cc0 not in (None, self.bank_number):
            return False
        with self.lock:
            if number in self.slot_of:
                self.current = self.slot_of[number]
                return True
        return False

    def _send(self, slot, track, note, vel=0):
        mods = self.patches[slot][1]
        per = TRACKS // len(mods)
        for i, module in enumerate(mods):
            self.sv.sv_send_event(slot, i * per + track, note, vel, module, 0, 0)

    def note_on(self, note, vel):
        with self.lock:
            if note in self.held:            # restruck while held: release it where it is
                slot, track = self.held.pop(note)
                self._send(slot, track, NOTECMD_NOTE_OFF); self.free[slot].append(track)
            slot = self.current
            if self.free[slot]:
                track = self.free[slot].pop(0)
            else:                            # all busy: take this slot's oldest held note
                oldest = next(n for n, (s, _) in self.held.items() if s == slot)
                track = self.held.pop(oldest)[1]
            self.held[note] = (slot, track)
            self._send(slot, track, note + NOTE_OFFSET, vel + 1)   # SunVox velocity is 1..129

    def note_off(self, note):
        with self.lock:
            if note in self.held:
                slot, track = self.held.pop(note)
                self._send(slot, track, NOTECMD_NOTE_OFF); self.free[slot].append(track)

    def all_off(self):
        with self.lock:
            self.held.clear()
            for slot, (_, mods) in enumerate(self.patches):
                self.free[slot] = list(range(TRACKS // len(mods)))
                self.sv.sv_send_event(slot, 0, NOTECMD_ALL_NOTES_OFF, 0, 0, 0, 0)

    def render(self):
        with self.lock:
            self.sv.sv_audio_callback(self.buf.ctypes.data, BLOCK, 0, self.sv.sv_get_ticks())
        return self.buf


def midi_loop(client, bank, channel):
    from alsa_midi import NoteOnEvent, NoteOffEvent, ControlChangeEvent, ProgramChangeEvent
    while True:
        ev = client.event_input()
        if getattr(ev, "channel", None) != channel: continue
        if isinstance(ev, NoteOnEvent):
            bank.note_on(ev.note, ev.velocity) if ev.velocity else bank.note_off(ev.note)
        elif isinstance(ev, NoteOffEvent): bank.note_off(ev.note)
        elif isinstance(ev, ProgramChangeEvent):
            if bank.program(ev.value): print(f"patch: {bank.name}", flush=True)
        elif isinstance(ev, ControlChangeEvent):
            if ev.param == 0: bank.bank_select(ev.value)
            elif ev.param in (120, 123): bank.all_off()


def demo(voice_dir: Path, out: str, phrase=(60, 64, 67, 72), step=0.18):
    """Every patch in turn: an arpeggio, then a held chord, offline into a WAV."""
    bank = Bank(voice_dir); frames = []
    def run(seconds):
        for _ in range(int(seconds * SR / BLOCK)): frames.append(bank.render().copy())
    for number, slot in sorted(bank.slot_of.items()):
        bank.program(number)
        for n in phrase: bank.note_on(n, 100); run(step); bank.note_off(n)
        for n in phrase[:3]: bank.note_on(n, 90)
        run(1.0); bank.all_off(); run(0.4)
    x = np.concatenate(frames)
    with wave.open(out, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    print(f"wrote {out}: {len(bank.patches)} patches, {len(x) / SR:.1f} s, peak {np.abs(x).max():.2f}")


def run(voice_dir: Path, channel: int, client: str, port: str, node: str):
    """The player: MIDI in on `channel` (0-based), audio out to PipeWire as `node`,
    left unlinked (mix/links.sh wires it into its mixer strip)."""
    if sys.argv[1:2] == ["--demo"]:
        return demo(voice_dir, sys.argv[2])
    from alsa_midi import SequencerClient, WRITE_PORT, PortType
    bank = Bank(voice_dir)
    seq = SequencerClient(client)
    seq.port_in = seq.create_port(port, caps=WRITE_PORT, type=PortType.MIDI_GENERIC | PortType.APPLICATION)
    print(f"MIDI in: ALSA client {seq.client_id} {client!r}, port 0 {port!r}; "
          f"{len(bank.patches)} patches, starting on {bank.name}", flush=True)
    threading.Thread(target=midi_loop, args=(seq, bank, channel), daemon=True).start()
    p = subprocess.Popen(["pw-cat", "--playback", "--raw", "--format=f32", f"--rate={SR}", "--channels=2",
                          "--target=0", f"--latency={BLOCK}", "-P", f"{{ node.name={node} }}", "-"],
                         stdin=subprocess.PIPE)
    fcntl.fcntl(p.stdin.fileno(), fcntl.F_SETPIPE_SZ, 4096)   # keep the pipe from adding ~170 ms of latency
    while True:
        p.stdin.write(bank.render().tobytes())                # blocks at real-time pace
