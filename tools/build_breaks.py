# /// script
# dependencies = ["numpy", "scipy", "soundfile"]
# ///
"""Picks 64 breaks from the GAMEBREAKS drum breaks for breaks.py, one per
hexagram, and writes them ready to slice.

Each break is assumed to be a seamless loop of 1, 2 or 4 bars of 4/4; the bar
count is the one that puts its tempo nearest TARGET_BPM inside BPM_RANGE.
Then its onsets are checked against that 16th-note grid: the score is the
share of onset energy that lands within GRID_TOLERANCE of a 16th. The
COUNT best-gridded breaks are kept, so slicing one into 16ths per bar cuts
between hits rather than through them. Each is resampled to 48 kHz, brought
to the same loudness (RMS_DB, peaks held under PEAK_DB) and written as
synths/breaks/NN.wav, with synths/breaks.json listing name, bars and tempo.
The audio is git-ignored (the pack has no license); rebuild it on a machine
that has the pack and copy synths/breaks/ over.

    uv run tools/build_breaks.py [BREAKS_DIR]   # default: gamebreaks_raw
"""
import json, pathlib, sys
import numpy as np, soundfile as sf
from scipy.signal import resample_poly

SR = 48000
COUNT = 64
BPM_RANGE, TARGET_BPM = (80.0, 180.0), 130.0
GRID_TOLERANCE = 0.015      # s
RMS_DB, PEAK_DB = -18.0, -1.0
HERE = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR, INDEX = HERE / "synths" / "breaks", HERE / "synths" / "breaks.json"


def load(path):
    audio, rate = sf.read(path, always_2d=True, dtype="float64")
    audio = audio[:, :2] if audio.shape[1] >= 2 else np.repeat(audio, 2, axis=1)
    return resample_poly(audio, SR, rate, axis=0) if rate != SR else audio


def onset_strength(mono, hop=240):
    """Positive change in a frame's energy (5 ms hops), and the hop length."""
    frames = len(mono) // hop
    energy = np.sqrt((mono[:frames * hop].reshape(frames, hop) ** 2).mean(axis=1))
    return np.maximum(np.diff(np.log(energy + 1e-4), prepend=0.0), 0.0) * energy, hop


def fit(audio):
    """(bars, bpm, grid score) for the bar count with the most plausible tempo."""
    seconds = len(audio) / SR
    bars = min((1, 2, 4), key=lambda n: abs(240 * n / seconds - TARGET_BPM))
    bpm = 240 * bars / seconds
    if not BPM_RANGE[0] <= bpm <= BPM_RANGE[1]:
        return bars, bpm, 0.0
    flux, hop = onset_strength(audio.mean(axis=1))
    times = np.arange(len(flux)) * hop / SR
    sixteenth = seconds / (bars * 16)
    off = np.abs((times + sixteenth / 2) % sixteenth - sixteenth / 2)
    return bars, bpm, float(flux[off <= GRID_TOLERANCE].sum() / (flux.sum() + 1e-12))


def level(audio):
    rms = np.sqrt((audio ** 2).mean())
    audio = audio * 10 ** (RMS_DB / 20) / (rms + 1e-12)
    peak = np.abs(audio).max()
    return audio * min(1.0, 10 ** (PEAK_DB / 20) / peak)


def main():
    src = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else \
        pathlib.Path.home() / "Documents/PRODUCTION/SAMPLES/GAMEBREAKS/gamebreaks_raw"
    scored = []
    for path in sorted(src.glob("*.wav")):
        audio = load(path)
        bars, bpm, score = fit(audio)
        scored.append((score, path, audio, bars, bpm))
    scored.sort(key=lambda s: -s[0])
    kept = scored[:COUNT]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index = []
    for i, (score, path, audio, bars, bpm) in enumerate(kept):
        name = f"{i:02d}.wav"
        sf.write(OUT_DIR / name, level(audio).astype(np.float32), SR, subtype="PCM_16")
        index.append({"file": name, "name": path.stem, "bars": bars, "bpm": round(bpm, 2),
                      "grid": round(score, 3)})
    INDEX.write_text(json.dumps({"breaks": index}, indent=2) + "\n")
    print(f"kept {len(kept)} of {len(scored)}: grid score {kept[-1][0]:.2f}..{kept[0][0]:.2f}, "
          f"{min(k[4] for k in kept):.0f}-{max(k[4] for k in kept):.0f} bpm -> {OUT_DIR}")


if __name__ == "__main__":
    main()
