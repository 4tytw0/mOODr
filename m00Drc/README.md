# m00Drc

SunVox instruments for [m00Dr](https://github.com/4tytw0/mOODr), and a roller that rolls m00Dr's
parameters and the acid synth's sound from one phase-shifted stream of random ints.

## SunVox instruments

`m00drc/build_project.py` writes two SunVox projects:

- `m00Drc.sunvox`: one instrument per m00Dr voice, each listening only on its MIDI channel, with
  "MIDI in: always" set.

  | voice  | ch | SunVox chain |
  |--------|----|--------------|
  | Chords | 1  | poly saw pad → Room reverb |
  | Bass   | 2  | mono square, 24 dB lowpass |
  | Arp    | 3  | FM pluck → Delay → Room |
  | Acid   | 5  | Glide → mono saw → resonant lowpass kicked open per note (harder on accents) → Distortion |

  To use it in the SunVox app: open it with **File → Open** (SunVox ignores files opened from
  Finder), then set **Preferences → MIDI** to the `m00Dr` port.
- `acid303.sunvox` (`--acid`): the acid voice alone. m800Dr's `sunvox303.py` plays it headless, as
  `synths/acid303.sunvox`.

```sh
uv run python -m m00drc.build_project          # m00Drc.sunvox
uv run python -m m00drc.build_project --acid   # acid303.sunvox; copy it to ../synths/
```

The acid filter settings are at the top of `build_project.py`.

## Drum kits

`m00drc/build_drumkits.py` writes three kits for m800Dr's `sunvoxdrums.py`, and `drumkits.json`,
which says which module(s) and SunVox library note each of m00Dr's drum keys (36 kick, 38 snare,
42 closed hat, 46 open hat) plays. A closed hat chokes the open one.

| Kit | Sound |
|---|---|
| Machine | 909-ish: Kicker thump, triangle + noise snare, high-passed noise hats |
| Boom | 808-ish: long low Kicker, snare in a small room, FM metal hats |
| Crunch | SunVox's DrumSynth through a 6-bit crusher and a low-pass |

```sh
uv run python -m m00drc.build_drumkits   # writes ../synths/drumkit-*.sunvox and drumkits.json
```

## Roller

`m00drc/roller.py` draws one seeded stream of 7-bit random ints (0..127, a MIDI CC value), one per
cycle of `--bars` bars at m00Dr's tempo. Lanes read the stream:

- **m00Dr's controls**: Roll, scale, acid noise and wide deviation, acid randomize. (Not the
  arp's pattern, rate or octave, or the stabs toggle: those are left to the player.) The acid lanes change the
  acid line's notes, so they fire together on the bar line every 4 bars, randomize included,
  rather than at their own points in the cycle. These are set over m00Dr's web remote (port 8766).
- **Sound**: `acid.cutoff`, `acid.resonance`, `acid.decay`, `acid.accent`, `acid.glide` and
  `acid.drive`. These are sent as CCs on ch5 to `sunvox303.py`, which eases each one in.

Each lane reads the stream shifted in two ways:
- **Lag:** each lane gets the value the lane before it in the same chain had one cycle earlier,
  like a shift register.
- **Phase:** each lane fires at its own point in the cycle, so the changes come one at a time.

Roll is held back: after a roll, the next one waits 2 to 4 minutes, set by the int that triggered
it. The first roll waits 2 minutes after the roller starts.

Knobs on the Launch Control XL (via the mixer's `controls` output; CC numbers assume the factory
template):

| Knob | CC | Controls |
|---|---|---|
| Column 4 top | 16 | shift, 0..1 of a cycle |
| Column 4 middle | 32 | bars per cycle, 1..8 |
| Column 4 bottom | 52 | active lanes (fully left stops rolling) |
| Column 6 top | 18 | seed |

On the desktop it runs as the `m00drc-roller` user service (`../systemd/m00drc-roller.service`),
with `--wait-for-play`.

```sh
uv run python -m m00drc.roller --seed 7 --bars 2 --shift 0.25 --wait-for-play
uv run python -m m00drc.roller --lanes roll,mode,acid.cutoff --dry-run --no-midi
```
