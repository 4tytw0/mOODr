"""Phase-shifted random rolls of m00Dr's parameters and its synths' sound.

One seeded stream of 7-bit random ints (0..127, a MIDI CC value), read by
several lanes. A lane is either one of m00Dr's controls, set over its web
remote, or a sound parameter: a CC sent to the synths over ALSA MIDI from
"m00Drc-Roller:sound out" (SOUND_LANES; m800Dr's links.sh wires it to
sunvox303.py, which eases each CC in over half a second). Each lane is the
same stream shifted in two ways:

  * lag:   lane k reads the int drawn `lag` cycles ago. A value that shows up
           on one lane comes back on the next lane a cycle later, the way a
           shift register works. So the changes chase each other through the
           controls instead of being unrelated noise. m00Dr's lanes and the
           sound lanes are two separate chains, each starting at lag 0.
  * phase: where in the cycle the lane fires (0..1). The lanes are spread
           out across the cycle, so the controls change one at a time, on the
           beat, instead of all jumping at the downbeat.

`--shift` rotates every phase by the same amount. That moves the whole
pattern relative to the bar without changing it.

I Ching: each Roll rolls an arc for everything else, spanning the time until
the next Roll (2 to 4 minutes). When a Roll lands, m00Dr's web remote sends
its six lines ("cast"), and the roller splits the time from then to the next
Roll into SECTIONS equal sections. The first is the Roll's own hexagram; each
one after it casts six lines with three coins, as m00Dr's oracle does, and
turns the lines that come up changing (each 1 in 4), so the arc usually
moves a line or two, now and then holds, and now and then leaps. A hexagram
is an int by reading its lines as binary, bottom line lowest, so a low line
turning is a nudge and a high line a leap. Each chord is then a cycle: when
m00Dr moves to a chord, the current section's int goes onto the head of the
stream and the lanes fire across that bar, so the controls settle onto each
section a lane at a time and land on the last one as the next Roll comes.
A lane doesn't send the value it last sent again. Before the first Roll (or
with --no-iching), the stream is the seeded dice, one draw per `--bars` cycle.

With --weights model, an arc's sections aren't tossed with coins up front:
each is decided as the section before it starts, by a local model (llama-swap,
--model) choosing to hold, nudge, shift or leap, with its odds rolled (see
weights.py). A section it can't decide in time is tossed as usual.

With --arrange dice|model, each section also gets an arrangement move (strip,
build, drop, sweep out...: see arrangement.py), played on the mixer's faders
and m00Dr's arp rate and drum mutation. The mix goes back as it was on exit.

Breaks (m800Dr's breaks.py, fader 8): the roller also drives them over
"sound out" on BREAKS_CHANNEL. Each landed Roll sends a program change, the
cast's hexagram as binary (0..63), which picks that Roll's break; every chord
change in an arc casts six fresh lines and sends them as two CCs (yang bits,
changing bits) that re-chop the next bar.

The acid line's notes change only every ACID_EVERY bars, all at once on the
bar line: the lanes that reshape it (ACID_NOTE_LANES: noise, wide deviation
and a fresh randomize) fire together then, instead of at their own phases,
so a riff holds for four bars before it moves.

Roll rewrites the key, scale and progression, so it is held back: after a
roll, the next one waits ROLL_GAP seconds, 2 to 4 minutes, set by the int that
triggered it. The first roll waits the minimum after the roller starts, so
restarts can't roll in quick succession.

Knobs: the roller has an ALSA MIDI input, "m00Drc-Roller:knobs in", that
m800Dr's synths/links.sh feeds from the mixer's "controls" output (every CC
from the Launch Control XL that isn't a fader). Knobs are absolute: the first
turn takes over from the command-line value. Any MIDI channel.

    column 4 top     CC 16  shift   0..1 of a cycle
    column 4 middle  CC 32  bars    1..8 per cycle
    column 4 bottom  CC 52  lanes   0..all; 0 stops rolling, n uses the first n
                                    (m00Dr's lanes, then the sound lanes)
    column 6 top     CC 18  seed    0..127, reseeds the stream

The cycle is timed from m00Dr's tempo (`--bars` bars of 4/4). Firings land on
16th-note boundaries, measured from when the roller connects or when m00Dr
starts playing. They are not locked to m00Dr's clock: the roller has no
access to MIDI clock.

Needs m00Dr running with "Web remote" ticked (it listens on port 8766).

    uv run python -m m00drc.roller                  # localhost, 4-bar cycle
    uv run python -m m00drc.roller --seed 7 --bars 2 --shift 0.25
    uv run python -m m00drc.roller --lanes roll,mode,acid.cutoff --dry-run
"""

import argparse
import asyncio
import json
import random
import signal
import time
from collections import deque
from dataclasses import dataclass

import websockets

from .arrangement import Arranger, mixer_faders
from .weights import MOVES, ModelMover, turn

# Draws are 7-bit, 0..INT_MAX: the range of a MIDI CC, so a sound lane sends
# its int as is. m00Dr's controls fold it into their own range with modulo.
INT_MAX = 127

# A Roll's arc: this many sections, evenly over the time to the next Roll.
SECTIONS = 8

# m800Dr's breaks.py: its MIDI channel (1-based) and the CCs that carry a
# chop's six lines (see breaks.chop).
BREAKS_CHANNEL, CHOP_LINES_CC, CHOP_CHANGING_CC = 8, 20, 21

# Seconds between rolls: ROLL_GAP[0] at int 0 up to ROLL_GAP[1] at INT_MAX.
ROLL_GAP = (120.0, 240.0)

# Lanes that change the acid line's notes, and how often they may: every
# ACID_EVERY bars, on the bar line, whatever the cycle length. Randomize
# always fires then (it is the 4-bar re-roll); noise and wide take their int.
ACID_NOTE_LANES = ("acidNoise", "acidWide", "randomize")
ACID_EVERY = 4

# Sound lane -> (MIDI channel, 1-based; CC). The CCs are the ones m800Dr's
# sunvox303.py maps onto the SunVox acid patch (its SOUND_CCS).
SOUND_LANES = {
    "acid.cutoff": (5, 74),
    "acid.resonance": (5, 71),
    "acid.decay": (5, 75),
    "acid.accent": (5, 76),
    "acid.glide": (5, 5),
    "acid.drive": (5, 77),
}

# Launch Control XL factory template 1: top knobs CC 13-20, middle 29-36,
# bottom 49-56 (assumed, as in m800Dr; check on the /mixer page's "last
# unmapped CC"). Columns 4 and 6 have no mixer strip and no other knob jobs.
SHIFT_CC, BARS_CC, LANES_CC, SEED_CC = 16, 32, 52, 18
MAX_BARS = 8


@dataclass
class Lane:
    control: str        # m00Dr web-remote id, "roll"/"randomize" to click it,
                        # or a SOUND_LANES name
    lag: int = 0        # cycles behind the head of the stream
    phase: float = 0.0  # 0..1, where in the cycle it fires
    every: int = 0      # if set, fires every this many 16ths from Play instead


# The default lanes. Key isn't one of them because Roll already moves the key
# around the circle of fifths. BPM is left alone because a DAW usually owns it,
# and the arp's pattern, rate and octave and the stabs toggle because they're
# played by hand.
DEFAULT_LANES = ["roll", "mode", "acidNoise", "acidWide", "randomize", *SOUND_LANES]


class PhaseShiftedInts:
    """A seeded random-int stream that lanes read at a lag."""

    def __init__(self, seed: int | None, depth: int):
        self.rng = random.Random(seed)   # also rolls the I Ching arcs
        self._history = deque(maxlen=depth + 1)

    def reseed(self, seed: int) -> None:
        # Keeps the history, so values already drawn carry on down the lanes.
        self.rng.seed(seed)

    def advance(self, value: int | None = None) -> None:
        """Pushes `value` onto the head of the stream, or a random draw."""
        self._history.appendleft(self.rng.randint(0, INT_MAX) if value is None else value)

    def read(self, lag: int) -> int | None:
        return self._history[lag] if lag < len(self._history) else None


def toss(rng: random.Random) -> int:
    """One line: three coins summed, 0..3. 0 and 3 are the changing lines."""
    return sum(rng.getrandbits(1) for _ in range(3))


def hexagram_int(lines: list[bool]) -> int:
    """Six lines (yang True, bottom first) as binary, spread over 0..INT_MAX."""
    return sum(1 << i for i, yang in enumerate(lines) if yang) * INT_MAX // 63


def hexagram_text(lines: list[bool]) -> str:
    """Six lines bottom first, drawn top line first, the way it's read."""
    return "".join("⚊" if yang else "⚋" for yang in reversed(lines))


def cast_lines(cast: list[int]) -> list[bool]:
    """A cast's own hexagram: lines 0..3, yang = 2 or 3."""
    return [value >= 2 for value in cast]


def toss_turn(lines: list[bool], rng: random.Random) -> list[bool]:
    """The next section with the dice: the lines a fresh cast comes up changing on turn."""
    return [not yang if toss(rng) in (0, 3) else yang for yang in lines]


def roll_arc(cast: list[int], rng: random.Random) -> list[list[bool]]:
    """SECTIONS hexagrams: the cast's own, then each one turning the lines a
    fresh cast comes up changing on."""
    lines = cast_lines(cast)
    arc = [lines]
    for _ in range(SECTIONS - 1):
        lines = toss_turn(lines, rng)
        arc.append(lines)
    return arc


@dataclass
class Arc:
    start: float               # time.monotonic()
    end: float                 # the next Roll
    sections: list[list[bool]]
    # Sections the arc will have. With --weights model they're appended one at
    # a time, so a section whose move is late holds the last one until it lands.
    count: int = SECTIONS

    @property
    def span(self) -> float:
        return (self.end - self.start) / self.count

    def section(self, now: float) -> int:
        return min(int((now - self.start) / self.span), self.count - 1, len(self.sections) - 1)


def playing_chord(state: dict) -> int | None:
    """Which of m00Dr's chord pads is sounding, or None."""
    return next((i for i, pad in enumerate(state.get("pads") or []) if pad.get("playing")), None)


def make_lanes(names: list[str], shift: float) -> list[Lane]:
    # Spread the phased lanes evenly and add the shift, so the k-th of n fires
    # at (k/n + shift) mod 1; the acid-note lanes fire on their own 4-bar grid.
    # A lane's lag counts up within its chain (m00Dr or sound), so each lane
    # gets the value the lane before it had one cycle earlier.
    phased = [name for name in names if name not in ACID_NOTE_LANES]
    lags = {False: 0, True: 0}
    lanes = []
    for name in names:
        sound = name in SOUND_LANES
        if name in ACID_NOTE_LANES:
            lane = Lane(name, lag=lags[sound], every=16 * ACID_EVERY)
        else:
            k = phased.index(name)
            lane = Lane(name, lag=lags[sound], phase=(k / len(phased) + shift) % 1.0)
        lanes.append(lane)
        lags[sound] += 1
    return lanes


def to_message(lane: Lane, value: int, control: dict | None) -> dict | None:
    """Fold a random int into whatever this control accepts."""
    if lane.control in SOUND_LANES:
        return {"cc": SOUND_LANES[lane.control], "value": value}
    if lane.control == "roll":
        return {"op": "click", "id": "roll"}  # Roller._fire holds it to ROLL_GAP
    if lane.control == "randomize":
        return {"op": "click", "id": lane.control}  # on the ACID_EVERY grid
    if control is None or not control.get("enabled", True):
        return None
    current = control.get("value")
    if "items" in control:
        items = control["items"]
        return {"op": "set", "id": lane.control, "value": items[value % len(items)]}
    if isinstance(current, bool):
        return {"op": "set", "id": lane.control, "value": bool(value & 1)}
    if "min" in control:
        lo, hi = control["min"], control["max"]
        return {"op": "set", "id": lane.control, "value": lo + value % (hi - lo + 1)}
    return None


class Roller:
    def __init__(self, args):
        self.args = args
        self.bars, self.shift, self.active = args.bars, args.shift, len(args.lanes)
        self.lanes = make_lanes(args.lanes, self.shift)
        self.stream = PhaseShiftedInts(args.seed, depth=len(args.lanes))
        self.state: dict = {}
        self.closed = False
        self.next_roll = time.monotonic() + ROLL_GAP[0]
        self.midi = self.sound_out = None
        # Set by _read when m00Dr moves to another chord, so _loop can start
        # that chord's cycle right away rather than at its next 16th.
        self.chord_changed = asyncio.Event()
        self.arc: Arc | None = None
        self.arc_cast: list[int] | None = None   # the cast self.arc was rolled from
        self.mover = (ModelMover(args.api, args.model, args.temperature)
                      if args.weights == "model" else None)
        self.arc_task: asyncio.Task | None = None  # fills a model-weighted arc
        self.arranger: Arranger | None = None
        self.breaks_program = 0             # the break for the current Roll (see _send_breaks)
        self.breaks_sent: int | None = None
        # Lane -> the last value it sent. A lane doesn't send the same value
        # twice in a row, so a section's int isn't re-sent every bar, and a
        # control changed by hand keeps that until the stream moves on.
        self.sent: dict[str, object] = {}

    def on_cc(self, cc: int, value: int) -> None:
        """A knob turned (0..127). Called on the event loop."""
        names = self.args.lanes
        if cc == SHIFT_CC:
            self.shift = value / 128
        elif cc == BARS_CC:
            self.bars = 1 + value * MAX_BARS // 128
        elif cc == LANES_CC:
            self.active = round(value * len(names) / 127)
        elif cc == SEED_CC:
            self.stream.reseed(value)
            print(f"knob: seed {value}", flush=True)
            return
        else:
            return
        self.lanes = make_lanes(names, self.shift)[:self.active]
        print(f"knob: shift {self.shift:.2f}, {self.bars} bars, "
              f"{self.active}/{len(names)} lanes", flush=True)

    def _open_midi(self) -> None:
        """ALSA client "m00Drc-Roller": knobs in, sound CCs out. Linux only."""
        from alsa_midi import AsyncSequencerClient, PortType, READ_PORT, WRITE_PORT
        kind = PortType.MIDI_GENERIC | PortType.APPLICATION
        self.midi = AsyncSequencerClient("m00Drc-Roller")
        # Keep references: alsa-midi deletes a port when it is garbage-collected.
        self.knobs_in = self.midi.create_port("knobs in", caps=WRITE_PORT, type=kind)
        self.sound_out = self.midi.create_port("sound out", caps=READ_PORT, type=kind)
        print(f"MIDI: ALSA client {self.midi.client_id} 'm00Drc-Roller', "
              "ports 'knobs in' and 'sound out'", flush=True)

    async def _listen_knobs(self) -> None:
        from alsa_midi import ControlChangeEvent
        while True:
            ev = await self.midi.event_input()
            if isinstance(ev, ControlChangeEvent):
                self.on_cc(ev.param, ev.value)

    async def _send_breaks(self) -> None:
        """On a chord change: the Roll's break (if it changed) and a fresh chop."""
        lines = [toss(self.stream.rng) for _ in range(6)]
        yang = sum(1 << i for i, v in enumerate(lines) if v >= 2)
        changing = sum(1 << i for i, v in enumerate(lines) if v in (0, 3))
        print(f"     breaks: break {self.breaks_program}, chop {yang:06b}/{changing:06b}", flush=True)
        if self.args.dry_run or self.sound_out is None:
            return
        from alsa_midi import ProgramChangeEvent
        if self.breaks_program != self.breaks_sent:
            await self.midi.event_output(ProgramChangeEvent(channel=BREAKS_CHANNEL - 1, value=self.breaks_program),
                                         port=self.sound_out)
            self.breaks_sent = self.breaks_program
        await self._send_cc(BREAKS_CHANNEL, CHOP_LINES_CC, yang)
        await self._send_cc(BREAKS_CHANNEL, CHOP_CHANGING_CC, changing)

    async def _send_cc(self, channel: int, cc: int, value: int) -> None:
        from alsa_midi import ControlChangeEvent
        await self.midi.event_output(
            ControlChangeEvent(channel=channel - 1, param=cc, value=value), port=self.sound_out)
        await self.midi.drain_output()

    def bpm(self) -> float:
        try:
            return float(self.state["controls"]["bpm"]["value"])
        except (KeyError, TypeError, ValueError):
            return 120.0

    def follow_cast(self) -> None:
        """Rolls a new arc when a new Roll has landed (or on the first one
        seen), from now until the next Roll the roll lane will allow."""
        cast = self.state.get("cast")
        if not self.args.iching or not cast or (self.arc and cast == self.arc_cast):
            return
        now = time.monotonic()
        # A Roll clicked by hand, or the one the roller is waiting on being
        # close: still give the arc a full minimum gap.
        end = self.next_roll if self.next_roll - now >= ROLL_GAP[0] / 2 else now + ROLL_GAP[0]
        self.breaks_program = sum(1 << i for i, value in enumerate(cast) if value >= 2)
        if self.mover:
            if self.arc_task:
                self.arc_task.cancel()
            self.arc, self.arc_cast = Arc(now, end, [cast_lines(cast)]), cast
            self.arc_task = asyncio.create_task(self._fill_arc(self.arc))
            print(f"arc: {SECTIONS} sections of {self.arc.span:.0f}s, moves by {self.mover.model}: "
                  f"{hexagram_text(self.arc.sections[0])}={hexagram_int(self.arc.sections[0])}", flush=True)
            return
        self.arc, self.arc_cast = Arc(now, end, roll_arc(cast, self.stream.rng)), cast
        print(f"arc: {SECTIONS} sections of {self.arc.span:.0f}s: " + " ".join(
            f"{hexagram_text(lines)}={hexagram_int(lines)}" for lines in self.arc.sections), flush=True)

    def _now_text(self) -> str:
        """The state the model is shown with each question."""
        controls = self.state.get("controls", {})
        mode = (controls.get("mode") or {}).get("value", "?")
        chord = playing_chord(self.state)
        return f"mode {mode}, {self.bpm():.0f} BPM, chord pad {'-' if chord is None else chord + 1}"

    async def _fill_arc(self, arc: Arc) -> None:
        """Appends a model-weighted arc's sections, deciding each one as the
        section before it starts, so the model has a section's length to
        answer. A section the model can't decide is tossed with the dice."""
        history = [f"section 1: {hexagram_text(arc.sections[0])} = {hexagram_int(arc.sections[0])} (the Roll)"]
        for k in range(1, arc.count):
            await asyncio.sleep(max(0.0, arc.start + (k - 1) * arc.span - time.monotonic()))
            started = time.monotonic()
            probs = await asyncio.to_thread(self.mover.probabilities, history, self._now_text())
            lines = arc.sections[-1]
            if probs is None:
                move, lines = "dice", toss_turn(lines, self.stream.rng)
                odds = "model unavailable"
            else:
                move = self.mover.roll(probs, self.stream.rng)
                lines = turn(lines, move, self.stream.rng)
                odds = " ".join(f"{m} {probs[m]:.2f}" for m in MOVES)
            arc.sections.append(lines)
            history.append(f"section {k + 1}: {move} -> {hexagram_text(lines)} = {hexagram_int(lines)}")
            print(f"move {k + 1}/{arc.count}: {move:<5} -> {hexagram_text(lines)}={hexagram_int(lines):<3} "
                  f"({odds}; {time.monotonic() - started:.1f}s)", flush=True)

    def section_int(self) -> tuple[int, int] | None:
        """The arc's current section and its int, or None before a Roll,
        while no chord is playing or with --no-iching."""
        if self.arc is None or playing_chord(self.state) is None:
            return None
        k = self.arc.section(time.monotonic())
        return k, hexagram_int(self.arc.sections[k])

    async def run(self) -> None:
        uri = f"ws://{self.args.host}:{self.args.port}"
        async with websockets.connect(uri) as ws:
            print(f"connected to m00Dr at {uri}", flush=True)
            self.state = json.loads(await ws.recv())
            tasks = [asyncio.create_task(self._read(ws))]
            if not self.args.no_midi:
                self._open_midi()
                tasks.append(asyncio.create_task(self._listen_knobs()))
            if self.args.arrange != "off":
                mix = mixer_faders()
                if mix is None:
                    print("arrange: m800Dr's mixer isn't running, so no arrangement moves", flush=True)
                else:
                    self.arranger = Arranger(*mix, self.state.get("controls", {}), self.args.dry_run)
                    print(f"arrange: {self.args.arrange}; {self.arranger.describe()}", flush=True)
                    tasks.append(asyncio.create_task(self._arrange(ws)))
            try:
                await self._loop(ws)
            finally:
                for task in tasks:
                    task.cancel()
                if self.arranger:
                    await self.arranger.restore(ws)
                    print("arrange: mix restored", flush=True)

    async def _arrange(self, ws) -> None:
        """One arrangement move per arc section, chosen as the section starts
        and played out over its first bars."""
        seen, history = None, []
        while True:
            await asyncio.sleep(0.25)
            arc = self.arc
            if arc is None or playing_chord(self.state) is None:
                continue
            k = arc.section(time.monotonic())
            if (id(arc), k) == seen:
                continue
            seen = (id(arc), k)
            to_roll = max(0.0, self.next_roll - time.monotonic())
            now = f"section {k + 1} of {arc.count}, next Roll in {to_roll:.0f}s, {self._now_text()}"
            started = time.monotonic()
            if self.args.arrange == "model":
                odds = await asyncio.to_thread(self.arranger.model_odds, self.args.api, self.args.model,
                                               history, now)
                move = (Arranger.roll(odds, self.args.temperature, self.stream.rng) if odds
                        else self.arranger.dice(self.stream.rng)[0])
            else:
                odds, move = None, self.arranger.dice(self.stream.rng)[0]
            took = time.monotonic() - started
            done = await self.arranger.play(move, ws, self.bpm(), self.stream.rng)
            history = (history + [f"section {k + 1}: {done}"])[-16:]
            shown = " ".join(f"{m} {p:.2f}" for m, p in odds.items()) if odds else (
                "model unavailable, dice" if self.args.arrange == "model" else "dice")
            print(f"arrange {k + 1}/{arc.count}: {done}  ({shown}; {took:.1f}s)", flush=True)

    async def _read(self, ws) -> None:
        try:
            async for text in ws:
                data = json.loads(text)
                if "controls" in data:
                    chord = playing_chord(data)
                    if chord is not None and chord != playing_chord(self.state):
                        self.chord_changed.set()
                    self.state = data
        finally:
            self.closed = True

    async def _loop(self, ws) -> None:
        loop = asyncio.get_running_loop()
        # Schedule by step number from a fixed start time, so rounding errors
        # don't add up over a long run. `step` counts 16ths from that start,
        # which a new chord moves (I Ching); `total` counts them from Play,
        # for the lanes on a fixed grid (Lane.every). `cycle` counts advances.
        start, step, total, cycle = loop.time(), 0, 0, -1
        sixteenth = 60.0 / self.bpm() / 4
        was_playing = False
        while self.args.cycles is None or cycle < self.args.cycles:
            if self.closed:
                raise ConnectionError("m00Dr closed the connection")
            playing = bool(self.state.get("playing"))
            if self.args.wait_for_play and not playing:
                was_playing = False
                await asyncio.sleep(0.05)
                continue
            if playing and not was_playing:
                start, step, total = loop.time(), 0, 0  # restart the cycle on Play
            was_playing = playing

            self.follow_cast()
            reading = self.section_int()
            if reading is not None:
                # A cycle is one chord (a bar), from when m00Dr moves to it.
                steps_per_cycle = 16
                if self.chord_changed.is_set():
                    self.chord_changed.clear()
                    start, step = loop.time(), 0
                    section, value = reading
                    self.stream.advance(value)
                    cycle += 1
                    print(f"c{cycle:<3} section {section + 1}/{SECTIONS}: int {value}", flush=True)
                    await self._send_breaks()
                position = step
            else:
                steps_per_cycle = 16 * self.bars  # the bars knob can change it mid-run
                position = step % steps_per_cycle
                if position == 0:
                    self.stream.advance()
                    cycle += 1
            if self.args.cycles is not None and cycle >= self.args.cycles:
                break
            for lane in self.lanes:
                due = (total % lane.every == 0 if lane.every
                       else int(lane.phase * steps_per_cycle) == position)
                if due:
                    await self._fire(ws, lane, cycle, position / steps_per_cycle)

            step += 1
            total += 1
            # On a tempo change, move the start so this step keeps the time the
            # old tempo gave it. Otherwise the new tempo re-times every step
            # since Play: faster fires the overdue steps in a burst, slower
            # stalls until the schedule catches up.
            new_sixteenth = 60.0 / self.bpm() / 4
            if new_sixteenth != sixteenth:
                start += step * (sixteenth - new_sixteenth)
                sixteenth = new_sixteenth
            delay = max(0.0, start + step * sixteenth - loop.time())
            if reading is None:
                await asyncio.sleep(delay)
            else:
                try:   # wake early for the next chord
                    await asyncio.wait_for(self.chord_changed.wait(), delay)
                except TimeoutError:
                    pass

    async def _fire(self, ws, lane: Lane, cycle: int, position: float) -> None:
        value = self.stream.read(lane.lag)
        if value is None:
            return  # this lane's lag hasn't filled yet
        tag = f"c{cycle:<3} φ{position:4.2f} {lane.control:<14} int={value:<3}"
        if lane.control == "roll":
            wait = self.next_roll - time.monotonic()
            if wait > 0:
                print(f"{tag} - (next roll in {wait:.0f}s)")
                return
            gap = ROLL_GAP[0] + (ROLL_GAP[1] - ROLL_GAP[0]) * value / INT_MAX
            self.next_roll = time.monotonic() + gap
            tag += f" (then {gap:.0f}s)"
        message = to_message(lane, value, self.state.get("controls", {}).get(lane.control))
        if message is None:
            print(f"{tag} -")
            return
        if "value" in message:
            if self.sent.get(lane.control) == message["value"]:
                return
            self.sent[lane.control] = message["value"]
        if "cc" in message:
            channel, cc = message["cc"]
            print(f"{tag} -> ch{channel} CC{cc}={value}")
            if not self.args.dry_run and self.sound_out is not None:
                await self._send_cc(channel, cc, value)
            return
        print(f"{tag} -> {message.get('value', 'click')}")
        if not self.args.dry_run:
            await ws.send(json.dumps(message))


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--seed", type=int, default=None, help="repeatable rolls")
    p.add_argument("--bars", type=int, default=4, help="bars per cycle")
    p.add_argument("--shift", type=float, default=0.0,
                   help="phase rotation of all lanes, 0..1 of a cycle")
    p.add_argument("--lanes", type=lambda s: s.split(","), default=DEFAULT_LANES,
                   help="comma-separated control ids, in lag order")
    p.add_argument("--cycles", type=int, default=None, help="stop after N cycles")
    p.add_argument("--wait-for-play", action="store_true",
                   help="only roll while m00Dr's transport is playing")
    p.add_argument("--no-iching", dest="iching", action="store_false",
                   help="roll from the dice only, without the I Ching arcs")
    p.add_argument("--weights", choices=("dice", "model"), default="dice",
                   help="how an I Ching arc moves between sections: the coins, or a local model's odds")
    p.add_argument("--model", default="spark-4b", help="llama-swap model for --weights model")
    p.add_argument("--api", default="http://127.0.0.1:8080", help="llama-swap's address")
    p.add_argument("--temperature", type=float, default=2.0,
                   help="flattens the model's odds before they're rolled (1 keeps them)")
    p.add_argument("--arrange", choices=("off", "dice", "model"), default="off",
                   help="an arrangement move per section (faders, arp rate, drums), chosen by dice or the model")
    p.add_argument("--dry-run", action="store_true", help="print, don't send")
    p.add_argument("--no-midi", action="store_true",
                   help="no ALSA MIDI (knobs in, sound CCs out); it is Linux only")
    return p.parse_args(argv)


def main(argv=None) -> int:
    # systemctl stop sends SIGTERM: handle it like Ctrl-C so the mix is restored.
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        asyncio.run(Roller(parse_args(argv)).run())
    except (ConnectionRefusedError, OSError, websockets.ConnectionClosed) as e:
        print(f"can't reach m00Dr's web remote ({e}). Tick 'Web remote' in m00Dr.")
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
