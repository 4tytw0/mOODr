#!/usr/bin/env python3
"""Writes synths/open303.carxp: a Carla rack of MIDI Channel Filter -> JC303.

Run on the desktop (needs carla, jc303-lv2 and a running PipeWire/JACK):
    python3 tools/build_open303.py [--dump]

Rack mode chains plugins in order, so the filter (passing only ACID_CHANNEL)
feeds JC303, whose stereo out is the rack's audio-out1/2. Uses Carla's own
backend to save the project instead of hand-writing its XML.
"""
import pathlib
import re
import sys
import time

sys.path.insert(0, "/usr/share/carla")
from carla_backend import (  # noqa: E402
    BINARY_NATIVE, ENGINE_OPTION_PATH_BINARIES, ENGINE_OPTION_PROCESS_MODE,
    ENGINE_PROCESS_MODE_CONTINUOUS_RACK, PLUGIN_INTERNAL, PLUGIN_LV2,
    CarlaHostDLL,
)

ACID_CHANNEL = 5  # m00Dr's ACID_CHANNEL (0-based 4) as a 1-based MIDI channel
JC303_URI = "http://github.com/midilab/JC303"
# JC303 starting patch by parameter index (see --dump). Its own defaults have
# Cutoff and EnvMod at 0, which sounds closed and dull. This one is deep and
# fuzzy: a saw (fuller low end than the square) through a low, driven filter
# with Bass Comp holding up the bottom, then JC303's overdrive mostly wet.
PATCH = {
    3: 0.65,   # Accent
    5: 0.70,   # Bass Comp
    6: 0.18,   # Cutoff
    7: 0.50,   # Decay
    8: 0.50,   # EnvMod
    10: 0.75,  # Filter Drive
    19: 0.80,  # Dry/Wet (overdrive)
    20: 0.60,  # Drive
    23: 0.62,  # Resonance
    39: 1.00,  # Switch Overdrive Mod: overdrive on
    41: 0.70,  # Volume
    42: 0.00,  # Waveform: saw
}
OUT = pathlib.Path(__file__).resolve().parent.parent / "synths" / "open303.carxp"


def dump(host, plugin):
    for i in range(host.get_parameter_count(plugin)):
        info = host.get_parameter_info(plugin, i)
        print(f"  [{i}] {info['name']!r} = {host.get_current_parameter_value(plugin, i)}")


def main():
    host = CarlaHostDLL("/usr/lib/carla/libcarla_standalone2.so", False)
    host.set_engine_option(ENGINE_OPTION_PROCESS_MODE, ENGINE_PROCESS_MODE_CONTINUOUS_RACK, "")
    host.set_engine_option(ENGINE_OPTION_PATH_BINARIES, 0, "/usr/lib/carla")
    if not host.engine_init("JACK", "m00Dr-Open303-build"):
        sys.exit(f"engine_init failed: {host.get_last_error()}")
    try:
        if not host.add_plugin(BINARY_NATIVE, PLUGIN_INTERNAL, "", "", "midichanfilter", 0, None, 0):
            sys.exit(f"adding midichanfilter failed: {host.get_last_error()}")
        if not host.add_plugin(BINARY_NATIVE, PLUGIN_LV2, "", "", JC303_URI, 0, None, 0):
            sys.exit(f"adding JC303 failed: {host.get_last_error()}")
        # midichanfilter has one on/off parameter per channel, 1..16 in order.
        for ch in range(16):
            host.set_parameter_value(0, ch, 1.0 if ch + 1 == ACID_CHANNEL else 0.0)
        for index, value in PATCH.items():
            host.set_parameter_value(1, index, value)
        for plugin in (0, 1):
            host.set_active(plugin, True)
        host.engine_idle()
        time.sleep(0.2)
        if "--dump" in sys.argv:
            for plugin in (0, 1):
                print(f"plugin {plugin}:")
                dump(host, plugin)
        if not host.save_project(str(OUT)):
            sys.exit(f"save_project failed: {host.get_last_error()}")
        # Carla snapshots the whole PipeWire graph into <ExternalPatchbay> and
        # would re-make those links on load; synths/links.sh owns wiring.
        OUT.write_text(re.sub(r"\n <ExternalPatchbay>.*?</ExternalPatchbay>", "",
                              OUT.read_text(), flags=re.S))
        print(f"wrote {OUT}")
    finally:
        host.engine_close()


if __name__ == "__main__":
    main()
