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

# Ticks-per-step for each supported arp rate (24 PPQN). Only "1/8" is
# exposed in the GUI today; the others exist so adding a rate selector
# later is just a new dropdown, not new engine logic.
ARP_RATE_TICKS = {
    "1/4": PPQN,
    "1/8": PPQN // 2,
    "1/16": PPQN // 4,
}

# Supported arp patterns. Only "down" is implemented/exposed today; adding
# "up"/"up_down"/"random" later means adding a branch here, nothing else.
ARP_PATTERNS = ("down",)


def _arp_sequence(chord_notes: list[int], pattern: str) -> list[int]:
    """The ordered notes for one full cycle of the given arp pattern
    through a chord's raw (pre-octave-shift) notes."""
    if pattern == "down":
        return sorted(chord_notes, reverse=True)
    raise ValueError(f"unknown arp pattern: {pattern!r}")


class PlaybackEngine:
    def __init__(self, midi_output, clock, chord_channel: int = CHORD_CHANNEL,
                 bass_channel: int = BASS_CHANNEL, arp_channel: int = ARP_CHANNEL,
                 rng: random.Random | None = None,
                 on_loop_complete: Callable[[], None] | None = None):
        self._midi_output = midi_output
        self._clock = clock
        self._chord_channel = chord_channel
        self._bass_channel = bass_channel
        self._arp_channel = arp_channel
        self._rng = rng
        self._chords: list[list[int]] = []
        self._roots: list[int] = []
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
    def position(self) -> int:
        """Index of the chord that will play at the next bar boundary."""
        return self._position

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
        self._sounding_position = None
        self._sounding_bass_enabled = False
        self._sounding_arp_note = None

    def _on_tick(self, tick: int) -> None:
        self._ticks_since_advance += 1
        if self._ticks_since_advance >= TICKS_PER_BAR:
            self._ticks_since_advance = 0
            self._advance()

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

        sequence = _arp_sequence(chord_notes, self.arp_pattern)
        note = sequence[self._arp_step_index % len(sequence)]
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
        self._sounding_bass_enabled = False
