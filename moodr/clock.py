"""MIDI Beat Clock engines: a master clock (MidiClock) and a slave clock
that follows an external one (MidiClockSlave, e.g. Ableton acting as
clock master). Both expose the same add_tick_callback()/is_running/
start()/stop() interface, so moodr.playback.PlaybackEngine can be driven
by either interchangeably -- note scheduling is driven by ticks, not by
blocking on time.sleep() like the OLD app's bar loop.

MidiClock sends real 0xF8 timing-clock pulses at 24 PPQN derived from
BPM, plus Start/Stop/Continue, on a background thread with drift-
corrected scheduling (unlike the OLD app's time.sleep() bar loop, which
only nudged its clock by half the measured error each bar).
"""

import threading
import time
from typing import Callable

PPQN = 24  # MIDI standard pulses per quarter note

TIMING_CLOCK = 0xF8
START = 0xFA
CONTINUE = 0xFB
STOP = 0xFC

TickCallback = Callable[[int], None]


class MidiClock:
    def __init__(self, midi_output, bpm: float = 80.0):
        self._midi_output = midi_output
        self._bpm = bpm
        self._tick_count = 0
        self._lock = threading.Lock()
        self._running = threading.Event()
        self._thread: threading.Thread | None = None
        self._tick_callbacks: list[TickCallback] = []

    @property
    def bpm(self) -> float:
        return self._bpm

    @bpm.setter
    def bpm(self, value: float) -> None:
        with self._lock:
            self._bpm = value

    @property
    def tick_interval(self) -> float:
        """Seconds between clock pulses at the current BPM."""
        with self._lock:
            bpm = self._bpm
        return 60.0 / (bpm * PPQN)

    @property
    def tick_count(self) -> int:
        return self._tick_count

    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    def add_tick_callback(self, callback: TickCallback) -> None:
        self._tick_callbacks.append(callback)

    def remove_tick_callback(self, callback: TickCallback) -> None:
        self._tick_callbacks.remove(callback)

    def start(self) -> None:
        if self.is_running:
            return
        self._tick_count = 0
        self._midi_output.send([START])
        self._running.set()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self.is_running:
            return
        self._running.clear()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        self._midi_output.send([STOP])

    def continue_(self) -> None:
        if self.is_running:
            return
        self._midi_output.send([CONTINUE])
        self._running.set()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def emit_tick(self) -> int:
        """Sends one clock pulse and invokes tick callbacks; returns the
        tick number. Exposed directly so tests can drive it without real
        threading/timing."""
        self._midi_output.send([TIMING_CLOCK])
        with self._lock:
            tick = self._tick_count
            self._tick_count += 1
        for callback in list(self._tick_callbacks):
            callback(tick)
        return tick

    def _run(self) -> None:
        next_tick_at = time.perf_counter()
        while self._running.is_set():
            self.emit_tick()
            next_tick_at += self.tick_interval
            remaining = next_tick_at - time.perf_counter()
            if remaining > 0:
                time.sleep(remaining)
            else:
                next_tick_at = time.perf_counter()


class MidiClockSlave:
    """Follows an external MIDI clock arriving on a MidiInput instead of
    generating one -- e.g. Ableton set as clock master, sending its Sync
    output to the port this MidiInput is listening on. Ticks arrive
    exactly when the external clock sends them, so there is no BPM or
    tick_interval to configure here; tempo is whatever the master's is.

    Tick callbacks (and on_start/on_stop/on_continue, if given) fire on
    rtmidi's own background thread, not the caller's -- same threading
    contract as MidiClock's background thread, so callers touching GUI
    state must marshal back to their own thread themselves.
    """

    def __init__(self, midi_input, on_start: Callable[[], None] | None = None,
                 on_stop: Callable[[], None] | None = None,
                 on_continue: Callable[[], None] | None = None):
        self._midi_input = midi_input
        self._tick_callbacks: list[TickCallback] = []
        self._tick_count = 0
        self._running = False
        self.on_start = on_start
        self.on_stop = on_stop
        self.on_continue = on_continue

    @property
    def tick_count(self) -> int:
        return self._tick_count

    @property
    def is_running(self) -> bool:
        return self._running

    def add_tick_callback(self, callback: TickCallback) -> None:
        self._tick_callbacks.append(callback)

    def remove_tick_callback(self, callback: TickCallback) -> None:
        self._tick_callbacks.remove(callback)

    def start(self) -> None:
        """Starts listening for the external clock. Unlike MidiClock,
        nothing is sent -- there's no local clock to start, this just
        subscribes to messages already arriving on the MIDI input."""
        if self._running:
            return
        self._running = True
        self._tick_count = 0
        self._midi_input.set_callback(self._on_message)

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._midi_input.cancel_callback()

    def _on_message(self, message: list[int], _delta_time: float) -> None:
        status = message[0]
        if status == TIMING_CLOCK:
            tick = self._tick_count
            self._tick_count += 1
            for callback in list(self._tick_callbacks):
                callback(tick)
        elif status == START:
            self._tick_count = 0
            if self.on_start is not None:
                self.on_start()
        elif status == STOP:
            if self.on_stop is not None:
                self.on_stop()
        elif status == CONTINUE:
            if self.on_continue is not None:
                self.on_continue()
