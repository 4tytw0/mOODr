"""Tests for the circle-of-fifths / I Ching roll (moodr/oracle.py)."""

import random
from collections import Counter

import pytest

from moodr import oracle, theory


# -- the coin tosses ------------------------------------------------------

def test_toss_range():
    rng = random.Random(1)
    assert all(0 <= oracle.toss(rng) <= 3 for _ in range(500))


def test_toss_is_three_coins_not_a_flat_draw():
    """The 1/8, 3/8, 3/8, 1/8 weighting is the whole reason for sourcing
    randomness from coin tosses, so it is worth pinning down: a flat draw
    over the same four values would make the rare bold move four times as
    common as intended."""
    rng = random.Random(7)
    counts = Counter(oracle.toss(rng) for _ in range(80_000))
    total = sum(counts.values())
    for value, expected in ((0, 0.125), (1, 0.375), (2, 0.375), (3, 0.125)):
        assert counts[value] / total == pytest.approx(expected, abs=0.01)


def test_cast_lines_length():
    assert len(oracle.cast_lines(random.Random(3))) == oracle.NUM_LINES


def test_hexagram_text_keeps_the_original_glyphs_top_line_first():
    lines = [0, 1, 2, 3, 2, 1]  # bottom to top
    text = oracle.hexagram_text(lines).splitlines()
    assert len(text) == 6
    # Drawn top-down, so the last line cast is printed first.
    assert text[0] == oracle.LINE_GLYPHS[1]
    assert text[-1] == oracle.LINE_GLYPHS[0]
    # Glyphs are verbatim from Tyler's iChing script.
    assert oracle.LINE_GLYPHS[0] == "-x-     ---"
    assert oracle.LINE_GLYPHS[3] == "-o-     - -"


# -- the circle of fifths ------------------------------------------------

def test_circle_of_fifths_is_the_real_circle():
    assert oracle.CIRCLE_OF_FIFTHS == [
        "C", "G", "D", "A", "E", "B", "F#", "C#", "G#", "D#", "A#", "F"]
    assert len(set(oracle.CIRCLE_OF_FIFTHS)) == 12


def test_one_step_is_a_perfect_fifth_in_either_direction():
    for key in theory.Note_Dict:
        here = theory.note_to_midi_int(key)
        assert theory.note_to_midi_int(oracle.shift_key(key, 1)) == (here + 7) % 12
        assert theory.note_to_midi_int(oracle.shift_key(key, -1)) == (here - 7) % 12


def test_twelve_fifths_returns_to_the_same_key():
    for key in theory.Note_Dict:
        assert oracle.shift_key(key, 12) == key


def test_fifths_position_agrees_with_the_circle():
    for position, key in enumerate(oracle.CIRCLE_OF_FIFTHS):
        assert oracle.fifths_position(key) == position


def test_no_line_value_ever_leaves_the_key_unchanged():
    """A roll should always be a move. None of the four possible steps is a
    multiple of 12 fifths, so none of them is a no-op."""
    for steps in oracle.LINE_TO_FIFTHS.values():
        for key in theory.Note_Dict:
            assert oracle.shift_key(key, steps) != key


# -- diatonic fifths -----------------------------------------------------

def test_a_diatonic_fifth_is_four_degrees_not_one():
    # I -> V, and I -> IV going the other way.
    assert oracle.shift_degree(0, 1) == 4
    assert oracle.shift_degree(0, -1) == 3


def test_degree_stays_inside_the_seven_scale_degrees():
    for index in range(8):  # 7 is the excluded octave tonic
        for steps in oracle.LINE_TO_FIFTHS.values():
            assert 0 <= oracle.shift_degree(index, steps) < oracle.DEGREES_PER_SCALE


def test_octave_tonic_folds_back_onto_the_tonic():
    """Index 7 is "2I"/"2i", a duplicate of degree 1, and is not part of
    the ring a roll moves around -- so rolling from it behaves as if it
    were the tonic rather than landing somewhere arbitrary."""
    for steps in oracle.LINE_TO_FIFTHS.values():
        assert oracle.shift_degree(7, steps) == oracle.shift_degree(0, steps)


def test_seven_diatonic_fifths_returns_to_the_same_degree():
    for index in range(oracle.DEGREES_PER_SCALE):
        assert oracle.shift_degree(index, 7) == index


# -- modes ---------------------------------------------------------------

def test_mode_families_pair_every_mode():
    families = oracle.mode_families()
    assert [m for family in families for m in family] == theory.Modes
    assert families[0] == ["Major", "Major 7"]
    assert families[-1] == ["snhtri", "snhtri7"]


def test_static_yin_leaves_the_scale_alone():
    for mode in theory.Modes:
        assert oracle.shift_mode(mode, 1) == mode


def test_static_yang_trades_the_triad_for_its_seventh():
    assert oracle.shift_mode("Minor", 2) == "Minor 7"
    assert oracle.shift_mode("Minor 7", 2) == "Minor"
    assert oracle.shift_mode("Byzintine", 2) == "Byzintine7"


def test_changing_lines_cross_into_another_family():
    assert oracle.shift_mode("Major", 3) == "Minor"
    assert oracle.shift_mode("Minor", 0) == "Major"
    # And the families wrap rather than dead-ending.
    assert oracle.shift_mode("Major", 0) == "snhtri"
    assert oracle.shift_mode("snhtri7", 3) == "Major 7"


def test_every_mode_and_line_yields_a_real_mode():
    for mode in theory.Modes:
        for line in oracle.LINE_GLYPHS:
            assert oracle.shift_mode(mode, line) in theory.Modes


def test_unknown_mode_is_left_alone_rather_than_raising():
    assert oracle.shift_mode("Lydian Dominant", 3) == "Lydian Dominant"


# -- the roll itself -----------------------------------------------------

def test_roll_is_reproducible_from_a_seed():
    first = oracle.roll("E", "Minor 7", [0, 1, 2, 3], random.Random(42))
    second = oracle.roll("E", "Minor 7", [0, 1, 2, 3], random.Random(42))
    assert (first.key, first.mode, first.slots, first.lines) == \
           (second.key, second.mode, second.slots, second.lines)


def test_roll_output_is_always_selectable():
    """Every rolled value has to be something the GUI can actually select:
    a key in the dropdown, a mode in the dropdown, and a degree index that
    exists in a progression slot."""
    for seed in range(400):
        cast = oracle.roll("E", "Minor 7", [0, 1, 2, 3], random.Random(seed))
        assert cast.key in theory.Note_Dict
        assert cast.mode in theory.Modes
        assert len(cast.slots) == 4
        assert all(0 <= s < oracle.DEGREES_PER_SCALE for s in cast.slots)
        assert len(cast.lines) == oracle.NUM_LINES


def test_roll_moves_every_selection_by_its_own_line():
    """The mapping from line position to what it governs is the contract
    the docstring states, so check it directly rather than trusting the
    aggregate."""
    for seed in range(60):
        rng = random.Random(seed)
        lines = oracle.cast_lines(random.Random(seed))
        cast = oracle.roll("C", "Major", [0, 1, 2, 3], rng)
        assert cast.lines == lines
        assert cast.key == oracle.shift_key("C", oracle.LINE_TO_FIFTHS[lines[0]])
        assert cast.mode == oracle.shift_mode("Major", lines[1])
        assert cast.slots[0] == oracle.ANCHOR_DEGREE
        for i, degree in enumerate(cast.slots[1:], start=1):
            steps = oracle.LINE_TO_FIFTHS[lines[oracle.FIRST_SLOT_LINE + i]]
            assert degree == oracle.shift_degree(i, steps)


def test_slot_one_always_lands_on_the_tonic():
    """The progression needs a home for the fifths moves in the other
    slots to be heard as movement away from something, so slot 1 is set to
    degree I rather than rolled -- from any starting degree, on any cast."""
    for seed in range(300):
        for start in range(oracle.DEGREES_PER_SCALE):
            cast = oracle.roll("E", "Minor 7", [start, 1, 2, 3], random.Random(seed))
            assert cast.slots[0] == oracle.ANCHOR_DEGREE == 0


def test_the_tonic_anchor_is_the_first_degree_of_every_mode():
    """ANCHOR_DEGREE is an index into the interval dict, so it is only the
    tonic if every mode actually starts on its tonic."""
    for mode in theory.Modes:
        intervals = theory.determine_mode(mode)
        first = list(intervals.keys())[oracle.ANCHOR_DEGREE]
        assert first in ("I", "i"), (mode, first)
        assert intervals[first] == 0, "the tonic sits at the root, no offset"


def test_the_other_slots_still_move():
    """Anchoring slot 1 must not have quietly frozen the rest of the row."""
    moved = [False, False, False]
    for seed in range(60):
        cast = oracle.roll("E", "Minor 7", [0, 1, 2, 3], random.Random(seed))
        for i in range(3):
            if cast.slots[i + 1] != [1, 2, 3][i]:
                moved[i] = True
    assert all(moved)


def test_roll_is_relative_so_repeated_rolls_walk_the_circle():
    """Two rolls from the same reading applied in sequence land the same
    place as a single roll of the combined step -- i.e. the moves compose,
    which is what makes repeated rolls a walk rather than a series of
    unrelated jumps."""
    rng = random.Random(11)
    first = oracle.roll("C", "Major", [0], rng)
    second = oracle.roll(first.key, first.mode, first.slots, rng)
    combined = (oracle.LINE_TO_FIFTHS[first.lines[0]]
                + oracle.LINE_TO_FIFTHS[second.lines[0]])
    assert second.key == oracle.shift_key("C", combined)


def test_roll_leaves_extra_slots_alone():
    """Only four lines are left over for slots, so a fifth slot -- if
    NUM_NUMERAL_SLOTS were ever raised -- keeps its value instead of being
    silently dropped or reading off the end of the hexagram."""
    cast = oracle.roll("C", "Major", [0, 1, 2, 3, 5, 6], random.Random(5))
    assert cast.slots[4:] == [5, 6]


def test_describe_and_drawing_cover_every_line():
    cast = oracle.roll("E", "Minor 7", [0, 1, 2, 3], random.Random(9))
    assert len(cast.drawing.splitlines()) == 6
    described = cast.describe()
    assert "key" in described and "scale" in described
    assert described.count("slot") == 4
    # Slot 1's line is still drawn and still reported, just as the anchor.
    assert "anchored" in described.splitlines()[2]


def test_a_rolled_scale_still_generates_playable_chords():
    """Guards the seam with theory.py: a rolled key/mode pair has to be
    something generate_full_scale can actually build, since the GUI feeds
    it straight into the engine."""
    for seed in range(120):
        cast = oracle.roll("E", "Minor 7", [0, 1, 2, 3], random.Random(seed))
        intervals = theory.determine_mode(cast.mode)
        assert intervals is not None
        root = theory.note_to_midi_int(cast.key) + 48
        roots = theory.to_midi_conversion(root, intervals)
        numerals = theory.from_midi_conversion(roots, intervals)
        chords = theory.root_mode_to_midi_chord(roots, numerals, cast.mode)
        for degree in cast.slots:
            assert chords[degree], f"degree {degree} produced no notes"


# -- naming the cast -----------------------------------------------------

def test_polarity_agrees_with_the_drawn_glyphs():
    """The polarity helpers and the glyph table are two readings of the
    same four numbers, so they have to agree: the left-hand column of
    Tyler's glyph is the line now, the right-hand column is what it
    becomes. If these ever disagreed the label would name a hexagram the
    drawing doesn't show."""
    for value, glyph in oracle.LINE_GLYPHS.items():
        now, becomes = glyph[:3], glyph[-3:]
        assert oracle.is_yang(value) == (now in ("---", "-o-")), value
        assert oracle.transformed_polarity(value) == (becomes == "---"), value
        assert oracle.is_changing(value) == (now in ("-x-", "-o-")), value


def test_only_the_rare_lines_change():
    assert oracle.CHANGING_VALUES == {0, 3}
    for value in (1, 2):
        assert oracle.transformed_polarity(value) == oracle.is_yang(value)
    for value in (0, 3):
        assert oracle.transformed_polarity(value) != oracle.is_yang(value)


def test_all_eight_trigrams_are_present_and_distinct():
    assert len(oracle.TRIGRAMS) == 8
    assert len({t.glyph for t in oracle.TRIGRAMS.values()}) == 8
    assert len({t.name for t in oracle.TRIGRAMS.values()}) == 8


def test_trigram_glyphs_match_their_unicode_codepoints():
    """The Unicode trigram block encodes the figure itself: U+2630 plus
    4*bottom + 2*middle + 1*top, counting yin as 1. Checking the table
    against that formula catches a glyph pasted onto the wrong pattern,
    which no amount of looking at the names would."""
    for (bottom, middle, top), trigram in oracle.TRIGRAMS.items():
        offset = 4 * (not bottom) + 2 * (not middle) + 1 * (not top)
        assert trigram.glyph == chr(0x2630 + offset), trigram


def test_the_famous_trigrams_are_the_right_way_up():
    assert oracle.trigram_for([True, True, True]).image == "Heaven"
    assert oracle.trigram_for([False, False, False]).image == "Earth"
    # Fire is solid outside, broken within; Water the reverse.
    assert oracle.trigram_for([True, False, True]).image == "Fire"
    assert oracle.trigram_for([False, True, False]).image == "Water"
    # Mountain has its solid line on top, Thunder at the bottom.
    assert oracle.trigram_for([False, False, True]).image == "Mountain"
    assert oracle.trigram_for([True, False, False]).image == "Thunder"


def test_king_wen_table_is_a_real_sequence():
    numbers = [n for row in oracle._KING_WEN_TABLE for n in row]
    assert sorted(numbers) == list(range(1, 65)), "not a permutation of 1..64"
    assert len(oracle._KING_WEN_ORDER) == 8
    assert set(oracle._KING_WEN_ORDER) == {t.name for t in oracle.TRIGRAMS.values()}


def test_the_hexagrams_everyone_knows():
    def number(bits):
        return oracle.hexagram_for([b == "1" for b in bits]).number
    #          bottom -> top
    assert number("111111") == 1    # Heaven over Heaven
    assert number("000000") == 2    # Earth over Earth
    assert number("100010") == 3    # Water over Thunder
    assert number("010001") == 4    # Mountain over Water
    assert number("111000") == 11   # Earth over Heaven, Peace
    assert number("000111") == 12   # Heaven over Earth, Standstill
    assert number("010010") == 29   # Water over Water
    assert number("101101") == 30   # Fire over Fire
    assert number("101010") == 63   # Water over Fire, After Completion
    assert number("010101") == 64   # Fire over Water, Before Completion


def test_every_hexagram_is_named_and_reachable():
    assert sorted(oracle.HEXAGRAM_NAMES) == list(range(1, 65))
    seen = set()
    for n in range(64):
        polarities = [bool(int(b)) for b in f"{n:06b}"]
        hexagram = oracle.hexagram_for(polarities)
        seen.add(hexagram.number)
        assert hexagram.name and hexagram.meaning
        assert len(hexagram.glyph) == 1
    assert seen == set(range(1, 65)), "some hexagram is unreachable"


def test_hexagram_glyphs_are_the_king_wen_ordered_block():
    for number, (name, _meaning) in oracle.HEXAGRAM_NAMES.items():
        polarities = next(p for p in
                          ([bool(int(b)) for b in f"{n:06b}"] for n in range(64))
                          if oracle.hexagram_for(p).number == number)
        assert oracle.hexagram_for(polarities).glyph == chr(0x4DC0 + number - 1)


def test_cast_names_its_hexagram_and_its_transformation():
    for seed in range(200):
        cast = oracle.roll("E", "Minor 7", [0, 1, 2, 3], random.Random(seed))
        assert 1 <= cast.hexagram.number <= 64
        changing = any(oracle.is_changing(v) for v in cast.lines)
        assert (cast.transformation is not None) == changing
        if changing:
            # A changing line flips a polarity, so the two must differ.
            assert cast.transformation.number != cast.hexagram.number


def test_reading_names_both_trigrams_upper_first():
    cast = oracle.roll("E", "Minor 7", [0, 1, 2, 3], random.Random(1234))
    reading = cast.reading()
    assert len(reading.splitlines()) == 2
    hexagram = cast.hexagram
    assert str(hexagram.number) in reading and hexagram.meaning in reading
    assert hexagram.upper.image in reading and hexagram.lower.image in reading
    assert reading.index(hexagram.upper.name) < reading.index(hexagram.lower.name)


def test_the_cast_lines_and_the_named_hexagram_are_the_same_figure():
    """The drawing and the name are produced by different code paths, so
    pin that they describe one figure: the drawn lines, read bottom-up,
    must be the polarities the hexagram was looked up from."""
    for seed in range(120):
        cast = oracle.roll("C", "Major", [0, 1, 2, 3], random.Random(seed))
        drawn = list(reversed(cast.drawing.splitlines()))  # back to bottom-first
        for line, yang in zip(drawn, cast.polarities):
            assert (line[:3] in ("---", "-o-")) == yang
