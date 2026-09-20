"""The real m00Dr GUI (Phase 4): PySide6, wired to the Phase 1 theory
module and the Phase 2 MidiClock/PlaybackEngine.

Replaces the OLD app's `key_spinner + mode + '\\n' + numerals` packed
label string (see archive/OLD mOODr_Kivy_app.kv's `info` Label and
selected_key/selected_mode/selected_prog) with real, directly-read widget
state -- there is no string to parse anywhere in this module.
"""

import html
import os
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGraphicsOpacityEffect,
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

from . import bridge_manager, midi_io, midi_status, oracle, theme, theory
from .clock import MidiClock, MidiClockSlave
from .playback import (ACID_STEPS, ARP_RATE_TICKS, BASS_CHANNEL, CHORD_CHANNEL,
                       PlaybackEngine, acid_note_for_step)

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

# How long to let CoreMIDI's MIDIServer actually respawn after being killed before this app
# tries to reopen its own ports on it -- racing this with no delay left the ports silently
# failing to come back in real use (see MainWindow._on_reset_midi_server).
RESET_SETTLE_SECONDS = 0.5

# How often MidiStatusDialog rescans the process table for bridges started
# outside this app. Slower than the 1s uptime-label tick because a scan
# shells out to `ps` and reads every process on the machine.
BRIDGE_SCAN_INTERVAL_MS = 3000


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

# The acid pattern is an I Ching cast (see playback._generate_acid_pattern)
# and this is how much of that cast is let through, so the default is all
# of it -- the reading as drawn. Pulling it down folds the rests, palette
# notes, octave jumps, accents and slides back toward a plain root; at 0
# the acid line is a straight unaccented 16th-note pulse on the root.
DEFAULT_ACID_NOISE_PERCENT = 100

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

CONTENT_MARGIN = 14
# The floor is the layout's own reported minimum (802x522 with the taller
# selectors) plus a little slack, rather than a guess. The previous 720x420
# was already smaller than the layout needed even before the selectors grew
# -- at 720 the performance row's "Bass->Ch2" and "MIDI Status" buttons were
# clipped -- and at the new selector height the rows overlapped outright.
MINIMUM_WINDOW_SIZE = (820, 540)
# Enough headroom above that floor for the chord pads to actually reach
# their CHORD_BUTTON_MAX_HEIGHT rather than sitting pinned at their minimum.
DEFAULT_WINDOW_SIZE = (1000, 680)
CONTROL_HEIGHT = 36
PRIMARY_BUTTON_HEIGHT = 48
PRIMARY_BUTTON_MAX_HEIGHT = 72
CHORD_BUTTON_MIN_HEIGHT = 84
CHORD_BUTTON_MAX_HEIGHT = 130
# How visible a progression slot the loop length doesn't reach stays. Dim
# enough to read as "not in play", not so dim it can't be pre-set.
INACTIVE_SLOT_OPACITY = 0.35
# The key/scale/progression dropdowns stretched the full window width at
# Qt's default 36px height -- a ~12:1 ribbon each. Capping their width and
# raising their height turns them into compact blocks instead. (The height
# only takes effect because theme.py styles QComboBox: macOS's native
# combo bezel ignores the height it's given.)
SELECTOR_HEIGHT = 72
SELECTOR_SPACING = 8
# Everything in a combo box that isn't the text: theme.py's 12px padding
# either side, its 22px drop-down, and a few px of slack.
COMBO_CHROME_WIDTH = 52
# Wide enough for three digits and a cursor, no wider.
BPM_FIELD_WIDTH = 96
# Separator between a numeral and its chord name in the progression
# dropdowns ("i · Em7").
NUMERAL_NAME_SEPARATOR = "  ·  "

NO_READING_TEXT = "no hexagram cast yet"

# A hexagram glyph is six hairline strokes and a trigram three, so at body
# size they are a smudge rather than a figure you can read the lines off.
# The label is rich text purely so the glyphs can be set large while their
# names stay at body size.
HEXAGRAM_GLYPH_SIZE = 30
TRIGRAM_GLYPH_SIZE = 20

READING_TOOLTIP = (
    "The I Ching reading behind the last roll. Roll again for a new one.")

ROLL_TOOLTIP = (
    "Roll a new key, scale and progression as a circle-of-fifths move away from the "
    "current ones, with the step sizes drawn by I Ching coin tosses -- usually one "
    "fifth, occasionally two. Every roll is relative to what is selected now, so "
    "repeated rolls wander through related keys rather than jumping at random. The "
    "first slot always holds the tonic, so the progression keeps a home to move away "
    "from.")
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


def _reading_html(cast: oracle.Cast) -> str:
    """The cast named, as the label's rich text: hexagram on the first
    line, the two trigrams that compose it on the second, spoken
    upper-over-lower the way a hexagram is described. The plain-text
    equivalent is oracle.Cast.reading(), which is what the tooltip and the
    tests read -- this function only adds presentation."""
    hexagram = cast.hexagram
    escape = html.escape

    def named(trigram, size):
        return (f'<span style="font-size:{size}px">{trigram.glyph}</span>&nbsp;'
                f'{escape(trigram.name)} ({escape(trigram.image)})')

    return (
        '<div style="line-height:130%">'
        f'<span style="font-size:{HEXAGRAM_GLYPH_SIZE}px">{hexagram.glyph}</span>'
        f'&nbsp;&nbsp;{hexagram.number} &middot; {escape(hexagram.name)}'
        f' &mdash; {escape(hexagram.meaning)}<br>'
        f'{named(hexagram.upper, TRIGRAM_GLYPH_SIZE)} over '
        f'{named(hexagram.lower, TRIGRAM_GLYPH_SIZE)}'
        '</div>')


def _row_width_limit(count: int, spacing: int = SELECTOR_SPACING) -> int:
    """The widest each of `count` fixed-width widgets can be and still fit
    a row at the *minimum* window size. Without this ceiling a fixed width
    chosen to fit the text would simply overflow a narrow window, since a
    fixed-width widget can't shrink."""
    usable = MINIMUM_WINDOW_SIZE[0] - 2 * CONTENT_MARGIN - (count - 1) * spacing
    return usable // count



def _fit_width(widget, texts, limit: int) -> int:
    """The width `widget` needs for the widest of `texts`, capped at
    `limit`. Measured rather than hardcoded so adding a mode, or changing
    how chord names are spelled, can't silently start eliding text."""
    metrics = QFontMetrics(widget.font())
    widest = max((metrics.horizontalAdvance(text) for text in texts), default=0)
    return min(widest + COMBO_CHROME_WIDTH, limit)


def generate_full_scale(key: str, mode: str):
    """The full scale-degree chord list, MIDI roots, numeral labels, and
    readable chord names for a key/mode -- composed from the Phase 1
    theory module's pure conversion functions."""
    mode_intervals = theory.determine_mode(mode)
    root = theory.note_to_midi_int(key) + 48
    midi_roots = theory.to_midi_conversion(root, mode_intervals)
    backend_notenumeral = theory.from_midi_conversion(midi_roots, mode_intervals)
    chords = theory.root_mode_to_midi_chord(midi_roots, backend_notenumeral, mode)
    numerals = list(mode_intervals.keys())
    names = theory.chord_names(midi_roots, backend_notenumeral, mode)
    return chords, midi_roots, numerals, names


def all_slot_labels() -> list[str]:
    """Every label a progression slot can ever show, across all keys and
    modes. The slots are sized against this whole set, not against the
    current key/mode, so that switching key or mode doesn't resize the row
    underneath the pointer."""
    labels = []
    for mode in theory.Modes:
        for key in theory.Note_Dict:
            _, _, numerals, names = generate_full_scale(key, mode)
            labels.extend(f"{numeral}{NUMERAL_NAME_SEPARATOR}{name}"
                          for numeral, name in zip(numerals, names))
    return labels


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
    # The progression index of the chord that just started sounding, or -1
    # for "nothing is sounding". -1 rather than None because Signal(int)
    # can't carry None, and an Optional signal type would buy nothing here.
    chord_changed = Signal(int)
    # The acid step just entered, or -1 for "nothing playing" -- same
    # reasoning as chord_changed for the -1 rather than None. Fires at a
    # 16th note, so its slot must stay cheap (see AcidLane).
    acid_step = Signal(int)


class MidiResetSignals(QObject):
    """Marshals MIDI-server-reset completion from a background thread onto
    the GUI thread. The reset + this app's own port reopen used to run
    directly on the GUI thread and could hang it indefinitely: confirmed in
    real use that a port open/close call can hang rather than just fail
    fast if CoreMIDI/MIDIServer doesn't respond, and since Qt's event loop
    is single-threaded, that froze the *entire* app, not just the dialog --
    Refresh, Close, even other windows stopped responding. Running the work
    on a background thread means a hang there just leaves the reset
    perpetually "in progress" instead of freezing anything else."""

    finished = Signal(bool)  # True = this app's own ports reopened OK


def _reopen_port(port, expected_name: str) -> bool:
    """Closes and reopens a MidiOutput/MidiInput, retrying once after a
    longer pause if the reopened port doesn't actually show up in a live
    port listing -- the same MIDIServer-not-back-yet race its caller is
    guarding against can still occasionally lose the first attempt.
    Returns whether it ended up open. Intended to run off the GUI thread
    (see MainWindow._reset_midi_server_worker) since any of these calls can
    itself hang rather than fail fast."""
    for attempt_delay in (0, RESET_SETTLE_SECONDS * 2):
        if attempt_delay:
            time.sleep(attempt_delay)
        port.close()
        port.open()
        inputs, outputs = midi_status.list_ports()
        if expected_name in inputs or expected_name in outputs:
            return True
    return False


class MidiStatusDialog(QDialog):
    """A live view of the MIDI ports this app depends on, plus a button to
    reset the platform's MIDI service (CoreMIDI's MIDIServer on macOS) if
    a port has gone stale -- e.g. after the machine slept, or the MIDI
    device on the other end of a bridge was unplugged and replugged. Pure
    display plus a button; the actual reset/reopen logic runs on a
    background thread owned by MainWindow (this dialog doesn't own m00Dr's
    own ports, just reads their names) and midi_status.py (the non-Qt
    reset call itself)."""

    def __init__(self, parent, own_output_name: str | None, own_input_name: str | None,
                 on_reset, reset_finished: Signal,
                 bridges: list[bridge_manager.ManagedBridge]) -> None:
        super().__init__(parent)
        self.setWindowTitle("MIDI Status")
        self._own_output_name = own_output_name
        self._own_input_name = own_input_name
        self._on_reset = on_reset
        self._bridges = bridges
        reset_finished.connect(self._on_reset_finished)

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
                "MIDI port(s) are reopened automatically afterward, and any Bridge below "
                "that's currently running is restarted too. Any OTHER MIDI app (DAWs, a "
                "bridge running in its own terminal instead of through this dialog, etc.) "
                "will need restarting separately, and may crash outright rather than just "
                "needing a reconnect -- a MIDIServer reset invalidates every virtual port "
                "open anywhere on the machine, not just this app's, and other MIDI "
                "libraries don't all handle that gracefully.")
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

        if self._bridges:
            layout.addWidget(QLabel("Bridges:"))
            self._bridge_status_labels: dict[str, QLabel] = {}
            self._bridge_buttons: dict[str, QPushButton] = {}
            for bridge in self._bridges:
                row = QHBoxLayout()
                name_label = QLabel(bridge.name)
                _grow(name_label)
                status_label = QLabel(bridge_manager.format_status(bridge))
                _grow(status_label)
                button = QPushButton()
                button.setToolTip(
                    "Restart covers bridges started in their own terminal too, not just "
                    "ones launched from here: m00Dr finds them in the process table, stops "
                    "them, and starts a fresh copy it owns. A bridge can survive a MIDI "
                    "server reset without crashing and still hold a dead connection, so "
                    "restarting it is often the actual fix.")
                _grow(button, min_height=PRIMARY_BUTTON_HEIGHT, point_size=PRIMARY_POINT_SIZE)
                button.clicked.connect(lambda _checked=False, b=bridge: self._on_bridge_clicked(b))
                self._bridge_status_labels[bridge.name] = status_label
                self._bridge_buttons[bridge.name] = button
                row.addWidget(name_label)
                row.addWidget(status_label, 1)
                row.addWidget(button)
                layout.addLayout(row)
            self._refresh_bridge_buttons()
            # Ticks the uptime text live without re-querying CoreMIDI every
            # second the way a full refresh() would -- this only reads the
            # bridges' already-known state.
            self._bridge_tick_timer = QTimer(self)
            self._bridge_tick_timer.timeout.connect(self._refresh_bridge_labels)
            self._bridge_tick_timer.start(1000)
            # Finding externally-started bridges means scanning the whole
            # process table (~30ms), so it runs on its own slower timer
            # rather than every second alongside the cheap label tick.
            self._bridge_scan_timer = QTimer(self)
            self._bridge_scan_timer.timeout.connect(self._rescan_bridges)
            self._bridge_scan_timer.start(BRIDGE_SCAN_INTERVAL_MS)

        layout.addWidget(close_button)

        self.resize(460, 620)
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
        if self._bridges:
            self._rescan_bridges()

    def _refresh_bridge_labels(self) -> None:
        for bridge in self._bridges:
            self._bridge_status_labels[bridge.name].setText(bridge_manager.format_status(bridge))

    def _rescan_bridges(self) -> None:
        """Re-reads which bridges are running anywhere on the machine, so
        one started (or quit) in a terminal shows up here without the
        dialog being reopened."""
        bridge_manager.refresh_all(self._bridges)
        self._refresh_bridge_labels()
        self._refresh_bridge_buttons()

    def _refresh_bridge_buttons(self) -> None:
        for bridge in self._bridges:
            self._bridge_buttons[bridge.name].setText("Restart" if bridge.is_running else "Start")

    def _on_bridge_clicked(self, bridge: bridge_manager.ManagedBridge) -> None:
        button = self._bridge_buttons[bridge.name]
        button.setEnabled(False)
        try:
            bridge.restart() if bridge.is_running else bridge.start()
        finally:
            button.setEnabled(True)
        self._refresh_bridge_labels()
        self._refresh_bridge_buttons()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if hasattr(self, "_bridge_tick_timer"):
            self._bridge_tick_timer.stop()
        super().closeEvent(event)

    def _on_reset_clicked(self) -> None:
        self.reset_button.setEnabled(False)
        self.reset_button.setText("Resetting...")
        self._on_reset()  # starts the background thread and returns immediately

    def _on_reset_finished(self, ok: bool) -> None:
        self.refresh()
        self._refresh_bridge_buttons()
        self.reset_button.setEnabled(midi_status.MIDI_SERVER_RESET_SUPPORTED)
        self.reset_button.setText("Reset MIDI Server")
        if not ok:
            QMessageBox.warning(
                self, "MIDI service reset",
                "MIDIServer was reset, but this app's own MIDI port(s) didn't come back up "
                "cleanly afterward. Please restart m00Dr.")

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


class ChordPad(QPushButton):
    """One of the seven chord-preview pads.

    A plain QPushButton can only show a single run of text in a single
    font, which isn't enough for the three things a pad needs to say at
    once: its keyboard shortcut, its roman numeral, and the actual chord
    it will play. So the text lives in three transparent-to-the-mouse
    QLabels laid out inside the button -- the button itself keeps all of
    its normal press/release behavior (including setDown() from the number
    -key shortcuts), and each line gets its own size and colour from the
    stylesheet in theme.py.
    """

    def __init__(self, key_hint: str) -> None:
        super().__init__()
        self.hint_label = QLabel(key_hint)
        self.hint_label.setObjectName("padHint")
        self.numeral_label = QLabel("-")
        self.numeral_label.setObjectName("padNumeral")
        self.name_label = QLabel("")
        self.name_label.setObjectName("padName")

        for label in (self.hint_label, self.numeral_label, self.name_label):
            label.setAlignment(Qt.AlignCenter)
            # Without this a click that happens to land on a label would be
            # swallowed by it instead of pressing the pad underneath.
            label.setAttribute(Qt.WA_TransparentForMouseEvents)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 8)
        layout.setSpacing(1)
        layout.addWidget(self.hint_label)
        layout.addStretch(1)
        layout.addWidget(self.numeral_label)
        layout.addWidget(self.name_label)
        layout.addStretch(1)

        # Read by theme.py's ChordPad[playing="true"] rule. Set here (not
        # only in set_playing) so the property exists before the first
        # polish, otherwise the selector never matches until it's toggled.
        self.setProperty("playing", False)

    def set_chord(self, numeral: str, name: str) -> None:
        self.numeral_label.setText(numeral)
        self.name_label.setText(name)
        self.setToolTip(f"{numeral} \u2014 {name}" if name else numeral)

    def set_playing(self, playing: bool) -> None:
        """Marks this pad as the chord currently being played by the
        sequencer."""
        theme.set_state_property(self, "playing", playing)


# Every fourth step is a beat, which is what the lane groups by.
ACID_STEPS_PER_BEAT = 4
ACID_REST_GLYPH = "\u00b7"

ACID_LANE_TOOLTIP = (
    "The 16 steps of the acid line as it will actually sound. Each cell is one "
    "16th note showing the note it plays, or \u00b7 for a rest; a trailing "
    "apostrophe is an octave jump, a bright cell is an accent, and a linked "
    "right edge means the step slides into the next one instead of "
    "retriggering. The pattern is written in scale degrees relative to the "
    "bar's chord root, so these names re-read themselves as the progression "
    "moves. The lit cell is the step playing now. Re-cast it with Randomize, "
    "or with Roll.")


class AcidCell(QLabel):
    """One 16th-note cell of the acid lane.

    A QLabel rather than a button: the lane is a readout, not a control,
    and making the cells clickable would imply a step editor that doesn't
    exist. The state it shows lives in Qt properties so theme.py can style
    it -- `playing` for the playhead, `rest` for a silent step, `accent`
    and `slide` for the two articulations that do most of the work in an
    acid line, and `downbeat` so the four beats stay countable."""

    def __init__(self, step_index: int):
        super().__init__(ACID_REST_GLYPH)
        self.setObjectName("acidStep")
        self.setAlignment(Qt.AlignCenter)
        for name in ("playing", "accent", "slide"):
            self.setProperty(name, False)
        self.setProperty("rest", True)
        self.setProperty("downbeat", step_index % ACID_STEPS_PER_BEAT == 0)

    def set_step(self, name: str | None, octave: int,
                 accent: bool, slide: bool) -> None:
        """`name` is the note this step sounds, or None for a rest. An
        octave jump is marked with a trailing apostrophe, which is the
        notation the published 303 pattern transcriptions use."""
        self.setText(ACID_REST_GLYPH if name is None
                     else f"{name}'" if octave else name)
        theme.set_state_property(self, "rest", name is None)
        theme.set_state_property(self, "accent", accent and name is not None)
        theme.set_state_property(self, "slide", slide and name is not None)

    def set_playing(self, playing: bool) -> None:
        theme.set_state_property(self, "playing", playing)


class AcidLane(QWidget):
    """The 16 acid steps in a row, with the playhead lit.

    Exists because the acid line is the one voice whose notes aren't
    visible anywhere else on the window: the chord pads show the
    progression and the slot dropdowns show the degrees, but the acid
    pattern is a cast that happens inside the engine and was previously
    only audible. Showing the *resolved* notes rather than the raw
    degree offsets is deliberate -- the offsets are an implementation
    detail, and the question being asked of this widget is "what is it
    playing?"."""

    def __init__(self):
        super().__init__()
        self.setToolTip(ACID_LANE_TOOLTIP)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SELECTOR_SPACING)
        self.steps = [AcidCell(i) for i in range(ACID_STEPS)]
        for step in self.steps:
            step.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            layout.addWidget(step)
        self._playing_index: int | None = None

    def set_steps(self, steps) -> None:
        """`steps` are (note name or None, octave, accent, slide) tuples."""
        for cell, (name, octave, accent, slide) in zip(self.steps, steps):
            cell.set_step(name, octave, accent, slide)

    def set_playing_step(self, index: int | None) -> None:
        """Lights the step playing now (None = nothing playing). Only the
        two cells that actually change are touched: this runs at a 16th
        note, and set_state_property() forces a full unpolish/polish, so
        restyling all 16 every step would be 8x the work at 120bpm."""
        if index == self._playing_index:
            return
        if self._playing_index is not None:
            self.steps[self._playing_index].set_playing(False)
        if index is not None:
            self.steps[index].set_playing(True)
        self._playing_index = index


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
        self._engine_signals.chord_changed.connect(self._on_engine_chord_changed)
        self._engine_signals.acid_step.connect(self._on_engine_acid_step)

        self._reset_signals = MidiResetSignals()
        self._reset_signals.finished.connect(self._on_midi_reset_finished)
        # Guards Play/Stop/chord-preview against touching self._midi_output
        # while a background MIDI-reset thread is closing/reopening it --
        # narrower than disabling the whole window, which was tried first
        # and found to also disable a currently-open MidiStatusDialog
        # (including its own Close button) since it's a child of this one.
        self._midi_reset_in_progress = False

        # Bridge scripts (e.g. to an M8/Teensy) this app can launch and
        # supervise itself, so their uptime is visible and they can be
        # restarted from the MIDI Status dialog. Not auto-started: one may
        # already be running in its own terminal, and silently launching a
        # second instance alongside it would be its own source of bugs.
        self._bridges = bridge_manager.discover_bridges()

        self._master_clock = MidiClock(self._midi_output)
        self._active_clock = self._master_clock
        # MIDI clock slave mode: following an external clock (e.g. Ableton
        # set as clock master) instead of generating one. Opened lazily,
        # only once "External clock sync" is actually checked.
        self._slave_input: midi_io.MidiInput | None = None
        self._slave_clock: MidiClockSlave | None = None

        self._engine = PlaybackEngine(
            self._midi_output, self._master_clock,
            on_loop_complete=self._engine_signals.looped.emit,
            # Both hooks are called on the clock's thread; emitting a signal
            # is the thread-safe handoff onto the GUI thread (see
            # EngineSignals). -1 stands in for the engine's None.
            on_chord_change=lambda position: self._engine_signals.chord_changed.emit(
                -1 if position is None else position),
            on_acid_step=lambda step: self._engine_signals.acid_step.emit(
                -1 if step is None else step))

        # Set by a Roll, spent at the next loop boundary (or at Play, if
        # stopped). A Roll casts the acid line along with key/scale/slots,
        # but randomize_acid_pattern() takes effect on the very next step,
        # which would swap the acid line mid-bar while the chords it plays
        # over are still waiting for the boundary. Deferring keeps a Roll
        # landing all at once, the way _on_roll_clicked already promises.
        self._pending_acid_cast = False
        # Which progression slot is sounding, or -1 while stopped. Tracked
        # only so the acid lane knows which bar's root to resolve its steps
        # against; the chord pads read it straight off the signal.
        self._sounding_slot = -1
        self._full_chords: list[list[int]] = []
        self._full_roots: list[int] = []
        self._numerals: list[str] = []
        self._chord_names: list[str] = []
        self._preview_octave_shift: dict[int, int] = {}
        self._preview_chords_enabled: dict[int, bool] = {}
        self._preview_bass_enabled: dict[int, bool] = {}
        self._preview_chord_channel: dict[int, int] = {}
        self._preview_bass_channel: dict[int, int] = {}

        self._build_widgets()
        theme.apply(self)
        self._on_mode_changed()
        self._update_progression_slots()

        # External sync is the default: m00Dr is nearly always run against
        # a DAW or hardware that owns the tempo, and being clock master by
        # default meant every session started by unchecking this. Done here
        # rather than with setChecked() at construction time so the toggle
        # handler runs against a fully built window -- it opens a MIDI port
        # and touches bpm_edit. Falls back to master mode if the port can't
        # be opened, rather than refusing to start.
        self.external_sync_checkbox.setChecked(True)

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

        self.roll_button = QPushButton("Roll")
        self.roll_button.setObjectName("rollButton")
        self.roll_button.setToolTip(ROLL_TOOLTIP)
        self.roll_button.clicked.connect(self._on_roll_clicked)

        # Names the cast the way the I Ching does. Two lines: the hexagram
        # itself, then the pair of trigrams that compose it, spoken
        # upper-over-lower.
        self.reading_label = QLabel(NO_READING_TEXT)
        self.reading_label.setTextFormat(Qt.RichText)
        self.reading_label.setObjectName("readingLabel")
        self.reading_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.reading_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.reading_label.setToolTip(READING_TOOLTIP)

        self.bpm_edit = QLineEdit(DEFAULT_BPM)
        self.bpm_edit.setObjectName("bpmEdit")
        self.bpm_edit.setAlignment(Qt.AlignCenter)
        self.bpm_edit.setToolTip("Beats per minute, used when m00Dr generates its own clock.")
        # A 3-digit field had been taking well over half the transport row;
        # the space goes to Play/Stop, which actually use it.
        self.bpm_edit.setFixedWidth(BPM_FIELD_WIDTH)

        self.loop_length_box = QComboBox()
        self.loop_length_box.addItems(LOOP_LENGTHS)
        self.loop_length_box.setCurrentText(LOOP_LENGTHS[-1])
        self.loop_length_box.setToolTip(
            "How many of the four progression slots above are played, from the left.")
        self.loop_length_box.currentTextChanged.connect(self._update_progression_slots)

        self.play_button = QPushButton("Play")
        self.play_button.setObjectName("playButton")
        self.play_button.setProperty("playing", False)
        self.play_button.clicked.connect(self._on_play)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("stopButton")
        self.stop_button.clicked.connect(self._on_stop)

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
            "How much of the acid line's I Ching cast is let through. Each 16th-note "
            "step is dealt four lines -- what it plays, which octave, whether it is "
            "accented, whether it slides into the next step -- and this is the chance "
            "each departure from a plain root is honoured. 100% is the reading as "
            "cast: a couple of rests a bar, a little over 40% of the notes on the "
            "root, and roughly a quarter each jumped an octave, accented and slid, "
            "which is the shape transcribed 303 lines actually have. 0% is a straight "
            "16th-note pulse on the root.")
        self.acid_noise_slider.valueChanged.connect(self._on_acid_noise_changed)
        self._update_acid_noise_label(DEFAULT_ACID_NOISE_PERCENT)

        self.acid_wide_checkbox = QCheckBox("Wide deviation")
        self.acid_wide_checkbox.setToolTip(
            "How far around the circle of fifths the pattern's handful of recurring "
            "non-root notes are drawn from. Unchecked: one fifth either way, so the "
            "fourth and the fifth. Checked: two fifths as well, adding the flat "
            "seventh and the second for a more outside line.")
        self.acid_wide_checkbox.setChecked(True)
        self.acid_wide_checkbox.toggled.connect(self._on_acid_wide_toggled)

        self.acid_randomize_button = QPushButton("Randomize")
        self.acid_randomize_button.clicked.connect(self._on_acid_randomize_clicked)

        self.acid_lane = AcidLane()

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
        for widget in (self.bpm_edit, self.loop_length_box,
                       self.humanize_checkbox, self.octave_spinbox, self.arp_pattern_box,
                       self.arp_rate_box, self.acid_wide_checkbox, self.acid_noise_slider,
                       self.external_sync_checkbox):
            _grow(widget)

        for button in (self.play_button, self.stop_button):
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

        # Key and scale: compact, measured widths rather than half the
        # window each.
        for widget in (self.key_box, self.mode_box, self.roll_button):
            _grow(widget, min_height=SELECTOR_HEIGHT, point_size=PRIMARY_POINT_SIZE)
        # Square rather than text-width, so it sits in the chunky-block row
        # as a block itself instead of a thin tab between two of them.
        self.roll_button.setFixedWidth(SELECTOR_HEIGHT)
        pair_limit = _row_width_limit(2)
        self.key_box.setFixedWidth(_fit_width(self.key_box, theory.Note_Dict, pair_limit))
        self.mode_box.setFixedWidth(_fit_width(self.mode_box, theory.Modes, pair_limit))

        progression_row = QHBoxLayout()
        progression_row.setSpacing(SELECTOR_SPACING)
        for widget in (self.key_box, self.mode_box, self.roll_button):
            progression_row.addWidget(widget)
        progression_row.addWidget(self.reading_label)
        progression_row.addStretch(1)

        self.numeral_boxes = [QComboBox() for _ in range(NUM_NUMERAL_SLOTS)]
        # One opacity effect per slot, created once and switched on/off by
        # _update_progression_slots() rather than attached and detached --
        # a disabled effect leaves the combo rendering natively, where a
        # permanently-attached one at full opacity would still route it
        # through an offscreen pixmap for no reason.
        self._slot_dim_effects: list[QGraphicsOpacityEffect] = []
        numeral_row = QHBoxLayout()
        numeral_row.setSpacing(SELECTOR_SPACING)
        slot_labels = all_slot_labels()
        for box in self.numeral_boxes:
            # The acid line is written in degrees relative to the bar's
            # chord root, so retuning a slot respells the lane -- worth
            # seeing immediately while stopped, not only once it plays.
            box.currentIndexChanged.connect(self._refresh_acid_lane)
            _grow(box, min_height=SELECTOR_HEIGHT, point_size=PRIMARY_POINT_SIZE)
            box.setFixedWidth(_fit_width(box, slot_labels,
                                         _row_width_limit(NUM_NUMERAL_SLOTS)))
            effect = QGraphicsOpacityEffect(box)
            effect.setOpacity(INACTIVE_SLOT_OPACITY)
            effect.setEnabled(False)
            box.setGraphicsEffect(effect)
            self._slot_dim_effects.append(effect)
            numeral_row.addWidget(box)
        numeral_row.addStretch(1)

        transport_row = QHBoxLayout()
        for widget in (self.bpm_edit, self.loop_length_box,
                       self.play_button, self.stop_button):
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

        self.chord_buttons: list[ChordPad] = []
        chord_row = QHBoxLayout()
        chord_row.setSpacing(8)
        for i in range(NUM_CHORD_BUTTONS):
            # The pads are triggered by the 1-7 number keys too (see
            # keyPressEvent), which is worth saying on the pad itself
            # rather than only in the roadmap.
            button = ChordPad(str(i + 1))
            _grow(button, min_height=CHORD_BUTTON_MIN_HEIGHT, point_size=PRIMARY_POINT_SIZE,
                  expanding=True, max_height=CHORD_BUTTON_MAX_HEIGHT)
            button.pressed.connect(lambda i=i: self._on_chord_pressed(i))
            button.released.connect(lambda i=i: self._on_chord_released(i))
            chord_row.addWidget(button)
            self.chord_buttons.append(button)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(CONTENT_MARGIN, CONTENT_MARGIN,
                                  CONTENT_MARGIN, CONTENT_MARGIN)
        layout.addLayout(progression_row)
        layout.addLayout(numeral_row)
        layout.addLayout(transport_row)
        layout.addLayout(performance_row)
        layout.addLayout(arp_row)
        layout.addLayout(acid_row)
        layout.addWidget(self.acid_lane)
        layout.addLayout(chord_row, 1)  # chord buttons get first claim on extra window space

    # -- state -------------------------------------------------------------

    def _on_mode_changed(self, _value: str | None = None) -> None:
        key, mode = self.key_box.currentText(), self.mode_box.currentText()
        (self._full_chords, self._full_roots, self._numerals,
         self._chord_names) = generate_full_scale(key, mode)
        self._engine.set_scale(self._full_roots)

        # A bare numeral says which scale degree, but not what you'll
        # actually hear -- the slots and the pads both spell the chord out.
        items = [f"{numeral}{NUMERAL_NAME_SEPARATOR}{name}"
                 for numeral, name in zip(self._numerals, self._chord_names)]
        for box in self.numeral_boxes:
            box.blockSignals(True)
            box.clear()
            box.addItems(items)
            box.blockSignals(False)
        for i, box in enumerate(self.numeral_boxes):
            if i < len(self._numerals):
                box.setCurrentIndex(i)

        for i, button in enumerate(self.chord_buttons):
            if i < len(self._numerals):
                button.set_chord(self._numerals[i], self._chord_names[i])
            else:
                button.set_chord("-", "")

        self._refresh_acid_lane()

    def _on_roll_clicked(self) -> None:
        """Rolls key, scale and the four progression slots (see oracle.py).

        Order matters: changing the key or the scale repopulates the slot
        dropdowns and resets them to the plain I..VII order, so the rolled
        degrees have to be applied after that, not before.

        Deliberately does not push the result to the engine. A roll leaves
        the widgets exactly as a hand-turned dropdown would, so a roll
        while playing takes effect at the next loop boundary (via
        _reload_progression) instead of switching chords mid-bar, and a
        roll while stopped is picked up by _on_play.
        """
        cast = oracle.roll(self.key_box.currentText(), self.mode_box.currentText(),
                           [box.currentIndex() for box in self.numeral_boxes])
        # The acid line is cast by the same roll, as its own 16-line figure
        # rather than out of this hexagram's six -- one line per 16th-note
        # step (see playback._generate_acid_pattern). Queued, not applied:
        # see _pending_acid_cast.
        self._pending_acid_cast = True
        self.key_box.setCurrentText(cast.key)
        self.mode_box.setCurrentText(cast.mode)
        for box, degree in zip(self.numeral_boxes, cast.slots):
            if degree < box.count():
                box.setCurrentIndex(degree)
        # The reading that produced this, so a roll under test can be read
        # back rather than just observed.
        self.roll_button.setToolTip(
            f"{ROLL_TOOLTIP}\n\nLast roll:\n{cast.drawing}\n\n{cast.describe()}")
        self._show_reading(cast)
        # Deferring only earns its keep while playing, where it stops the
        # acid line swapping mid-bar under the chords it plays over. While
        # stopped there is no bar to protect and nothing to hear, so
        # holding it back just makes Roll look like it ignored the acid
        # line -- the lane would show the old pattern merely respelled
        # into the new key.
        if not self._engine.is_playing:
            self._apply_pending_acid_cast()

    def _show_reading(self, cast: oracle.Cast) -> None:
        """Names the cast on screen. The label carries the hexagram and its
        two trigrams; the changing-line transformation goes in the tooltip
        rather than the label, since it is the half of a reading that is
        nice to have and would otherwise double the row's height."""
        self.reading_label.setText(_reading_html(cast))
        moving = cast.transformation
        detail = [cast.drawing]
        if moving is None:
            detail.append("No changing lines \u2014 the reading stands as cast.")
        else:
            changing = [str(i + 1) for i, value in enumerate(cast.lines)
                        if oracle.is_changing(value)]
            detail.append(f"Changing line(s) {', '.join(changing)} \u2014 "
                          f"this hexagram moves to:\n{moving.label()}")
        self.reading_label.setToolTip("\n\n".join([READING_TOOLTIP, *detail]))

    def _selected_progression(self) -> tuple[list[list[int]], list[int]]:
        """The chords/roots currently chosen by the numeral dropdowns,
        sliced to the loop length. This -- reading widget state directly
        -- is the real-state replacement for the OLD app's packed label
        string parsing."""
        loop_length = int(self.loop_length_box.currentText())
        # currentIndex(), not a lookup of currentText() in self._numerals:
        # the slot labels now read "i · Em7" rather than a bare numeral,
        # and the index is the scale degree directly in any case.
        indices = [box.currentIndex() for box in self.numeral_boxes]
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
        self._apply_pending_acid_cast()

    def _update_progression_slots(self, _value: str | None = None) -> None:
        """Dims the progression slots the loop length doesn't reach.

        Loop length silently slices the progression (see
        _selected_progression), so with a loop length of 2 the third and
        fourth slots have no effect at all -- previously with nothing on
        screen saying so. They stay enabled rather than disabled, so a
        progression can still be set up before the loop length is raised
        to play it."""
        loop_length = int(self.loop_length_box.currentText())
        for i, effect in enumerate(self._slot_dim_effects):
            active = i < loop_length
            effect.setEnabled(not active)
            self.numeral_boxes[i].setToolTip("" if active else (
                f"Not played: the loop is {loop_length} bar(s) long, so only the first "
                f"{loop_length} slot(s) are used. Raise \"loop length\" to include this one."))

    def _on_engine_chord_changed(self, position: int) -> None:
        """Lights the pad for whichever chord the sequencer is playing
        right now (-1 = nothing playing). Arrives on the GUI thread via
        EngineSignals.chord_changed; `position` indexes the progression
        slots, so the pad it corresponds to is whatever scale degree that
        slot currently has selected."""
        self._sounding_slot = position
        pad_index = None
        if 0 <= position < len(self.numeral_boxes):
            pad_index = self.numeral_boxes[position].currentIndex()
        for i, pad in enumerate(self.chord_buttons):
            pad.set_playing(i == pad_index)
        self._refresh_acid_lane()

    def _on_engine_acid_step(self, step: int) -> None:
        """Moves the acid lane's playhead (-1 = nothing playing). Arrives on
        the GUI thread via EngineSignals.acid_step, once per 16th note."""
        self.acid_lane.set_playing_step(None if step < 0 else step)

    def _refresh_acid_lane(self) -> None:
        """Re-reads the engine's cast pattern into the lane, resolved to
        the notes it will actually sound. Called wherever those notes could
        have moved: a new cast, a new bar (the pattern is relative to the
        bar's chord root, so the same 16 steps spell different notes), or a
        key/scale change.

        The root is taken from the slot dropdowns rather than from the
        engine -- same reasoning as _selected_progression, and it means the
        lane reads correctly while stopped, when the engine has no
        progression loaded at all."""
        if not self._full_roots:
            return
        slot = self._sounding_slot
        if not 0 <= slot < len(self.numeral_boxes):
            slot = 0  # stopped: preview against the bar that will play first
        home_note = self._full_roots[self.numeral_boxes[slot].currentIndex()]
        steps = []
        for step in self._engine.acid_pattern:
            note = acid_note_for_step(step.degree, home_note, self._full_roots)
            steps.append((None if note is None else theory.midi_int_to_note(note),
                          step.octave, step.accent, step.slide))
        self.acid_lane.set_steps(steps)

    # -- actions -------------------------------------------------------------

    def _on_play(self) -> None:
        if self._midi_reset_in_progress:
            return
        if self._active_clock is self._master_clock:
            try:
                bpm = float(self.bpm_edit.text())
            except ValueError:
                return
            self._master_clock.bpm = bpm
        chords, roots = self._selected_progression()
        self._engine.load_progression(chords, roots)
        self._apply_pending_acid_cast()
        self._engine.start()
        theme.set_state_property(self.play_button, "playing", self._engine.is_playing)
        self.external_sync_checkbox.setEnabled(False)

    def _on_stop(self) -> None:
        if self._midi_reset_in_progress:
            return
        self._engine.stop()
        theme.set_state_property(self.play_button, "playing", False)
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
        # An explicit re-cast now, which also supersedes any roll still
        # waiting for the loop boundary -- otherwise that boundary would
        # immediately throw away the pattern just asked for by hand.
        self._pending_acid_cast = False
        self._engine.randomize_acid_pattern()
        self._refresh_acid_lane()

    def _apply_pending_acid_cast(self) -> None:
        """Spends a Roll's queued acid cast, if there is one. Called at the
        loop boundary while playing and at Play while stopped, matching how
        a rolled key/scale/progression reaches the engine."""
        if not self._pending_acid_cast:
            return
        self._pending_acid_cast = False
        self._engine.randomize_acid_pattern()
        self._refresh_acid_lane()

    def _on_sync_mode_toggled(self, external: bool) -> None:
        """Switches PlaybackEngine between the internal master MidiClock
        and an external MidiClockSlave following a MIDI input named
        "m00Dr In" (created the same way MidiOutput creates "m00Dr" for
        note output). Only reachable while stopped -- the checkbox is
        disabled during playback, see _on_play()/_on_stop()."""
        if external:
            if self._slave_clock is None:
                try:
                    self._slave_input = midi_io.MidiInput()
                    self._slave_input.open()
                except Exception:
                    # Opening "m00Dr In" can fail outright if the platform's
                    # MIDI service is wedged. Since this path is now taken at
                    # startup (see __init__), a failure has to leave a usable
                    # window: drop back to the internal clock and let the box
                    # show it, rather than propagating out of a constructor.
                    self._slave_input = None
                    self.external_sync_checkbox.setChecked(False)
                    return
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
                                   self._on_reset_midi_server, self._reset_signals.finished,
                                   self._bridges)
        dialog.exec()

    def _on_reset_midi_server(self) -> None:
        """Starts the MIDI-service reset + this app's own port reopen on a
        background thread and returns immediately -- see
        MidiResetSignals for why this isn't done directly on the GUI
        thread. Sets _midi_reset_in_progress for the duration: the
        background thread touches self._midi_output/_slave_input directly,
        and letting Play/Stop/chord-preview touch them concurrently would
        be a real race, not just a cosmetic issue."""
        self._midi_reset_in_progress = True
        thread = threading.Thread(target=self._reset_midi_server_worker, daemon=True)
        thread.start()

    def _reset_midi_server_worker(self) -> None:
        """Runs entirely off the GUI thread -- must not touch any widget
        directly; reports back via self._reset_signals.finished, which Qt
        marshals onto the GUI thread automatically since emitter and
        receiver live in different threads.

        Resets the platform MIDI service, then reopens this app's own
        ports on the fresh service -- a MIDIServer reset invalidates every
        virtual port already open in this process (m00Dr's own "m00Dr"
        output, and "m00Dr In" if external clock sync has ever been
        enabled), the same way it invalidates every other process's ports.
        This can't do anything about *other* MIDI apps/bridges; those need
        restarting separately, and may crash outright rather than just
        needing a reconnect (the MidiStatusDialog's tooltip says so).

        Bug found and fixed same day: reopening immediately after the kill,
        with no delay, silently failed in real use (own ports vanished
        entirely -- confirmed missing from a live port listing afterward,
        not just a display-refresh timing issue). MIDIServer's process exit
        is immediate but its respawn/re-registration isn't instantaneous;
        racing it left this process's own close()+open() calls operating on
        a MIDI service that wasn't back yet.

        Second bug found and fixed the same day: the fix for the above ran
        directly on the GUI thread and hung the *entire app* in real use --
        not just the dialog -- when a port open/close call itself hung
        rather than failing fast. Moved onto a background thread instead of
        adding yet another synchronous retry.

        Also restarts every bridge that was running before the reset --
        found in real use that a long-lived bridge process can survive a
        MIDIServer reset without crashing, yet still end up with a
        permanently stale CoreMIDI connection of its own (confirmed: its
        log showed zero successful reconnects for nearly an hour, while a
        brand new process queried at the same moment saw the port it was
        looking for just fine). A bridge process not crashing was never
        proof it was healthy.

        That restart now reaches bridges started *outside* this app (the
        way M8-SETUP.md tells you to run them, in their own terminal), not
        just ones launched from the dialog -- which is the case that
        mattered most, since the untouchable bridge was exactly the one
        likely to have been running long enough to go stale. The scan
        happens after the MIDIServer reset so it also catches a bridge that
        crashed as a result of it, rather than restarting a process that is
        about to die."""
        midi_status.reset_midi_server()
        time.sleep(RESET_SETTLE_SECONDS)

        ok = _reopen_port(self._midi_output, "m00Dr")

        if self._slave_clock is not None:
            was_running = self._slave_clock.is_running
            if was_running:
                # stop()/start() (not touching _slave_input directly) so the
                # slave clock re-subscribes its callback to the freshly
                # reopened port -- close()+open() alone would silently drop
                # it, since cancel_callback()/set_callback() are what
                # start()/stop() actually call.
                self._slave_clock.stop()
            if not _reopen_port(self._slave_input, "m00Dr In"):
                ok = False
            if was_running:
                self._slave_clock.start()

        # The dialog's own timer may be scanning on the GUI thread at the
        # same moment. Both only assign whole values to _external/_process,
        # so the worst case is the dialog briefly showing pre-restart state
        # and correcting itself on its next tick.
        bridge_manager.refresh_all(self._bridges)
        for bridge in self._bridges:
            if bridge.is_running:
                bridge.restart()

        self._reset_signals.finished.emit(ok)

    def _on_midi_reset_finished(self, _ok: bool) -> None:
        self._midi_reset_in_progress = False

    def _on_chord_pressed(self, index: int) -> None:
        if index >= len(self._full_chords) or self._midi_reset_in_progress:
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
