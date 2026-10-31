"""Generate m00Drc.sunvox: one SunVox instrument per m00Dr voice.

m00Dr sends each voice on its own MIDI channel (see moodr/playback.py), so
each instrument here listens on exactly that channel with "MIDI in: always"
set, which is what lets all four play at once instead of only the module
that happens to be selected in SunVox.

    Chords  ch1  poly saw pad       -> Reverb
    Bass    ch2  mono square + LP   (dry, centre)
    Arp     ch3  FM pluck           -> Delay -> Reverb
    Acid    ch5  Glide -> mono saw -> resonant LP that snaps open on every
                 note -> saturation with a light bit crush

Run: uv run python -m m00drc.build_project [out.sunvox]
     uv run python -m m00drc.build_project --acid [acid303.sunvox]   # ch5 voice only
"""

import sys
from pathlib import Path

from rv.api import Project, m
from rv.modules.base.analoggenerator import BaseAnalogGenerator as AG
from rv.modules.base.distortion import BaseDistortion
from rv.modules.base.filter import BaseFilter
from rv.modules.base.fm import BaseFm

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "m00Drc.sunvox"
DEFAULT_ACID_OUT = DEFAULT_OUT.with_name("acid303.sunvox")

# m00Dr's channel map, 1-based as SunVox shows it.
CHORD_CH, BASS_CH, ARP_CH, ACID_CH = 1, 2, 3, 5

# Acid filter, in Filter-module units. The cutoff rests at ACID_CUTOFF Hz; each
# note kicks it to between the IMPULSE values (Hz, by velocity) and it falls
# back at ACID_RESPONSE (measured: 3 is ~120 ms back to rest, 8 ~50 ms).
ACID_CUTOFF, ACID_RESONANCE, ACID_RESPONSE = 400, 1250, 3
ACID_IMPULSE_MIN, ACID_IMPULSE_MAX = 2500, 6000
IMPULSE_CTL = 7  # the Filter's Impulse controller, 1-based


def listen(module, channel: int):
    module.midi_in_always = True
    module.midi_in_channel = channel
    return module


def deaf(module):
    """For modules fed by a note module upstream, not straight from MIDI."""
    module.midi_in_never = True
    return module


def add_acid(project: Project, out, x: int, y: int):
    """The 303 voice (ch5): Glide -> mono saw -> resonant LP that snaps open
    on every note -> Distortion. Returns the Glide module, which takes the notes."""
    # m00Dr's slides overlap the next note-on with the previous note-off; the
    # Glide module turns that legato into a pitch sweep for a mono generator.
    glide = listen(project.new_module(
        m.Glide, name="Acid glide ch5", x=x, y=y,
        response=600, polyphony=False, reset_on_first_note=True,
    ), ACID_CH)
    acid = deaf(project.new_module(
        m.AnalogGenerator, name="Acid 303", x=x + 200, y=y,
        waveform=AG.Waveform.saw, volume=100, polyphony=1,
        mode=AG.Mode.hq_mono, attack=0, release=8, sustain=True,
        osc2_volume=0,
    ))
    # The squelch: the generator's own filter envelope closes to 0 Hz, but a
    # 303's settles back to the cutoff. The Filter module's impulse does that:
    # writing `impulse` jumps the cutoff there, then it eases back to `freq`
    # at `response`. Velocity2Ctl writes it on every note-on, scaled by
    # velocity, so m00Dr's accents open the filter further.
    squelch = project.new_module(
        m.Filter, name="Acid filter", x=x + 400, y=y,
        type=BaseFilter.Type.lp, roll_off=BaseFilter.RollOff._24db,
        freq=ACID_CUTOFF, resonance=ACID_RESONANCE, impulse=ACID_IMPULSE_MAX,
        response=ACID_RESPONSE,
    )
    kick = listen(project.new_module(
        m.Velocity2Ctl, name="Acid accent ch5", x=x + 200, y=y - 150,
        out_min=round(ACID_IMPULSE_MIN * 0x8000 / 14000),
        out_max=round(ACID_IMPULSE_MAX * 0x8000 / 14000),
        out_controller=IMPULSE_CTL,
    ), ACID_CH)
    # Saturation into a light bit and sample-rate crush: grit on top of the
    # squelch (10-bit, 22 kHz; 8-bit/16 kHz measured as noticeably harsher).
    drive = project.new_module(
        m.Distortion, name="Acid drive", x=x + 600, y=y, volume=90,
        type=BaseDistortion.Type.saturation3, power=140, bit_depth=10, freq=22050)
    glide >> acid >> squelch >> drive >> out
    kick >> squelch
    return glide


def build_acid() -> Project:
    """The 303 voice alone, for a headless SunVox (m800Dr's sunvox303.py)."""
    project = Project()
    project.name = "m00Drc acid"
    project.output.x, project.output.y = 900, 512
    add_acid(project, project.output, x=300, y=512)
    return project


def build() -> Project:
    project = Project()
    project.name = "m00Drc"
    project.initial_bpm = 120
    out = project.output
    out.x, out.y = 900, 512

    reverb = project.new_module(
        m.Reverb, name="Room", x=700, y=380,
        dry=256, wet=70, feedback=200, damp=180, room_size=64)
    reverb >> out

    # -- Chords (ch1): slow, wide, polyphonic --------------------------------
    chords = listen(project.new_module(
        m.AnalogGenerator, name="Chords ch1", x=300, y=200,
        waveform=AG.Waveform.saw, volume=70, polyphony=8,
        mode=AG.Mode.hq, attack=40, release=90, sustain=True,
        osc2=7, osc2_volume=20000,
        filter=AG.Filter.lp_24db, f_freq=3200, f_resonance=200,
    ), CHORD_CH)
    chords >> reverb

    # -- Bass (ch2): one note at a time, round and dry ---------------------------
    bass = listen(project.new_module(
        m.AnalogGenerator, name="Bass ch2", x=300, y=420,
        waveform=AG.Waveform.square, duty_cycle=512, volume=110, polyphony=1,
        mode=AG.Mode.hq_mono, attack=0, release=12, sustain=True,
        filter=AG.Filter.lp_24db, f_freq=900, f_resonance=300,
    ), BASS_CH)
    bass >> out

    # -- Arp (ch3): short FM pluck into a delay ----------------------------------
    arp = listen(project.new_module(
        m.Fm, name="Arp ch3", x=300, y=620,
        c_volume=80, m_volume=90, c_freq_ratio=1, m_freq_ratio=3,
        c_attack=0, c_decay=60, c_sustain=0, c_release=40,
        m_attack=0, m_decay=40, m_sustain=0, m_release=20,
        polyphony=4, mode=BaseFm.Mode.hq,
    ), ARP_CH)
    delay = project.new_module(
        m.Delay, name="Arp delay", x=500, y=620, dry=256, wet=110, feedback=12000)
    arp >> delay >> reverb

    add_acid(project, out, x=100, y=820)

    return project


def main(argv: list[str]) -> int:
    args = argv[1:]
    acid_only = "--acid" in args
    args = [a for a in args if a != "--acid"]
    path = Path(args[0]) if args else (DEFAULT_ACID_OUT if acid_only else DEFAULT_OUT)
    with path.open("wb") as f:
        (build_acid() if acid_only else build()).write_to(f)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
