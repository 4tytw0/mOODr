"""Button styling for the m00Dr window.

Qt's native macOS button rendering gives a checked QPushButton only a
slightly lighter gray than an unchecked one -- with five independent
toggles on screen (Chords/Bass/Stabs/Arp/Acid) that difference is close to
unreadable in dark mode, and it's the single most important state in the
app to be able to read at a glance while playing. This module replaces it
with an unmistakable filled-accent-vs-dim-outline pair.

Everything is derived from the running QPalette rather than hardcoded, so
the same stylesheet works in light and dark mode and picks up the user's
own macOS accent colour instead of imposing one. The stylesheet is scoped
to QPushButton, QComboBox and the labels inside a ChordPad; the check
boxes and the Noise slider are deliberately left native, since styling a
widget at all in Qt opts it out of native rendering entirely and those
already read clearly.

The combo boxes are styled for a second reason beyond looks: macOS draws
a QComboBox at a fixed bezel height and simply centres it in whatever
space it's given, so raising minimumHeight on a native combo buys nothing
but whitespace. A styled combo honours its height, which is what lets the
key/scale/progression selectors be compact blocks rather than the thin
full-width ribbons they were.
"""

import os
import tempfile

from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QColor, QPalette

# Text on top of an accent-filled button: white unless the accent itself
# is light enough that white would wash out against it.
ACCENT_LIGHTNESS_FOR_DARK_TEXT = 160

# The combo boxes' drop-down chevron. Styling a QComboBox's ::drop-down
# means the platform style stops drawing its arrow, and QSS can't draw one
# itself: a zero-sized box with only a coloured top border (the usual CSS
# triangle trick) comes out as a filled rectangle in Qt, and `image: none`
# leaves the combo with no affordance at all. Both were tried. So the
# arrow is a real image -- written to disk at startup rather than shipped
# as a file, because its colour has to follow the palette's accent.
CHEVRON_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="12" height="8" '
               'viewBox="0 0 12 8"><path d="M1.5 2 L6 6 L10.5 2" fill="none" '
               'stroke="{color}" stroke-width="2" stroke-linecap="round" '
               'stroke-linejoin="round"/></svg>')
CHEVRON_WIDTH = 12
CHEVRON_HEIGHT = 8


def chevron_path(color: QColor) -> str:
    """An on-disk SVG chevron in `color`, as a QSS-usable path, or "" if
    it couldn't be written (the caller then simply omits the arrow rather
    than failing to style anything). Named after the colour so switching
    accents doesn't serve a stale one, and cached across runs."""
    try:
        cache = (QStandardPaths.writableLocation(QStandardPaths.CacheLocation)
                 or tempfile.gettempdir())
        directory = os.path.join(cache, "m00Dr-chevrons")
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"chevron-{color.name()[1:]}.svg")
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(CHEVRON_SVG.format(color=color.name()))
        # QSS wants forward slashes even on Windows.
        return path.replace(os.sep, "/")
    except OSError:
        return ""


def set_state_property(widget, name: str, value) -> None:
    """Sets a dynamic property used by a property-based stylesheet rule
    (e.g. [playing="true"]) and forces the restyle it needs.

    Qt evaluates those selectors when a widget is polished, not when the
    property changes, so setProperty() alone updates nothing on screen.
    No-ops when the value is unchanged, since unpolish/polish is a full
    restyle of the widget and its children."""
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    """`a` blended `t` of the way toward `b` (t=0 -> a, t=1 -> b)."""
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


def accent_color(palette: QPalette) -> QColor:
    """The user's real system accent colour.

    QPalette.Accent (Qt 6.6+) carries it undimmed -- on macOS with a
    magenta accent that's #923796. QPalette.Highlight is *not* the same
    thing: it's the selection colour, already blended toward the window
    background (#5c445e here) and swapped for plain gray when the window
    isn't frontmost, which would make every "on" toggle change colour
    whenever the app lost focus. Highlight is only the fallback for a Qt
    too old to have Accent.
    """
    if hasattr(QPalette, "Accent"):
        return palette.color(QPalette.Accent)
    return palette.color(QPalette.Highlight)


def stylesheet(palette: QPalette) -> str:
    """The app's button stylesheet, built against `palette`."""
    bg = palette.color(QPalette.Window)
    fg = palette.color(QPalette.WindowText)
    accent = accent_color(palette)

    # Shades are mixes of the window background toward its own text
    # colour, so they move the right direction in both themes: on dark
    # chrome each step is lighter, on light chrome each step is darker.
    face = _mix(bg, fg, 0.09)
    face_hover = _mix(bg, fg, 0.16)
    face_pressed = _mix(bg, fg, 0.30)
    border = _mix(bg, fg, 0.20)
    text_off = _mix(fg, bg, 0.30)
    text_disabled = _mix(fg, bg, 0.65)
    text_faint = _mix(fg, bg, 0.55)
    text_muted = _mix(fg, bg, 0.30)

    accent_hover = accent.lighter(118)
    accent_pressed = accent.lighter(135)
    accent_text = "#000000" if accent.lightness() > ACCENT_LIGHTNESS_FOR_DARK_TEXT \
        else "#ffffff"

    # Accent-tinted, so the styled combos keep the splash of system colour
    # the native ones had in their arrow button.
    chevron = chevron_path(accent)
    arrow_rule = f"""
    QComboBox::down-arrow {{
        image: url("{chevron}");
        width: {CHEVRON_WIDTH}px;
        height: {CHEVRON_HEIGHT}px;
    }}""" if chevron else ""
    # The playing pad is tinted rather than filled: a filled pad would
    # read as "this toggle is on", which is what the accent already means
    # everywhere else on the window.
    playing_face = _mix(bg, accent, 0.28)

    return f"""
    QPushButton {{
        background-color: {face.name()};
        color: {text_off.name()};
        border: 1px solid {border.name()};
        border-radius: 6px;
        padding: 4px 12px;
    }}
    QPushButton:hover {{
        background-color: {face_hover.name()};
        color: {fg.name()};
    }}
    QPushButton:pressed {{
        background-color: {face_pressed.name()};
        color: {fg.name()};
    }}
    QPushButton:disabled {{
        color: {text_disabled.name()};
        border-color: {face.name()};
    }}
    QPushButton:checked {{
        background-color: {accent.name()};
        color: {accent_text};
        border: 1px solid {accent_hover.name()};
        font-weight: bold;
    }}
    QPushButton:checked:hover {{
        background-color: {accent_hover.name()};
    }}
    QPushButton:checked:pressed {{
        background-color: {accent_pressed.name()};
    }}

    /* Combo boxes, matching the buttons. macOS's native bezel ignores the
       height it's given, so these have to be styled to be any taller than
       Qt's default -- see the module docstring. */
    QComboBox {{
        background-color: {face.name()};
        color: {fg.name()};
        border: 1px solid {border.name()};
        border-radius: 6px;
        padding-left: 12px;
        padding-right: 12px;
    }}
    QComboBox:hover, QComboBox:on {{
        background-color: {face_hover.name()};
    }}
    QComboBox:disabled {{
        color: {text_disabled.name()};
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: center right;
        width: 22px;
        border: none;
        background: transparent;
    }}
{arrow_rule}
    QComboBox QAbstractItemView {{
        background-color: {face.name()};
        color: {fg.name()};
        border: 1px solid {border.name()};
        border-radius: 6px;
        padding: 4px;
        outline: none;
        selection-background-color: {accent.name()};
        selection-color: {accent_text};
    }}

    /* The BPM field, so it doesn't sit among the styled combos as the one
       remaining native black box. Scoped by object name rather than to
       QLineEdit generally: QSpinBox keeps a QLineEdit inside it, and a
       bare QLineEdit rule would restyle the octave spinbox's editor while
       leaving its native up/down buttons, which looks half-finished. */
    QLineEdit#bpmEdit {{
        background-color: {face.name()};
        color: {fg.name()};
        border: 1px solid {border.name()};
        border-radius: 6px;
        padding: 4px 10px;
    }}
    QLineEdit#bpmEdit:focus {{
        border: 1px solid {accent.name()};
    }}

    /* The transport pair: Play carries the same accent fill as a lit
       toggle, but only while the sequencer is actually running, so the
       window always answers "is this thing playing?" from across a room. */
    QPushButton#playButton, QPushButton#stopButton, QPushButton#rollButton {{
        font-weight: 600;
        color: {fg.name()};
    }}
    QPushButton#playButton[playing="true"] {{
        background-color: {accent.name()};
        color: {accent_text};
        border: 1px solid {accent_hover.name()};
    }}
    QPushButton#playButton[playing="true"]:hover {{
        background-color: {accent_hover.name()};
    }}

    /* The I Ching reading beside the Roll button. The hexagram and
       trigram glyphs live in Unicode blocks a UI font may not cover, so
       the family list falls through to the macOS fonts that do carry
       them before Qt substitutes something arbitrary. */
    QLabel#readingLabel {{
        color: {text_muted.name()};
        font-family: "Apple Symbols", "Arial Unicode MS";
        font-size: 13px;
        padding-left: 4px;
    }}

    /* A 2px border on every pad, not just the playing one: the border
       eats into the content rect, so going 1px -> 2px only when a pad
       lights up would nudge its text down a pixel on every bar. */
    ChordPad {{
        padding: 0px;
        border-width: 2px;
    }}
    ChordPad QLabel {{
        background: transparent;
        border: none;
    }}
    ChordPad #padHint {{
        color: {text_faint.name()};
        font-size: 11px;
    }}
    ChordPad #padNumeral {{
        color: {fg.name()};
        font-size: 22px;
        font-weight: 600;
    }}
    ChordPad #padName {{
        color: {text_muted.name()};
        font-size: 13px;
    }}
    ChordPad:hover {{
        background-color: {face_hover.name()};
    }}
    ChordPad:pressed {{
        background-color: {face_pressed.name()};
    }}
    ChordPad[playing="true"] {{
        background-color: {playing_face.name()};
        border: 2px solid {accent.name()};
    }}
    ChordPad[playing="true"] #padNumeral {{
        color: {fg.name()};
    }}
    /* Listed after the [playing] rule so holding the pad that's currently
       playing still gives press feedback rather than looking inert. */
    ChordPad[playing="true"]:pressed {{
        background-color: {face_pressed.name()};
    }}

    /* The acid lane: 16 cells, read left to right. Same tint-don't-fill
       treatment as a playing ChordPad, for the same reason -- a filled
       cell would read as "this is switched on". */
    AcidStep#acidStep {{
        background-color: {face.name()};
        color: {fg.name()};
        border: 1px solid {border.name()};
        border-radius: 4px;
        padding: 3px 0px;
        font-size: 12px;
        font-weight: 600;
    }}
    /* A resting step still draws its cell, so the lane keeps its shape and
       the beat grid stays countable -- only its glyph dims. */
    AcidStep#acidStep[rest="true"] {{
        color: {text_disabled.name()};
        font-weight: normal;
    }}
    /* Every fourth cell is a beat. Marked by lifting the whole cell's face
       rather than by an edge: a 1-2px border was too fine to count by at a
       glance, which is the only thing this marking is for. */
    AcidStep#acidStep[downbeat="true"] {{
        background-color: {face_pressed.name()};
        border-color: {text_faint.name()};
    }}
    AcidStep#acidStep[playing="true"] {{
        background-color: {playing_face.name()};
        border: 1px solid {accent.name()};
        color: {fg.name()};
    }}
    /* Last, so the playhead still reads as the playhead on a downbeat. */
    AcidStep#acidStep[playing="true"][downbeat="true"] {{
        background-color: {playing_face.name()};
        border-color: {accent.name()};
    }}
    """


def apply(widget) -> None:
    """Applies the stylesheet to a widget (typically the QApplication or
    the main window) using that widget's own palette."""
    widget.setStyleSheet(stylesheet(widget.palette()))
