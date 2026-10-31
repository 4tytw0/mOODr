# /// script
# requires-python = ">=3.11"
# dependencies = ["radiant-voices>=2.0.1.2.1.4.0"]
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

Run: uv run voices/chords/build.py [out.sunvox]   # default: wurli.sunvox beside this file
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


def build() -> Project:
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


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUT
    with path.open("wb") as f:
        build().write_to(f)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
