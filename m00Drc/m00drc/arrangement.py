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

A part's "in" level is its fader when the roller started, so the mix you set
is the ceiling (no fuzz: that's above KNEE). On exit the faders, sidechain,
arp rate and drum mutation go back to where they were.

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

MOVES = ("hold", "strip", "take_out", "bring_back", "build", "drop", "sweep_out")
PARTS = ("chords", "bass", "arp", "acid", "drums", "breaks")
CORE = ("drums", "bass")          # what strip keeps
TONAL = ("chords", "bass", "arp", "acid")
ARP_RATES = ("1/4", "1/8", "1/16")
SIDECHAIN_BUILD_STEP, SIDECHAIN_DROP, SIDECHAIN_MAX = 0.1, 0.6, 0.8   # builds stop at MAX
MUTATE_BUILD_STEP = 25

CONTROL = ("127.0.0.1", 9810)
LIVE = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "m800dr-mixer.json"
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
Builds should lead to drops, strips should rebuild, and the last section \
before a new Roll suits a sweep_out. Answer with exactly one word: hold, strip, \
take_out, bring_back, build, drop or sweep_out."""


def mixer_faders() -> tuple[dict[str, float], float] | None:
    """The mixer's current faders and sidechain amount, or None if it isn't running."""
    try:
        state = json.loads(LIVE.read_text())
        return {s["name"]: float(s["fader"]) for s in state["strips"]}, float(state["sidechain"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


class Arranger:
    def __init__(self, base: dict[str, float], sidechain: float, controls: dict, dry_run: bool):
        self.base = {p: base[p] for p in PARTS if p in base}   # each part's "in" level
        self.level = dict(self.base)
        self.sidechain0 = self.sidechain = sidechain
        self.arp_rate0 = (controls.get("arpRate") or {}).get("value", "1/8")
        self.mutate0 = int((controls.get("drumMutate") or {}).get("value", 0))
        self.arp_rate, self.mutate = self.arp_rate0, self.mutate0
        self.dry_run = dry_run
        self.wanted: str | None = None   # the model's favourite, when it wasn't a valid move
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # -- what's possible now ------------------------------------------------------

    def parts_in(self) -> list[str]:
        return [p for p in self.base if self.level[p] > 0.01]

    def parts_out(self) -> list[str]:
        return [p for p in self.base if self.level[p] <= 0.01]

    def valid(self) -> tuple[str, ...]:
        ins, outs = self.parts_in(), self.parts_out()
        ok = {"hold": True,
              "strip": any(p not in CORE for p in ins),
              "take_out": len(ins) > 2,
              "bring_back": bool(outs),
              "build": self.arp_rate != ARP_RATES[-1] or self.mutate < 100 or self.sidechain < SIDECHAIN_MAX,
              # A drop needs something to release: parts out, or a build to let go of.
              "drop": bool(outs) or self.built(),
              "sweep_out": any(p in TONAL for p in ins)}
        return tuple(m for m in MOVES if ok[m])

    def built(self) -> bool:
        """Whether a build has raised anything a drop would let go of."""
        return (self.arp_rate != self.arp_rate0 or self.mutate != self.mutate0
                or abs(self.sidechain - SIDECHAIN_DROP) > 0.01)

    def describe(self) -> str:
        return (f"in: {', '.join(self.parts_in()) or 'nothing'}; out: {', '.join(self.parts_out()) or 'nothing'}; "
                f"arp {self.arp_rate}, drums busy {self.mutate}%, pump {self.sidechain:.2f}")

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
                self._send({"strip": p, "fader": round(self.level[p], 4)})
            await asyncio.sleep(beat_s / RAMP_STEPS_PER_BEAT)

    async def _set_m00dr(self, ws, control: str, value) -> None:
        if not self.dry_run and ws is not None:
            await ws.send(json.dumps({"op": "set", "id": control, "value": value}))

    def _set_sidechain(self, amount: float) -> None:
        self.sidechain = min(max(amount, 0.0), 1.0)
        self._send({"sidechain": round(self.sidechain, 3)})

    async def play(self, move: str, ws, bpm: float, rng: random.Random) -> str:
        """Plays `move` out; returns what it did, for the log and the model's history."""
        beat_s = 60.0 / bpm
        if move == "strip":
            gone = [p for p in self.parts_in() if p not in CORE]
            self._set_sidechain(self.sidechain0)
            await self._ramp({p: 0.0 for p in gone}, 8, beat_s)
            return f"strip ({', '.join(gone)} out)"
        if move == "take_out":
            part = rng.choice([p for p in self.parts_in() if p != "drums"] or self.parts_in())
            await self._ramp({part: 0.0}, 8, beat_s)
            return f"take_out ({part})"
        if move == "bring_back":
            part = rng.choice(self.parts_out())
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
            await self._ramp({p: 0.0 for p in gone}, 16, beat_s)
            return f"sweep_out ({', '.join(gone)})"
        return "hold"

    async def restore(self, ws) -> None:
        """Puts the mix and m00Dr's arp rate and drums back as they were."""
        for p, level in self.base.items():
            self._send({"strip": p, "fader": level})
        self._set_sidechain(self.sidechain0)
        self.arp_rate, self.mutate = self.arp_rate0, self.mutate0
        try:
            await self._set_m00dr(ws, "arpRate", self.arp_rate0)
            await self._set_m00dr(ws, "drumMutate", self.mutate0)
        except Exception:
            pass   # m00Dr already gone: nothing to put back there
        self.level = dict(self.base)
