# m800Dr

The audio side of a Dirtywave M8 + [m00Dr](https://github.com/4tytw0/mOODr) rig, running on a Linux
desktop (PipeWire, KDE). m00Dr generates the music as MIDI. The synths here play it, a mixer
balances and dirties the parts on a Launch Control XL, and an **audio server** streams the result to
any browser on the LAN with a Lissajous scope and the mixer beside it.

```
m00Dr ──MIDI──▶ FluidSynth      ch1 chords · ch3 arp · ch4 drums ────┐
            ├─▶ sunvoxbass.py   ch2 fuzz bass ───────────────────────┤
            ├─▶ sunvox303.py    ch5 acid ────────────────────────────┤
            ├─▶ sunvoxdrums.py  ch6 SunVox kits (drums strip) ───────┤
            ├─▶ sunvoxkeys.py   ch7 FM Wurli (chords strip) ─────────┤
            └─▶ breaks.py       ch8 chopped breaks, on the clock ────┤
m00Drc roller ──▶ m00Dr's web remote · acid sound CCs · breaks ──────┤
Launch Control XL ──▶ mixer.py: a strip per part, faders, ◀────┘
                      drive, sidechain ──▶ m800dr_mix ──▶ server.py :8808
```

## Status

As of 2026-10-17. Everything below is running on the desktop; nothing new has been **judged by ear**
yet, only measured. The SunVox work is in open, stacked PRs (merge bottom-up: m800Dr #1 → #2 → #4 →
#5, mOODr #2 → #3); the player change is m800Dr #3.

| Part | Plays on | State |
|---|---|---|
| Chords | FluidSynth (ch1) or **SunVox Wurli** (ch7) | live, Wurli selected; tremolo synced to tempo (PR #4) |
| Bass | **SunVox fuzz bass** (ch2) | live; replaced reese.py; swell once per beat (PR #5) |
| Arp | FluidSynth (ch3) | live, unchanged |
| Acid | **SunVox 303** (ch5) | live; saturation + 10-bit crush (PR #1, #4) |
| Drums | FluidSynth kits (ch4) or **SunVox Machine/Boom/Crunch** (ch6) | live, Machine selected; punchy kick (PR #5); double-time hats (mOODr #3) |
| Roller | m00Drc → m00Dr's web remote + acid CCs | live; Roll at most every 2-4 min, acid notes every 4 bars (PR #1, #2) |
| Player | `/` on :8808 | AUTO buffer, ~23 ms instead of 60 (PR #3) |
| LCXL knobs | roller CC 16/32/52/18 | wired; CC numbers assumed, unverified on the device |
| Wi-Fi power save | desktop `wlan0` | **still on**; turning it off needs root (see HANDOFF) |
| reese.py | — | kept in `synths/reference/` for its BPM-locked wobble (not a service). Open303 removed 2026-10-31 |

## Video

[▶ Triple Mutant Pullout](docs/video/triple-mutant-pullout.mp4): a 10 s fractal render (2026-10-03,
21 MB). GitHub shows it as a file you can play or download.

## Pages (audio server, port 8808)

- **`/`** is the player. **Listen** streams the mix. The scope is an XY plot turned 45° (mono lies
  flat along the width), with an M/S mode, auto gain and full screen (`f` or double-click). The mixer
  is a panel to its right (**MIXER** toggles it), so opening it never stops the audio.
- **`/mixer`** is the mixer on its own, e.g. on a tablet while a TV shows the scope.

## Breaks (`breaks.py`, fader 8)

64 GAMEBREAKS breaks (`tools/build_breaks.py` picks the best-gridded, one per hexagram; the audio is
git-ignored, so build it on the Mac and copy `synths/breaks/` over) played on m00Dr's MIDI clock as 16
slices per bar. Each landed Roll picks the break (the roller sends the cast's hexagram as a program
change on ch8); every chord change re-chops the bar from six freshly cast lines (CC 20/21): changing
lines stutter, swap or echo a pair of 16ths, static lines leave it.

## Mixer (`mixer.py`)

One stereo strip per part. Fader N drives the part on MIDI channel N, except the drums (MIDI ch 4),
which sit in column 7 of the Launch Control XL:

| Fader | 1 | 2 | 3 | 5 | 7 | 8 |
|---|---|---|---|---|---|---|
| Strip | chords | bass | arp | acid | drums | breaks |

- **Volume, then drive:** up to 80% of the throw is volume (unity at 80%). Above that, the strip blends
  into an asymmetric fuzz that gets harder toward the top.
- **Sidechain:** the drums' low end (the kick) ducks chords, bass, arp and acid, by up to about −8 dB
  by default, recovering over a few hundred ms. Set it with the page slider or the middle knob in
  the drums column (column 7, CC 35).
- **Inputs:** faders move on CC 7 on the strip's channel, on Launch Control XL CC 77–84 (any
  channel), by dragging on the page (double-click = unity), or via `POST /mixer/control`. Other
  knob CCs pass through to m00Dr's "m00Dr Control" input. The top knob in the drums column (column 7, CC 19)
  morphs the drum pattern from house to two-step to broken beats.
- **Persistence:** fader positions survive restarts.

### Launch Control XL (mk2)

Use factory template 1 (MIDI channel 9). If the controller is plugged into the desktop, it's picked
up automatically. If it's on a Mac, run `uv run tools/midi_bridge.py`, which forwards its CCs to the
audio server over the LAN. A launch agent for it is in `tools/com.m800dr.midi-bridge.plist`; macOS
must allow it under Privacy & Security → Local Network. See `HANDOFF.md` for the controller's
SysEx and LED reference.

## Synths

- **FluidSynth** (`synths/fluidsynth.cfg`) outputs to JACK, one output group per channel. Chords and
  arp use `gamebreaks.sf2` (stabs and a phaser sine) or `chords.sf2` (15 looped string/organ/guitar
  multisamples; m00Dr's Chord sound menu picks one), drums use `gamedrums.sf2` (32 kits, a new one
  per Roll).
- **`synths/reference/reese.py`** is a numpy reese bass: two detuned saws, a sub, a filter and drive. The saws' beat
  follows the chords on ch1: consonant chords pulse slowly, dissonant ones beat faster. Not a service:
  `sunvoxbass.py` plays the bass now; this is kept as the reference for porting the wobble to SunVox.
- **`sunvox303.py`** plays the acid on a headless SunVox library: `synths/acid303.sunvox`, a 303-shaped
  patch built by [m00Drc](m00Drc/) (Glide → mono saw → resonant low-pass kicked open per note,
  harder on accents → drive). 7-bit CCs on ch5 move its key controls (74 cutoff, 71 resonance,
  75 filter decay, 76 accent depth, 5 glide time, 77 drive), eased in over half a second; m00Drc's
  roller sends them. The library (`synths/sunvox_lib/sunvox.so`, from warmplace.ru's
  `sunvox_lib-*.zip`) is git-ignored.
- **`sunvoxdrums.py`** plays m00Dr's drums when its drum source is a SunVox kit: three kits built by
  m00Drc (`synths/drumkit-*.sunvox`, note map in `synths/drumkits.json`): **Machine** (909-ish),
  **Boom** (808-ish, FM hats, a small room on the snare) and **Crunch** (SunVox's DrumSynth,
  bitcrushed). m00Dr sends those drums on ch6 with a program change 0-2 for the kit, and the
  soundfont kits on ch4, so FluidSynth and SunVox never both play; both feed the drums strip.
- **`sunvoxkeys.py`** plays m00Dr's chords when its chord sound is "SunVox Wurli": a DX7-style FM
  electric piano voiced like a Wurlitzer (`synths/wurli.sunvox`, built by m00Drc): a 1:1 FM reed
  whose brightness decays after the attack, a short 1:2 odd-harmonic bark set by velocity, ~5.5 Hz
  tremolo, a little amp saturation and a small room. m00Dr sends those chords on ch7; both it and
  FluidSynth feed the chords strip.
- **`sunvoxbass.py`** plays m00Dr's bass (ch2), in place of `reese.py`: `synths/fuzzbass.sunvox`
  (built by m00Drc), the reese's oscillators in SunVox (a saw with a detuned second oscillator, plus
  a sine sub at the same pitch) mixed into hard-clipping fuzz, then a low-pass. The saws' beat
  follows m00Dr's clock: their detune is set per note so the tone swells once per beat.
- The Wurli's tremolo also follows m00Dr's clock (8th notes, phase reset each beat), and the Machine
  kit's kick is short and punchy with a noise click.
- **m00Drc's roller** (`m00Drc/`, user service `m00drc-roller`) rolls m00Dr's controls over its web
  remote and sends the sound CCs above, from one phase-shifted stream of 7-bit random ints. Roll
  fires at most once every 2-4 minutes. Its shift, bars, lanes and seed are on Launch Control XL
  knobs; see `m00Drc/README.md`.
- **`synths/links.sh`** keeps the PipeWire graph wired (m00Dr → synths → mixer → mix bus, and
  controllers → mixer), re-linking anything that restarts.

## Building the sounds

The soundfonts are git-ignored (the samples aren't licensed for redistribution). Rebuild them on a
machine that has the GAMEBREAKS sample pack:

```sh
tools/build_gamebreaks.sh                              # synths/gamebreaks.sf2 (stabs + phase_sine)
uv run tools/build_chords.py synths/chords.sf2         # strings/organ/guitar (Strings Custom Kit)
uv run tools/build_drumkit.py synths/gamedrums.sf2     # kits sliced from the raw drum breaks
```

## Running

Every piece is a systemd user service. Unit copies are in `systemd/`; install them into
`~/.config/systemd/user/` and enable them:

```sh
systemctl --user enable --now m00dr-fluidsynth m00dr-sunvoxbass m00dr-sunvox303 m00dr-sunvoxdrums \
    m00dr-sunvoxkeys m00dr-breaks m00drc-roller \
    m800dr-mixer m00dr-synth-links m800dr-audio
```

Python tooling is run with [uv](https://docs.astral.sh/uv/). The scripts declare their dependencies
inline. Current state, gotchas and next steps are in [`HANDOFF.md`](HANDOFF.md).
