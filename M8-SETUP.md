# m00Dr <-> M8 (Teensy, M8 Headless) working configuration

Confirmed working end-to-end (2026-09-19): m00Dr's chords/arp/bass/acid voices trigger the
M8 correctly, m00Dr starts/stops/tempo-syncs to the M8's own transport (M8 is the master --
see Launch order below), and M8 audio is routed to the MacBook's speakers through m8c.

## Launch order

The M8 is the transport master (see MIDI Settings below): pressing Play on the M8 itself
starts and tempo-syncs m00Dr, not the other way around. Two bridges carry MIDI in opposite
directions between them.

1. **m00Dr GUI** (`uv run python main.py` from `python_files/m00Dr/`) -- with **"External
   clock sync"** checked, so it listens for the M8's Start/Stop/clock instead of generating
   its own
2. **Note bridge** (`uv run python -u moodr_to_m8_bridge.py`, same directory) -- forwards
   m00Dr's virtual `"m00Dr"` port to the Teensy's `"M8"` MIDI port 1:1 (notes, CCs; also
   passes through clock/start/stop, but m00Dr in external-sync mode doesn't generate any, so
   there's nothing for this to carry back to the M8 -- see `moodr/clock.py`'s
   `MidiClockSlave`)
3. **Transport bridge** (`uv run python -u m8_to_moodr_transport_bridge.py`, same
   directory) -- the reverse direction: forwards the M8's own clock/Start/Stop/Continue to
   m00Dr's `"m00Dr In"` slave-clock port
4. **m8c** (`m8c.app`, or the built binary, pointed at the Teensy's serial device, e.g.
   `-dev /dev/cu.usbmodem94156301`) -- mirrors the M8 display and routes its audio back to
   the Mac

If any of these are restarted out of order, or macOS's CoreMIDI `MIDIServer` process is
restarted for any reason, **m00Dr and both bridges need restarting** -- CoreMIDI
invalidates all existing virtual-port references on a MIDIServer restart, silently killing
both bridges' connections and m00Dr's own `"m00Dr"`/`"m00Dr In"` virtual ports. m8c is
unaffected since it uses the serial connection, not MIDI.

## m8c audio config (`~/Library/Application Support/m8c/config.ini`)

```ini
[audio]
audio_enabled=true
audio_buffer_size=0
audio_device_name=Default
```

This is m8c's default and is what's confirmed working (M8's CoreAudio input routed to
whatever macOS considers the default output device -- MacBook Pro Speakers). No changes
needed from m8c's out-of-the-box defaults.

**Known gotcha**: m8c does not automatically retry opening the audio device after a USB
disconnect/reconnect blip. If the Teensy's serial connection drops and reconnects (happens
occasionally), m8c's audio silently fails to reopen (`Cannot find M8 audio input device` in
its log) and stays silent with no further retries, even though the device is present again.
Fix: fully quit and relaunch m8c.

## M8 MIDI Settings screen (on the M8/Teensy itself, not m8c)

Reached via M8's own `PROJECT` view -> `MIDI SETTINGS`.

| Field | Value |
|---|---|
| RECEIVE SYNC | **OFF** (the M8 is the master; it doesn't follow anything incoming) |
| RECEIVE TRANSPORT | **OFF** |
| SEND SYNC | ON |
| SEND TRANSPORT | SONG |
| PG CHANGE | ON |
| MODE | **POLY** (required so multiple tracks sharing a MIDI channel round-robin a chord's simultaneous notes into real polyphony) |

**TRACK MIDI INPUT** (chords need multiple tracks on channel 1 for polyphony since each M8
track is otherwise monophonic; arp/bass/acid are single-note voices so one track each is
enough):

| Track | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|
| CHAN. | 01 | 03 | 02 | 05 | 01 | 01 | 01 | 01 |
| INST# | 00 | 01 | 02 | 03 | 00 | 00 | 00 | 00 |

This matches m00Dr's channel scheme (see `moodr/playback.py`'s `CHORD_CHANNEL`/
`BASS_CHANNEL`/`ARP_CHANNEL`/`ACID_CHANNEL`, changed 2026-09-19): chords = ch.1,
**bass = ch.2**, **arp = ch.3**, channel 4 unused, **acid = ch.5**. Tracks 5-7 are extra
chord voices on ch.1 (round-robined by POLY mode) to let a 3-4 note chord actually sound as
a chord instead of one note at a time.

**Important**: the M8 numbers instrument slots in 2-digit hex starting at `00` (so
`INST# 01` on this screen means the *second* instrument slot, i.e. `INST. 01`, not
`INST. 00`). Double-check each `INST#` here actually points at a loaded instrument (`TYPE`
is a real engine like `WAVSYNTH`, not empty) -- a mismatch here was the root cause of MIDI
routing looking correct but nothing actually playing.

**Confirmed (2026-09-19)**: with `RECEIVE SYNC`/`RECEIVE TRANSPORT` set to `OFF` per the
"M8 as transport master" setup below, an otherwise-empty Song (no chain/phrase content
programmed anywhere) only sends one MIDI Start-then-Stop blip when you press Play -- m00Dr
gets triggered but only plays its first note before the M8 stops again. Fix: put `00` in
Song row `00`, track 1 (assigns Chain `00`, which has an empty Phrase `00` in its first
step) -- with real content for the transport to iterate through, the M8 keeps running and
m00Dr plays its full sequence continuously. `m8-project-gen/generate.js` now bakes this in
by default, so a freshly generated project doesn't need this done by hand.

**To persist these as the default for new M8 projects**: use the `SAVE DEFAULTS` button at
the bottom of this same MIDI Settings screen on the M8 itself (m8c mirrors the screen but
can't press this for you -- it's a manual step on the device).

## Generating a pre-configured M8 project file

`m8-project-gen/` (Node, uses the [`m8-js`](https://github.com/whitlockjc/m8-js) library)
builds an `.m8s` project from scratch with the MIDI settings above already applied, plus
four WAVSYNTH instruments shaped to loosely match each m00Dr voice's role: `CHORDS`
(triangle, slow-ish attack, chorus+reverb), `ARP` (pulse 50%, short decay, a little delay),
`BASS` (pulse 12%, dark filter, punchy envelope), `ACID` (saw, high filter resonance with a
filter envelope for the classic squelch sweep).

```sh
cd m8-project-gen
npm install   # first time only
npm run generate
```

Writes `m8-project-gen/M00DR-DEFAULT.m8s`. Copy that onto the M8's SD card to load it.

**Confirmed working on real hardware (2026-09-19).** The file's format version (`2.7.8`,
from m8-js's `LATEST_M8_VERSION`) loads cleanly on this device's firmware (`3.1.4`) despite
being a different version number -- M8 *file format* version and *firmware* version are
separate schemes that don't necessarily track together, but this combination is now
verified, not just round-tripped programmatically.

## Other gotchas hit while setting this up

- macOS may intercept F12 (m8c's audio-routing toggle key) as a media key before it reaches
  the app. Use Fn+F12, or enable "Use F1, F2, etc. keys as standard function keys" in
  System Settings -> Keyboard.
