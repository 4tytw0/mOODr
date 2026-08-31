"""Music-theory core: notes, modes, chord shapes, and MIDI conversions.

Extracted from archive/OLD mOODr_app.py with no dependency on Kivy, rtmidi,
or any GUI. Behavior is preserved as-is (including quirks of the original),
except snhtri()/make_snh_intervals() gained an optional rng parameter so
their randomness can be seeded for tests.
"""

import random

Note_Dict = ['C', 'C#', 'D', 'D#', 'E', 'F',
             'F#', 'G', 'G#', 'A', 'A#', 'B']

Modes = ['Major', 'Major 7', 'Minor', 'Minor 7', 'Byzintine', 'Byzintine7', 'snhtri', 'snhtri7']

progression_conversions = {1: 'I',
                            2: 'II',
                            3: 'III',
                            4: 'IV',
                            5: 'V',
                            6: 'VI',
                            7: 'VII'}

major_intervals = {"I": 0,
                    "ii": 2,
                    "iii": 2,
                    "IV": 1,
                    "V": 2,
                    "vi": 2,
                    "vii°": 2,
                    "2I": 1
                    }
minor_intervals = {"i": 0,
                    "ii°": 2,
                    "III": 1,
                    "iv": 2,
                    "v": 2,
                    "VI": 1,
                    "VII": 2,
                    "2i": 2
                    }
byzintine_intervals = {
                    "I": 0,
                    "ii": 2,
                    "III": 2,
                    "IV": 1,
                    "V": 1,
                    "vi": 2,
                    "VII°": 2,
                    "2I": 2
                    }


def snhtri(rng: random.Random | None = None) -> list[int]:
    """Generates 8 random 1-or-2 steps used to build snh_intervals."""
    source = rng if rng is not None else random
    return [source.randint(1, 2) for _ in range(8)]


def make_snh_intervals(rng: random.Random | None = None) -> dict[str, int]:
    """Builds an snh_intervals-shaped dict from a fresh snhtri() draw."""
    values = snhtri(rng)
    return {
        "I": 0,
        'ii': values[1],
        'III': values[2],
        'IV': values[3],
        'V': values[4],
        'VI': values[5],
        'VII': values[6],
        '2I': values[7]
    }


snh_intervals = make_snh_intervals()

MAJOR_Chord = [0, +4, +3]
minor_Chord = [0, +3, +4]
dim_Chord = [0, +3, +3]
M7 = +4
m7 = +3
dim7 = +4


def determine_mode(key):
    if "maj" in key.lower():
        return major_intervals
    elif "min" in key.lower():
        return minor_intervals
    elif 'byz' in key.lower():
        return byzintine_intervals
    elif 'snhtri' in key.lower():
        return snh_intervals


def prog_conv(digits):
    numerals = []
    for digit in digits:
        numerals.append(progression_conversions[digit])
    return numerals


def note_to_midi_int(note):
    """Uses first two items of a string to determine the midi integer"""
    if '#' in note:
        return Note_Dict.index(note[0]) + 1
    elif "b" in note:
        return Note_Dict.index(note[0]) - 1
    else:
        return Note_Dict.index(note[0])


def midi_int_to_note(digits):
    return Note_Dict[digits % 12]


def _key_determine(key, interval):
    return key + interval


def to_midi_conversion(root, sel_mode):
    """Determines the midi integer of each note in selected scale"""
    curr_note = root
    midi_list = []
    for interval in sel_mode.values():
        note_numbers = _key_determine(curr_note, interval)
        curr_note += interval
        midi_list.append(note_numbers)
    return midi_list


def from_midi_conversion(digits_list, mode):
    """Determines the letter & mode of each note in selected scale"""
    note_numeral_list = []
    progression_index = 0
    mode_keys = list(mode.keys())

    for note in digits_list:
        mode_progression = mode_keys[progression_index]
        letter_note = midi_int_to_note(note)
        if mode_progression.isupper():
            note_numeral_list.append(str(letter_note)
                                      + mode_keys[progression_index])
            progression_index += 1
        elif mode_progression.islower():
            note_numeral_list.append(str(letter_note.lower())
                                      + mode_keys[progression_index])
            progression_index += 1
    return note_numeral_list


def ui_conv(prog):
    """Converts the backend data to a user readable form"""
    readable_progression = []
    for note in prog:
        note_letter = note[0].upper()
        readable_progression.append(note_letter + note[1:])
    return readable_progression


def root_mode_to_midi_chord(roots, backend_list, sel_key):
    """Determines midi notes of each note in selected
        scale from the note letter & mode"""

    func_list = []
    progression_index = 0
    for note_number in roots:
        func_item = []
        current_note = note_number
        determined_mode = backend_list[progression_index]
        if determined_mode.isupper():
            for interval in MAJOR_Chord:
                current_note += interval
                func_item.append(current_note)
            if "7" in sel_key:
                func_item.append(current_note + M7)
        elif "°" in determined_mode:
            for interval in dim_Chord:
                current_note += interval
                func_item.append(current_note)
            if "7" in sel_key:
                func_item.append(current_note + dim7)
        elif determined_mode.islower():
            for interval in minor_Chord:
                current_note += interval
                func_item.append(current_note)
            if "7" in sel_key:
                func_item.append(current_note + m7)
        progression_index += 1
        func_list.append(func_item)
    return func_list
