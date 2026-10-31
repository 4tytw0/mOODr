# /// script
# requires-python = ">=3.11"
# dependencies = ["radiant-voices>=2.0.1.2.1.4.0", "numpy", "soundfile"]
# ///
"""Generate wurli.sunvox: a DX7-style FM electric piano voiced like a Wurlitzer.

For m00Dr's chords, played by m800Dr's sunvoxkeys.py. Two 2-operator FM
layers sum, in the spirit of the DX7's stacked-pair electric pianos, then go
through the things that make a Wurlitzer a Wurlitzer:

    Reed   carrier:modulator 1:1. The modulation index falls fast after the
           attack, so each note starts bright and settles into a round, nearly
           sine body: the DX7 EP's decay.
    Bark   1:2, which gives mostly odd harmonics: the nasal, hollow bite of a
           struck reed. Short, so it's only there on the attack.
    -> Tremolo   the Wurlitzer's built-in amplitude tremolo, shallow. 5.5 Hz in
                 the file; sunvoxkeys.py sets it to 8th notes at m00Dr's tempo
                 and restarts it on each beat
    -> Amp       a little saturation, the small amp's grit
    -> Tone      low-pass, takes the FM fizz off the top
    -> Room      a short reverb

Velocity: sunvoxkeys.py sets the Bark layer's level from each note's
velocity just before the note (BARK_SOFT..BARK_HARD), so a hard hit barks and
a soft one is nearly pure reed, as on a real Wurlitzer. (A Velocity2Ctl in the
patch did this a note late: its change landed after the Bark note had started.)

Notes go to all of INPUT_MODULES (sunvoxkeys.py sends each note to each).

Run: uv run voices/chords/build.py [OUT_DIR]   # default: beside this file (all patches + patches.json)
"""

import sys
from pathlib import Path

from rv.api import Project, m
from rv.modules.base.distortion import BaseDistortion
from rv.modules.base.filter import BaseFilter
from rv.modules.base.fm import BaseFm
from rv.modules.base.lfo import BaseLfo

DEFAULT_OUT = Path(__file__).resolve().parent / "wurli.sunvox"
INPUT_MODULES = ("Reed", "Bark")
BARK_SOFT, BARK_HARD = 0, 90  # Bark carrier volume (0..256) at velocity 0 and 127, set by the player
TREMOLO_HZ = 5.5
TREMOLO_DEPTH = 70  # LFO amplitude, 0..256: measured ±21% (about 3.5 dB peak to dip)


def build_wurli() -> Project:
    p = Project()
    p.name = "m00Drc keys: Wurli"
    out = p.output
    out.x, out.y = 1100, 512

    room = p.new_module(m.Reverb, name="Room", x=900, y=512,
                        dry=256, wet=50, feedback=140, damp=200, room_size=20)
    tone = p.new_module(m.Filter, name="Tone", x=750, y=512, type=BaseFilter.Type.lp,
                        freq=5500, roll_off=BaseFilter.RollOff._12db)
    amp = p.new_module(m.Distortion, name="Amp", x=600, y=512, volume=95,
                       type=BaseDistortion.Type.saturation3, power=40)
    tremolo = p.new_module(m.Lfo, name="Tremolo", x=450, y=512, type=BaseLfo.Type.amplitude,
                           amplitude=TREMOLO_DEPTH, waveform=BaseLfo.Waveform.sin,
                           frequency_unit=BaseLfo.FrequencyUnit.ms)
    # As a period in ms: in Hz/64 units SunVox caps the rate at 256 (4 Hz).
    tremolo.freq = round(1000 / TREMOLO_HZ)
    tremolo >> amp >> tone >> room >> out

    # Envelope times are FM-module units (0..512, longer as they grow).
    p.new_module(m.Fm, name="Reed", x=250, y=420,
                 c_volume=45, m_volume=110, c_freq_ratio=1, m_freq_ratio=1,
                 m_self_modulation=24,
                 c_attack=0, c_decay=260, c_sustain=110, c_release=70,
                 m_attack=0, m_decay=90, m_sustain=40, m_release=40,
                 m_scaling_per_key=2, polyphony=16, mode=BaseFm.Mode.hq) >> tremolo
    bark = p.new_module(m.Fm, name="Bark", x=250, y=600,
                 c_volume=BARK_SOFT, m_volume=150, c_freq_ratio=1, m_freq_ratio=2,
                 m_self_modulation=0,
                 c_attack=0, c_decay=70, c_sustain=0, c_release=30,
                 m_attack=0, m_decay=40, m_sustain=0, m_release=20,
                 m_scaling_per_key=3, polyphony=16, mode=BaseFm.Mode.hq)
    bark >> tremolo
    return p


# --- Sampled sets: multisamples from the "Strings Custom Kit", as the old soundfont
# (tools/build_chords.py) cut them: one file per note, its WAV loop kept so held
# chords sustain, each root covering the keys halfway to its neighbours. zone()
# from that script reads each file (root from name + detected pitch, tuning, loop).
KIT = Path.home() / "samples/Synths/Strings Custom Kit"
# Tremolo violins left out: the files' octave can't be pinned down (names and
# detection disagree by an octave; the strong 2nd harmonic fools both detectors).
SAMPLED = ["OBX strings", "Arco strings", "Organ"]
# SunVox's Sampler ignores a sample's stored rate for pitch; it compensates through
# the sample's relative note + finetune (1/128 semitone), XM-style. Measured with the
# library's own WAV loader on a 44.1 kHz file: relative note 16, finetune 100 plays
# it at its own pitch on library note 61 (MIDI 60 with the players' NOTE_OFFSET 1).
REF_SEMIS, REF_RATE = 16 + 100 / 128, 44100


def loop_tuned(z):
    """A held note's pitch is the loop's: a single-cycle loop of L samples at rate sr
    plays sr/L Hz whatever the attack measured (the Organ's 33-sample loops at
    14962 Hz play 453 Hz, not 440: 50 cents sharp, in the old soundfont too). For
    short loops, retune the root and cents from the loop itself."""
    import math
    ls, le = z["loop"]; n = le - ls; sr = z["sr"]
    if n <= 0 or n >= sr // 20: return z
    det = z["root"] - z["cents"] / 100                       # the attack's measured pitch
    k = max(1, round(440 * 2 ** ((det - 69) / 12) * n / sr))  # cycles in the loop
    loop_midi = 69 + 12 * math.log2(sr * k / n / 440)
    root = round(loop_midi)
    return {**z, "root": root, "cents": round(-(loop_midi - root) * 100)}


def build_sampled(name: str) -> Project:
    import math, re
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    import build_chords as bc
    from rv.modules.sampler import Sampler
    pattern = dict(bc.SETS)[name]
    zones = []
    for f in sorted(KIT.rglob("*.wav")):
        mt = re.match(pattern, f.name)
        if mt: zones.append(loop_tuned(bc.zone(f, mt)))
    zones = sorted({z["root"]: z for z in zones}.values(), key=lambda z: z["root"])   # one per root
    if not zones: raise SystemExit(f"no samples for {name} in {KIT}")
    p = Project()
    p.name = f"m00Dr chords: {name}"
    out = p.output
    out.x, out.y = 900, 512
    s = p.new_module(m.Sampler, name="Sampler", x=500, y=512)
    s >> out
    roots = [z["root"] for z in zones]
    edges = [0] + [(a + b + 1) // 2 for a, b in zip(roots, roots[1:])] + [len(s.note_samples)]
    for i, z in enumerate(zones):
        smp = Sampler.Sample()
        smp.data = z["pcm"].tobytes(); smp.format = Sampler.Format.int16; smp.channels = Sampler.Channels.mono
        smp.rate = z["sr"]
        # rate compensation + put the root on its own MIDI note, + the file's fine tuning
        semis = REF_SEMIS + 12 * math.log2(z["sr"] / REF_RATE) + (60 - z["root"]) + z["cents"] / 100
        total = round(semis * 128); smp.relative_note = round(total / 128); smp.finetune = total - smp.relative_note * 128
        ls, le = z["loop"]
        smp.loop_start, smp.loop_len, smp.loop_type = ls, max(1, le - ls), Sampler.LoopType.forward
        s.samples[i] = smp
        for n in range(edges[i], edges[i + 1]): s.note_samples[n] = i
    return p


# --- Pad: SunVox-native. Two detuned saws, slow attack and release, a low-pass,
# chorus-ish width from the detune, and a hall.
def build_pad() -> Project:
    from rv.modules.base.analoggenerator import BaseAnalogGenerator as AG
    p = Project()
    p.name = "m00Dr chords: Pad"
    out = p.output
    out.x, out.y = 1100, 512
    hall = p.new_module(m.Reverb, name="Hall", x=900, y=512, dry=200, wet=110, room_size=110)
    tone = p.new_module(m.Filter, name="Tone", x=700, y=512, type=BaseFilter.Type.lp, freq=2400,
                        resonance=300, roll_off=BaseFilter.RollOff._12db)
    tone >> hall >> out
    p.new_module(m.AnalogGenerator, name="Pad", x=500, y=512, waveform=AG.Waveform.saw, volume=70,
                 polyphony=16, attack=90, release=110, sustain=True, osc2_volume=32768,
                 osc2_mode=AG.Osc2Mode.add, osc2=10) >> tone
    return p


# program -> (name, builder, file, input modules, note offset). m00Dr sends the chords
# on ch7 with a program change per sound; voices/chords/player.py loads them all.
# Note offset: MIDI n plays library note n + offset at pitch n. FM and Sampler: +1
# (measured); this AnalogGenerator: -23 (measured: the Pad; the acid player measured
# the same, the bass -11).
PATCHES = [(0, "Wurli", build_wurli, "wurli.sunvox", list(INPUT_MODULES), 1)]
PATCHES += [(i + 1, n, (lambda n=n: build_sampled(n)), n.lower().replace(" ", "-") + ".sunvox", ["Sampler"], 1)
            for i, n in enumerate(SAMPLED)]
PATCHES += [(len(PATCHES), "Pad", build_pad, "pad.sunvox", ["Pad"], -23)]
CHORD = (48, 52, 55, 60)   # level-matching chord: C major, open


def chord_rms(path, inputs, offset):
    """RMS (dB) of CHORD held 1.5 s on a patch, rendered with the SunVox library at
    the players' slot volume."""
    import numpy as np
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_sunvox"))
    from sunvoxlib import open_lib, load, find_module
    sr, blk = 48000, 2048
    sv = open_lib(sr); load(sv, 0, path); sv.sv_volume(0, 256)
    mods = [find_module(sv, 0, n, path) for n in inputs]; per = 32 // len(mods)
    for t, n in enumerate(CHORD):
        for i, mo in enumerate(mods): sv.sv_send_event(0, i * per + t, n + offset, 101, mo, 0, 0)
    buf = np.zeros((blk, 2), dtype="<f4"); xs = []
    for _ in range(int(1.5 * sr / blk)):
        sv.sv_audio_callback(buf.ctypes.data, blk, 0, sv.sv_get_ticks()); xs.append(buf.copy())
    sv.sv_deinit()
    x = np.concatenate(xs); return 20 * np.log10(np.sqrt((x ** 2).mean()) + 1e-9)


def main(argv: list[str]) -> int:
    import json
    out_dir = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUT.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = []
    for program, name, builder, file, inputs, offset in PATCHES:
        with (out_dir / file).open("wb") as f:
            builder().write_to(f)
        meta.append({"program": program, "name": name, "file": file, "inputs": inputs, "note_offset": offset,
                     "rms": chord_rms(out_dir / file, inputs, offset)})
        print(f"wrote {out_dir / file}")
    # Level: every patch's slot volume brings its chord down to the quietest one's
    # (the library's slot volume only attenuates: 0..256).
    target = min(p.pop("rms") for p in [dict(m) for m in meta])
    for p in meta:
        p["volume"] = min(256, round(256 * 10 ** ((target - p.pop("rms")) / 20)))
        print(f"  {p['name']:14} volume {p['volume']}")
    (out_dir / "patches.json").write_text(json.dumps({"patches": meta}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
