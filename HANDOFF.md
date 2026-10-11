# m800Dr / m00Dr on the desktop — handoff

_Last updated 2026-10-11 (drum kits per Roll, drums on column 7, chord/arp sound menus, chord-following reese beat; committed and pushed)._ This is the M8 rig, which now runs entirely on the Linux desktop
(`Desktop`, 192.168.1.17, CachyOS, KDE Wayland) instead of the MacBook. The Dirtywave M8
(Teensy / M8 Headless) is plugged in here over USB.

**In progress:** m00Dr's parts play on desktop synths (FluidSynth chords/arp/drums, reese.py bass,
Open303 acid), all through `mixer.py`, a per-part mixer on the **Launch Control XL**'s faders.
Everything added since 2026-10-10 night is built and live but **not yet judged by ear** (kits,
chord sounds, reese beat range, levels).
**The controller is plugged into the Mac; move it to the desktop** (links.sh then wires it up). See
"Resume here" at the bottom. Full plan: `~/.claude/plans/m00dr-still-plays-when-dreamy-flurry.md`.

```
 m00Dr GUI ──MIDI port "m00Dr" (ch1 chords, ch2 bass, ch3 arp, ch4 drums, ch5 acid)
   │ ├─(pw-link, m00dr-synth-links)──▶ FluidSynth [ch1, ch3, ch4 drums; JACK out per channel] ─┐
   │ ├─(pw-link)──▶ reese.py (ch2) ──────────────────────────────────────────────────────────┤
   │ ├─(pw-link)──▶ Carla "m00Dr-Open303": MIDI Channel Filter(ch5) ─▶ JC303 ────────────────┤
   │ │ web remote :8765/:8766 (iPad: play/stop works)                                         │
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
| Reese bass (ch2) | user service `m00dr-reese` → `~/code/m800Dr/reese.py` (numpy synth: ALSA seq client `m00Dr-Reese` in, `pw-cat --target=0` out, linked to the mixer; also reads ch1 chords to set the saws' beat). Unit copy in `systemd/` | yes (enabled) |
| Open303 acid (ch5) | user service `m00dr-open303` → `carla-rack --no-gui synths/open303.carxp` (rack: internal MIDI Channel Filter passing ch5 only → JC303 LV2). JACK client `m00Dr-Open303` via `CARLA_CLIENT_NAME`; `PIPEWIRE_QUANTUM=256/48000` (Carla asserts if the buffer size changes). Regenerate the project with `python3 tools/build_open303.py [--dump]` (uses Carla's backend; sets the starting patch in `PATCH`, strips `<ExternalPatchbay>`). Unit copy in `systemd/` | yes (enabled) |
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
- **`~/code/mOODr/`**: the user's repo `4tytw0/mOODr`, branch `linux-alsa-ports` (pushed;
  no PR yet). `m8_midi_route.sh` is **untracked**. m00Dr part channels are in `moodr/playback.py`
  (`CHORD/BASS/ARP/DRUM/ACID_CHANNEL` = 0/1/2/3/4).
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
2. Check: `systemctl --user is-active m00dr m00dr-fluidsynth m00dr-reese m00dr-open303 m00dr-synth-links m800dr-mixer m800dr-mix-m8 m800dr-audio`
   and `pw-link -l | grep -A2 '^m800Dr-Mixer'`.
3. Move the Launch Control XL to the desktop; check its faders move the strips on
   `http://192.168.1.17:8808/mixer` (factory template; if a fader shows as "last unmapped CC",
   adjust `FADER_CCS` in `mixer.py`).
4. Listen to the new acid and the drum kits; tune `PATCH` and balance levels. M8 (fader 8) is
   unplugged right now, so `m800dr-mix-m8` restart-loops harmlessly. Acid fader 5 was left at 0.
5. Listen to the 32 kits (Roll, or `select 3 4 128 <kit>` on the :9800 shell); reseed the mixes
   (`build_drumkit.py --seed`) if they repeat too much.
6. Try the chord/arp sound menus; balance the new `chords.sf2` patches against Phase sine.
7. Judge the reese beat (the journal logs `chord [...]: beat x Hz`); tune `BEAT_*`/`ROUGH_*` in reese.py.
8. Check the column-7 knobs really send CC 19 / CC 35 once the controller is on the desktop.
9. Open a PR for mOODr `linux-alsa-ports` when happy.

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
