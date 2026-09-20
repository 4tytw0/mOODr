import time
from pathlib import Path

import pytest

from moodr import bridge_manager

SLEEPY_SCRIPT = """
import time
time.sleep(30)
"""


@pytest.fixture
def sleepy_script(tmp_path) -> Path:
    script = tmp_path / "sleepy.py"
    script.write_text(SLEEPY_SCRIPT)
    return script


def test_bridge_starts_and_reports_running(sleepy_script):
    bridge = bridge_manager.ManagedBridge("sleepy", sleepy_script)
    assert not bridge.is_running
    assert bridge.uptime_seconds is None

    bridge.start()
    try:
        assert bridge.is_running
        assert bridge.uptime_seconds is not None
        assert bridge.uptime_seconds >= 0
    finally:
        bridge.stop()


def test_bridge_stop_actually_terminates_the_process(sleepy_script):
    bridge = bridge_manager.ManagedBridge("sleepy", sleepy_script)
    bridge.start()
    pid = bridge._process.pid
    bridge.stop()

    assert not bridge.is_running
    assert bridge.uptime_seconds is None
    # The OS process itself is actually gone, not just forgotten locally.
    with pytest.raises(ProcessLookupError):
        import os
        os.kill(pid, 0)


def test_bridge_restart_replaces_the_process_and_resets_uptime(sleepy_script):
    bridge = bridge_manager.ManagedBridge("sleepy", sleepy_script)
    bridge.start()
    first_pid = bridge._process.pid
    time.sleep(0.2)

    bridge.restart()

    try:
        assert bridge.is_running
        assert bridge._process.pid != first_pid
        assert bridge.uptime_seconds < 0.2
    finally:
        bridge.stop()


def test_starting_an_already_running_bridge_is_a_noop(sleepy_script):
    bridge = bridge_manager.ManagedBridge("sleepy", sleepy_script)
    bridge.start()
    try:
        pid = bridge._process.pid
        bridge.start()  # already running -- must not spawn a second process
        assert bridge._process.pid == pid
    finally:
        bridge.stop()


def test_stopping_a_never_started_bridge_is_a_noop(tmp_path):
    bridge = bridge_manager.ManagedBridge("sleepy", tmp_path / "sleepy.py")
    bridge.stop()  # must not raise
    assert not bridge.is_running


def test_starting_a_bridge_whose_script_is_missing_is_a_noop(tmp_path):
    bridge = bridge_manager.ManagedBridge("ghost", tmp_path / "does_not_exist.py")
    bridge.start()
    assert not bridge.is_running


def test_discover_bridges_only_returns_scripts_that_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge_manager, "REPO_ROOT", tmp_path)
    (tmp_path / "moodr_to_m8_bridge.py").write_text("")
    # m8_to_moodr_transport_bridge.py deliberately left absent.

    bridges = bridge_manager.discover_bridges()

    assert [b.name for b in bridges] == ["m00Dr -> M8 (notes)"]


def test_format_duration():
    assert bridge_manager.format_duration(7) == "7s"
    assert bridge_manager.format_duration(72) == "1m 12s"
    assert bridge_manager.format_duration(3725) == "1h 02m"


def test_format_status_not_running(tmp_path):
    bridge = bridge_manager.ManagedBridge("sleepy", tmp_path / "sleepy.py")
    assert bridge_manager.format_status(bridge) == "Not running"


def test_format_status_running(sleepy_script):
    bridge = bridge_manager.ManagedBridge("sleepy", sleepy_script)
    bridge.start()
    try:
        status = bridge_manager.format_status(bridge)
        assert status.startswith("Running since ")
    finally:
        bridge.stop()
