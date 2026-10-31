# /// script
# dependencies = ["numpy", "scipy", "soundfile"]
# ///
"""Picks 64 drum breaks for breaks.py, one per hexagram, and writes them cut
to whole bars, ready to slice into 16ths.

The breaks come from the sample packs in SOURCES (classic funk/soul breaks,
MPC and vintage breaks, jungle loops); --games uses the GAMEBREAKS pack
instead. Neither is consistently trimmed to a loop, so each file is
analysed rather than trusted:

  * tempo: the period whose 1, 2, 4 and 16 16ths line up best in the onset
    envelope's autocorrelation (70-180 bpm), nudged towards a tempo in the
    filename when it has one;
  * grid: the 16th-note phase that catches the most onset energy within
    GRID_TOLERANCE of a 16th. Live breaks swing, so this only rejects what
    isn't on a grid at all (under MIN_GRID);
  * downbeat: the 16th whose bar lines carry the most kick.

A file that is a whole number of bars at that tempo is a loop and is turned
to start on its downbeat; anything else is cut to BARS bars from its first
downbeat (fewer if it's short). Only full kits are kept (kick, snare and hat
energy all present). The canonical breaks in PINNED come first (when they
pass); the rest of the COUNT are picked round-robin across the packs, each
pack's best-gridded first, so live breaks aren't crowded out by programmed
loops (which always grid tighter). They're written in tempo order, resampled to 48 kHz at the same loudness, as
synths/breaks/NN.wav with synths/breaks.json (name, pack, bars, tempo).
The audio is git-ignored (sampled breaks): build it on the machine that has
the packs and copy synths/breaks/ over.

    uv run tools/build_breaks.py [--games]
"""
import json, pathlib, re, subprocess, sys, tempfile
import numpy as np, soundfile as sf
from scipy.signal import butter, resample_poly, sosfilt

SR, HOP = 48000, 240                 # onset frames of 5 ms
COUNT, BARS = 64, 2
BPM_RANGE = (70.0, 180.0)
GRID_TOLERANCE, MIN_GRID = 0.2, 0.45  # of a 16th; share of onset energy
RMS_DB, PEAK_DB = -18.0, -1.0
PRODUCTION = pathlib.Path.home() / "Documents/PRODUCTION"
BREAKS = PRODUCTION / "SAMPLES/Drums/Breaks"
SOURCES = {
    "Classic Breaks": BREAKS / "Rhythm Labs/Classic Breaks",
    "MPC Breaks": BREAKS / "Rhythm Labs/MPC Breaks",
    "Vintage Breaks 6": BREAKS / "Vintage Breaks Vol 6",
    "Reworked Breaks": BREAKS / "Reworked Breaks",
    "Varispeed Breaks": BREAKS / "MusicRadar Breaks Varispeed",
    "Classic Jungle 1": BREAKS / "Acid DRUM N BASS The Clasic Breaks Vol 1/MAINLOOP",
    "Classic Jungle 2": BREAKS / "Acid Drum n Bass The Classic Breaks Vol 2/mainloop",
    "DnB Breaks": BREAKS / "dnb-breaks-mr",
    "Tape Breaks": PRODUCTION / "SAMPLES/w:/uhlectronics/tapeBreaks",
}
# The breaks everyone sampled: always in the set if they're full kits (a
# swung live break can grid under MIN_GRID). Matched against the file name.
PINNED = ["Amen, Brother (ver.1)", "Lyn Collins - Think (part1)", "Funky President",
          "When The Levee Breaks", "I Know You Got Soul", "Hot Pants", "Humpty Dump",
          "Crazy Legs", "Kool Is Back", "Take The Money And Run (part1)", "Funky Worm",
          "Use Me", "Fresh", "Funky Donkey (part1)"]
GAMES = {"GAMEBREAKS": PRODUCTION / "SAMPLES/GAMEBREAKS/gamebreaks_raw"}
HERE = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR, INDEX = HERE / "synths" / "breaks", HERE / "synths" / "breaks.json"
BANDS = {name: butter(4, f, kind, fs=SR, output="sos") for name, f, kind in
         (("low", 150, "lowpass"), ("mid", (180, 1200), "bandpass"), ("high", 6000, "highpass"))}


def read(path):
    try:
        audio, rate = sf.read(path, always_2d=True, dtype="float64")
    except RuntimeError:   # e.g. AIFF-C: let macOS decode it
        tmp = pathlib.Path(tempfile.mkdtemp()) / "x.wav"
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16", str(path), str(tmp)],
                       check=True, capture_output=True)
        audio, rate = sf.read(tmp, always_2d=True, dtype="float64")
    audio = audio[:, :2] if audio.shape[1] >= 2 else np.repeat(audio, 2, axis=1)
    return resample_poly(audio, SR, rate, axis=0) if rate != SR else audio


def onsets(mono):
    frames = len(mono) // HOP
    energy = np.sqrt((mono[:frames * HOP].reshape(frames, HOP) ** 2).mean(axis=1))
    return np.maximum(np.diff(np.log(energy + 1e-4), prepend=0.0), 0.0) * energy


def tempo_hint(stem):
    match = re.search(r"(?:drm|_|^|\()(\d{2,3})(?:\s*bpm|_|\)|$)", stem, re.I)
    value = float(match.group(1)) if match else None
    return value if value and BPM_RANGE[0] <= value <= BPM_RANGE[1] else None


def analyse(audio, hint=None):
    """(bpm, grid fit, first downbeat in samples)."""
    mono = audio.mean(axis=1)
    flux, kick = onsets(mono), onsets(sosfilt(BANDS["low"], mono))
    fps, frames = SR / HOP, np.arange(len(flux))
    centred = flux - flux.mean()
    ac = np.correlate(centred, centred, "full")[len(flux) - 1:]
    lags = np.arange(len(ac))

    def periodicity(bpm):
        sixteenth = fps * 15 / bpm
        score = sum(np.interp(k * sixteenth, lags, ac) * w for k, w in ((2, .5), (4, 1), (8, 1), (16, 1.5)))
        near = hint and min(abs(bpm - hint), abs(2 * bpm - hint), abs(bpm - 2 * hint)) < 2
        return score * (1.5 if near else 1)
    bpm = max(np.arange(BPM_RANGE[0], BPM_RANGE[1] + 0.01, 0.25), key=periodicity)
    sixteenth = fps * 15 / bpm

    def fit(phase):
        off = np.abs((frames - phase + sixteenth / 2) % sixteenth - sixteenth / 2)
        return flux[off <= GRID_TOLERANCE * sixteenth].sum() / (flux.sum() + 1e-12)
    phase = max(np.linspace(0, sixteenth, 24, endpoint=False), key=fit)

    def kick_on_bar_lines(k):
        starts = np.arange(phase + k * sixteenth, len(kick) - 2, 16 * sixteenth)
        return sum(kick[int(s):int(s) + 3].sum() for s in starts)
    downbeat = phase + max(range(16), key=kick_on_bar_lines) * sixteenth
    return float(bpm), float(fit(phase)), int(downbeat * HOP)


def cut(audio, bpm, downbeat):
    """Whole bars from the downbeat: (audio, bars), or None if under a bar."""
    bar = SR * 240 / bpm
    whole = len(audio) / bar
    if abs(whole - round(whole)) < 0.06 and round(whole) >= 1:   # a loop: turn it to the downbeat
        bars = min(round(whole), 4)
        looped = np.roll(audio, -downbeat, axis=0)
        return looped[:int(round(bars * bar))], bars
    bars = min(BARS, int((len(audio) - downbeat) / bar))
    if bars < 1:
        return None
    return audio[downbeat:downbeat + int(round(bars * bar))], bars


def full_kit(audio):
    mono = audio.mean(axis=1)
    level = {k: np.sqrt((sosfilt(s, mono) ** 2).mean()) for k, s in BANDS.items()}
    total = sum(level.values()) + 1e-12
    return level["low"] / total > 0.12 and level["mid"] / total > 0.12 and level["high"] / total > 0.02


def level(audio):
    audio = audio * 10 ** (RMS_DB / 20) / (np.sqrt((audio ** 2).mean()) + 1e-12)
    return audio * min(1.0, 10 ** (PEAK_DB / 20) / np.abs(audio).max())


def main():
    sources = GAMES if "--games" in sys.argv else SOURCES
    candidates, seen = [], set()
    for pack, folder in sources.items():
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in (".wav", ".aif", ".aiff") or path.name.startswith("."):
                continue
            try:
                audio = read(path)
            except (RuntimeError, subprocess.CalledProcessError):
                continue
            key = audio[:SR].tobytes()
            if key in seen:        # the same loop in two packs
                continue
            seen.add(key)
            bpm, grid, downbeat = analyse(audio, tempo_hint(path.stem))
            loop = cut(audio, bpm, downbeat)
            pinned = any(pin in path.stem for pin in PINNED)   # known breaks skip MIN_GRID
            if loop is None or (grid < MIN_GRID and not pinned) or not full_kit(loop[0]):
                continue
            candidates.append((grid, pack, path.stem, loop[0], loop[1], bpm))
    by_pack = {}
    for c in sorted(candidates, key=lambda c: -c[0]):
        by_pack.setdefault(c[1], []).append(c)
    kept, per_pack = [], {}
    for pin in PINNED:
        for queue in by_pack.values():
            match = next((c for c in queue if pin in c[2]), None)
            if match:
                queue.remove(match); kept.append(match)
                per_pack[match[1]] = per_pack.get(match[1], 0) + 1
                break
        else:
            print(f"pinned break not usable: {pin}")
    while len(kept) < COUNT and any(by_pack.values()):
        for pack, queue in by_pack.items():
            if queue and len(kept) < COUNT:
                kept.append(queue.pop(0)); per_pack[pack] = per_pack.get(pack, 0) + 1
    kept.sort(key=lambda c: c[5])   # slot order by tempo
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("*.wav"):
        old.unlink()
    index = []
    for i, (grid, pack, name, audio, bars, bpm) in enumerate(kept):
        sf.write(OUT_DIR / f"{i:02d}.wav", level(audio).astype(np.float32), SR, subtype="PCM_16")
        index.append({"file": f"{i:02d}.wav", "name": name, "pack": pack, "bars": bars,
                      "bpm": round(bpm, 2), "grid": round(grid, 3)})
    INDEX.write_text(json.dumps({"breaks": index}, indent=2) + "\n")
    print(f"{len(candidates)} usable; kept {len(kept)}: " +
          ", ".join(f"{p} {n}" for p, n in sorted(per_pack.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()
