# m800Dr

The audio side of a Dirtywave M8 + [m00Dr](https://github.com/4tytw0/mOODr) rig, running on a Linux
desktop (PipeWire, KDE). m00Dr generates the music as MIDI. The synths here play it, a mixer
balances and dirties the parts on a Launch Control XL, and an **audio server** streams the result to
any browser on the LAN with a Lissajous scope and the mixer beside it.

```
m00Dr ──MIDI──▶ FluidSynth   ch1 chords · ch3 arp · ch4 drums ─┐
            ├─▶ reese.py     ch2 bass ─────────────────────────┤
            └─▶ SunVox       ch5 acid (sunvox303.py) ──────────┤
M8 USB audio ──────────────────────────────────────────────────┤
Launch Control XL ──▶ mixer.py: a strip per part, faders, ◀────┘
                      drive, sidechain ──▶ m800dr_mix ──▶ server.py :8808
```

## Pages (audio server, port 8808)

- **`/`** is the player. **Listen** streams the mix. The scope is an XY plot turned 45° (mono lies
  flat along the width), with an M/S mode, auto gain and full screen (`f` or double-click). The mixer
  is a panel to its right (**MIXER** toggles it), so opening it never stops the audio.
- **`/mixer`** is the mixer on its own, e.g. on a tablet while a TV shows the scope.

## Mixer (`mixer.py`)

One stereo strip per part. Fader N drives the part on MIDI channel N, except the drums (MIDI ch 4),
which sit in column 7 of the Launch Control XL:

| Fader | 1 | 2 | 3 | 5 | 7 | 8 |
|---|---|---|---|---|---|---|
| Strip | chords | bass | arp | acid | drums | M8 |

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
- **`reese.py`** is a numpy reese bass: two detuned saws, a sub, a filter and drive. The saws' beat
  follows the chords on ch1: consonant chords pulse slowly, dissonant ones beat faster.
- **`sunvox303.py`** plays the acid on a headless SunVox library: `synths/acid303.sunvox`, a 303-shaped
  patch built by [m00Drc](m00Drc/) (Glide → mono saw → resonant low-pass kicked open per note,
  harder on accents → drive). 7-bit CCs on ch5 move its key controls (74 cutoff, 71 resonance,
  75 filter decay, 76 accent depth, 5 glide time, 77 drive), eased in over half a second; m00Drc's
  roller sends them. The library (`synths/sunvox_lib/sunvox.so`, from warmplace.ru's
  `sunvox_lib-*.zip`) is git-ignored.
- **m00Drc's roller** (`m00Drc/`, user service `m00drc-roller`) rolls m00Dr's controls over its web
  remote and sends the sound CCs above, from one phase-shifted stream of 7-bit random ints. Roll
  fires at most once every 2-4 minutes. Its shift, bars, lanes and seed are on Launch Control XL
  knobs; see `m00Drc/README.md`.
- **Open303** (JC303 in a headless Carla rack, `synths/open303.carxp`) played ch5 before SunVox. Its
  unit is still in `systemd/`; `synths/links.sh` says how to switch back.
- **`synths/links.sh`** keeps the PipeWire graph wired (m00Dr → synths → mixer → mix bus, and
  controllers → mixer), re-linking anything that restarts.

## Building the sounds

The soundfonts are git-ignored (the samples aren't licensed for redistribution). Rebuild them on a
machine that has the GAMEBREAKS sample pack:

```sh
tools/build_gamebreaks.sh                              # synths/gamebreaks.sf2 (stabs + phase_sine)
uv run tools/build_chords.py synths/chords.sf2         # strings/organ/guitar (Strings Custom Kit)
uv run tools/build_drumkit.py synths/gamedrums.sf2     # kits sliced from the raw drum breaks
python3 tools/build_open303.py                         # synths/open303.carxp (edit PATCH first)
```

## Running

Every piece is a systemd user service. Unit copies are in `systemd/`; install them into
`~/.config/systemd/user/` and enable them:

```sh
systemctl --user enable --now m00dr-fluidsynth m00dr-reese m00dr-sunvox303 m00drc-roller \
    m800dr-mixer m00dr-synth-links m800dr-mix-m8 m800dr-audio
```

Python tooling is run with [uv](https://docs.astral.sh/uv/). The scripts declare their dependencies
inline. Current state, gotchas and next steps are in [`HANDOFF.md`](HANDOFF.md).
