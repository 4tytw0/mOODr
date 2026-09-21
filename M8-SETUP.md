# m00Dr <-> M8 (Teensy, M8 Headless) working configuration

Confirmed working end-to-end on real hardware (2026-09-19): m00Dr's chords/arp/bass/acid
voices trigger the M8, m00Dr starts/stops/tempo-syncs to the M8's own transport (the M8 is
the master), and M8 audio is routed to the MacBook's speakers through m8c.

## MIDI channel map

| Voice | Channel |
|---|---|
| Chords | 1 |
| Bass | 2 |
| Arp | 3 |
| Acid | 5 |

Channel 4 is unused. `moodr/playback.py` (`CHORD_CHANNEL`/`BASS_CHANNEL`/`ARP_CHANNEL`/
`ACID_CHANNEL`) is the source of truth; the M8's TRACK MIDI INPUT table below has to match
it. The GUI's `Bass→Ch2` button is a leftover no-op -- bass already defaults to channel 2.

## Launch order

The M8 is the transport master: pressing Play on the M8 starts and tempo-syncs m00Dr, not
the other way around. Two bridges carry MIDI in opposite directions.

1. **m00Dr GUI** -- `uv run python main.py` from `Code-ish/m00Dr/`, with **"External clock
   sync"** checked so it follows the M8's Start/Stop/clock instead of generating its own.
   Checking it is what creates the `"m00Dr In"` port.
2. **Both bridges** -- open **MIDI Status** in m00Dr and press **Start** on each:
   - *m00Dr -> M8 (notes)* -- forwards m00Dr's `"m00Dr"` port to the Teensy's `"M8"` port
     1:1 (notes, CCs; also clock/start/stop, but in external-sync mode m00Dr generates
     none, so there is nothing to carry back).
   - *M8 -> m00Dr (transport)* -- forwards the M8's clock/Start/Stop/Continue to `"m00Dr In"`.

   Running them by hand in their own terminals still works and is equivalent
   (`uv run python -u moodr_to_m8_bridge.py`, `... m8_to_moodr_transport_bridge.py`).
   m00Dr finds terminal-launched bridges in the process table, labels them
   `(outside m00Dr)`, and its Restart button works on those too. If the Circuit scripts are
   also present they show up in the same list.
3. **m8c** -- the **Open M8 UI** button in the same dialog launches it (the path is asked
   for once and remembered in `~/.config/m00Dr/m00Dr.ini`). From a terminal it needs the
   Teensy's serial device, e.g. `-dev /dev/cu.usbmodem94156301`. m8c mirrors the M8 display
   and routes its audio back to the Mac.

**Order is not critical.** Both bridges re-open *both* of their endpoints every 3 seconds
(`RECONNECT_INTERVAL_SECONDS`), so a bridge started before m00Dr, or left running across an
m00Dr restart or a Teensy unplug/replug, reconnects itself within a few seconds.

## When MIDI goes dead anyway

The exception to that self-healing is a CoreMIDI `MIDIServer` restart, which invalidates
every virtual-port reference in every process. A bridge can survive that **without
crashing** and still hold a permanently dead connection while reporting itself connected --
observed once going nearly an hour with no successful reconnect, while a freshly started
process saw the same port fine. A bridge not crashing was never proof it was healthy.

So: **MIDI Status -> Reset MIDI Server**. It resets the platform MIDI service, waits for it
to respawn, reopens m00Dr's own `"m00Dr"`/`"m00Dr In"` ports, and restarts every running
bridge -- including ones started in a terminal. It runs on a background thread, so a hung
port call can no longer freeze the app. m8c is unaffected by any of this; it uses the
serial connection, not MIDI.

## m8c audio config (`~/Library/Application Support/m8c/config.ini`)

m8c's out-of-the-box defaults are what's confirmed working -- the M8's CoreAudio input
routed to whatever macOS considers the default output:

```ini
[audio]
audio_enabled=true
audio_buffer_size=0
audio_device_name=Default
```

**Gotcha**: m8c does not retry opening the audio device after a USB disconnect/reconnect
blip. It logs `Cannot find M8 audio input device` and stays silent with no further retries
even once the device is back. Fix: fully quit and relaunch m8c.

## M8 MIDI Settings screen (on the M8 itself, not m8c)

Reached via the M8's own `PROJECT` view -> `MIDI SETTINGS`.

| Field | Value |
|---|---|
| RECEIVE SYNC | **OFF** (the M8 is the master; it follows nothing incoming) |
| RECEIVE TRANSPORT | **OFF** |
| SEND SYNC | ON |
| SEND TRANSPORT | SONG |
| PG CHANGE | ON |
| MODE | **POLY** (required so tracks sharing a channel round-robin a chord's simultaneous notes into real polyphony) |

**TRACK MIDI INPUT** -- chords need several tracks on channel 1 because each M8 track is
monophonic; arp/bass/acid are single-note voices, so one track each:

| Track | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|
| CHAN. | 01 | 03 | 02 | 05 | 01 | 01 | 01 | 01 |
| INST# | 00 | 01 | 02 | 03 | 00 | 00 | 00 | 00 |

Tracks 5-7 are extra chord voices on ch.1, round-robined by POLY mode, so a 3-4 note chord
sounds as a chord instead of one note at a time.

**Instrument slots are 2-digit hex starting at `00`**, so `INST# 01` means the *second*
slot. Check each `INST#` points at a loaded instrument (`TYPE` is a real engine like
`WAVSYNTH`, not empty) -- a mismatch here was the root cause of routing that looked correct
while nothing played.

**An empty Song sends only a Start-then-Stop blip**, so m00Dr plays one note and halts. Put
`00` in Song row `00`, track 1 to give the transport something to iterate through. The
project generator bakes this in, so it only matters for hand-built projects.

**To keep these settings for new projects**: `SAVE DEFAULTS` at the bottom of the same MIDI
Settings screen. It's a manual press on the device; m8c mirrors the screen but can't do it
for you.

## Generating a pre-configured M8 project

`m8-project-gen/` (Node, using [`m8-js`](https://github.com/whitlockjc/m8-js)) builds an
`.m8s` with the MIDI settings above already applied, plus four WAVSYNTH instruments roughly
matching each voice: `CHORDS` (triangle, slow attack, chorus+reverb), `ARP` (pulse 50%,
short decay, some delay), `BASS` (pulse 12%, dark filter, punchy envelope), `ACID` (saw,
high resonance with a filter envelope for the squelch sweep).

```sh
cd m8-project-gen
npm install   # first time only
npm run generate
```

Writes `m8-project-gen/M00DR-DEFAULT.m8s`; copy it onto the M8's SD card.

Confirmed loading on real hardware (2026-09-19). The file's format version (`2.7.8`, from
m8-js's `LATEST_M8_VERSION`) loads fine on this device's firmware (`3.1.4`) -- M8 file-format
and firmware versions are separate schemes and don't have to match.

### The 303 build

`npm run generate:303` writes two further projects from `generate-303.js`, voiced from what
transcriptions of real acid lines actually do (see the 2026-09-20 ROADMAP entry). The acid
instrument is the one that meaningfully changed: saw through a lowpass that *starts closed*
(`CUT 0x30`) with resonance high (`RES 0xE0`) and a fast envelope on the cutoff, because the
squelch is that filter sweep repeating per note rather than anything the oscillator does.
`CUT`, `RES` and `DE1` are the three to turn by ear. The other three voices were re-balanced
around it -- chords and arp moved to pulse waves with an immediate attack (the old slow pad
attack smeared m00Dr's off-beat stabs), and the bass was darkened so it stops fighting the
acid for the same low-mid range.

Two files are written because **M8's track input mode is one global setting** and the two
voices want opposite things from it:

| file | mode | chords | acid slides |
|---|---|---|---|
| `M00DR-303.m8s` | POLY | sound as real chords | retrigger, no glide |
| `M00DR-303-LEGATO.m8s` | LEGATO | collapse to one note | glide, the real 303 slide |

POLY is the one to start with, since m00Dr is a chord tool first; load the LEGATO build when
the acid line is the point and the chords are muted or thinned.

`recordNoteVelocity` is on in both so m00Dr's accents survive the trip -- it sends accented
acid steps at velocity 127 against 80 for plain ones. Note this lands as *volume* on the M8,
where a real 303 accent also opens the filter; getting the brightness too would mean m00Dr
sending a CC alongside the note, which it does not currently do.

Neither 303 build has been loaded on hardware yet. The channel map in both is checked against
`moodr/playback.py`'s own constants rather than against the comments.

## Other gotchas

- macOS may swallow F12 (m8c's audio-routing toggle) as a media key. Use Fn+F12, or turn on
  "Use F1, F2, etc. keys as standard function keys" in System Settings -> Keyboard.
