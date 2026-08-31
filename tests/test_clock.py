import time

import pytest

from moodr.clock import PPQN, START, STOP, TIMING_CLOCK, MidiClock


class RecordingOutput:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(list(message))


def test_tick_interval_derives_from_bpm_and_ppqn():
    clock = MidiClock(RecordingOutput(), bpm=120)
    assert clock.tick_interval == pytest.approx(60 / (120 * PPQN))


def test_emit_tick_sends_clock_byte_and_invokes_callbacks():
    output = RecordingOutput()
    clock = MidiClock(output, bpm=120)
    seen = []
    clock.add_tick_callback(seen.append)

    tick = clock.emit_tick()

    assert tick == 0
    assert output.sent == [[TIMING_CLOCK]]
    assert seen == [0]


def test_emit_tick_increments_tick_count():
    clock = MidiClock(RecordingOutput(), bpm=120)
    clock.emit_tick()
    clock.emit_tick()
    assert clock.tick_count == 2


def test_removed_callback_stops_receiving_ticks():
    clock = MidiClock(RecordingOutput(), bpm=120)
    seen = []
    clock.add_tick_callback(seen.append)
    clock.emit_tick()
    clock.remove_tick_callback(seen.append)
    clock.emit_tick()
    assert seen == [0]


def test_start_stop_sends_realtime_messages_and_runs_a_thread():
    output = RecordingOutput()
    clock = MidiClock(output, bpm=6000)  # fast tick_interval keeps this test quick

    clock.start()
    assert clock.is_running
    time.sleep(0.05)
    clock.stop()

    assert not clock.is_running
    assert output.sent[0] == [START]
    assert output.sent[-1] == [STOP]
    assert clock.tick_count > 0
