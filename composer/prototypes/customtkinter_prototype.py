"""Phase 3 GUI prototype: CustomTkinter.

Same widget set as prototypes/pyside6_prototype.py (key/mode dropdowns,
BPM field, Play/Stop, 7 chord buttons) so the two can be compared
like-for-like. See ROADMAP.md Phase 3 for the comparison writeup.
"""

import queue
import sys
from pathlib import Path

import customtkinter as ctk

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared import build_engine, build_progression  # noqa: E402

from moodr import theory  # noqa: E402


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("m00Dr -- CustomTkinter prototype")
        self.geometry("520x220")

        # Tkinter widgets may only be touched from the main thread, so the
        # clock's background-thread tick callback pushes onto this
        # thread-safe queue instead, and a periodic .after() poll drains it.
        self._tick_events: queue.Queue = queue.Queue()
        self._output, self._clock, self._engine = build_engine()
        self._clock.add_tick_callback(self._tick_events.put)

        self._chords: list = []
        self._roots: list = []
        self._numerals: list = []

        top = ctk.CTkFrame(self)
        top.pack(padx=12, pady=12, fill="x")

        self.key_menu = ctk.CTkOptionMenu(top, values=theory.Note_Dict, command=self._on_selection_change)
        self.key_menu.set("C")
        self.key_menu.pack(side="left", padx=4)

        self.mode_menu = ctk.CTkOptionMenu(top, values=theory.Modes, command=self._on_selection_change)
        self.mode_menu.set("Major")
        self.mode_menu.pack(side="left", padx=4)

        self.bpm_entry = ctk.CTkEntry(top, width=60)
        self.bpm_entry.insert(0, "80")
        self.bpm_entry.pack(side="left", padx=4)

        ctk.CTkButton(top, text="Play", command=self._on_play, width=60).pack(side="left", padx=4)
        ctk.CTkButton(top, text="Stop", command=self._on_stop, width=60).pack(side="left", padx=4)

        chords_frame = ctk.CTkFrame(self)
        chords_frame.pack(padx=12, pady=8, fill="x")
        self.chord_buttons = []
        for i in range(7):
            button = ctk.CTkButton(chords_frame, text="-", width=50,
                                    command=lambda i=i: self._on_chord(i))
            button.pack(side="left", padx=2)
            self.chord_buttons.append(button)

        self.chord_status = ctk.CTkLabel(self, text="")
        self.chord_status.pack(pady=(8, 0))
        self.tick_status = ctk.CTkLabel(self, text="ticks: 0")
        self.tick_status.pack(pady=(0, 8))

        self._on_selection_change()
        self._poll_tick_events()

    def _on_selection_change(self, _value=None):
        key, mode = self.key_menu.get(), self.mode_menu.get()
        self._chords, self._roots, self._numerals = build_progression(key, mode)
        for i, button in enumerate(self.chord_buttons):
            button.configure(text=self._numerals[i] if i < len(self._numerals) else "-")

    def _on_play(self):
        try:
            bpm = float(self.bpm_entry.get())
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
        self.chord_status.configure(text=f"previewed chord {index + 1}: root midi {root_note}")

    def _poll_tick_events(self):
        latest = None
        try:
            while True:
                latest = self._tick_events.get_nowait()
        except queue.Empty:
            pass
        if latest is not None:
            self.tick_status.configure(text=f"ticks: {latest}")
        self.after(50, self._poll_tick_events)


if __name__ == "__main__":
    App().mainloop()
