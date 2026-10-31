# /// script
# requires-python = ">=3.11"
# dependencies = ["radiant-voices>=2.0.1.2.1.4.0", "numpy", "soundfile"]
# ///
"""Build the arp's SunVox patches: the GAMEBREAKS stabs and the phase sine, one
.sunvox per patch, plus patches.json for the player (voices/_sunvox/bank.py).

Each patch is a Sampler into the Output. radiant-voices writes that scaffold;
the SunVox library then loads the WAV into the Sampler (sv_sampler_load), sets
the loop, and saves the project, so the sample lives inside the .sunvox and the
file opens on its own in the SunVox app.

Program numbers match m00Dr's arp sounds on bank 1 (what FluidSynth's
gamebreaks.sf2 used to be): 0-9 the stabs, 10 the phase sine.

Run: uv run voices/arp/build.py [STABS_DIR]   # default ~/samples/GAMEBREAKS/gamestabs
"""
import ctypes, json, subprocess, sys, tempfile
from pathlib import Path

from rv.api import Project, m

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent / "_sunvox"))
from sunvoxlib import open_lib  # noqa: E402
import numpy as np, soundfile as sf  # noqa: E402


# Pitch detection, copied from tools/build_sf2.py (the old soundfont build).
def f0(x, sr):
    x = x - x.mean(); n = len(x)
    c = np.fft.irfft(np.abs(np.fft.rfft(x, 2*n))**2)[:n]
    lo, hi = int(sr/2000), int(sr/40)
    k = lo + np.argmax(c[lo:hi]); conf = c[k]/c[0]
    a, b, d = c[k-1], c[k], c[k+1]; k = k + 0.5*(a-d)/(a-2*b+d)
    return sr/k, conf

def detect(p, loop=False):
    x, sr = sf.read(p, always_2d=True); m = x.mean(1)
    if not loop: on = np.argmax(np.abs(m) > 0.01*np.abs(m).max()); m = m[on:]
    m = m/np.abs(m).max()*0.9
    if not loop: f = int(0.01*sr); m[-f:] *= np.linspace(1, 0, f)  # declick the tail
    pk = int(np.argmax(np.abs(m)))
    fr = [f0(m[i:i+4096], sr) for i in range(pk, max(pk+1, len(m)-4096), 1024)]
    fs = [f for f, c in fr if c > 0.5] or [max(fr, key=lambda t: t[1])[0]]
    midi = 69+12*np.log2(np.median(fs)/440); root = round(midi)
    return (m*32767).astype('<i2'), sr, root, round(100*(root-midi))

STABS = ["smet_nor3", "toa_f", "toa_fm2", "toa_lif", "toa_ns", "toa_oue", "tyr_sav", "tyr_sav2", "vk_cfh2", "vk_sos"]
NUDGE_CENTS = {"toa_lif": -18}   # tuning nudge, as in the old soundfont build (build_gamebreaks.sh)
# Level: each patch is rendered once (MIDI 60, velocity 100) and its Sampler volume
# (0..512, 256 = unity) set so that note peaks here. Leaves headroom for chords.
TARGET_PEAK = 0.3


def scaffold(path, name):
    p = Project()
    p.name = name
    s = p.new_module(m.Sampler, name="Sampler")
    s >> p.output
    with open(path, "wb") as f:
        p.write_to(f)


BUF = np.zeros((2048, 2), dtype="<f4")


def render_peak(sv, mod, seconds=0.8):
    """Peak of MIDI 60 at velocity 100 on this patch (library note 61), then silence."""
    sv.sv_set_event_t(0, 1, 0)
    sv.sv_send_event(0, 0, 61, 101, mod + 1, 0, 0); peak = 0.0
    for _ in range(int(seconds * 44100 / 2048)):
        sv.sv_audio_callback(BUF.ctypes.data, 2048, 0, sv.sv_get_ticks()); peak = max(peak, float(np.abs(BUF).max()))
    sv.sv_send_event(0, 0, 129, 0, 0, 0, 0)
    for _ in range(30): sv.sv_audio_callback(BUF.ctypes.data, 2048, 0, sv.sv_get_ticks())
    return peak


def build(stabs_dir: Path):
    sv = open_lib(44100)
    sv.sv_sampler_load.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    sv.sv_save.argtypes = [ctypes.c_int, ctypes.c_char_p]
    sv.sv_load.argtypes = [ctypes.c_int, ctypes.c_char_p]
    sv.sv_open_slot(0)
    phase_wav = Path(tempfile.gettempdir()) / "m00dr-phase_sine.wav"
    subprocess.run(["uv", "run", "-q", str(ROOT / "tools" / "phase_sine.py"), str(phase_wav)], check=True)
    sources = [(f"Stab {n}", stabs_dir / f"{n}.wav", NUDGE_CENTS.get(n, 0), False) for n in STABS]
    sources.append(("Phase sine", phase_wav, 0, True))
    patches = []
    for program, (name, wav, nudge, loop) in enumerate(sources):
        out = HERE / (name.lower().replace(" ", "-") + ".sunvox")
        with tempfile.NamedTemporaryFile(suffix=".sunvox") as tmp:
            scaffold(tmp.name, name)
            assert sv.sv_load(0, tmp.name.encode()) == 0, name
        sv.sv_volume(0, 256)   # as the players run it (a fresh slot is at 80)
        mod = sv.sv_find_module(0, b"Sampler")
        assert sv.sv_sampler_load(0, mod, str(wav).encode(), -1) == 0, f"can't load {wav}"
        # Tune it the way the soundfont did: the sample's detected root plays at its own
        # MIDI note, so MIDI n -> pitch n with the players' NOTE_OFFSET = 1. The sampler
        # keeps tuning XM-style: relative note (semitones) + finetune (1/128 semitone).
        _, _, root, cents = detect(wav, loop)
        cents += nudge
        total = (sv.sv_sampler_par(0, mod, 0, 7, 0, 0) * 128 + sv.sv_sampler_par(0, mod, 0, 6, 0, 0)
                 + round(128 * ((60 - root) + cents / 100)))
        rel = round(total / 128)
        sv.sv_sampler_par(0, mod, 0, 7, rel, 1); sv.sv_sampler_par(0, mod, 0, 6, total - rel * 128, 1)
        if loop:   # sv_sampler_par: 0 loop begin, 1 loop length, 2 loop type (1 = forward)
            frames = sv.sv_sampler_par(0, mod, 0, 1, 0, 0)   # read: length defaults to the whole sample
            sv.sv_sampler_par(0, mod, 0, 0, 0, 1)
            sv.sv_sampler_par(0, mod, 0, 2, 1, 1)
        peak = render_peak(sv, mod)
        vol = min(512, round(256 * TARGET_PEAK / peak))
        sv.sv_set_module_ctl_value(0, mod, 0, vol, 0)   # ctl 0 = Volume
        sv.sv_audio_callback(BUF.ctypes.data, 256, 0, sv.sv_get_ticks())   # apply it before saving
        assert sv.sv_save(0, str(out).encode()) == 0, f"can't save {out}"
        patches.append({"program": program, "name": name, "file": out.name, "inputs": ["Sampler"]})
        print(f"wrote {out.name}  (root MIDI {root}, {cents:+d} cents, volume {vol})")
    (HERE / "patches.json").write_text(json.dumps({"bank": 1, "patches": patches}, indent=1) + "\n")
    print("wrote patches.json")


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "samples/GAMEBREAKS/gamestabs")
