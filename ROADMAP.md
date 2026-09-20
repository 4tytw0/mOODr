# m00Dr Roadmap

MIDI chord-progression generator. Tracking the resurrection of the old Kivy/time.sleep()
implementation into a modern, maintained app while keeping the music-theory core intact.

## Session summary (2026-09-06 → 2026-09-07)

Added three new instrument voices, each on its own MIDI channel, plus on/off toggle buttons
for all four: **chords** (ch. 1), **arpeggiator** (ch. 2 — with Up/Down/Up-Down/Random
direction and 1/4-1/8-1/16 rate selectors), **bass** (ch. 3, moved off ch. 2 to make room for
arp), and an **acid sequencer** (ch. 4 — a locked-in 16-step pattern with a Noise slider for
rests/pitch deviation, a Wide/Narrow deviation toggle, and a Randomize button, per user
request). Also added a live octave-shift control, MIDI clock **slave** mode (m00Dr can follow
Ableton's own clock instead of only generating one), and a UI pass (bigger buttons, a
resizable window). Three real bugs were found and fixed along the way: a Qt thread-safety
violation (GUI state read off the GUI thread), an arp/bass sounding-state tracking cross-wire,
and a loop-boundary race condition that doubled the first chord every cycle (user-reported as
"1 2 3 4 1 1 2 3 4 1", reproduced byte-for-byte with the real threaded clock before fixing).
Full details for each are under their Phase 6 entries below. Running total: **97 tests, all
passing** (`uv run pytest`).

**Open for next time** (stretch goals, no particular priority set):
- Ableton Link support as an alternative to raw MIDI clock (needs a new dependency, `abl_link`)
- Save/load chord progressions and settings
- Additional modes beyond Major/Minor/Byzantine/snhtri
- Swing/humanization on note *timing* (velocity humanization already exists)
- GUI control for MIDI port selection (currently always the default virtual port)
- A real drum-trigger output, replacing the OLD app's abandoned channel-3 hack
- A top menu bar to hold lesser-used settings, decluttering the main window

## Session summary (2026-09-20, roll collision fix)

**Fixed: rolled progressions collapsed onto the tonic.** Reported from real use as "rolling
seems to only change the first two notes then uses the first note for the rest". Not an
arithmetic bug — the slots moved independently, so they collided at exactly the rate three
free draws from seven degrees would. Measured over 20k chained rolls: only **35% came back
with four distinct degrees**, 12% had just two, and **37% put a later slot back on the
tonic**, which (slot 1 being anchored there) reads as the roll reusing the first chord.

`shift_degree()` now takes an `avoid` set: a slot landing on a degree an earlier slot holds
carries on in the same direction by the same interval until it finds a free one. The line
still chooses the direction and size of the move; only the stopping point changes. It always
terminates — seven degrees is prime and a fifths move is never a multiple of seven, so
repeated addition visits every degree. Now **100% four distinct degrees**, all 120 possible
progressions still reachable and still weighted toward fifths-related moves (1.5% down to
0.35%). `Cast.continued` records which slots carried on so `describe()` stays honest about
the arithmetic. **189 tests**, verified in the real GUI.

## Session summary (2026-09-20, acid lane + sync default)

**External clock sync is now on by default.** m00Dr is nearly always run against a DAW or
hardware that owns the tempo, so being clock master by default meant unchecking the box every
session. Enabled in `__init__` *after* `_build_widgets()` rather than via `setChecked()` at
construction, since the toggle handler opens a MIDI port and touches `bpm_edit`; the port open
is now wrapped so a wedged MIDI service drops back to master mode instead of taking the
constructor down with it. **Consequence worth knowing**: Play does nothing until an external
clock is actually running — that is inherent to the default, not a bug.

**Added an acid lane to the UI** (`AcidStep`/`AcidLane` in `app.py`, styled in `theme.py`):
16 cells, one per 16th note, showing the note each step will sound or `·` for a rest, with
the playhead lit and every fourth cell's face lifted so the beats stay countable. It shows
*resolved notes*, not the raw degree offsets — the acid pattern is written relative to the
bar's chord root, so the lane respells itself as the progression, key or scale moves. The
resolver `playback.acid_note_for_step()` was extracted so the sequencer and the lane cannot
disagree, and a test compares the lane's note names against the real note-ons. The playhead
comes from a new `on_acid_step` engine callback through `EngineSignals.acid_step`; it fires
at a 16th note, so `AcidLane.set_playing_step` only restyles the two cells that changed.

The lane reads the bar's root from the slot dropdowns rather than from the engine, matching
`_selected_progression`'s existing read-widget-state philosophy, which also means it displays
correctly while stopped (the engine has no progression loaded until Play).

**185 tests, all passing**, and verified in the real GUI this time: window builds with sync
on and bpm disabled, lane respells on a key change, re-casts on Randomize, playhead tracks
during playback and clears on Stop, and a Roll still defers its cast to the loop boundary.

**Handoff state**: branch `ui-chord-selection-polish`, **9 commits ahead of `main`** —
`git checkout main && git merge --ff-only ui-chord-selection-polish`. The two Circuit bridge
scripts remain deliberately untracked. The acid-offset-via-fifths follow-up noted below is
still open.

## Session summary (2026-09-20, later)

**The acid line is now an I Ching cast.** `_generate_acid_pattern` casts one line per
16th-note step (`oracle.cast_lines(count=16)`) instead of drawing flat: static lines — the
common 3/8 draws — hold the bar's chord root, and only the rare changing lines depart from
it, changing yin into a rest and changing yang into a scale deviation. So a bar is roughly
75% root, 12% rests, 12% deviations, and the departures are rare *by construction* rather
than by probability. The **Noise slider changed meaning** as a result: it is now how much of
the cast is let through (the chance a changing line is honoured rather than folded back onto
the root), 0% being a straight 16th-note tonic pulse and 100% the reading as drawn. Its
default moved 25% → 100%, since anything less is a partly-ignored reading. The **Roll button
now casts the acid line too**, as its own 16-line figure rather than out of the hexagram's
six, queued via `_pending_acid_cast` and spent at the next loop boundary (or at Play, if
stopped) so a Roll still lands all at once instead of swapping the acid line mid-bar.
Randomize stays as a manual re-cast and supersedes a queued roll. **179 tests, all passing**;
not yet run in the real GUI.

**Handoff state**: branch `ui-chord-selection-polish`, now **8 commits ahead of `main`** —
`git checkout main && git merge --ff-only ui-chord-selection-polish`. The two Circuit bridge
scripts remain deliberately untracked.

**Left open on purpose**: a deviated step still picks its offset with a flat `choice()` from
`ACID_NARROW_OFFSETS` / ±7, so the Wide deviation checkbox keeps its documented meaning. The
natural follow-up is to draw that offset from a toss through `LINE_TO_FIFTHS`/`shift_degree`
too, which would put the acid line's leaps on the same diatonic circle of fifths the
progression slots move on. There is no `test_app.py` in this repo, so the deferral path
(`_pending_acid_cast`) is covered by reading, not by a test.

## Session summary (2026-09-20)

Two features and a documentation pass. **Resetting bridges started outside m00Dr** (the app
now finds terminal-launched bridges in the process table, adopts them and can restart them),
**M8-SETUP.md rewritten** for the GUI-managed bridge workflow, and the **circle-of-fifths
Roll** — a button that rewrites key, scale and progression as a fifths move away from the
current selections, with the step sizes drawn by I Ching coin tosses from Tyler's own
`Code-ish/iChing` script, and the cast named on screen by hexagram and trigram. Confirmed by
Tyler running locally: "Everything seems to be fine running locally." **178 tests, all
passing** (`uv run pytest`).

**Handoff state**: branch `ui-chord-selection-polish`, **7 commits ahead of `main` and not
yet merged** — `git checkout main && git merge --ff-only ui-chord-selection-polish`. The two
Circuit bridge scripts (`circuit_to_moodr_bridge.py`, `moodr_to_circuit_bridge.py`) are
Tyler's and deliberately left untracked.

**Found in passing, not fixed** (see the open items below): `tests/test_bridge_manager.py` is
flaky, roughly one full-suite run in four. Diagnosed, not guessed — it is the tests
contaminating each other, not a real bridge and not a product bug. Also still open from
before: the Mac leg of the network-MIDI chain, and the GUI-thread MIDI port listing that can
hang the app.

## Source files

| File | Status | Role |
|---|---|---|
| `archive/OLD mOODr_app.py` | reference only, do not extend | Full working logic: MIDI I/O, chord generation, playback loop, Kivy App class |
| `archive/OLD mOODr_Kivy_app.kv` | reference only, being replaced | Kivy UI layout |
| `moodr/app.py` | the real app (Phase 4) | PySide6 `MainWindow`, wired to `moodr/theory.py` + `moodr/clock.py` + `moodr/playback.py` |
| `moodr/midi_status.py` | Phase 6 | Non-Qt MIDI service status/reset (port listing, `MIDIServer` reset on macOS); `app.py`'s `MidiStatusDialog` is the Qt view onto it |
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

**Investigated (2026-08-31): a "noticeable delay" reported when recording m00Dr's output into
Ableton (MIDI clock slave mode), where the recorded chord lands a fixed ~150-400ms after the
clip's start (not audible in real time, not explained by Count-In (None) or Ableton's reported
14.8ms audio latency). Ruled out on m00Dr's side with real timestamps, not just theory: from
Ableton's actual Start message arriving to m00Dr sending its first note-on was 0.6ms in the
user's own session; a separate loopback test measured m00Dr's `MidiOutput.send()` to a receiver
actually getting the message at 0.44ms. Both directions of the pipeline m00Dr controls are
sub-millisecond. The remaining delay must be inside Ableton's own handling/timestamping of
incoming MIDI when recording, which is outside m00Dr's code and wasn't pursued further** — the
user is fine with current performance. If revisited, the next diagnostic step would be
recording a different MIDI source (e.g. a MIDI keyboard, or Ableton's Computer Keyboard input)
into the same track to check whether the same fixed offset shows up for non-m00Dr input too.
- [ ] Ableton Link support as an alternative to raw MIDI clock for DAW sync — more robust
      (network-based, bidirectional tempo/transport, no MIDI port routing needed; Ableton has
      a built-in Link toggle). Would need a new native dependency (`abl_link`). Considered
      during the MIDI clock slave mode work above and deliberately deferred: bigger scope,
      separate stretch goal.
- [x] Octave shift control: move the whole progression (chords and bass together) up or down
      by whole octaves. `midi_message_gen()`/`bass_message_gen()` gained an `octave_shift: int
      = 0` parameter (each unit = +/-12 semitones, added on top of the existing +1/-1 octave
      chord/bass split); `PlaybackEngine` exposes it as a live-adjustable `octave_shift`
      attribute; the GUI has an "Octave: N" spinbox (range -1..+1, one octave either way) wired
      to both Play and the chord-preview buttons. **Correctness fix beyond the literal
      +12/-12 ask**: turning the
      octave knob while a chord is still sustaining could otherwise send a note-off to the
      *new* octave instead of the one actually sounding, leaving the real notes stuck --
      fixed by capturing the octave_shift in effect at note-on time
      (`PlaybackEngine._sounding_octave_shift`, and per-index for the chord-preview buttons)
      and reusing that same value for the matching note-off, regardless of what the knob
      says by then. Verified with a live headless test that changes the octave mid-hold and
      confirms release still turns off the originally-sounding notes. 4 new tests (58 total).
- [x] Bass on/off toggle button. `PlaybackEngine` gained a `bass_enabled` property (default
      `True`); setting it `False` while a bass note is currently sounding sends its note-off
      immediately (a mute should mute right away, not wait for the next bar boundary) --
      captured the same way as `_sounding_octave_shift` so the note-off always targets what
      note-on actually turned on, not whatever the live setting is by the time it fires. The
      GUI's checkable "Bass" button wires to this and to the chord-preview buttons (captured
      per-index, same pattern as the octave shift). 5 new tests (63 total).
- [x] Arpeggiator on its own MIDI channel, with channels reassigned to chords=1, arp=2,
      bass=3 (previously chords=1, bass=2). `moodr/playback.py` gained `ARP_CHANNEL`,
      `ARP_RATE_TICKS` (a dict of rate-name -> ticks-per-step, only `"1/8"` used today) and
      `ARP_PATTERNS`/`_arp_sequence()` (only `"down"` implemented today) so a rate/pattern
      selector can be added later as GUI dropdowns wired to `PlaybackEngine.arp_rate`/
      `arp_pattern` attributes, with no engine changes needed. The arp steps through the
      currently-sounding chord's notes on its own tick subdivision, independent of the
      once-per-bar chord advance; each new chord resets the pattern to its top rather than
      continuing mid-sequence. `arp_enabled` (default `True`) is a property mirroring
      `bass_enabled`: toggling it off mid-note immediately silences the sounding arp note
      instead of waiting for the next step. The GUI has a checkable "Arp" button (matching
      Bass's non-expanding sizing, not wired to the chord-preview buttons -- those stay
      chord+bass only, since arp is a playback-only voice). Verified through the real
      `MainWindow` (all 3 channels producing distinct note-ons) and headlessly (toggle
      wiring, chord-preview buttons confirmed arp-free). **Bug found and fixed while
      building this**: `_turn_off_arp_note()` had a copy-paste line that also cleared
      `_sounding_position` (the *chord* tracking, not arp's), which caused the chord to
      silently stop advancing after the arp's second step in initial testing -- caught by a
      full pytest run showing 5 unrelated-looking failures, not a arp-specific test.
      5 new tests (68 total).
- [x] Arp direction and rate selectors. `_arp_sequence()` replaced by `_arp_note_for_step()`,
      which now supports all 4 `ARP_PATTERNS`: `"up"`, `"down"`, `"up_down"` (ping-pong,
      without repeating the top/bottom note at the turn) and `"random"` (a fresh independent
      pick each step, using the engine's seedable `_rng` like the rest of the app). The GUI's
      new "Arp" row has a Direction dropdown (Down/Up/Up-Down/Random, default Down) and a
      Rate dropdown (1/4, 1/8, 1/16, default 1/8), both live-wired to
      `PlaybackEngine.arp_pattern`/`arp_rate` -- exactly the follow-up planned when the arp
      was first built, no engine rework needed. 7 new tests (75 total): pure pattern-sequence
      tests for each direction plus one confirming the engine actually uses a changed
      pattern/rate live. Confirmed working end-to-end in Ableton Live by the user (with real
      instruments on the chord/arp/bass channels) after an initial false alarm turned out to
      be "External clock sync" checked with Ableton not actually sending clock pulses (ticks
      stayed at 0) -- not a bug, since master-mode playback (or slave mode once Ableton's
      Sync is properly enabled per the earlier MIDI-clock-slave-mode section) works correctly.
- [x] Acid sequencer on a 4th MIDI channel (channel 4). A locked-in 16-step pattern (one bar's
      worth at 16th-note resolution: `ACID_STEPS=16` * `ACID_STEP_TICKS=6` = `TICKS_PER_BAR`)
      where each step is a rest, the bar's home note (the sounding chord's root -- moves with
      the progression, same root as Bass), or a note some number of scale degrees away from
      that root. `PlaybackEngine.set_scale()` feeds it the full 8-degree key/mode scale
      (separate from `load_progression()`'s sliced 4-chord progression) so deviated notes have
      something to draw from. A single "Noise" slider (0-100%) is the *independent*
      probability, per step, of (a) being a rest and (b) — if not a rest — being deviated
      instead of the home note. A "Wide deviation" checkbox toggles whether deviations can
      land on any other scale degree or stay within 2 degrees of home. Per user request, the
      pattern does **not** regenerate every bar -- it locks in (generated once at construction,
      and again whenever "Randomize" is clicked) and repeats until explicitly re-rolled.
      `acid_enabled` (default `True`) mirrors `bass_enabled`/`arp_enabled`: toggling off
      mid-note mutes immediately. Verified through the real `MainWindow` (channel 4 producing
      note-ons) and headlessly (all control wiring, randomize producing a different pattern).
      16 new tests (91 total): pure pattern-generation tests (zero/max noise edge cases,
      seedable reproducibility, narrow vs. wide offset ranges) and engine-level tests (home
      note tracks the chord root live, deviation reads through `set_scale()`, rest steps are
      silent, immediate mute, `stop()` cleanup).
      **Bug found and fixed while reviewing this code**: `_turn_off_arp_note()` had a leftover
      line clearing `_sounding_bass_enabled` -- unrelated to the arp, and since arp steps fire
      far more often than bar boundaries, this silently broke the next bar's bass note-off
      almost every time, leaking stuck bass notes over a session. Fixed in its own commit
      first, with a regression test confirmed to fail against the reintroduced bug before
      confirming the fix (76 tests at that point, before acid's own 91).

**Bug found and fixed (2026-09-06): a real loop-boundary race condition**, reported by the
user as the chord progression audibly playing "1 2 3 4 1 1 2 3 4 1" -- chord 1 doubling once
per cycle. Root cause: `on_loop_complete` (the hook `MainWindow._reload_progression()` uses to
re-read the numeral/loop-length dropdowns live at each loop boundary) fires as a **queued
cross-thread Qt signal** in real usage, since the real clock ticks on its own background
thread and reading `QComboBox` state must stay on the GUI thread. That means the reload can
arrive *after* the clock thread has already advanced one or more further bars past the
boundary that triggered it -- and `_reload_progression()` was calling `load_progression()`,
whose `reset()` unconditionally snapped `position` back to `0`, replaying the first chord.
Reproduced exactly (byte-for-byte matching the user's reported "1 2 3 4 1 1 2 3 4 1 1"
sequence) using the real threaded `MidiClock` + real `MainWindow`, after two earlier,
simpler synchronous-clock tests (which don't have this race at all -- same-thread Qt signals
are delivered synchronously, not queued) came back clean and didn't reproduce it. Fixed with a
new `PlaybackEngine.set_progression()` that swaps in new chord/root data without resetting
position or in-flight sounding-note tracking; `_reload_progression()` now uses it instead of
`load_progression()` (which remains correct for the initial Play-press load, where a real
reset is wanted). Verified the fix resolves the real threaded-clock reproduction (clean
`1,2,3,4,1,2,3,4,...` afterward) and added 2 regression tests -- one of which explicitly
simulates the delayed-arrival race deterministically and was confirmed to fail against the old
`load_progression()`-based reload before confirming the fix (93 tests total).
- [x] Chords on/off toggle button, for parity with Bass/Arp/Acid. `PlaybackEngine` gained a
      `chords_enabled` property (default `True`) with the same immediate-mid-note-mute
      behavior as the others. The GUI's "Chords" button also gates the chord-preview buttons
      (per-index captured, same pattern as Bass/octave there), matching how Bass already did.
      4 new tests (97 total).
- [x] Channel reassignment (2026-09-19, for M8 hardware use): chords stay ch1, but
      **bass = ch2** (was ch3), **arp = ch3** (was ch2), **acid = ch5** (was ch4, ch4 left
      unused) — `moodr/playback.py`'s `CHORD_CHANNEL`/`BASS_CHANNEL`/`ARP_CHANNEL`/
      `ACID_CHANNEL`. Found and fixed a real conflict while making this change: bumping
      `ARP_CHANNEL` to channel 3 collided with `BASS_CHANNEL`'s old default (also channel 3)
      whenever the "Bass→Ch2" toggle was left off (its default state) — fixed by moving
      `BASS_CHANNEL`'s own default to channel 2 rather than relying on that toggle, so the
      new channel map holds with no extra step. That leaves the "Bass→Ch2" toggle currently a
      no-op (kept in place in case a future Circuit-specific map diverges from this default
      again — see its updated tooltip/comment in `app.py`). All 97 existing tests still pass
      unmodified (they reference the channel constants symbolically, not literal numbers).
- [x] MIDI Status dialog + MIDI service reset. `moodr/midi_status.py` (no Qt dependency,
      same split as `clock.py`/`midi_io.py`): `list_ports()` and `reset_midi_server()` (kills
      macOS's CoreMIDI `MIDIServer`; a no-op returning `False` on other platforms). `app.py`'s
      new `MidiStatusDialog` (opened via a "MIDI Status" button in the performance row) shows
      live input/output port lists and whether this app's own expected ports ("m00Dr", and
      "m00Dr In" if external clock sync has ever been enabled) are currently present, plus a
      "Reset MIDI Server" button. `MainWindow._on_reset_midi_server` does the real work: resets
      the service, then reopens this app's own ports on it (a MIDIServer reset invalidates
      every virtual port open in *any* process, not just this one — the slave clock is
      stopped/reopened/restarted through its normal lifecycle methods rather than by touching
      its MIDI input directly, so it re-subscribes its callback correctly). Does **not** and
      cannot restart other MIDI apps/bridges on the same machine; the dialog's tooltip says so.
      Verified headlessly: constructing the dialog, refreshing its port lists, and — with
      `reset_midi_server()` mocked out so the test doesn't kill the real service — driving
      `_on_reset_midi_server()` end-to-end with external clock sync active beforehand,
      confirming the output port, the slave input port, and the slave clock's running state
      all come back correctly afterward. 3 new tests for `midi_status.py` (100 total); the
      dialog/reset-flow itself isn't in the permanent suite, following this project's existing
      practice of verifying `MainWindow`-level Qt behavior via headless one-off runs rather
      than a committed `test_app.py`.
- [x] Removed the "ticks: N" and chord-preview status-line debug labels from the main window
      (`tick_label`/`status_label`, plus the now-unused `TickSignal` class and its
      add/remove_tick_callback wiring in `_on_sync_mode_toggled` -- nothing else consumed tick
      events once the label was gone). `PlaybackEngine`'s own internal `_on_tick` (its
      scheduling logic, not the GUI's) is untouched.
- [x] "Open M8 UI" + "Change..." buttons on the MIDI Status dialog, to launch the M8 display
      client (m8c) alongside m00Dr. Since this repo is public and m8c's install location is
      per-machine, the path isn't hardcoded: the first click prompts via `QFileDialog`
      (`.app` bundles selectable on macOS) and remembers the choice in `QSettings`
      (`org="m00Dr", app="m00Dr"`, key `m8_ui_path`); later clicks launch directly, and
      re-prompt automatically if the remembered path no longer exists. Verified headlessly
      with `QSettings` repointed at a scratch ini file and both `QFileDialog.getOpenFileName`
      and `subprocess.Popen` mocked: first click prompts and launches (`open <path>.app` on
      macOS, the raw executable otherwise) and persists the choice; a second click launches
      directly with no re-prompt.

      **Bug found and fixed same day**: reported as "Open M8 UI opens a Finder window, not
      m8c" — native file-dialog handling of `.app` bundles isn't fully reliable across
      Qt/macOS versions, so a user can end up selecting something *inside* the bundle (or a
      plain folder) instead of the bundle itself; the un-resolved path then didn't end in
      `.app`, and `open`-ing a plain folder is exactly what shows a Finder window instead of
      erroring. Fixed with `midi_status.resolve_app_bundle()` (moved there from `app.py`
      since it's pure path logic, no Qt needed, same split as everything else in that
      module): climbs back out to the nearest `.app` ancestor if the picked path is inside
      one, otherwise leaves it unchanged. `_launch_m8_ui` also now explicitly rejects a plain
      folder with a clear warning dialog instead of silently calling `open` on it. 3 new
      tests for `resolve_app_bundle()` (103 total); verified headlessly end-to-end for both
      the "picked something inside the bundle" case (resolves and launches correctly) and
      the "cached path is a plain folder" case (warns instead of opening Finder).

      **That fix didn't resolve the real report, though** — still "opens a Finder window"
      after a fresh restart. The actual root cause was one level up: `QSettings(SETTINGS_ORG,
      SETTINGS_APP)` on macOS defaults to NativeFormat, i.e. CFPreferences/NSUserDefaults,
      which needs the process to be a properly registered app bundle to persist reliably. A
      bare `uv run python main.py` process isn't one — confirmed directly with `defaults read
      com.m00dr.m00Dr`, which reported the domain not existing at all despite the app calling
      `setValue()`. So the remembered path was never actually being read back, meaning
      `_on_open_m8_ui_clicked` likely fell through to the file-picker prompt on *every*
      click, not just the first — which is what probably looked like "a Finder window"
      opening. Fixed by forcing `QSettings.IniFormat` (a new `_m8_ui_settings()` helper) so
      the M8 UI path is stored in a plain, inspectable `.ini` file under Qt's per-user config
      dir instead of the OS-native store; confirmed the value now round-trips correctly
      across separate process runs, including checking the file's actual on-disk contents
      directly (not just Qt's own read-back).
- [x] "Reset MIDI Server" reliability fix (2026-09-19). Real-world use turned up two problems
      the earlier implementation's own testing (which mocked `reset_midi_server()` out) never
      exercised: (1) killing MIDIServer while a *separate* process has a port open can crash
      that process outright at the C++ level (confirmed via a real macOS crash report naming
      one of this session's MIDI bridge scripts) -- `MidiStatusDialog`'s tooltip now says this
      explicitly rather than just "will need restarting separately"; (2) `_on_reset_midi_server`
      reopened this app's own ports immediately after the kill with no delay, which raced
      MIDIServer's respawn and left the ports **silently missing entirely** in real use --
      confirmed directly via a live port listing after clicking the button, not just a stale
      display-refresh. Fixed with a settle delay (`RESET_SETTLE_SECONDS = 0.5`) before
      reopening, plus a new `_reopen_port()` helper that verifies the reopened port actually
      appears in a fresh port listing and retries once after a longer pause if not, only then
      giving up and telling the user (via a message box) to restart the app -- silent failure
      was the actual bug, not just needing a longer delay.

      **That fix caused a worse bug, found the same day**: the settle-delay + retry version
      above ran synchronously on the GUI thread, and in real use a port open/close call didn't
      just fail slowly -- it hung indefinitely (a blocking C-level `rtmidi`/CoreMIDI call that
      never returned), which froze the **entire app**, not just the dialog, since Qt's event
      loop is single-threaded: Refresh, Close, everything stopped responding, confirmed by the
      process staying alive (not crashed) but consuming zero CPU (blocked in a syscall, not
      spinning) for far longer than the code's own few seconds of scripted delays could
      explain. Required a hard `kill -9` to recover -- there was no way to interrupt it from
      the UI. Fixed by moving the whole reset+reopen sequence onto a background
      `threading.Thread` (new `MidiResetSignals` class marshals the result back to the GUI
      thread via a Qt signal, same pattern `MidiClock`'s own background thread already uses);
      a hang there now just leaves the reset perpetually "in progress" instead of freezing
      anything else. Also added a narrower guard (`_midi_reset_in_progress`, checked in
      `_on_play`/`_on_stop`/`_on_chord_pressed`) against Play/Stop/chord-preview touching
      `self._midi_output`/`_slave_input` concurrently with the background thread's own
      close()/open() calls on those same objects -- deliberately *not* applied to
      `_on_chord_released`, since dropping a note-off to avoid a rare race would risk
      re-creating the exact stuck-note problem that prompted using this feature in the first
      place. First tried disabling the whole window (`self.setEnabled(False)`) for the
      duration instead of a flag -- headless-verified that this **also disables an
      already-open `MidiStatusDialog`** (it's a child of `MainWindow`), including its own
      Close button, which would leave the user unable to even dismiss the dialog if the
      background thread hung again. Verified headlessly: a normal reset still completes and
      clears the guard flag; `_on_play`/`_on_chord_pressed` are confirmed no-ops (no
      exception, no MIDI sent) while the flag is set; and, critically, both `MainWindow` and
      an open `MidiStatusDialog` stay `isEnabled() == True` throughout a reset now.
- [x] Bridge process management (2026-09-19), prompted directly by the day's actual failure
      pattern: a bridge process can survive a MIDIServer reset without crashing and still end
      up with a permanently stale CoreMIDI connection of its own (confirmed: one bridge's log
      showed zero successful reconnects to `"m00Dr In"` for nearly an hour, while a brand new
      process queried at the same moment saw that same port fine -- a bridge not crashing was
      never proof it was healthy). New `moodr/bridge_manager.py` (no Qt dependency, same split
      as the other non-Qt modules): `ManagedBridge` wraps a bridge script as a `subprocess.Popen`
      child of this app (`sys.executable` directly, not `uv run` -- these scripts only need
      `rtmidi`, already installed in this app's own environment), with `start()`/`stop()`
      (terminate, falling back to kill after a timeout)/`restart()`/`is_running`/`uptime_seconds`;
      `discover_bridges()` finds `moodr_to_m8_bridge.py` and `m8_to_moodr_transport_bridge.py`
      next to `main.py` if present, skipping either that doesn't exist since these are specific
      to an M8/Teensy setup, not something every m00Dr user has. `MidiStatusDialog` gained a
      "Bridges" section: one row per discovered bridge with a live-updating "Running since
      HH:MM:SS (Nm Ns)" / "Not running" label (a 1s `QTimer` that only touches the bridges' own
      in-process state, not a full port refresh) and a Start/Restart button. "Reset MIDI
      Server" now also restarts any bridge that's currently running through this dialog, as
      part of the same background-thread flow. Real limitation, stated in the code and the
      dialog: **only a bridge started through this dialog is tracked** -- one already running
      in its own terminal (how these scripts were run all session, including the one that went
      stale) is invisible to this app until restarted through here instead; there's no way to
      discover or adopt a process this app didn't start itself. **(Lifted 2026-09-20 --
      see "Reset bridges started outside m00Dr" below.)** 10 new tests
      (`tests/test_bridge_manager.py`, 113 total) against a real harmless sleep-script
      subprocess, not mocks -- start/is_running/uptime, stop actually terminates the OS process
      (checked via `os.kill(pid, 0)` raising `ProcessLookupError`), restart replaces the PID and
      resets uptime, starting an already-running or stopping a never-started bridge is a no-op,
      a missing script is a no-op, and `discover_bridges()` only returns scripts that exist.
      Verified headlessly end-to-end beyond the unit tests too: the dialog's Start button
      launches and updates its own label/button live, and triggering a full (mocked) MIDI
      reset while a fake bridge is running confirms it gets restarted (a new PID) as part of
      that same flow.
- [ ] Save/load chord progressions and settings
- [ ] Additional modes beyond Major/Minor/Byzantine/snhtri
- [ ] Swing/humanization on note timing and velocity
- [ ] Config for MIDI port selection in the GUI instead of always picking port 0
- [ ] **Fix the flaky bridge tests** (found 2026-09-20; about one full-suite run in four,
      and the failing runs take ~16s against a normal ~1.5s).
      Cause: the `bridge_script` fixture gives every test a file with the *same* basename,
      `moodr_to_m8_bridge.py`, and `looks_like_bridge_process()` matches on basename, not on
      the full path. Several tests spawn genuinely orphaned copies (`_spawn_orphan`). So a
      test that only excludes *its own* child — `test_a_bridge_this_app_started_is_not_also_
      counted_as_external` is the clearest — can see another test's orphan, still on its way
      out, and count it as external. A slow run widens that window, which is why the failures
      cluster and why the bridge file passes when run on its own.
      Ruled out: a real bridge running on the machine (one was up throughout, including
      during every passing run), and processes leaked past the end of a run (checked the
      table straight after one; nothing but the real bridge). The app itself is fine — it
      spawns with `sys.executable`, so its child *is* the interpreter and is excluded by pid;
      there is no `uv` wrapper grandchild to miss.
      Fix: give the fixture a unique basename per test (the name only has to end in something
      the matcher recognises) so no two tests can ever see each other's processes.
- [ ] Dedicated drum-trigger output: a proper replacement for the OLD app's channel-3 hack
      (a fixed note sent every bar, never turned off, used to trigger a drum track) — likely
      a configurable channel/note plus a real note-off, rather than a hanging note
- [x] **UI/UX pass 2b: square the key / scale / progression selectors.** Reported as "can we
      make the key, scale, & chord selections more square instead of thin like they currently
      are" — the key and scale dropdowns each took half the window at Qt's default 36px
      height (~12:1 ribbons) and the four progression slots spread across the full width.
      - **The blocker, found by trying the obvious fix first**: raising `minimumHeight` on a
        native macOS QComboBox does nothing visible. The platform draws the combo at a fixed
        bezel height and simply centres it in whatever space it's given, so the row just
        gained whitespace. Getting a taller combo *requires* styling it, since a styled
        widget is no longer drawn by the platform style. So `theme.py` now styles QComboBox
        to match the buttons, and the height takes effect.
      - **The drop-down arrow had to be rebuilt.** Styling `::drop-down` stops the platform
        drawing its arrow, and QSS can't draw one itself: the usual CSS-triangle trick (a
        zero-sized box with only a coloured top border) renders as a filled magenta rectangle
        in Qt, and `image: none` leaves no affordance at all. Both were tried and screenshot
        side by side. The fix is a real SVG chevron, written to the Qt cache directory at
        startup and named after its colour, so it can be tinted with the palette accent
        rather than shipped as a fixed-colour asset. `chevron_path()` returns "" if the file
        can't be written, and the arrow rule is then simply omitted rather than the whole
        stylesheet failing.
      - **Widths are measured, not guessed.** `_fit_width()` sizes each selector from
        `QFontMetrics` over the actual strings it can hold, so adding a mode or changing how
        chord names are spelled can't silently start eliding text. The progression slots are
        measured against `all_slot_labels()` — every label possible across all 12 keys × 8
        modes — rather than the current key/mode, so switching key doesn't resize the row
        under the pointer. `_row_width_limit()` caps each width so a fixed-width row still
        fits the minimum window, since a fixed-width widget can't shrink.
      - Result: key 71×72 (essentially square), scale 118×72, each slot 145×72, down from
        ~460×36 and ~230×36.
      - The BPM field was left as the one remaining native black box among the styled combos,
        so it is styled to match (scoped by object name, not to QLineEdit generally — QSpinBox
        holds a QLineEdit inside it, and a blanket rule would restyle the octave spinbox's
        editor while leaving its native buttons). It also dropped from over half the transport
        row to 96px, a three-digit field's worth; the space went to Play and Stop.
      - **Window floors corrected.** `MINIMUM_WINDOW_SIZE` was 720×420, which the layout could
        not actually honour: at the new selector height the rows *overlapped*, and even before
        this change 720 was too narrow — the performance row's "Bass→Ch2" and "MIDI Status"
        buttons were clipped at that width. Qt reports the layout's real minimum as 802×522,
        so the floor is now 820×540 and the default 1000×680, the latter chosen so the chord
        pads can still reach `CHORD_BUTTON_MAX_HEIGHT` instead of sitting pinned at their
        minimum.
      - Re-verified: 123 tests still pass, the 22-check behavior script still passes in full,
        the threaded-clock run still walks the highlight 0→1→2→3→0, and screenshots were taken
        at the new minimum (nothing overlapping or clipped), at the new default, and in a
        forced light palette.
- [x] **Reset bridges started outside m00Dr** (2026-09-20), asked for as "can we make m00Dr
      able to reset the midi bridge on the desktop?". Lifts the limitation logged above: until
      now only a bridge *launched from the dialog* could be restarted, while M8-SETUP.md tells
      you to run them in their own terminal — so the one process most likely to have been up
      long enough to go stale was exactly the one the app could not touch, and "Restart" would
      have cheerfully started a second copy fighting it for the same ports.
      - `bridge_manager.scan_processes()` reads the whole process table in one `ps` call
        (`pid,ppid,etime,command`); `ManagedBridge.refresh_external()` picks out copies of its
        own script. `is_running` is now "running at all, whoever started it", which is what
        lets `start()` refuse to create a duplicate, and `stop()`/`restart()` SIGTERM then
        SIGKILL external processes (polling with signal 0, since they aren't children and
        there's no `waitpid` to call). `restart()` hands ownership to the app.
      - **The matching rule is the whole safety story here, and the first version was
        dangerous.** Matching "python" and the script name anywhere in the command line looked
        reasonable and was not: it also matches a shell running a one-liner that merely
        mentions both. This was found the hard way — the test run killed its own shell, twice,
        because the harness executes commands as `zsh -c '<script>'` and the script text
        contained both strings. An editor holding the file open or a `tail` of its log would
        have gone the same way. The rule now tests **argv[0] specifically**: the process must
        *be* a Python interpreter with the script among its arguments, or the script itself for
        a shebang launch. `ancestor_pids()` adds a second layer, excluding this process and
        everything up its ppid chain, so a bridge can never resolve to the terminal or IDE that
        launched m00Dr.
      - `uv run python -u <script>` is two processes: the `uv` wrapper (argv[0] "uv", not
        matched) and the interpreter it spawns (matched). Verified that killing the interpreter
        takes the wrapper with it, so nothing is left behind.
      - The dialog rescans on its own 3s timer rather than the 1s uptime tick, because a scan
        shells out to `ps` and reads ~900 processes (~30ms). Status reads "Running since
        13:42:05 (3s) (outside m00Dr)" for one the app didn't start. "Reset MIDI Server" now
        rescans and restarts those too — the case that actually mattered — after the server
        reset rather than before, so it also catches a bridge the reset itself killed.
      - The two Circuit bridge scripts were added to `KNOWN_BRIDGE_SCRIPTS` alongside the M8
        pair, since `discover_bridges()` already skips scripts that don't exist and there was
        no reason the Circuit rig couldn't be managed the same way.
      - 14 new tests (137 total): etime parsing in all four ps formats, the matcher against
        real launch shapes *and* against the shell/editor/tail false positives that motivated
        it, ancestor walking including a ppid cycle, and end-to-end against genuinely orphaned
        processes (spawned via a shell that exits, so they are not children of the test — a
        killed child that nobody reaps stays a zombie and still answers signal 0, which would
        have made the kill look like it worked when it hadn't). Plus a 13-check script driving
        the real dialog against the real `moodr_to_m8_bridge.py` launched the documented way.

- [ ] **Network MIDI: m00Dr (Mac) -> desktop -> Anbernic -> M8.** Two of three hops already
      exist; only the Mac leg is missing. Full context lives in `SSHstuff/CLAUDE.md`
      ("rtpmidid" and "Network MIDI link to the Anbernic RG353V") and
      `anbernic backup/M8C-SETUP.md` section 3 -- read those first, they have the hard-won
      details. State as of 2026-09-20:
      - *Desktop (`cacheos`, passwordless `ssh cacheos`)*: rtpmidid runs as a `systemd --user`
        service and is active. `ufw` default-deny-incoming is confirmed to block inbound
        AppleMIDI/aseqnet, so **every link must have the desktop dial out**, which is why the
        desktop is the aseqnet *client* despite being the always-on machine. Doing it the
        other way needs a sudo password nobody has.
      - *Desktop -> Anbernic*: built and verified with a real note, but **down whenever m8c
        isn't running** -- the handheld's aseqnet *server* is started by `m8c.sh` and lives
        only for that session. Checked 2026-09-20: `aseqnet-anbernic` sits in `activating`
        (its normal idle retry state) and no `Net Client` appears in the desktop's
        `aconnect -l`, even though `RK3566.local` pings. A `Net Client-Network` record still
        shows in `dns-sd -B _apple-midi._udp` -- that is a stale announcement, not a live path.
      - *Anbernic -> M8*: `m8c.sh`'s aconnect loop wires ALSA clients to the M8 both ways.
        Dry-run verified with the Teensy attached (client 24); m8c has never actually been
        launched on the hardware.
      - **To do**: (1) enable a Network MIDI session on the Mac in Audio MIDI Setup -> MIDI
        Studio -> Network (GUI-only, needs Tyler; there is no session at all today).
        (2) Add a `[connect_to]` block to the desktop's `~/.config/rtpmidid/rtpmidid.ini`
        aimed at that session -- the file currently has only the commented examples.
        (3) `aconnect` the resulting rtpmidid port through to the handheld's port, the job
        `~/.local/bin/aseqnet-anbernic-wire.sh` already does for the other direction.
        (4) Add a Mac-side forwarder from m00Dr's virtual `"m00Dr"` port into the network
        session port -- a near-copy of `moodr_to_m8_bridge.py` with a different destination,
        which `bridge_manager.KNOWN_BRIDGE_SCRIPTS` then picks up for free.
      - **Expect poor timing, and say so before building much.** `RK3566.local` pinged at
        32-190ms with ~79ms stddev (2.4GHz-only Wi-Fi), and this adds two hops. The current
        rig has the M8 as transport master sending clock *back*, which that jitter will wreck;
        unchecking "External clock sync" at least takes the clock out of the round trip.
        Realistically this is for triggering and auditioning, not tight sync.
      - Note this **replaces** the USB path rather than adding to it: the Teensy has to be
        plugged into the Anbernic for this topology, not the Mac.

- [ ] UI/UX pass 3, what pass 2/2b left: the rows still have no labels, so "E"/"Minor 7",
      the BPM field and the loop-length box are unlabelled (captions over the selector blocks
      would also make the empty space to the right of the key/scale row read as deliberate
      grouping rather than a gap); "Acid" stays at its minimum width while "Arp" stretches
      across its row, which looks accidental; and the octave QSpinBox is now the only
      remaining natively-drawn dark box, which needs its own up/down arrow images to style
      the way the combos were. Grouping the voice rows into titled boxes would also make the
      channel layout (chords ch1, bass ch2, arp ch3, acid ch5) visible somewhere other than a
      tooltip. Note that merging the key/scale row into the progression row was considered and
      rejected: six fixed-width selectors don't fit the minimum window width without eliding.
- [ ] Top menu bar for lesser-used settings (e.g. humanize velocity, MIDI port selection once
      that exists, clock sync mode), so the main window stays focused on the controls used
      every session
- [x] **UI/UX polish**: larger buttons and window/layout proportion scaling. Default window
      grew from a fixed `640x260` to a resizable `960x560` with a `720x420` floor
      (`MainWindow.setMinimumSize`). Chord-preview and Play/Stop buttons use
      `QSizePolicy.Expanding` with min/max height bounds (chord buttons 72-120px, Play/Stop/
      Bass 48-72px) via a new `_grow()` helper, so they genuinely grow when the window is
      resized larger but stop at a sane cap rather than becoming disproportionate; regular
      controls (dropdowns, BPM field, checkboxes) got a smaller but still larger-than-default
      minimum height and font size. The single cramped 10-widget top row was split into
      logical rows (key/mode; numeral dropdowns; BPM/loop/Play/Stop; humanize/octave/bass/
      sync). Verified two ways: headlessly (resizing 960x560 -> 1600x900 -> 720x420 and
      confirming chord-button height grows, caps at 120px, and floors correctly; chord preview
      still functions) and visually, with real screenshots taken in this session (screen
      recording permission was available this time, unlike Phase 3/4) -- the first screenshot
      caught a real bug live (the "Bass" toggle button was stretching across almost the whole
      window, since it was the only `Expanding`-policy widget sharing a row with fixed-size
      checkboxes and so claimed all the leftover space), which was then fixed by only growing
      Bass's height/font, not its width, and re-verified.
- [x] **UI/UX pass 2: button states and chord selection.** The five voice toggles
      (Chords/Bass/Stabs/Arp/Acid) were separated from each other only by a few shades of
      gray in Qt's native macOS rendering, so "is Bass on?" wasn't answerable at a glance
      while playing; and the seven chord pads showed a bare roman numeral with no indication
      of what chord they'd actually play, which of the 1-7 number keys triggered them, or
      which chord the sequencer was on.
      - New `moodr/theme.py` holds a QSS stylesheet that gives a checked QPushButton a
        filled-accent-with-bold-text look against a dim outline when unchecked. Every colour
        is derived from the live QPalette rather than hardcoded, so it follows light/dark
        mode and the user's own macOS accent colour. It reads `QPalette.Accent` (the real
        accent, `#923796` here) and *not* `QPalette.Highlight` — Highlight is the selection
        colour, already blended toward the window background and swapped for gray when the
        window isn't frontmost, which would have made every "on" toggle change colour
        whenever the app lost focus. The stylesheet is scoped to QPushButton and the chord
        pads only: styling a widget at all in Qt opts it out of native rendering, and the
        combo boxes / check boxes / Noise slider already read clearly.
      - New `ChordPad` class in `app.py` replaces the plain QPushButton chord pads. A
        QPushButton can only show one run of text in one font, so the pad's three pieces of
        information live in three `WA_TransparentForMouseEvents` QLabels inside it (key hint,
        roman numeral, chord name) while the button keeps all of its normal press/release
        behavior, including `setDown()` from the number-key shortcuts.
      - New `theory.chord_names()` (plus `chord_quality()`) names each scale degree —
        `Em7`, `F#m7b5`, `Gmaj7`. It reuses the same branch *order* as
        `root_mode_to_midi_chord()` (uppercase, then `°`, then lowercase) so a displayed name
        can't describe different notes than the app sends, deliberately preserving that
        function's quirk where Byzantine's `VII°` is caught by `isupper()` first ('°' is
        uncased) and so gets a major chord. A property test walks every mode × key and
        asserts each name's implied interval shape equals the chord actually generated.
      - New `PlaybackEngine.on_chord_change` hook fires with each chord's progression index
        as it starts sounding, and `None` on stop, so the pad for the chord currently playing
        lights up. Like every engine callback it runs on the clock thread, so `app.py`
        marshals it to the GUI thread through a new `EngineSignals.chord_changed` signal
        (`-1` standing in for `None`, since `Signal(int)` can't carry it). It is called
        *after* the note-ons are already sent, so a slow or throwing callback can't affect
        playback timing — there's a test pinning that ordering.
      - Play now lights with the accent while the sequencer is running and goes dark on stop,
        so the window answers "is this playing?" from across the room. Without this the
        transport looked *less* prominent than the lit toggles once they were styled.
      - The progression slots the loop length doesn't reach are dimmed with a
        `QGraphicsOpacityEffect` (loop length 2 ⇒ slots 3 and 4 dim), since loop length
        silently slices the progression and nothing on screen said so. They stay *enabled*,
        just dim, so a progression can be set up before raising the loop length to play it.
        The effects are created once and enabled/disabled rather than attached/detached: a
        disabled effect leaves the combo rendering natively.
      - The slot dropdowns now read `i · Em7` rather than a bare numeral, so
        `_selected_progression()` switched from looking `currentText()` up in `self._numerals`
        to reading `currentIndex()` directly — more robust regardless, since the index *is*
        the scale degree.
      - Every chord pad carries a 2px border at all times, not just when playing: the border
        eats into the content rect, so going 1px → 2px only on the lit pad nudged its text
        down a pixel on every bar.
      - **Verified four ways**, not just by eye: the full suite (123 tests, up from 113, with
        10 new ones covering chord naming and the `on_chord_change` hook); a headless
        22-check script confirming the *pre-existing* behavior survived (preview note-on/
        note-off pairs per chord, loop-length slicing, slot→chord mapping, number-key
        shortcuts, key/mode rebuilds) rather than only that the new labels render; a real
        threaded-clock run at 480 BPM confirming the highlight actually walks 0→1→2→3→0 in
        step with the bars and clears on stop (the cross-thread part); and screenshots in
        dark mode, in a forced light palette, and of the MIDI Status dialog, which inherits
        the stylesheet.

- [x] **Circle-of-fifths roll, cast by I Ching coin tosses** (2026-09-20, confirmed working
      by Tyler running locally). A "Roll" button
      beside the key/scale selectors rewrites the key, the scale and the four progression
      slots as a *move away from* what is currently selected, not a fresh random draw — so
      repeated rolls walk through related keys instead of teleporting. New pure module
      `moodr/oracle.py`; no Qt, no MIDI, every function takes an optional `rng` so a roll
      replays exactly in a test.
      - The move is always a signed number of fifths, at both levels: the key moves around
        the circle of fifths (±7 semitones per step), and a progression slot moves around the
        *diatonic* circle within the scale, which is +4 of the seven degrees, not +1 (I→V).
        The eighth interval-dict entry (`2I`, the octave tonic) is excluded from that ring and
        folds onto the tonic, since counting it would make "up a fifth" land on a non-fifth.
      - Randomness is Tyler's `Code-ish/iChing` script, mechanism unchanged: three coin flips
        per line, summed, 0–3. The 1/8 · 3/8 · 3/8 · 1/8 weighting is the reason to use it
        over a flat `choice()` — a roll usually nudges one fifth and only occasionally leaps
        two. Six lines drive the six selections, bottom-first, and the two trigrams split
        the work the way the app does: the lower three are the ground (key, scale, and slot 1)
        and the upper three are the movement (slots 2–4). Scale has no circle to move around,
        so it reads the line's I Ching sense instead (static = stay or swap triad↔7th,
        changing = cross into another mode family).
      - **Slot 1 is anchored to the tonic**, asked for as "the first chord should stay on 1".
        A progression that starts somewhere other than I has no home, so the fifths moves in
        the slots after it stop reading as movement *away* from anything. Its line is still
        cast and still shown in the reading, as the anchor. The degree is what is fixed, not
        the chord — rolling the key or scale still changes which chord the tonic is.
      - The button leaves the widgets exactly as a hand-turned dropdown would, so a roll while
        playing lands at the next loop boundary rather than switching chords mid-bar. Its
        tooltip carries the hexagram and a per-line reading of what moved where.
      - **The cast is named on screen**, asked for as "can we display the trigram rolled in
        the UI with it's name from the iching?". A label beside the Roll button gives the
        hexagram (glyph, King Wen number, pinyin, meaning) and under it the two trigrams that
        compose it, spoken upper-over-lower — `☵ Kǎn (Water) over ☷ Kūn (Earth)`. The
        changing-line transformation ("this hexagram moves to …") is in the label's tooltip
        rather than the label, since it would otherwise double the row's height; it costs
        nothing to compute, being the right-hand column the original iChing script drew.
        The label is rich text for one reason: a hexagram glyph is six hairline strokes, so
        it needs ~30px to be legible while its name stays at body size.
        The King Wen 8×8 table is written out, not computed — the sequence is traditional and
        has no closed form — and is guarded by a test asserting it is a real permutation of
        1..64 plus spot checks on the pairs everyone knows. Two further tests tie the new
        tables back to the existing ones: trigram glyphs are checked against their Unicode
        codepoint formula (which encodes the figure itself, so a glyph pasted onto the wrong
        line pattern fails), and the drawn lines are checked to be the same figure the name
        was looked up from.
      - 41 new tests (178 total) plus 28- and 16-check runs against the real `MainWindow`. The check
        that mattered: setting the key repopulates the slot dropdowns and resets them to
        I…VII, so the rolled degrees have to be applied *after* that — pinned by replaying one
        seeded cast and asserting exact equality, not just "something changed".

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
