import random

from moodr import theory


def test_c_major_scale_produces_expected_midi_notes():
    # C = midi int 0, major scale steps are whole/whole/half/whole/whole/whole/half
    midi_notes = theory.to_midi_conversion(0, theory.major_intervals)
    assert midi_notes == [0, 2, 4, 5, 7, 9, 11, 12]


def test_c_major_i_chord_is_c_e_g():
    midi_roots = theory.to_midi_conversion(0, theory.major_intervals)
    backend_notenumeral = theory.from_midi_conversion(midi_roots, theory.major_intervals)
    chords = theory.root_mode_to_midi_chord(midi_roots, backend_notenumeral, "Major")
    assert chords[0] == [0, 4, 7]


def test_c_major7_i_chord_adds_major_seventh():
    midi_roots = theory.to_midi_conversion(0, theory.major_intervals)
    backend_notenumeral = theory.from_midi_conversion(midi_roots, theory.major_intervals)
    chords = theory.root_mode_to_midi_chord(midi_roots, backend_notenumeral, "Major 7")
    assert chords[0] == [0, 4, 7, 11]


def test_a_minor_i_chord_is_a_c_e():
    midi_root = theory.note_to_midi_int('A')
    midi_roots = theory.to_midi_conversion(midi_root, theory.minor_intervals)
    backend_notenumeral = theory.from_midi_conversion(midi_roots, theory.minor_intervals)
    chords = theory.root_mode_to_midi_chord(midi_roots, backend_notenumeral, "Minor")
    assert chords[0] == [9, 12, 16]


def test_note_to_midi_int_handles_sharps():
    assert theory.note_to_midi_int('C') == 0
    assert theory.note_to_midi_int('C#') == 1
    assert theory.note_to_midi_int('B') == 11


def test_midi_int_to_note_wraps_across_octaves():
    assert theory.midi_int_to_note(0) == 'C'
    assert theory.midi_int_to_note(12) == 'C'
    assert theory.midi_int_to_note(13) == 'C#'


def test_determine_mode_dispatches_on_key_substring():
    assert theory.determine_mode('Major') is theory.major_intervals
    assert theory.determine_mode('Minor 7') is theory.minor_intervals
    assert theory.determine_mode('Byzintine') is theory.byzintine_intervals
    assert theory.determine_mode('snhtri') is theory.snh_intervals


def test_prog_conv_maps_digits_to_roman_numerals():
    assert theory.prog_conv([1, 4, 5]) == ['I', 'IV', 'V']


def test_ui_conv_capitalizes_only_first_letter():
    assert theory.ui_conv(['ebIII', 'aVI']) == ['EbIII', 'AVI']


def test_snhtri_is_seedable_and_reproducible():
    rng_a = random.Random(42)
    rng_b = random.Random(42)
    assert theory.snhtri(rng_a) == theory.snhtri(rng_b)


def test_snhtri_values_are_always_one_or_two():
    values = theory.snhtri(random.Random(7))
    assert len(values) == 8
    assert all(v in (1, 2) for v in values)


def test_make_snh_intervals_root_is_always_zero():
    intervals = theory.make_snh_intervals(random.Random(1))
    assert intervals['I'] == 0
    assert len(intervals) == 8


# The interval shape each chord-name suffix claims, measured from the
# chord's own root. Used to check a name never describes different notes
# than root_mode_to_midi_chord() actually builds.
SHAPE_FOR_SUFFIX = {
    "": [0, 4, 7],              # major triad
    "maj7": [0, 4, 7, 11],
    "°": [0, 3, 6],        # diminished triad
    "m7b5": [0, 3, 6, 10],      # half-diminished
    "m": [0, 3, 7],             # minor triad
    "m7": [0, 3, 7, 10],
}


def test_chord_names_for_e_minor_seventh():
    chords, roots, numerals, names = _full_scale('E', 'Minor 7')
    assert names == ['Em7', 'F#m7b5', 'Gmaj7', 'Am7', 'Bm7', 'Cmaj7', 'Dmaj7', 'Em7']


def test_chord_names_drop_the_seventh_for_a_triad_mode():
    chords, roots, numerals, names = _full_scale('E', 'Minor')
    assert names == ['Em', 'F#°', 'G', 'Am', 'Bm', 'C', 'D', 'Em']


def test_chord_names_for_c_major():
    chords, roots, numerals, names = _full_scale('C', 'Major')
    assert names == ['C', 'Dm', 'Em', 'F', 'G', 'Am', 'B°', 'C']


def test_byzantine_seventh_degree_is_named_major_matching_the_chord_built():
    """Byzantine's 'VII°' looks diminished but isn't played that way:
    root_mode_to_midi_chord() tests isupper() first, and '°' is uncased so
    'VII°'.isupper() is True, giving it a major chord. chord_names()
    mirrors that branch order deliberately, so the label matches the
    notes rather than the numeral."""
    chords, roots, numerals, names = _full_scale('C', 'Byzintine')
    seventh_degree = numerals.index('VII°')
    chord = chords[seventh_degree]
    assert [note - chord[0] for note in chord] == [0, 4, 7]  # major triad
    assert names[seventh_degree] == 'A#'


def test_every_chord_name_matches_the_notes_actually_generated():
    for mode in theory.Modes:
        for key in theory.Note_Dict:
            chords, roots, numerals, names = _full_scale(key, mode)
            for numeral, chord, name in zip(numerals, chords, names):
                suffix = name[len(theory.midi_int_to_note(chord[0])):]
                shape = [note - chord[0] for note in chord]
                assert shape == SHAPE_FOR_SUFFIX[suffix], (key, mode, numeral, name)


def _full_scale(key, mode):
    """The same composition of theory functions the GUI's
    generate_full_scale() performs, kept here so these tests don't need to
    import the Qt app just to build a scale."""
    intervals = theory.determine_mode(mode)
    roots = theory.to_midi_conversion(theory.note_to_midi_int(key) + 48, intervals)
    backend = theory.from_midi_conversion(roots, intervals)
    chords = theory.root_mode_to_midi_chord(roots, backend, mode)
    names = theory.chord_names(roots, backend, mode)
    return chords, roots, list(intervals.keys()), names
