"""MIDI output plumbing: an rtmidi port wrapper plus the pure message-
generation helpers ported from archive/OLD mOODr_app.py (chord/bass note
messages, velocity randomization, BPM-to-bar-length conversion). No clock
or scheduling logic lives here -- see moodr.clock and moodr.playback.
"""

import random

import rtmidi

ALL_NOTES_OFF = 0x7B


class MidiOutput:
    """Owns a single rtmidi output port; no module-level globals."""

    def __init__(self):
        self._port = rtmidi.MidiOut()
        self._port_name: str | None = None
        self._is_virtual = False

    @staticmethod
    def list_ports() -> list[str]:
        return rtmidi.MidiOut().get_ports()

    @property
    def is_open(self) -> bool:
        # rtmidi's is_port_open() is unreliable for virtual ports (stays
        # True even after close_port(), since close_port() is a no-op for
        # them -- see close() below), so open/closed state is tracked here
        # instead of trusting it.
        return self._port_name is not None

    @property
    def port_name(self) -> str | None:
        return self._port_name

    def open(self, port_index: int | None = None, virtual_name: str = "m00Dr") -> None:
        """Opens the given real port by index, or -- by default -- creates a
        virtual port named "m00Dr" so DAWs like Ableton can select m00Dr as a
        MIDI input source, instead of silently opening whatever real port
        happens to be at index 0. Falls back to a real port if the platform
        doesn't support virtual ports (e.g. Windows, where python-rtmidi
        raises NotImplementedError)."""
        if port_index is not None:
            self._port.open_port(port_index)
            self._port_name = self._port.get_ports()[port_index]
            self._is_virtual = False
            return
        try:
            self._port.open_virtual_port(virtual_name)
            self._port_name = virtual_name
            self._is_virtual = True
        except NotImplementedError:
            ports = self._port.get_ports()
            if not ports:
                raise
            self._port.open_port(0)
            self._port_name = ports[0]
            self._is_virtual = False

    def close(self) -> None:
        if not self.is_open:
            return
        if self._is_virtual:
            # close_port() is a no-op for virtual ports -- they can only be
            # torn down by deleting the underlying port object, which then
            # can't be reused (reusing a deleted rtmidi object segfaults),
            # so a fresh one takes its place to keep this MidiOutput reusable.
            self._port.delete()
            self._port = rtmidi.MidiOut()
        else:
            self._port.close_port()
        self._port_name = None
        self._is_virtual = False

    def send(self, message: list[int]) -> None:
        self._port.send_message(message)

    def all_notes_off(self, channel: int) -> None:
        self.send([0xB0 | (channel & 0x0F), ALL_NOTES_OFF, 0])

    def __enter__(self) -> "MidiOutput":
        self.open()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


FULL_VELOCITY = 127


def random_velocity(rng: random.Random | None = None) -> int:
    """Random note velocity in the OLD app's 72-108 range."""
    source = rng if rng is not None else random
    return source.randint(72, 108)


def bpm_conversion(tempo: float) -> float:
    """Seconds for one 4-beat (whole note) bar at the given BPM."""
    return float(format((1 / (float(tempo) / 60)) * 4, '.3f'))


def midi_message_gen(state: int, midi_list: list[list[int]], position: int,
                      rng: random.Random | None = None, humanize: bool = True) -> list[list[int]]:
    """Chord note-on/off triples for one progression position, up an octave.
    humanize=True (default) randomizes each note's velocity in the OLD
    app's 72-108 range for a touch of human feel; humanize=False sends
    every note at FULL_VELOCITY instead."""
    return [[state, note + 12, random_velocity(rng) if humanize else FULL_VELOCITY]
            for note in midi_list[position]]


def bass_message_gen(state: int, midi_list: list[int], position: int) -> list[int]:
    """Bass note-on/off triple for one progression position, down an octave."""
    return [state, midi_list[position] - 12, 127]
