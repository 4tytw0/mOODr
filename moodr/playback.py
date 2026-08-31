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
BASS_CHANNEL = 1  # MIDI channel 2


class PlaybackEngine:
    def __init__(self, midi_output, clock, chord_channel: int = CHORD_CHANNEL,
                 bass_channel: int = BASS_CHANNEL, rng: random.Random | None = None,
                 on_loop_complete: Callable[[], None] | None = None):
        self._midi_output = midi_output
        self._clock = clock
        self._chord_channel = chord_channel
        self._bass_channel = bass_channel
        self._rng = rng
        self._chords: list[list[int]] = []
        self._roots: list[int] = []
        self._position = 0
        self._sounding_position: int | None = None
        self._ticks_since_advance = 0
        self._playing = False
        # Fires once the loaded progression has played all the way through
        # and is about to wrap back to its first chord -- lets a caller
        # (e.g. the GUI) reload a freshly-read progression right at that
        # boundary, matching the OLD app's live-GUI-reread-at-loop-boundary
        # behavior without this engine needing to know about GUI state.
        self.on_loop_complete = on_loop_complete

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def position(self) -> int:
        """Index of the chord that will play at the next bar boundary."""
        return self._position

    def load_progression(self, chords: list[list[int]], roots: list[int]) -> None:
        if len(chords) != len(roots):
            raise ValueError("chords and roots must be the same length")
        self._chords = chords
        self._roots = roots
        self.reset()

    def reset(self) -> None:
        self._position = 0
        self._sounding_position = None

    def start(self) -> None:
        if self._playing or not self._chords:
            return
        self._playing = True
        self._ticks_since_advance = 0
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
        self._sounding_position = None

    def _on_tick(self, tick: int) -> None:
        self._ticks_since_advance += 1
        if self._ticks_since_advance >= TICKS_PER_BAR:
            self._ticks_since_advance = 0
            self._advance()

    def _advance(self) -> None:
        looped_back = self._position == 0 and self._sounding_position is not None
        self._turn_off_sounding()
        if looped_back and self.on_loop_complete is not None:
            self.on_loop_complete()

        for message in midi_io.midi_message_gen(
                0x90 | self._chord_channel, self._chords, self._position, self._rng):
            self._midi_output.send(message)
        self._midi_output.send(midi_io.bass_message_gen(
            0x90 | self._bass_channel, self._roots, self._position))

        self._sounding_position = self._position
        self._position = (self._position + 1) % len(self._chords)

    def _turn_off_sounding(self) -> None:
        if self._sounding_position is None:
            return
        for message in midi_io.midi_message_gen(
                0x80 | self._chord_channel, self._chords, self._sounding_position, self._rng):
            self._midi_output.send(message)
        self._midi_output.send(midi_io.bass_message_gen(
            0x80 | self._bass_channel, self._roots, self._sounding_position))
        self._sounding_position = None
