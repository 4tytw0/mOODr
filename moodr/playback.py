"""Playback engine: drives chord/bass note-on/off from MidiClock ticks
instead of time.sleep(), replacing the OLD app's play_loop/stop_loop and
their unsynchronized module globals (loop, bar, midi_progression, gui)
with one object that owns its own state and has explicit start/stop/reset.
"""

import random
from typing import Callable, NamedTuple

from . import midi_io, oracle
from .clock import PPQN

BEATS_PER_BAR = 4  # the OLD app treats one "bar" as a whole note (4 beats)
TICKS_PER_BAR = PPQN * BEATS_PER_BAR

CHORD_CHANNEL = 0  # MIDI channel 1
BASS_CHANNEL = 1  # MIDI channel 2
ARP_CHANNEL = 2  # MIDI channel 3
ACID_CHANNEL = 4  # MIDI channel 5

# Ticks-per-step for each supported arp rate (24 PPQN).
ARP_RATE_TICKS = {
    "1/4": PPQN,
    "1/8": PPQN // 2,
    "1/16": PPQN // 4,
}

# Supported arp patterns/directions.
ARP_PATTERNS = ("up", "down", "up_down", "random")

# House-style off-beat chord stabs: the full chord retriggers as a short
# punchy hit on the "and" of each beat (4 stabs per bar) instead of
# sustaining continuously -- e.g. Robin S "Show Me Love"-style piano.
# Gate length is a 16th note: short enough to breathe before the next
# stab even at the fastest 1/16 arp rate shares ticks with.
STAB_GATE_TICKS = PPQN // 4

# Acid sequencer: a locked-in 16-step pattern, one step per 16th note, so
# a full pattern is exactly one bar long (16 * 6 ticks = 96 = TICKS_PER_BAR).
ACID_STEPS = 16
ACID_STEP_TICKS = PPQN // 4

# What a step is dealt, cast one line at a time (see _generate_acid_pattern).
# The 303 only reached one octave above its base note, so `octave` is 0 or 1.


class AcidStep(NamedTuple):
    """One 16th note of the acid line."""
    degree: int | None = 0   # scale degrees from the bar's root; None = rest
    octave: int = 0          # octaves above the base note
    accent: bool = False     # louder, and on a real 303 a wider filter sweep
    slide: bool = False      # ties into the next step instead of retriggering


ACID_REST = AcidStep(degree=None)
ACID_ROOT = AcidStep()

# Accented steps are what gives an acid line its pump. A plain step sits
# below the humanized 72-108 range the chords use so the accents have room
# to land above it.
ACID_ACCENT_VELOCITY = 127
ACID_PLAIN_VELOCITY = 80

# How many draws go into a pattern's pitch palette. Duplicates collapse, so
# a pattern ends up with one to three recurring non-root notes -- which is
# what transcriptions of the famous 303 lines actually show. "Access" is one
# pitch across all 16 steps; "Higher State of Consciousness" is two. Acid
# lines are not built from pitch variety, they are built from octave jumps,
# accents and slides over a very small set of notes.
ACID_PALETTE_DRAWS = {0: 3, 1: 2, 2: 2, 3: 3}

# Which scale degrees the palette can hold. Deliberately the oracle's own
# circle-of-fifths move from the root rather than a new table, because it
# already lands exactly on the acid vocabulary: one fifth either way is the
# fourth and the fifth (the common 3/8 draws), two fifths either way is the
# flat seventh and the second (the rare 1/8 ones).
ACID_NARROW_FIFTHS = (-1, 1)


def _cast_acid_palette(rng: random.Random | None,
                       wide_deviation: bool) -> list[int]:
    """The handful of non-root scale degrees one pattern draws on."""
    palette: list[int] = []
    for _ in range(ACID_PALETTE_DRAWS[oracle.toss(rng)]):
        line = oracle.toss(rng)
        if wide_deviation:
            steps = oracle.LINE_TO_FIFTHS[line]
        else:
            # Narrow keeps to a single fifth either way -- the two degrees
            # closest to the root around the circle.
            steps = ACID_NARROW_FIFTHS[line >= 2]
        degree = oracle.shift_degree(0, steps)
        if degree not in palette:
            palette.append(degree)
    return palette or [oracle.shift_degree(0, 1)]


def _generate_acid_pattern(noise: float, wide_deviation: bool,
                            rng: random.Random | None = None) -> list[AcidStep]:
    """A fresh locked-in ACID_STEPS-length pattern, cast from the I Ching.

    Each step is dealt four lines -- what it plays, which octave, whether it
    is accented, whether it slides -- and a line's own sense decides each
    one. For pitch, the rare changing yin rests and the two static draws
    split between the bar's root and the pattern's palette; for the other
    three, a *changing* line (1/8 either way, so a quarter of steps) is the
    one that departs. That lands close to what transcribed 303 lines do:
    around an eighth of steps resting, a little over 40% of the sounding
    notes on the root, and roughly a quarter each octave-jumped, accented
    and slid.

    `noise` (0.0-1.0) is how much of that cast is let through: the
    probability each departure is honoured rather than falling back to a
    plain unaccented root at the base octave. 0.0 is a straight 16th-note
    root pulse with the reading ignored entirely; 1.0 is the cast as drawn.
    """
    source = rng if rng is not None else random
    palette = _cast_acid_palette(rng, wide_deviation)

    def lets_through() -> bool:
        return source.random() < noise

    pattern: list[AcidStep] = []
    for _ in range(ACID_STEPS):
        pitch, octave, accent, slide = (oracle.toss(rng) for _ in range(4))

        if pitch == 0 and lets_through():          # changing yin: a rest
            pattern.append(ACID_REST)
            continue
        if pitch >= 2 and lets_through():          # static yang: the palette
            degree = palette[source.randrange(len(palette))]
        else:                                      # static yin: the root
            degree = 0

        pattern.append(AcidStep(
            degree=degree,
            octave=1 if oracle.is_changing(octave) and lets_through() else 0,
            accent=oracle.is_changing(accent) and lets_through(),
            slide=oracle.is_changing(slide) and lets_through(),
        ))
    return pattern


def acid_note_for_step(step_value: int | None, home_note: int,
                       scale_roots: list[int]) -> int | None:
    """The MIDI note one acid step sounds, or None for a rest. `step_value`
    is a _generate_acid_pattern() entry: None, 0 (the home note), or a
    scale-degree offset from it.

    Public and pure because the UI's acid lane has to resolve exactly the
    same way the sequencer does -- a viewer that disagreed with what is
    being played would be worse than no viewer. A home note whose degree
    isn't in `scale_roots` (or an empty scale) falls back to the home note
    itself rather than guessing."""
    if step_value is None:
        return None
    if step_value == 0 or not scale_roots:
        return home_note
    try:
        home_index = scale_roots.index(home_note)
    except ValueError:
        home_index = 0
    return scale_roots[(home_index + step_value) % len(scale_roots)]


def _arp_note_for_step(chord_notes: list[int], pattern: str, step_index: int,
                        rng: random.Random | None = None) -> int:
    """The note to play for one arp step, given the pattern and how many
    steps have played so far through the current chord. "random" picks
    independently each call (step_index is unused for it); the other
    patterns cycle through a fixed per-chord sequence built from the
    chord's distinct raw (pre-octave-shift) notes, low to high."""
    if pattern == "random":
        source = rng if rng is not None else random
        return source.choice(chord_notes)

    notes = sorted(set(chord_notes))
    if pattern == "up":
        sequence = notes
    elif pattern == "down":
        sequence = list(reversed(notes))
    elif pattern == "up_down":
        # Up then down without repeating the top/bottom note at the turn,
        # e.g. [60, 64, 67, 71] -> 60, 64, 67, 71, 67, 64, (loops to 60).
        sequence = notes if len(notes) <= 2 else notes + list(reversed(notes[1:-1]))
    else:
        raise ValueError(f"unknown arp pattern: {pattern!r}")
    return sequence[step_index % len(sequence)]


class PlaybackEngine:
    def __init__(self, midi_output, clock, chord_channel: int = CHORD_CHANNEL,
                 bass_channel: int = BASS_CHANNEL, arp_channel: int = ARP_CHANNEL,
                 acid_channel: int = ACID_CHANNEL, rng: random.Random | None = None,
                 on_loop_complete: Callable[[], None] | None = None,
                 on_chord_change: Callable[[int | None], None] | None = None,
                 on_acid_step: Callable[[int | None], None] | None = None):
        self._midi_output = midi_output
        self._clock = clock
        self._chord_channel = chord_channel
        self._bass_channel = bass_channel
        self._arp_channel = arp_channel
        self._acid_channel = acid_channel
        self._rng = rng
        self._chords: list[list[int]] = []
        self._roots: list[int] = []
        # The full key/mode scale's roots (all 8 scale-degree pitches),
        # independent of the loaded/sliced 4-chord progression above --
        # used only so the acid sequencer's pitch deviations have a scale
        # to draw from. Set via set_scale(), not load_progression().
        self._scale_roots: list[int] = []
        self._position = 0
        self._sounding_position: int | None = None
        # The octave_shift in effect when the currently-sounding chord was
        # turned on -- captured so its note-off always targets the exact
        # notes that were turned on, even if octave_shift changes (a live
        # performance knob) while that chord is still sustaining. Using the
        # then-current octave_shift instead would send a note-off to notes
        # that were never turned on, leaving the real ones stuck.
        self._sounding_octave_shift = 0
        # Whether the currently-sounding bar's chord/bass notes are
        # actually sounding -- i.e. what chords_enabled/bass_enabled were
        # when they were turned on. Same reasoning as _sounding_octave_shift:
        # note-off must match what note-on actually did, not whatever the
        # live setting is now.
        self._sounding_chords_enabled = False
        self._chords_enabled = True
        self._sounding_bass_enabled = False
        self._bass_enabled = True
        # Off-beat stab state: same "always turn off what was last played
        # before playing the next one, or immediately on mute" shape as the
        # arp/acid state below, but retriggering the whole sounding chord
        # (all its notes) rather than one note at a time, only on the
        # offbeat ticks -- see _advance_stab(). Takes effect at the next
        # bar boundary when toggled, same as arp_pattern/arp_rate changes.
        self._stab_enabled = False
        self._sounding_stab_notes: list[int] | None = None
        self._sounding_stab_octave_shift = 0
        self._stab_ticks_since_on = 0
        # Arp state: steps through the currently-sounding chord's notes on
        # its own tick subdivision (independent of the once-per-bar chord
        # advance), always turning off whatever note it last played before
        # playing the next one, or immediately if arp_enabled is turned off
        # mid-note. _sounding_arp_note being the sounding-state source of
        # truth (rather than a separate bool like _sounding_bass_enabled)
        # is enough here since, unlike bass, nothing else shares its
        # tracking.
        self._arp_ticks_since_step = 0
        self._arp_step_index = 0
        self._sounding_arp_note: int | None = None
        self._sounding_arp_octave_shift = 0
        self._arp_enabled = True
        self.arp_pattern = "down"
        self.arp_rate = "1/8"
        # Acid sequencer state: same tick-subdivision/note-off-before-
        # note-on/immediate-mute shape as the arp above, but stepping
        # through a locked-in ACID_STEPS-length pattern (see
        # randomize_acid_pattern()) instead of the chord's own notes.
        self._acid_ticks_since_step = 0
        self._acid_step_index = 0
        self._sounding_acid_note: int | None = None
        self._sounding_acid_octave_shift = 0
        # Set when a step asks to slide into the next one.
        self._acid_slide_pending = False
        self._acid_enabled = True
        self.acid_noise = 1.0
        self.acid_wide_deviation = True
        self._acid_pattern: list[int | None] = _generate_acid_pattern(
            self.acid_noise, self.acid_wide_deviation, self._rng)
        self._ticks_since_advance = 0
        self._playing = False
        # Fires once the loaded progression has played all the way through
        # and is about to wrap back to its first chord -- lets a caller
        # (e.g. the GUI) reload a freshly-read progression right at that
        # boundary, matching the OLD app's live-GUI-reread-at-loop-boundary
        # behavior without this engine needing to know about GUI state.
        self.on_loop_complete = on_loop_complete
        # Fires with the progression index of each chord as it starts
        # sounding, and with None once nothing is sounding any more
        # (stop()) -- lets a caller show which chord is currently playing
        # without polling. Purely informational: it is called after the
        # chord's note-ons have already been sent, so a slow or throwing
        # callback can't delay or break playback timing. Like every other
        # engine callback it runs on the clock's thread, so a GUI caller
        # must marshal it onto the GUI thread (see app.py EngineSignals).
        self.on_chord_change = on_chord_change
        # Called with the index of the acid step just entered, or None when
        # nothing is playing. Fires at a 16th note, so a GUI consumer must
        # keep its handler cheap.
        self.on_acid_step = on_acid_step
        # True (default): randomize each chord note's velocity (72-108) for
        # a touch of human feel, as the OLD app always did. False: every
        # chord note at full velocity. Freely toggleable during playback.
        self.humanize_velocity = True
        # Shifts the whole progression (chords and bass) by this many
        # additional octaves, +/-, on top of the OLD app's fixed +1/-1
        # octave chord/bass split. Freely adjustable during playback --
        # see _sounding_octave_shift above for why note-off doesn't just
        # read this directly.
        self.octave_shift = 0

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def chords_enabled(self) -> bool:
        return self._chords_enabled

    @chords_enabled.setter
    def chords_enabled(self, value: bool) -> None:
        if value == self._chords_enabled:
            return
        self._chords_enabled = value
        if not value and self._sounding_chords_enabled and self._sounding_position is not None:
            # Silence the currently-sounding chord immediately rather than
            # leaving it hanging until the next bar boundary -- a mute
            # toggle should mute right away, not on a delay.
            for message in midi_io.midi_message_gen(
                    0x80 | self._chord_channel, self._chords, self._sounding_position,
                    self._rng, self.humanize_velocity, self._sounding_octave_shift):
                self._midi_output.send(message)
            self._sounding_chords_enabled = False
        if not value:
            self._turn_off_stab()

    @property
    def stab_enabled(self) -> bool:
        return self._stab_enabled

    @stab_enabled.setter
    def stab_enabled(self, value: bool) -> None:
        if value == self._stab_enabled:
            return
        self._stab_enabled = value
        if not value:
            # A mute toggle should mute right away, not wait for the next
            # offbeat -- same reasoning as arp_enabled/acid_enabled above.
            self._turn_off_stab()

    @property
    def bass_enabled(self) -> bool:
        return self._bass_enabled

    @bass_enabled.setter
    def bass_enabled(self, value: bool) -> None:
        if value == self._bass_enabled:
            return
        self._bass_enabled = value
        if not value and self._sounding_bass_enabled and self._sounding_position is not None:
            # Silence the currently-sounding bass note immediately rather
            # than leaving it hanging until the next bar boundary -- a mute
            # toggle should mute right away, not on a delay.
            self._midi_output.send(midi_io.bass_message_gen(
                0x80 | self._bass_channel, self._roots, self._sounding_position,
                self._sounding_octave_shift))
            self._sounding_bass_enabled = False

    @property
    def chord_channel(self) -> int:
        return self._chord_channel

    @chord_channel.setter
    def chord_channel(self, value: int) -> None:
        if value == self._chord_channel:
            return
        # Silence whatever's currently sounding on the old channel (regular
        # sustain or stab hits) before switching -- otherwise a note turned
        # on pre-switch would get its note-off sent on the *new* channel
        # instead, leaving the real one stuck sounding. Same reasoning as
        # chords_enabled's setter above.
        if self._sounding_chords_enabled and self._sounding_position is not None:
            for message in midi_io.midi_message_gen(
                    0x80 | self._chord_channel, self._chords, self._sounding_position,
                    self._rng, self.humanize_velocity, self._sounding_octave_shift):
                self._midi_output.send(message)
            self._sounding_chords_enabled = False
        self._turn_off_stab()
        self._chord_channel = value

    @property
    def bass_channel(self) -> int:
        return self._bass_channel

    @bass_channel.setter
    def bass_channel(self, value: int) -> None:
        if value == self._bass_channel:
            return
        if self._sounding_bass_enabled and self._sounding_position is not None:
            self._midi_output.send(midi_io.bass_message_gen(
                    0x80 | self._bass_channel, self._roots, self._sounding_position,
                    self._sounding_octave_shift))
            self._sounding_bass_enabled = False
        self._bass_channel = value

    @property
    def arp_enabled(self) -> bool:
        return self._arp_enabled

    @arp_enabled.setter
    def arp_enabled(self, value: bool) -> None:
        if value == self._arp_enabled:
            return
        self._arp_enabled = value
        if not value:
            # A mute toggle should mute right away, not wait for the next
            # arp step -- same reasoning as bass_enabled above.
            self._turn_off_arp_note()

    @property
    def acid_enabled(self) -> bool:
        return self._acid_enabled

    @acid_enabled.setter
    def acid_enabled(self, value: bool) -> None:
        if value == self._acid_enabled:
            return
        self._acid_enabled = value
        if not value:
            # A mute toggle should mute right away, not wait for the next
            # acid step -- same reasoning as bass_enabled/arp_enabled above.
            self._turn_off_acid_note()

    @property
    def position(self) -> int:
        """Index of the chord that will play at the next bar boundary."""
        return self._position

    def set_scale(self, scale_roots: list[int]) -> None:
        """The full key/mode scale (all 8 scale-degree roots) the acid
        sequencer's pitch deviations draw from -- distinct from
        load_progression()'s 4-chord loaded/sliced progression."""
        self._scale_roots = scale_roots

    @property
    def acid_pattern(self) -> list[int | None]:
        """The locked-in pattern as raw step values -- a copy, so a caller
        holding it can't mutate what the sequencer is reading."""
        return list(self._acid_pattern)

    def randomize_acid_pattern(self) -> None:
        """Casts a fresh locked-in acid pattern from the current
        acid_noise/acid_wide_deviation settings. Takes effect starting
        from whichever step the sequencer is currently on -- it doesn't
        wait for the next bar or restart the pattern from step 0, so the
        app calls it at a loop boundary when it wants the two aligned."""
        self._acid_pattern = _generate_acid_pattern(
            self.acid_noise, self.acid_wide_deviation, self._rng)

    def set_clock(self, clock) -> None:
        """Swaps the clock this engine is driven by -- e.g. switching
        between the internal master MidiClock and an external
        MidiClockSlave. Only valid while stopped, since a tick callback is
        registered on whichever clock is currently attached while playing."""
        if self._playing:
            raise RuntimeError("cannot change clocks while playing; call stop() first")
        self._clock = clock

    def load_progression(self, chords: list[list[int]], roots: list[int]) -> None:
        if len(chords) != len(roots):
            raise ValueError("chords and roots must be the same length")
        self._chords = chords
        self._roots = roots
        self.reset()

    def set_progression(self, chords: list[list[int]], roots: list[int]) -> None:
        """Swaps in new chord/root data WITHOUT resetting playback position
        or in-flight sounding-note tracking. Use this (not load_progression)
        from an on_loop_complete callback: that callback is typically a
        queued cross-thread signal (needed for thread-safe GUI access), so
        it can arrive after the clock thread has already advanced one or
        more further bars past the loop boundary that triggered it.
        load_progression()'s reset() would then snap position back to 0,
        replaying the first chord an extra time -- this doesn't."""
        if len(chords) != len(roots):
            raise ValueError("chords and roots must be the same length")
        self._chords = chords
        self._roots = roots
        if self._chords:
            self._position %= len(self._chords)

    def reset(self) -> None:
        self._position = 0
        self._sounding_position = None
        self._sounding_octave_shift = 0
        self._sounding_chords_enabled = False
        self._sounding_bass_enabled = False
        self._arp_ticks_since_step = 0
        self._arp_step_index = 0
        self._sounding_arp_note = None
        self._sounding_stab_notes = None
        self._stab_ticks_since_on = 0

    def start(self) -> None:
        if self._playing or not self._chords:
            return
        self._playing = True
        self._ticks_since_advance = 0
        self._arp_ticks_since_step = 0
        self._acid_ticks_since_step = 0
        self._acid_step_index = 0
        self._acid_slide_pending = False
        self._advance()
        self._clock.add_tick_callback(self._on_tick)
        if not self._clock.is_running:
            self._clock.start()

    def stop(self) -> None:
        if not self._playing:
            return
        self._playing = False
        self._clock.remove_tick_callback(self._on_tick)
        self._midi_output.all_notes_off(self._chord_channel)
        self._midi_output.all_notes_off(self._bass_channel)
        self._midi_output.all_notes_off(self._arp_channel)
        self._midi_output.all_notes_off(self._acid_channel)
        self._sounding_position = None
        self._sounding_chords_enabled = False
        self._sounding_bass_enabled = False
        self._sounding_arp_note = None
        self._sounding_acid_note = None
        self._acid_slide_pending = False
        self._sounding_stab_notes = None
        if self.on_chord_change is not None:
            self.on_chord_change(None)
        if self.on_acid_step is not None:
            self.on_acid_step(None)

    def _on_tick(self, tick: int) -> None:
        self._ticks_since_advance += 1
        if self._ticks_since_advance >= TICKS_PER_BAR:
            self._ticks_since_advance = 0
            self._advance()

        self._acid_ticks_since_step += 1
        if self._acid_ticks_since_step >= ACID_STEP_TICKS:
            self._acid_ticks_since_step = 0
            self._advance_acid_step()

        self._arp_ticks_since_step += 1
        if self._arp_ticks_since_step >= ARP_RATE_TICKS[self.arp_rate]:
            self._arp_ticks_since_step = 0
            self._advance_arp_step()

        if self._chords_enabled and self._stab_enabled:
            self._advance_stab()
        elif self._sounding_stab_notes is not None:
            self._turn_off_stab()

    def _advance(self) -> None:
        looped_back = self._position == 0 and self._sounding_position is not None
        self._turn_off_sounding()
        if looped_back and self.on_loop_complete is not None:
            self.on_loop_complete()

        octave_shift = self.octave_shift
        # While stab mode is on, the chord voice is driven entirely by
        # _advance_stab()'s offbeat retriggers instead of one continuous
        # sustained note-on -- sending both would double-trigger the same
        # notes on the same channel.
        if self._chords_enabled and not self._stab_enabled:
            for message in midi_io.midi_message_gen(
                    0x90 | self._chord_channel, self._chords, self._position,
                    self._rng, self.humanize_velocity, octave_shift):
                self._midi_output.send(message)
        if self._bass_enabled:
            self._midi_output.send(midi_io.bass_message_gen(
                0x90 | self._bass_channel, self._roots, self._position, octave_shift))

        self._sounding_position = self._position
        self._sounding_octave_shift = octave_shift
        self._sounding_chords_enabled = self._chords_enabled and not self._stab_enabled
        self._sounding_bass_enabled = self._bass_enabled
        # Each new chord's arp restarts its pattern from the top, rather
        # than continuing mid-sequence from the previous chord.
        self._arp_step_index = 0
        self._position = (self._position + 1) % len(self._chords)
        if self.on_chord_change is not None:
            self.on_chord_change(self._sounding_position)

    def _turn_off_sounding(self) -> None:
        if self._sounding_position is None:
            return
        if self._sounding_chords_enabled:
            for message in midi_io.midi_message_gen(
                    0x80 | self._chord_channel, self._chords, self._sounding_position,
                    self._rng, self.humanize_velocity, self._sounding_octave_shift):
                self._midi_output.send(message)
        if self._sounding_bass_enabled:
            self._midi_output.send(midi_io.bass_message_gen(
                0x80 | self._bass_channel, self._roots, self._sounding_position,
                self._sounding_octave_shift))

    def _advance_arp_step(self) -> None:
        self._turn_off_arp_note()

        if not self._arp_enabled or self._sounding_position is None:
            return
        chord_notes = self._chords[self._sounding_position]
        if not chord_notes:
            return

        note = _arp_note_for_step(chord_notes, self.arp_pattern, self._arp_step_index, self._rng)
        self._arp_step_index += 1

        octave_shift = self.octave_shift
        message = midi_io.midi_message_gen(
            0x90 | self._arp_channel, [[note]], 0, self._rng, self.humanize_velocity,
            octave_shift)[0]
        self._midi_output.send(message)
        self._sounding_arp_note = note
        self._sounding_arp_octave_shift = octave_shift

    def _turn_off_arp_note(self) -> None:
        if self._sounding_arp_note is None:
            return
        message = midi_io.midi_message_gen(
            0x80 | self._arp_channel, [[self._sounding_arp_note]], 0, self._rng,
            self.humanize_velocity, self._sounding_arp_octave_shift)[0]
        self._midi_output.send(message)
        self._sounding_arp_note = None

    def _advance_stab(self) -> None:
        """Offbeat chord-stab step, called every tick while stab mode is on
        (see _on_tick). Retriggers the whole sounding chord for a short,
        punchy STAB_GATE_TICKS-long gate on each beat's offbeat (halfway
        through every PPQN-tick beat -- 4 stabs per TICKS_PER_BAR-tick bar),
        and turns it back off once that gate elapses."""
        tick_in_beat = self._ticks_since_advance % PPQN
        if tick_in_beat == PPQN // 2:
            self._turn_off_stab()
            if self._sounding_position is None:
                return
            chord_notes = self._chords[self._sounding_position]
            if not chord_notes:
                return
            octave_shift = self.octave_shift
            for message in midi_io.midi_message_gen(
                    0x90 | self._chord_channel, self._chords, self._sounding_position,
                    self._rng, self.humanize_velocity, octave_shift):
                self._midi_output.send(message)
            self._sounding_stab_notes = chord_notes
            self._sounding_stab_octave_shift = octave_shift
            self._stab_ticks_since_on = 0
            return

        if self._sounding_stab_notes is not None:
            self._stab_ticks_since_on += 1
            if self._stab_ticks_since_on >= STAB_GATE_TICKS:
                self._turn_off_stab()

    def _turn_off_stab(self) -> None:
        if self._sounding_stab_notes is None:
            return
        for message in midi_io.midi_message_gen(
                0x80 | self._chord_channel, [self._sounding_stab_notes], 0,
                self._rng, self.humanize_velocity, self._sounding_stab_octave_shift):
            self._midi_output.send(message)
        self._sounding_stab_notes = None

    def _advance_acid_step(self) -> None:
        index = self._acid_step_index
        step = self._acid_pattern[index % len(self._acid_pattern)]
        self._acid_step_index = (index + 1) % ACID_STEPS
        # Reported before the early returns below, so a muted or resting
        # step still moves the playhead -- the lane shows where the
        # sequencer is, not only where it last made a sound.
        if self.on_acid_step is not None:
            self.on_acid_step(index)

        # Whether the *previous* step asked to slide into this one. A 303
        # slide ties the two notes together rather than retriggering, so
        # the outgoing note-off has to land after the incoming note-on --
        # the overlap is what makes a synth's portamento take over.
        sliding_in = self._acid_slide_pending
        self._acid_slide_pending = False

        if not self._acid_enabled or self._sounding_position is None \
                or step.degree is None:
            self._turn_off_acid_note()
            return

        home_note = self._roots[self._sounding_position]
        note = acid_note_for_step(step.degree, home_note, self._scale_roots)
        octave_shift = self.octave_shift + step.octave

        held_note, held_shift = self._sounding_acid_note, self._sounding_acid_octave_shift
        if not sliding_in:
            self._turn_off_acid_note()
            held_note = None

        message = midi_io.midi_message_gen(
            0x90 | self._acid_channel, [[note]], 0, self._rng, self.humanize_velocity,
            octave_shift,
            velocity=ACID_ACCENT_VELOCITY if step.accent else ACID_PLAIN_VELOCITY)[0]
        self._midi_output.send(message)
        self._sounding_acid_note = note
        self._sounding_acid_octave_shift = octave_shift
        self._acid_slide_pending = step.slide

        if held_note is not None:
            self._send_acid_note_off(held_note, held_shift)

    def _send_acid_note_off(self, note: int, octave_shift: int) -> None:
        message = midi_io.midi_message_gen(
            0x80 | self._acid_channel, [[note]], 0, self._rng,
            self.humanize_velocity, octave_shift)[0]
        self._midi_output.send(message)

    def _turn_off_acid_note(self) -> None:
        if self._sounding_acid_note is None:
            return
        self._send_acid_note_off(self._sounding_acid_note,
                                 self._sounding_acid_octave_shift)
        self._sounding_acid_note = None
        self._acid_slide_pending = False
