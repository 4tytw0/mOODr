"""Playback engine: drives chord/bass note-on/off from MidiClock ticks
instead of time.sleep(), replacing the OLD app's play_loop/stop_loop and
their unsynchronized module globals (loop, bar, midi_progression, gui)
with one object that owns its own state and has explicit start/stop/reset.
"""

import random
from typing import Callable

from . import midi_io
from .clock import PPQN

BEATS_PER_BAR = 4  # the OLD app treats one "bar" as a whole note (4 beats)
TICKS_PER_BAR = PPQN * BEATS_PER_BAR

CHORD_CHANNEL = 0  # MIDI channel 1
ARP_CHANNEL = 1  # MIDI channel 2
BASS_CHANNEL = 2  # MIDI channel 3
ACID_CHANNEL = 3  # MIDI channel 4

# Ticks-per-step for each supported arp rate (24 PPQN).
ARP_RATE_TICKS = {
    "1/4": PPQN,
    "1/8": PPQN // 2,
    "1/16": PPQN // 4,
}

# Supported arp patterns/directions.
ARP_PATTERNS = ("up", "down", "up_down", "random")

# Acid sequencer: a locked-in 16-step pattern, one step per 16th note, so
# a full pattern is exactly one bar long (16 * 6 ticks = 96 = TICKS_PER_BAR).
ACID_STEPS = 16
ACID_STEP_TICKS = PPQN // 4
# Offsets (in scale degrees from the bar's home note) narrow deviation can
# land on. Wide deviation instead draws from every other degree in a
# (theory.py's) 8-note scale -- see _generate_acid_pattern.
ACID_NARROW_OFFSETS = (-2, -1, 1, 2)


def _generate_acid_pattern(noise: float, wide_deviation: bool,
                            rng: random.Random | None = None) -> list[int | None]:
    """A fresh locked-in ACID_STEPS-length pattern. Each step is `None`
    (rest), `0` (play the bar's home note -- the sounding chord's root),
    or a nonzero scale-degree offset from that home note. noise (0.0-1.0)
    is the independent probability of a step being a rest, and -- for
    steps that aren't -- of it being deviated instead of the home note."""
    source = rng if rng is not None else random
    pattern: list[int | None] = []
    for _ in range(ACID_STEPS):
        if source.random() < noise:
            pattern.append(None)
        elif source.random() < noise:
            if wide_deviation:
                offset = source.choice([o for o in range(-7, 8) if o != 0])
            else:
                offset = source.choice(ACID_NARROW_OFFSETS)
            pattern.append(offset)
        else:
            pattern.append(0)
    return pattern


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
                 on_loop_complete: Callable[[], None] | None = None):
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
        # Whether the currently-sounding chord's bass note is actually
        # sounding -- i.e. what bass_enabled was when it was turned on.
        # Same reasoning as _sounding_octave_shift: note-off must match
        # what note-on actually did, not whatever the live setting is now.
        self._sounding_bass_enabled = False
        self._bass_enabled = True
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
        self._acid_enabled = True
        self.acid_noise = 0.25
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

    def randomize_acid_pattern(self) -> None:
        """Rolls a fresh locked-in acid pattern from the current
        acid_noise/acid_wide_deviation settings. Takes effect starting
        from whichever step the sequencer is currently on -- it doesn't
        wait for the next bar or restart the pattern from step 0."""
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
        self._sounding_bass_enabled = False
        self._arp_ticks_since_step = 0
        self._arp_step_index = 0
        self._sounding_arp_note = None

    def start(self) -> None:
        if self._playing or not self._chords:
            return
        self._playing = True
        self._ticks_since_advance = 0
        self._arp_ticks_since_step = 0
        self._acid_ticks_since_step = 0
        self._acid_step_index = 0
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
        self._sounding_bass_enabled = False
        self._sounding_arp_note = None
        self._sounding_acid_note = None

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

    def _advance(self) -> None:
        looped_back = self._position == 0 and self._sounding_position is not None
        self._turn_off_sounding()
        if looped_back and self.on_loop_complete is not None:
            self.on_loop_complete()

        octave_shift = self.octave_shift
        for message in midi_io.midi_message_gen(
                0x90 | self._chord_channel, self._chords, self._position,
                self._rng, self.humanize_velocity, octave_shift):
            self._midi_output.send(message)
        if self._bass_enabled:
            self._midi_output.send(midi_io.bass_message_gen(
                0x90 | self._bass_channel, self._roots, self._position, octave_shift))

        self._sounding_position = self._position
        self._sounding_octave_shift = octave_shift
        self._sounding_bass_enabled = self._bass_enabled
        # Each new chord's arp restarts its pattern from the top, rather
        # than continuing mid-sequence from the previous chord.
        self._arp_step_index = 0
        self._position = (self._position + 1) % len(self._chords)

    def _turn_off_sounding(self) -> None:
        if self._sounding_position is None:
            return
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

    def _advance_acid_step(self) -> None:
        self._turn_off_acid_note()

        step_value = self._acid_pattern[self._acid_step_index % len(self._acid_pattern)]
        self._acid_step_index = (self._acid_step_index + 1) % ACID_STEPS

        if not self._acid_enabled or self._sounding_position is None or step_value is None:
            return

        home_note = self._roots[self._sounding_position]
        if step_value == 0 or not self._scale_roots:
            note = home_note
        else:
            try:
                home_index = self._scale_roots.index(home_note)
            except ValueError:
                home_index = 0
            note = self._scale_roots[(home_index + step_value) % len(self._scale_roots)]

        octave_shift = self.octave_shift
        message = midi_io.midi_message_gen(
            0x90 | self._acid_channel, [[note]], 0, self._rng, self.humanize_velocity,
            octave_shift)[0]
        self._midi_output.send(message)
        self._sounding_acid_note = note
        self._sounding_acid_octave_shift = octave_shift

    def _turn_off_acid_note(self) -> None:
        if self._sounding_acid_note is None:
            return
        message = midi_io.midi_message_gen(
            0x80 | self._acid_channel, [[self._sounding_acid_note]], 0, self._rng,
            self.humanize_velocity, self._sounding_acid_octave_shift)[0]
        self._midi_output.send(message)
        self._sounding_acid_note = None
