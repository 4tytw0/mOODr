"""The arrangement layer (--arrange): which parts play, and how hard.

Once per section of an I Ching arc, one of MOVES is chosen (by the dice, or a
local model's odds, rolled like the arc's moves) and played out by code over
the next bars: the mixer's faders ramp, and m00Dr's arp rate and drum
mutation step. The choice never carries raw values; the code below sets every
level, so a move can't push a part past the mix it started from.

    hold        nothing changes
    strip       everything but drums and bass fades out (2 bars), the pump relaxes
    take_out    one part fades out (2 bars)
    bring_back  one part that's out fades back in (2 bars)
    build       arp rate up a step, drums busier, more sidechain pump
    drop        every part in at once, on the bar; a build's arp, drums and pump let go
    sweep_out   the tonal parts fade out over 4 bars, ready for the next Roll; the pump relaxes
    push        one part driven into the fader's fuzz for a section, for emphasis;
                it eases back to its level with the next move

With an FX hook (the roller's MIDI out to m800Dr's fx.py, channel 10), a drop
waits for a 2-bar noise riser and lands with an impact as it ends, a strip
brings in the atmosphere (until the next drop) and a sweep_out has a downlifter.

A part's "in" level is its fader when the roller started, so the mix you set
is the ceiling for every move but push, which may go PUSH_ABOVE past it into
the mixer's drive zone (above its KNEE, 0.8). The big moves have cooldowns
(COOLDOWN, in sections), so between them the arrangement moves a part at a
time instead of flipping between drums and bass and everything at once. On
exit the faders, sidechain, arp rate and drum mutation go back to where they were.

The mixer (m800Dr's mixer.py) listens on UDP CONTROL for {"strip", "fader"}
and {"sidechain"}; it writes its state, faders included, to LIVE.
"""

import asyncio
import json
import os
import pathlib
import random
import socket

from .weights import choice_odds, flatten, with_floor

MOVES = ("hold", "strip", "take_out", "bring_back", "build", "drop", "sweep_out", "push", "gate", "wobble")
PARTS = ("chords", "bass", "arp", "acid", "drums", "breaks")
CORE = ("drums", "bass")          # what strip keeps
TONAL = ("chords", "bass", "arp", "acid")
ARP_RATES = ("1/4", "1/8", "1/16")
SIDECHAIN_BUILD_STEP, SIDECHAIN_DROP, SIDECHAIN_MAX = 0.1, 0.6, 0.8   # builds stop at MAX
MUTATE_BUILD_STEP = 25
# Sections that must pass before a move may come again (a section is 15-30 s).
COOLDOWN = {"strip": 6, "sweep_out": 6, "drop": 4, "push": 6, "gate": 3, "wobble": 3}
PUSH_ABOVE, PUSH_MAX = 0.12, 0.95   # how far past its level a push drives a fader, and the top

# fx.py's notes (MIDI ch10): a 2-bar riser, a 4-bar downlifter, the impact, the atmosphere.
FX_RISER_2, FX_DOWN_4, FX_IMPACT, FX_ATMOS = 60, 65, 67, 72
RISER_BARS = 2

CONTROL = ("127.0.0.1", 9810)
LIVE = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "m800dr-mixer.json"
# The mix as it was before the arranger touched it, kept until it's restored, so
# a roller that died mid-strip doesn't take the stripped mix for the real one.
BASE = pathlib.Path.home() / ".local/state/m00drc/arrange-base.json"
RAMP_STEPS_PER_BEAT = 8

INSTRUCTIONS = """\
You arrange a live generative techno set, section by section, like a DJ \
working the mixer. Each section lasts 15 to 30 seconds. Choose the next move:
- hold: leave the arrangement as it is
- strip: drop to drums and bass only
- take_out: remove one part
- bring_back: bring one missing part back in
- build: raise tension (faster arp, busier drums, more pump)
- drop: everything in at once, the release after a build
- sweep_out: fade the music out, leading into a new key
- push: drive one part into distortion for one section, for emphasis
- gate: chop the bass into a rhythm, re-attacked on some 16ths, or let it play legato again
- wobble: change how fast the bass swells: slower or faster than once a beat
A drop only comes after a build. Move mostly one part at a time: take_out and bring_back shape a groove \
gradually. Strip, drop and sweep_out are big moments; push is rare emphasis, \
for when a part really needs to hit harder. Builds should lead to drops, strips \
should rebuild, and the last section before a new Roll suits a sweep_out. \
The break line is the drum break playing now, a bar at a time: its 16ths as x \
(a hit starts there) or . (none), and how many of the 16 hit. A busy break \
already carries the rhythm, so other parts can make room; a sparse one leaves \
space to fill. \
Gate and wobble are bass colour: use them to answer the drums or to lift a \
section without moving the mix. \
Answer with exactly one word: hold, strip, take_out, bring_back, build, drop, \
sweep_out, push, gate or wobble."""

PART_INSTRUCTIONS = """\
You arrange a live generative techno set like a DJ working the mixer. The \
next move is chosen; pick what it acts on, from the ones listed: a part, or \
for gate a bass pattern (x where the note re-attacks) and for wobble a swell \
speed. Weigh \
the drum break: when it's busy, the breaks part carries the rhythm; when it's \
sparse, other parts have room. Answer with exactly one word from the list."""
PART_MOVES = ("take_out", "bring_back", "push", "gate", "wobble")

# The bass's gate patterns and swell speeds: m800Dr sunvoxbass.py's GATES and
# WOBBLES, in the same order (a gate's CC value is its index), on MIDI ch2.
BASS_CHANNEL, BASS_WOBBLE_CC, BASS_GATE_CC = 2, 22, 23
GATES = {"legato": None, "eighths": 0xAAAA, "sixteenths": 0xFFFF, "offbeats": 0x2222,
         "quarters": 0x8888, "dotted": 0x9249, "gallop": 0xB6DB, "broken": 0x8A28}
WOBBLES = {"crawl": 0.25, "slow": 0.5, "beat": 1.0, "fast": 2.0, "frantic": 4.0}


def steps(pattern: int | None) -> str:
    return "legato" if pattern is None else f"{pattern:016b}".replace("1", "x").replace("0", ".")


def mixer_faders() -> tuple[dict[str, float], float] | None:
    """The mixer's current faders and sidechain amount, or None if it isn't running."""
    try:
        state = json.loads(LIVE.read_text())
        return {s["name"]: float(s["fader"]) for s in state["strips"]}, float(state["sidechain"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def starting_mix(controls: dict, keep: bool) -> tuple[dict[str, float], float, dict] | None:
    """The mix to arrange from: the one saved by a run that never restored it,
    if there is one, else the mixer's and m00Dr's now. With `keep`, it's saved
    until restore() clears it (a dry run doesn't save: it changes nothing)."""
    try:
        saved = json.loads(BASE.read_text())
        return saved["faders"], saved["sidechain"], saved["controls"]
    except (OSError, ValueError, KeyError):
        pass
    mix = mixer_faders()
    if mix is None:
        return None
    controls = {k: controls[k] for k in ("arpRate", "drumMutate") if k in controls}
    if keep:
        save_base(mix[0], mix[1], controls)
    return mix[0], mix[1], controls


def save_base(faders: dict[str, float], sidechain: float, controls: dict) -> None:
    BASE.parent.mkdir(parents=True, exist_ok=True)
    BASE.write_text(json.dumps({"faders": faders, "sidechain": sidechain, "controls": controls}))


class Arranger:
    def __init__(self, base: dict[str, float], sidechain: float, controls: dict, dry_run: bool):
        self.controls0 = controls
        self.base = {p: base[p] for p in PARTS if p in base}   # each part's "in" level
        self.level = dict(self.base)
        self.sidechain0 = self.sidechain = sidechain
        self.arp_rate0 = (controls.get("arpRate") or {}).get("value", "1/8")
        self.mutate0 = int((controls.get("drumMutate") or {}).get("value", 0))
        self.arp_rate, self.mutate = self.arp_rate0, self.mutate0
        self.dry_run = dry_run
        self.wanted: str | None = None   # the model's favourite, when it wasn't a valid move
        self.section = 0                   # moves played so far
        self.last: dict[str, int] = {}     # move -> the section it was last played in
        self.pushed: str | None = None     # the part a push is driving, until the next move
        self.built_since = False           # a build since the last strip, drop or sweep_out
        self.fx = None                     # async fx(note, velocity) -> sends to fx.py; 0 = note off
        self.atmos = False
        self.position: tuple[int, int] | None = None   # (section, of n) in the arc, set by the roller
        self.break_info: str | None = None   # the break playing and its transient map, set by the roller
        self.gate, self.wobble = "legato", "beat"   # the bass's, see GATES and WOBBLES
        self.bass_cc = None                  # async bass_cc(cc, value) -> sends to sunvoxbass.py
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # -- what's possible now ------------------------------------------------------

    def parts_in(self) -> list[str]:
        return [p for p in self.base if self.level[p] > 0.01]

    def parts_out(self) -> list[str]:
        return [p for p in self.base if self.level[p] <= 0.01]

    def valid(self) -> tuple[str, ...]:
        ins, outs = self.parts_in(), self.parts_out()
        ok = {"hold": True,
              "strip": len(ins) >= 4 and any(p not in CORE for p in ins),   # from a full groove
              "take_out": len(ins) > 2,
              "bring_back": bool(outs),
              "build": self.arp_rate != ARP_RATES[-1] or self.mutate < 100 or self.sidechain < SIDECHAIN_MAX,
              # A drop releases what a build wound up: a build since the last big move,
              # and something to let go of (parts out, or the build's arp, drums, pump).
              "drop": self.built_since and (bool(outs) or self.built()),
              # Only into the next Roll: the arc's last two sections.
              "sweep_out": any(p in TONAL for p in ins) and (self.position is None
                                                              or self.position[0] >= self.position[1] - 2),
              "push": any(p in TONAL for p in ins),
              "gate": "bass" in ins,
              "wobble": "bass" in ins}
        for move, gap in COOLDOWN.items():
            if move in self.last and self.section + 1 - self.last[move] < gap:
                ok[move] = False
        return tuple(m for m in MOVES if ok[m])

    def built(self) -> bool:
        """Whether a build has raised anything a drop would let go of."""
        return (self.arp_rate != self.arp_rate0 or self.mutate != self.mutate0
                or abs(self.sidechain - SIDECHAIN_DROP) > 0.01)

    def describe(self) -> str:
        return (f"in: {', '.join(self.parts_in()) or 'nothing'}; out: {', '.join(self.parts_out()) or 'nothing'}; "
                f"arp {self.arp_rate}, drums busy {self.mutate}%, pump {self.sidechain:.2f}, "
                f"bass gate {self.gate}, wobble {self.wobble}"
                + (f"\nBreak: {self.break_info}" if self.break_info else ""))

    def part_choices(self, move: str) -> list[str]:
        """The parts `move` may act on now (drums never go out on their own; push is tonal only)."""
        if move == "take_out":
            return [p for p in self.parts_in() if p != "drums"] or self.parts_in()
        if move == "bring_back":
            return self.parts_out()
        if move == "push":
            return [p for p in self.parts_in() if p in TONAL]
        if move == "gate":
            return [g for g in GATES if g != self.gate]
        if move == "wobble":
            return [w for w in WOBBLES if w != self.wobble]
        return []

    # -- choosing -----------------------------------------------------------------

    def dice(self, rng: random.Random) -> tuple[str, dict[str, float] | None]:
        return rng.choice(self.valid()), None

    def model_odds(self, api: str, model: str, history: list[str], now: str) -> dict[str, float] | None:
        """Blocking. The model's odds over the valid moves only, or None."""
        user = ("Sections so far:\n" + ("\n".join(history) or "(none yet)")
                + f"\n\nNow: {now}\nThe arrangement: {self.describe()}\n"
                f"Possible moves now: {', '.join(self.valid())}.\nWhat is the next move?")
        odds = choice_odds(api, model, MOVES, INSTRUCTIONS, user)
        if odds is None:
            return None
        top = max(odds, key=odds.get)
        valid = {m: p for m, p in odds.items() if m in self.valid()}
        # Its favourite isn't possible now: the rest of its odds are close to noise.
        self.wanted = top if top not in valid and odds[top] > 0.5 else None
        return valid if sum(valid.values()) > 0 else None

    def model_part(self, api: str, model: str, move: str, history: list[str], now: str) -> str | None:
        """Blocking. The part the model would have `move` act on, or None (the dice pick it)."""
        choices = self.part_choices(move)
        if len(choices) < 2:
            return None
        user = ("Sections so far:\n" + ("\n".join(history) or "(none yet)")
                + f"\n\nNow: {now}\nThe arrangement: {self.describe()}\n"
                + (f"The next move: gate. Bass patterns: "
                   + ", ".join(f"{g} {steps(GATES[g])}" for g in choices) + ".\nWhich pattern?" if move == "gate"
                   else f"The next move: wobble. Swell speeds, per beat: "
                   + ", ".join(f"{w} {WOBBLES[w]:g}" for w in choices) + ".\nWhich speed?" if move == "wobble"
                   else f"The next move: {move}. Parts it can act on: {', '.join(choices)}.\nWhich part?"))
        odds = choice_odds(api, model, tuple(choices), PART_INSTRUCTIONS, user)
        return max(odds, key=odds.get) if odds else None

    def roll(self, odds: dict[str, float], temperature: float, floor: float, rng: random.Random) -> str:
        """Rolls the odds over the moves valid now; `floor` spreads some chance over all of them."""
        odds = {m: odds.get(m, 0.0) for m in self.valid()}
        weights = with_floor(flatten(odds, temperature), floor)
        return rng.choices(list(weights), weights=list(weights.values()))[0]

    # -- playing a move -----------------------------------------------------------

    def _send(self, message: dict) -> None:
        if not self.dry_run:
            self.sock.sendto(json.dumps(message).encode(), CONTROL)

    async def _ramp(self, targets: dict[str, float], beats: float, beat_s: float) -> None:
        """Moves the faders in `targets` from where they are, linearly, over `beats`."""
        start = {p: self.level[p] for p in targets}
        steps = max(1, int(beats * RAMP_STEPS_PER_BEAT))
        for i in range(1, steps + 1):
            for p, target in targets.items():
                self.level[p] = start[p] + (target - start[p]) * i / steps
                self._send({"by": "arranger", "strip": p, "fader": round(self.level[p], 4)})
            await asyncio.sleep(beat_s / RAMP_STEPS_PER_BEAT)

    async def _set_m00dr(self, ws, control: str, value) -> None:
        if not self.dry_run and ws is not None:
            await ws.send(json.dumps({"op": "set", "id": control, "value": value}))

    def _set_sidechain(self, amount: float) -> None:
        self.sidechain = min(max(amount, 0.0), 1.0)
        self._send({"sidechain": round(self.sidechain, 3)})

    async def _bass(self, cc: int, value: int) -> None:
        if self.bass_cc is not None and not self.dry_run:
            await self.bass_cc(cc, value)

    async def _fx(self, note: int, velocity: int = 100) -> None:
        if self.fx is not None and not self.dry_run:
            await self.fx(note, velocity)

    async def play(self, move: str, ws, bpm: float, rng: random.Random, part: str | None = None) -> str:
        """Plays `move` out; returns what it did, for the log and the model's history.
        `part` is the model's pick for a PART_MOVES move; without one (or if it
        isn't possible) the dice pick."""
        if part not in self.part_choices(move):
            part = None
        beat_s = 60.0 / bpm
        self.section += 1
        self.last[move] = self.section
        if move == "build":
            self.built_since = True
        elif move in ("strip", "drop", "sweep_out"):
            self.built_since = False
        if self.pushed:   # last section's push eases back first
            part, self.pushed = self.pushed, None
            await self._ramp({part: self.base[part]}, 2, beat_s)
        if move == "gate":
            self.gate = part or rng.choice(self.part_choices("gate"))
            await self._bass(BASS_GATE_CC, list(GATES).index(self.gate))
            return f"gate (bass {self.gate})"
        if move == "wobble":
            self.wobble = part or rng.choice(self.part_choices("wobble"))
            i = list(WOBBLES).index(self.wobble)
            await self._bass(BASS_WOBBLE_CC, (2 * i + 1) * 128 // (2 * len(WOBBLES)))
            return f"wobble (bass {self.wobble})"
        if move == "push":
            part = part or rng.choice(self.part_choices("push"))
            self.pushed = part
            await self._ramp({part: min(PUSH_MAX, self.base[part] + PUSH_ABOVE)}, 2, beat_s)
            return f"push ({part} into the fuzz)"
        if move == "strip":
            gone = [p for p in self.parts_in() if p not in CORE]
            self._set_sidechain(self.sidechain0)
            if not self.atmos:
                await self._fx(FX_ATMOS, 100)
                self.atmos = True
            await self._ramp({p: 0.0 for p in gone}, 8, beat_s)
            return f"strip ({', '.join(gone)} out)"
        if move == "take_out":
            part = part or rng.choice(self.part_choices("take_out"))
            await self._ramp({part: 0.0}, 8, beat_s)
            return f"take_out ({part})"
        if move == "bring_back":
            part = part or rng.choice(self.part_choices("bring_back"))
            await self._ramp({part: self.base[part]}, 8, beat_s)
            return f"bring_back ({part})"
        if move == "build":
            if self.arp_rate != ARP_RATES[-1]:
                self.arp_rate = ARP_RATES[min(ARP_RATES.index(self.arp_rate) + 1, len(ARP_RATES) - 1)] \
                    if self.arp_rate in ARP_RATES else ARP_RATES[1]
                await self._set_m00dr(ws, "arpRate", self.arp_rate)
            self.mutate = min(100, self.mutate + MUTATE_BUILD_STEP)
            await self._set_m00dr(ws, "drumMutate", self.mutate)
            self._set_sidechain(min(SIDECHAIN_MAX, max(self.sidechain, self.sidechain + SIDECHAIN_BUILD_STEP)))
            return f"build (arp {self.arp_rate}, drums {self.mutate}%, pump {self.sidechain:.2f})"
        if move == "drop":
            await self._fx(FX_RISER_2, 120)          # the riser, and the drop lands as it ends
            await asyncio.sleep(RISER_BARS * 4 * beat_s)
            await self._fx(FX_IMPACT, 110)
            if self.atmos:
                await self._fx(FX_ATMOS, 0)
                self.atmos = False
            outs = self.parts_out()
            self._set_sidechain(SIDECHAIN_DROP)
            self.arp_rate, self.mutate = self.arp_rate0, self.mutate0
            await self._set_m00dr(ws, "arpRate", self.arp_rate)
            await self._set_m00dr(ws, "drumMutate", self.mutate)
            await self._ramp({p: self.base[p] for p in outs}, 0.25, beat_s)   # in on the beat
            brought = f"{', '.join(outs)} in" if outs else "all already in"
            return f"drop ({brought}, pump {self.sidechain:.2f})"
        if move == "sweep_out":
            gone = [p for p in self.parts_in() if p in TONAL]
            self._set_sidechain(self.sidechain0)
            await self._fx(FX_DOWN_4, 110)
            await self._ramp({p: 0.0 for p in gone}, 16, beat_s)
            return f"sweep_out ({', '.join(gone)})"
        return "hold"

    async def restore(self, ws) -> None:
        """Puts the mix and m00Dr's arp rate and drums back as they were."""
        self.pushed = None
        if self.atmos:
            try:
                await self._fx(FX_ATMOS, 0)
            except Exception:
                pass
            self.atmos = False
        for p, level in self.base.items():
            self._send({"by": "arranger", "strip": p, "fader": level})
        self._set_sidechain(self.sidechain0)
        self.arp_rate, self.mutate = self.arp_rate0, self.mutate0
        self.gate, self.wobble = "legato", "beat"
        try:
            await self._bass(121, 0)   # sunvoxbass: reset all controllers = legato, one swell a beat
        except Exception:
            pass
        try:
            await self._set_m00dr(ws, "arpRate", self.arp_rate0)
            await self._set_m00dr(ws, "drumMutate", self.mutate0)
        except Exception:
            pass   # m00Dr already gone: nothing to put back there
        self.level = dict(self.base)
        if not self.dry_run:
            BASE.unlink(missing_ok=True)
