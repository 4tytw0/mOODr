# /// script
# dependencies = ["numpy", "scipy", "soundfile"]
# ///
"""Builds a General MIDI-style drum SoundFont from the GAMEBREAKS drum breaks.

Each break is sliced at its onsets into one-shot hits, every hit is sorted by
spectrum into a drum role (kick, snare, hats, toms, perc, crash), and the
cleanest hit per role wins. Preset 0 ("gamebreaks best") takes the best hit of
each role across all breaks; presets 1.. are kits cut from a single break each,
the KITS breaks that yield the most complete, cleanest kits. After those come
MIXES mixed kits: every role drawn at random from a different break among the
top candidates, so each kit is a new combination. Some mixes ("driven",
"extreme") take their kick, snare and toms from the distorted type1 folders
next to BREAKS_DIR. All presets are in bank 128 (drums); m00Dr picks one per
Roll with a program change on its drum channel.

    uv run tools/build_drumkit.py synths/gamedrums.sf2 [BREAKS_DIR] [--kits N] [--seed S]

BREAKS_DIR defaults to gamebreaks_raw: the type1/type2 versions are dulled
(spectral centroid ~2 kHz against ~6 kHz raw), which leaves no hats to find.
"""
import pathlib, random, struct, sys
import numpy as np, soundfile as sf
from scipy.signal import resample_poly

SR = 48000
KITS = 15
# Mixed kits: (name, folder the body comes from). The body roles (kick, snare,
# toms) come from that folder; everything else from the raw breaks, since the
# processed folders are too dull to yield hats.
MIXES = [("mix", None)] * 8 + [("driven", "gamebreaks_type1_driven")] * 4 \
    + [("extreme", "gamebreaks_type1_extreme")] * 4
BODY = ("kick", "snare", "tom_lo", "tom_hi")
POOL = 12                     # a mixed kit draws each role from this many best hits
SEED = 8
# role -> GM drum keys it is mapped to. Hats share an exclusive class so a
# closed hat chokes an open one, as on a real kit.
ROLES = {
    "kick":   [35, 36],
    "snare":  [38, 40],
    "rim":    [37, 39],          # side stick, clap: the brightest short snare-like hit
    "chat":   [42, 44],          # closed hat, pedal hat
    "ohat":   [46],
    "tom_lo": [41, 43, 45],
    "tom_hi": [47, 48, 50],
    "crash":  [49, 51, 52, 55, 57, 59],
    "perc":   [54, 56, 60, 61, 62, 63, 64, 75, 76, 77],
}
HAT_CLASS = 1
MAX_HIT = {"crash": 1.6, "ohat": 0.7}   # seconds kept per hit; others 0.45


def mono(path):
    x, sr = sf.read(path, always_2d=True)
    m = x.mean(1)
    return resample_poly(m, SR, sr) if sr != SR else m


def onsets(m):
    """Spectral-flux onsets, as sample indices."""
    hop, n = 256, 1024
    frames = np.lib.stride_tricks.sliding_window_view(np.pad(m, (n, n)), n)[::hop] * np.hanning(n)
    mag = np.log1p(100 * np.abs(np.fft.rfft(frames, axis=1)))
    flux = np.maximum(np.diff(mag, axis=0), 0).sum(1)
    thr = np.convolve(flux, np.ones(16) / 16, "same") * 1.5 + flux.mean() * 0.5
    peaks = [i for i in range(1, len(flux) - 1)
             if flux[i] > thr[i] and flux[i] >= flux[i - 1] and flux[i] >= flux[i + 1]]
    out = []
    for p in peaks:                                   # at least 60 ms apart
        s = max(p * hop - n // 2, 0)
        if not out or s - out[-1] > 0.06 * SR:
            out.append(s)
    return out


def features(hit):
    spec = np.abs(np.fft.rfft(hit[: int(0.12 * SR)] * np.hanning(min(len(hit), int(0.12 * SR))), 8192)) ** 2
    f = np.fft.rfftfreq(8192, 1 / SR)
    tot = spec.sum() + 1e-12
    env = np.abs(hit)
    peak = env.max() + 1e-12
    # decay: time until the envelope (5 ms smoothing) stays under -30 dB of peak
    sm = np.convolve(env, np.ones(240) / 240, "same")
    above = np.nonzero(sm > peak * 0.0316)[0]
    return {
        "centroid": float((spec * f).sum() / tot),
        "low": float(spec[f < 150].sum() / tot),
        "mid": float(spec[(f >= 150) & (f < 4000)].sum() / tot),
        "high": float(spec[f >= 6000].sum() / tot),
        "flat": float(np.exp(np.log(spec[(f > 100) & (f < 16000)] + 1e-20).mean())
                      / (spec[(f > 100) & (f < 16000)].mean() + 1e-20)),   # noisiness 0..1
        "decay": float(above[-1] / SR) if len(above) else 0.0,
        "peak": float(peak),
    }


def classify(ft):
    # Thresholds fit to the raw breaks' hits (chip drums: short decays, hats
    # are noise with most energy above 6 kHz, snares are mid noise).
    c, lo, hi, fl, dec = ft["centroid"], ft["low"], ft["high"], ft["flat"], ft["decay"]
    if lo > 0.6 and c < 600:
        return "kick"
    if hi > 0.4 and c > 4500:
        if dec > 0.35:
            return "crash"
        return "ohat" if dec > 0.15 else "chat"
    if fl > 0.1 and 1200 < c < 5000 and lo < 0.3:
        return "rim" if dec < 0.07 and c > 2500 else "snare"
    if fl < 0.05 and 0.15 < lo and c < 1500:
        return "tom_lo" if c < 450 else "tom_hi"
    return "perc"


def slices(path):
    """(role, quality, pcm) for each hit in one break."""
    m = mono(path)
    m = m / (np.abs(m).max() + 1e-12)
    on = onsets(m) + [len(m)]
    out = []
    for a, b in zip(on, on[1:]):
        hit = m[a:b]
        if len(hit) < 0.04 * SR:
            continue
        ft = features(hit)
        role = classify(ft)
        keep = int(MAX_HIT.get(role, 0.45) * SR)
        natural = int(ft["decay"] * SR) + int(0.02 * SR)
        hit = hit[: min(len(hit), keep, max(natural, int(0.06 * SR)))]
        # Quality: loud, rings out before the next onset (not cut off), and
        # strongly typed (a kick that is mostly low end, a hat that is mostly top).
        rang_out = 1.0 if natural <= b - a else (b - a) / natural
        typed = {"kick": ft["low"], "chat": ft["high"], "ohat": ft["high"], "crash": ft["high"],
                 "snare": ft["flat"] * 4, "rim": ft["flat"] * 4, "tom_lo": ft["low"],
                 "tom_hi": ft["mid"], "perc": 0.3}[role]
        q = ft["peak"] * rang_out * min(typed, 1.0)
        out.append((role, q, hit))
    return out


def finish(pcm):
    pcm = pcm / (np.abs(pcm).max() + 1e-12) * 0.9
    f = min(int(0.008 * SR), len(pcm) // 4)
    pcm[-f:] *= np.linspace(1, 0, f) ** 2               # declick the tail
    return (pcm * 32767).astype("<i2")


# --- SoundFont writer -------------------------------------------------------
def ck(tag, data): return tag + struct.pack("<I", len(data)) + data + (b"\0" if len(data) % 2 else b"")
def lst(tag, *chunks): return ck(b"LIST", tag + b"".join(chunks))
def name(s): return s.encode()[:19].ljust(20, b"\0")


def write_sf2(out, kits):
    """kits: [(preset name, {role: pcm})]"""
    smpl = b""; shdr = b""; inst = b""; ibag = b""; igen = b""; phdr = b""; pbag = b""; pgen = b""
    sample_ids = {}
    for kit_i, (kname, roles) in enumerate(kits):
        inst += name(kname) + struct.pack("<H", len(ibag) // 4)
        for role, pcm in roles.items():
            key = id(pcm)
            if key not in sample_ids:
                start = len(smpl) // 2
                smpl += pcm.tobytes() + b"\0" * 92
                end = start + len(pcm)
                sample_ids[key] = len(shdr) // 46
                shdr += name(f"{kname[:10]}_{role}") + struct.pack("<IIIIIBbHH", start, end, start, end, SR, 60, 0, 0, 1)
            for note in ROLES[role]:
                ibag += struct.pack("<HH", len(igen) // 4, 0)
                igen += struct.pack("<HBB", 43, note, note)             # keyRange
                igen += struct.pack("<Hh", 38, -1200 if role in ("crash", "ohat") else -2400)  # release
                if role in ("chat", "ohat"):
                    igen += struct.pack("<HH", 57, HAT_CLASS)          # exclusiveClass: hats choke
                igen += struct.pack("<HH", 58, 60)                      # overridingRootKey: no transposition
                igen += struct.pack("<HH", 53, sample_ids[key])          # sampleID (last)
        phdr += name(kname) + struct.pack("<HHHIII", kit_i, 128, kit_i, 0, 0, 0)
        pbag += struct.pack("<HH", kit_i, 0)
        pgen += struct.pack("<HH", 41, kit_i)
    k = len(kits)
    inst += name("EOI") + struct.pack("<H", len(ibag) // 4)
    ibag += struct.pack("<HH", len(igen) // 4, 0); igen += b"\0" * 4
    phdr += name("EOP") + struct.pack("<HHHIII", 0, 0, k, 0, 0, 0)
    pbag += struct.pack("<HH", k, 0); pgen += b"\0" * 4
    shdr += name("EOS") + b"\0" * 26
    body = b"sfbk" + lst(b"INFO", ck(b"ifil", struct.pack("<HH", 2, 1)), ck(b"isng", b"EMU8000\0"),
                         ck(b"INAM", b"gamebreaks drums\0\0")) \
        + lst(b"sdta", ck(b"smpl", smpl)) \
        + lst(b"pdta", ck(b"phdr", phdr), ck(b"pbag", pbag), ck(b"pmod", b"\0" * 10), ck(b"pgen", pgen),
              ck(b"inst", inst), ck(b"ibag", ibag), ck(b"imod", b"\0" * 10), ck(b"igen", igen), ck(b"shdr", shdr))
    pathlib.Path(out).write_bytes(ck(b"RIFF", body))


def pool(paths, roles):
    """role -> the POOL best (q, pcm, break) hits of `roles` across `paths`."""
    best = {r: [] for r in roles}
    for path in paths:
        for role, q, pcm in slices(path):
            if role in best:
                best[role].append((q, pcm, path.stem))
    for r in best:
        best[r].sort(key=lambda t: -t[0])
        # One hit per break, so a kit's roles really do come from different breaks.
        seen, top = set(), []
        for q, pcm, stem in best[r]:
            if stem not in seen:
                seen.add(stem); top.append((q, pcm, stem))
            if len(top) == POOL:
                break
        best[r] = top
    return best


def mixed_kits(src, rng):
    """[(name, {role: pcm}, {role: break})] for MIXES."""
    raw = pool(sorted(src.glob("*.wav")), ROLES)
    bodies = {None: raw}
    out, counts = [], {}
    for label, folder in MIXES:
        if folder not in bodies:
            bodies[folder] = pool(sorted((src.parent / folder).glob("*.wav")), BODY)
        counts[label] = counts.get(label, 0) + 1
        roles, origin = {}, {}
        for r in ROLES:
            cands = (bodies[folder] if r in BODY else raw).get(r) or raw.get(r)
            if not cands:
                continue
            # Favor the better hits without always taking the top one.
            q, pcm, stem = rng.choices(cands, weights=[len(cands) - i for i in range(len(cands))])[0]
            roles[r], origin[r] = finish(pcm), stem
        out.append((f"{label} {counts[label]}", roles, origin))
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opt = lambda flag, default: int(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else default
    kits_n, seed = opt("--kits", KITS), opt("--seed", SEED)
    for flag in ("--kits", "--seed"):
        if flag in sys.argv:
            args.remove(sys.argv[sys.argv.index(flag) + 1])
    out = args[0]
    src = pathlib.Path(args[1] if len(args) > 1 else
                       pathlib.Path.home() / "Documents/PRODUCTION/SAMPLES/GAMEBREAKS/gamebreaks_raw")
    best_all = {}                        # role -> (q, pcm, break)
    per_break = []                       # (score, break, {role: (q, pcm)})
    for path in sorted(src.glob("*.wav")):
        roles = {}
        for role, q, pcm in slices(path):
            if q > roles.get(role, (0,))[0]:
                roles[role] = (q, pcm)
            if q > best_all.get(role, (0,))[0]:
                best_all[role] = (q, pcm, path.stem)
        core = [r for r in ("kick", "snare", "chat") if r in roles]
        score = len(core) * 10 + len(roles) + sum(roles[r][0] for r in core)
        per_break.append((score, path.stem, roles))
    per_break.sort(key=lambda t: -t[0])

    kits = [("gamebreaks best", {r: finish(p) for r, (_, p, _) in best_all.items()})]
    print("preset  0  gamebreaks best: " + ", ".join(f"{r}<-{b}" for r, (_, _, b) in best_all.items()))
    for score, stem, roles in per_break[:kits_n]:
        if not all(r in roles for r in ("kick", "snare", "chat")):
            continue
        # Fill roles this break lacks from the best-of kit so every key sounds.
        full = {r: finish(p) for r, (_, p) in roles.items()}
        for r in ROLES:
            full.setdefault(r, kits[0][1][r]) if r in kits[0][1] else None
        kits.append((stem, full))
        print(f"preset {len(kits) - 1:2}  {stem:24} own: {', '.join(sorted(roles))}")
    for kname, roles, origin in mixed_kits(src, random.Random(seed)):
        kits.append((kname, roles))
        print(f"preset {len(kits) - 1:2}  {kname:24} " + ", ".join(f"{r}<-{b}" for r, b in origin.items()))
    write_sf2(out, kits)
    print(f"wrote {out}: {len(kits)} kits, {pathlib.Path(out).stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
