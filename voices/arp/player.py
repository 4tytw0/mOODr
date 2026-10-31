# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "alsa-midi"]
# ///
"""m00Dr's arp on SunVox: the GAMEBREAKS stabs and the phase sine (in place of
FluidSynth's gamebreaks.sf2). m00Dr picks them as before, bank 1 programs 0-10;
patches.json (from build.py) lists them.

    uv run voices/arp/player.py                 # run (the m00dr-sunvoxarp user service does this)
    uv run voices/arp/player.py --demo x.wav    # every patch, offline
"""
import sys
from pathlib import Path

VOICE = Path(__file__).resolve().parent
sys.path.insert(0, str(VOICE.parent / "_sunvox"))
from bank import run  # noqa: E402

ARP_CHANNEL = 2   # 0-based: m00Dr's arp is MIDI ch3

if __name__ == "__main__":
    run(VOICE, ARP_CHANNEL, client="m00Dr-SunVoxArp", port="arp in", node="m00Dr-SunVoxArp")
