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

The roller's dice lanes keep moving m00Dr and the 303 every bar; the producer
modifies that. Each plan lands only when a d6 rolls --producer-needs or more
(5: 1 in 3); otherwise the dice keep the set, and the model is told so. The
automation lanes of a plan that landed keep running until replaced.
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
ACID_CCS = {"cutoff": 74, "resonance": 71, "decay": 75, "accent": 76, "glide": 5, "drive": 77, "grit": 78, "level": 79, "delay": 80, "feedback": 81, "time": 82}
ACID_ALIASES = {"filter": "cutoff", "res": "resonance", "env": "decay", "slide": "glide", "distortion": "drive", "volume": "level", "amp": "level", "gain": "level", "echo": "delay", "wet": "delay", "delay time": "time", "delaytime": "time", "fb": "feedback", "dirt": "grit", "crush": "grit"}
RAMP_STEPS_PER_BEAT = 8
HISTORY = 16                  # action results kept for the next prompt
GATE_SHARE, GATE_WINDOW = 40, 600   # the bass: gated about this % of the time, over this many s
UNLOAD_AFTER = 30             # s with m00Dr stopped before the producer's model is unloaded (frees the GPU)
# The producer's notes for the audio page (server.py serves this as /producer): the
# last NOTES_KEPT, newest last, each {"time": epoch s, "took": s, "notes": text}.
NOTES_FILE = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "m00drc-producer.json"
NOTES_KEPT = 8
# The page chat (surfaces/server.py writes it): what the human listening said, and what
# you answered there. Messages since the last plan go into the next plan's prompt.
CHAT_FILE = NOTES_FILE.with_name("m00drc-chat.json")
# Requests for improvements: the model asks for what it doesn't have; the human approves
# them on the audio page (server.py's /requests) and Claude builds approved ones in a
# coding session, then marks them done. Kept across reboots, outside git.
REQUESTS_FILE = pathlib.Path.home() / ".local/state/m00drc/requests.json"
REQUESTS_PER_HOUR = 3
REQUESTS_SHOWN = 8
# Reading while producing: files from the rig's repo and the sample library, read-only,
# into the next plan's prompt. "m00Dr/<path>" or "samples/<path>"; a folder lists its files.
READ_ROOTS = {"m00Dr": REPO, "samples": SAMPLES}
READS_PER_PLAN, READ_CHARS, READ_TOTAL = 2, 6000, 9000
# Asking Claude: a headless Claude Code on the desktop that can only read (Read, Grep, Glob;
# --strict-mcp-config also drops the account's connectors, which --tools alone leaves on)
# the repo and the sample library, sent the voices' live state; its answer comes back in a
# later plan. On the human's account, so capped. Logged to CLAUDE_LOG for the human.
CLAUDE_PER_HOUR, CLAUDE_TIMEOUT = 4, 300
CLAUDE_LOG = pathlib.Path.home() / ".local/state/m00drc/claude-asks.jsonl"
LIVE_STATE = {"303": "m800dr-acid.json", "mixer": "m800dr-mixer.json"}   # in XDG_RUNTIME_DIR
CLAUDE_BRIEF = (
    "You're answering the producer of the m00Dr rig: a local model (Ornith) running a live "
    "generative techno set, which can't read code well itself. You can only read and search: "
    "the rig's repo (the working directory: composer/ is m00Dr, voices/ the SunVox voices, "
    "mix/ the mixer, brain/ the producer and roller, docs/) and the sample library (~/samples, "
    "with catalog.json). Answer in under 200 words, plainly, with the exact control names and "
    "values it can use. If what it wants needs a code or patch change, say so as a proposal it "
    "can file with its request action; the human approves those. Never claim you changed anything.")
# What can be heard: each part's m00Dr on/off toggle (web remote id; None: no toggle)
# and its mixer strip. The prompt says which parts are silent and why, and actions on
# a part that's switched off are flagged in the feedback: the model kept tuning parts
# it had switched off itself, believing them heard.
AUDIBLE_PARTS = {"chords": "chords", "bass": "bass", "arp": "arp", "acid": "acid", "drums": "drums",
                 "breaks": None, "fx": None}
# Action results -> the part they affect (by prefix, lowercased).
RESULT_PARTS = (("acid ", "acid"), ("set acidnoise", "acid"), ("set acidwide", "acid"), ("bass ", "bass"),
                ("set basssound", "bass"), ("set chordsound", "chords"), ("set stabs", "chords"),
                ("set arpsound", "arp"), ("set arppattern", "arp"), ("set arprate", "arp"),
                ("set drumsource", "drums"), ("set drummutate", "drums"), ("break", "breaks"))
FAILED = ("not ", "no such", "failed", "one of", "disabled")


def part_of(result: str) -> str | None:
    """The part an action result touched, if it's one of AUDIBLE_PARTS."""
    r = result.lower()
    return next((part for prefix, part in RESULT_PARTS if r.startswith(prefix)), None)
DEFAULT_BARS = 4              # when a plan doesn't come back
# Automation lanes: the model writes a lane of values, one per step of `beats`, and
# code glides from each to the next and loops it until the lane is replaced. Plans
# come a minute or more apart; the lanes are what moves the set in between.
LANE_MAX_STEPS, LANES_MAX = 16, 8
LANE_TICKS_PER_BEAT = 8

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "notes": {"type": "string", "maxLength": 600},
        "actions": {"type": "array", "maxItems": 12, "items": {
            "type": "object",
            "properties": {
                "do": {"enum": ["set", "fader", "sidechain", "bass", "acid", "automate", "break", "sample", "fx",
                                "roll", "request", "read", "ask_claude"]},
                "target": {"type": "string", "maxLength": 60},
                "value": {"type": "string", "maxLength": 400},
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
        self.gate_log: list[tuple[float, bool]] = [(time.monotonic(), False)]   # (since, gated)
        self.acid: dict[str, int] = {}
        self.fx_next = 0
        self.playing: list[tuple[int, str]] = []    # live fx notes held or sounding: (note, name)
        self.started = time.monotonic()
        self.rolled = time.monotonic()
        self.library, self.catalog_mtime = {}, None
        self.resolver = Resolver([])
        self.rolls: list[float] = []      # when it Rolled, for the prompt
        self.reads: list[str] = []        # files read this plan, for the next prompt
        self.claude_asks: list[float] = []       # when it asked, for the hourly cap
        self.claude_answers: list[str] = []      # answers not yet shown
        self.claude_pending: dict[str, asyncio.Task] = {}
        self.die = random.Random()        # gates each plan (not the roller's seeded stream)
        self.needs = getattr(self.args, "producer_needs", 5)
        self.lanes: dict[str, dict] = {}  # automation, by lane key ("acid cutoff", "fader chords", ...)
        self.lane_task: asyncio.Task | None = None
        try:
            self.sidechain_now = float(json.loads(MIXER_LIVE.read_text())["sidechain"])
        except (OSError, ValueError, KeyError, TypeError):
            self.sidechain_now = 0.5
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
- sidechain: value 0..1, how the kick ducks everything but the drums and breaks: up \
to 0.5 is mix glue (to 6 dB, quick), above it an effect (to 30 dB at 1, a slow obvious pump)
- bass: the bass plays in phases: long held (legato) stretches, and gated sections, \
about {GATE_SHARE}% of the time overall. You decide when a phase starts and ends (several \
bars at least, not a new pattern every plan); you're shown the running share.
  target gate, value legato, a pattern ({', '.join(g for g in GATES if g != 'legato')}) \
or four hex digits for 16 steps, first step on top (8A28 = x...x.x...x.x...), x = the \
note re-attacks; or target wobble, value swells per beat (0.125..8)
- acid: target {', '.join(ACID_CCS)}, value 0..127 (the 303's knobs); beats = how long it glides there
- automate: target acid <knob>, fader <strip>, sidechain or bass wobble; value a lane of 2..16 \
numbers separated by spaces, in that control's range (acid 0..127, fader and sidechain 0..1, wobble \
0.125..8); beats = the length of each step (4 = one value a bar). The lane loops, gliding from each \
value to the next, until you automate that target again; value off stops it where it is, and a \
one-off move on that target also stops it. Your plans are a minute or more apart and nothing else \
moves in between: automation is how the set breathes. Keep three or more lanes running so \
something changes every bar, give them different lengths (say 4, 6 and 8 steps) so they drift \
against each other, and rewrite them as the sections change.
- break: target search, value words to find a drum loop in the library; or target \
fixed, value 0..63 (the built-in breaks)
- sample: target words to find a sample in the library, value once, loop or stop \
(stop: target the words you started it with, or all)
- fx: target riser2, riser4, riser8 (bars), down2, down4, impact or atmos; value on \
or off for atmos, otherwise ""
- roll: a new chord progression (m00Dr's Roll button); target and value ""
- request: ask for something you don't have: target a short title, value why you need it \
(a control, a module or effect on a voice, a sound, a change to how you're asked). The \
human approves or rejects it, and Claude builds approved ones between sets. Your requests \
and their status are listed each time: don't repeat one. At most {REQUESTS_PER_HOUR} an hour.
- read: target a path: m00Dr/<path> for the rig's code and docs (m00Dr/voices/acid/player.py, \
m00Dr/docs/HANDOFF.md, m00Dr/voices/drums/drumkits.json...) or samples/<path> for the sample \
library (a folder lists its files; samples/catalog.json is big: give a word); value "" or a \
word to find (you get the lines around it). The text is in your next plan. Read-only, at \
most {READS_PER_PLAN} per plan.
- ask_claude: target a short topic, value your question, for Claude: a coding agent that can \
read the rig's code, the sample library and the voices' live state, but change nothing. Its \
answer arrives in a later plan (a minute or two). Ask what your own reads can't answer: how \
a voice works, what a sound would take, why something sounds wrong. At most {CLAUDE_PER_HOUR} an hour.
Library packs: {', '.join(packs)}. Search with one to three plain words (kick, clap, vox, \
amen, jungle, pad, riser, noise...); every result tells you what it searched for.
The roller's dice move m00Dr and the 303 every bar on their own. You modify what they \
do, but only when your die comes up: each plan lands only on a d6 roll of {self.needs}+ \
({7 - self.needs} in 6); otherwise the dice keep the set and you're told so next time. So \
make each plan count, and put the movement you want in automation lanes: they keep \
running after a plan lands, until you replace them.
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
        audible = "; ".join(f"{p} {s}" for p, s in self.audible().items())
        return (f"Bar {bars:.0f} of the set, {r.bpm():.0f} BPM, "
                f"{'playing' if r.state.get('playing') else 'stopped'}; last Roll {time.monotonic() - self.rolled:.0f} s ago, "
                f"{sum(1 for t in self.rolls if time.monotonic() - t < 600)} Rolls in the last 10 minutes.\n"
                f"m00Dr: {values}\nChord progression slots:\n{slots}\n{mix}\n"
                f"What's audible now: {audible}. Moves on a SILENT part change nothing anyone hears.\n"
                f"Bass: gate {self.bass['gate']}, wobble {self.bass['wobble']}; {self._gate_text()}. "
                f"303 knobs you've set: {self.acid or 'none yet'}.\nBreak playing: {brk}\n"
                f"Samples sounding: {', '.join(n for _, n in self.playing) or 'none'}\n"
                f"Automation running: {self._lanes_text()}\n"
                f"Your requests: {self._requests_text()}\n"
                + self._reads_text() + self._claude_text() +
                f"Your notes: {self.notes or '(none yet: this is the start of the set)'}\n"
                f"What your last actions did:\n" + ("\n".join(self.history) or "(nothing yet)")
                + self._chat_text() + "\nPlan the next stretch.")

    def toggles(self) -> dict[str, bool]:
        """m00Dr's part toggles as the web remote last reported them."""
        controls = self.roller.state.get("controls", {})
        return {t: bool(controls[t].get("value")) for t in AUDIBLE_PARTS.values() if t and t in controls}

    def audible(self) -> dict[str, str]:
        """Each part: "sounding", "on, silent right now", or SILENT and why."""
        toggles = self.toggles()
        try:
            strips = {s["name"]: s for s in json.loads(MIXER_LIVE.read_text())["strips"]}
        except (OSError, ValueError, KeyError, TypeError):
            strips = {}
        out = {}
        for part, toggle in AUDIBLE_PARTS.items():
            strip = strips.get(part)
            if toggle and toggles.get(toggle) is False:
                out[part] = f"SILENT (switched off in m00Dr: set {toggle} = true to hear it)"
            elif strip is not None and strip.get("fader", 1) <= 0.01:
                out[part] = "SILENT (its fader is at 0)"
            elif strip is not None:
                out[part] = "sounding" if strip.get("peak", 0) > 0.001 else "on, silent right now"
        return out

    def _flag_silent(self, results: list[str]) -> list[str]:
        """Marks actions on a part that's switched off in m00Dr (after the plan's own
        toggles: a plan that switches a part on and tunes it isn't flagged)."""
        toggles = self.toggles()
        for res in results:   # this plan's own switches win over a state echo that may lag
            low = res.lower()
            for t in AUDIBLE_PARTS.values():
                if t and low.startswith(f"set {t} = "):
                    toggles[t] = low.endswith("true")
        flagged = []
        for res in results:
            part = part_of(res)
            toggle = AUDIBLE_PARTS.get(part) if part else None
            if toggle and toggles.get(toggle) is False and not any(f in res.lower() for f in FAILED):
                res += f" (no effect anyone hears: {part} is switched off in m00Dr)"
            flagged.append(res)
        return flagged

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
                                       "state": state, "toggles": self.toggles(), "notes": self.feed}))
            tmp.replace(NOTES_FILE)
        except OSError:
            pass

    def _unload_model(self) -> None:
        """Blocking: unloads llama-swap's model if it's this producer's (not one someone is chatting with)."""
        base = self.args.api.rstrip("/")
        try:
            with urllib.request.urlopen(f"{base}/running", timeout=5) as r:
                running = [m["model"] for m in json.load(r).get("running", [])]
            if self.args.model in running and not self.args.dry_run:
                urllib.request.urlopen(f"{base}/unload", timeout=30).read()
                print(f"producer: m00Dr stopped, {self.args.model} unloaded", flush=True)
        except (OSError, ValueError):
            pass

    async def run(self, ws) -> None:
        print(f"producer: on, {len(self.library.get('samples', []))} library samples", flush=True)
        stopped_since = None
        while True:
            if not self.roller.state.get("playing"):
                # Paused with the transport: no plans, the lanes hold (see _run_lanes), and
                # after UNLOAD_AFTER the model is unloaded. Play reloads it with the next plan.
                stopped_since = stopped_since or time.monotonic()
                if stopped_since > 0 and time.monotonic() - stopped_since > UNLOAD_AFTER:
                    await asyncio.to_thread(self._unload_model)
                    stopped_since = -1.0   # done for this stop
                await asyncio.sleep(1)
                continue
            stopped_since = None
            self._refresh_library()
            started = time.monotonic()
            plan = await asyncio.to_thread(self.ask)
            took = time.monotonic() - started
            bars = DEFAULT_BARS
            if plan and not self.roller.state.get("playing"):
                print("producer: m00Dr stopped while planning; plan discarded", flush=True)
                continue
            if plan:
                self.notes = plan.get("notes", "")[:600]
                bars = min(max(int(plan.get("next_bars", DEFAULT_BARS)), 1), 16)
                print(f"producer ({took:.0f}s): {self.notes}", flush=True)
                roll = self.die.randint(1, 6)
                if roll < self.needs:
                    print(f"producer: rolled {roll}, needs {self.needs}: the dice keep the set", flush=True)
                    self.history = (self.history + [f"your last plan didn't land: your die rolled {roll}, "
                                                    f"it needed {self.needs}+; the dice kept the set"])[-HISTORY:]
                    self._publish_notes(took, plan.get("notes", ""),
                                        [f"not applied: rolled {roll}, needs {self.needs}+"])
                    await asyncio.sleep(bars * 240 / self.roller.bpm())
                    continue
                print(f"producer: rolled {roll}: the plan lands", flush=True)
                self.chat_seen = self._chat_upto   # the chat it was shown has been answered
                results = []
                for action in plan.get("actions", [])[:12]:
                    try:
                        result = await self.apply(ws, action)
                    except Exception as e:   # one bad action mustn't stop the set
                        result = f"{action.get('do')} {action.get('target')}: failed ({e})"
                    print(f"producer:   {result}", flush=True)
                    results.append(result)
                await asyncio.sleep(0.5)   # let m00Dr's state echo this plan's switches
                results = self._flag_silent(results)
                for result in results:
                    if result.endswith("switched off in m00Dr)"):
                        print(f"producer:   ^ {result.rsplit(' (', 1)[1][:-1]}", flush=True)
                self.history = (self.history + results)[-HISTORY:]
                self._publish_notes(took, plan.get("notes", ""), results)
                print(f"producer: next in {bars} bars", flush=True)
            await asyncio.sleep(bars * 240 / self.roller.bpm())

    async def stop(self) -> None:
        for task in self.ramps.values():
            task.cancel()
        if self.lane_task:
            self.lane_task.cancel()
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
        self._unlane(f"fader {target}")
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
        self._unlane("sidechain")
        if beats:
            self.lanes["sidechain"] = self._oneshot("sidechain", self.sidechain_now, amount, beats)
            return f"sidechain {self.sidechain_now:.2f} -> {amount:.2f} over {beats:g} beats"
        await self._send_lane("sidechain", amount)
        return f"sidechain = {amount:.2f}"

    async def _bass(self, ws, target, value, beats):
        if target == "gate":
            name = norm(value)
            if name in GATES:
                await self._cc(BASS_CHANNEL, BASS_GATE_CC, list(GATES).index(name))
                self.bass["gate"] = name
                self._log_gate(name != "legato")
                return f"bass gate {name}"
            if re.fullmatch(r"(0x)?[0-9a-f]{1,4}", name):
                bits = int(name.removeprefix("0x"), 16)
                for cc, part in zip(GATE_BITS_CCS, (bits >> 10 & 63, bits >> 5 & 31, bits & 31)):
                    await self._cc(BASS_CHANNEL, cc, part)
                steps = f"{bits:016b}".replace("1", "x").replace("0", ".")
                self.bass["gate"] = f"{bits:04X} {steps}"
                self._log_gate(True)
                return f"bass gate {bits:04X} {steps}"
            return f"bass gate {value!r}: not a pattern name or four hex digits"
        if target == "wobble":
            try:
                rate = WOBBLES.get(norm(value)) or num(value)
            except ValueError:
                return f"bass wobble {value!r}: not a rate"
            self._unlane("bass wobble")
            await self._send_lane("bass wobble", rate)
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
        key = f"acid {target}"
        self._unlane(key)
        if beats:
            start = self.acid.get(target, v)
            self.lanes[key] = self._oneshot(key, start, v, beats)
            return f"acid {target} {start} -> {v} over {beats:g} beats"
        await self._send_lane(key, v)
        return f"acid {target} = {v}"

    # -- the bass's phases --------------------------------------------------------

    def _log_gate(self, gated: bool) -> None:
        if gated != self.gate_log[-1][1]:
            self.gate_log.append((time.monotonic(), gated))
        self.gate_log = self.gate_log[-64:]

    def _gate_text(self) -> str:
        """How the bass has spent the last GATE_WINDOW: legato or gated, and for how long now."""
        now = time.monotonic()
        start = now - GATE_WINDOW
        gated = 0.0
        for (t0, g), (t1, _) in zip(self.gate_log, self.gate_log[1:] + [(now, None)]):
            if g:
                gated += max(0.0, t1 - max(t0, start))
        span = min(GATE_WINDOW, now - self.started)
        share = 100 * gated / span if span > 0 else 0
        current, since = self.gate_log[-1][1], now - self.gate_log[-1][0]
        bars = since / (240 / self.roller.bpm())
        return (f"gated {share:.0f}% of the last {span / 60:.0f} min (aim about {GATE_SHARE}%); "
                f"{'gated' if current else 'legato'} for the last {bars:.0f} bars")

    # -- reading and asking -------------------------------------------------------

    def _reads_text(self) -> str:
        if not self.reads:
            return ""
        text, self.reads = "\n".join(self.reads), []
        return f"Files you read last plan:\n{text[:READ_TOTAL]}\n"

    def _claude_text(self) -> str:
        pending = ", ".join(self.claude_pending) or "none"
        out = ""
        if self.claude_answers:
            out = "Claude answered:\n" + "\n".join(self.claude_answers) + "\n"
            self.claude_answers = []
        return out + f"Questions to Claude still out: {pending}\n"

    async def _read(self, ws, target, value, beats):
        if len(self.reads) >= READS_PER_PLAN:
            return f"read {target!r}: {READS_PER_PLAN} reads per plan already"
        root_name, _, rel = str(target).strip().lstrip("/").partition("/")
        root = READ_ROOTS.get(root_name) or next((r for n, r in READ_ROOTS.items() if n.lower() == root_name.lower()), None)
        if root is None:
            return f"read {target!r}: start the path with m00Dr/ or samples/"
        path = (root / rel).resolve()
        if path != root.resolve() and root.resolve() not in path.parents:
            return f"read {target!r}: outside {root_name}/"
        if path.is_dir():
            names = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir() if not p.name.startswith("."))
            body = ", ".join(names[:200]) + (f" ... ({len(names)} in all)" if len(names) > 200 else "")
        elif path.is_file():
            try:
                text = path.read_text(errors="replace")
            except OSError as e:
                return f"read {target!r}: {e}"
            word = str(value).strip()
            if word:
                lines = text.splitlines()
                hits = [i for i, line in enumerate(lines) if word.lower() in line.lower()]
                if not hits:
                    return f"read {target!r}: no line has {word!r}"
                body = "\n...\n".join("\n".join(lines[max(0, i - 6):i + 12]) for i in hits[:4])
            else:
                body = text
            if len(body) > READ_CHARS:
                body = body[:READ_CHARS] + f"\n... (cut at {READ_CHARS} of {len(body)} chars: read again with a word to find)"
        else:
            return f"read {target!r}: no such file or folder"
        self.reads.append(f"--- {root_name}/{rel}{' (around ' + repr(value) + ')' if value else ''}\n{body}")
        return f"read {root_name}/{rel}: {len(body)} chars, in your next plan"

    def _live_state(self) -> str:
        run = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
        parts = []
        for name, f in LIVE_STATE.items():
            try:
                parts.append(f"{name}: {(run / f).read_text()[:3000]}")
            except OSError:
                pass
        return "\n".join(parts)

    async def _ask_claude(self, ws, target, value, beats):
        topic, question = str(target).strip()[:60] or "question", str(value).strip()
        if not question:
            return "ask_claude: put the question in value"
        self.claude_asks = [t for t in self.claude_asks if time.time() - t < 3600]
        if len(self.claude_asks) >= CLAUDE_PER_HOUR:
            return f"ask_claude {topic!r}: {CLAUDE_PER_HOUR} questions in the last hour already"
        if topic in self.claude_pending:
            return f"ask_claude {topic!r}: already waiting on that one"
        if not shutil.which("claude"):
            return "ask_claude: Claude Code isn't installed on this machine"
        self.claude_asks.append(time.time())
        prompt = (f"The producer asks ({topic}): {question}\n\nThe set right now:\n"
                  f"{self.prompt[:4000]}\n\nThe voices' live state:\n{self._live_state()}")
        self.claude_pending[topic] = asyncio.create_task(self._claude(topic, question, prompt))
        return f"ask_claude {topic!r}: asked; the answer comes in a later plan"

    async def _claude(self, topic: str, question: str, prompt: str) -> None:
        started, answer, cost = time.time(), None, None
        try:
            if self.args.dry_run:
                answer = "(dry run: not asked)"
            else:
                proc = await asyncio.create_subprocess_exec(
                    "claude", "-p", prompt, "--tools", "Read", "Grep", "Glob", "--strict-mcp-config",
                    "--add-dir", str(SAMPLES),
                    "--append-system-prompt", CLAUDE_BRIEF, "--output-format", "json",
                    cwd=str(REPO), stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                try:
                    out, err = await asyncio.wait_for(proc.communicate(), CLAUDE_TIMEOUT)
                except asyncio.TimeoutError:
                    proc.kill()
                    out, err = b"", b"timed out"
                items = json.loads(out or b"[]")
                items = items if isinstance(items, list) else [items]
                result = next((x for x in reversed(items) if isinstance(x, dict) and x.get("type") == "result"), {})
                answer, cost = result.get("result") or err.decode(errors="replace")[-300:] or "no answer", result.get("total_cost_usd")
                if result.get("is_error"):
                    answer = f"(couldn't ask: {answer})"
        except (OSError, ValueError) as e:
            answer = f"(couldn't ask: {e})"
        self.claude_answers.append(f"[{topic}] {str(answer)[:2500]}")
        print(f"producer: Claude answered {topic!r} in {time.time() - started:.0f}s: {str(answer)[:200]}", flush=True)
        try:
            CLAUDE_LOG.parent.mkdir(parents=True, exist_ok=True)
            with CLAUDE_LOG.open("a") as f:
                f.write(json.dumps({"time": started, "took": round(time.time() - started), "topic": topic,
                                    "question": question, "answer": answer, "cost_usd": cost}) + "\n")
        except OSError:
            pass
        self.claude_pending.pop(topic, None)

    # -- requests -----------------------------------------------------------------

    @staticmethod
    def _requests() -> list[dict]:
        try:
            return json.loads(REQUESTS_FILE.read_text()).get("requests", [])
        except (OSError, ValueError, AttributeError):
            return []

    def _requests_text(self) -> str:
        shown = [r for r in self._requests() if r["status"] in ("pending", "approved")]
        shown += [r for r in self._requests() if r["status"] in ("done", "rejected")][-3:]
        if not shown:
            return "none yet"
        return "; ".join(f"#{r['id']} {r['title']} ({r['status']}{': ' + r['note'] if r.get('note') else ''})"
                         for r in shown[-REQUESTS_SHOWN:])

    async def _request(self, ws, target, value, beats):
        title, why = str(target).strip()[:60], str(value).strip()[:200]
        if not title:
            return "request: give it a short title (target) and why (value)"
        requests = self._requests()
        hour = [r for r in requests if time.time() - r["time"] < 3600]
        if len(hour) >= REQUESTS_PER_HOUR:
            return f"request {title!r}: {REQUESTS_PER_HOUR} requests in the last hour already; ask later"
        for r in requests:
            if r["status"] in ("pending", "approved") and difflib.SequenceMatcher(
                    None, norm(r["title"]), norm(title)).ratio() > 0.75:
                return f"request {title!r}: already asked as #{r['id']} {r['title']!r} ({r['status']})"
        entry = {"id": max((r["id"] for r in requests), default=0) + 1, "time": time.time(),
                 "bar": round((time.monotonic() - self.started) / (240 / self.roller.bpm())),
                 "model": self.args.model, "title": title, "why": why, "status": "pending"}
        if not self.args.dry_run:
            REQUESTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = REQUESTS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps({"requests": requests + [entry]}, indent=1))
            tmp.replace(REQUESTS_FILE)
        return f"request #{entry['id']} {title!r}: filed, waiting for the human"

    # -- automation ---------------------------------------------------------------

    def _lane_key(self, target: str) -> tuple[str, float, float] | None:
        """A lane target as the model writes it -> (key, low, high), or None."""
        words = norm(target).replace(":", " ").replace(".", " ").split()
        if not words:
            return None
        if words[0] in ("sidechain", "sc"):
            return "sidechain", 0.0, 1.0
        if "wobble" in words:
            return "bass wobble", 0.125, 8.0
        rest = words[1:] if words[0] in ("acid", "303", "fader", "mixer") else words
        knob = ACID_ALIASES.get(rest[0], rest[0]) if rest else ""
        if words[0] != "fader" and knob in ACID_CCS:
            return f"acid {knob}", 0, 127
        strips = ("chords", "bass", "arp", "fx", "acid", "drums", "breaks")
        if rest and rest[0] in strips and words[0] != "acid":
            return f"fader {rest[0]}", 0.0, 1.0
        return None

    def _oneshot(self, key: str, start: float, end: float, beats: float) -> dict:
        """A glide from start to end over beats, then it holds (a lane that doesn't loop)."""
        if self.lane_task is None or self.lane_task.done():
            self.lane_task = asyncio.create_task(self._run_lanes())
        return {"points": [start, end], "beats": beats, "pos": 0.0, "loop": False, "last": None}

    def _unlane(self, key: str) -> None:
        self.lanes.pop(key, None)

    def _lanes_text(self) -> str:
        lanes = [(k, l) for k, l in self.lanes.items() if l["loop"]]
        if not lanes:
            return "none: nothing moves between your plans"
        return "; ".join(f"{k} [{' '.join(f'{p:g}' for p in l['points'])}] every {l['beats']:g} beats, "
                         f"step {int(l['pos']) % len(l['points']) + 1} of {len(l['points'])}" for k, l in lanes)

    async def _automate(self, ws, target, value, beats):
        found = self._lane_key(target)
        if found is None:
            return (f"automate {target!r}: target acid <knob> ({', '.join(ACID_CCS)}), fader <strip>, "
                    f"sidechain or bass wobble")
        key, low, high = found
        if norm(value) in ("off", "stop", "none", ""):
            return f"automate {key}: stopped" if self.lanes.pop(key, None) else f"automate {key}: wasn't running"
        points = [float(x.replace(",", ".")) for x in re.findall(r"-?\d*[.,]?\d+", value)][:LANE_MAX_STEPS]
        if len(points) < 2:
            return f"automate {key} {value!r}: a lane needs 2..{LANE_MAX_STEPS} numbers"
        clamped = [min(max(p, low), high) for p in points]
        if key.startswith("acid "):
            clamped = [round(p) for p in clamped]
        if key not in self.lanes and sum(1 for l in self.lanes.values() if l["loop"]) >= LANES_MAX:
            return f"automate {key}: {LANES_MAX} lanes already running; stop one first"
        if key.startswith("fader ") and key[6:] in self.ramps:
            self.ramps[key[6:]].cancel()
        step = min(max(beats or 4, 0.5), 32)
        self.lanes[key] = {"points": clamped, "beats": step, "pos": 0.0, "loop": True, "last": None}
        if self.lane_task is None or self.lane_task.done():
            self.lane_task = asyncio.create_task(self._run_lanes())
        note = " (clamped to its range)" if clamped != points[:len(clamped)] else ""
        return (f"automate {key}: {' -> '.join(f'{p:g}' for p in clamped)}, {step:g} beats a step, "
                f"looping every {step * len(clamped) / 4:g} bars{note}")

    async def _run_lanes(self) -> None:
        """Plays the lanes: every tick, each lane's value glides from its step's point to the next."""
        last = time.monotonic()
        while True:
            await asyncio.sleep(60 / self.roller.bpm() / LANE_TICKS_PER_BEAT)
            now = time.monotonic()
            beats_passed, last = (now - last) * self.roller.bpm() / 60, now
            if not self.roller.state.get("playing"):
                continue   # paused with the transport: the lanes hold where they are
            for key, lane in list(self.lanes.items()):
                pts, n = lane["points"], len(lane["points"])
                lane["pos"] += beats_passed / lane["beats"]
                if not lane["loop"] and lane["pos"] >= n - 1:
                    value = pts[-1]
                    del self.lanes[key]
                else:
                    i, frac = int(lane["pos"]) % n, lane["pos"] % 1.0
                    value = pts[i] + (pts[(i + 1) % n] - pts[i]) * frac
                try:
                    await self._send_lane(key, value)
                except Exception as e:   # one bad lane mustn't stop the others
                    print(f"producer: lane {key} dropped ({e})", flush=True)
                    self.lanes.pop(key, None)

    async def _send_lane(self, key: str, value: float) -> None:
        """Sends one lane value to its control (skipping repeats)."""
        if key.startswith("acid "):
            v = min(max(round(value), 0), 127)
            if self.acid.get(key[5:]) != v:
                await self._cc(ACID_CHANNEL, ACID_CCS[key[5:]], v)
                self.acid[key[5:]] = v
        elif key.startswith("fader "):
            if not self.args.dry_run:
                self.sock.sendto(json.dumps({"by": "producer", "strip": key[6:],
                                             "fader": round(min(max(value, 0.0), 1.0), 4)}).encode(), MIXER)
        elif key == "sidechain":
            v = round(min(max(value, 0.0), 1.0), 3)
            if v != self.sidechain_now:
                self.sidechain_now = v
                if not self.args.dry_run:
                    self.sock.sendto(json.dumps({"sidechain": v}).encode(), MIXER)
        elif key == "bass wobble":
            cc = min(max(round(16 * math.log2(max(value, 1e-3) * 8)), 0), 127)
            if self.bass.get("wobble_cc") != cc:
                await self._cc(BASS_CHANNEL, WOBBLE_RATE_CC, cc)
                self.bass["wobble_cc"] = cc
                self.bass["wobble"] = f"{min(8.0, 2 ** (cc / 16) / 8):.3g} swells a beat"

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
