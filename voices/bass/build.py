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

Run: uv run voices/bass/build.py [out.sunvox]   # default: fuzzbass.sunvox beside this file
"""

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


def build() -> Project:
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


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUT
    with path.open("wb") as f:
        build().write_to(f)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
