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


def cast_lines(rng: random.Random | None = None) -> list[int]:
    """A hexagram as six line values, bottom line first."""
    return [toss(rng) for _ in range(NUM_LINES)]


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


@dataclass(frozen=True)
class Cast:
    """One roll's result: what to select, and the reading behind it."""
    key: str
    mode: str
    slots: list[int]
    lines: list[int]

    @property
    def hexagram(self) -> str:
        return hexagram_text(self.lines)

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
