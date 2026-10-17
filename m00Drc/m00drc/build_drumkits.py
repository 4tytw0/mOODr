"""Generate three SunVox drum kits for m00Dr's drum part, and their note map.

m00Dr's drums play four General MIDI keys: 36 kick, 38 snare, 42 closed hat,
46 open hat. Each kit is a .sunvox project with a module (or a few) per
voice. drumkits.json says which module(s) each key triggers and at which
SunVox library note. m800Dr's sunvoxdrums.py loads the kits and plays them.

    Machine  909-ish: Kicker thump, triangle + noise snare, high-passed
             noise hats.
    Boom     808-ish: long low Kicker, snare through a small room, FM metal
             hats.
    Crunch   SunVox's DrumSynth through a bitcrusher and a low-pass:
             lo-fi breaks.

Library notes: the SunVox library plays note n at the pitch of MIDI note
n + 23 (measured on the acid patch: library note 37 is middle C), so a tuned
voice that should sound at MIDI m is listed at m - 23. DrumSynth repeats its
layout every octave: the first 4 notes are bass drums, the next 3 hats, the
last 5 snares, with the octave setting length and pitch (mapped by
rendering every note).

Run: uv run python -m m00drc.build_drumkits [out_dir]   # default ../synths
"""

import json
import sys
from pathlib import Path

from rv.api import Project, m
from rv.modules.base.analoggenerator import BaseAnalogGenerator as AG
from rv.modules.base.filter import BaseFilter
from rv.modules.base.fm import BaseFm
from rv.modules.base.generator import BaseGenerator as Gen
from rv.modules.base.kicker import BaseKicker

DEFAULT_OUT = Path(__file__).resolve().parent.parent.parent / "synths"
KICK, SNARE, CLOSED_HAT, OPEN_HAT = 36, 38, 42, 46
LIB_OFFSET = 23  # MIDI note = library note + LIB_OFFSET
KICK_CLICK_RELEASE, KICK_CLICK_VOLUME = 6, 70   # the Machine kick's attack transient
# The Kicker sounds well below its note, so its notes are set by ear-proxy
# (zero-crossing rate over the hit). The 909's is a punch: 36 starts ~125 Hz
# and drops fast; the 808's, 27, is lower and long.
KICKER_NOTE_909, KICKER_NOTE_808 = 36, 27


def lib(midi_note: int) -> int:
    return midi_note - LIB_OFFSET


def noise_hit(project, name, release, filter_type, freq, x, y, waveform=AG.Waveform.white_noise,
              volume=110):
    """A burst of filtered noise that dies away on its own."""
    return project.new_module(
        m.AnalogGenerator, name=name, x=x, y=y, waveform=waveform, volume=volume,
        polyphony=1, mode=AG.Mode.hq_mono, attack=0, release=release, sustain=False,
        filter=filter_type, f_freq=freq, f_exponential_freq=False, osc2_volume=0)


def machine() -> tuple[Project, dict]:
    p = Project()
    p.name = "m00Drc kit: Machine"
    out = p.output
    out.x, out.y = 900, 512
    # A punch, not a woop: a fast pitch drop (high acceleration), a short body
    # (90% of the energy gone by ~115 ms, was ~325) and a noise click on top.
    p.new_module(m.Kicker, name="Kick", x=300, y=200, waveform=BaseKicker.Waveform.sin,
                 volume=140, release=55, boost=450, acceleration=600, no_click=False) >> out
    noise_hit(p, "Kick click", KICK_CLICK_RELEASE, AG.Filter.hp_12db, 2500, 500, 200,
              volume=KICK_CLICK_VOLUME) >> out
    p.new_module(m.Generator, name="Snare body", x=300, y=350, waveform=Gen.Waveform.triangle,
                 volume=60, attack=0, release=40, sustain=False, polyphony=1) >> out
    noise_hit(p, "Snare noise", 90, AG.Filter.hp_12db, 1500, 500, 350, volume=80) >> out
    # Hats: plain noise through one high-pass. (The AnalogGenerator's
    # exponential envelope cut them to a 3 ms click.)
    hats = p.new_module(m.Filter, name="Hat filter", x=700, y=500,
                        type=BaseFilter.Type.hp, freq=7000, roll_off=BaseFilter.RollOff._24db)
    hats >> out
    for name, release, volume, x in (("Hat", 50, 70, 300), ("Open hat", 260, 55, 500)):
        p.new_module(m.Generator, name=name, x=x, y=500, waveform=Gen.Waveform.noise,
                     volume=volume, attack=0, release=release, sustain=False, polyphony=1) >> hats
    return p, {
        KICK: [("Kick", KICKER_NOTE_909), ("Kick click", lib(60))],
        SNARE: [("Snare body", lib(52)), ("Snare noise", lib(60))],
        CLOSED_HAT: [("Hat", lib(60))],
        OPEN_HAT: [("Open hat", lib(60))],
    }


def boom() -> tuple[Project, dict]:
    p = Project()
    p.name = "m00Drc kit: Boom"
    out = p.output
    out.x, out.y = 900, 512
    p.new_module(m.Kicker, name="Kick", x=300, y=200, waveform=BaseKicker.Waveform.sin,
                 volume=140, release=400, boost=0, acceleration=300, no_click=True) >> out
    room = p.new_module(m.Reverb, name="Snare room", x=700, y=350,
                        dry=256, wet=60, feedback=120, damp=200, room_size=24)
    room >> out
    p.new_module(m.Generator, name="Snare body", x=300, y=350, waveform=Gen.Waveform.triangle,
                 volume=80, attack=0, release=30, sustain=False, polyphony=1) >> room
    noise_hit(p, "Snare noise", 25, AG.Filter.hp_12db, 2000, 500, 350,
              waveform=AG.Waveform.pink_noise) >> room
    # Metallic hats: a high carrier with an inharmonic, self-modulating
    # modulator, high-passed so only the sizzle is left.
    hats = p.new_module(m.Filter, name="Hat filter", x=700, y=500,
                        type=BaseFilter.Type.hp, freq=7000, roll_off=BaseFilter.RollOff._24db)
    hats >> out
    for name, decay, x in (("Hat", 20, 300), ("Open hat", 110, 500)):
        p.new_module(m.Fm, name=name, x=x, y=500, c_volume=60, m_volume=256,
                     c_freq_ratio=1, m_freq_ratio=7, m_self_modulation=200,
                     c_attack=0, c_decay=decay, c_sustain=0, c_release=decay // 2,
                     m_attack=0, m_decay=decay, m_sustain=0, m_release=decay // 2,
                     polyphony=1, mode=BaseFm.Mode.hq_mono) >> hats
    return p, {
        KICK: [("Kick", KICKER_NOTE_808)],
        SNARE: [("Snare body", lib(57)), ("Snare noise", lib(60))],
        CLOSED_HAT: [("Hat", lib(84))],
        OPEN_HAT: [("Open hat", lib(84))],
    }


def crunch() -> tuple[Project, dict]:
    p = Project()
    p.name = "m00Drc kit: Crunch"
    out = p.output
    out.x, out.y = 900, 512
    drums = p.new_module(m.DrumSynth, name="Drums", x=300, y=512, volume=110, polyphony=4,
                         bass_power=256, bass_length=170, snare_tone=160, hihat_length=64)
    crush = p.new_module(m.Distortion, name="Crush", x=500, y=512, volume=200, power=0,
                         bit_depth=6, freq=16000)
    tone = p.new_module(m.Filter, name="Tone", x=700, y=512, type=BaseFilter.Type.lp,
                        freq=9000, roll_off=BaseFilter.RollOff._12db)
    drums >> crush >> tone >> out
    return p, {
        KICK: [("Drums", 13)],         # octave 1 bass drum
        SNARE: [("Drums", 44)],        # octave 3 snare, short
        CLOSED_HAT: [("Drums", 31)],   # octave 2 hat, a tick
        OPEN_HAT: [("Drums", 42)],     # octave 3 hat, open
    }


KITS = [("Machine", "drumkit-machine.sunvox", machine),
        ("Boom", "drumkit-boom.sunvox", boom),
        ("Crunch", "drumkit-crunch.sunvox", crunch)]


def main(argv: list[str]) -> int:
    out_dir = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUT
    kits = []
    for name, filename, build in KITS:
        project, voices = build()
        with (out_dir / filename).open("wb") as f:
            project.write_to(f)
        kits.append({"name": name, "file": filename,
                     "voices": {str(key): [list(v) for v in hits] for key, hits in voices.items()},
                     # A closed hat cuts an open one off, as on a real hi-hat.
                     "chokes": {str(CLOSED_HAT): [str(OPEN_HAT)]}})
        print(f"wrote {out_dir / filename}")
    (out_dir / "drumkits.json").write_text(json.dumps({"kits": kits}, indent=2) + "\n")
    print(f"wrote {out_dir / 'drumkits.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
