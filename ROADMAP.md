# m00Dr Roadmap

MIDI chord-progression generator. Tracking the resurrection of the old Kivy/time.sleep()
implementation into a modern, maintained app while keeping the music-theory core intact.

## Source files

| File | Status | Role |
|---|---|---|
| `archive/OLD mOODr_app.py` | reference only, do not extend | Full working logic: MIDI I/O, chord generation, playback loop, Kivy App class |
| `archive/OLD mOODr_Kivy_app.kv` | reference only, being replaced | Kivy UI layout |
| `moodr/app.py` | the real app (Phase 4) | PySide6 `MainWindow`, wired to `moodr/theory.py` + `moodr/clock.py` + `moodr/playback.py` |
| `main.py` | entry point | `uv run python main.py` launches the real app (also runnable as `uv run python -m moodr`) |

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
- Channel 3 (`0x9B`, no off variant used) = a fixed note (60, velocity 90) sent once per bar
  and never turned off. **Confirmed intentional** (2026-08-30): this was a hacked-together
  drum trigger, not a bug or a reference tone. Left out of the Phase 4 rebuild for now; a
  proper drum-trigger output is planned as a later phase (see Phase 6).
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
- [x] Create a standalone module (`moodr/theory.py`) containing the dictionaries and
      conversion functions listed above, with no dependency on Kivy, rtmidi, or any GUI
- [x] Add basic tests/sanity checks (e.g. a known root+mode produces the expected MIDI notes)
      — see `tests/test_theory.py` (12 tests, run via `uv run pytest`)
- [x] Confirm `snhtri()`'s randomness is seeded/re-seedable in a way that's testable —
      `snhtri()`/`make_snh_intervals()` now take an optional `random.Random` instance so
      tests can pin down deterministic output; default behavior (module-level `random`) is
      unchanged for existing callers

### Phase 2 — MIDI I/O & clock engine rewrite
- [x] Wrap `rtmidi` port open/close/list in a small `MidiOutput` class (own the port,
      not module-level globals) — `moodr/midi_io.py`
- [x] Build a MIDI Beat Clock engine: background thread/timer sending `0xF8` at 24 PPQN
      derived from BPM, with Start (`0xFA`)/Stop (`0xFC`)/Continue (`0xFB`) messages —
      `moodr/clock.py`'s `MidiClock`, using drift-corrected scheduling (`next_tick_at +=
      tick_interval`) rather than the OLD app's half-step latency nudge
- [x] Drive chord/bass note-on/note-off scheduling off clock ticks instead of `time.sleep()`
      — `moodr/playback.py`'s `PlaybackEngine` hooks a tick callback onto `MidiClock` and
      advances one bar (96 ticks = 4 beats at 24 PPQN) at a time
- [x] Decide master-only vs. master+slave sync (slave = follow an external MIDI clock) —
      master-only implemented for Phase 2; slave mode remains a stretch goal (Phase 6)
- [x] Port over the parts of `play_loop`/`stop_loop`/`chord`/`midi_message_gen`/
      `bass_message_gen`/`bpm_conversion` that are still needed, rebuilt on the new engine —
      `midi_message_gen`/`bass_message_gen`/`bpm_conversion`/`random_velocity` ported to
      `moodr/midi_io.py` (velocity randomization now optionally seedable, same pattern as
      Phase 1's `snhtri`); `play_loop`/`stop_loop`'s responsibilities split across
      `PlaybackEngine.start()`/`stop()`/`reset()`, which own their own state instead of
      module globals. `chord()` was a thin send-loop wrapper, folded directly into
      `PlaybackEngine._advance()` rather than kept as a separate function. Progression
      cycling (loop back to the first chord) is implemented; the OLD app's `loop_length` /
      live-GUI-reread-at-loop-boundary behavior is intentionally deferred to Phase 4/5 since
      it depends on GUI state that doesn't exist yet. Verified with 32 passing tests
      (`tests/test_midi_io.py`, `tests/test_clock.py`, `tests/test_playback.py`) covering
      message generation, clock tick/start/stop lifecycle, and bar-boundary chord advancing;
      channel 3's OLD drum-trigger hack was deliberately left out per the Phase 5 decision
      noted above (a proper replacement is planned for a later phase, see Phase 6)

### Phase 3 — GUI library evaluation
- [x] Prototype the same 3-4 widgets (key/mode dropdowns, BPM field, play/stop, 7 chord
      buttons) in at least CustomTkinter and one alternative (e.g. PySide6/Qt or DearPyGui)
      — `prototypes/customtkinter_prototype.py` and `prototypes/pyside6_prototype.py`, both
      built on shared, toolkit-agnostic logic in `prototypes/shared.py` (theory-module
      progression generation + a real `MidiClock`/`PlaybackEngine` wired to a silent
      `NullMidiOutput`) so the comparison is about the toolkits, not duplicated logic. Both
      launched and ran cleanly with no exceptions; headlessly exercising the shared Play-button
      logic (load a 4-chord C-major progression, start the engine/clock, run briefly, stop)
      confirmed correct MIDI output. Interactive click-through and screenshots weren't possible
      in this sandboxed session (no Screen Recording/Accessibility permission), so this was
      verified by launch-and-log-check plus a headless logic run, not visually — worth a quick
      manual click-through before committing further.
- [x] Compare on: packaging/distribution story, responsiveness under the new clock thread,
      how well it fits a "press a button while a clock runs in the background" app, and
      maintenance activity — see comparison notes below
- [x] Record the decision and reasoning here once made — see Decisions log

**Comparison notes**

| | CustomTkinter | PySide6/Qt |
|---|---|---|
| Packaging/distribution | Thin theming layer over stdlib Tkinter; small footprint, no extra native libs to bundle beyond what Python already ships | `pyside6-essentials` + `pyside6-addons` alone are ~420MB to download as dependencies; a packaged app (PyInstaller/briefcase) will be tens-to-100+MB larger than the Tkinter equivalent |
| Responsiveness under the new clock thread | Tkinter widgets are **not thread-safe** — the prototype has to push clock-thread tick events onto a `queue.Queue` and drain it via a periodic `self.after(50, poll)` call on the GUI thread; that plumbing has to be repeated everywhere a background thread needs to touch a widget | Qt's signals/slots auto-marshal a signal emitted from a background thread into a queued, thread-safe call on the GUI thread — the prototype's tick handling is one line (`clock.add_tick_callback(signal.emit)`), no manual queue/poll loop needed |
| Fit for "press a button while a clock runs in background" | Workable, but all thread-safety is the app's responsibility to get right and keep right as the app grows | This is Qt's home turf — `QThread`/signals-and-slots was designed around exactly this pattern |
| Maintenance activity | Actively used, single primary maintainer; underlying Tkinter itself is CPython-maintained and rock solid, so worst case this is a thin, replaceable layer | Backed directly by The Qt Company, very active release cadence, large ecosystem, extensive docs |

Given Phase 2 made a background MIDI clock thread the center of this app's architecture, the
threading/responsiveness column matters more here than for a typical form-and-button app, and
PySide6 needs meaningfully less manual plumbing to stay safe under it. The cost is a much
heavier install/package size, which mostly matters if this were being distributed broadly —
less so for a personal-use MIDI utility.

### Phase 4 — GUI rebuild on chosen library
- [x] Rebuild the layout from `archive/OLD mOODr_Kivy_app.kv` (key spinner, mode spinner, 4 numeral
      spinners, BPM input, loop-length selector, play/stop, 7 chord buttons) in the chosen library
      — `moodr/app.py`'s `MainWindow`, PySide6
- [x] Wire widgets to Phase 1 theory module + Phase 2 clock/MIDI engine — `PlaybackEngine`
      gained an `on_loop_complete` hook (fires once the loaded progression wraps back to its
      first chord) so `MainWindow._reload_progression()` can re-read the numeral/loop-length
      dropdowns live at the loop boundary, matching the OLD app's behavior, without the engine
      itself knowing anything about GUI state. Verified headlessly (Qt's offscreen platform,
      no display needed): constructing `MainWindow`, pressing/releasing chord-preview buttons,
      Play/Stop, and changing key/mode all produce correct MIDI messages against a fake
      recorder, and separately against a **real** `rtmidi` port looped back through "IAC Driver
      Bus 1" (note-on/off messages received matched what was sent). 3 new tests cover
      `on_loop_complete` in `tests/test_playback.py` (35 tests total).
- [x] Replace the `key_spinner + 'mode' + '\n' + numerals` string-encoding hack (see
      `selected_key`/`selected_mode`/`selected_prog`) with real state, not a parsed label
      string — `MainWindow` reads `QComboBox`/`QLineEdit` widget state directly
      (`_selected_progression()`); no packed/parsed string exists anywhere in the new code

**Deliberate deviations from the OLD app (confirmed, 2026-08-30):**
- Chord-preview buttons send a real note-off for the exact chord/bass notes on release,
  instead of the OLD app's blunt all-notes-off CC on channel 1. The CC approach would also
  silence a progression actively playing via Play.
- Mode-change resets all 4 numeral dropdowns to fresh default selections (index 0-3 of the new
  mode's scale degrees) rather than the OLD Kivy Spinner's quirk of keeping stale selected text
  that may not exist in the new value list.

**Bug found during real-hardware testing (fixed same day):** testing against Ableton Live
showed "m00Dr" never appeared as a selectable MIDI input source. Cause: `MidiOutput.open()`
only created a named virtual port as a *fallback* when zero real ports existed on the machine
-- since this machine already has `IAC Driver Bus 1`/`Network Session 1`, it silently opened
`Network Session 1` (port index 0) instead. Fixed: `MidiOutput.open()` now creates a virtual
port named `"m00Dr"` by default (so it's always selectable in DAWs), and only opens a specific
real port when a `port_index` is explicitly passed (falls back to a real port on platforms
without virtual-port support, e.g. Windows). While fixing this, also found and fixed a real
`python-rtmidi` bug: `close_port()` is a no-op for virtual ports (`is_port_open()` stays `True`
forever after "closing" one), and reusing a `delete()`-d port object segfaults the process --
`MidiOutput.close()` now branches on whether the open port was virtual, deleting and replacing
the underlying `rtmidi.MidiOut()` in that case so the wrapper stays safely reusable across
repeated open/close cycles (e.g. repeated Play/Stop). 2 new tests cover this in
`tests/test_midi_io.py` (37 tests total). GUI port selection (choosing a specific real
port instead of the default virtual one) remains a Phase 6 stretch goal.

### Phase 5 — Feature parity check
- [x] Play a progression end-to-end at a chosen BPM and loop length, confirm it matches OLD
      behavior — confirmed live in Ableton Live (2026-08-30): MIDI recorded from m00Dr's
      virtual port shows the expected once-per-bar chord changes (3-4 note voicings) with a
      sustained bass note underneath each chord
- [x] Confirm individual chord-preview buttons work and release cleanly (all-notes-off) —
      confirmed working via mouse in Ableton Live (2026-08-30). Also added number-key (1-7)
      shortcuts for the same 7 chord-preview buttons (`MainWindow.keyPressEvent`/
      `keyReleaseEvent`), verified headlessly with `QTest.keyPress`/`keyRelease`. Note: a
      focused text field (e.g. the BPM box) intercepts number keys as normal typing, same as
      any text input -- shortcuts fire when focus is elsewhere in the window.
- [x] Confirm bass note (channel 2) and the extra channel-3 click/reference note behavior
      are intentionally kept or deliberately dropped (decide, don't silently lose it) —
      channel 3 confirmed as a hacked-together drum trigger (not a bug), intentionally left
      out of the Phase 4 rebuild; a proper drum-trigger output is planned for a later phase
      (see Phase 6)
- [x] Confirm velocity randomization range (72-108) is preserved or intentionally changed —
      kept as the default (it's liked, as a touch of human feel), but made toggleable rather
      than baked in: `midi_message_gen()` gained a `humanize: bool = True` parameter
      (`False` sends every chord note at `FULL_VELOCITY` = 127 instead), `PlaybackEngine`
      exposes it as a live-toggleable `humanize_velocity` attribute, and the GUI has a
      "Humanize velocity" checkbox wired to both Play and the chord-preview buttons. Bass
      notes remain fixed at 127 either way, matching the OLD app. 3 new tests (40 total).

### Phase 6 — Stretch goals (post-parity)
- [x] MIDI clock **slave** mode (sync to external clock instead of only generating one) —
      `moodr/clock.py`'s `MidiClockSlave` follows an external MIDI clock via a new
      `moodr/midi_io.py MidiInput` (mirrors `MidiOutput`, including its virtual-port quirks;
      defaults to creating a virtual destination named `"m00Dr In"` so Ableton can select it
      directly under Sync in Link/Tempo/MIDI preferences, symmetric with `"m00Dr"` on the
      output side). `PlaybackEngine` gained `set_clock()` so the GUI can swap between the
      internal master clock and the external slave clock (only while stopped). The GUI's new
      "External clock sync" checkbox starts the slave clock *listening* immediately on check
      (independent of local Play/Stop) so an incoming external Start can actually be noticed;
      an external Start/Continue triggers local playback the same as pressing Play, an
      external Stop triggers local Stop, and — unlike the master clock — local Stop leaves the
      slave still listening so a later external Start keeps working without re-checking the
      box. Verified end-to-end (not just against fakes): opened the real `"m00Dr In"` virtual
      port, drove it from a *separate* `rtmidi.MidiOut` (simulating Ableton) sending real
      Start/Clock/Stop bytes, and confirmed correct note-on/off output through the actual
      `MainWindow`. 15 new unit tests against fakes (`MidiInput` open/close, `MidiClockSlave`
      tick/transport dispatch, `PlaybackEngine.set_clock`), 54 total.
      **Bug found and fixed while building this**: `PlaybackEngine`'s `on_loop_complete` hook
      (added in Phase 4) was being invoked directly from whichever background thread ticks
      the attached clock (already true for the master `MidiClock`'s own thread, not just the
      new slave clock) — meaning `MainWindow._reload_progression()` was reading `QComboBox`
      widget state from a non-GUI thread the whole time since Phase 4, a real Qt thread-safety
      violation that happened not to crash in testing so far. Fixed by routing
      `on_loop_complete` (and the new external Start/Stop hooks) through a Qt signal
      (`EngineSignals`), the same queued-connection pattern already used for the tick counter.
- [ ] Ableton Link support as an alternative to raw MIDI clock for DAW sync — more robust
      (network-based, bidirectional tempo/transport, no MIDI port routing needed; Ableton has
      a built-in Link toggle). Would need a new native dependency (`abl_link`). Considered
      during the MIDI clock slave mode work above and deliberately deferred: bigger scope,
      separate stretch goal.
- [x] Octave shift control: move the whole progression (chords and bass together) up or down
      by whole octaves. `midi_message_gen()`/`bass_message_gen()` gained an `octave_shift: int
      = 0` parameter (each unit = +/-12 semitones, added on top of the existing +1/-1 octave
      chord/bass split); `PlaybackEngine` exposes it as a live-adjustable `octave_shift`
      attribute; the GUI has an "Octave: N" spinbox (range -2..+2) wired to both Play and the
      chord-preview buttons. **Correctness fix beyond the literal +12/-12 ask**: turning the
      octave knob while a chord is still sustaining could otherwise send a note-off to the
      *new* octave instead of the one actually sounding, leaving the real notes stuck --
      fixed by capturing the octave_shift in effect at note-on time
      (`PlaybackEngine._sounding_octave_shift`, and per-index for the chord-preview buttons)
      and reusing that same value for the matching note-off, regardless of what the knob
      says by then. Verified with a live headless test that changes the octave mid-hold and
      confirms release still turns off the originally-sounding notes. 4 new tests (58 total).
- [ ] Save/load chord progressions and settings
- [ ] Additional modes beyond Major/Minor/Byzantine/snhtri
- [ ] Swing/humanization on note timing and velocity
- [ ] Config for MIDI port selection in the GUI instead of always picking port 0
- [ ] Dedicated drum-trigger output: a proper replacement for the OLD app's channel-3 hack
      (a fixed note sent every bar, never turned off, used to trigger a drum track) — likely
      a configurable channel/note plus a real note-off, rather than a hanging note
- [ ] Top menu bar for lesser-used settings (e.g. humanize velocity, MIDI port selection once
      that exists, clock sync mode), so the main window stays focused on the controls used
      every session

## Decisions log

- **MIDI clock scope**: Full MIDI Beat Clock output (real `0xF8` sync messages, not just
  accurate internal timing), so m00Dr can act as a clock master for external gear. (2026-08-29)
- **GUI library**: **PySide6/Qt**, decided after prototyping both in Phase 3 (see the
  comparison table above). The deciding factor was thread safety: Qt's signals/slots
  auto-marshal calls from the background MIDI clock thread to the GUI thread with almost no
  extra code, where Tkinter/CustomTkinter would need a manual `queue.Queue` + polling loop
  at every point the clock thread touches a widget — a meaningfully bigger risk surface for
  an app whose core architecture (Phase 2) is a background clock ticking continuously while
  the user interacts. PySide6 is LGPLv3-licensed (free for this project, including
  commercial/closed-source use, unlike PyQt's GPL-or-paid-commercial model) at the cost of a
  much heavier install (~420MB of Qt dependencies) and larger packaged app size, which was
  judged an acceptable tradeoff for a personal-use utility. `m00Dr.py`'s old CustomTkinter
  shell will be replaced in Phase 4. (2026-08-30)
