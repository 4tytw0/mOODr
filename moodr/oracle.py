"""Circle-of-fifths transitions, chosen by I Ching coin tosses.

Two ideas, kept in one module because they only make sense together.

**The move** is always a signed number of fifths. A key moves around the
circle of fifths (C -> G -> D -> ...), and a progression slot moves around
the *diatonic* circle of fifths within the current scale (I -> V -> ii ->
vi -> ...). One step is musically the closest possible relation in both
cases, which is what makes a roll read as a modulation rather than a jump
to an unrelated place. Scale/mode has no circle to move around, so it gets
its own table (see `shift_mode`).

**The randomness** is Tyler's iChing script (`Code-ish/iChing`), unchanged
in mechanism: three coin flips per line, summed, giving 0-3. The weighting
that falls out of that is the reason to use it rather than a flat
`choice()` -- the two static values come up 3/8 of the time each and the
two changing values only 1/8 each, so a roll usually nudges by one fifth
and occasionally leaps by two. A flat draw over the same four values would
make the bold move four times as common as it should be.

Six lines govern the six selections, bottom line first, the order a
hexagram is traditionally read in. The two trigrams split the work the
same way the app does -- the lower three are the ground the progression
stands on, the upper three are what moves over it:

    line 1  key                  |
    line 2  scale / mode         |  lower trigram: the ground
    line 3  progression slot 1   |  (anchored to the tonic -- never moves)

    line 4  progression slot 2   |
    line 5  progression slot 3   |  upper trigram: the movement
    line 6  progression slot 4   |

Slot 1 holding the tonic is deliberate, not a gap: a progression that
starts somewhere other than I has no home to be heard as moving away
from, so rolling it would undercut the fifths moves in the slots that
follow. Its line is still cast and still shown in the reading; it simply
governs the one selection that stays put.

Everything here is pure and takes an optional `rng`, so a roll can be
replayed exactly in a test. No Qt, no MIDI.
"""

import random
from dataclasses import dataclass

from . import theory

# One fifth, in semitones. Repeatedly adding this modulo 12 *is* the
# circle of fifths, which is why CIRCLE_OF_FIFTHS below is derived from it
# rather than typed out -- the two can't drift apart.
FIFTH_SEMITONES = 7

# Scale degrees a roll will move a progression slot between: I..VII. The
# interval dicts in theory.py have an eighth entry ("2I"/"2i", the octave
# tonic) which is deliberately excluded -- it's a duplicate of the first
# degree, so counting it would make "up a fifth" land four degrees up an
# eight-item ring instead of a real fifth. It stays selectable by hand.
DEGREES_PER_SCALE = 7

# Progression slot 1 always lands here: degree index 0, the tonic (the "I"
# or "i" that every interval dict in theory.py starts with).
ANCHOR_DEGREE = 0

# A tossed line's value (0-3) as a signed number of fifths. The changing
# lines -- the rare ones -- are the bold moves; that is the whole point of
# drawing them this way.
LINE_TO_FIFTHS = {
    0: -2,   # changing yin  (1/8)
    1: -1,   # static yin    (3/8)
    2: +1,   # static yang   (3/8)
    3: +2,   # changing yang (1/8)
}

# The glyphs from the original iChing script, preserved exactly: present
# hexagram in the left column, the one it transforms into on the right.
LINE_GLYPHS = {
    0: "-x-     ---",
    1: "- -     - -",
    2: "---     ---",
    3: "-o-     - -",
}

LINE_NAMES = {
    0: "changing yin",
    1: "static yin",
    2: "static yang",
    3: "changing yang",
}

NUM_LINES = 6

# Which line index (0 = bottom) drives what.
KEY_LINE = 0
MODE_LINE = 1
FIRST_SLOT_LINE = 2   # slot 1; anchored, so this line moves nothing

CIRCLE_OF_FIFTHS = [theory.Note_Dict[(i * FIFTH_SEMITONES) % 12] for i in range(12)]


def flip(rng: random.Random | None = None) -> int:
    """One coin: 0 or 1."""
    source = rng if rng is not None else random
    return source.randint(0, 1)


def toss(rng: random.Random | None = None) -> int:
    """Three coins summed: 0-3, weighted 1/8, 3/8, 3/8, 1/8."""
    return flip(rng) + flip(rng) + flip(rng)


def cast_lines(rng: random.Random | None = None,
               count: int = NUM_LINES) -> list[int]:
    """A hexagram as six line values, bottom line first.

    `count` exists for casts that aren't hexagrams: the acid sequencer
    casts one line per 16th-note step (see playback._generate_acid_pattern),
    which is the same coin-toss weighting spent on a longer figure. Six
    stays the default, so every caller that wants a hexagram gets one."""
    return [toss(rng) for _ in range(count)]


def hexagram_text(lines: list[int]) -> str:
    """The six lines drawn the way the original script drew them: top line
    first, since a hexagram is built from the bottom up but read from the
    top down."""
    return "\n".join(LINE_GLYPHS[value] for value in reversed(lines))


def fifths_position(key: str) -> int:
    """How many fifths above C `key` sits, 0-11."""
    semitones = theory.note_to_midi_int(key) % 12
    return CIRCLE_OF_FIFTHS.index(theory.Note_Dict[semitones])


def shift_key(key: str, steps: int) -> str:
    """`key` moved `steps` positions around the circle of fifths. Positive
    is the dominant direction (C -> G), negative the subdominant (C -> F)."""
    semitones = theory.note_to_midi_int(key) + steps * FIFTH_SEMITONES
    return theory.Note_Dict[semitones % 12]


def shift_degree(index: int, steps: int) -> int:
    """A scale-degree index moved `steps` diatonic fifths. A fifth up from
    degree 1 is degree 5, i.e. +4 of the seven degrees, so that -- not +1
    -- is one step. An index at the excluded octave tonic (7) is folded
    back onto the tonic before moving."""
    base = index % DEGREES_PER_SCALE
    fifth_in_degrees = steps * 4
    return (base + fifth_in_degrees) % DEGREES_PER_SCALE


def mode_families() -> list[list[str]]:
    """theory.Modes grouped into plain/seventh pairs: [['Major', 'Major 7'],
    ['Minor', 'Minor 7'], ...]. Derived by pairing rather than by parsing
    names, because the names aren't consistent about it ('Major 7' has a
    space, 'Byzintine7' doesn't). A trailing unpaired mode, if one were
    ever added, becomes a family of its own rather than being dropped."""
    modes = theory.Modes
    return [list(modes[i:i + 2]) for i in range(0, len(modes), 2)]


def shift_mode(mode: str, line: int) -> str:
    """The mode a tossed `line` moves `mode` to.

    Fifths mean nothing here, so this reads the line's I Ching sense
    directly instead: a static line stays inside the current sound (either
    untouched, or trading the triad for its seventh), and only a changing
    line crosses into another family. So the scale holds still 3/8 of the
    time, which keeps a roll from scrambling everything at once."""
    families = mode_families()
    for family_index, family in enumerate(families):
        if mode in family:
            break
    else:
        return mode

    seventh = family.index(mode)
    if line == 1:                               # static yin: unchanged
        return mode
    if line == 2:                               # static yang: triad <-> 7th
        return family[(seventh + 1) % len(family)]

    direction = 1 if line == 3 else -1          # changing: next/prev family
    target = families[(family_index + direction) % len(families)]
    return target[min(seventh, len(target) - 1)]


# -- naming the cast -----------------------------------------------------
#
# A line's value says both what it is now and what it turns into: 0 and 1
# are yin, 2 and 3 are yang, and the two *changing* values (0 and 3) flip
# when the hexagram transforms. That is exactly the two columns Tyler's
# iChing script already drew, so nothing new is being invented here -- the
# glyphs in LINE_GLYPHS and the polarity functions below are two readings
# of the same numbers.

YANG_VALUES = frozenset({2, 3})
CHANGING_VALUES = frozenset({0, 3})


def is_yang(value: int) -> bool:
    """Whether a line value is yang (solid) rather than yin (broken)."""
    return value in YANG_VALUES


def is_changing(value: int) -> bool:
    """Whether a line transforms -- the rare 1/8 draws at either end."""
    return value in CHANGING_VALUES


def transformed_polarity(value: int) -> bool:
    """The line's polarity *after* the hexagram changes: a changing line
    flips, a static one stays."""
    return not is_yang(value) if is_changing(value) else is_yang(value)


@dataclass(frozen=True)
class Trigram:
    """One of the eight three-line figures."""
    glyph: str
    name: str      # pinyin, as the I Ching names it
    image: str     # the natural image it stands for


# Keyed by (bottom, middle, top), True = yang. Trigrams are built from the
# bottom up, which is why the key reads in that order rather than the order
# the glyph is drawn in.
TRIGRAMS = {
    (True,  True,  True):  Trigram("\u2630", "Qi\u00e1n", "Heaven"),
    (True,  True,  False): Trigram("\u2631", "Du\u00ec", "Lake"),
    (True,  False, True):  Trigram("\u2632", "L\u00ed", "Fire"),
    (True,  False, False): Trigram("\u2633", "Zh\u00e8n", "Thunder"),
    (False, True,  True):  Trigram("\u2634", "X\u00f9n", "Wind"),
    (False, True,  False): Trigram("\u2635", "K\u01cen", "Water"),
    (False, False, True):  Trigram("\u2636", "G\u00e8n", "Mountain"),
    (False, False, False): Trigram("\u2637", "K\u016bn", "Earth"),
}

# The King Wen sequence as the standard 8x8 table: rows are the lower
# trigram, columns the upper one. Written out rather than computed because
# the sequence is traditional and has no closed form -- test_oracle.py
# checks it is a genuine permutation of 1..64 and spot-checks the pairs
# everyone knows (1 Heaven/Heaven, 2 Earth/Earth, 63/64 the two
# Fire-and-Water crossings).
_KING_WEN_ORDER = ["Qi\u00e1n", "Zh\u00e8n", "K\u01cen", "G\u00e8n",
                   "K\u016bn", "X\u00f9n", "L\u00ed", "Du\u00ec"]
_KING_WEN_TABLE = [
    # upper:  Qian Zhen  Kan  Gen  Kun  Xun   Li  Dui      # lower
    [            1,  34,   5,  26,  11,   9,  14,  43],    # Qian
    [           25,  51,   3,  27,  24,  42,  21,  17],    # Zhen
    [            6,  40,  29,   4,   7,  59,  64,  47],    # Kan
    [           33,  62,  39,  52,  15,  53,  56,  31],    # Gen
    [           12,  16,   8,  23,   2,  20,  35,  45],    # Kun
    [           44,  32,  48,  18,  46,  57,  50,  28],    # Xun
    [           13,  55,  63,  22,  36,  37,  30,  49],    # Li
    [           10,  54,  60,  41,  19,  61,  38,  58],    # Dui
]

HEXAGRAM_NAMES = {
    1: ("Qi\u00e1n", "The Creative"),
    2: ("K\u016bn", "The Receptive"),
    3: ("Zh\u016bn", "Difficulty at the Beginning"),
    4: ("M\u00e9ng", "Youthful Folly"),
    5: ("X\u016b", "Waiting"),
    6: ("S\u00f2ng", "Conflict"),
    7: ("Sh\u012b", "The Army"),
    8: ("B\u01d0", "Holding Together"),
    9: ("Xi\u01ceo Ch\u00f9", "The Taming Power of the Small"),
    10: ("L\u01da", "Treading"),
    11: ("T\u00e0i", "Peace"),
    12: ("P\u01d0", "Standstill"),
    13: ("T\u00f3ng R\u00e9n", "Fellowship with Men"),
    14: ("D\u00e0 Y\u01d2u", "Possession in Great Measure"),
    15: ("Qi\u0101n", "Modesty"),
    16: ("Y\u00f9", "Enthusiasm"),
    17: ("Su\u00ed", "Following"),
    18: ("G\u01d4", "Work on What Has Been Spoiled"),
    19: ("L\u00edn", "Approach"),
    20: ("Gu\u0101n", "Contemplation"),
    21: ("Sh\u00ec K\u00e8", "Biting Through"),
    22: ("B\u00ec", "Grace"),
    23: ("B\u014d", "Splitting Apart"),
    24: ("F\u00f9", "Return"),
    25: ("W\u00fa W\u00e0ng", "Innocence"),
    26: ("D\u00e0 Ch\u00f9", "The Taming Power of the Great"),
    27: ("Y\u00ed", "The Corners of the Mouth"),
    28: ("D\u00e0 Gu\u00f2", "Preponderance of the Great"),
    29: ("K\u01cen", "The Abysmal (Water)"),
    30: ("L\u00ed", "The Clinging (Fire)"),
    31: ("Xi\u00e1n", "Influence"),
    32: ("H\u00e9ng", "Duration"),
    33: ("D\u00f9n", "Retreat"),
    34: ("D\u00e0 Zhu\u00e0ng", "The Power of the Great"),
    35: ("J\u00ecn", "Progress"),
    36: ("M\u00edng Y\u00ed", "Darkening of the Light"),
    37: ("Ji\u0101 R\u00e9n", "The Family"),
    38: ("Ku\u00ed", "Opposition"),
    39: ("Ji\u01cen", "Obstruction"),
    40: ("Xi\u00e8", "Deliverance"),
    41: ("S\u01d4n", "Decrease"),
    42: ("Y\u00ec", "Increase"),
    43: ("Gu\u00e0i", "Break-through"),
    44: ("G\u00f2u", "Coming to Meet"),
    45: ("Cu\u00ec", "Gathering Together"),
    46: ("Sh\u0113ng", "Pushing Upward"),
    47: ("K\u00f9n", "Oppression"),
    48: ("J\u01d0ng", "The Well"),
    49: ("G\u00e9", "Revolution"),
    50: ("D\u01d0ng", "The Cauldron"),
    51: ("Zh\u00e8n", "The Arousing (Thunder)"),
    52: ("G\u00e8n", "Keeping Still (Mountain)"),
    53: ("Ji\u00e0n", "Development"),
    54: ("Gu\u012b M\u00e8i", "The Marrying Maiden"),
    55: ("F\u0113ng", "Abundance"),
    56: ("L\u01da", "The Wanderer"),
    57: ("X\u00f9n", "The Gentle (Wind)"),
    58: ("Du\u00ec", "The Joyous (Lake)"),
    59: ("Hu\u00e0n", "Dispersion"),
    60: ("Ji\u00e9", "Limitation"),
    61: ("Zh\u014dng F\u00fa", "Inner Truth"),
    62: ("Xi\u01ceo Gu\u00f2", "Preponderance of the Small"),
    63: ("J\u00ec J\u00ec", "After Completion"),
    64: ("W\u00e8i J\u00ec", "Before Completion"),
}

# The Unicode hexagram block runs U+4DC0..U+4DFF in King Wen order, so the
# glyph comes straight off the number.
HEXAGRAM_GLYPH_BASE = 0x4DC0


@dataclass(frozen=True)
class Hexagram:
    """A named cast: the six lines as the I Ching reads them."""
    number: int
    name: str
    meaning: str
    glyph: str
    lower: Trigram
    upper: Trigram

    def label(self) -> str:
        """One line: "\u4dc1 2 \u00b7 K\u016bn \u2014 The Receptive"."""
        return f"{self.glyph}  {self.number} \u00b7 {self.name} \u2014 {self.meaning}"

    def trigram_label(self) -> str:
        """The pair that composes it, named. Spoken upper-over-lower, the
        way a hexagram is described."""
        return (f"{self.upper.glyph} {self.upper.name} ({self.upper.image}) over "
                f"{self.lower.glyph} {self.lower.name} ({self.lower.image})")


def trigram_for(polarities: list[bool]) -> Trigram:
    """The trigram for three polarities, bottom line first."""
    bottom, middle, top = polarities
    return TRIGRAMS[(bottom, middle, top)]


def _king_wen_number(lower: Trigram, upper: Trigram) -> int:
    return _KING_WEN_TABLE[_KING_WEN_ORDER.index(lower.name)][
        _KING_WEN_ORDER.index(upper.name)]


def hexagram_for(polarities: list[bool]) -> Hexagram:
    """The named hexagram for six polarities, bottom line first."""
    lower = trigram_for(polarities[:3])
    upper = trigram_for(polarities[3:])
    number = _king_wen_number(lower, upper)
    name, meaning = HEXAGRAM_NAMES[number]
    return Hexagram(number=number, name=name, meaning=meaning,
                    glyph=chr(HEXAGRAM_GLYPH_BASE + number - 1),
                    lower=lower, upper=upper)


@dataclass(frozen=True)
class Cast:
    """One roll's result: what to select, and the reading behind it."""
    key: str
    mode: str
    slots: list[int]
    lines: list[int]

    @property
    def drawing(self) -> str:
        """The six lines as the original iChing script drew them."""
        return hexagram_text(self.lines)

    @property
    def polarities(self) -> list[bool]:
        """Each line as yang (True) or yin (False), bottom first."""
        return [is_yang(value) for value in self.lines]

    @property
    def hexagram(self) -> Hexagram:
        """The cast named: its number, name and two trigrams."""
        return hexagram_for(self.polarities)

    @property
    def transformation(self) -> "Hexagram | None":
        """The hexagram this one changes into, or None if no line was a
        changing one. This is the right-hand column the original script
        already drew -- the coin-toss method produces it for free, and it
        is the half of a reading that says where things are heading."""
        if not any(is_changing(value) for value in self.lines):
            return None
        return hexagram_for([transformed_polarity(v) for v in self.lines])

    def reading(self) -> str:
        """The I Ching side of the cast, two lines, for the GUI label."""
        hexagram = self.hexagram
        return f"{hexagram.label()}\n{hexagram.trigram_label()}"

    def describe(self) -> str:
        """The reading in words, for a tooltip or a log line."""
        moves = [
            f"key {LINE_NAMES[self.lines[KEY_LINE]]}: "
            f"{LINE_TO_FIFTHS[self.lines[KEY_LINE]]:+d} fifths -> {self.key}",
            f"scale {LINE_NAMES[self.lines[MODE_LINE]]} -> {self.mode}",
        ]
        for i, degree in enumerate(self.slots):
            line = self.lines[FIRST_SLOT_LINE + i]
            if i == 0:
                moves.append(f"slot 1 {LINE_NAMES[line]}: "
                             f"anchored -> degree {degree + 1} (tonic)")
            else:
                moves.append(f"slot {i + 1} {LINE_NAMES[line]}: "
                             f"{LINE_TO_FIFTHS[line]:+d} fifths -> degree {degree + 1}")
        return "\n".join(moves)


def roll(key: str, mode: str, slot_indices: list[int],
         rng: random.Random | None = None) -> Cast:
    """Casts a hexagram and reads it as a transition away from the current
    selections. Every result is relative to what's selected now, so
    repeated rolls wander through fifths-related territory instead of
    teleporting somewhere unrelated each time.

    `slot_indices` are scale-degree indices, one per progression slot.
    Slot 1 is set to the tonic rather than moved, so the progression
    always has a home to be heard as moving away from; slots 2-4 move by
    their own line. Any slot beyond the fourth is left as it is.
    """
    lines = cast_lines(rng)
    new_key = shift_key(key, LINE_TO_FIFTHS[lines[KEY_LINE]])
    new_mode = shift_mode(mode, lines[MODE_LINE])

    movable = NUM_LINES - FIRST_SLOT_LINE
    slots = list(slot_indices)
    for i in range(min(movable, len(slots))):
        if i == 0:
            slots[i] = ANCHOR_DEGREE
        else:
            slots[i] = shift_degree(slots[i], LINE_TO_FIFTHS[lines[FIRST_SLOT_LINE + i]])
    return Cast(key=new_key, mode=new_mode, slots=slots, lines=lines)
