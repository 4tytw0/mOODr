"""The SunVox library, loaded for offline rendering: shared by sunvox303.py
and sunvoxdrums.py.

The library itself isn't in git: synths/sunvox_lib/ holds sunvox.so (Linux
x86_64) or sunvox.dylib (macOS), from warmplace.ru/soft/sunvox
(sunvox_lib-*.zip).
"""
import ctypes, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE / "synths" / "sunvox_lib" / ("sunvox.dylib" if sys.platform == "darwin" else "sunvox.so")

SV_INIT_FLAG_NO_DEBUG_OUTPUT = 1 << 0
SV_INIT_FLAG_USER_AUDIO_CALLBACK = 1 << 1
SV_INIT_FLAG_AUDIO_FLOAT32 = 1 << 3
SV_INIT_FLAG_ONE_THREAD = 1 << 4
NOTECMD_NOTE_OFF, NOTECMD_ALL_NOTES_OFF = 128, 129


def open_lib(sample_rate: int):
    """Loads and initialises the library: float32 stereo, rendered on demand
    with sv_audio_callback (no audio device of its own), events and rendering
    on one thread. Returns the ctypes handle."""
    sv = ctypes.CDLL(str(LIB))
    sv.sv_load.argtypes = [ctypes.c_int, ctypes.c_char_p]
    sv.sv_find_module.argtypes = [ctypes.c_int, ctypes.c_char_p]
    sv.sv_audio_callback.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_uint32]
    sv.sv_get_ticks.restype = ctypes.c_uint32
    sv.sv_get_module_ctl_name.restype = ctypes.c_char_p
    flags = (SV_INIT_FLAG_NO_DEBUG_OUTPUT | SV_INIT_FLAG_USER_AUDIO_CALLBACK
             | SV_INIT_FLAG_AUDIO_FLOAT32 | SV_INIT_FLAG_ONE_THREAD)
    if sv.sv_init(None, sample_rate, 2, flags) < 0:
        raise RuntimeError("sv_init failed")
    return sv


def load(sv, slot: int, path: Path) -> None:
    """Opens `slot` and loads a project into it. Events sent to it are
    applied at the start of the next sv_audio_callback (without
    sv_set_event_t they are timestamped into the future and never play)."""
    sv.sv_open_slot(slot)
    if sv.sv_load(slot, str(path).encode()) != 0:
        raise RuntimeError(f"can't load {path}")
    sv.sv_set_event_t(slot, 1, 0)


def find_module(sv, slot: int, name: str, path: Path) -> int:
    """A module's number + 1, the form sv_send_event takes."""
    module = sv.sv_find_module(slot, name.encode())
    if module < 0:
        raise RuntimeError(f"no module {name!r} in {path}")
    return module + 1
