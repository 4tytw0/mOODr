"""The real m00Dr GUI (Phase 4): PySide6, wired to the Phase 1 theory
module and the Phase 2 MidiClock/PlaybackEngine.

Replaces the OLD app's `key_spinner + mode + '\\n' + numerals` packed
label string (see archive/OLD mOODr_Kivy_app.kv's `info` Label and
selected_key/selected_mode/selected_prog) with real, directly-read widget
state -- there is no string to parse anywhere in this module.
"""

import os
import subprocess
import sys

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import midi_io, midi_status, theory
from .clock import MidiClock, MidiClockSlave
from .playback import ARP_RATE_TICKS, BASS_CHANNEL, CHORD_CHANNEL, PlaybackEngine

DEFAULT_KEY = "E"
DEFAULT_MODE = "Minor 7"
DEFAULT_BPM = "80"
LOOP_LENGTHS = ["1", "2", "3", "4"]
NUM_NUMERAL_SLOTS = 4
NUM_CHORD_BUTTONS = 7
OCTAVE_SHIFT_RANGE = (-1, 1)

# Settings identity for the M8 UI (m8c) path remembered by MidiStatusDialog. Per-user, not
# committed to the repo -- everyone who uses this points it at their own m8c install.
SETTINGS_ORG = "m00Dr"
SETTINGS_APP = "m00Dr"
M8_UI_PATH_SETTING = "m8_ui_path"


def _m8_ui_settings() -> QSettings:
    """A QSettings instance for the M8 UI path, forced onto IniFormat
    rather than each OS's native store (NSUserDefaults/CFPreferences on
    macOS, the registry on Windows). NativeFormat needs the process to be
    a properly registered app bundle to persist reliably -- a bare
    `uv run python main.py` process isn't one, and testing confirmed
    values written that way silently failed to read back (`defaults read`
    reported the domain not existing at all). A plain ini file under Qt's
    standard per-user config location sidesteps that platform machinery
    entirely and is trivial to inspect directly if something looks wrong
    again."""
    return QSettings(QSettings.IniFormat, QSettings.UserScope, SETTINGS_ORG, SETTINGS_APP)

# Display label -> PlaybackEngine.arp_pattern value. Dict order sets the
# dropdown's order, with the current default ("down") listed first.
ARP_PATTERN_LABELS = {
    "Down": "down",
    "Up": "up",
    "Up-Down": "up_down",
    "Random": "random",
}
DEFAULT_ARP_PATTERN_LABEL = "Down"
ARP_RATE_LABELS = list(ARP_RATE_TICKS.keys())  # "1/4", "1/8", "1/16"
DEFAULT_ARP_RATE = "1/8"

DEFAULT_ACID_NOISE_PERCENT = 25

# Novation Circuit Tracks' default MIDI map (confirmed against its own
# manual): Synth 1 = ch1, Synth 2 = ch2 are its only two actual synth
# voices; MIDI 1 = ch3, MIDI 2 = ch4 are pass-through-only tracks with no
# sound of their own; Drums 1-4 = ch10. m00Dr's own defaults already put
# chords on ch1 (Synth 1, correct), but bass defaults to ch3 (MIDI 1 --
# silent). This toggle leaves chords alone and moves bass to ch2 (Synth
# 2) instead, landing both on Circuit Tracks' only two synth voices.
# (Earlier version of this toggle moved both to ch3/ch4, which was
# backwards -- that's the *silent* pair, not the synth pair.)
CIRCUIT_CHORD_CHANNEL = CHORD_CHANNEL  # unchanged: ch1 (Synth 1) was already correct
CIRCUIT_BASS_CHANNEL = 1  # MIDI channel 2 (Synth 2)

DEFAULT_WINDOW_SIZE = (960, 560)
MINIMUM_WINDOW_SIZE = (720, 420)
CONTROL_HEIGHT = 36
PRIMARY_BUTTON_HEIGHT = 48
PRIMARY_BUTTON_MAX_HEIGHT = 72
CHORD_BUTTON_MIN_HEIGHT = 72
CHORD_BUTTON_MAX_HEIGHT = 120
CONTROL_POINT_SIZE = 11
PRIMARY_POINT_SIZE = 14


def _grow(widget, min_height: int = CONTROL_HEIGHT, point_size: int = CONTROL_POINT_SIZE,
          expanding: bool = False, max_height: int | None = None) -> None:
    """Applies the app's larger-button/larger-text sizing consistently.
    expanding=True (chord/transport buttons) lets a widget grow to fill
    extra window space rather than staying pinned at its minimum size;
    max_height caps how large that growth can get, so a big window doesn't
    turn the buttons into an oversized, disproportionate block."""
    widget.setMinimumHeight(min_height)
    if max_height is not None:
        widget.setMaximumHeight(max_height)
    font = widget.font()
    font.setPointSize(point_size)
    widget.setFont(font)
    if expanding:
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)


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


class EngineSignals(QObject):
    """Marshals PlaybackEngine/MidiClockSlave callbacks that touch GUI
    state (reading widget selections, calling engine.start()/stop()) from
    a background thread onto the GUI thread. Needed for on_loop_complete
    even in master-mode-only use, since MidiClock's tick callbacks -- and
    so PlaybackEngine._advance(), which calls on_loop_complete -- already
    run on MidiClock's own background thread, not the GUI thread."""

    looped = Signal()
    external_start = Signal()
    external_stop = Signal()


class MidiStatusDialog(QDialog):
    """A live view of the MIDI ports this app depends on, plus a button to
    reset the platform's MIDI service (CoreMIDI's MIDIServer on macOS) if
    a port has gone stale -- e.g. after the machine slept, or the MIDI
    device on the other end of a bridge was unplugged and replugged. Pure
    display plus a button; the actual reset/reopen logic lives in
    MainWindow._on_reset_midi_server (this dialog doesn't own m00Dr's own
    ports, just reads their names) and midi_status.py (the non-Qt reset
    call itself)."""

    def __init__(self, parent, own_output_name: str | None, own_input_name: str | None,
                 on_reset) -> None:
        super().__init__(parent)
        self.setWindowTitle("MIDI Status")
        self._own_output_name = own_output_name
        self._own_input_name = own_input_name
        self._on_reset = on_reset

        self.input_list = QListWidget()
        self.output_list = QListWidget()
        self.own_ports_label = QLabel()
        self.own_ports_label.setWordWrap(True)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)

        self.reset_button = QPushButton("Reset MIDI Server")
        self.reset_button.clicked.connect(self._on_reset_clicked)
        if midi_status.MIDI_SERVER_RESET_SUPPORTED:
            self.reset_button.setToolTip(
                "Kills and lets macOS restart CoreMIDI's MIDIServer process. This app's own "
                "MIDI port(s) are reopened automatically afterward. Any OTHER MIDI app or "
                "bridge (DAWs, hardware bridges, etc.) will need restarting separately -- a "
                "MIDIServer reset invalidates every virtual port open anywhere on the "
                "machine, not just this app's.")
        else:
            self.reset_button.setEnabled(False)
            self.reset_button.setToolTip(
                "Only supported on macOS (CoreMIDI's MIDIServer). Not available on this "
                "platform.")

        self.open_m8_ui_button = QPushButton("Open M8 UI")
        self.open_m8_ui_button.setToolTip(
            "Launches the M8 display client (m8c) for a Dirtywave M8 or M8 Headless. The "
            "first click asks you to locate its application/executable; that choice is "
            "remembered for next time.")
        self.open_m8_ui_button.clicked.connect(self._on_open_m8_ui_clicked)

        self.change_m8_ui_path_button = QPushButton("Change...")
        self.change_m8_ui_path_button.setToolTip(
            "Pick a different M8 UI (m8c) application/executable.")
        self.change_m8_ui_path_button.clicked.connect(self._on_change_m8_ui_path_clicked)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)

        for widget in (self.refresh_button, self.reset_button, self.open_m8_ui_button,
                       self.change_m8_ui_path_button, close_button):
            _grow(widget, min_height=PRIMARY_BUTTON_HEIGHT, point_size=PRIMARY_POINT_SIZE)
        _grow(self.own_ports_label)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Input ports:"))
        layout.addWidget(self.input_list)
        layout.addWidget(QLabel("Output ports:"))
        layout.addWidget(self.output_list)
        layout.addWidget(self.own_ports_label)
        service_row = QHBoxLayout()
        for widget in (self.refresh_button, self.reset_button):
            service_row.addWidget(widget)
        layout.addLayout(service_row)
        m8_ui_row = QHBoxLayout()
        for widget in (self.open_m8_ui_button, self.change_m8_ui_path_button):
            m8_ui_row.addWidget(widget)
        layout.addLayout(m8_ui_row)
        layout.addWidget(close_button)

        self.resize(460, 540)
        self.refresh()

    def refresh(self) -> None:
        inputs, outputs = midi_status.list_ports()
        self.input_list.clear()
        self.input_list.addItems(inputs)
        self.output_list.clear()
        self.output_list.addItems(outputs)

        parts = []
        if self._own_output_name:
            found = self._own_output_name in outputs
            parts.append(f'"{self._own_output_name}" (notes out): '
                         f'{"found" if found else "MISSING"}')
        if self._own_input_name:
            found = self._own_input_name in inputs
            parts.append(f'"{self._own_input_name}" (clock sync in): '
                         f'{"found" if found else "MISSING"}')
        self.own_ports_label.setText(" | ".join(parts))

    def _on_reset_clicked(self) -> None:
        self.reset_button.setEnabled(False)
        self.reset_button.setText("Resetting...")
        self._on_reset()
        # A brief pause before reopening/re-listing: MIDIServer's process
        # exit (SIGKILL) is immediate, but giving CoreMIDI a beat before the
        # very next call to it is cheap insurance against a tight race with
        # this same process's own in-flight client state.
        QTimer.singleShot(400, self._finish_reset)

    def _finish_reset(self) -> None:
        self.refresh()
        self.reset_button.setEnabled(midi_status.MIDI_SERVER_RESET_SUPPORTED)
        self.reset_button.setText("Reset MIDI Server")

    def _on_open_m8_ui_clicked(self) -> None:
        settings = _m8_ui_settings()
        path = settings.value(M8_UI_PATH_SETTING, "", str)
        if not path or not os.path.exists(path):
            path = self._prompt_for_m8_ui_path()
            if not path:
                return
            settings.setValue(M8_UI_PATH_SETTING, path)
            settings.sync()
        self._launch_m8_ui(path)

    def _on_change_m8_ui_path_clicked(self) -> None:
        path = self._prompt_for_m8_ui_path()
        if not path:
            return
        settings = _m8_ui_settings()
        settings.setValue(M8_UI_PATH_SETTING, path)
        settings.sync()
        self._launch_m8_ui(path)

    def _prompt_for_m8_ui_path(self) -> str:
        # QFileDialog treats a macOS .app bundle as a pickable "file" here,
        # not a directory to descend into, so this filter works on macOS
        # despite .app actually being a directory on disk. Native dialog
        # behavior around bundles isn't fully reliable across Qt/macOS
        # versions though -- a user can still end up selecting something
        # *inside* the bundle (or a plain folder) instead of the bundle
        # itself, so the result is normalized below rather than trusted
        # as-is.
        file_filter = ("Applications (*.app);;All files (*)" if sys.platform == "darwin"
                        else "All files (*)")
        path, _ = QFileDialog.getOpenFileName(
            self, "Locate the M8 display client (m8c)", "", file_filter)
        if path:
            path = midi_status.resolve_app_bundle(path)
        return path

    def _launch_m8_ui(self, path: str) -> None:
        if path.endswith(".app"):
            if sys.platform != "darwin":
                QMessageBox.warning(self, "Couldn't open M8 UI",
                                     f"{path} looks like a macOS .app bundle, but this isn't "
                                     f"macOS -- pick the actual m8c executable instead.")
                return
            launch = ["open", path]
        elif os.path.isdir(path):
            # A plain folder (not a .app bundle) isn't launchable -- `open`
            # would silently open it in Finder instead of erroring, which
            # looks like "nothing happened" rather than a clear mistake.
            QMessageBox.warning(self, "Couldn't open M8 UI",
                                 f"{path} is a folder, not the m8c application/executable "
                                 f'itself. Use "Change..." and pick the actual app or binary '
                                 f"(on macOS, the m8c.app bundle).")
            return
        else:
            launch = [path]

        try:
            subprocess.Popen(launch)
        except OSError as exc:
            QMessageBox.warning(self, "Couldn't open M8 UI", f"Failed to launch {path}:\n{exc}")


class MainWindow(QWidget):
    def __init__(self, midi_output: midi_io.MidiOutput | None = None):
        super().__init__()
        self.setWindowTitle("m00Dr")

        self._midi_output = midi_output if midi_output is not None else midi_io.MidiOutput()
        if not self._midi_output.is_open:
            self._midi_output.open()

        self._engine_signals = EngineSignals()
        self._engine_signals.looped.connect(self._reload_progression)
        self._engine_signals.external_start.connect(self._on_play)
        self._engine_signals.external_stop.connect(self._on_stop)

        self._master_clock = MidiClock(self._midi_output)
        self._active_clock = self._master_clock
        # MIDI clock slave mode: following an external clock (e.g. Ableton
        # set as clock master) instead of generating one. Opened lazily,
        # only once "External clock sync" is actually checked.
        self._slave_input: midi_io.MidiInput | None = None
        self._slave_clock: MidiClockSlave | None = None

        self._engine = PlaybackEngine(self._midi_output, self._master_clock,
                                       on_loop_complete=self._engine_signals.looped.emit)

        self._full_chords: list[list[int]] = []
        self._full_roots: list[int] = []
        self._numerals: list[str] = []
        self._preview_octave_shift: dict[int, int] = {}
        self._preview_chords_enabled: dict[int, bool] = {}
        self._preview_bass_enabled: dict[int, bool] = {}
        self._preview_chord_channel: dict[int, int] = {}
        self._preview_bass_channel: dict[int, int] = {}

        self._build_widgets()
        self._on_mode_changed()

        # Lets the 1-7 number keys trigger chord previews (see key{Press,
        # Release}Event below) while a text field like bpm_edit doesn't have
        # focus and is intercepting keystrokes. Qt would otherwise hand
        # initial focus to the first tabbable child (bpm_edit) on show, so
        # claim it for the window itself instead.
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFocus()

        # Chord/transport buttons use QSizePolicy.Expanding (see _grow()) so
        # they grow to fill extra space -- this floor keeps the layout from
        # getting cramped if the window is shrunk instead.
        self.setMinimumSize(*MINIMUM_WINDOW_SIZE)

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
        self.bpm_edit.setAlignment(Qt.AlignCenter)

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

        self.octave_spinbox = QSpinBox()
        self.octave_spinbox.setRange(*OCTAVE_SHIFT_RANGE)
        self.octave_spinbox.setValue(0)
        self.octave_spinbox.setPrefix("Octave: ")
        self.octave_spinbox.valueChanged.connect(self._on_octave_shift_changed)

        self.chords_button = QPushButton("Chords")
        self.chords_button.setCheckable(True)
        self.chords_button.setChecked(True)
        self.chords_button.toggled.connect(self._on_chords_toggled)

        self.bass_button = QPushButton("Bass")
        self.bass_button.setCheckable(True)
        self.bass_button.setChecked(True)
        self.bass_button.toggled.connect(self._on_bass_toggled)

        self.stab_button = QPushButton("Stabs")
        self.stab_button.setCheckable(True)
        self.stab_button.setChecked(False)
        self.stab_button.setToolTip(
            "House-style chord stabs: retriggers the whole chord as a short punchy hit on "
            "the offbeat (4 per bar) instead of sustaining it continuously. Takes effect at "
            "the next bar boundary.")
        self.stab_button.toggled.connect(self._on_stab_toggled)

        self.circuit_channels_button = QPushButton("Bass→Ch2")
        self.circuit_channels_button.setCheckable(True)
        self.circuit_channels_button.setChecked(False)
        # NOTE: currently a no-op. BASS_CHANNEL's default was moved to channel 2 directly
        # (see playback.py) so chords/arp/bass/acid land on channels 1/3/2/5 without needing
        # this toggle, and CIRCUIT_CHORD_CHANNEL == CHORD_CHANNEL already. Left in place in
        # case a future Circuit-specific channel map diverges from the M8 default again.
        self.circuit_channels_button.setToolTip(
            "Currently has no effect: bass already defaults to channel 2 (chords ch1, arp "
            "ch3, bass ch2, acid ch5). Kept for a future Circuit-specific channel map that "
            "diverges from this default.")
        self.circuit_channels_button.toggled.connect(self._on_circuit_channels_toggled)

        self.arp_button = QPushButton("Arp")
        self.arp_button.setCheckable(True)
        self.arp_button.setChecked(True)
        self.arp_button.toggled.connect(self._on_arp_toggled)

        self.arp_pattern_box = QComboBox()
        self.arp_pattern_box.addItems(ARP_PATTERN_LABELS.keys())
        self.arp_pattern_box.setCurrentText(DEFAULT_ARP_PATTERN_LABEL)
        self.arp_pattern_box.currentTextChanged.connect(self._on_arp_pattern_changed)

        self.arp_rate_box = QComboBox()
        self.arp_rate_box.addItems(ARP_RATE_LABELS)
        self.arp_rate_box.setCurrentText(DEFAULT_ARP_RATE)
        self.arp_rate_box.currentTextChanged.connect(self._on_arp_rate_changed)

        self.acid_button = QPushButton("Acid")
        self.acid_button.setCheckable(True)
        self.acid_button.setChecked(True)
        self.acid_button.toggled.connect(self._on_acid_toggled)

        self.acid_noise_label = QLabel()
        self.acid_noise_slider = QSlider(Qt.Horizontal)
        self.acid_noise_slider.setRange(0, 100)
        self.acid_noise_slider.setValue(DEFAULT_ACID_NOISE_PERCENT)
        self.acid_noise_slider.setToolTip(
            "Chance per 16th-note step of a rest, and independently of a random pitch "
            "within the scale, instead of the bar's chord root.")
        self.acid_noise_slider.valueChanged.connect(self._on_acid_noise_changed)
        self._update_acid_noise_label(DEFAULT_ACID_NOISE_PERCENT)

        self.acid_wide_checkbox = QCheckBox("Wide deviation")
        self.acid_wide_checkbox.setToolTip(
            "Checked: a deviated step can be any note in the scale. Unchecked: deviated "
            "steps stay near the home note (within 2 scale degrees).")
        self.acid_wide_checkbox.setChecked(True)
        self.acid_wide_checkbox.toggled.connect(self._on_acid_wide_toggled)

        self.acid_randomize_button = QPushButton("Randomize")
        self.acid_randomize_button.clicked.connect(self._on_acid_randomize_clicked)

        self.external_sync_checkbox = QCheckBox("External clock sync")
        self.external_sync_checkbox.setToolTip(
            "Follow an external MIDI clock (e.g. Ableton set as clock master) instead of "
            "generating one. Enable \"Sync\" for the \"m00Dr In\" port in Ableton's "
            "Link/Tempo/MIDI preferences.")
        self.external_sync_checkbox.toggled.connect(self._on_sync_mode_toggled)

        self.midi_status_button = QPushButton("MIDI Status")
        self.midi_status_button.setToolTip(
            "View the MIDI ports this app depends on, and reset the platform's MIDI service "
            "if one has gone stale (e.g. after sleep, or a device was unplugged/replugged).")
        self.midi_status_button.clicked.connect(self._on_midi_status_clicked)
        # Defaults to off: MidiClockSlave never generates ticks on its own,
        # only reacting to pulses it receives -- defaulting this on with no
        # external clock master actually connected silently freezes the
        # whole transport (chords/bass/arp/acid/stabs all stop advancing).
        # Turn it on manually once a real clock master (e.g. Ableton) is
        # sending to "m00Dr In".

        # Regular controls: larger than Qt's cramped defaults, but not the
        # primary-action treatment Play/Stop and the chord buttons get below.
        for widget in (self.key_box, self.mode_box, self.bpm_edit, self.loop_length_box,
                       self.humanize_checkbox, self.octave_spinbox, self.arp_pattern_box,
                       self.arp_rate_box, self.acid_wide_checkbox, self.acid_noise_slider,
                       self.external_sync_checkbox):
            _grow(widget)

        for button in (play_button, stop_button):
            _grow(button, min_height=PRIMARY_BUTTON_HEIGHT, point_size=PRIMARY_POINT_SIZE,
                  expanding=True, max_height=PRIMARY_BUTTON_MAX_HEIGHT)
        # Bass/Arp/Acid sit alongside fixed-size checkboxes in their rows,
        # not alone in a row of their own like Play/Stop -- expanding=True
        # there would let one swallow all the row's leftover space (it did,
        # badly, for Bass originally). Still bigger/bolder than a checkbox,
        # just not stretchy.
        for button in (self.chords_button, self.bass_button, self.stab_button,
                       self.circuit_channels_button, self.arp_button,
                       self.acid_button, self.acid_randomize_button, self.midi_status_button):
            _grow(button, min_height=PRIMARY_BUTTON_HEIGHT, point_size=PRIMARY_POINT_SIZE)

        progression_row = QHBoxLayout()
        for widget in (self.key_box, self.mode_box):
            progression_row.addWidget(widget)

        self.numeral_boxes = [QComboBox() for _ in range(NUM_NUMERAL_SLOTS)]
        numeral_row = QHBoxLayout()
        for box in self.numeral_boxes:
            _grow(box)
            numeral_row.addWidget(box)

        transport_row = QHBoxLayout()
        for widget in (self.bpm_edit, self.loop_length_box, play_button, stop_button):
            transport_row.addWidget(widget)

        performance_row = QHBoxLayout()
        for widget in (self.humanize_checkbox, self.octave_spinbox, self.chords_button,
                       self.bass_button, self.stab_button, self.circuit_channels_button,
                       self.external_sync_checkbox, self.midi_status_button):
            performance_row.addWidget(widget)

        arp_row = QHBoxLayout()
        for widget in (self.arp_button, self.arp_pattern_box, self.arp_rate_box):
            arp_row.addWidget(widget)

        acid_row = QHBoxLayout()
        for widget in (self.acid_button, self.acid_noise_label, self.acid_noise_slider,
                       self.acid_wide_checkbox, self.acid_randomize_button):
            acid_row.addWidget(widget)

        self.chord_buttons: list[QPushButton] = []
        chord_row = QHBoxLayout()
        for i in range(NUM_CHORD_BUTTONS):
            button = QPushButton("-")
            _grow(button, min_height=CHORD_BUTTON_MIN_HEIGHT, point_size=PRIMARY_POINT_SIZE,
                  expanding=True, max_height=CHORD_BUTTON_MAX_HEIGHT)
            button.pressed.connect(lambda i=i: self._on_chord_pressed(i))
            button.released.connect(lambda i=i: self._on_chord_released(i))
            chord_row.addWidget(button)
            self.chord_buttons.append(button)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.addLayout(progression_row)
        layout.addLayout(numeral_row)
        layout.addLayout(transport_row)
        layout.addLayout(performance_row)
        layout.addLayout(arp_row)
        layout.addLayout(acid_row)
        layout.addLayout(chord_row, 1)  # chord buttons get first claim on extra window space

    # -- state -------------------------------------------------------------

    def _on_mode_changed(self, _value: str | None = None) -> None:
        key, mode = self.key_box.currentText(), self.mode_box.currentText()
        self._full_chords, self._full_roots, self._numerals = generate_full_scale(key, mode)
        self._engine.set_scale(self._full_roots)

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
        dropdowns live, matching the OLD app's loop-boundary GUI reread.
        Uses set_progression(), not load_progression(): this fires via a
        queued cross-thread signal, so it can arrive after the clock
        thread has already advanced past the boundary that triggered it --
        load_progression()'s reset() would then rewind playback position,
        replaying the first chord an extra time."""
        chords, roots = self._selected_progression()
        self._engine.set_progression(chords, roots)

    # -- actions -------------------------------------------------------------

    def _on_play(self) -> None:
        if self._active_clock is self._master_clock:
            try:
                bpm = float(self.bpm_edit.text())
            except ValueError:
                return
            self._master_clock.bpm = bpm
        chords, roots = self._selected_progression()
        self._engine.load_progression(chords, roots)
        self._engine.start()
        self.external_sync_checkbox.setEnabled(False)

    def _on_stop(self) -> None:
        self._engine.stop()
        if self._active_clock is self._master_clock:
            # A slave clock keeps listening through Stop, so a later
            # external Start can still be noticed and followed -- only the
            # master clock's own ticking thread needs to actually halt.
            self._master_clock.stop()
        self.external_sync_checkbox.setEnabled(True)

    def _on_humanize_toggled(self, checked: bool) -> None:
        self._engine.humanize_velocity = checked

    def _on_octave_shift_changed(self, value: int) -> None:
        self._engine.octave_shift = value

    def _on_chords_toggled(self, checked: bool) -> None:
        self._engine.chords_enabled = checked

    def _on_bass_toggled(self, checked: bool) -> None:
        self._engine.bass_enabled = checked

    def _on_stab_toggled(self, checked: bool) -> None:
        self._engine.stab_enabled = checked

    def _on_circuit_channels_toggled(self, checked: bool) -> None:
        self._engine.chord_channel = CIRCUIT_CHORD_CHANNEL if checked else CHORD_CHANNEL
        self._engine.bass_channel = CIRCUIT_BASS_CHANNEL if checked else BASS_CHANNEL

    def _on_arp_toggled(self, checked: bool) -> None:
        self._engine.arp_enabled = checked

    def _on_arp_pattern_changed(self, label: str) -> None:
        self._engine.arp_pattern = ARP_PATTERN_LABELS[label]

    def _on_arp_rate_changed(self, rate: str) -> None:
        self._engine.arp_rate = rate

    def _on_acid_toggled(self, checked: bool) -> None:
        self._engine.acid_enabled = checked

    def _on_acid_noise_changed(self, value: int) -> None:
        self._engine.acid_noise = value / 100
        self._update_acid_noise_label(value)

    def _update_acid_noise_label(self, percent: int) -> None:
        self.acid_noise_label.setText(f"Noise: {percent}%")

    def _on_acid_wide_toggled(self, checked: bool) -> None:
        self._engine.acid_wide_deviation = checked

    def _on_acid_randomize_clicked(self) -> None:
        self._engine.randomize_acid_pattern()

    def _on_sync_mode_toggled(self, external: bool) -> None:
        """Switches PlaybackEngine between the internal master MidiClock
        and an external MidiClockSlave following a MIDI input named
        "m00Dr In" (created the same way MidiOutput creates "m00Dr" for
        note output). Only reachable while stopped -- the checkbox is
        disabled during playback, see _on_play()/_on_stop()."""
        if external:
            if self._slave_clock is None:
                self._slave_input = midi_io.MidiInput()
                self._slave_input.open()
                self._slave_clock = MidiClockSlave(
                    self._slave_input,
                    on_start=self._engine_signals.external_start.emit,
                    on_stop=self._engine_signals.external_stop.emit,
                    on_continue=self._engine_signals.external_start.emit,
                )
            new_clock = self._slave_clock
            # Starts listening immediately, independent of local Play/Stop,
            # so an external Start message can actually be noticed -- not
            # just once playback (which an external Start is meant to
            # trigger) has already begun.
            new_clock.start()
        else:
            if self._slave_clock is not None and self._slave_clock.is_running:
                self._slave_clock.stop()
            new_clock = self._master_clock

        self._engine.set_clock(new_clock)
        self._active_clock = new_clock
        self.bpm_edit.setEnabled(not external)

    def _on_midi_status_clicked(self) -> None:
        own_input_name = self._slave_input.port_name if self._slave_input is not None else None
        dialog = MidiStatusDialog(self, self._midi_output.port_name, own_input_name,
                                   self._on_reset_midi_server)
        dialog.exec()

    def _on_reset_midi_server(self) -> None:
        """Resets the platform MIDI service, then reopens this app's own
        ports on the fresh service -- a MIDIServer reset invalidates every
        virtual port already open in this process (m00Dr's own "m00Dr"
        output, and "m00Dr In" if external clock sync has ever been
        enabled), the same way it invalidates every other process's ports.
        This can't do anything about *other* MIDI apps/bridges; those need
        restarting separately (the MidiStatusDialog that calls this warns
        about that in its tooltip)."""
        midi_status.reset_midi_server()

        self._midi_output.close()
        self._midi_output.open()

        if self._slave_clock is not None:
            was_running = self._slave_clock.is_running
            if was_running:
                # stop()/start() (not touching _slave_input directly) so the
                # slave clock re-subscribes its callback to the freshly
                # reopened port -- close()+open() alone would silently drop
                # it, since cancel_callback()/set_callback() are what
                # start()/stop() actually call.
                self._slave_clock.stop()
            self._slave_input.close()
            self._slave_input.open()
            if was_running:
                self._slave_clock.start()

    def _on_chord_pressed(self, index: int) -> None:
        if index >= len(self._full_chords):
            return
        humanize = self.humanize_checkbox.isChecked()
        # Captured per-index so release always turns off the exact notes
        # press turned on, even if the octave spinbox changes in between
        # (see PlaybackEngine._sounding_octave_shift for the same reasoning).
        octave_shift = self.octave_spinbox.value()
        self._preview_octave_shift[index] = octave_shift
        chords_enabled = self.chords_button.isChecked()
        self._preview_chords_enabled[index] = chords_enabled
        bass_enabled = self.bass_button.isChecked()
        self._preview_bass_enabled[index] = bass_enabled
        # Read from the engine's live channel (respects the Ch 3/4
        # toggle), captured per-index for the same reason as octave_shift
        # above -- release must turn off on whatever channel press used.
        chord_channel = self._engine.chord_channel
        self._preview_chord_channel[index] = chord_channel
        bass_channel = self._engine.bass_channel
        self._preview_bass_channel[index] = bass_channel
        if chords_enabled:
            for message in midi_io.midi_message_gen(0x90 | chord_channel, self._full_chords,
                                                      index, humanize=humanize,
                                                      octave_shift=octave_shift):
                self._midi_output.send(message)
        if bass_enabled:
            self._midi_output.send(midi_io.bass_message_gen(
                0x90 | bass_channel, self._full_roots, index, octave_shift))

    def _on_chord_released(self, index: int) -> None:
        if index >= len(self._full_chords):
            return
        humanize = self.humanize_checkbox.isChecked()
        octave_shift = self._preview_octave_shift.pop(index, self.octave_spinbox.value())
        chords_enabled = self._preview_chords_enabled.pop(index, self.chords_button.isChecked())
        bass_enabled = self._preview_bass_enabled.pop(index, self.bass_button.isChecked())
        chord_channel = self._preview_chord_channel.pop(index, self._engine.chord_channel)
        bass_channel = self._preview_bass_channel.pop(index, self._engine.bass_channel)
        if chords_enabled:
            for message in midi_io.midi_message_gen(0x80 | chord_channel, self._full_chords,
                                                      index, humanize=humanize,
                                                      octave_shift=octave_shift):
                self._midi_output.send(message)
        if bass_enabled:
            self._midi_output.send(midi_io.bass_message_gen(
                0x80 | bass_channel, self._full_roots, index, octave_shift))

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
        self._active_clock.stop()
        self._midi_output.close()
        if self._slave_input is not None:
            self._slave_input.close()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.resize(*DEFAULT_WINDOW_SIZE)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
