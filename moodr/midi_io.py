"""MIDI I/O plumbing: rtmidi port wrappers plus the pure message-
generation helpers ported from archive/OLD mOODr_app.py (chord/bass note
messages, velocity randomization, BPM-to-bar-length conversion). No clock
or scheduling logic lives here -- see moodr.clock and moodr.playback.
"""

import random
from typing import Callable

import rtmidi

ALL_NOTES_OFF = 0x7B


class _MidiPort:
    """Shared open/close/list-ports plumbing for a single rtmidi input or
    output port; no module-level globals. Handles python-rtmidi's virtual-
    port quirks once for both MidiInput and MidiOutput: close_port() is a
    no-op for virtual ports (is_port_open() stays True forever after
    "closing" one), and a delete()-d port object segfaults the process if
    reused, so open/closed state is tracked here instead of trusted from
    rtmidi, and close() replaces the port object after deleting a virtual
    one to stay safely reusable.
    """

    _rtmidi_cls: type
    _default_virtual_name: str

    def __init__(self):
        self._port = self._rtmidi_cls()
        self._port_name: str | None = None
        self._is_virtual = False

    @classmethod
    def list_ports(cls) -> list[str]:
        return cls._rtmidi_cls().get_ports()

    @property
    def is_open(self) -> bool:
        return self._port_name is not None

    @property
    def port_name(self) -> str | None:
        return self._port_name

    def open(self, port_index: int | None = None, virtual_name: str | None = None) -> None:
        """Opens the given real port by index, or -- by default -- creates a
        named virtual port so DAWs like Ableton can select m00Dr directly,
        instead of silently opening whatever real port happens to be at
        index 0. Falls back to a real port if the platform doesn't support
        virtual ports (e.g. Windows, where python-rtmidi raises
        NotImplementedError)."""
        if port_index is not None:
            self._port.open_port(port_index)
            self._port_name = self._port.get_ports()[port_index]
            self._is_virtual = False
            return
        name = virtual_name if virtual_name is not None else self._default_virtual_name
        try:
            self._port.open_virtual_port(name)
            self._port_name = name
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
            self._port.delete()
            self._port = self._rtmidi_cls()
        else:
            self._port.close_port()
        self._port_name = None
        self._is_virtual = False

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


class MidiOutput(_MidiPort):
    """Owns a single rtmidi output port."""

    _rtmidi_cls = rtmidi.MidiOut
    _default_virtual_name = "m00Dr"

    def send(self, message: list[int]) -> None:
        self._port.send_message(message)

    def all_notes_off(self, channel: int) -> None:
        self.send([0xB0 | (channel & 0x0F), ALL_NOTES_OFF, 0])


class MidiInput(_MidiPort):
    """Owns a single rtmidi input port. Used for MIDI clock slave mode
    (see moodr.clock.MidiClockSlave) -- following an external clock (e.g.
    Ableton acting as clock master) instead of generating one."""

    _rtmidi_cls = rtmidi.MidiIn
    _default_virtual_name = "m00Dr In"

    def __init__(self):
        super().__init__()
        self._apply_ignore_types()

    def open(self, port_index: int | None = None, virtual_name: str | None = None) -> None:
        super().open(port_index, virtual_name)
        self._apply_ignore_types()

    def close(self) -> None:
        if self.is_open:
            self._port.cancel_callback()
        super().close()

    def set_callback(self, callback: Callable[[list[int], float], None]) -> None:
        """callback(message, delta_time) is invoked on rtmidi's own
        background thread -- not the caller's -- whenever a MIDI message
        arrives, so callers touching GUI state must marshal back to their
        own thread themselves (e.g. via a Qt signal)."""
        self._port.set_callback(lambda event, _data: callback(event[0], event[1]))

    def cancel_callback(self) -> None:
        self._port.cancel_callback()

    def _apply_ignore_types(self) -> None:
        # timing=False so 0xF8 Timing Clock pulses are delivered -- rtmidi
        # ignores them by default. Start/Stop/Continue (0xFA/0xFC/0xFB) are
        # System Realtime messages, not "timing", and pass through either way.
        self._port.ignore_types(sysex=True, timing=False, active_sense=True)


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
