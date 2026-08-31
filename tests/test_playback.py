from moodr.midi_io import FULL_VELOCITY
from moodr.playback import BASS_CHANNEL, CHORD_CHANNEL, TICKS_PER_BAR, PlaybackEngine


class RecordingOutput:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(list(message))

    def all_notes_off(self, channel):
        self.sent.append(["all_off", channel])


class FakeClock:
    """Drives tick callbacks synchronously, on demand, for deterministic tests."""

    def __init__(self):
        self.callbacks = []
        self.is_running = False
        self.started = False

    def add_tick_callback(self, callback):
        self.callbacks.append(callback)

    def remove_tick_callback(self, callback):
        self.callbacks.remove(callback)

    def start(self):
        self.started = True
        self.is_running = True

    def tick(self, count=1):
        for _ in range(count):
            for callback in list(self.callbacks):
                callback(0)


def make_engine():
    output = RecordingOutput()
    clock = FakeClock()
    engine = PlaybackEngine(output, clock)
    return output, clock, engine


def test_start_plays_first_chord_and_bass_immediately():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67]], [48])

    engine.start()

    chord_on = [m for m in output.sent if m[0] == 0x90 | CHORD_CHANNEL]
    bass_on = [m for m in output.sent if m[0] == 0x90 | BASS_CHANNEL]
    assert [m[1] for m in chord_on] == [72, 76, 79]
    assert all(72 <= m[2] <= 108 for m in chord_on)
    assert bass_on == [[0x90 | BASS_CHANNEL, 36, 127]]
    assert clock.started
    assert engine.is_playing


def test_bar_boundary_turns_previous_chord_off_and_next_one_on():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67], [65, 69, 72]], [48, 53])
    engine.start()
    output.sent.clear()

    clock.tick(TICKS_PER_BAR)

    off_messages = [m for m in output.sent if m[0] == 0x80 | CHORD_CHANNEL]
    on_messages = [m for m in output.sent if m[0] == 0x90 | CHORD_CHANNEL]
    assert [m[1] for m in off_messages] == [72, 76, 79]
    assert [m[1] for m in on_messages] == [77, 81, 84]


def test_progression_wraps_around_to_the_first_chord():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67], [65, 69, 72]], [48, 53])
    engine.start()
    clock.tick(TICKS_PER_BAR)
    output.sent.clear()

    clock.tick(TICKS_PER_BAR)

    on_messages = [m for m in output.sent if m[0] == 0x90 | CHORD_CHANNEL]
    assert [m[1] for m in on_messages] == [72, 76, 79]


def test_ticks_short_of_a_full_bar_do_not_advance():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67], [65, 69, 72]], [48, 53])
    engine.start()
    output.sent.clear()

    clock.tick(TICKS_PER_BAR - 1)

    assert output.sent == []


def test_stop_sends_all_notes_off_and_detaches_from_clock():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67]], [48])
    engine.start()

    engine.stop()

    assert not engine.is_playing
    assert clock.callbacks == []
    assert ["all_off", CHORD_CHANNEL] in output.sent
    assert ["all_off", BASS_CHANNEL] in output.sent


def test_reset_clears_position_without_sending_midi():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67], [65, 69, 72]], [48, 53])

    engine.reset()

    assert engine.position == 0
    assert output.sent == []


def test_bass_enabled_defaults_true():
    output, clock, engine = make_engine()
    assert engine.bass_enabled is True


def test_disabling_bass_before_play_omits_bass_messages():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67]], [48])
    engine.bass_enabled = False

    engine.start()

    bass_messages = [m for m in output.sent if m[0] == 0x90 | BASS_CHANNEL]
    chord_on = [m for m in output.sent if m[0] == 0x90 | CHORD_CHANNEL]
    assert bass_messages == []
    assert chord_on  # chords are unaffected


def test_disabling_bass_mid_sustain_immediately_silences_it():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67]], [48])
    engine.start()
    output.sent.clear()

    engine.bass_enabled = False

    bass_off = [m for m in output.sent if m[0] == 0x80 | BASS_CHANNEL]
    assert bass_off == [[0x80 | BASS_CHANNEL, 36, 127]]


def test_disabling_bass_when_none_is_sounding_sends_nothing():
    output, clock, engine = make_engine()
    engine.bass_enabled = False  # never started playing
    assert output.sent == []


def test_re_enabling_bass_takes_effect_on_the_next_bar():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67], [65, 69, 72]], [48, 53])
    engine.bass_enabled = False
    engine.start()
    output.sent.clear()

    engine.bass_enabled = True
    clock.tick(TICKS_PER_BAR)

    bass_on = [m for m in output.sent if m[0] == 0x90 | BASS_CHANNEL]
    assert bass_on == [[0x90 | BASS_CHANNEL, 41, 127]]


def test_octave_shift_defaults_to_zero_and_moves_the_progression():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67]], [48])
    assert engine.octave_shift == 0

    engine.octave_shift = 1
    engine.start()

    chord_on = [m for m in output.sent if m[0] == 0x90 | CHORD_CHANNEL]
    bass_on = [m for m in output.sent if m[0] == 0x90 | BASS_CHANNEL]
    assert [m[1] for m in chord_on] == [84, 88, 91]  # +12 baseline + 12 (one octave up)
    assert bass_on == [[0x90 | BASS_CHANNEL, 48, 127]]  # -12 baseline + 12


def test_changing_octave_shift_mid_sustain_does_not_orphan_the_old_notes():
    """Regression test: note-off must target the exact notes a note-on
    used, even if octave_shift changes while that chord is still
    sounding -- otherwise the real notes get stuck and a note-off is sent
    to pitches that were never on."""
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67], [65, 69, 72]], [48, 53])
    engine.octave_shift = 0
    engine.start()  # turns on the first chord at octave_shift=0

    engine.octave_shift = 2  # the user turns the knob mid-sustain
    output.sent.clear()
    clock.tick(TICKS_PER_BAR)  # bar boundary: turn off chord 1, turn on chord 2

    off_messages = [m for m in output.sent if m[0] == 0x80 | CHORD_CHANNEL]
    on_messages = [m for m in output.sent if m[0] == 0x90 | CHORD_CHANNEL]
    # off targets what was actually turned on (octave_shift=0 at the time)
    assert [m[1] for m in off_messages] == [72, 76, 79]
    # the new chord uses the now-current octave_shift=2
    assert [m[1] for m in on_messages] == [101, 105, 108]


def test_set_clock_swaps_the_clock_while_stopped():
    output, clock, engine = make_engine()
    other_clock = FakeClock()

    engine.set_clock(other_clock)
    engine.load_progression([[60, 64, 67]], [48])
    engine.start()

    assert other_clock.started
    assert not clock.started  # the original clock was never touched


def test_set_clock_raises_while_playing():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67]], [48])
    engine.start()

    try:
        engine.set_clock(FakeClock())
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_humanize_velocity_defaults_true_and_can_be_disabled():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67]], [48])

    assert engine.humanize_velocity is True

    engine.humanize_velocity = False
    engine.start()

    chord_on = [m for m in output.sent if m[0] == 0x90 | CHORD_CHANNEL]
    assert [m[2] for m in chord_on] == [FULL_VELOCITY] * 3


def test_start_without_a_loaded_progression_is_a_no_op():
    output, clock, engine = make_engine()

    engine.start()

    assert not engine.is_playing
    assert output.sent == []


def test_on_loop_complete_does_not_fire_before_a_full_pass():
    output, clock, engine = make_engine()
    calls = []
    engine.on_loop_complete = calls.append
    engine.load_progression([[60, 64, 67], [65, 69, 72]], [48, 53])

    engine.start()
    clock.tick(TICKS_PER_BAR)  # advances to the second (last) chord

    assert calls == []


def test_on_loop_complete_fires_once_per_full_pass_through_the_progression():
    output, clock, engine = make_engine()
    calls = []
    engine.on_loop_complete = lambda: calls.append(None)
    engine.load_progression([[60, 64, 67], [65, 69, 72]], [48, 53])

    engine.start()
    clock.tick(TICKS_PER_BAR * 2)  # wraps back to the first chord

    assert len(calls) == 1


def test_on_loop_complete_can_swap_in_a_new_progression_at_the_boundary():
    output, clock, engine = make_engine()

    def reload_progression():
        engine.load_progression([[72, 76, 79]], [60])

    engine.on_loop_complete = reload_progression
    engine.load_progression([[60, 64, 67], [65, 69, 72]], [48, 53])
    engine.start()
    output.sent.clear()

    clock.tick(TICKS_PER_BAR * 2)  # completes the 2-chord loop, triggering the swap

    on_messages = [m for m in output.sent if m[0] == 0x90 | CHORD_CHANNEL]
    assert [m[1] for m in on_messages[-3:]] == [84, 88, 91]  # the new chord, +12
