"""The producer: the model runs the set (roller --arrange producer).

Instead of picking a move from a menu, the model is shown the whole set each
time it's asked -- every m00Dr control and what it accepts, the mixer with the
human's ceilings and the limiter, the bass, the 303, the break, the fx and the
sample library -- with its own notes and what its last actions did, and it
answers with a plan (PLAN_SCHEMA, enforced by llama-server's JSON grammar):
actions to take now, and how many bars until it wants to be asked again.

Actions are applied as given, within what each control accepts; a value a
control can't take is reported back to the model next time, not guessed at.
The limits are the human's: a fader they've moved by hand is that strip's
ceiling (held in mixer.py, which also clamps these moves), and mixer.py's
master limiter, which nothing here can reach. The dice lanes are off in this
mode; the model also decides when to Roll.
"""
import asyncio
import json
import math
import os
import pathlib
import random
import re
import shutil
import socket
import tempfile
import time
import urllib.request

from .arrangement import BASS_CHANNEL, BASS_GATE_CC, GATES, WOBBLES

MIXER = ("127.0.0.1", 9810)   # mixer.py's UDP control
MIXER_LIVE = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "m800dr-mixer.json"
REPO = pathlib.Path(__file__).resolve().parents[2]
SAMPLES = pathlib.Path.home() / "samples"
PREP = REPO / "tools" / "prep_sample.py"
LIVE_FX = pathlib.Path.home() / ".local/state/m00drc/live-fx"   # fx.py's live slots
LIVE_FX_NOTE, LIVE_FX_SLOTS = 96, 32
FX_CHANNEL = 10
FX_NOTES = {"riser2": 60, "riser4": 61, "riser8": 62, "down2": 64, "down4": 65, "impact": 67, "atmos": 72}
GATE_BITS_CCS, WOBBLE_RATE_CC = (25, 26, 27), 28        # sunvoxbass.py
ACID_CHANNEL = 5
ACID_CCS = {"cutoff": 74, "resonance": 71, "decay": 75, "accent": 76, "glide": 5, "drive": 77}
ACID_ALIASES = {"filter": "cutoff", "res": "resonance", "env": "decay", "slide": "glide", "distortion": "drive"}
RAMP_STEPS_PER_BEAT = 8
HISTORY = 16                  # action results kept for the next prompt
DEFAULT_BARS = 4              # when a plan doesn't come back

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "notes": {"type": "string", "maxLength": 600},
        "actions": {"type": "array", "maxItems": 12, "items": {
            "type": "object",
            "properties": {
                "do": {"enum": ["set", "fader", "sidechain", "bass", "acid", "break", "sample", "fx", "roll"]},
                "target": {"type": "string", "maxLength": 60},
                "value": {"type": "string", "maxLength": 60},
                "beats": {"type": "number"}},
            "required": ["do", "target", "value", "beats"]}},
        "next_bars": {"type": "integer", "minimum": 1, "maximum": 16}},
    "required": ["notes", "actions", "next_bars"]}


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def num(text: str) -> float:
    """A number as the model writes it: ",65" and "0,65" are 0.65; raises ValueError if none."""
    match = re.search(r"-?\d*[.,]?\d+", str(text))
    if not match:
        raise ValueError(text)
    return float(match.group().replace(",", "."))


def describe_control(name: str, c: dict) -> str | None:
    """What a m00Dr control accepts, for the system prompt; slots are listed live."""
    if name.startswith("slot"):
        return None
    if "items" in c:
        return f"{name}: one of {', '.join(map(str, c['items']))}"
    if "min" in c:
        return f"{name}: a whole number {c['min']}..{c['max']}"
    if isinstance(c.get("value"), bool):
        return f"{name}: true/false" + (f" ({c['label']})" if c.get("label") else "")
    if name == "bpm":
        return "bpm: the tempo, 60..200"
    return None


class Producer:
    def __init__(self, roller):
        self.roller = roller
        self.args = roller.args
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.notes = ""
        self.history: list[str] = []
        self.ramps: dict[str, asyncio.Task] = {}
        self.bass = {"gate": "legato", "wobble": "1 swell a beat"}
        self.acid: dict[str, int] = {}
        self.fx_next = 0
        self.playing: list[tuple[int, str]] = []    # live fx notes held or sounding: (note, name)
        self.started = time.monotonic()
        self.rolled = time.monotonic()
        self.library, self.catalog_mtime = {}, None
        self._refresh_library()

    def _refresh_library(self) -> None:
        """(Re)reads the sample catalog when it changes (a resync), and the prompt that lists it."""
        try:
            mtime = (SAMPLES / "catalog.json").stat().st_mtime
            if mtime == self.catalog_mtime:
                return
            self.library, self.catalog_mtime = json.loads((SAMPLES / "catalog.json").read_text()), mtime
            self.roller.loops = self.library.get("loops", self.roller.loops)
            print(f"producer: library {len(self.library.get('samples', []))} samples, "
                  f"{len(self.roller.loops)} loops", flush=True)
        except (OSError, ValueError):
            pass
        self.system = self._system()

    # -- the prompt ---------------------------------------------------------------

    def _system(self) -> str:
        controls = self.roller.state.get("controls", {})
        accepted = "\n".join(f"   {line}" for line in
                             (describe_control(k, c) for k, c in controls.items()) if line)
        packs = sorted({s["pack"] for s in self.library.get("samples", [])})
        return f"""\
You are the producer of a live generative techno set, with your hands on everything: \
m00Dr (the generator: key, mode, the chord progression, which parts play and their \
sounds, the arp, the drums, the acid line, the tempo), the mixer, the bass synth, the \
303, the drum breaks, the fx, and a sample library of {len(self.library.get('samples', []))} \
files. You have a plan for this set: keep it in your notes, build it section by \
section, use tension and release, space and silence. You're asked again after \
next_bars bars, and see your notes and what your actions did.

Answer in JSON: notes (your running plan, for yourself), actions (what to do now, in \
order), next_bars. Each action has do, target, value (text) and beats (how long a \
fader move takes; 0 = at once; 0 where it doesn't apply):
- set: target a m00Dr control, value one it accepts:
{accepted}
   slot0..slot3: the chord progression's four slots, from the options listed each time
- fader: target a mixer strip (chords, bass, arp, fx, acid, drums, breaks), value \
0..1 (0.8 is unity; above it the strip distorts into fuzz; 0 is silent)
- sidechain: value 0..1, how hard the kick ducks the tonal parts
- bass: target gate, value legato, a pattern ({', '.join(g for g in GATES if g != 'legato')}) \
or four hex digits for 16 steps, first step on top (8A28 = x...x.x...x.x...), x = the \
note re-attacks; or target wobble, value swells per beat (0.125..8)
- acid: target {', '.join(ACID_CCS)}, value 0..127 (the 303's knobs)
- break: target search, value words to find a drum loop in the library; or target \
fixed, value 0..63 (the built-in breaks)
- sample: target words to find a sample in the library, value once, loop or stop \
(stop: target the words you started it with, or all)
- fx: target riser2, riser4, riser8 (bars), down2, down4, impact or atmos; value on \
or off for atmos, otherwise ""
- roll: a new chord progression (m00Dr's Roll button); target and value ""
Library packs: {', '.join(packs)}.
The human can override you: a fader they've moved has a ceiling your moves stay \
under, shown with the mixer. A limiter protects the speakers."""

    def _user(self) -> str:
        r = self.roller
        controls = r.state.get("controls", {})
        values = "; ".join(f"{k}={c.get('value')!r}" for k, c in controls.items()
                           if not k.startswith("slot") and c.get("enabled", True))
        slots = "\n".join(f"  {k} = {c.get('value')!r} (options: {', '.join(map(str, c.get('items', [])))})"
                          for k, c in controls.items() if k.startswith("slot"))
        try:
            mixer = json.loads(MIXER_LIVE.read_text())
            strips = "; ".join(f"{s['name']} {s['fader']:.2f}" + (f" (ceiling {s['ceiling']:.2f})"
                               if s.get("ceiling") is not None else "") + f" peak {s['peak']:.2f}"
                               for s in mixer["strips"])
            mix = f"Mixer: {strips}; sidechain {mixer['sidechain']:.2f}; limiter {mixer.get('limiter_db', 0)} dB"
        except (OSError, ValueError, KeyError):
            mix = "Mixer: not reporting"
        program = r.breaks_program
        entry = r.live.get(program) or (r.breaks_index[program] if program < len(r.breaks_index) else None)
        from .roller import break_text
        brk = break_text(entry) if entry else "none"
        bars = (time.monotonic() - self.started) / (240 / r.bpm())
        return (f"Bar {bars:.0f} of the set, {r.bpm():.0f} BPM, "
                f"{'playing' if r.state.get('playing') else 'stopped'}; last Roll {time.monotonic() - self.rolled:.0f} s ago.\n"
                f"m00Dr: {values}\nChord progression slots:\n{slots}\n{mix}\n"
                f"Bass: gate {self.bass['gate']}, wobble {self.bass['wobble']}. "
                f"303 knobs you've set: {self.acid or 'none yet'}.\nBreak playing: {brk}\n"
                f"Samples sounding: {', '.join(n for _, n in self.playing) or 'none'}\n"
                f"Your notes: {self.notes or '(none yet: this is the start of the set)'}\n"
                f"What your last actions did:\n" + ("\n".join(self.history) or "(nothing yet)")
                + "\nPlan the next stretch.")

    def ask(self) -> dict | None:
        """Blocking: the model's plan, or None."""
        body = {"model": self.args.model, "max_tokens": 900, "temperature": 0.8,
                "chat_template_kwargs": {"enable_thinking": False},
                "response_format": {"type": "json_schema", "json_schema": {"name": "plan", "schema": PLAN_SCHEMA}},
                "messages": [{"role": "system", "content": self.system}, {"role": "user", "content": self._user()}]}
        request = urllib.request.Request(f"{self.args.api.rstrip('/')}/v1/chat/completions",
                                         json.dumps(body).encode(), {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return json.loads(json.load(response)["choices"][0]["message"]["content"])
        except (OSError, ValueError, KeyError, IndexError, TypeError) as e:
            print(f"producer: no plan ({e})", flush=True)
            return None

    # -- the loop -----------------------------------------------------------------

    async def run(self, ws) -> None:
        print(f"producer: on, {len(self.library.get('samples', []))} library samples", flush=True)
        while True:
            if not self.roller.state.get("playing"):
                await asyncio.sleep(1)
                continue
            self._refresh_library()
            started = time.monotonic()
            plan = await asyncio.to_thread(self.ask)
            took = time.monotonic() - started
            bars = DEFAULT_BARS
            if plan:
                self.notes = plan.get("notes", "")[:600]
                bars = min(max(int(plan.get("next_bars", DEFAULT_BARS)), 1), 16)
                print(f"producer ({took:.0f}s): {self.notes}", flush=True)
                for action in plan.get("actions", [])[:12]:
                    try:
                        result = await self.apply(ws, action)
                    except Exception as e:   # one bad action mustn't stop the set
                        result = f"{action.get('do')} {action.get('target')}: failed ({e})"
                    print(f"producer:   {result}", flush=True)
                    self.history = (self.history + [result])[-HISTORY:]
                print(f"producer: next in {bars} bars", flush=True)
            await asyncio.sleep(bars * 240 / self.roller.bpm())

    async def stop(self) -> None:
        for task in self.ramps.values():
            task.cancel()
        for note, _ in self.playing:
            await self._note(FX_CHANNEL, note, 0)
        await self._note(FX_CHANNEL, FX_NOTES["atmos"], 0)

    # -- actions ------------------------------------------------------------------

    async def apply(self, ws, a: dict) -> str:
        do, target, value = a.get("do"), str(a.get("target", "")).strip(), str(a.get("value", "")).strip()
        beats = max(float(a.get("beats") or 0), 0.0)
        return await getattr(self, f"_{do}")(ws, target, value, beats)

    async def _set(self, ws, target, value, beats):
        c = self.roller.state.get("controls", {}).get(target)
        if c is None:
            return f"set {target}: no such control"
        if not c.get("enabled", True):
            return f"set {target}: disabled right now"
        if "items" in c:
            items = list(map(str, c["items"]))
            match = next((i for i in items if norm(i) == norm(value)), None) or next(
                (i for i in items if any(norm(part) == norm(value) for part in i.split("·"))), None)
            if match is None:
                return f"set {target} {value!r}: not one of its options"
            v = match
        elif "min" in c:
            try:
                v = min(max(round(num(value)), c["min"]), c["max"])
            except ValueError:
                return f"set {target} {value!r}: not a number"
        elif isinstance(c.get("value"), bool):
            v = norm(value) in ("true", "on", "yes", "1")
        elif target == "bpm":
            try:
                v = str(min(max(round(num(value)), 60), 200))
            except ValueError:
                return f"set bpm {value!r}: not a number"
        else:
            v = value
        if not self.args.dry_run:
            await ws.send(json.dumps({"op": "set", "id": target, "value": v}))
        return f"set {target} = {v!r}"

    async def _fader(self, ws, target, value, beats):
        try:
            level = min(max(num(value), 0.0), 1.0)
            strip = next(s for s in json.loads(MIXER_LIVE.read_text())["strips"] if s["name"] == target)
        except (ValueError, StopIteration):
            return f"fader {target} {value!r}: no such strip, or not a level"
        if target in self.ramps:
            self.ramps[target].cancel()
        start, ceiling = strip["fader"], strip.get("ceiling")
        self.ramps[target] = asyncio.create_task(self._ramp(target, start, level, beats))
        capped = f" (the human's ceiling {ceiling:.2f} holds it there)" if ceiling is not None and level > ceiling else ""
        return f"fader {target} {start:.2f} -> {level:.2f} over {beats:g} beats{capped}"

    async def _ramp(self, strip, start, level, beats):
        steps = max(1, int(beats * RAMP_STEPS_PER_BEAT))
        for i in range(1, steps + 1):
            if not self.args.dry_run:
                self.sock.sendto(json.dumps({"by": "producer", "strip": strip,
                                             "fader": round(start + (level - start) * i / steps, 4)}).encode(), MIXER)
            if beats:
                await asyncio.sleep(60 / self.roller.bpm() / RAMP_STEPS_PER_BEAT)

    async def _sidechain(self, ws, target, value, beats):
        try:
            amount = min(max(num(value or target), 0.0), 1.0)
        except ValueError:
            return f"sidechain {value!r}: not a number"
        if not self.args.dry_run:
            self.sock.sendto(json.dumps({"sidechain": amount}).encode(), MIXER)
        return f"sidechain = {amount:.2f}"

    async def _bass(self, ws, target, value, beats):
        if target == "gate":
            name = norm(value)
            if name in GATES:
                await self._cc(BASS_CHANNEL, BASS_GATE_CC, list(GATES).index(name))
                self.bass["gate"] = name
                return f"bass gate {name}"
            if re.fullmatch(r"(0x)?[0-9a-f]{1,4}", name):
                bits = int(name.removeprefix("0x"), 16)
                for cc, part in zip(GATE_BITS_CCS, (bits >> 10 & 63, bits >> 5 & 31, bits & 31)):
                    await self._cc(BASS_CHANNEL, cc, part)
                steps = f"{bits:016b}".replace("1", "x").replace("0", ".")
                self.bass["gate"] = f"{bits:04X} {steps}"
                return f"bass gate {bits:04X} {steps}"
            return f"bass gate {value!r}: not a pattern name or four hex digits"
        if target == "wobble":
            try:
                rate = WOBBLES.get(norm(value)) or num(value)
            except ValueError:
                return f"bass wobble {value!r}: not a rate"
            cc = min(max(round(16 * math.log2(max(rate, 1e-3) * 8)), 0), 127)
            await self._cc(BASS_CHANNEL, WOBBLE_RATE_CC, cc)
            self.bass["wobble"] = f"{min(8.0, 2 ** (cc / 16) / 8):.3g} swells a beat"
            return f"bass wobble {self.bass['wobble']}"
        return f"bass {target}: gate or wobble"

    async def _acid(self, ws, target, value, beats):
        target = ACID_ALIASES.get(norm(target), norm(target))
        cc = ACID_CCS.get(target)
        try:
            v = min(max(round(num(value)), 0), 127)
        except ValueError:
            return f"acid {target} {value!r}: not 0..127"
        if cc is None:
            return f"acid {target}: one of {', '.join(ACID_CCS)}"
        await self._cc(ACID_CHANNEL, cc, v)
        self.acid[norm(target)] = v
        return f"acid {target} = {v}"

    async def _break(self, ws, target, value, beats):
        r = self.roller
        if target == "fixed":
            try:
                r.breaks_program = int(num(value)) % len(r.breaks_index)
            except (ValueError, ZeroDivisionError):
                return f"break fixed {value!r}: not 0..63"
            return f"break: built-in {r.breaks_program} ({r.breaks_index[r.breaks_program]['name']}), from the next bar"
        words = norm(value or target).split()
        from .roller import CUT_BREAKS, LIVE_BREAKS, LIVE_FIRST, LIVE_SLOTS, STRETCH
        bpm = r.bpm()
        found = [loop for loop in r.loops if all(w in norm(loop["path"]) for w in words)]
        fits = [loop for loop in found if STRETCH[0] <= bpm / loop["bpm"] <= STRETCH[1]]
        if not fits:
            return (f"break search {' '.join(words)!r}: {len(found)} loops match, none within reach of "
                    f"{bpm:.0f} BPM" if found else f"break search {' '.join(words)!r}: nothing matches")
        picks = random.sample(fits, min(len(fits), 6))
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="m00drc-cuts-"))
        try:
            proc = await asyncio.create_subprocess_exec(
                "uv", "run", "-q", str(CUT_BREAKS), "--out", str(tmp),
                *[f"{SAMPLES / loop['path']}@{loop['bpm']}" for loop in picks],
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await proc.communicate()
            for loop, cut in zip(picks, json.loads(out or b"[]")):
                if "file" not in cut:
                    continue
                entry = {"name": loop["name"], "pack": loop["pack"], "bars": cut["bars"], "bpm": cut["bpm"],
                         "transients": cut["transients"]}
                slot, r.live_next = r.live_next, (r.live_next + 1) % LIVE_SLOTS
                if not self.args.dry_run:
                    LIVE_BREAKS.mkdir(parents=True, exist_ok=True)
                    shutil.copy(tmp / cut["file"], LIVE_BREAKS / f"{slot:02d}.wav")
                    index = self._read_index(LIVE_BREAKS)
                    index[str(slot)] = entry
                    (LIVE_BREAKS / "index.json").write_text(json.dumps(index, indent=1))
                r.live[LIVE_FIRST + slot] = entry
                r.breaks_program = LIVE_FIRST + slot
                return (f"break search {' '.join(words)!r}: {loop['name']} ({loop['pack']}, {cut['bpm']:.0f} BPM, "
                        f"{cut['bars']} bars, {' '.join(cut['transients'])}), from the next bar")
            return f"break search {' '.join(words)!r}: {len(picks)} tried, none cut into a clean loop"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    async def _sample(self, ws, target, value, beats):
        mode = norm(value) or "once"
        if mode == "stop":
            gone = [(n, name) for n, name in self.playing if norm(target) in ("all", "") or norm(target) in norm(name)]
            for note, _ in gone:
                await self._note(FX_CHANNEL, note, 0)
            self.playing = [p for p in self.playing if p not in gone]
            return f"sample stop {target!r}: {len(gone)} stopped"
        words = norm(target).split()
        found = [s for s in self.library.get("samples", []) if all(w in norm(s["path"]) for w in words)]
        if not found:
            return f"sample {target!r}: nothing in the library matches"
        short = [s for s in found if s["seconds"] <= 30] or found
        pick = random.choice(short)
        slot, self.fx_next = self.fx_next, (self.fx_next + 1) % LIVE_FX_SLOTS
        note = LIVE_FX_NOTE + slot
        if not self.args.dry_run:
            LIVE_FX.mkdir(parents=True, exist_ok=True)
            proc = await asyncio.create_subprocess_exec(
                "uv", "run", "-q", str(PREP), "--out", str(LIVE_FX / f"{slot:02d}.wav"), str(SAMPLES / pick["path"]),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await proc.communicate()
            if proc.returncode:
                return f"sample {target!r}: {pick['name']} couldn't be read"
            index = self._read_index(LIVE_FX)
            index[f"{slot:02d}"] = {"name": pick["name"], "loop": mode == "loop"}
            (LIVE_FX / "index.json").write_text(json.dumps(index, indent=1))
        self.playing = [p for p in self.playing if p[0] != note] + [(note, pick["name"])]
        await self._note(FX_CHANNEL, note, 100)
        return f"sample {target!r}: {pick['name']} ({pick['pack']}, {pick['seconds']:.1f} s, {mode}) of {len(found)} matches"

    async def _fx(self, ws, target, value, beats):
        note = FX_NOTES.get(norm(target))
        if note is None:
            return f"fx {target}: one of {', '.join(FX_NOTES)}"
        on = not (norm(target) == "atmos" and norm(value) == "off")
        await self._note(FX_CHANNEL, note, 100 if on else 0)
        return f"fx {target}{'' if on else ' off'}"

    async def _roll(self, ws, target, value, beats):
        if not self.args.dry_run:
            await ws.send(json.dumps({"op": "click", "id": "roll"}))
        self.rolled = time.monotonic()
        return "roll: a new progression"

    # -- helpers ------------------------------------------------------------------

    @staticmethod
    def _read_index(folder):
        try:
            return json.loads((folder / "index.json").read_text())
        except (OSError, ValueError):
            return {}

    async def _cc(self, channel, cc, value):
        if not self.args.dry_run and self.roller.sound_out is not None:
            await self.roller._send_cc(channel, cc, value)

    async def _note(self, channel, note, velocity):
        """A note to fx.py (the only note target); velocity 0 is a note off."""
        if not self.args.dry_run and self.roller.sound_out is not None and channel == FX_CHANNEL:
            await self.roller._send_fx(note, velocity)
