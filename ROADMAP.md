# m00Dr Roadmap

MIDI chord-progression generator. Tracking the resurrection of the old Kivy/time.sleep()
implementation into a modern, maintained app while keeping the music-theory core intact.

## Source files

| File | Status | Role |
|---|---|---|
| `archive/OLD mOODr_app.py` | reference only, do not extend | Full working logic: MIDI I/O, chord generation, playback loop, Kivy App class |
| `archive/OLD mOODr_Kivy_app.kv` | reference only, being replaced | Kivy UI layout |
| `m00Dr.py` | in-progress rewrite target | Partial CustomTkinter shell (Gemini-assisted restart), no logic wired up yet |

## What we're keeping as-is

Carried over verbatim (or near-verbatim) from the OLD code into the new core module —
this is the part of the project that already works and is liked:

- `Note_Dict`, `Modes`, `progression_conversions`
- Interval maps: `major_intervals`, `minor_intervals`, `byzintine_intervals`, `snh_intervals` (+ `snhtri()` generator)
- Chord shapes: `MAJOR_Chord`, `minor_Chord`, `dim_Chord`, `M7`, `m7`, `dim7`
- Conversion functions: `determine_mode`, `note_to_midi_int`, `midi_int_to_note`, `to_midi_conversion`,
  `from_midi_conversion`, `root_mode_to_midi_chord`, `prog_conv`, `ui_conv`

## What we're replacing

- **GUI**: Kivy → TBD, see Phase 3 (CustomTkinter is a candidate, not yet decided)
- **Timing**: blocking `time.sleep()` bar loop → non-blocking scheduler driving a real
  MIDI Beat Clock (`0xF8` clock pulses at 24 PPQN, plus Start/Stop/Continue `0xFA`/`0xFC`/`0xFB`)
- **Threading model**: ad hoc `Thread(target=...)` calls → a clean playback engine with
  explicit start/stop/reset, not reliant on global mutable state (`gui`, `midi_progression`,
  `loop`, `bar` as module globals)

## Behaviors & bugs found reading `archive/OLD mOODr_app.py` end-to-end

**Channels & messages**
- Channel 1 (`0x90`/`0x80`) = chord notes, offset **+12** (up an octave) from the raw MIDI root.
- Channel 2 (`0x91`/`0x81`) = bass note (chord root only), offset **-12** (down an octave),
  fixed velocity 127 (not randomized).
- Channel 3 (`0x9B`, no off variant used) = a fixed reference/click note (60, velocity 90)
  sent once per bar and **never turned off** — a hanging note, possibly intentional as a
  drone/reference tone, possibly a bug. Decide in Phase 5.
- All-notes-off is CC `0x7B` on channel 1 (`0xB0`) and channel 2 (`0xB1`), sent on stop,
  chord-button release, and `KeyboardInterrupt`. Channel 3 is never silenced this way.
- Chord note velocities are randomized per note (72-108) on both note-on *and* note-off;
  bass velocity is always 127.
- `midi_in` is opened at import time but never read from anywhere — dead code, safe to drop
  unless slave-clock mode (Phase 6) wants it.

**Chord/scale logic**
- Chord shapes are relative interval stacks: Major `[0,+4,+3]`, minor `[0,+3,+4]`, dim
  `[0,+3,+3]`; a 7th (`M7=+4`/`m7=+3`/`dim7=+4`) is appended only if the mode string
  contains `"7"` (e.g. `"min7"`).
- Root key maps to a MIDI int via `note_to_midi_int(key) + 48` (root sits around C3).
- Progression is always exactly 4 chords, chosen via 4 scale-degree dropdowns.

**Timing/loop**
- `bpm_conversion(tempo) = (1/(tempo/60))*4` — seconds for one 4/4 bar (whole note) at that
  BPM. Each bar: chord+bass on, `time.sleep(clock)`, chord+bass off, immediately re-trigger
  next bar (no gap).
- Latency correction is a half-step nudge only: `clock += latency/2` after each bar, not a
  full resync to wall-clock — will drift under load rather than snap back.
- `loop_length` (1-4, from GUI) counts bars down to 0; at 0 the progression (key/mode/
  numerals) is **re-read live from the GUI**, so changes to the dropdowns only take effect
  at a loop boundary, not mid-loop.
- **Bug**: `stop_loop()` does `time.sleep(int(gui.get_bpm()) + .3)` — sleeps for the raw BPM
  number of seconds (e.g. 80.3s at 80 BPM) rather than one bar's duration. Looks like a
  mistaken call (`bpm_conversion(bpm)` was probably intended). Decide whether to fix or note
  as intentionally dropped behavior in Phase 5.

**Threading/state**
- Every UI action (`play_seq`, `stop_seq`, chord buttons) spawns a bare `Thread` with no
  join/cleanup; `play_loop`/`stop_loop` coordinate purely through unsynchronized module
  globals (`loop`, `bar`, `midi_progression`, `gui`) — a real race between Play and Stop.
- **Bug/order-dependency**: chord preview buttons (`ChordButtons.get_midi_ints`) read the
  global `midi_progression`, which is only ever set inside `play_loop()` — pressing a chord
  button before Play has run once will crash on an undefined global.
- Key/mode/progression are packed into one GUI label string (`"<key> <mode>\n<num1> <num2>
  <num3> <num4>"`) and re-parsed via `selected_key`/`selected_mode`/`selected_prog` — this is
  the string-encoding hack already called out under Phase 4.

## Phases

### Phase 0 — Audit & environment
- [x] Initialize git repo in the project folder, add `.gitignore` (`.venv/`, `__pycache__/`, etc.)
- [x] Set up project tooling with `uv` (`pyproject.toml` + `uv.lock`, Python 3.12 pinned via
      `.python-version`) instead of a manually created `venv`; `customtkinter` and
      `python-rtmidi` added as dependencies and verified to import via `uv run`
- [x] Confirm `python-rtmidi` actually opens a MIDI port and sends messages on the current
      machine — verified by opening the "IAC Driver Bus 1" virtual port for both out and in
      and looping a note-on/note-off through it; both messages were received back
- [x] Decide on Python version / venv setup, add `requirements.txt` or `pyproject.toml`
- [x] Read through `archive/OLD mOODr_app.py` end-to-end and note every behavior to preserve
      (loop length, BPM field, per-chord buttons, bass note on channel 2, velocity
      randomization, latency-correction in the old clock, etc.) — see "Behaviors & bugs
      found" section above

### Phase 1 — Extract the music-theory core (no GUI, no MIDI I/O)
- [ ] Create a standalone module (e.g. `moodr/theory.py`) containing the dictionaries and
      conversion functions listed above, with no dependency on Kivy, rtmidi, or any GUI
- [ ] Add basic tests/sanity checks (e.g. a known root+mode produces the expected MIDI notes)
- [ ] Confirm `snhtri()`'s randomness is seeded/re-seedable in a way that's testable

### Phase 2 — MIDI I/O & clock engine rewrite
- [ ] Wrap `rtmidi` port open/close/list in a small `MidiOutput` class (own the port,
      not module-level globals)
- [ ] Build a MIDI Beat Clock engine: background thread/timer sending `0xF8` at 24 PPQN
      derived from BPM, with Start (`0xFA`)/Stop (`0xFC`)/Continue (`0xFB`) messages
- [ ] Drive chord/bass note-on/note-off scheduling off clock ticks instead of `time.sleep()`
- [ ] Decide master-only vs. master+slave sync (slave = follow an external MIDI clock) —
      master-only is the Phase 2 target; note slave mode as a stretch goal (Phase 6)
- [ ] Port over the parts of `play_loop`/`stop_loop`/`chord`/`midi_message_gen`/
      `bass_message_gen`/`bpm_conversion` that are still needed, rebuilt on the new engine

### Phase 3 — GUI library evaluation
- [ ] Prototype the same 3-4 widgets (key/mode dropdowns, BPM field, play/stop, 7 chord
      buttons) in at least CustomTkinter and one alternative (e.g. PySide6/Qt or DearPyGui)
- [ ] Compare on: packaging/distribution story, responsiveness under the new clock thread,
      how well it fits a "press a button while a clock runs in the background" app, and
      maintenance activity
- [ ] Record the decision and reasoning here once made

### Phase 4 — GUI rebuild on chosen library
- [ ] Rebuild the layout from `archive/OLD mOODr_Kivy_app.kv` (key spinner, mode spinner, 4 numeral
      spinners, BPM input, loop-length selector, play/stop, 7 chord buttons) in the chosen library
- [ ] Wire widgets to Phase 1 theory module + Phase 2 clock/MIDI engine
- [ ] Replace the `key_spinner + 'mode' + '\n' + numerals` string-encoding hack (see
      `selected_key`/`selected_mode`/`selected_prog`) with real state, not a parsed label string

### Phase 5 — Feature parity check
- [ ] Play a progression end-to-end at a chosen BPM and loop length, confirm it matches OLD behavior
- [ ] Confirm individual chord-preview buttons work and release cleanly (all-notes-off)
- [ ] Confirm bass note (channel 2) and the extra channel-3 click/reference note behavior
      are intentionally kept or deliberately dropped (decide, don't silently lose it)
- [ ] Confirm velocity randomization range (72-108) is preserved or intentionally changed

### Phase 6 — Stretch goals (post-parity)
- [ ] MIDI clock **slave** mode (sync to external clock instead of only generating one)
- [ ] Save/load chord progressions and settings
- [ ] Additional modes beyond Major/Minor/Byzantine/snhtri
- [ ] Swing/humanization on note timing and velocity
- [ ] Config for MIDI port selection in the GUI instead of always picking port 0

## Decisions log

- **MIDI clock scope**: Full MIDI Beat Clock output (real `0xF8` sync messages, not just
  accurate internal timing), so m00Dr can act as a clock master for external gear. (2026-08-29)
- **GUI library**: Not yet decided — Phase 3 will evaluate CustomTkinter vs. alternatives
  before committing. `m00Dr.py`'s existing CustomTkinter shell is a starting prototype,
  not a final choice. (2026-08-29)
