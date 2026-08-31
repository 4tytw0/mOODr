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

    @staticmethod
    def list_ports() -> list[str]:
        return rtmidi.MidiOut().get_ports()

    @property
    def is_open(self) -> bool:
        return self._port.is_port_open()

    @property
    def port_name(self) -> str | None:
        return self._port_name

    def open(self, port_index: int = 0, virtual_name: str = "m00Dr virtual output") -> None:
        ports = self._port.get_ports()
        if ports:
            self._port.open_port(port_index)
            self._port_name = ports[port_index]
        else:
            self._port.open_virtual_port(virtual_name)
            self._port_name = virtual_name

    def close(self) -> None:
        if self.is_open:
            self._port.close_port()
        self._port_name = None

    def send(self, message: list[int]) -> None:
        self._port.send_message(message)

    def all_notes_off(self, channel: int) -> None:
        self.send([0xB0 | (channel & 0x0F), ALL_NOTES_OFF, 0])

    def __enter__(self) -> "MidiOutput":
        self.open()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def random_velocity(rng: random.Random | None = None) -> int:
    """Random note velocity in the OLD app's 72-108 range."""
    source = rng if rng is not None else random
    return source.randint(72, 108)


def bpm_conversion(tempo: float) -> float:
    """Seconds for one 4-beat (whole note) bar at the given BPM."""
    return float(format((1 / (float(tempo) / 60)) * 4, '.3f'))


def midi_message_gen(state: int, midi_list: list[list[int]], position: int,
                      rng: random.Random | None = None) -> list[list[int]]:
    """Chord note-on/off triples for one progression position, up an octave."""
    return [[state, note + 12, random_velocity(rng)] for note in midi_list[position]]


def bass_message_gen(state: int, midi_list: list[int], position: int) -> list[int]:
    """Bass note-on/off triple for one progression position, down an octave."""
    return [state, midi_list[position] - 12, 127]
