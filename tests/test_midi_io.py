import random

from moodr import midi_io


def test_midi_output_opens_and_closes():
    output = midi_io.MidiOutput()
    assert not output.is_open
    output.open()
    assert output.is_open
    output.send([0x90, 60, 100])
    output.close()
    assert not output.is_open


def test_midi_output_default_open_creates_a_named_virtual_port():
    """So m00Dr shows up as a selectable MIDI input source in DAWs like
    Ableton, instead of silently opening whatever real port is at index 0."""
    output = midi_io.MidiOutput()
    output.open()
    assert output.port_name == "m00Dr"
    output.close()


def test_midi_output_can_open_a_specific_real_port_by_index():
    ports = midi_io.MidiOutput.list_ports()
    if not ports:
        return  # no real ports on this machine to test against
    output = midi_io.MidiOutput()
    output.open(port_index=0)
    assert output.port_name == ports[0]
    output.close()


def test_midi_output_context_manager():
    with midi_io.MidiOutput() as output:
        assert output.is_open
        output.send([0x90, 60, 100])
    assert not output.is_open


def test_bpm_conversion_matches_known_values():
    assert midi_io.bpm_conversion(60) == 4.0
    assert midi_io.bpm_conversion(120) == 2.0


def test_random_velocity_is_seedable_and_reproducible():
    assert midi_io.random_velocity(random.Random(1)) == midi_io.random_velocity(random.Random(1))


def test_random_velocity_stays_in_old_apps_range():
    for _ in range(50):
        assert 72 <= midi_io.random_velocity() <= 108


def test_midi_message_gen_offsets_octave_up_and_applies_state():
    messages = midi_io.midi_message_gen(0x90, [[48, 52, 55]], 0, rng=random.Random(1))
    assert [m[:2] for m in messages] == [[0x90, 60], [0x90, 64], [0x90, 67]]
    assert all(72 <= m[2] <= 108 for m in messages)


def test_midi_message_gen_selects_requested_position():
    messages = midi_io.midi_message_gen(0x80, [[48], [50], [52]], 1)
    assert [m[1] for m in messages] == [62]


def test_midi_message_gen_humanize_false_uses_full_velocity():
    messages = midi_io.midi_message_gen(0x90, [[48, 52, 55]], 0, humanize=False)
    assert [m[2] for m in messages] == [midi_io.FULL_VELOCITY] * 3


def test_midi_message_gen_humanize_true_randomizes_velocity():
    messages = midi_io.midi_message_gen(0x90, [[48, 52, 55]], 0, rng=random.Random(1), humanize=True)
    assert all(72 <= m[2] <= 108 for m in messages)


def test_bass_message_gen_offsets_octave_down_with_fixed_velocity():
    assert midi_io.bass_message_gen(0x91, [48, 53, 55], 0) == [0x91, 36, 127]
    assert midi_io.bass_message_gen(0x81, [48, 53, 55], 1) == [0x81, 41, 127]


def test_midi_input_opens_and_closes():
    midi_input = midi_io.MidiInput()
    assert not midi_input.is_open
    midi_input.open()
    assert midi_input.is_open
    midi_input.close()
    assert not midi_input.is_open


def test_midi_input_default_open_creates_a_named_virtual_port():
    """So m00Dr shows up as a selectable Sync destination in DAWs like
    Ableton, the same way MidiOutput does for note output."""
    midi_input = midi_io.MidiInput()
    midi_input.open()
    assert midi_input.port_name == "m00Dr In"
    midi_input.close()


def test_midi_input_can_open_a_specific_real_port_by_index():
    ports = midi_io.MidiInput.list_ports()
    if not ports:
        return  # no real ports on this machine to test against
    midi_input = midi_io.MidiInput()
    midi_input.open(port_index=0)
    assert midi_input.port_name == ports[0]
    midi_input.close()


def test_midi_input_reopen_after_close_works():
    """Regression check for the virtual-port delete()/reuse quirk: opening,
    closing, and reopening (as happens toggling sync mode repeatedly)
    should not crash or leave stale state."""
    midi_input = midi_io.MidiInput()
    for _ in range(3):
        midi_input.open()
        assert midi_input.is_open
        midi_input.close()
        assert not midi_input.is_open
