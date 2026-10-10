# m800Dr / m00Dr on the desktop — handoff

_Last updated 2026-10-10 (end of session)._ This is the M8 rig, which now runs entirely on the Linux desktop
(`Desktop`, 192.168.1.17, CachyOS, KDE Wayland) instead of the MacBook. The Dirtywave M8
(Teensy / M8 Headless) is plugged in here over USB.

**In progress:** m00Dr's parts now play on desktop synths: FluidSynth for chords/bass/arp (done)
and Open303 for acid (**not started; waiting on a package install**). See "Resume here" at the
bottom. Full plan: `~/.claude/plans/m00dr-still-plays-when-dreamy-flurry.md`.

```
 m00Dr GUI ──MIDI port "m00Dr" (ch1 chords, ch2 bass, ch3 arp, ch5 acid)
   │ ├─(pw-link, m00dr-synth-links)──▶ FluidSynth [router: ch1-3 only] ─┐
   │ ├─(planned)──▶ Carla: MIDI Channel Filter(ch5) ─▶ JC303 (Open303) ─┤
   │ └─ (M8 no longer gets m00Dr MIDI; m00dr-m8-midi disabled)          │
   │ web remote :8765/:8766 (iPad: play/stop works)                     │
 M8 USB audio ──(pw-loopback, m800dr-mix-m8)────────────────────────────┤
                                                         PipeWire null sink "m800dr_mix"
                                                                │ .monitor
                                    m800dr-audio :8808 ◀────────┘  browser player + Lissajous scope
```

## What runs where

| Piece | How it runs | Survives reboot? |
|---|---|---|
| m00Dr GUI | transient unit `m00dr` (`systemd-run`, cwd `~/code/mOODr`, `uv run python main.py`) | **No.** Relaunch from the desktop launcher (`~/.local/share/applications/m00dr.desktop`) |
| m00Dr web remote | inside the GUI; `~/.config/m00Dr/m00Dr.conf` → `[web_remote] enabled=true` | with the GUI |
| Mix bus `m800dr_mix` | `~/.config/pipewire/pipewire.conf.d/m800dr-mix.conf` (null sink, 48 kHz, low priority so the M8 stays the default sink) | yes |
| M8 audio → mix | user service `m800dr-mix-m8` (`pw-loopback`, pinned targets) | yes (enabled) |
| Audio → browsers | user service `m800dr-audio` → `~/code/m800Dr/server.py`, `SOURCE = "m800dr_mix.monitor"` | yes (enabled) |
| FluidSynth (ch1 chords, ch3 arp) | user service `m00dr-fluidsynth`. Config is `synths/fluidsynth.cfg` (router passes ch1/ch3 only); fonts are `synths/GeneralUser-GS.sf2` and `synths/gamebreaks.sf2` (font 2, bank offset 1). Chords = preset 10 `phase_sine`, arp = preset 0 `smet_nor3`. `PULSE_SINK=m800dr_mix`, gain 1.5. TCP shell on :9800 (no `nc`; use python sockets) | yes (enabled) |
| Reese bass (ch2) | user service `m00dr-reese` → `~/code/m800Dr/reese.py` (numpy synth: ALSA seq client `m00Dr-Reese` in, `pacat` out to `m800dr_mix`). Unit copy in `systemd/` | yes (enabled) |
| m00Dr → synth links | user service `m00dr-synth-links` → `synths/links.sh` (re-runs `pw-link` for the pairs in `LINKS` every 2 s: m00Dr → FluidSynth and m00Dr → Reese) | yes (enabled) |
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
- **`~/code/mOODr/`**: the user's repo `4tytw0/mOODr`, branch `linux-alsa-ports`. Commit
  `055f3d8` (`find_port()`) is **not pushed**; `m8_midi_route.sh` is **untracked**. m00Dr part
  channels are in `moodr/playback.py` (`CHORD/BASS/ARP/ACID_CHANNEL` = 0/1/2/4).
- Python tooling: **uv only** (user preference).

## Verified tonight

- FluidSynth channel split: test notes on ch1 and ch3 are loud in the mix; ch5 stays silent
  (peak 14 of 32768). Live m00Dr playback was reaching FluidSynth.
- The :8808 stream carries the mix: FluidSynth and M8 audio both appear in `/stream`.
- The M8 is now producing real audio (rms ~11k, peaks near full scale). Earlier it was digital
  silence because nothing was playing. **Levels:** M8 + synths can clip the mix. Lower the M8
  mixer or FluidSynth `-g` (now 1.5).

## Resume here (after a reboot)

1. Relaunch m00Dr from the desktop launcher. Check:
   `systemctl --user is-active m00dr-fluidsynth m00dr-synth-links m800dr-mix-m8 m800dr-audio`
   and `pw-link -l | grep -A2 'RtMidiOut Client:m00Dr'` (should show FluidSynth).
2. **Packages:** the user runs `sudo pacman -S jc303-lv2 carla pipewire-jack` (about 25 MB; it
   replaces `jack`). Still **not installed** at session end; `paru -Syu jc303-lv2 carla pipewire-jack` also works. Skip `freepats-general-midi`. Then
   `systemctl --user restart pipewire pipewire-pulse wireplumber`.
3. **Open303 (plan step 5):** hand-write the Carla project `synths/open303.carxp` with Carla's
   internal *MIDI Channel Filter* (ch5 only) feeding JC303 LV2 (URI from
   `/usr/lib/lv2/*jc303*/*.ttl`), plus a user service `m00dr-open303` (`carla --no-gui <project>`
   if `carla --help` has the flag). Test: a ch5 note sounds and a ch1 note doesn't.
4. Add Carla's pairs to `LINKS` in `synths/links.sh`: m00Dr → Carla `events-in`, and Carla
   `audio-out1/2` → `m800dr_mix:playback_FL/FR` (unless the project keeps them). Get exact names
   from `pw-link -o` / `pw-link -i`.
5. (done) `m00dr-m8-midi` is already disabled.
6. End-to-end check: m00Dr playing → `/stream` has signal; muting acid leaves only FluidSynth,
   muting the others leaves only the 303; the scope moves on the TV/Mac.

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
- The user had `sudo pacman -S jc303-lv2 carla pipewire-jack` waiting on a password; once
  installed, a plugin synth via Carla could replace `reese.py` if wanted.

## Open issues

- **HDMI sink missing** from PipeWire, so `m8-loopback` can't play anywhere. Nobody has looked into
  it; the TV is fed by the browser stream instead.
- **Serial / m8c:** the user isn't in the `uucp` group (`sudo usermod -aG uucp emin0r` +
  re-login). That's needed for m8c, m8.run and screen mirroring.
- **Persistence of the m00Dr GUI:** consider a real user service instead of `systemd-run`.
- Push/PR `linux-alsa-ports` when happy; decide whether `m8_midi_route.sh` is still worth
  committing.

## Ideas queued

- **Instrument control surface:** a web page sending MIDI CCs, bound via the M8's MIDI MAPPING
  view (could also drive JC303's knobs through Carla).
- Other synths for m00Dr: Surge XT / Vital / Dexed (through Carla), SuperCollider/Pd, Web Audio
  synths fed by relayed MIDI, the Novation Circuit (m00Dr has bridge hooks).
