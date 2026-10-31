# /// script
# dependencies = ["numpy", "scipy", "soundfile"]
# ///
"""Prepares a library sample for fx.py's live slots: 48 kHz stereo, at most
--max-seconds long (faded out if cut), levelled like the breaks (RMS_DB, peak
under PEAK_DB). Prints {"seconds": ...} as JSON.

    uv run tools/prep_sample.py --out OUT.wav [--max-seconds 30] SRC
"""
import argparse, json, pathlib, sys
import numpy as np, soundfile as sf

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_breaks import SR, level, read  # noqa: E402

FADE = 0.05   # s


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=pathlib.Path, required=True)
    p.add_argument("--max-seconds", type=float, default=30.0)
    p.add_argument("src", type=pathlib.Path)
    args = p.parse_args()
    audio = read(args.src)[: int(args.max_seconds * SR)]
    fade = min(len(audio), int(FADE * SR))
    audio[len(audio) - fade:] *= np.linspace(1, 0, fade)[:, None]
    sf.write(args.out, level(audio).astype(np.float32), SR, subtype="PCM_16")
    print(json.dumps({"seconds": round(len(audio) / SR, 2)}))


if __name__ == "__main__":
    main()
