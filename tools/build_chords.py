# /// script
# dependencies = ["numpy", "soundfile"]
# ///
"""Builds synths/chords.sf2: sustained multisampled patches for m00Dr's chords.

Each preset is one multisample set from the "Strings Custom Kit" folder: every
file is one note, keeps the loop points stored in its WAV `smpl` chunk (so a
held chord sustains), and covers the keys halfway to its neighbours. A file's
pitch class comes from its name; the octave and fine tuning come from the
detected pitch, since the sets don't agree on octave numbering. Files named
-L/-R or (L)/(R) are panned hard left/right as a stereo pair.

    uv run tools/build_chords.py synths/chords.sf2 [KIT_DIR]

The ROMpler Chords and DX-7 folders aren't used: each of their files is a whole
chord, so m00Dr's voicings would stack chords on chords.
"""
import pathlib, re, struct, sys
import numpy as np, soundfile as sf

PC = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}
NAMES = "C C# D D# E F F# G G# A A# B".split()

# (preset name, filename regex). Group "n" is the note letter with an optional
# #, b or + (sharp); a group "midi" is a MIDI note number instead.
NOTE = r"(?P<n>[A-Ga-g][#b+]?)"
SETS = [
    ("OBX strings",      rf"^OBXString{NOTE}\d+\.wav$"),
    ("P5 strings",       rf"^P5 Strings {NOTE}\d+\.wav$"),
    ("Arco strings",     rf"^Arco String {NOTE}\d+\.wav$"),
    ("Section strings",  rf"^{NOTE}\d .*(MIX|MX|VA\+VN)\.wav$"),
    ("Stereo strings",   rf"^{NOTE}\d \((L|R)\)\.wav$"),
    ("Trans strings",    rf"^TRNSTR4-{NOTE}\d-(L|R)\.wav$"),
    ("Storm arco",       r"^STORMARC(?P<midi>\d+)-L\.wav$"),
    ("Viola section",    rf"^SWVla {NOTE}_?\d+c\.wav$"),
    ("Solo violin",      rf"^ ?Vl {NOTE}_?\d+\.wav$"),
    ("CES strings",      rf"^CES {NOTE} ?\d ?LN\.wav$"),
    ("CDG strings",      rf"^{NOTE}\d\.wav$"),
    ("RnB strings",      rf"^New RnB_{NOTE}\d7f7f7f\.wav$"),
    ("Tremolo violins",  rf"^Tremo Violins ?{NOTE}\d\.wav$"),
    ("Organ",            rf"^Organ {NOTE}\d\.wav$"),
    ("Acoustic guitar",  rf"^Ac Gtr {NOTE}\d\.wav$"),
]
ATTEN = 200          # cB nominal; FluidSynth applies 0.4x (~8 dB), so held chords don't clip
ATTACK, RELEASE = -6000, -1200   # timecents: ~30 ms attack, ~0.5 s release


def loop_points(path):
    b = path.read_bytes(); i = 12
    while i + 8 <= len(b):
        tag, n = b[i:i + 4], struct.unpack("<I", b[i + 4:i + 8])[0]
        if tag == b"smpl":
            d = b[i + 8:i + 8 + n]
            if struct.unpack("<I", d[28:32])[0]:
                return struct.unpack("<II", d[44:52])
        i += 8 + n + (n & 1)
    return None


def detect(x, sr):
    """Detected pitch in fractional MIDI (autocorrelation over up to 0.5 s)."""
    x = x[: int(0.5 * sr)] - x[: int(0.5 * sr)].mean(); n = len(x)
    c = np.fft.irfft(np.abs(np.fft.rfft(x, 2 * n)) ** 2)[:n]
    lo, hi = int(sr / 2500), min(int(sr / 30), n - 2)
    k = lo + int(np.argmax(c[lo:hi]))
    if c[0] <= 0 or not 0 < k < n - 1:
        return float("nan")
    a, b_, d = c[k - 1], c[k], c[k + 1]; k = k + 0.5 * (a - d) / (a - 2 * b_ + d)
    return 69 + 12 * np.log2(sr / k / 440)


def zone(path, m):
    x, sr = sf.read(path, always_2d=True)
    mono = x.mean(1)
    loop = loop_points(path)
    if not loop or loop[1] - loop[0] < 8:
        return None
    seg = mono[loop[0]:loop[1] + 1] if loop[1] - loop[0] > sr // 20 else mono
    det = detect(seg, sr)
    if m.groupdict().get("midi"):
        pc = int(m["midi"]) % 12
    else:
        n = m["n"].lower(); pc = (PC[n[0]] + (1 if n[1:] in ("#", "+") else -1 if n[1:] == "b" else 0)) % 12
    if not np.isfinite(det):                    # no clear period: take the name's octave as written
        octave = re.search(r"(\d)", path.name[m.end("n") if m.groupdict().get("n") else 0:])
        det = pc + 12 * (int(octave[1]) + 1) if octave else 60 + pc
    # Octave from the detection, pitch class from the name.
    root = pc + 12 * round((det - pc) / 12)
    off = det - root
    if abs(off) > 0.6:                           # name and pitch disagree: trust the ear
        print(f"    ! {path.name}: name says {NAMES[pc]}, detected {det:.2f}; using detection")
        root, off = round(det), det - round(det)
    pcm = mono / (np.abs(mono).max() + 1e-12) * 0.9
    pan = -500 if re.search(r"(-L|\(L\))\.wav$", path.name) else 500 if re.search(r"(-R|\(R\))\.wav$", path.name) else 0
    return {"pcm": (pcm * 32767).astype("<i2"), "sr": sr, "root": root, "cents": round(-off * 100),
            "loop": loop, "pan": pan, "name": path.stem}


# --- SoundFont writer -------------------------------------------------------
def ck(tag, data): return tag + struct.pack("<I", len(data)) + data + (b"\0" if len(data) % 2 else b"")
def lst(tag, *chunks): return ck(b"LIST", tag + b"".join(chunks))
def name(s): return s.encode()[:19].ljust(20, b"\0")


def write_sf2(out, presets):
    smpl = b""; shdr = b""; inst = b""; ibag = b""; igen = b""; phdr = b""; pbag = b""; pgen = b""
    nsamp = 0
    for p_i, (pname, zones) in enumerate(presets):
        inst += name(pname) + struct.pack("<H", len(ibag) // 4)
        roots = sorted({z["root"] for z in zones})
        # Each root covers up to halfway to its neighbours; the ends reach the keyboard's.
        edges = [0] + [(a + b + 1) // 2 for a, b in zip(roots, roots[1:])] + [128]
        rng = {r: (edges[i], edges[i + 1] - 1) for i, r in enumerate(roots)}
        for z in zones:
            start = len(smpl) // 2
            smpl += z["pcm"].tobytes() + b"\0" * 92
            end = start + len(z["pcm"])
            ls, le = z["loop"]
            shdr += name(z["name"]) + struct.pack("<IIIIIBbHH", start, end, start + ls, start + le + 1,
                                                  z["sr"], z["root"], z["cents"], 0, 1)
            lo, hi = rng[z["root"]]
            ibag += struct.pack("<HH", len(igen) // 4, 0)
            igen += struct.pack("<HBB", 43, lo, hi)                    # keyRange (first)
            igen += struct.pack("<Hh", 34, ATTACK) + struct.pack("<Hh", 38, RELEASE)
            igen += struct.pack("<HH", 48, ATTEN)
            if z["pan"]:
                igen += struct.pack("<Hh", 17, z["pan"])
            igen += struct.pack("<HH", 54, 1)                           # loop continuously
            igen += struct.pack("<HH", 53, nsamp)                       # sampleID (last)
            nsamp += 1
        phdr += name(pname) + struct.pack("<HHHIII", p_i, 0, p_i, 0, 0, 0)
        pbag += struct.pack("<HH", p_i, 0)
        pgen += struct.pack("<HH", 41, p_i)
    k = len(presets)
    inst += name("EOI") + struct.pack("<H", len(ibag) // 4)
    ibag += struct.pack("<HH", len(igen) // 4, 0); igen += b"\0" * 4
    phdr += name("EOP") + struct.pack("<HHHIII", 0, 0, k, 0, 0, 0)
    pbag += struct.pack("<HH", k, 0); pgen += b"\0" * 4
    shdr += name("EOS") + b"\0" * 26
    body = b"sfbk" + lst(b"INFO", ck(b"ifil", struct.pack("<HH", 2, 1)), ck(b"isng", b"EMU8000\0"),
                         ck(b"INAM", b"m800Dr chords\0")) \
        + lst(b"sdta", ck(b"smpl", smpl)) \
        + lst(b"pdta", ck(b"phdr", phdr), ck(b"pbag", pbag), ck(b"pmod", b"\0" * 10), ck(b"pgen", pgen),
              ck(b"inst", inst), ck(b"ibag", ibag), ck(b"imod", b"\0" * 10), ck(b"igen", igen), ck(b"shdr", shdr))
    pathlib.Path(out).write_bytes(ck(b"RIFF", body))


def main():
    out = sys.argv[1]
    src = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else
                       pathlib.Path.home() / "Documents/PRODUCTION/SAMPLES/Synths/Strings Custom Kit")
    files = sorted(src.glob("*.wav"))
    presets = []
    for pname, pat in SETS:
        zones = [z for f in files if (m := re.match(pat, f.name)) and (z := zone(f, m))]
        # One zone per root (per side, for stereo pairs): two takes on a key would double it.
        zones = list({(z["root"], z["pan"]): z for z in zones}.values())
        if not zones:
            print(f"  (no files for {pname})"); continue
        roots = sorted(z["root"] for z in zones)
        print(f"preset {len(presets):2}  {pname:16} {len(zones):2} zones, roots "
              + " ".join(f"{NAMES[r % 12]}{r // 12 - 1}" for r in roots))
        presets.append((pname, zones))
    write_sf2(out, presets)
    print(f"wrote {out}: {len(presets)} presets, {pathlib.Path(out).stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
