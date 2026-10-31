# m00Drc

A SunVox sound source for [m00Dr](../m00Dr), and a roller that rolls m00Dr's parameters.

## SunVox instruments

`m00Drc.sunvox` has one instrument for each m00Dr voice. Each one listens only on that
voice's MIDI channel, with "MIDI in: always" set:

| voice  | ch | SunVox chain |
|--------|----|--------------|
| Chords | 1  | poly saw pad → Room reverb |
| Bass   | 2  | mono square, 24 dB lowpass |
| Arp    | 3  | FM pluck → Delay → Room |
| Acid   | 5  | Glide → mono saw, resonant lowpass with a per-note envelope → Distortion |

```sh
uv run python -m m00drc.build_project   # writes m00Drc.sunvox
```

In SunVox:
1. Open the file with **File → Open**. SunVox ignores files opened from Finder.
2. In **Preferences → MIDI**, set the controller input to the `m00Dr` port.

## Roller

`m00drc/roller.py` reads one seeded stream of random ints from several lanes, one lane
per m00Dr control. Each lane reads the stream at its own offset in two ways:
- **lag**: lane *k* gets the value lane *k-1* had one cycle earlier, like a shift register.
- **phase**: each lane fires at its own point in the cycle, so the controls change one at a time.

The roller sends its changes through m00Dr's web remote, which must be ticked on (port 8766).

```sh
uv run python -m m00drc.roller --seed 7 --bars 4 --shift 0.25 --wait-for-play
uv run python -m m00drc.roller --lanes roll,mode,acidNoise --dry-run
```
