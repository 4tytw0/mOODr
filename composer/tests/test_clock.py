import time

import pytest

from moodr.clock import CONTINUE, PPQN, START, STOP, TIMING_CLOCK, MidiClock, MidiClockSlave


class RecordingOutput:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(list(message))


class FakeMidiInput:
    """Captures the callback MidiClockSlave registers, and lets tests feed
    it synthetic MIDI messages directly -- no real MIDI hardware needed."""

    def __init__(self):
        self._callback = None
        self.cancel_calls = 0

    def set_callback(self, callback):
        self._callback = callback

    def cancel_callback(self):
        self.cancel_calls += 1
        self._callback = None

    def feed(self, message, delta_time=0.0):
        self._callback(message, delta_time)


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


def test_midi_clock_slave_not_running_until_started():
    slave = MidiClockSlave(FakeMidiInput())
    assert not slave.is_running


def test_midi_clock_slave_start_subscribes_to_the_midi_input():
    midi_input = FakeMidiInput()
    slave = MidiClockSlave(midi_input)

    slave.start()

    assert slave.is_running
    assert midi_input._callback is not None


def test_midi_clock_slave_dispatches_ticks_from_incoming_clock_bytes():
    midi_input = FakeMidiInput()
    slave = MidiClockSlave(midi_input)
    seen = []
    slave.add_tick_callback(seen.append)
    slave.start()

    midi_input.feed([TIMING_CLOCK])
    midi_input.feed([TIMING_CLOCK])

    assert seen == [0, 1]
    assert slave.tick_count == 2


def test_midi_clock_slave_ignores_non_clock_bytes_for_tick_callbacks():
    midi_input = FakeMidiInput()
    slave = MidiClockSlave(midi_input)
    seen = []
    slave.add_tick_callback(seen.append)
    slave.start()

    midi_input.feed([0x90, 60, 100])  # an ordinary note-on, not a clock byte

    assert seen == []
    assert slave.tick_count == 0


def test_midi_clock_slave_start_message_resets_tick_count_and_fires_on_start():
    midi_input = FakeMidiInput()
    starts = []
    slave = MidiClockSlave(midi_input, on_start=lambda: starts.append(None))
    slave.start()
    midi_input.feed([TIMING_CLOCK])
    midi_input.feed([TIMING_CLOCK])

    midi_input.feed([START])

    assert slave.tick_count == 0
    assert len(starts) == 1


def test_midi_clock_slave_stop_and_continue_messages_fire_callbacks():
    midi_input = FakeMidiInput()
    stops, continues = [], []
    slave = MidiClockSlave(midi_input, on_stop=lambda: stops.append(None),
                            on_continue=lambda: continues.append(None))
    slave.start()

    midi_input.feed([STOP])
    midi_input.feed([CONTINUE])

    assert len(stops) == 1
    assert len(continues) == 1


def test_midi_clock_slave_stop_cancels_the_midi_input_callback():
    midi_input = FakeMidiInput()
    slave = MidiClockSlave(midi_input)
    slave.start()

    slave.stop()

    assert not slave.is_running
    assert midi_input.cancel_calls == 1


def test_midi_clock_slave_remove_tick_callback_stops_receiving_ticks():
    midi_input = FakeMidiInput()
    slave = MidiClockSlave(midi_input)
    seen = []
    slave.add_tick_callback(seen.append)
    slave.start()
    midi_input.feed([TIMING_CLOCK])
    slave.remove_tick_callback(seen.append)

    midi_input.feed([TIMING_CLOCK])

    assert seen == [0]
