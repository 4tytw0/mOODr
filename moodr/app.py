"""The real m00Dr GUI (Phase 4): PySide6, wired to the Phase 1 theory
module and the Phase 2 MidiClock/PlaybackEngine.

Replaces the OLD app's `key_spinner + mode + '\\n' + numerals` packed
label string (see archive/OLD mOODr_Kivy_app.kv's `info` Label and
selected_key/selected_mode/selected_prog) with real, directly-read widget
state -- there is no string to parse anywhere in this module.
"""

import sys

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import midi_io, theory
from .clock import MidiClock
from .playback import BASS_CHANNEL, CHORD_CHANNEL, PlaybackEngine

DEFAULT_KEY = "E"
DEFAULT_MODE = "Minor 7"
DEFAULT_BPM = "80"
LOOP_LENGTHS = ["1", "2", "3", "4"]
NUM_NUMERAL_SLOTS = 4
NUM_CHORD_BUTTONS = 7


def generate_full_scale(key: str, mode: str):
    """The full scale-degree chord list, MIDI roots, and numeral labels
    for a key/mode -- composed from the Phase 1 theory module's pure
    conversion functions."""
    mode_intervals = theory.determine_mode(mode)
    root = theory.note_to_midi_int(key) + 48
    midi_roots = theory.to_midi_conversion(root, mode_intervals)
    backend_notenumeral = theory.from_midi_conversion(midi_roots, mode_intervals)
    chords = theory.root_mode_to_midi_chord(midi_roots, backend_notenumeral, mode)
    numerals = list(mode_intervals.keys())
    return chords, midi_roots, numerals


class TickSignal(QObject):
    """Marshals MidiClock's background-thread tick callback onto the GUI
    thread via Qt's queued connections."""

    ticked = Signal(int)


class MainWindow(QWidget):
    def __init__(self, midi_output: midi_io.MidiOutput | None = None):
        super().__init__()
        self.setWindowTitle("m00Dr")

        self._midi_output = midi_output if midi_output is not None else midi_io.MidiOutput()
        if not self._midi_output.is_open:
            self._midi_output.open()

        self._clock = MidiClock(self._midi_output)
        self._engine = PlaybackEngine(self._midi_output, self._clock,
                                       on_loop_complete=self._reload_progression)

        self._tick_signal = TickSignal()
        self._tick_signal.ticked.connect(self._on_tick)
        self._clock.add_tick_callback(self._tick_signal.ticked.emit)

        self._full_chords: list[list[int]] = []
        self._full_roots: list[int] = []
        self._numerals: list[str] = []

        self._build_widgets()
        self._on_mode_changed()

        # Lets the 1-7 number keys trigger chord previews (see key{Press,
        # Release}Event below) while a text field like bpm_edit doesn't have
        # focus and is intercepting keystrokes.
        self.setFocusPolicy(Qt.StrongFocus)

    # -- widget construction --------------------------------------------

    def _build_widgets(self) -> None:
        self.key_box = QComboBox()
        self.key_box.addItems(theory.Note_Dict)
        self.key_box.setCurrentText(DEFAULT_KEY)
        self.key_box.currentTextChanged.connect(self._on_mode_changed)

        self.mode_box = QComboBox()
        self.mode_box.addItems(theory.Modes)
        self.mode_box.setCurrentText(DEFAULT_MODE)
        self.mode_box.currentTextChanged.connect(self._on_mode_changed)

        self.bpm_edit = QLineEdit(DEFAULT_BPM)

        self.loop_length_box = QComboBox()
        self.loop_length_box.addItems(LOOP_LENGTHS)
        self.loop_length_box.setCurrentText(LOOP_LENGTHS[-1])

        play_button = QPushButton("Play")
        play_button.clicked.connect(self._on_play)
        stop_button = QPushButton("Stop")
        stop_button.clicked.connect(self._on_stop)

        self.humanize_checkbox = QCheckBox("Humanize velocity")
        self.humanize_checkbox.setChecked(True)
        self.humanize_checkbox.toggled.connect(self._on_humanize_toggled)

        top_row = QHBoxLayout()
        for widget in (self.key_box, self.mode_box, self.bpm_edit,
                       self.loop_length_box, play_button, stop_button,
                       self.humanize_checkbox):
            top_row.addWidget(widget)

        self.numeral_boxes = [QComboBox() for _ in range(NUM_NUMERAL_SLOTS)]
        numeral_row = QHBoxLayout()
        for box in self.numeral_boxes:
            numeral_row.addWidget(box)

        self.chord_buttons: list[QPushButton] = []
        chord_row = QHBoxLayout()
        for i in range(NUM_CHORD_BUTTONS):
            button = QPushButton("-")
            button.pressed.connect(lambda i=i: self._on_chord_pressed(i))
            button.released.connect(lambda i=i: self._on_chord_released(i))
            chord_row.addWidget(button)
            self.chord_buttons.append(button)

        self.status_label = QLabel("")
        self.tick_label = QLabel("ticks: 0")

        layout = QVBoxLayout(self)
        layout.addLayout(top_row)
        layout.addLayout(numeral_row)
        layout.addLayout(chord_row)
        layout.addWidget(self.status_label)
        layout.addWidget(self.tick_label)

    # -- state -------------------------------------------------------------

    def _on_mode_changed(self, _value: str | None = None) -> None:
        key, mode = self.key_box.currentText(), self.mode_box.currentText()
        self._full_chords, self._full_roots, self._numerals = generate_full_scale(key, mode)

        for box in self.numeral_boxes:
            box.blockSignals(True)
            box.clear()
            box.addItems(self._numerals)
            box.blockSignals(False)
        for i, box in enumerate(self.numeral_boxes):
            if i < len(self._numerals):
                box.setCurrentIndex(i)

        for i, button in enumerate(self.chord_buttons):
            button.setText(self._numerals[i] if i < len(self._numerals) else "-")

    def _selected_progression(self) -> tuple[list[list[int]], list[int]]:
        """The chords/roots currently chosen by the numeral dropdowns,
        sliced to the loop length. This -- reading widget state directly
        -- is the real-state replacement for the OLD app's packed label
        string parsing."""
        loop_length = int(self.loop_length_box.currentText())
        indices = [self._numerals.index(box.currentText()) for box in self.numeral_boxes]
        chords = [self._full_chords[i] for i in indices][:loop_length]
        roots = [self._full_roots[i] for i in indices][:loop_length]
        return chords, roots

    def _reload_progression(self) -> None:
        """Called by PlaybackEngine right as its loaded progression wraps
        back to its first chord -- re-reads the numeral/loop-length
        dropdowns live, matching the OLD app's loop-boundary GUI reread."""
        chords, roots = self._selected_progression()
        self._engine.load_progression(chords, roots)

    # -- actions -------------------------------------------------------------

    def _on_play(self) -> None:
        try:
            bpm = float(self.bpm_edit.text())
        except ValueError:
            return
        self._clock.bpm = bpm
        chords, roots = self._selected_progression()
        self._engine.load_progression(chords, roots)
        self._engine.start()

    def _on_stop(self) -> None:
        self._engine.stop()
        self._clock.stop()

    def _on_humanize_toggled(self, checked: bool) -> None:
        self._engine.humanize_velocity = checked

    def _on_chord_pressed(self, index: int) -> None:
        if index >= len(self._full_chords):
            return
        humanize = self.humanize_checkbox.isChecked()
        for message in midi_io.midi_message_gen(0x90 | CHORD_CHANNEL, self._full_chords, index,
                                                  humanize=humanize):
            self._midi_output.send(message)
        self._midi_output.send(midi_io.bass_message_gen(0x90 | BASS_CHANNEL, self._full_roots, index))
        self.status_label.setText(f"previewing chord {self._numerals[index]}")

    def _on_chord_released(self, index: int) -> None:
        if index >= len(self._full_chords):
            return
        humanize = self.humanize_checkbox.isChecked()
        for message in midi_io.midi_message_gen(0x80 | CHORD_CHANNEL, self._full_chords, index,
                                                  humanize=humanize):
            self._midi_output.send(message)
        self._midi_output.send(midi_io.bass_message_gen(0x80 | BASS_CHANNEL, self._full_roots, index))

    def _on_tick(self, tick: int) -> None:
        self.tick_label.setText(f"ticks: {tick}")

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        index = self._chord_index_for_key(event.key())
        if index is not None and not event.isAutoRepeat():
            self.chord_buttons[index].setDown(True)
            self._on_chord_pressed(index)
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        index = self._chord_index_for_key(event.key())
        if index is not None and not event.isAutoRepeat():
            self.chord_buttons[index].setDown(False)
            self._on_chord_released(index)
            return
        super().keyReleaseEvent(event)

    @staticmethod
    def _chord_index_for_key(key: int) -> int | None:
        if Qt.Key_1 <= key <= Qt.Key_7:
            return key - Qt.Key_1
        return None

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._engine.stop()
        self._clock.stop()
        self._midi_output.close()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.resize(640, 260)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
