from kivy.app import App
from kivy.uix.dropdown import DropDown
from kivy.uix.gridlayout import GridLayout
from kivy.uix.actionbar import ActionBar
from kivy.properties import ObjectProperty
from threading import Thread
import time
import random
import rtmidi
import timeit
from collections import deque  
from rtmidi.midiutil import open_midiinput

"""Opens midi port"""

midi_out = rtmidi.MidiOut()
midi_in = rtmidi.MidiIn()
available_ports = midi_out.get_ports()
input_ports = midi_in.get_ports()

if available_ports:
    midi_out.open_port(0)
    # midi_in.open_port(0)
    # midi_in.open_port(0)
else:
    midi_out.open_virtual_port("My virtual output")

print('Available Ports: ', available_ports)
print('Selected Out Ports: ', midi_out.get_port_name(0))
print('Selected In Ports: ', midi_in.get_port_name(0))

midi_channel = 0
# midi_note_on = 0x9
# midi_note_off = 0x8
midi_channel_one_on = 0x90  # fourth digit represents channel number
midi_channel_one_off = 0x80  # (str(midi_note_off) + str(midi_channel))
midi_channel_two_on = 0x91  # fourth digit represents channel number
midi_channel_two_off = 0x81  # (str(midi_note_off) + str(midi_channel))
midi_channel_three_on = 0x9B  # fourth digit represents channel number

# print(midi_channel_one_on)

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

"""Mode Dictionaries"""

major_intervals = {"I": 0,
                   "ii": 2,
                   "iii": 2,
                   "IV": 1,
                   "V": 2,
                   "vi": 2,
                   "vii°": 2,
                   "2I": 1
                   }  # Option,Shift,8 = °
minor_intervals = {"i": 0,
                   "ii°": 2,  # Option,Shift,8 = °
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

def snhtri():
    xlist = []
    for x in range(8):
        xlist.append(random.randint(1,2))
    # print(xlist)
    #print('trig')
    return xlist
        
    # print(xlist)
    # return xlist

seed = snhtri()

snh_intervals = {
                "I": 0,
                'ii': seed[1], # 
                'III': seed[2], # "III": seed()[2],
                'IV': seed[3], # "IV": snhtri()[3],
                'V': seed[4], # "V": snhtri()[4],
                'VI': seed[5], # "vi": snhtri()[5],
                'VII': seed[6], # "VII°": snhtri()[6],
                '2I': seed[7] # "2I": snhtri()[7]
                }
# print(seed)
# print('snh intervals', snh_intervals)


"""Chord types & augments"""

MAJOR_Chord = [0, +4, +3]
minor_Chord = [0, +3, +4]
dim_Chord = [0, +3, +3]
M7 = +4
m7 = +3
dim7 = +4


def determine_mode(key):
    # for mode in Modes:
    #     if mode in key.lower():
    #         return
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
    elif "b" in note:  # not working placeholder
        return Note_Dict.index(note[0]) - 1
    else:
        return Note_Dict.index(note[0])


def midi_int_to_note(digits):
    return Note_Dict[digits % 12]


def key_determine(key, interval):
    return key + interval


def to_midi_conversion(root, sel_mode):
    """Determines the midi integer of each note in selected scale"""
    curr_note = root
    # print(sel_mode)
    midi_list = []
    for interval in sel_mode.values():
        note_numbers = key_determine(curr_note, interval)
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
    # print("Chords: " + str(Readable_Progression))


def root_mode_to_midi_chord(roots, backend_list, sel_key):
    """Determines midi notes of each note in selected
        scale from the note letter & mode"""

    func_list = []
    progression_index = 0
    for note_number in roots:
        func_item = []
        current_note = note_number
        determined_mode = backend_list[progression_index]
        # chord_determine = roots[progression_index]
        if determined_mode.isupper():
            for interval in MAJOR_Chord:
                current_note += interval
                func_item.append(current_note)
            if "7" in sel_key:
                func_item.append(current_note + M7)
            else:
                pass
        elif "°" in determined_mode:
            for interval in dim_Chord:
                current_note += interval
                func_item.append(current_note)
            if "7" in sel_key:
                func_item.append(current_note + dim7)
            else:
                pass
        elif determined_mode.islower():
            for interval in minor_Chord:
                current_note += interval
                func_item.append(current_note)
            if "7" in sel_key:
                func_item.append(current_note + m7)
        progression_index += 1
        func_list.append(func_item)
    # print('HEEERE', func_list)
    return func_list


def progression_gen(full_list, selection):
    temp_list = []
    for item in selection:
        temp_list.append(full_list[item])
    return temp_list


def selected_key(key_num):
    return (key_num.partition('\n')[0]).partition(' ')[0]


def selected_mode(key_num):
    return determine_mode((key_num.partition('\n')[0]).partition(' ')[2])


def selected_prog(key_num):
    temp_str = key_num.partition('\n')[2]
    return temp_str.split()


def selected_prog_int(sel_mode, sel_prog):
    temp_list = []
    for numeral in sel_prog:
        temp_list.append(list(sel_mode.keys()).index(numeral))
    return temp_list


# """ Initialized Variables"""

key_numerals = [] # 'D' + ' ' + 'maj7' + '\n' + 'i' + 'v' + 'VI' + 'iv'


class Settings(ActionBar):
    pass


class GUI(GridLayout):
    root_spinner = ObjectProperty(None)
    first_spinner = ObjectProperty(None)
    second_spinner = ObjectProperty(None)
    third_spinner = ObjectProperty(None)
    fourth_spinner = ObjectProperty(None)
    modeSelect = ObjectProperty(None)
    DropDown = DropDown()

    def __init__(self, **kwargs):
        super(GUI, self).__init__(**kwargs)

        # key = selected_key('E')
        # midikey = ((note_to_midi_int(key)) + 48)
        # midi_digit_roots = to_midi_conversion(midikey, minor_intervals)
        # backend_notenumeral = from_midi_conversion(midi_digit_roots,
        #                                                minor_intervals)
        # midi_progression = root_mode_to_midi_chord(midi_digit_roots,
        #                                                    backend_notenumeral,
        #                                                    'min7')

        """Row of chord buttons"""
        self.add_widget(ChordButtons())

        # self.add_widget(Bar())
        # self.add_widget(ActionBar())

    def get_key_numerals(self):
        # print('got key numerals')
        return self.ids.info.text

    def get_key(self):
        return self.ids.key_spinner.text

    def get_numerals(self):
        temp_list = list(selected_mode(self.ids.info.text).keys())
        # print(temp_list)
        return temp_list

    def update_numerals(self):
        self.ids.first_spinner.values = self.get_numerals()
        self.ids.second_spinner.values = self.get_numerals()
        self.ids.third_spinner.values = self.get_numerals()
        self.ids.fourth_spinner.values = self.get_numerals()
        print('Numerals Updated')

    # def on_select(self):
    #     self.first_spinner.text = self.get_numerals()[0]

    def get_mode(self):
        temp_list = self.ids.modeSelect.text
        return temp_list

    def get_bpm(self):
        return self.ids.bpm_input.text
        # return self.bpmm.text

    def get_loop_length(self):
        return int(self.ids.loop_length.text)

    ns = Note_Dict
    modes = Modes
    
    # key = []  # self.ids['key_spinner.text']

    # numerals = list(gypsy_intervals.keys())
    # print('Here: ', numerals)

    @staticmethod
    def play_seq():
        p = Thread(target=play_loop)
        p.start()

    @staticmethod
    def stop_seq():
        s = Thread(target=stop_loop)
        s.start()


class ChordButtons(GridLayout):

    def get_intervals(self):
        global gui
        print(gui.ids.info.text)
        # temp_list = GUI.get_numerals()
        # return temp_list


    def get_midi_ints(self):
        global midi_progression
        temp_list = midi_progression
        return temp_list

    @staticmethod
    def chord_button(chords, selection):
        b = Thread(target=chord(midi_message_gen(
            midi_channel_one_on, chords, selection)))
        b.start()

    @staticmethod
    def chord_off():
        s = Thread(target=midi_out.send_message([0xB0, 0x7B, 0]))
        s.start()


class mOODrApp(App):
    def build(self):
        global gui
        # Config.set('graphics', 'fullscreen', 1)
        self.load_kv('mOODr_Kivy_app.kv')
        gui = GUI()
        return gui


def timeit(method):
    def timed(*args, **kw):
        ts = time.time()
        result = method(*args, **kw)
        te = time.time()
        if 'log_time' in kw:
            name = kw.get('log_name', method.__name__.upper())
            kw['log_time'][name] = int((te - ts) * 1000)
        else:
            print('%r  %2.2f ms' % \
                  (method.__name__, (te - ts) * 1000))
        return result
    return timed


def random_velocity():
    """Generates random velocity values for each note"""
    return random.randint(72, 108)


# @timeit
def chord(message):
    for variable in message:
        midi_out.send_message(variable)
        # time.sleep(.1)


# @timeit
def midi_message_gen(state, midi_list, position):
    list_of_list = []
    for item in midi_list:
        single_list = []
        for i in item:
            single_list.append([state,
                                i+12,
                                random_velocity()])
        list_of_list.append(single_list)
    return list_of_list[position]


def bass_message_gen(state, midi_list, position):
    single_list = []
    for i in midi_list:
        single_list.append([state,
                            i-12,
                            127])
    return single_list[position]


def bpm_conversion(tempo):
    conv = format((1/(float(tempo)/60))*4, '.3f')
    return float(conv)






"""MIDI OUTPUT"""


# @timeit
def play_loop():
    global loop
    global bar
    global midi_progression

    try:
        init_time = time.time()
        loop = gui.get_loop_length()
        bar = 0
        gui_update = gui.get_key_numerals()
        key = selected_key(gui_update)
        # print('Key:', key)
        midikey = ((note_to_midi_int(key)) + 48)
        # print('Midi Key: ', midikey)
        mode = gui.get_mode()
        def mode_dict():
            return determine_mode(gui.get_mode())
        # print('Mode: ', mode_dict)
        numerals = selected_prog(gui_update)
        # print('Numerals: ', numerals)
        int_prog = selected_prog_int(mode_dict(), numerals)
        # print('Int Progression: ', int_prog)
        midi_digit_roots = to_midi_conversion(midikey, mode_dict())
        # print('here ', midi_digit_roots)
        backend_notenumeral = from_midi_conversion(midi_digit_roots,
                                                   mode_dict())
        # print(backend_notenumeral)
        midi_progression = root_mode_to_midi_chord(midi_digit_roots,
                                                   backend_notenumeral,
                                                   mode)
        midi_chord = [midi_progression[int_prog[0]],
                     midi_progression[int_prog[1]],
                     midi_progression[int_prog[2]],
                     midi_progression[int_prog[3]]]
        root_progression = midi_chord[0][0], midi_chord[1][0], \
                           midi_chord[2][0], midi_chord[3][0]
        clock = bpm_conversion(gui.get_bpm())
        start_loop = time.time()
        while loop >= 1:
            start_bar = time.time()
            print("Bar # ", bar + 1)

            # print(loop)

            # number = gui.get_variable()
            # print('HERE: ', number)

            chord_on = midi_message_gen(midi_channel_one_on, midi_chord, bar)
            bass_on = bass_message_gen(midi_channel_two_on, root_progression, bar)
            chords_off = midi_message_gen(midi_channel_one_off, midi_chord, bar)
            bass_off = bass_message_gen(midi_channel_two_off, root_progression, bar)
            # print(bass_on)
            """Circuit channel swap"""
            # chord_on = midi_message_gen(midi_channel_two_on, midi_chord, bar)
            # bass_on = bass_message_gen(midi_channel_one_on, root_progression, bar)
            # chords_off = midi_message_gen(midi_channel_two_off, midi_chord, bar)
            # bass_off = bass_message_gen(midi_channel_one_off, root_progression, bar)

            chord(chord_on)
            # print(chord_on)
            midi_out.send_message(bass_on)
            # print(bass_on)
            midi_out.send_message([midi_channel_three_on, 60, 90])

            # print(clock)
            time.sleep(clock)

            chord(chords_off)
            midi_out.send_message(bass_off)
            # midi_out.send_message([0xB0, 0x7B, 0])

            bar += 1
            loop -= 1

            end_bar = time.time()
            bar_time = end_bar-start_bar
            latency = clock - bar_time
            # print('Latency: ', latency)

            # print('Bar time: ', bar_time)
            clock = clock + latency/2
            # print('New Clock: ', clock)
            if loop == 0:
                gui_loop_update = gui.get_key_numerals()
                key = selected_key(gui_loop_update)
                # print(key)
                midikey = ((note_to_midi_int(key)) + 48)
                # print('Midi Key: ', midikey)
                mode = gui.get_mode()
                mode_dict = determine_mode(mode)
                # print('Mode: ', mode_dict)
                numerals = selected_prog(gui_loop_update)
                # print(numerals)
                int_prog = selected_prog_int(mode_dict, numerals)
                # print('Int Progression: ', int_prog)
                midi_digit_roots = to_midi_conversion(midikey, mode_dict)
                backend_notenumeral = from_midi_conversion(midi_digit_roots,
                                                           mode_dict)
                midi_progression = root_mode_to_midi_chord(midi_digit_roots,
                                                           backend_notenumeral,
                                                           mode)
                midi_chord = [midi_progression[int_prog[0]],
                              midi_progression[int_prog[1]],
                              midi_progression[int_prog[2]],
                              midi_progression[int_prog[3]]]
                root_progression = midi_chord[0][0], midi_chord[1][0], \
                                   midi_chord[2][0], midi_chord[3][0]
                clock = bpm_conversion(gui.get_bpm())
                loop += gui.get_loop_length()
                bar -= gui.get_loop_length()
                loop_reset = time.time()
                print('Loop time: ', loop_reset - start_loop)
    except KeyboardInterrupt:
        midi_out.send_message([0xB0, 0x7B, 0])
        midi_out.close_port()


def stop_loop():
    global loop
    global bar
    # chords_off = midi_message_gen(midi_channel_one_off, midi_chord, bar)
    # bass_off = bass_message_gen(midi_channel_two_off, root_progression, bar)
    # chord(chords_off)
    # midi_out.send_message(bass_off)
    midi_out.send_message([0xB0, 0x7B, 0])
    midi_out.send_message([0xB1, 0x7B, 0])
    loop = -1
    bar = 0
    time.sleep(int(gui.get_bpm()) + .3)

    # loop = -1


if __name__ == '__main__':
    import sys
    app = mOODrApp()
    app.run()
    # midiclock = Thread(sys.exit(main(sys.argv[1:]) or 0))
    # midiclock.start()

