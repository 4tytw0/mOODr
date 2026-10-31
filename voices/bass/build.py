# /// script
# requires-python = ">=3.11"
# dependencies = ["radiant-voices>=2.0.1.2.1.4.0"]
# ///
"""Generate fuzzbass.sunvox: m00Dr's bass on SunVox, a reese-like saw pair and a
sine sub mixed into a fuzz.

For m800Dr's sunvoxbass.py. The oscillators are the reese's (reese.py: two
detuned saws beating against each other, plus a sine sub), here as SunVox
modules, and both go into the fuzz rather than a gentle drive:

    Saws   one AnalogGenerator, saw, with its second oscillator detuned by
           DETUNE (1/64 semitones) at equal volume: the reese beat
    Sub    a sine Generator at the saws' pitch, as in reese.py
    -> Fuzz    hard clipping, high gain: both oscillators fold into one
               buzzing tone, the sub keeping it heavy
    -> Tone    24 dB low-pass, takes the fuzz's fizz off

Run: uv run voices/bass/build.py [OUT_DIR]   # default: beside this file (fuzzbass, reese, patches.json)
"""

import json
import sys
from pathlib import Path

from rv.api import Project, m
from rv.modules.base.analoggenerator import BaseAnalogGenerator as AG
from rv.modules.base.distortion import BaseDistortion
from rv.modules.base.filter import BaseFilter
from rv.modules.base.generator import BaseGenerator as Gen

DEFAULT_OUT = Path(__file__).resolve().parent / "fuzzbass.sunvox"
INPUT_MODULES = ("Saws", "Sub")
# Osc2's detune, in 1/64 semitones (measured; SunVox stores it as 1000 + this,
# and rv takes the signed value).
# 8/64 of a semitone is ~12 cents: at 55 Hz the saws beat about 0.4 times a
# second. sunvoxbass.py overrides it per note so the beat follows the tempo.
DETUNE = 8


def build_fuzz() -> Project:
    p = Project()
    p.name = "m00Drc bass: Fuzz"
    out = p.output
    out.x, out.y = 900, 512
    tone = p.new_module(m.Filter, name="Tone", x=700, y=512, type=BaseFilter.Type.lp,
                        freq=1800, roll_off=BaseFilter.RollOff._24db)
    fuzz = p.new_module(m.Distortion, name="Fuzz", x=500, y=512, volume=60,
                        type=BaseDistortion.Type.clipping, power=200)
    fuzz >> tone >> out
    saws = p.new_module(m.AnalogGenerator, name="Saws", x=300, y=420,
                        waveform=AG.Waveform.saw, volume=90, polyphony=1, mode=AG.Mode.hq_mono,
                        attack=0, release=15, sustain=True, osc2_volume=32768,
                        osc2_mode=AG.Osc2Mode.add, osc2=DETUNE)
    saws >> fuzz
    p.new_module(m.Generator, name="Sub", x=300, y=600, waveform=Gen.Waveform.sin,
                 volume=110, attack=0, release=15, sustain=True, polyphony=1) >> fuzz
    return p


# The reese: the same Saws and Sub (so voices/bass/player.py's tempo-locked beat
# and gates drive it unchanged), voiced like voices/reference/reese.py instead of
# the fuzz: Saws -> "Tone", a resonant low-pass swept by a slow LFO (reese.py's
# 0.18 Hz) -> soft saturation. No filter envelope: the AnalogGenerator's closes
# fully as it decays (measured: only the sub was left), so reese.py's bright
# attack isn't reproduced. The sub skips the filters, straight into the saturation, so it
# stays solid under the sweep.
# Measured against reese.py's demo (spectral centroid of a held A1): it settles at
# 300-430 Hz; this sweeps ~330-560 Hz. The Filter's LFO sweeps a fixed span below
# the cutoff (about 14000 Hz x lfo_amp / 256), so a small depth on a higher base
# keeps it from closing. Level matched to the fuzz (~-8 dB RMS).
REESE = dict(tone=750, tone_res=420, lfo_hz=0.18, lfo_amp=3,
             drive_type="saturation3", drive_power=96, drive_volume=120)


def build_reese() -> Project:
    r = REESE
    p = Project()
    p.name = "m00Dr bass: Reese"
    out = p.output
    out.x, out.y = 1100, 512
    drive = p.new_module(m.Distortion, name="Drive", x=900, y=512, volume=r["drive_volume"],
                         type=BaseDistortion.Type[r["drive_type"]], power=r["drive_power"])
    tone = p.new_module(m.Filter, name="Tone", x=700, y=420, type=BaseFilter.Type.lp,
                        freq=r["tone"], resonance=r["tone_res"], roll_off=BaseFilter.RollOff._12db,
                        lfo_freq_unit=BaseFilter.LfoFreqUnit.hz_mul_0_02, lfo_freq=round(r["lfo_hz"] / 0.02),
                        lfo_amp=r["lfo_amp"])
    tone >> drive >> out
    saws = p.new_module(m.AnalogGenerator, name="Saws", x=500, y=420,
                        waveform=AG.Waveform.saw, volume=90, polyphony=1, mode=AG.Mode.hq_mono,
                        attack=0, release=15, sustain=True, osc2_volume=32768,
                        osc2_mode=AG.Osc2Mode.add, osc2=DETUNE)
    saws >> tone
    p.new_module(m.Generator, name="Sub", x=500, y=600, waveform=Gen.Waveform.sin,
                 volume=80, attack=0, release=15, sustain=True, polyphony=1) >> drive
    return p


# program -> (name, builder, file). The player loads every one; program change
# on the bass channel picks one (0 = Fuzz, the default).
PATCHES = [(0, "Fuzz", build_fuzz, "fuzzbass.sunvox"), (1, "Reese", build_reese, "reese.sunvox")]
HERE = Path(__file__).resolve().parent


def main(argv: list[str]) -> int:
    out_dir = Path(argv[1]) if len(argv) > 1 else HERE
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = []
    for program, name, builder, file in PATCHES:
        with (out_dir / file).open("wb") as f:
            builder().write_to(f)
        meta.append({"program": program, "name": name, "file": file, "inputs": ["Saws", "Sub"]})
        print(f"wrote {out_dir / file}")
    (out_dir / "patches.json").write_text(json.dumps({"patches": meta}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
