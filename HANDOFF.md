# m800Dr / m00Dr on the desktop — handoff

_Last updated 2026-10-17 (SunVox synths for acid, drums, chords and bass; m00Drc's roller; player latency; all in open PRs, see "Done 2026-10-16/17")._ This is the M8 rig, which now runs entirely on the Linux desktop
(`Desktop`, 192.168.1.17, CachyOS, KDE Wayland) instead of the MacBook. The Dirtywave M8
(Teensy / M8 Headless) is plugged in here over USB.

**In progress:** m00Dr's parts now mostly play on headless **SunVox** players: acid (ch5), bass
(ch2, replacing reese.py), drums when a SunVox kit is picked (ch6) and chords when "SunVox Wurli" is
picked (ch7). FluidSynth still plays the arp (ch3) and the soundfont chord/drum choices. m00Drc's
roller rolls m00Dr's controls and the acid's sound. Everything is live on the desktop and measured,
but **not yet judged by ear**.
**Nothing from 2026-10-16/17 is merged:** it's in stacked PRs (m800Dr #1-#5, mOODr #2-#3), and the
desktop runs it from **uncommitted copies** (see "Code / files"). See "Resume here" at the bottom.

```
 m00Dr GUI ──MIDI port "m00Dr" (notes + MIDI Beat Clock)
   │ ├─(pw-link, m00dr-synth-links)──▶ FluidSynth [ch1 chords, ch3 arp, ch4 drums] ──────────┤
   │ ├─▶ sunvoxbass.py  (ch2 bass: detuned saws + sub → fuzz; beat swells with the clock) ───┤
   │ ├─▶ sunvox303.py   (ch5 acid: Glide → saw → kicked filter → saturation + 10-bit crush) ──┤
   │ ├─▶ sunvoxdrums.py (ch6: Machine / Boom / Crunch kits, program 0-2) ─▶ drums strip ─────┤
   │ ├─▶ sunvoxkeys.py  (ch7: FM Wurli, tremolo synced to the clock) ─▶ chords strip ─────────┤
   │ │ web remote :8765/:8766 (iPad) ◀── m00Drc roller (lanes; sound CCs → sunvox303) ◀── LCXL knobs
   │ │ (reese.py and Open303 are disabled; links.sh says how to switch back)                   │
 M8 USB audio ──(pw-loopback, m800dr-mix-m8)─────────────────────────────────────────────────┤
 Launch Control XL ──(on the Mac: tools/midi_bridge.py → POST :8808/mixer/control;
                      on the desktop: any USB MIDI, links.sh)──▶ m800Dr-Mixer                         │
                                    m800Dr-Mixer (mixer.py): strip per part, fader = volume, ◀┘
                                    top of fader = fuzz ──▶ PipeWire null sink "m800dr_mix"
                                                                │ .monitor
                 m800dr-audio "audio server" :8808 ◀────────────┘  / player + scope, /mixer faders
```

## What runs where

| Piece | How it runs | Survives reboot? |
|---|---|---|
| m00Dr GUI | user service `m00dr` (`uv run python main.py` in `~/code/mOODr`; unit copy in `systemd/`), wanted by `graphical-session.target`, `Restart=on-failure`. The desktop launcher now runs `systemctl --user start m00dr` | at Plasma login. SDDM autologin has no `User=`, so a reboot waits at the login screen |
| m00Dr web remote | inside the GUI; `~/.config/m00Dr/m00Dr.conf` → `[web_remote] enabled=true` | with the GUI |
| Mix bus `m800dr_mix` | `~/.config/pipewire/pipewire.conf.d/m800dr-mix.conf` (null sink, 48 kHz, low priority so the M8 stays the default sink) | yes |
| M8 audio → mix | user service `m800dr-mix-m8` (`pw-loopback`, pinned targets). `Restart=always` since 2026-10-10: at boot it ran before the M8 node existed and exited 0. Unit copy in `systemd/` | yes (enabled) |
| Audio server | user service `m800dr-audio` → `~/code/m800Dr/server.py` (`/` player, `/mixer` fader page polling `/mixer/state`), `SOURCE = "m800dr_mix.monitor"` | yes (enabled) |
| Mixer | user service `m800dr-mixer` → `mixer.py` (JACK client `m800Dr-Mixer`). Strips (MIDI ch / fader): chords 1/1, bass 2/2, arp 3/3, acid 5/5, drums 4/7, m8 8/8. Moves on CC 7 on the strip's channel or Launch Control XL CC 77-84 (any channel). Unity at 80% of travel (`KNEE`); above it, asymmetric tanh fuzz. Positions saved in `~/.local/state/m800dr/mixer.json`; live state in `$XDG_RUNTIME_DIR/m800dr-mixer.json` | yes (enabled) |
| FluidSynth (ch1 chords, ch3 arp, ch4 drums) | user service `m00dr-fluidsynth` (unit copy in `systemd/`). Config is `synths/fluidsynth.cfg` (router passes ch1/ch3/ch4 only); fonts are `synths/GeneralUser-GS.sf2`, `synths/gamebreaks.sf2` (font 2, bank offset 1), `synths/chords.sf2` (font 3, bank offset 2) and `synths/gamedrums.sf2` (font 4, bank 128, 32 kits). Startup: chords preset 10 `phase_sine`, arp preset 1 `toa_f`, drums kit 0; m00Dr then sets chords/arp from its sound menus and the kit per Roll. JACK, 4 output groups (chords l/r_00, arp _02, drums _03; 2 fx pairs per group), gain 1.5. TCP shell on :9800 (no `nc`; use python sockets) | yes (enabled) |
| SunVox bass (ch2) | user service `m00dr-sunvoxbass` → `sunvoxbass.py` (`synths/fuzzbass.sunvox`; ALSA client `m00Dr-SunVoxBass`, `pw-cat` → bass strip). Mono, last-note priority; per-note osc2 detune from m00Dr's clock = one fuzz swell per beat | yes (enabled) |
| SunVox acid (ch5) | user service `m00dr-sunvox303` → `sunvox303.py` (`synths/acid303.sunvox`; client `m00Dr-SunVox303` → acid strip). CCs 74/71/75/76/5/77 on ch5 = cutoff/resonance/decay/accent/glide/drive, eased over 0.5 s | yes (enabled) |
| SunVox drums (ch6) | user service `m00dr-sunvoxdrums` → `sunvoxdrums.py` (`synths/drumkit-*.sunvox` + `drumkits.json`, one SunVox slot per kit; program change 0-2 picks; client `m00Dr-SunVoxDrums` → drums strip) | yes (enabled) |
| SunVox keys (ch7) | user service `m00dr-sunvoxkeys` → `sunvoxkeys.py` (`synths/wurli.sunvox`; client `m00Dr-SunVoxKeys` → chords strip). Tremolo = 8th notes at the clock's tempo, phase reset each beat | yes (enabled) |
| m00Drc roller | user service `m00drc-roller` → `m00Drc/` (`uv run python -u -m m00drc.roller --wait-for-play`). Web remote :8766 for m00Dr's controls; ALSA client `m00Drc-Roller` (`knobs in` ← mixer controls, `sound out` → sunvox303) | with m00dr (`WantedBy=m00dr.service`) |
| Reese bass (ch2) | `m00dr-reese` → `reese.py`. **Disabled 2026-10-16** (sunvoxbass replaces it) | no |
| Open303 acid (ch5) | **Disabled 2026-10-16** (sunvox303 replaces it). Was: user service `m00dr-open303` → `carla-rack --no-gui synths/open303.carxp` (rack: internal MIDI Channel Filter passing ch5 only → JC303 LV2). JACK client `m00Dr-Open303` via `CARLA_CLIENT_NAME`; `PIPEWIRE_QUANTUM=256/48000` (Carla asserts if the buffer size changes). Regenerate the project with `python3 tools/build_open303.py [--dump]` (uses Carla's backend; sets the starting patch in `PATCH`, strips `<ExternalPatchbay>`). Unit copy in `systemd/` | yes (enabled) |
| Graph links | user service `m00dr-synth-links` → `synths/links.sh` (re-runs `pw-link` for the pairs in `LINKS` every 2 s: m00Dr → synths, synths and M8 → mixer strips, mixer → `m800dr_mix`; plus every USB MIDI device except Midi Through/M8 → `m800Dr-Mixer:faders`) | yes (enabled) |
| m00Dr → M8 notes | user service `m00dr-m8-midi` → `~/code/mOODr/m8_midi_route.sh` (aconnect) | **disabled** 2026-10-10: the user wants FluidSynth only |
| Audio → HDMI | user service `m8-loopback` | enabled, but HDMI isn't a PipeWire sink right now, so it has nowhere to play |
| Network MIDI | `rtpmidid` user service | yes |
| Anbernic link | `aseqnet-anbernic` user service | restart loop (handheld offline); suspected cause of the earlier rtmidi ENOMEM crash. `systemctl --user disable --now aseqnet-anbernic` |
| Firewall | ufw default-deny; LAN allowed on tcp 8765:8766 and 8808 | yes |

Retired: the rtmidi bridge units `m00dr-bridge-out`/`-in` (crashed with ALSA seq ENOMEM; replaced by
the aconnect/pw-link keepers). Transport is fine: the user starts m00Dr from the iPad remote.

## Code / files

- **`~/code/m800Dr/`** (git repo since 2026-10-10; the Mac clone is `~/Desktop/Code-ish/m800Dr`, origin `cacheos:code/m800Dr`): `server.py`, `index.html` (player with the Lissajous scope:
  the square panel above Listen; XY or M/S; full screen with FULL, double-click or `f`; hard-refresh
  if a cached old page shows), `reese.py`, `synths/` (`fluidsynth.cfg`, `links.sh`; `*.sf2`/`*.wav` are
  git-ignored), `tools/` (soundfont recipe: `build_gamebreaks.sh` rebuilds `gamebreaks.sf2` on the Mac,
  where the samples live), `systemd/` (unit copies).
- **`~/code/mOODr/`**: the user's repo `4tytw0/mOODr`, checked out on **`sunvox-wurli`** (mOODr PR #3,
  stacked on #2 `sunvox-drum-kits` → `linux-alsa-ports`). `m8_midi_route.sh` is **untracked**.
  Channels in `moodr/playback.py`: CHORD/BASS/ARP/DRUM/ACID = 0/1/2/3/4, plus
  `SUNVOX_DRUM_CHANNEL` 5 (ch6) and `SUNVOX_CHORD_CHANNEL` 6 (ch7).
- **`~/code/m800Dr/` on the desktop is on `master` with ~22 uncommitted files**: copies of everything in
  m800Dr PRs #1-#5 (and #3's `index.html`). Before pulling a merged branch there, stash or remove them
  (`git stash -u`), or git will refuse to overwrite untracked files.
- **`m00Drc/`** (in m800Dr since PR #1): builders for every SunVox patch (`build_project.py` acid +
  4-voice project, `build_drumkits.py`, `build_keys.py`, `build_bass.py`) and the roller. Its own uv
  project (`cd m00Drc && uv sync`). `sunvoxlib.py` (repo root) loads the library and has
  `ClockFollower`; the SunVox library itself (`synths/sunvox_lib/sunvox.so`) is git-ignored.
- Python tooling: **uv only** (user preference).

## Verified tonight

- FluidSynth channel split: test notes on ch1 and ch3 are loud in the mix; ch5 stays silent
  (peak 14 of 32768). Live m00Dr playback was reaching FluidSynth.
- The :8808 stream carries the mix: FluidSynth and M8 audio both appear in `/stream`.
- The M8 is now producing real audio (rms ~11k, peaks near full scale). Earlier it was digital
  silence because nothing was playing. **Levels:** M8 + synths can clip the mix. Lower the M8
  mixer or FluidSynth `-g` (now 1.5).

## Done 2026-10-10 (late): mixer, faders page, deeper acid, drums

- **Acid** (`tools/build_open303.py` `PATCH`): saw, Cutoff 0.18, Resonance 0.62, Bass Comp 0.7,
  Filter Drive 0.75, JC303 overdrive on (Drive 0.6, Dry/Wet 0.8). Not yet judged by ear.
- **Carla gotcha:** if Carla starts while the graph is at 1024 frames it asserts
  (`nframes == pData->bufferSize`) and stays **silent** until restarted. The mixer and FluidSynth
  units set `PIPEWIRE_LATENCY=256/48000` so the graph stays at 256.
- **Drums:** `tools/build_drumkit.py` slices `gamebreaks_raw` breaks at onsets, sorts hits by
  spectrum (kick/snare/rim/hats/toms/crash/perc) and writes `synths/gamedrums.sf2`: kit 0 best-of,
  kits 1-15 one break each (names in `fluidsynth.cfg`). type1/type2 are dulled; raw has the hats.
  Rebuild on the Mac (samples live there), copy to cacheos. **Nothing plays ch10 yet**: m00Dr has
  no drum part.
- **Mixer verified** with CCs via Midi Through (CC 7 per channel and CC 77-84); the Launch Control XL
  itself is untested (it's on the Mac). JACK-Client MIDI data is a cffi buffer: `bytes(data)` first.
- Stuck notes: restarting synths or links while m00Dr plays can drop note-offs. Fix: send CC 123/120
  on all channels (Midi Through → synths).
- Renamed the :8808 pages "Audio Server" (`index.html`) with a MIXER link to `/mixer`.

## Done 2026-10-10 (later): faders work, m00Dr drums, scope rotated

- **Faders verified** from the Launch Control XL **on the Mac** (factory template, MIDI ch 9, faders
  CC 77-84). `tools/midi_bridge.py` forwards its CCs to `POST /mixer/control`; server.py relays
  them to mixer.py over UDP 127.0.0.1:9810. The `/mixer` page faders are draggable (double-click =
  unity) and post to the same endpoint.
- **Mac bridge persistence: not solved.** It's running as a `nohup` process from a terminal. The
  launch agent `~/Library/LaunchAgents/com.m800dr.midi-bridge.plist` (copy in `tools/`) gets
  `EHOSTUNREACH` because macOS Local Network privacy blocks it. Allow it under System Settings →
  Privacy & Security → Local Network, then
  `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.m800dr.midi-bridge.plist`. Or move the
  controller to the desktop, where links.sh wires it directly.
- **m00Dr drums** (`~/code/mOODr`, branch `linux-alsa-ports`): `DRUM_CHANNEL = 3` (MIDI ch4), a 16-step
  house pattern (kick 36, snare 38, hats 42/46) varied by an I Ching cast, re-cast on every Roll.
  There's a Drums toggle + Randomize in the GUI and the web remote, and 7 new tests (209 pass).
  The FluidSynth drum kit moved from ch10 to ch4 to match.
- **Scope:** XY mode is turned 45° (x = L+R, y = R−L), so mono lies flat across the width, and the
  x axis scales to the screen width.

## Done 2026-10-10 (latest): drum mutation knob, mixer inside the player

- **Drum mutation:** the Launch Control XL's top knob in the drums column (now column 7, CC 19, any
  channel) morphs m00Dr's drums: house (0) → UK garage two-step (middle: the kick skips beats 2/4,
  swing on the off-16ths) → broken breakbeat (full). Each Roll keeps one cast, so the knob morphs
  it reversibly. Path: controller → mixer.py (CCs that aren't faders go out on
  `m800Dr-Mixer:controls`) → m00Dr's always-open input "m00Dr Control" → the Drums slider (also in
  the GUI and the web remote). Drum step 0 now lands on the bar line. 214 tests pass.
- **External clock sync is now remembered** (`[clock] external_sync` in `~/.config/m00Dr/m00Dr.conf`),
  and it's **off**: nothing sends clock on the desktop. With it on, Play only sounds bar 1's
  chords and bass, and arp/acid/drums never step.
- **The mixer is a panel to the right of the scope** (open by default, MIXER button toggles it,
  iframe of `/mixer?embed`), so it never stops the audio. Desktop layout; mobile isn't a priority. server.py ignores query strings now.

## Done 2026-10-10 (final): sidechain, README, committed

- **Sidechain** in mixer.py: the drums strip (post-fader, 150 Hz low-passed key) ducks chords,
  bass, arp and acid. Instant attack (2 ms smoothing), 150 ms release, full duck when the key reaches
  0.25. Amount 0-1 (default 0.6 ≈ −8 dB) via the mixer page slider, `{"sidechain": x}` on
  `/mixer/control`, or CC 35 (the middle knob in the drums column, now column 7; factory layout **assumed**). The
  amount is saved with the faders. Verified: 4 kicks on ch4 → −8.0 dB on the tonal strips.
- `README.md` added. m800Dr committed on cacheos and pushed to `github` (private); the Mac clone
  pulled. mOODr's drums/knob/sync work is committed on `linux-alsa-ports` and pushed.

## Done 2026-10-10 (night): a new drum kit on every Roll

- `tools/build_drumkit.py` now also writes 16 **mixed kits** (presets 16-31): each role drawn from a
  different break among the 12 best candidates (seeded, `--seed`). 16-23 `mix`, 24-27 `driven` and
  28-31 `extreme` (kick/snare/toms from `gamebreaks_type1_driven`/`_extreme`). Crashes all come from
  the ddpdoj breaks (only ones with crashes). Rebuilt `gamedrums.sf2` (32 kits, 4.1 MB) and deployed.
- m00Dr (`linux-alsa-ports`, **uncommitted** on cacheos): each Roll sends a program change on ch4 to
  a different kit (`DRUM_KITS = 32`, `PlaybackEngine.randomize_drum_kit`); Play re-sends the current
  kit. The drums label shows `· kit N` (GUI and web remote). 216 tests pass; verified via the web
  remote that Roll moves FluidSynth's ch4 preset.

## Done 2026-10-10 (night): drums on LCXL column 7

- The mixer's drums strip is now **fader 7** (still MIDI ch 4 / CC 7 on ch 4). Drum knobs moved with it:
  sidechain `SIDECHAIN_CC = 35` (middle row) and m00Dr `DRUM_MUTATION_CC = 19` (top row), both from
  the **assumed** factory layout (top 13-20, middle 29-36). Faders 4 and 6 are free.

## Done 2026-10-11: chord sounds, chord-following reese beat

- **`synths/chords.sf2`** (`tools/build_chords.py`, 15 presets, 25 MB, git-ignored): multisampled,
  looped strings/organ/guitar from `SAMPLES/Synths/Strings Custom Kit` (OBX, P5, Arco, Section,
  Stereo, Trans, Storm arco, Viola section, Solo violin, CES, CDG, RnB, Tremolo violins, Organ,
  Acoustic guitar). Roots come from the filename's pitch class plus the detected octave and cents
  (SWVla files are ~1.35 semitones flat of their names: wrong header rate). Loaded as font 3 with bank
  offset 2, so **gamedrums is now font 4** (`select 3 4 128 <kit>`). The ROMpler Chords and DX-7
  folders were skipped: every file there is a whole chord (the user chose single-note sounds only).
- **m00Dr Chord sound menu** (GUI performance row + web remote `chordSound`): `CHORD_SOUNDS` in
  `playback.py` = (name, bank, program); sends CC 0 = bank, then a program change, on the chord
  channel, and again on Play. Remembered in `[sound] chords`; nothing is sent until a sound is picked
  (so the Circuit/M8 setups aren't touched). The arp has the same menu (`arpSound`, ch3,
  `[sound] arp`); both share `SOUNDS`. 220 tests pass. Levels not balanced by ear yet.
- **Mute toggles show their channel** ("Chords ch1", "Bass ch2", "Arp ch3", "Drums ch4", "Acid ch5"),
  updated by the Bass→Ch2 toggle; the web remote takes the labels from the app (`label` in the
  snapshot). mOODr README updated (five voices, sounds, drums).
- **Open303 went quiet on 2026-10-11:** the acid fader (5) was at 0, and Carla's buffer-size assertion
  fired again on restart (`v1 256, v2 1024`) though it still played (test note peak 0.32 at unity).
- **reese.py beat follows the chords:** it now also reads ch1 (chords) and sets the saws' beat in Hz
  from each chord's roughness (Sethares/Plomp-Levelt over 8 harmonics per note): stable/high chords
  pulse slowly (0.3 Hz), low or diminished ones faster (~3 Hz), clusters up to 6 Hz; 0.25 s glide.
  `DETUNE` is gone; tune `BEAT_MIN/MAX`, `ROUGH_LO/HI`. Logs `chord [...]: beat x Hz` per chord.

## Done 2026-10-16/17: SunVox synths, roller, latency

All in open PRs, stacked: **m800Dr** #1 `sunvox-303-acid` → master, #2 `sunvox-drum-kits` → #1,
#4 `sunvox-wurli` → #2, #5 `sunvox-bass` → #4; #3 `stream-latency` → master on its own.
**mOODr** #2 `sunvox-drum-kits` → `linux-alsa-ports`, #3 `sunvox-wurli` → #2. mOODr #1 (double-time
drums) was **closed**: the user asked for normal speed back. Merge bottom-up.

- **SunVox app on the Mac:** 2.1.4d in /Applications (1.9.6c kept as `SunVox-1.9.6c.app`). The
  Homebrew cask is disabled (Gatekeeper); it came from warmplace.ru. The app ignores files opened
  from Finder: use File → Open. `m00Drc.sunvox` has the 4 voices on ch1/2/3/5 for the app.
- **Acid (ch5):** Glide → saw → Filter whose `impulse` is written by a Velocity2Ctl on every note
  (the AnalogGenerator's filter envelope closes to 0 Hz, a 303's doesn't) → saturation 140 into
  10-bit/22 kHz (energy >3 kHz 2.3% → 14%).
- **Drums:** three generated kits; m00Dr's **Drums source** menu (Soundfont / SunVox Machine / Boom /
  Crunch) moves the drums to ch6 for SunVox. Machine's kick was made punchy on request (90% energy by
  115 ms, was 325; noise click on top). **Hats are double time** (32nds, twice per bar; mOODr #3);
  kick/snare stay on 16ths.
- **Chords:** "SunVox Wurli" in the chord sound menu moves chords to ch7: two FM layers (1:1 reed,
  1:2 bark set per note from velocity), tremolo synced to m00Dr's clock (8th notes), saturation,
  low-pass, room.
- **Bass:** sunvoxbass replaced reese: detuned saw pair + same-pitch sine sub into hard-clip fuzz;
  the detune is set per note so the swell is once per beat (half a beat cycle per beat, since a
  cycle swells twice through the fuzz). The reese's chord-following beat is gone with it.
- **Roller** (`m00Drc/m00drc/roller.py`): one seeded stream of 7-bit ints read by lanes shifted in lag
  and phase. Lanes now: roll, scale, acid noise/wide/randomize, and the six acid sound CCs. **Not**
  rolled, at the user's request: arp pattern, arp rate, octave, stabs. Roll fires at most every 2-4
  min; acid-note lanes fire together every 4 bars on the bar line. Knobs: LCXL column 4 CC 16/32/52
  (shift/bars/lanes) and column 6 CC 18 (seed), **assumed** factory CCs, unverified on the device.
- **Player latency** (m800Dr #3, deployed): the desktop path is 3-8 ms note → `/stream`; Wi-Fi
  jitter was ≤16 ms; the fixed 60 ms buffer was most of the delay. AUTO buffer (20 ms floor, ±0.4%
  rate trim) replays a real trace at ~23 ms with no dropouts. **Wi-Fi power save is still on** on
  `wlan0`: run `sudo nmcli connection modify q-5G-2 802-11-wireless.powersave 2 && sudo iw dev wlan0
  set power_save off` (needs the password).
- **SunVox library gotchas** (all measured; they cost time):
  - Without `sv_set_event_t(slot, 1, 0)` events are timestamped into the future and never play.
  - Events only work on **tracks 0-31**; a note on track 32 is silent. Players split 32 tracks
    across their input modules.
  - `sv_init` once per process.
  - Pattern controller values (`sv_send_event` ctl) are 0..0x8000 across the range; use
    `sv_set_module_ctl_value(..., scaled=0)` for real values.
  - Pitch offsets differ by module: through Glide (acid) MIDI n = library n + 23; AnalogGenerator,
    Generator and FM played directly: n − 11 (Generator/AG) and n + 1 (FM, Wurli). The Kicker sounds
    well below its note; set it by measurement.
  - LFO Hz/64 rate caps at 256 (4 Hz): use the ms unit. A Velocity2Ctl's change lands after the
    note it came from has started, so set per-note levels from the player instead.
  - AnalogGenerator `osc2` is 1/64 semitones, stored 1000 + value (rv takes the signed value).
- **Don't restart m00Dr while it plays** without saying so: two deploy restarts on 2026-10-16
  looked like a crash to the user (transport stops, window reopens, tempo resets to 120). Restarting
  the SunVox players only cuts their part for ~1 s.

## Launch Control XL notes (mk2: USB 1235:0061)

From Novation's programmer's reference (launch-control-xl-programmers-reference-guide.pdf):
- 16 templates: user 0-7 (editable in the Novation editor), factory 8-15 (fixed). A factory template
  sends on the MIDI channel matching its slot, so factory template 1 = **ch 9** (that's what ours sends).
- 24 knobs (3 rows × 8), 8 faders, 24 buttons. The 16 "channel" buttons have red/green (amber)
  LEDs; the 4 arrows have red LEDs; Device/Mute/Solo/Record Arm have yellow LEDs. Buttons send a
  note or CC at 127 on press and 0 on release.
- LEDs via SysEx (works on any template): `F0 00 20 29 02 11 78 <template> <index> <value> F7`, with
  several index/value pairs allowed. Index: 0-7 top knobs, 8-15 middle knobs, 16-23 bottom knobs,
  24-31 top buttons, 32-39 bottom buttons, 40-43 Device/Mute/Solo/Arm, 44-47 arrows.
  Value = 16×green + red + 12 (each colour 0-3); e.g. 15 red, 60 green, 63 amber, 12 off.
- Change template: `F0 00 20 29 02 11 77 <template> F7` (the device sends the same message when you
  switch). Reset LEDs: `B<n> 00 00`.
- The factory CC numbers aren't in the reference. Seen on the device: faders = CC 77-84. Assumed
  (common factory-1 layout, **not yet seen**): top knobs CC 13-20, so the drums column's top knob
  is CC 19 (the drums moved to column 7 on 2026-10-10). Check it: turn that knob and look at the "last unmapped CC" line on the mixer page.

## Resume here

1. Log in to Plasma on the desktop (the box was at the **lightdm** greeter after the reboot, not
   SDDM as noted earlier; autologin isn't working). `m00dr` starts with the session.
2. Check: `systemctl --user is-active m00dr m00dr-fluidsynth m00dr-sunvoxbass m00dr-sunvox303 m00dr-sunvoxdrums m00dr-sunvoxkeys m00drc-roller m00dr-synth-links m800dr-mixer m800dr-audio`
   and `pw-link -l | grep -A2 '^m800Dr-Mixer'`.
3. **Listen** to the SunVox parts (none judged by ear yet): Wurli tremolo depth, fuzz-bass level vs
   the kick, the punchier Machine kick, Boom/Crunch, the acid's crush. Faders 3 (arp) and 5 (acid)
   were at 0 on 2026-10-16. Patch settings are at the top of each `m00Drc/m00drc/build_*.py`.
4. Turn Wi-Fi power save off (command in "Done 2026-10-16/17"), then reload the player.
5. Review and merge the stacked PRs bottom-up, then put the desktop checkouts back on clean branches.
6. Move the Launch Control XL to the desktop; check its faders move the strips on
   `http://192.168.1.17:8808/mixer` (factory template; if a fader shows as "last unmapped CC",
   adjust `FADER_CCS` in `mixer.py`).
7. Balance levels across the strips. M8 (fader 8) is unplugged right now, so `m800dr-mix-m8`
   restart-loops harmlessly.
8. Listen to the 32 kits (Roll, or `select 3 4 128 <kit>` on the :9800 shell); reseed the mixes
   (`build_drumkit.py --seed`) if they repeat too much.
9. Try the chord/arp sound menus; balance the new `chords.sf2` patches against Phase sine.
10. (reese.py is disabled; only if switching back: judge its beat, tune `BEAT_*`/`ROUGH_*`.)
11. Check the knobs really send their assumed CCs once the controller is on the desktop: column 7
    (19 drum mutation, 35 sidechain) and the roller's column 4/6 (16, 32, 52, 18). The roller's
    journal (`journalctl --user -fu m00drc-roller`) prints a `knob:` line per CC it takes.
12. mOODr PRs #2/#3 target `linux-alsa-ports`; that branch itself still has no PR into `main`.

## Done 2026-10-10: Open303

- Installed `jc303-lv2` 0.13.0, `carla` 2.5.10, `pipewire-jack` (libjack is now PipeWire's).
- Verified with test notes via `aseqsend -p 14:0` → `Midi Through` → `m00Dr-Open303:events-in`:
  ch1 and ch3 notes left the mix silent (peak 0); a ch5 note peaked at 5979 of 32768 (about −15 dBFS).
- The remote login shell on cacheos is **fish**: wrap POSIX snippets in `sh -c` / `ssh cacheos sh -s`.

## Done 2026-10-10: sounds and scope

- **`gamebreaks.sf2`**: built from the Mac's `~/Documents/PRODUCTION/SAMPLES/GAMEBREAKS/gamestabs`
  (the other folders are drum breaks). Presets 0-9 are one-shot stabs, each mapped across the keyboard
  at its detected root; preset 10 `phase_sine` is a generated loop (sine, soft-clipped, 4-stage
  phaser, one 1.6 s sweep per loop). Rebuild with `tools/build_gamebreaks.sh`, then copy to cacheos.
  FluidSynth applies only 0.4x of an SF2's initialAttenuation, which is why `build_sf2.py` uses 220 cB.
- **Scope** (`index.html`): deep purple trace with a neon green glow and a black core; expanding,
  fading echoes (`ECHOES`, `ECHO_EVERY`, `ECHO_GROW`); 0.25 ms Haas offset (`HAAS`) to widen shapes;
  graticule rings and crosshair removed.
- **Not pushed anywhere**: pushing from the Mac to cacheos is refused (non-bare repo). Commit on
  cacheos and `git pull` on the Mac, or set `receive.denyCurrentBranch updateInstead` on cacheos.
- Now that Carla is installed, a plugin synth through Carla could replace `reese.py` if wanted.

## Open issues

- **Desktop runs uncommitted copies** of m800Dr PRs #1-#5; `~/code/mOODr` is on the PR branch
  `sunvox-wurli`. Tidy both once merged.
- **LCXL knob CCs are assumed** (roller 16/32/52/18, drum mutation 19, sidechain 35); the
  controller hasn't been on the desktop to check.
- **Restart loops:** `m800dr-mix-m8` (M8 unplugged, >100k restarts) and `aseqnet-anbernic`
  (handheld offline). Disable until the devices are back?
- The roller times its cycles from m00Dr's BPM, not its MIDI clock, so it drifts slowly against the
  bar; and in external-sync mode m00Dr doesn't forward clock, so the Wurli tremolo and bass swell
  hold the last tempo.

- **HDMI sink missing** from PipeWire, so `m8-loopback` can't play anywhere. Nobody has looked into
  it; the TV is fed by the browser stream instead.
- **Serial / m8c:** the user isn't in the `uucp` group (`sudo usermod -aG uucp emin0r` +
  re-login). That's needed for m8c, m8.run and screen mirroring.
- Open a PR for `linux-alsa-ports` when happy; decide whether `m8_midi_route.sh` is still worth
  committing.

## Ideas queued

- **Instrument control surface:** a web page sending MIDI CCs, bound via the M8's MIDI MAPPING
  view (could also drive JC303's knobs through Carla).
- Other synths for m00Dr: Surge XT / Vital / Dexed (through Carla), SuperCollider/Pd, Web Audio
  synths fed by relayed MIDI, the Novation Circuit (m00Dr has bridge hooks).
