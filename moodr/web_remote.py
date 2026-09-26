"""Browser remote: control m00Dr from an iPad (or any browser) on the LAN.

Two small servers run on the Qt event loop, so nothing here touches a
widget off the GUI thread: a bare-bones HTTP server that hands out the one
page (remote.html), and a WebSocket server the page talks to.

The remote drives the *widgets*, not the engine. A tap on the iPad does
exactly what the same click on the desktop window would -- setChecked(),
setCurrentText(), click() -- so every existing handler, guard and side
effect runs unchanged, and the two views can't drift apart. Going the
other way, the window's state is snapshotted a few times a second while a
client is connected and sent only when it changed; polling rather than
hooking every widget signal keeps this module from needing to know about
state that changes without a signal (the playhead, the reading label).
"""

import json
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QAbstractSocket, QHostAddress, QNetworkInterface, QTcpServer
from PySide6.QtWebSockets import QWebSocketServer
from PySide6.QtWidgets import QAbstractButton, QCheckBox, QComboBox

HTTP_PORT = 8765
WS_PORT = 8766
# Fast enough that the acid playhead (a 16th note is 125ms at 120bpm) reads
# as moving with the music; a snapshot is a few hundred bytes of widget
# reads and is skipped entirely while nobody is connected.
POLL_INTERVAL_MS = 40
PAGE = Path(__file__).with_name("remote.html")


def lan_address() -> str | None:
    """The first non-loopback IPv4 address, i.e. what the iPad should type."""
    for address in QNetworkInterface.allAddresses():
        if (address.protocol() == QAbstractSocket.IPv4Protocol
                and not address.isLoopback()
                and not address.toString().startswith("169.254.")):
            return address.toString()
    return None


class WebRemote(QObject):
    def __init__(self, window, http_port: int = HTTP_PORT, ws_port: int = WS_PORT):
        super().__init__(window)
        self._window = window
        self._http_port = http_port
        self._ws_port = ws_port
        self._http = QTcpServer(self)
        self._http.newConnection.connect(self._on_http_connection)
        self._ws = QWebSocketServer("m00Dr", QWebSocketServer.NonSecureMode, self)
        self._ws.newConnection.connect(self._on_ws_connection)
        # socket -> pad indices it is holding down, so a client that drops
        # mid-press (Wi-Fi blip, screen lock) doesn't leave a chord hanging.
        self._clients: dict = {}
        self._last_snapshot = ""
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._broadcast_if_changed)

    # -- lifecycle -----------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._http.isListening()

    @property
    def url(self) -> str:
        return f"http://{lan_address() or 'localhost'}:{self._http_port}"

    def start(self) -> bool:
        if self.is_running:
            return True
        if not self._http.listen(QHostAddress.Any, self._http_port):
            return False
        if not self._ws.listen(QHostAddress.Any, self._ws_port):
            self._http.close()
            return False
        self._timer.start()
        return True

    def stop(self) -> None:
        self._timer.stop()
        for socket in list(self._clients):
            self._release_held(socket)
            socket.close()
        self._clients.clear()
        self._ws.close()
        self._http.close()
        self._last_snapshot = ""

    # -- HTTP: serves the page, nothing else -------------------------------

    def _on_http_connection(self) -> None:
        while self._http.hasPendingConnections():
            socket = self._http.nextPendingConnection()
            socket.readyRead.connect(lambda s=socket: self._serve(s))
            socket.disconnected.connect(socket.deleteLater)

    def _serve(self, socket) -> None:
        request = bytes(socket.readAll()).decode("latin-1")
        path = request.split(" ", 2)[1] if request.count(" ") >= 2 else ""
        if path.split("?")[0] in ("/", "/index.html"):
            body = PAGE.read_bytes().replace(b"__WS_PORT__", str(self._ws_port).encode())
            status, kind = "200 OK", "text/html; charset=utf-8"
        else:
            body, status, kind = b"not found", "404 Not Found", "text/plain"
        socket.write(f"HTTP/1.1 {status}\r\nContent-Type: {kind}\r\n"
                     f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\n"
                     "Connection: close\r\n\r\n".encode() + body)
        socket.disconnectFromHost()

    # -- WebSocket ------------------------------------------------------------

    def _on_ws_connection(self) -> None:
        while self._ws.hasPendingConnections():
            socket = self._ws.nextPendingConnection()
            self._clients[socket] = set()
            socket.textMessageReceived.connect(
                lambda text, s=socket: self._on_message(s, text))
            socket.disconnected.connect(lambda s=socket: self._on_disconnected(s))
            socket.sendTextMessage(self._snapshot_json())

    def _on_disconnected(self, socket) -> None:
        self._release_held(socket)
        self._clients.pop(socket, None)
        socket.deleteLater()

    def _broadcast_if_changed(self) -> None:
        if not self._clients:
            return
        snapshot = self._snapshot_json()
        if snapshot == self._last_snapshot:
            return
        self._last_snapshot = snapshot
        for socket in self._clients:
            socket.sendTextMessage(snapshot)

    # -- remote -> window -------------------------------------------------------

    def _controls(self) -> dict:
        w = self._window
        controls = {
            "key": w.key_box, "mode": w.mode_box, "loop": w.loop_length_box,
            "octave": w.octave_spinbox, "humanize": w.humanize_checkbox,
            "chords": w.chords_button, "bass": w.bass_button, "stabs": w.stab_button,
            "circuit": w.circuit_channels_button, "sync": w.external_sync_checkbox,
            "arp": w.arp_button, "arpPattern": w.arp_pattern_box, "arpRate": w.arp_rate_box,
            "acid": w.acid_button, "acidNoise": w.acid_noise_slider,
            "acidWide": w.acid_wide_checkbox, "bpm": w.bpm_edit,
        }
        for i, box in enumerate(w.numeral_boxes):
            controls[f"slot{i}"] = box
        return controls

    def _on_message(self, socket, text: str) -> None:
        try:
            message = json.loads(text)
            op = message["op"]
        except (ValueError, KeyError, TypeError):
            return
        w = self._window
        if op == "click":
            button = {"play": w.play_button, "stop": w.stop_button, "roll": w.roll_button,
                      "randomize": w.acid_randomize_button}.get(message.get("id"))
            if button is not None and button.isEnabled():
                button.click()
        elif op in ("press", "release"):
            index = message.get("pad")
            if not isinstance(index, int) or not 0 <= index < len(w.chord_buttons):
                return
            held = self._clients.get(socket, set())
            if op == "press" and index not in held:
                held.add(index)
                w.chord_buttons[index].setDown(True)
                w._on_chord_pressed(index)
            elif op == "release" and index in held:
                held.discard(index)
                w.chord_buttons[index].setDown(False)
                w._on_chord_released(index)
        elif op == "set":
            self._set(message.get("id"), message.get("value"))
        self._broadcast_if_changed()

    def _set(self, control_id, value) -> None:
        widget = self._controls().get(control_id)
        if widget is None or not widget.isEnabled():
            return
        if isinstance(widget, QComboBox):
            if widget.findText(str(value)) >= 0:
                widget.setCurrentText(str(value))
        elif isinstance(widget, QAbstractButton):
            widget.setChecked(bool(value))
        elif control_id == "bpm":
            try:
                bpm = float(value)
            except (TypeError, ValueError):
                return
            if 20 <= bpm <= 300:
                widget.setText(f"{bpm:g}")
        elif isinstance(value, (int, float)):
            widget.setValue(int(value))  # QSpinBox / QSlider clamp to their own range

    def _release_held(self, socket) -> None:
        for index in sorted(self._clients.get(socket, ())):
            self._window.chord_buttons[index].setDown(False)
            self._window._on_chord_released(index)
        if socket in self._clients:
            self._clients[socket].clear()

    # -- window -> remote -------------------------------------------------------

    def _snapshot_json(self) -> str:
        w = self._window
        controls = {}
        for control_id, widget in self._controls().items():
            entry = {"enabled": widget.isEnabled()}
            if isinstance(widget, QComboBox):
                entry["value"] = widget.currentText()
                entry["items"] = [widget.itemText(i) for i in range(widget.count())]
            elif isinstance(widget, (QAbstractButton, QCheckBox)):
                entry["value"] = widget.isChecked()
            elif control_id == "bpm":
                entry["value"] = widget.text()
            else:
                entry.update(value=widget.value(), min=widget.minimum(), max=widget.maximum())
            controls[control_id] = entry
        loop_length = int(w.loop_length_box.currentText())
        for i in range(len(w.numeral_boxes)):
            controls[f"slot{i}"]["active"] = i < loop_length
        return json.dumps({
            "controls": controls,
            "playing": bool(w.play_button.property("playing")),
            "reading": w.reading_label.text(),
            "noiseLabel": w.acid_noise_label.text(),
            "pads": [{"numeral": p.numeral_label.text(), "name": p.name_label.text(),
                      "playing": bool(p.property("playing"))} for p in w.chord_buttons],
            "acid": [{"text": c.text(), "rest": bool(c.property("rest")),
                      "accent": bool(c.property("accent")),
                      "slide": bool(c.property("slide")),
                      "playing": bool(c.property("playing"))} for c in w.acid_lane.steps],
        }, separators=(",", ":"))
