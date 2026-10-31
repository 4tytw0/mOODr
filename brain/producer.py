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
import difflib
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
REPO = pathlib.Path(__file__).resolve().parents[1]
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
# The producer's notes for the audio page (server.py serves this as /producer): the
# last NOTES_KEPT, newest last, each {"time": epoch s, "took": s, "notes": text}.
NOTES_FILE = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "m00drc-producer.json"
NOTES_KEPT = 8
# The page chat (surfaces/server.py writes it): what the human listening said, and what
# you answered there. Messages since the last plan go into the next plan's prompt.
CHAT_FILE = NOTES_FILE.with_name("m00drc-chat.json")
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
    """A number as the model writes it: ",65" and "0,65" are 0.65, and "0.36 -> 0.28" is where it's
    going, 0.28 (the last number). Raises ValueError if there's none."""
    found = re.findall(r"-?\d*[.,]?\d+", str(text))
    if not found:
        raise ValueError(text)
    return float(found[-1].replace(",", "."))


# The resolver: the model searches the library in a producer's words ("stomp-clap-resonance:
# build to the snare roll"); these turn that into the library's own words before ranking.
FILLER = set("""a an and the to of in on at for with into onto from then than that this these those it its
is are be been so too very just more less some any all our my your their we i me us them up down out
over under before after while until when where how what which who build make let lets get go going
pull push bring keep hold drop rise fall feel like deep real hard soft heavy light big small little
one two three next now new old full half sound sounds sample samples loop loops texture""".split())
SLANG = {
    "stomp": ["kick"], "thump": ["kick"], "boom": ["kick", "808"], "heartbeat": ["kick"], "spine": ["kick"],
    "sub": ["sub", "808"], "low": ["bass", "sub"], "clap": ["clap"], "snap": ["snap", "clap"],
    "ghost": ["snare", "hat"], "roll": ["roll", "fill"], "fill": ["fill"], "hats": ["hat"], "hihat": ["hat"],
    "hi": ["hat"], "shaker": ["shaker"], "cymbal": ["cymbal", "crash", "ride"], "perc": ["perc"],
    "breath": ["breath", "vox"], "breathe": ["breath", "vox"], "inhale": ["breath"], "whisper": ["whisper", "vox"],
    "voice": ["vox", "vocal"], "vocal": ["vocal", "vox"], "vox": ["vox", "vocal"], "choir": ["choir", "vox"],
    "acapella": ["acapella", "vocal"], "texture": ["texture", "pad", "atmos"], "textures": ["texture", "pad"],
    "atmosphere": ["atmos", "ambient"], "ambient": ["ambient", "atmos"], "air": ["atmos", "noise"],
    "space": ["atmos", "reverb"], "silence": ["atmos"], "noise": ["noise"], "static": ["noise", "vinyl"],
    "riser": ["riser", "sweep"], "sweep": ["sweep"], "whoosh": ["whoosh"], "impact": ["impact", "hit"],
    "hit": ["hit"], "drone": ["drone", "pad"], "pad": ["pad"], "pads": ["pad"], "stab": ["stab"],
    "chord": ["chord"], "chords": ["chord"], "scratch": ["scratch"], "vinyl": ["vinyl"], "jungle": ["jungle"],
    "amen": ["amen"], "funk": ["funk"], "funky": ["funk"], "soul": ["soul", "funk"], "dnb": ["dnb", "drum"],
    "breakbeat": ["break"], "granular": ["granular", "texture"], "glitch": ["glitch"], "metal": ["metal"],
    "metallic": ["metal"], "industrial": ["industrial", "metal"], "rain": ["rain"], "wind": ["wind"],
    "nature": ["nature"], "resonance": ["reso"], "resonant": ["reso"], "tension": ["riser", "sweep"],
    "snare": ["snare"], "snares": ["snare"], "kick": ["kick"], "kicks": ["kick"], "tom": ["tom"], "toms": ["tom"],
}


def stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


class Resolver:
    """Ranks library entries against a phrase in the model's words."""

    def __init__(self, samples: list[dict]):
        category = {x["path"]: x.get("category") or "" for x in samples}
        self.tokens = {}          # path -> its words: folders, file name, Sononym category
        for x in samples:
            words = re.split(r"[^a-z0-9]+", f"{x['path']} {category[x['path']]}".lower())
            self.tokens[x["path"]] = {stem(w) for w in words if w}
        self.vocab = sorted({w for words in self.tokens.values() for w in words if len(w) > 2})

    def terms(self, phrase: str) -> list[str]:
        """The phrase in the library's words: filler dropped, slang translated, spelling snapped."""
        out = []
        for word in re.split(r"[^a-z0-9]+", phrase.lower()):
            if not word or word.isdigit() and len(word) < 3:
                continue
            if word in SLANG:        # slang first: some of it ("texture") is filler otherwise
                out += SLANG[word]
            elif word in FILLER:
                continue
            elif stem(word) in self.vocab or len(word) < 4:
                out.append(stem(word))
            else:
                close = difflib.get_close_matches(stem(word), self.vocab, n=1, cutoff=0.88)
                out += close
        return list(dict.fromkeys(out))

    def rank(self, phrase: str, entries: list[dict]) -> tuple[list[str], list[dict]]:
        """(the search terms, the entries that match any, best first; ties shuffled)."""
        terms = self.terms(phrase)
        scored = []
        for x in entries:
            words = self.tokens.get(x["path"]) or set()
            score = sum(2 if t in words else 1 if len(t) >= 4 and any(t in w for w in words) else 0 for t in terms)
            if score:
                scored.append((score, random.random(), x))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return terms, [x for _, _, x in scored]


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
        self.feed = []                # NOTES_FILE's entries
        self.chat_seen = time.time() - 600   # page chat read up to here (the last 10 min at start)
        self._chat_upto = self.chat_seen
        self.prompt = ""              # the state the last plan was asked with, for the chat
        self.history: list[str] = []
        self.ramps: dict[str, asyncio.Task] = {}
        self.bass = {"gate": "legato", "wobble": "1 swell a beat"}
        self.acid: dict[str, int] = {}
        self.fx_next = 0
        self.playing: list[tuple[int, str]] = []    # live fx notes held or sounding: (note, name)
        self.started = time.monotonic()
        self.rolled = time.monotonic()
        self.library, self.catalog_mtime = {}, None
        self.resolver = Resolver([])
        self.rolls: list[float] = []      # when it Rolled, for the prompt
        self._refresh_library()

    def _refresh_library(self) -> None:
        """(Re)reads the sample catalog when it changes (a resync), and the prompt that lists it."""
        try:
            mtime = (SAMPLES / "catalog.json").stat().st_mtime
            if mtime == self.catalog_mtime:
                return
            self.library, self.catalog_mtime = json.loads((SAMPLES / "catalog.json").read_text()), mtime
            self.roller.loops = self.library.get("loops", self.roller.loops)
            self.resolver = Resolver(self.library.get("samples", []))
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
Library packs: {', '.join(packs)}. Search with one to three plain words (kick, clap, vox, \
amen, jungle, pad, riser, noise...); every result tells you what it searched for.
Only your actions change the set: your notes are for you, and nothing in them happens \
unless an action does it.
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
                f"{'playing' if r.state.get('playing') else 'stopped'}; last Roll {time.monotonic() - self.rolled:.0f} s ago, "
                f"{sum(1 for t in self.rolls if time.monotonic() - t < 600)} Rolls in the last 10 minutes.\n"
                f"m00Dr: {values}\nChord progression slots:\n{slots}\n{mix}\n"
                f"Bass: gate {self.bass['gate']}, wobble {self.bass['wobble']}. "
                f"303 knobs you've set: {self.acid or 'none yet'}.\nBreak playing: {brk}\n"
                f"Samples sounding: {', '.join(n for _, n in self.playing) or 'none'}\n"
                f"Your notes: {self.notes or '(none yet: this is the start of the set)'}\n"
                f"What your last actions did:\n" + ("\n".join(self.history) or "(nothing yet)")
                + self._chat_text() + "\nPlan the next stretch.")

    def _chat_text(self) -> str:
        """The page chat since the last plan, for the prompt ("" if nothing new)."""
        try:
            messages = json.loads(CHAT_FILE.read_text()).get("messages", [])
        except (OSError, ValueError, AttributeError):
            return ""
        self._chat_upto = time.time()
        new = [m for m in messages if m.get("time", 0) > self.chat_seen]
        if not any(m.get("role") == "user" for m in new):
            return ""
        lines = "\n".join(("The human: " if m["role"] == "user" else "You (in the chat): ") + str(m.get("text", ""))
                          for m in new[-8:])
        return ("\nThe human listening talked to you in the page chat since your last plan (newest last):\n"
                f"{lines}\nWeigh it like a request from someone in the room: you still decide, but say in "
                "your notes what you did about it.")

    def ask(self) -> dict | None:
        """Blocking: the model's plan, or None."""
        self.prompt = self._user()
        body = {"model": self.args.model, "max_tokens": 900, "temperature": 0.8,
                "chat_template_kwargs": {"enable_thinking": False},
                "response_format": {"type": "json_schema", "json_schema": {"name": "plan", "schema": PLAN_SCHEMA}},
                "messages": [{"role": "system", "content": self.system}, {"role": "user", "content": self.prompt}]}
        request = urllib.request.Request(f"{self.args.api.rstrip('/')}/v1/chat/completions",
                                         json.dumps(body).encode(), {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return json.loads(json.load(response)["choices"][0]["message"]["content"])
        except (OSError, ValueError, KeyError, IndexError, TypeError) as e:
            print(f"producer: no plan ({e})", flush=True)
            return None

    # -- the loop -----------------------------------------------------------------

    def _publish_notes(self, took: float, notes: str, actions: list[str] | None = None) -> None:
        """Adds this plan's notes, in full (self.notes is cut to 600 for the prompt), and
        what its actions did to NOTES_FILE, for the audio page and its chat. Best effort."""
        notes = str(notes).strip()[:4000]
        if not notes:
            return
        self.feed = (self.feed + [{"time": time.time(), "took": round(took), "notes": notes,
                                   "actions": actions or []}])[-NOTES_KEPT:]
        state = self.prompt.removesuffix("\nPlan the next stretch.")
        tmp = NOTES_FILE.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps({"model": getattr(self.roller.args, "model", None), "api": self.args.api,
                                       "state": state, "notes": self.feed}))
            tmp.replace(NOTES_FILE)
        except OSError:
            pass

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
                self.chat_seen = self._chat_upto   # the chat it was shown has been answered
                results = []
                for action in plan.get("actions", [])[:12]:
                    try:
                        result = await self.apply(ws, action)
                    except Exception as e:   # one bad action mustn't stop the set
                        result = f"{action.get('do')} {action.get('target')}: failed ({e})"
                    print(f"producer:   {result}", flush=True)
                    self.history = (self.history + [result])[-HISTORY:]
                    results.append(result)
                self._publish_notes(took, plan.get("notes", ""), results)
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
        do, target, value = norm(a.get("do", "")), str(a.get("target", "")).strip(), str(a.get("value", "")).strip()
        if do in ("fader", "bass", "fx", "break"):
            target = norm(target)
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
        from .roller import CUT_BREAKS, LIVE_BREAKS, LIVE_FIRST, LIVE_SLOTS, STRETCH
        bpm = r.bpm()
        fits = [loop for loop in r.loops if STRETCH[0] <= bpm / loop["bpm"] <= STRETCH[1]]
        terms, ranked = self.resolver.rank(value or target, fits)
        words = terms
        if not ranked:
            return (f"break search {value or target!r} (searched as {' '.join(terms) or 'nothing usable'}): "
                    f"no loop within reach of {bpm:.0f} BPM matches")
        picks = ranked[:6]
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
                return (f"break search searched as {' '.join(words)!r}: {loop['name']} ({loop['pack']}, {cut['bpm']:.0f} BPM, "
                        f"{cut['bars']} bars, {' '.join(cut['transients'])}), from the next bar")
            return f"break search {' '.join(words)!r}: {len(picks)} tried, none cut into a clean loop"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    async def _sample(self, ws, target, value, beats):
        mode = norm(value) or "once"
        if norm(target) in ("once", "loop", "stop") and mode not in ("once", "loop", "stop"):
            target, mode = value, norm(target)   # the mode where the words should be
        if mode not in ("once", "loop", "stop"):
            mode = "once"
        if mode == "stop":
            gone = [(n, name) for n, name in self.playing if norm(target) in ("all", "") or norm(target) in norm(name)]
            for note, _ in gone:
                await self._note(FX_CHANNEL, note, 0)
            self.playing = [p for p in self.playing if p not in gone]
            return f"sample stop {target!r}: {len(gone)} stopped"
        terms, found = self.resolver.rank(target, self.library.get("samples", []))
        if not found:
            return f"sample {target!r} (searched as {' '.join(terms) or 'nothing usable'}): nothing matches"
        short = [x for x in found[:40] if x["seconds"] <= 30] or found[:40]
        pick = short[0]
        others = ", ".join(x["name"] for x in short[1:4])
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
        return (f"sample {target!r} searched as {' '.join(terms)!r}: {pick['name']} ({pick['pack']}, "
                f"{pick.get('category') or 'uncategorised'}, {pick['seconds']:.1f} s, {mode}); "
                f"{len(found)} matches, next best: {others or 'none'}")

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
        self.rolls = [t for t in self.rolls if self.rolled - t < 600] + [self.rolled]
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
