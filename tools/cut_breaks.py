# /// script
# dependencies = ["numpy", "scipy", "soundfile"]
# ///
"""Cuts loops from the sample catalog into breaks.py-ready breaks, for the roller.

Each FILE[@BPM] (BPM: Sononym's tempo, a hint) is analysed and cut the way
build_breaks.py does it: whole bars from its downbeat, levelled, 48 kHz.
Loops that aren't on a grid (MIN_GRID) or aren't a full kit are skipped. The
cuts are written to OUT as 0.wav, 1.wav, ... in argument order and described
on stdout as JSON, one entry per FILE: {"file", "bars", "bpm", "grid",
"transients"} for a cut, or {"skip": why}.

    uv run tools/cut_breaks.py --out DIR FILE[@BPM] ...
"""
import argparse, json, pathlib, sys
import numpy as np, soundfile as sf

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_breaks import MIN_GRID, SR, analyse, cut, full_kit, level, read  # noqa: E402
from transient_map import transient_map  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=pathlib.Path, required=True)
    p.add_argument("files", nargs="+", metavar="FILE[@BPM]")
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    for i, spec in enumerate(args.files):
        path, _, hint = spec.rpartition("@") if "@" in spec else (spec, "", "")
        try:
            audio = read(pathlib.Path(path))
        except Exception as e:   # an unreadable file is skipped, not fatal
            results.append({"skip": f"unreadable: {e}"[:120]})
            continue
        bpm, grid, downbeat = analyse(audio, float(hint) if hint else None)
        loop = cut(audio, bpm, downbeat)
        if loop is None:
            results.append({"skip": "under a bar"})
        elif grid < MIN_GRID:
            results.append({"skip": f"off the grid ({grid:.2f})"})
        elif not full_kit(loop[0]):
            results.append({"skip": "not a full kit"})
        else:
            audio, bars = level(loop[0]).astype(np.float32), loop[1]
            sf.write(args.out / f"{i}.wav", audio, SR, subtype="PCM_16")
            results.append({"file": f"{i}.wav", "bars": bars, "bpm": round(bpm, 2), "grid": round(grid, 3),
                            "transients": [f"{b:04X}" for b in transient_map(audio, bars)]})
    print(json.dumps(results))


if __name__ == "__main__":
    main()
