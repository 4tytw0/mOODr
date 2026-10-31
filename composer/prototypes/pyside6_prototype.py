"""Phase 3 GUI prototype: PySide6/Qt.

Same widget set as prototypes/customtkinter_prototype.py (key/mode
dropdowns, BPM field, Play/Stop, 7 chord buttons) so the two can be
compared like-for-like. See ROADMAP.md Phase 3 for the comparison writeup.
"""

import sys
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared import build_engine, build_progression  # noqa: E402

from moodr import theory  # noqa: E402


class TickSignal(QObject):
    """Qt automatically marshals a signal emitted on a background thread
    into a queued call on the receiving (GUI-thread) slot -- no manual
    queue/polling plumbing needed, unlike Tkinter."""

    ticked = Signal(int)


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("m00Dr -- PySide6 prototype")

        self._tick_signal = TickSignal()
        self._tick_signal.ticked.connect(self._on_tick)
        self._output, self._clock, self._engine = build_engine()
        self._clock.add_tick_callback(self._tick_signal.ticked.emit)

        self._chords: list = []
        self._roots: list = []
        self._numerals: list = []

        self.key_box = QComboBox()
        self.key_box.addItems(theory.Note_Dict)
        self.key_box.currentTextChanged.connect(self._on_selection_change)

        self.mode_box = QComboBox()
        self.mode_box.addItems(theory.Modes)
        self.mode_box.currentTextChanged.connect(self._on_selection_change)

        self.bpm_edit = QLineEdit("80")

        play_button = QPushButton("Play")
        play_button.clicked.connect(self._on_play)
        stop_button = QPushButton("Stop")
        stop_button.clicked.connect(self._on_stop)

        top_row = QHBoxLayout()
        for widget in (self.key_box, self.mode_box, self.bpm_edit, play_button, stop_button):
            top_row.addWidget(widget)

        self.chord_buttons = []
        chord_row = QHBoxLayout()
        for i in range(7):
            button = QPushButton("-")
            button.clicked.connect(lambda checked=False, i=i: self._on_chord(i))
            chord_row.addWidget(button)
            self.chord_buttons.append(button)

        self.chord_status = QLabel("")
        self.tick_status = QLabel("ticks: 0")

        layout = QVBoxLayout(self)
        layout.addLayout(top_row)
        layout.addLayout(chord_row)
        layout.addWidget(self.chord_status)
        layout.addWidget(self.tick_status)

        self._on_selection_change()

    def _on_selection_change(self, _value=None):
        key, mode = self.key_box.currentText(), self.mode_box.currentText()
        self._chords, self._roots, self._numerals = build_progression(key, mode)
        for i, button in enumerate(self.chord_buttons):
            button.setText(self._numerals[i] if i < len(self._numerals) else "-")

    def _on_play(self):
        try:
            bpm = float(self.bpm_edit.text())
        except ValueError:
            return
        self._clock.bpm = bpm
        self._engine.load_progression(self._chords[:4], self._roots[:4])
        self._engine.start()

    def _on_stop(self):
        self._engine.stop()
        self._clock.stop()

    def _on_chord(self, index):
        if index >= len(self._chords):
            return
        root_note = self._chords[index][0]
        self.chord_status.setText(f"previewed chord {index + 1}: root midi {root_note}")

    def _on_tick(self, tick):
        self.tick_status.setText(f"ticks: {tick}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = Window()
    window.resize(520, 180)
    window.show()
    sys.exit(app.exec())
