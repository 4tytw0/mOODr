import random

from moodr.midi_io import FULL_VELOCITY
from moodr.playback import (
    ACID_CHANNEL,
    ACID_STEP_TICKS,
    ACID_STEPS,
    ARP_CHANNEL,
    ARP_RATE_TICKS,
    BASS_CHANNEL,
    CHORD_CHANNEL,
    TICKS_PER_BAR,
    PlaybackEngine,
    _arp_note_for_step,
    _generate_acid_pattern,
)


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

    chord_messages = [m for m in output.sent if m[0] in (0x90 | CHORD_CHANNEL, 0x80 | CHORD_CHANNEL)]
    bass_messages = [m for m in output.sent if m[0] in (0x90 | BASS_CHANNEL, 0x80 | BASS_CHANNEL)]
    assert chord_messages == []
    assert bass_messages == []


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


def test_chords_enabled_defaults_true():
    output, clock, engine = make_engine()
    assert engine.chords_enabled is True


def test_disabling_chords_before_play_omits_chord_messages():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67]], [48])
    engine.chords_enabled = False

    engine.start()

    chord_on = [m for m in output.sent if m[0] == 0x90 | CHORD_CHANNEL]
    bass_on = [m for m in output.sent if m[0] == 0x90 | BASS_CHANNEL]
    assert chord_on == []
    assert bass_on  # bass is unaffected


def test_disabling_chords_mid_sustain_immediately_silences_them():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67]], [48])
    engine.start()
    output.sent.clear()

    engine.chords_enabled = False

    chord_off = [m for m in output.sent if m[0] == 0x80 | CHORD_CHANNEL]
    assert [m[1] for m in chord_off] == [72, 76, 79]


def test_re_enabling_chords_takes_effect_on_the_next_bar():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67], [65, 69, 72]], [48, 53])
    engine.chords_enabled = False
    engine.start()
    output.sent.clear()

    engine.chords_enabled = True
    clock.tick(TICKS_PER_BAR)

    chord_on = [m[1] for m in output.sent if m[0] == 0x90 | CHORD_CHANNEL]
    assert chord_on == [77, 81, 84]


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


def test_set_progression_does_not_reset_position_or_sounding_state():
    output, clock, engine = make_engine()
    engine.load_progression([[60], [65], [70], [75]], [48, 53, 58, 63])
    engine.start()
    clock.tick(TICKS_PER_BAR * 2)  # 2 bar advances since start()'s own initial one

    engine.set_progression([[60], [65], [70], [75]], [48, 53, 58, 63])

    assert engine.position == 3  # unaffected by set_progression -- not reset to 0


def test_late_on_loop_complete_reload_does_not_replay_the_first_chord():
    """Regression test for a real race condition: on_loop_complete fires as
    a queued cross-thread Qt signal in the real app, so the GUI's reload
    can arrive *after* the clock thread has already advanced one or more
    further bars past the loop boundary that triggered it. Using
    load_progression() (which resets position to 0) for that reload would
    rewind an already-advanced position, replaying the first chord an
    extra time -- exactly the "1 2 3 4 1 1 2 3 4 1" bug a user reported.
    This simulates the delay explicitly: on_loop_complete only records
    that a reload was requested, and the test applies it (via
    set_progression, as the real MainWindow now does) only after ticking
    an extra bar past the boundary -- mimicking the reload arriving late.
    """
    output, clock, engine = make_engine()
    progression = ([[60], [65], [70], [75]], [48, 53, 58, 63])
    reload_requested = []
    engine.on_loop_complete = lambda: reload_requested.append(True)
    engine.load_progression(*progression)
    engine.start()

    clock.tick(TICKS_PER_BAR * 4)  # completes one full loop -- on_loop_complete fires
    assert reload_requested
    clock.tick(TICKS_PER_BAR)  # the clock advances another bar before the reload "arrives"

    engine.set_progression(*progression)  # the delayed reload finally applies
    output.sent.clear()
    clock.tick(TICKS_PER_BAR)

    on_messages = [m[1] for m in output.sent if m[0] == 0x90 | CHORD_CHANNEL]
    assert on_messages == [82]  # chords[2] (70+12), continuing on -- not a repeat of chord 1


def test_arp_note_for_step_up():
    notes = [60, 64, 67]
    assert [_arp_note_for_step(notes, "up", i) for i in range(4)] == [60, 64, 67, 60]


def test_arp_note_for_step_down():
    notes = [60, 64, 67]
    assert [_arp_note_for_step(notes, "down", i) for i in range(4)] == [67, 64, 60, 67]


def test_arp_note_for_step_up_down_does_not_repeat_the_turnaround_notes():
    notes = [60, 64, 67, 71]
    assert [_arp_note_for_step(notes, "up_down", i) for i in range(8)] == \
        [60, 64, 67, 71, 67, 64, 60, 64]


def test_arp_note_for_step_up_down_degenerates_gracefully_for_two_notes():
    notes = [60, 64]
    assert [_arp_note_for_step(notes, "up_down", i) for i in range(4)] == [60, 64, 60, 64]


def test_arp_note_for_step_random_is_seedable_and_reproducible():
    notes = [60, 64, 67]
    picks_a = [_arp_note_for_step(notes, "random", i, random.Random(1)) for i in range(10)]
    picks_b = [_arp_note_for_step(notes, "random", i, random.Random(1)) for i in range(10)]
    assert picks_a == picks_b
    assert all(pick in notes for pick in picks_a)


def test_arp_note_for_step_unknown_pattern_raises():
    try:
        _arp_note_for_step([60, 64, 67], "sideways", 0)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_engine_arp_pattern_and_rate_are_live_settable():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67]], [48])
    engine.arp_pattern = "up"
    engine.arp_rate = "1/16"
    engine.start()
    output.sent.clear()

    clock.tick(ARP_RATE_TICKS["1/16"])

    arp_on = [m[1] for m in output.sent if m[0] == 0x90 | ARP_CHANNEL]
    assert arp_on == [72]  # "up" pattern's first (lowest) note, 60 + 12 baseline


def test_arp_defaults_enabled():
    output, clock, engine = make_engine()
    assert engine.arp_enabled is True


def test_arp_steps_down_through_chord_notes_at_eighth_note_rate():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67]], [48])
    engine.start()
    output.sent.clear()

    step_ticks = ARP_RATE_TICKS["1/8"]
    clock.tick(step_ticks)
    clock.tick(step_ticks)
    clock.tick(step_ticks)
    clock.tick(step_ticks)

    on_notes = [m[1] for m in output.sent if m[0] == 0x90 | ARP_CHANNEL]
    # descending through [67, 64, 60] (+12 baseline octave), then wraps
    assert on_notes == [79, 76, 72, 79]


def test_arp_turns_off_the_previous_note_before_playing_the_next():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67]], [48])
    engine.start()
    output.sent.clear()

    step_ticks = ARP_RATE_TICKS["1/8"]
    clock.tick(step_ticks)
    clock.tick(step_ticks)

    arp_events = [(m[0], m[1]) for m in output.sent if m[0] in (0x90 | ARP_CHANNEL, 0x80 | ARP_CHANNEL)]
    assert arp_events == [(0x90 | ARP_CHANNEL, 79), (0x80 | ARP_CHANNEL, 79), (0x90 | ARP_CHANNEL, 76)]


def test_arp_note_offs_do_not_suppress_the_bass_note_off():
    """Regression test: an arp note-off must not touch bass tracking --
    otherwise the next bar boundary's _turn_off_sounding() would skip the
    bass note-off (thinking bass wasn't sounding), leaking a stuck note
    that never releases as the progression continues."""
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67], [65, 69, 72]], [48, 53])
    engine.start()

    clock.tick(ARP_RATE_TICKS["1/8"])  # at least one arp note-off happens
    output.sent.clear()

    clock.tick(TICKS_PER_BAR - ARP_RATE_TICKS["1/8"])  # reach the bar boundary

    bass_off = [m for m in output.sent if m[0] == 0x80 | BASS_CHANNEL]
    assert bass_off == [[0x80 | BASS_CHANNEL, 36, 127]]


def test_disabling_arp_mid_note_immediately_silences_it():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67]], [48])
    engine.start()
    clock.tick(ARP_RATE_TICKS["1/8"])  # first arp note-on fires
    output.sent.clear()

    engine.arp_enabled = False

    arp_off = [m[1] for m in output.sent if m[0] == 0x80 | ARP_CHANNEL]
    assert arp_off == [79]


def test_arp_resets_to_the_top_of_the_pattern_on_chord_change():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67], [65, 69, 72]], [48, 53])
    engine.start()

    clock.tick(TICKS_PER_BAR)  # advances to chord 2, resetting the arp pattern

    arp_on = [m[1] for m in output.sent if m[0] == 0x90 | ARP_CHANNEL]
    # the last arp note-on before/at the bar boundary is chord 2's top note
    # ([72, 69, 65] descending), not a continuation of chord 1's sequence
    assert arp_on[-1] == 84


def test_acid_step_count_and_rate_span_exactly_one_bar():
    assert ACID_STEPS * ACID_STEP_TICKS == TICKS_PER_BAR


def test_generate_acid_pattern_zero_noise_is_all_home_notes():
    assert _generate_acid_pattern(0.0, wide_deviation=True) == [0] * ACID_STEPS


def test_generate_acid_pattern_max_noise_is_all_rests():
    # noise=1.0 -- random.random() < 1.0 is always true, so the rest check
    # always wins before the deviation check ever gets a chance to run.
    assert _generate_acid_pattern(1.0, wide_deviation=True) == [None] * ACID_STEPS


def test_generate_acid_pattern_is_seedable_and_reproducible():
    a = _generate_acid_pattern(0.5, True, random.Random(1))
    b = _generate_acid_pattern(0.5, True, random.Random(1))
    assert a == b
    assert len(a) == ACID_STEPS


def test_generate_acid_pattern_narrow_deviation_uses_narrow_offsets():
    deviated = set()
    for seed in range(50):
        pattern = _generate_acid_pattern(0.9, wide_deviation=False, rng=random.Random(seed))
        deviated.update(v for v in pattern if v not in (None, 0))
    assert deviated  # at least some deviations occurred across all trials
    assert deviated <= {-2, -1, 1, 2}


def test_generate_acid_pattern_wide_deviation_can_reach_beyond_narrow_range():
    deviated = set()
    for seed in range(50):
        pattern = _generate_acid_pattern(0.9, wide_deviation=True, rng=random.Random(seed))
        deviated.update(v for v in pattern if v not in (None, 0))
    assert deviated <= set(range(-7, 8)) - {0}
    assert deviated - {-2, -1, 1, 2}  # reaches beyond narrow's range at least sometimes


def test_acid_defaults_enabled():
    output, clock, engine = make_engine()
    assert engine.acid_enabled is True


def test_acid_plays_home_note_every_step_at_zero_noise():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67]], [48])
    engine.acid_noise = 0.0
    engine.randomize_acid_pattern()
    engine.start()
    output.sent.clear()

    clock.tick(ACID_STEP_TICKS)

    acid_on = [m[1] for m in output.sent if m[0] == 0x90 | ACID_CHANNEL]
    assert acid_on == [60]  # home note = bass root 48, +12 baseline


def test_acid_rest_step_produces_no_note():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67]], [48])
    engine._acid_pattern = [None] * ACID_STEPS
    engine.start()
    output.sent.clear()

    clock.tick(ACID_STEP_TICKS)

    acid_msgs = [m for m in output.sent if m[0] in (0x90 | ACID_CHANNEL, 0x80 | ACID_CHANNEL)]
    assert acid_msgs == []


def test_acid_deviation_uses_the_full_scale_via_set_scale():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67]], [48])
    engine.set_scale([48, 50, 52, 53, 55, 57, 59, 60])
    engine._acid_pattern = [2] + [None] * (ACID_STEPS - 1)
    engine.start()
    output.sent.clear()

    clock.tick(ACID_STEP_TICKS)

    acid_on = [m[1] for m in output.sent if m[0] == 0x90 | ACID_CHANNEL]
    # home (48) is scale index 0; +2 degrees -> scale[2]=52, +12 baseline
    assert acid_on == [64]


def test_acid_deviation_falls_back_gracefully_if_home_note_not_in_scale():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67]], [999])
    engine.set_scale([48, 50, 52])
    engine._acid_pattern = [1] + [None] * (ACID_STEPS - 1)
    engine.start()
    output.sent.clear()

    clock.tick(ACID_STEP_TICKS)

    acid_on = [m[1] for m in output.sent if m[0] == 0x90 | ACID_CHANNEL]
    # home note isn't in the scale -- falls back to index 0, so scale[1]=50
    assert acid_on == [62]


def test_acid_home_note_tracks_the_current_bars_chord_root():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67], [65, 69, 72]], [48, 53])
    engine._acid_pattern = [0] * ACID_STEPS
    engine.start()
    output.sent.clear()

    clock.tick(TICKS_PER_BAR)  # advances to bar 2 partway through this span

    acid_on = [m[1] for m in output.sent if m[0] == 0x90 | ACID_CHANNEL]
    assert acid_on[-1] == 65  # bar 2's root (53) + 12 baseline


def test_disabling_acid_mid_note_immediately_silences_it():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67]], [48])
    engine.acid_noise = 0.0
    engine.randomize_acid_pattern()
    engine.start()
    clock.tick(ACID_STEP_TICKS)
    output.sent.clear()

    engine.acid_enabled = False

    acid_off = [m[1] for m in output.sent if m[0] == 0x80 | ACID_CHANNEL]
    assert acid_off == [60]


def test_randomize_acid_pattern_replaces_the_pattern():
    output, clock, engine = make_engine()
    engine.acid_noise = 0.0
    engine.randomize_acid_pattern()
    pattern_a = list(engine._acid_pattern)

    engine.acid_noise = 1.0
    engine.randomize_acid_pattern()
    pattern_b = list(engine._acid_pattern)

    assert pattern_a == [0] * ACID_STEPS
    assert pattern_b == [None] * ACID_STEPS


def test_stop_sends_all_notes_off_for_acid_channel():
    output, clock, engine = make_engine()
    engine.load_progression([[60, 64, 67]], [48])
    engine.start()

    engine.stop()

    assert ["all_off", ACID_CHANNEL] in output.sent
