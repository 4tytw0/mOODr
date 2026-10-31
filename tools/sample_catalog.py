"""Builds the drum sample catalog the roller picks from, out of Sononym's analysis.

SAMPLES (default ~/samples) holds a copy of the Mac's PRODUCTION/SAMPLES drum
folders (Drums/, GAMEBREAKS/) and a snapshot of its sononym.db; sync it from
the Mac with

    S=~/Documents/PRODUCTION/SAMPLES
    sqlite3 -readonly $S/sononym.db ".backup /tmp/sononym.db"
    rsync -a --exclude='*.asd' $S/Drums $S/GAMEBREAKS /tmp/sononym.db cacheos:samples/

and this writes SAMPLES/catalog.json:

  * loops: Sononym's Loops under Drums/Breaks and GAMEBREAKS with a tempo,
    for breaks: path, name, pack, bpm, seconds;
  * hits: its OneShots under Drums/HITs in a drum category (KIT_CATEGORIES),
    for kits: path, name, pack, category, how sure Sononym is of it, seconds.

Paths are relative to SAMPLES. Files Sononym couldn't analyse, or that
aren't in the copy, are left out.

    python3 tools/sample_catalog.py [--samples ~/samples]
"""
import argparse
import json
import pathlib
import sqlite3

LOOP_FOLDERS = ("Drums/Breaks/", "GAMEBREAKS/")
HIT_FOLDER = "Drums/HITs/"
BPM_RANGE = (60.0, 200.0)
MAX_HIT_SECONDS = 2.5
KIT_CATEGORIES = ("Perc Kicks", "Perc Snares", "Perc Claps", "Perc Hats & Shakers", "Perc Toms",
                  "Perc Cymbal Crashes", "Perc Cymbal Rides", "Perc Wood Hits", "Perc Bongos & Congas",
                  "Perc Metal Hits", "Perc Snips & Snaps")


def pack(path: str) -> str:
    """The sample pack: the folder under Drums/Breaks, Drums/HITs or GAMEBREAKS."""
    parts = path.split("/")
    return parts[2] if parts[0] == "Drums" and len(parts) > 3 else parts[1] if len(parts) > 2 else parts[0]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--samples", type=pathlib.Path, default=pathlib.Path.home() / "samples")
    args = p.parse_args()
    db = sqlite3.connect(f"file:{args.samples / 'sononym.db'}?mode=ro", uri=True)
    rows = db.execute("SELECT filename, basename, classes_VS, categories_VS, category_strengths_VR, bpm_R, "
                      "file_length_R FROM assets WHERE status = 'succeeded'")
    loops, hits = [], []
    for path, name, classes, categories, strengths, bpm, seconds in rows:
        if not (args.samples / path).is_file():
            continue
        kind = (json.loads(classes or "[]") or [None])[0]
        if kind == "Loop" and path.startswith(LOOP_FOLDERS) and bpm and BPM_RANGE[0] <= bpm <= BPM_RANGE[1]:
            loops.append({"path": path, "name": name, "pack": pack(path), "bpm": round(bpm, 2),
                          "seconds": round(seconds, 2)})
        elif kind == "OneShot" and path.startswith(HIT_FOLDER) and seconds and seconds <= MAX_HIT_SECONDS:
            cats = json.loads(categories or "[]")
            if cats and cats[0] in KIT_CATEGORIES:
                # Sononym lists a sample's categories strongest first; its strengths
                # are a vector over every category, so the top one is the max.
                sure = max(json.loads(strengths or "[0]") or [0])
                hits.append({"path": path, "name": name, "pack": pack(path), "category": cats[0],
                             "sure": round(sure, 2), "seconds": round(seconds, 3)})
    out = args.samples / "catalog.json"
    out.write_text(json.dumps({"loops": loops, "hits": hits}, indent=1) + "\n")
    by_cat = {}
    for h in hits:
        by_cat[h["category"]] = by_cat.get(h["category"], 0) + 1
    print(f"{len(loops)} loops, {len(hits)} hits -> {out}")
    print("  " + ", ".join(f"{c.removeprefix('Perc ')} {n}" for c, n in sorted(by_cat.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()
