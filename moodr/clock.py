"""Master MIDI Beat Clock engine.

Sends real 0xF8 timing-clock pulses at 24 PPQN derived from BPM, plus
Start/Stop/Continue, on a background thread with drift-corrected
scheduling (unlike the OLD app's time.sleep() bar loop, which only
nudged its clock by half the measured error each bar). Master-only:
this clock does not sync to an external MIDI clock -- see ROADMAP
Phase 6 for slave mode as a stretch goal.

Playback code (moodr.playback) hooks in via add_tick_callback() instead
of blocking on time.sleep(), so note scheduling is driven by ticks.
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
