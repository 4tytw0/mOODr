"""Shared, GUI-toolkit-agnostic setup for the Phase 3 GUI prototypes.

Both the CustomTkinter and PySide6 prototypes build their widgets around
this so the comparison is about the toolkits, not duplicated logic. Not
part of the installed moodr package -- these prototypes are throwaway
evaluation artifacts per ROADMAP.md Phase 3.
"""

from moodr import theory
from moodr.clock import MidiClock
from moodr.playback import PlaybackEngine


class NullMidiOutput:
    """Records messages instead of touching real hardware -- these
    prototypes are for evaluating threading/responsiveness, not sound."""

    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(list(message))

    def all_notes_off(self, channel):
        self.sent.append(["all_off", channel])


def build_progression(key: str, mode: str):
    """The 8-chord scale for the given key/mode from the Phase 1 theory
    module, plus the roman-numeral labels for the chord-preview buttons."""
    mode_intervals = theory.determine_mode(mode)
    root = theory.note_to_midi_int(key) + 48
    midi_roots = theory.to_midi_conversion(root, mode_intervals)
    backend_notenumeral = theory.from_midi_conversion(midi_roots, mode_intervals)
    chords = theory.root_mode_to_midi_chord(midi_roots, backend_notenumeral, mode)
    numerals = list(mode_intervals.keys())
    return chords, midi_roots, numerals


def build_engine(bpm: float = 80.0):
    """A PlaybackEngine wired to a silent NullMidiOutput, for exercising
    the real MidiClock/PlaybackEngine threading against each GUI toolkit."""
    output = NullMidiOutput()
    clock = MidiClock(output, bpm=bpm)
    engine = PlaybackEngine(output, clock)
    return output, clock, engine
