from unittest.mock import patch

from moodr import midi_status


def test_list_ports_returns_input_and_output_name_lists():
    inputs, outputs = midi_status.list_ports()
    assert isinstance(inputs, list)
    assert isinstance(outputs, list)


def test_reset_midi_server_is_a_noop_when_unsupported():
    with patch.object(midi_status, "MIDI_SERVER_RESET_SUPPORTED", False), \
         patch("subprocess.run") as run:
        assert midi_status.reset_midi_server() is False
        run.assert_not_called()


def test_reset_midi_server_kills_the_process_when_supported():
    # Patches subprocess.run rather than actually resetting the platform's
    # real MIDI service -- killing it is a real side effect on the
    # machine running the tests, not something a test suite should do.
    with patch.object(midi_status, "MIDI_SERVER_RESET_SUPPORTED", True), \
         patch("subprocess.run") as run:
        assert midi_status.reset_midi_server() is True
        run.assert_called_once()
        args, kwargs = run.call_args
        assert args[0] == ["killall", "-9", midi_status.MIDI_SERVER_PROCESS_NAME]
        assert kwargs.get("check") is False


def test_resolve_app_bundle_returns_the_bundle_itself_unchanged():
    assert midi_status.resolve_app_bundle("/Applications/m8c.app") == "/Applications/m8c.app"


def test_resolve_app_bundle_climbs_out_of_a_path_inside_the_bundle():
    # A file dialog can let a user select something *inside* a .app bundle
    # (e.g. drilling down to the actual Mach-O binary) instead of the
    # bundle itself -- this is the real-world case that caused "Open M8
    # UI" to `open` a plain folder (showing Finder) instead of launching
    # m8c, since the un-resolved path didn't end in ".app".
    path = "/Users/x/build/m8c.app/Contents/MacOS/m8c"
    assert midi_status.resolve_app_bundle(path) == "/Users/x/build/m8c.app"


def test_resolve_app_bundle_leaves_a_plain_path_unchanged():
    assert midi_status.resolve_app_bundle("/usr/local/bin/m8c") == "/usr/local/bin/m8c"
