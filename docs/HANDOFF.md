# m00Dr rig on the desktop — handoff

## Start here (2026-10-31): one repo, all SunVox, a producer you can talk to

**Everything below the next heading is history from before 2026-10-31**: paths like `~/code/m800Dr`,
`m00Drc/`, `synths/`, FluidSynth and Open303 are gone. Read `README.md` for the current layout.

- **One repo:** this one (`~/code/m00Dr`, GitHub `4tytw0/mOODr`, **`main`**; Phases 0 and 1 merged
  in PR #7, 2026-10-31. The live checkout is on `main`). m800Dr and m00Drc were merged in with history; the old checkouts are in
  `~/code/_old/`. Layout: `composer/` `voices/<strip>/` `mix/` `surfaces/` `brain/` `tools/` `edges/`.
- **Start/stop:** `rig up | status | log | down` from the Mac (`edges/mac/rig`, installed in the
  Mac's `~/.local/bin`), or `systemctl --user start|stop m00dr.target` here. Units install with
  `cp systemd/*.service systemd/m00dr.target ~/.config/systemd/user/ && systemctl --user daemon-reload`.
- **Voices:** one SunVox session per strip, patches by program change (`voices/_sunvox/bank.py`):
  chords Wurli/OBX strings/Arco strings/Organ/Pad (ch7), bass Fuzz/Reese (ch2), arp 10 stabs +
  Phase sine (ch3), acid 303 (ch5), drums Machine/Boom/Crunch (ch6), breaks (ch8), fx (ch10).
  `uv run voices/<strip>/build.py` rebuilds a strip's patches; `player.py --demo out.wav` renders them.
- **Producer:** `qwen3.6-35b-music175` (set by the localModel session). m00Dr's menus list only
  SunVox patches (+ a bass menu) so its sound picks are real. Its prompt now says what's audible
  per part, and actions on a part switched off in m00Dr come back flagged as having no effect.
- **Audio page:** the producer's notes and a chat with it (`/producer`, `/chat`; files in
  `$XDG_RUNTIME_DIR/m00drc-{producer,chat}.json`); its next plan reads the chat. On the Mac use
  RAW: WebRTC never connects from Chrome there.
- **Heard once** (14:00–14:25): all ran; the producer switched most parts off itself (a style the
  ×0.175 vector seems to push; Tyler: leave it). Fixed since, not yet heard: the saved mix keeps
  m00Dr's part toggles; the audible grounding.
- **Open:** the roller is sometimes killed on stop (systemd timeout; the next start restores from
  its save) · mixer strip labels show old channels (cosmetic) · FluidSynth leftovers to delete
  (`voices/_fluidsynth/`, its unit, `tools/build_drumkit.py`, `build_gamebreaks.sh`, `build_sf2.py`;
  keep `tools/build_chords.py` and `phase_sine.py`, the builders use them) · Phase 2 (headless
  composer) and Phase 3 (fewer processes) not started. The full plan is in Tyler's Code-ish vault,
  `_vault/m00Dr rig consolidation.md`.

### Next session: Phases 2-4

- **Phase 2, headless composer** (nice to have: the desktop logs in at boot, so a session is
  always there). Today the Qt window *is* the state: `composer/moodr/app.py` holds every control
  as a widget, `PlaybackEngine` (`playback.py`) reads them, and `web_remote.py` (which the
  producer drives) works the window's widgets. The move is a plain state/controller object that
  the engine reads and the web remote, the producer and (optionally) Qt all write. Start by
  listing what `app.py` owns that isn't presentation; `playback.py`, `clock.py` and `oracle.py`
  are already UI-free. 233 tests in `composer/tests` cover the engine.
- **Phase 3, fewer processes.** One SunVox library session renders all its slots into one stereo
  output, so strips must stay separate sessions: one process would need one library *copy* per
  strip (first check that `sunvox.so` loaded under different names, or with `dlmopen`, gives
  independent instances). Players share `voices/_sunvox/bank.py`; chords and bass have their own
  tempo logic (tremolo, beat/wobble) to keep.
- **Phase 4, the producer as a client.** It already steers through m00Dr's web remote and the
  mixer's UDP control. Left: the "a fader you move caps it" rule lives in `mix/mixer.py` (fine), and
  the roller is sometimes killed on stop (systemd timeout), so it never restores on stop.

## Before 2026-10-31

_Last updated 2026-10-24 (I Ching arcs, breaks on fader 8, controller transport, drums back on the grid, dropouts fixed, WebRTC stream; see "Done 2026-10-18/24"). **All m00Dr services were stopped on request at the end of 2026-10-24**; see "Resume here"._ This is the M8 rig, which now runs entirely on the Linux desktop
(`Desktop`, 192.168.1.17, CachyOS, KDE Wayland) instead of the MacBook. The Dirtywave M8
(Teensy / M8 Headless) is plugged in here over USB.

**In progress:** m00Dr's parts now mostly play on headless **SunVox** players: acid (ch5), bass
(ch2, replacing reese.py), drums when a SunVox kit is picked (ch6) and chords when "SunVox Wurli" is
picked (ch7). FluidSynth still plays the arp (ch3) and the soundfont chord/drum choices. m00Drc's
roller rolls m00Dr's controls and the acid's sound. Everything is live on the desktop and measured,
but **not yet judged by ear**.
**Nothing is merged into master/main yet.** Everything is on one linear branch per repo,
**`rig-2026-10-17`** (m800Dr: 27 commits on `master`, this handoff update last; mOODr: 9 on `main`, ending
`986d1d7`). It replaces the stacked PRs (m800Dr #1-#9, mOODr #2-#5), which are still open. The desktop
runs it from **uncommitted copies** that match that branch file for file (see "Code / files"). See
"Resume here" at the bottom.

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
| Mixer | user service `m800dr-mixer` → `mixer.py` (JACK client `m800Dr-Mixer`). Strips (MIDI ch / fader): chords 1/1, bass 2/2, arp 3/3, fx 10/4, acid 5/5, drums 4/7, breaks 8/8 (the M8 strip is gone). Moves on CC 7 on the strip's channel or Launch Control XL CC 77-84 (any channel). Unity at 80% of travel (`KNEE`); above it, asymmetric tanh fuzz. Positions saved in `~/.local/state/m800dr/mixer.json`; live state in `$XDG_RUNTIME_DIR/m800dr-mixer.json` | yes (enabled) |
| FluidSynth (ch1 chords, ch3 arp, ch4 drums) | user service `m00dr-fluidsynth` (unit copy in `systemd/`). Config is `synths/fluidsynth.cfg` (router passes ch1/ch3/ch4 only); fonts are `synths/GeneralUser-GS.sf2`, `synths/gamebreaks.sf2` (font 2, bank offset 1), `synths/chords.sf2` (font 3, bank offset 2) and `synths/gamedrums.sf2` (font 4, bank 128, 32 kits). Startup: chords preset 10 `phase_sine`, arp preset 1 `toa_f`, drums kit 0; m00Dr then sets chords/arp from its sound menus and the kit per Roll. JACK, 4 output groups (chords l/r_00, arp _02, drums _03; 2 fx pairs per group), gain 1.5. TCP shell on :9800 (no `nc`; use python sockets) | yes (enabled) |
| SunVox bass (ch2) | user service `m00dr-sunvoxbass` → `sunvoxbass.py` (`synths/fuzzbass.sunvox`; ALSA client `m00Dr-SunVoxBass`, `pw-cat` → bass strip). Mono, last-note priority; per-note osc2 detune from m00Dr's clock = one fuzz swell per beat | yes (enabled) |
| SunVox acid (ch5) | user service `m00dr-sunvox303` → `sunvox303.py` (`synths/acid303.sunvox`; client `m00Dr-SunVox303` → acid strip). CCs 74/71/75/76/5/77 on ch5 = cutoff/resonance/decay/accent/glide/drive, eased over 0.5 s | yes (enabled) |
| SunVox drums (ch6) | user service `m00dr-sunvoxdrums` → `sunvoxdrums.py` (`synths/drumkit-*.sunvox` + `drumkits.json`, one SunVox slot per kit; program change 0-2 picks; client `m00Dr-SunVoxDrums` → drums strip). Every kit has a bus compressor + make-up Amplifier (`build_drumkits.py` `drum_bus()`) | yes (enabled) |
| FX (ch10) | user service `m00dr-fx` → `fx.py`: synthesized noise risers (notes 60/61/62 = 2/4/8 bars on m00Dr's clock), downlifters (64/65), an impact (67), a held atmosphere (72), and WAVs from `synths/fx.json` (84+; files in `synths/fx/`). Client `m00Dr-FX` → fx strip (fader 4, ducked by the sidechain like the tonal parts). The roller's arranger: riser into each drop (the drop lands as it ends, with the impact), atmosphere through a strip, downlifter on a sweep_out | yes (enabled) |
| Breaks (ch8) | user service `m00dr-breaks` → `breaks.py`: 64 traditional breaks (`synths/breaks.json`; audio in `synths/breaks/`, git-ignored, built on the Mac by `tools/build_breaks.py` from `~/Documents/PRODUCTION`), 16 slices per bar on m00Dr's MIDI clock. The roller sends a program change (the cast's yang bits, 0-63) per landed Roll and chop CCs 20/21 per chord → breaks strip (fader 8) | yes (enabled) |
| WebRTC stream | user service `m800dr-webrtc` → `webrtc.py` (uv script, aiortc): stereo Opus over UDP, signalling on localhost:8809, reached through server.py's `POST /webrtc/offer`. The player page's **WEBRTC** chip (default; **RAW** is the old `/stream`) | yes (enabled) |
| Wi-Fi power save | user service `m00drc-powersave` → `m00drc.powersave`: power save off while m00Dr plays, on while stopped. **Not running:** needs `sudo install -m 440 -o root -g root ~/code/m800Dr/systemd/m800dr-powersave.sudoers /etc/sudoers.d/m800dr-powersave` first, then `systemctl --user enable --now m00drc-powersave` | not yet |
| Controller transport (Mac) | `tools/midi_bridge.py` on the Mac: the LCXL's bottom-left and bottom-right buttons (notes 73/92) start/stop m00Dr; both LEDs green while playing, red while stopped | no: `nohup` from a terminal |
| SunVox keys (ch7) | user service `m00dr-sunvoxkeys` → `sunvoxkeys.py` (`synths/wurli.sunvox`; client `m00Dr-SunVoxKeys` → chords strip). Tremolo = 8th notes at the clock's tempo, phase reset each beat | yes (enabled) |
| m00Drc roller | user service `m00drc-roller` → `m00Drc/` (`uv run python -u -m m00drc.roller --wait-for-play`). Web remote :8766 for m00Dr's controls; ALSA client `m00Drc-Roller` (`knobs in` ← mixer controls, `sound out` → sunvox303) | with m00dr (`WantedBy=m00dr.service`) |
| Reese bass (ch2) | **Not a service since 2026-10-31**: `synths/reference/reese.py`, kept for its BPM-locked wobble (sunvoxbass plays ch2) | no |
| Open303 acid (ch5) | **Removed 2026-10-31** (unit, `open303.carxp`, `tools/build_open303.py`; in git history before that) | — |
| Graph links | user service `m00dr-synth-links` → `synths/links.sh`: every 2 s, reads `pw-link -o/-i/-l` once and links only the pairs in `LINKS` that are missing (m00Dr → synths, synths → mixer strips, mixer → `m800dr_mix`; plus every USB MIDI device except Midi Through/M8 → `m800Dr-Mixer:faders`). The ~120-clients-per-pass churn (issue #10) is fixed: 30 PipeWire client events per 10 s, was ~970 | **no: disabled** since 2026-10-22 (for #10); enable it now: `systemctl --user enable m00dr-synth-links` |
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

## Done 2026-10-18/24: arcs, breaks, transport, groove, dropouts, WebRTC

On `rig-2026-10-17` in both repos (see the top). In order:

- **Roller tempo fix:** a tempo change re-timed every step since Play (a burst or a stall); it now
  re-anchors. The roll lane still ignores a control's `enabled` (noted only).
- **Drum rolls keep the groove** (mOODr): a groove's main hits (chance ≥ `_MAIN_CHANCE` 0.85) always
  play while the knob is nearest that groove.
- **303 tab** beside the scope on :8808 (`acid.html`; sunvox303.py takes UDP 9811 and writes
  `$XDG_RUNTIME_DIR/m800dr-acid.json`; a knob moved there holds against the roller).
- **Drum bus:** shorter Machine hat (`MACHINE_HAT_RELEASE` 18) and a compressor on every kit
  (`BUS_THRESHOLD/SLOPE/ATTACK/RELEASE` 80/50/3/80, make-up `BUS_MAKEUP` 322 through an Amplifier:
  the SunVox Compressor's volume is an input gain, and its threshold is an amplitude out of 256).
- **I Ching arcs** (`roller.py`): each landed Roll (every 120-240 s) rolls an arc of 8 sections up to
  the next Roll. Three-coin tosses flip the changing lines; `hexagram_int` reads the six lines as binary,
  × 127 // 63. m00Dr's web remote shares the landed Roll's lines (`cast` in the snapshot). `--no-iching`
  rolls from the dice only.
- **Breaks on fader 8** (see "What runs where"); the M8 strip and its links are gone. `chop()`: a
  changing yin line stutters a pair of 16ths; a changing yang line swaps it with the pair half a bar on
  (lower trigram) or echoes the beat before (upper). Humpty Dump's downbeat is probably wrong.
- **Controller transport:** the LCXL's bottom-left button, and since 2026-10-24 the bottom-right too,
  start/stop m00Dr (LED note-on on the template channel: 60 green, 15 red, 12 off).
- **Rebased** every PR into one linear `rig-2026-10-17` per repo (mOODr's two "Web remote" commits
  squashed).
- **Drums back on the grid** (mOODr, 2026-10-24): the double-time hats played the hat pattern on
  32nds, twice a bar, which moved the offbeat hats onto the "e" and "a" and left them unswung under
  swung snares (flams). Hats now step with the kick/snare on the swung 16ths. Another groove's main
  hits no longer bleed in at partial odds (at 0.25 the house kicks were each 45% over the two-step).
  Timing was measured clean first: m00Dr's clock ±1 ms, PipeWire's MIDI bridge a fixed 10.8 ms.
- **Dropouts** (2026-10-24): the Python mixer was missing deadlines. Two causes: the links.sh churn
  (#10, fixed above: 20 xruns/30 s with the old keeper, 0 with the new) and the 256-frame quantum. The
  quantum is now **forced to 512 at runtime** (`pw-metadata -n settings 0 clock.force-quantum 512`;
  mixer CPU 34% → 5%). A PipeWire restart loses it; `force-quantum 0` reverts. Every graph change
  (adding/removing a link) can blip the mixer, so don't probe the graph while someone listens; watch
  the mixer's ERR column in `pw-top -b -n 2`.
- **Wi-Fi:** with power save on, the stream to the Mac stalled ~100 ms at a time whenever the iPad
  woke (measured: m00Dr's clock steady, the stream's arrival gappy). The Mac's AWDL (`awdl0`) adds
  off-channel bursts too. Hence the power-save watcher (needs the sudoers step) and:
- **WebRTC stream** (see "What runs where"): Opus in `OPUS_APPLICATION_AUDIO` (aiortc hardcodes VOIP;
  `webrtc.py` swaps the constant), ~99 kbps stereo; the page munges `stereo=1` into its offer. Headless
  Chromium on the Mac: 0 lost, 0% concealed, ~40 ms adaptive jitter buffer, 4 ms RTT. It preserves
  the stream more than it lowers latency; Ethernet on the desktop would do most for both.
- **Stuck bass** (2026-10-24): a steady 0.02 on the bass strip with m00Dr stopped, which CC 123/120
  didn't clear; restarting `m00dr-sunvoxbass` did. Not a held note: all-notes-off silences the bass,
  303 and keys within 0.5 s (measured offline). Next time, record the bass output before restarting.

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

> **2026-10-24: every unit below is disabled** (on request): nothing starts at boot or login. The "yes (enabled)" column predates this. Start on demand: `systemctl --user start m800dr-mixer m800dr-audio m800dr-webrtc llama-swap`, then RIG on the audio server page (it starts the synths and m00Dr and presses Play).

## Resume here

1. **All m00Dr services were stopped on 2026-10-24** (on request; the m800Dr mixer, audio server and
   WebRTC stream still run). Start them with
   `systemctl --user start m00dr-synth-links m00dr-fluidsynth m00dr-sunvox303 m00dr-sunvoxbass m00dr-sunvoxdrums m00dr-sunvoxkeys m00dr-breaks m00dr`
   (the roller follows m00dr). After a reboot: log in to Plasma (the box waits at the **lightdm**
   greeter; autologin isn't working), and `m00dr` starts with the session. Re-force the 512 quantum
   (see "Done 2026-10-18/24") and enable `m00dr-synth-links`, or nothing re-links after a restart.
2. Check: `systemctl --user is-active m00dr m00dr-fluidsynth m00dr-sunvoxbass m00dr-sunvox303 m00dr-sunvoxdrums m00dr-sunvoxkeys m00drc-roller m00dr-synth-links m800dr-mixer m800dr-audio`
   and `pw-link -l | grep -A2 '^m800Dr-Mixer'`.
3. **Listen** to the SunVox parts (none judged by ear yet): Wurli tremolo depth, fuzz-bass level vs
   the kick, the punchier Machine kick, Boom/Crunch, the acid's crush. Faders 3 (arp) and 5 (acid)
   were at 0 on 2026-10-16. Patch settings are at the top of each `m00Drc/m00drc/build_*.py`.
4. Install the power-save sudoers rule and enable `m00drc-powersave` (commands in "What runs where");
   better still, put the desktop on Ethernet.
5. Open one PR per repo from `rig-2026-10-17` (replacing the stacks, then close them), merge, and put
   the desktop checkouts back on clean branches. That fixes issue #10 on master; branch
   `handoff-links-note` only added the #10 note and is superseded.
6. Move the Launch Control XL to the desktop; check its faders move the strips on
   `http://192.168.1.17:8808/mixer` (factory template; if a fader shows as "last unmapped CC",
   adjust `FADER_CCS` in `mixer.py`).
7. Balance levels across the strips. Fader 8 is the breaks now (it sat at 0.87, in the fuzz zone).
   The M8 is unplugged, so `m800dr-mix-m8` restart-loops harmlessly.
8. Listen to the 32 kits (Roll, or `select 3 4 128 <kit>` on the :9800 shell); reseed the mixes
   (`build_drumkit.py --seed`) if they repeat too much.
9. Try the chord/arp sound menus; balance the new `chords.sf2` patches against Phase sine.
10. (reese.py is disabled; only if switching back: judge its beat, tune `BEAT_*`/`ROUGH_*`.)
11. Check the knobs really send their assumed CCs once the controller is on the desktop: column 7
    (19 drum mutation, 35 sidechain) and the roller's column 4/6 (16, 32, 52, 18). The roller's
    journal (`journalctl --user -fu m00drc-roller`) prints a `knob:` line per CC it takes.
12. Make the drums follow the Rolls (asked for 2026-10-24, not started): the I Ching arcs drive the
    breaks but not m00Dr's own drum pattern.
13. Idea the user raised: seed with the planets as hexagrams (Human Design's 64 gates of 5.625° on
    the zodiac; Sun's gate for key/mode, Moon's pulling the changing lines), via PyEphem. Not started.

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

- **Desktop runs uncommitted copies** matching `rig-2026-10-17` in both repos (deploys are file
  copies, compared with `cmp`). Tidy both once merged.
- **The 512 quantum is a runtime setting**, and the mixer is Python: at 256 frames it xruns under
  load. A native mixer (PipeWire filter-chain, or C/Rust) would allow 128-256 again.
- **WebRTC is tested in Chromium only**; Safari should connect but ignores the buffer slider.
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

## Done 2026-10-30: Qwen picks breaks, drives the bass; REC; hexagram feed
- **Hexagram feed** (45ad464): the roller writes each section's hexagram to `$XDG_RUNTIME_DIR/m00drc-hexagram.json`;
  `GET /hexagram` serves it. `~/Documents/Blender/iChing.blend` (Mac) draws it live (its `iChing` text block).
- **Transient maps** (3fe2296): `tools/transient_map.py` tags each break in `breaks.json` with `"transients"`, one
  4-hex-digit word per bar (bit per 16th, first 16th on top; half the 90th-percentile step's onset counts).
- **Arranger sees the break** (2af7046): its map is in every prompt; the model picks the part for take_out/bring_back/push.
  Roller unit runs `qwen3.6-35b-music20` (localModel's steered Qwen, see its HANDOFF).
- **REC** (ad754de): `POST /record` records the mix (parec, 24-bit) to `~/Recordings/m800Dr/`, 3 h cap; `↓ WAV` link.
  parec needs `--latency-msec=20` or the last ~2 s of a take are lost.
- **Sample library** (9f88557): `~/samples` = rsync of the Mac's `PRODUCTION/SAMPLES/{Drums,GAMEBREAKS}` + a
  `sononym.db` snapshot (resync steps in `tools/sample_catalog.py`); `catalog.json` from it. Each Roll the roller cuts
  up to 5 loops near the tempo (`tools/cut_breaks.py`, ~4 s) and the model picks one or the Roll's own (shuffled; it
  kept the own twice when it was always option a). Picks go to breaks.py live slots (program 64-127,
  `~/.local/state/m00drc/live-breaks/`).
- **Bass gate/wobble** (9f88557): sunvoxbass CC 22 wobble (5 speeds), CC 23 gate (8 patterns, 0 legato), CC 121 reset.
  Arranger moves `gate`/`wobble` (cooldown 3); links.sh wires roller sound out -> bass.
- **Sound picks** (762f91c): each Roll the model picks chordSound, arpSound, arpPattern and drumSource from
  what's playing + 5 shuffled menu options (letters); logged as `sounds:`. The 303 knobs stay on the dice lanes.
- **Not done:** drum kits composed from the library's hits (catalog has 4849 categorised hits; FluidSynth needs an
  sf2 per kit — the gamedrums builder could be reused).

## Done 2026-10-31: Qwen is the producer
- **Limiter** (3f483b8): mixer.py's last stage, -1 dBFS peak / -9 dBFS RMS (300 ms), 250 ms release; no control
  reaches it. `limiter_db` in the mixer state.
- **Producer mode** (caf97a9): `m00Drc/producer.py`, roller `--arrange producer` (the unit runs it on music20; dice
  lanes off). Each plan: the model sees all m00Dr controls + options, mixer (ceilings, limiter), bass, 303, break,
  samples sounding, its notes and its last actions' results; answers JSON (grammar-enforced): raw actions + next_bars.
  Logged as `producer:` lines. A plan takes ~80-110 s. Bad values come back to it as results, not guesses.
- **Hand ceilings:** a fader moved by hand (controller, /mixer page) caps machine moves on that strip until you move
  it again. Shown in the mixer state as `ceiling`.
- **Library:** `~/samples` = the Mac's whole PRODUCTION/SAMPLES (19 GB, 17,215 samples catalogued); resync steps in
  `tools/sample_catalog.py`; the producer reloads the catalog when it changes. Library samples play through fx.py's
  live slots (notes 96+, `~/.local/state/m00drc/live-fx/`), prepared by `tools/prep_sample.py`.
- **Back to the menu arranger:** set the unit's ExecStart to `--arrange model --model qwen3.6-35b-music175 --floor 0.15`.

## Start-up: the producer configuration (as of 2026-10-31)

Qwen (`qwen3.6-35b-music175`) produces the set: the roller runs `--arrange producer`, dice lanes off. Nothing starts at
boot; everything below is on demand, on the desktop (`ssh cacheos`; the login shell is fish, so wrap POSIX snippets in
`bash -c`). m00Dr is a GUI app: someone has to be logged in to Plasma (autologin doesn't work).

1. **Mixer, page, stream, model server:**
   `systemctl --user start m800dr-mixer m800dr-audio m800dr-webrtc llama-swap`
   Restarting `m800dr-mixer` later drops the stream for a second or two.
2. **Rig on** (synths, breaks, fx, links, m00Dr, then Play): RIG on the page `http://192.168.1.17:8808` (only from the
   desktop or NetBird), or `curl -s -X POST -d '{"on": true}' http://127.0.0.1:8808/rig`.
3. **The producer:** the roller (`m00drc-roller`) follows when someone's here (an open player page or a listener; the
   idle watchdog stops it after 3 min with nobody). Without a listener: `systemctl --user start m00drc-roller`.
   llama-swap loads music175 on its first plan (~10 s), then a plan takes ~80-110 s.
4. **Check:**
   - `systemctl --user is-active m00dr m00dr-synth-links m00dr-fluidsynth m00dr-sunvox303 m00dr-sunvoxbass m00dr-sunvoxdrums m00dr-sunvoxkeys m00dr-breaks m00dr-fx m00drc-roller m800dr-mixer m800dr-audio m800dr-webrtc llama-swap`
   - `curl -s 127.0.0.1:8080/running` shows `qwen3.6-35b-music175` once the producer has asked.
   - `journalctl --user -u m00drc-roller -f | grep producer`: each plan's notes, every action with its result, and
     when it's asked next. `producer: no plan (...)` means a failed request; it retries after 4 bars.
   - The limiter: `limiter_db` in `$XDG_RUNTIME_DIR/m800dr-mixer.json` (0 = not touching the music).
5. **Breaks silent after a restart mid-set:** breaks.py starts on the roller's bar-line sync (CC 24) at the next
   chord change; one Stop/Play in m00Dr re-locks everything to the clock.
6. **Blender (Mac, optional):** the hexagram in 3D. Open it with the iChing script running:
   `open -a "$HOME/Library/Application Support/Steam/steamapps/common/Blender/Blender.app" --args "$HOME/Documents/Blender/iChing.blend" --python-text iChing`
   It polls `http://192.168.1.17:8808/hexagram` every 2 s (macOS asks once to allow local network access). The
   script's source is `tools/blender_iching.py`; paste it into the file's `iChing` text block if it's ever lost.

**Your hands on it:** a fader you move by hand (Launch Control XL or the /mixer page) is that strip's ceiling for the
producer until you move it again. The master limiter (-1 dBFS peak, -9 dBFS RMS, end of mixer.py) can't be reached
by anything it sends. REC on the page records the mix to `~/Recordings/m800Dr/`.

**The sample library** (`~/samples`, 19 GB, not in git): a copy of the Mac's `~/Documents/PRODUCTION/SAMPLES` plus a
`sononym.db` snapshot. To resync after adding samples, on the Mac:

    S=~/Documents/PRODUCTION/SAMPLES
    sqlite3 -readonly $S/sononym.db ".backup /tmp/sononym.db"
    rsync -a --exclude='*.asd' --exclude='sononym.db*' $S/ cacheos:samples/ && rsync -a /tmp/sononym.db cacheos:samples/

then on the desktop `python3 ~/code/m800Dr/tools/sample_catalog.py`; the producer reloads the catalog by itself.
Sononym must have analysed new files (open it on the Mac first) or the catalog leaves them out.

**Other modes** (the roller unit's ExecStart, `systemd/m00drc-roller.service`, copied to `~/.config/systemd/user/`,
then `systemctl --user daemon-reload && systemctl --user restart m00drc-roller`):
- producer: `--wait-for-play --arrange producer --model qwen3.6-35b-music175` (now, since 2026-10-31; music20 before)
- menu arranger + library breaks + sound picks: `--wait-for-play --arrange model --model qwen3.6-35b-music175 --floor 0.15`
- steering strength: `qwen3.6-35b-music05` / `-music10` / `-music175` / `-music20`, or plain `qwen3.6-35b` (localModel's llama-swap.yaml)
- dice only: `--wait-for-play --arrange dice`

**All off** (the mix is put back as the roller found it, the model unloads):
`systemctl --user stop m00drc-roller; systemctl --user stop 'm00dr*' 'm800dr*' m8-loopback llama-swap`
