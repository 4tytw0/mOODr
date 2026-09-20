import os
import signal
import subprocess
import sys
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


# -- bridges started outside this app --------------------------------------


def test_parse_etime_handles_every_ps_format():
    assert bridge_manager.parse_etime("42") is None      # ps always has MM:SS at minimum
    assert bridge_manager.parse_etime("00:42") == 42
    assert bridge_manager.parse_etime("05:12") == 312
    assert bridge_manager.parse_etime("02:05:12") == 7512
    assert bridge_manager.parse_etime("03-02:05:12") == 3 * 86400 + 7512
    assert bridge_manager.parse_etime("  05:12  ") == 312
    assert bridge_manager.parse_etime("garbage") is None


def test_looks_like_bridge_process_matches_real_launch_shapes():
    name = "moodr_to_m8_bridge.py"
    assert bridge_manager.looks_like_bridge_process(
        "/usr/bin/python3 -u moodr_to_m8_bridge.py", name)
    assert bridge_manager.looks_like_bridge_process(
        "/repo/.venv/bin/Python3.12 moodr_to_m8_bridge.py", name)  # case-insensitive
    # Shebang launch: no "python" anywhere in the command line.
    assert bridge_manager.looks_like_bridge_process("./moodr_to_m8_bridge.py", name)


def test_uv_run_matches_the_interpreter_but_not_the_uv_wrapper():
    """`uv run python -u <script>` is two processes. Killing the real
    interpreter is what stops the bridge; the uv wrapper exits with it,
    and matching argv[0] "uv" would mean killing a launcher rather than
    the thing holding the MIDI port."""
    name = "moodr_to_m8_bridge.py"
    assert not bridge_manager.looks_like_bridge_process(
        "uv run python -u moodr_to_m8_bridge.py", name)
    assert bridge_manager.looks_like_bridge_process(
        "/Library/Frameworks/Python.framework/Versions/3.12/Resources/"
        "Python.app/Contents/MacOS/Python -u /repo/moodr_to_m8_bridge.py", name)


def test_looks_like_bridge_process_ignores_things_that_merely_mention_the_script():
    """This module kills what it matches, so a false positive here is a
    killed process the user cared about."""
    name = "moodr_to_m8_bridge.py"
    assert not bridge_manager.looks_like_bridge_process("nvim moodr_to_m8_bridge.py", name)
    assert not bridge_manager.looks_like_bridge_process("tail -f moodr_to_m8_bridge.py.log",
                                                        name)
    assert not bridge_manager.looks_like_bridge_process("grep -r moodr_to_m8_bridge.py .", name)
    assert not bridge_manager.looks_like_bridge_process("python -u some_other_bridge.py", name)
    # The regression that prompted the strict argv[0] test: a shell running
    # a one-liner mentioning both python and the script matched the loose
    # version, and stopping the bridge killed that shell.
    assert not bridge_manager.looks_like_bridge_process(
        "/bin/zsh -c python -u moodr_to_m8_bridge.py && echo done", name)
    assert not bridge_manager.looks_like_bridge_process(
        "/bin/sh -c cd /repo && python moodr_to_m8_bridge.py", name)


def test_scan_processes_returns_this_process(tmp_path):
    rows = bridge_manager.scan_processes()
    assert rows, "ps returned nothing at all"
    assert any(pid == os.getpid() for pid, _ppid, _etime, _command in rows)
    # Shape: every row is (int pid, int ppid, etime string, non-empty command).
    for pid, ppid, etime, command in rows:
        assert isinstance(pid, int) and isinstance(ppid, int) and etime and command


def test_ancestor_pids_includes_this_process_and_what_launched_it():
    rows = bridge_manager.scan_processes()
    chain = bridge_manager.ancestor_pids(rows)

    assert os.getpid() in chain
    assert os.getppid() in chain
    # Walks all the way up rather than stopping at the immediate parent.
    assert len(chain) > 2


def test_ancestor_pids_survives_a_ppid_cycle():
    """A malformed/racy ps table must not hang the scan in a loop."""
    rows = [(os.getpid(), 1234, "00:01", "python"),
            (1234, os.getpid(), "00:01", "python")]
    chain = bridge_manager.ancestor_pids(rows)
    assert chain == {os.getpid(), 1234}


def _spawn_orphan(script: Path) -> int:
    """Starts `script` detached, so it is NOT a child of this test process
    -- the shell that launches it exits immediately and the process is
    reparented to init. That's the situation the feature exists for (a
    bridge started in its own terminal), and it also means the kill can be
    observed properly: a killed child of a process that never waits on it
    lingers as a zombie and still answers to signal 0."""
    result = subprocess.run(
        ["/bin/sh", "-c", f'{sys.executable} -u "{script}" >/dev/null 2>&1 & echo $!'],
        capture_output=True, text=True, check=True)
    pid = int(result.stdout.strip())
    for _ in range(100):
        try:
            os.kill(pid, 0)
            return pid
        except ProcessLookupError:
            time.sleep(0.02)
    raise AssertionError("orphan process never appeared")


def _wait_for_external(bridge, expected: int, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        bridge.refresh_external()
        if len(bridge.external_processes) == expected:
            return
        time.sleep(0.05)
    raise AssertionError(
        f"expected {expected} external process(es), found "
        f"{[p.command for p in bridge.external_processes]}")


@pytest.fixture
def bridge_script(tmp_path) -> Path:
    """A sleep script named like a real bridge, so the process-table scan
    will recognise it."""
    script = tmp_path / "moodr_to_m8_bridge.py"
    script.write_text(SLEEPY_SCRIPT)
    return script


def test_bridge_finds_a_copy_it_did_not_start(bridge_script):
    pid = _spawn_orphan(bridge_script)
    bridge = bridge_manager.ManagedBridge("m8", bridge_script)
    try:
        assert not bridge.is_running  # nothing known until it scans
        _wait_for_external(bridge, 1)

        assert bridge.is_running
        assert not bridge.started_by_this_app
        assert bridge.external_processes[0].pid == pid
        assert bridge.uptime_seconds is not None
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_stop_kills_a_bridge_this_app_did_not_start(bridge_script):
    pid = _spawn_orphan(bridge_script)
    bridge = bridge_manager.ManagedBridge("m8", bridge_script)
    _wait_for_external(bridge, 1)

    bridge.stop()

    assert not bridge.is_running
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_restart_replaces_an_external_bridge_with_one_this_app_owns(bridge_script):
    pid = _spawn_orphan(bridge_script)
    bridge = bridge_manager.ManagedBridge("m8", bridge_script)
    _wait_for_external(bridge, 1)

    bridge.restart()
    try:
        assert bridge.is_running
        assert bridge.started_by_this_app, "restart should hand ownership to this app"
        assert bridge._process.pid != pid
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)  # the terminal-launched copy is gone
    finally:
        bridge.stop()


def test_a_bridge_this_app_started_is_not_also_counted_as_external(bridge_script):
    """Without excluding our own child, it would be found by the scan and
    then killed as though it belonged to someone else."""
    bridge = bridge_manager.ManagedBridge("m8", bridge_script)
    bridge.start()
    try:
        bridge.refresh_external()
        assert bridge.external_processes == ()
        assert bridge.started_by_this_app
    finally:
        bridge.stop()


def test_start_does_not_launch_a_duplicate_alongside_an_external_bridge(bridge_script):
    pid = _spawn_orphan(bridge_script)
    bridge = bridge_manager.ManagedBridge("m8", bridge_script)
    try:
        bridge.start()
        assert bridge._process is None, "started a second copy fighting the first"
        assert bridge.is_running
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        bridge.stop()


def test_format_status_says_when_a_bridge_was_started_outside_the_app(bridge_script):
    pid = _spawn_orphan(bridge_script)
    bridge = bridge_manager.ManagedBridge("m8", bridge_script)
    try:
        _wait_for_external(bridge, 1)
        status = bridge_manager.format_status(bridge)
        assert status.startswith("Running since ")
        assert status.endswith("(outside m00Dr)")
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_refresh_all_shares_one_scan_across_bridges(tmp_path, monkeypatch):
    scans = []
    real_scan = bridge_manager.scan_processes

    def counting_scan():
        scans.append(1)
        return real_scan()

    monkeypatch.setattr(bridge_manager, "scan_processes", counting_scan)
    bridges = [bridge_manager.ManagedBridge(f"b{i}", tmp_path / f"b{i}.py") for i in range(4)]

    bridge_manager.refresh_all(bridges)

    assert len(scans) == 1
