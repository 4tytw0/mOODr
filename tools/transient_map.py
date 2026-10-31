# /// script
# dependencies = ["numpy", "scipy", "soundfile"]
# ///
"""Tags each break in voices/breaks/breaks.json with its transient map: per bar, two
bytes, one bit per 16th, set where a transient starts on that 16th.

A 16th's bit is 1 when the strongest onset within GRID_TOLERANCE of a 16th
of its start (wrapping round the loop, so a hit just early for the downbeat
counts) reaches THRESHOLD of the break's REFERENCE percentile 16th: not the
loudest, so one huge hit can't hide the rest of the break. Onsets are
build_breaks.py's (5 ms energy rises), and the breaks are already cut to
whole bars on their downbeat, so bar b's 16ths are its 16 equal slices, as
breaks.py plays them. The first 16th is the top bit, so the hex reads like a
step sequencer: "8A28" is 1000 1010 0010 1000. Written as "transients": one
4-digit hex string per bar.

    uv run tools/transient_map.py [--dry-run] [--threshold 0.5]
"""
import argparse, json, pathlib, sys
import numpy as np, soundfile as sf

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_breaks import GRID_TOLERANCE, INDEX, OUT_DIR, onsets  # noqa: E402

THRESHOLD, REFERENCE = 0.5, 90   # a 16th counts at half the 90th-percentile 16th's onset


def transient_map(audio, bars, threshold=THRESHOLD):
    """One 16-bit int per bar, the first 16th as the top bit."""
    flux = onsets(audio.mean(axis=1))
    steps = bars * 16
    sixteenth = len(flux) / steps
    reach = max(1, round(GRID_TOLERANCE * sixteenth))
    peaks = np.array([flux[np.arange(round(k * sixteenth) - reach, round(k * sixteenth) + reach + 1) % len(flux)].max()
                      for k in range(steps)])
    hits = peaks >= threshold * np.percentile(peaks, REFERENCE)
    return [sum(1 << (15 - i) for i in range(16) if hits[b * 16 + i]) for b in range(bars)]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--threshold", type=float, default=THRESHOLD, help="share of the REFERENCE percentile 16th that counts")
    p.add_argument("--dry-run", action="store_true", help="print the maps, don't write breaks.json")
    args = p.parse_args()
    index = json.loads(INDEX.read_text())
    for entry in index["breaks"]:
        audio, _ = sf.read(OUT_DIR / entry["file"], always_2d=True, dtype="float64")
        tags = [f"{bits:04X}" for bits in transient_map(audio, entry["bars"], args.threshold)]
        entry["transients"] = tags
        grids = "  ".join(f"{int(t, 16):016b}".replace("0", ".").replace("1", "x") for t in tags)
        print(f"{entry['file']}  {' '.join(tags):<19}  {grids}  {entry['name']}")
    if not args.dry_run:
        INDEX.write_text(json.dumps(index, indent=2) + "\n")


if __name__ == "__main__":
    main()
