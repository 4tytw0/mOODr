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
import time
from collections import deque
from dataclasses import dataclass

import websockets

# Draws are 7-bit, 0..INT_MAX: the range of a MIDI CC, so a sound lane sends
# its int as is. m00Dr's controls fold it into their own range with modulo.
INT_MAX = 127

# Seconds between rolls: ROLL_GAP[0] at int 0 up to ROLL_GAP[1] at INT_MAX.
ROLL_GAP = (120.0, 240.0)

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


# The default lanes. Key isn't one of them because Roll already moves the key
# around the circle of fifths. BPM is left alone because a DAW usually owns it,
# and the arp rate and octave because they're played by hand.
DEFAULT_LANES = ["roll", "mode", "arpPattern", "acidNoise",
                 "acidWide", "randomize", "stabs", *SOUND_LANES]


class PhaseShiftedInts:
    """A seeded random-int stream that lanes read at a lag."""

    def __init__(self, seed: int | None, depth: int):
        self._rng = random.Random(seed)
        self._history = deque(maxlen=depth + 1)

    def reseed(self, seed: int) -> None:
        # Keeps the history, so values already drawn carry on down the lanes.
        self._rng.seed(seed)

    def advance(self) -> None:
        self._history.appendleft(self._rng.randint(0, INT_MAX))

    def read(self, lag: int) -> int | None:
        return self._history[lag] if lag < len(self._history) else None


def make_lanes(names: list[str], shift: float) -> list[Lane]:
    # Spread the phases evenly and add the shift, so lane k fires at
    # (k/n + shift) mod 1. Its lag counts up within its chain (m00Dr or
    # sound), so each lane gets the value the lane before it had one cycle
    # earlier.
    n = len(names)
    lags = {False: 0, True: 0}
    lanes = []
    for k, name in enumerate(names):
        sound = name in SOUND_LANES
        lanes.append(Lane(name, lag=lags[sound], phase=(k / n + shift) % 1.0))
        lags[sound] += 1
    return lanes


def to_message(lane: Lane, value: int, control: dict | None) -> dict | None:
    """Fold a random int into whatever this control accepts."""
    if lane.control in SOUND_LANES:
        return {"cc": SOUND_LANES[lane.control], "value": value}
    if lane.control == "roll":
        return {"op": "click", "id": "roll"}  # Roller._fire holds it to ROLL_GAP
    if lane.control == "randomize":
        # Fires on odd ints only, so about every other cycle.
        return {"op": "click", "id": lane.control} if value & 1 else None
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

    async def run(self) -> None:
        uri = f"ws://{self.args.host}:{self.args.port}"
        async with websockets.connect(uri) as ws:
            print(f"connected to m00Dr at {uri}", flush=True)
            self.state = json.loads(await ws.recv())
            tasks = [asyncio.create_task(self._read(ws))]
            if not self.args.no_midi:
                self._open_midi()
                tasks.append(asyncio.create_task(self._listen_knobs()))
            try:
                await self._loop(ws)
            finally:
                for task in tasks:
                    task.cancel()

    async def _read(self, ws) -> None:
        try:
            async for text in ws:
                data = json.loads(text)
                if "controls" in data:
                    self.state = data
        finally:
            self.closed = True

    async def _loop(self, ws) -> None:
        loop = asyncio.get_running_loop()
        # Schedule by step number from a fixed start time, so rounding errors
        # don't add up over a long run.
        start, step, cycle = loop.time(), 0, 0
        was_playing = False
        while self.args.cycles is None or cycle < self.args.cycles:
            steps_per_cycle = 16 * self.bars  # the bars knob can change it mid-run
            if self.closed:
                raise ConnectionError("m00Dr closed the connection")
            playing = bool(self.state.get("playing"))
            if self.args.wait_for_play and not playing:
                was_playing = False
                await asyncio.sleep(0.05)
                continue
            if playing and not was_playing:
                start, step = loop.time(), 0  # restart the cycle on Play
            was_playing = playing

            if step % steps_per_cycle == 0:
                self.stream.advance()
                cycle = step // steps_per_cycle
                if self.args.cycles is not None and cycle >= self.args.cycles:
                    break
            position = (step % steps_per_cycle) / steps_per_cycle
            for lane in self.lanes:
                if int(lane.phase * steps_per_cycle) == step % steps_per_cycle:
                    await self._fire(ws, lane, cycle, position)

            step += 1
            sixteenth = 60.0 / self.bpm() / 4
            await asyncio.sleep(max(0.0, start + step * sixteenth - loop.time()))

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
    p.add_argument("--dry-run", action="store_true", help="print, don't send")
    p.add_argument("--no-midi", action="store_true",
                   help="no ALSA MIDI (knobs in, sound CCs out); it is Linux only")
    return p.parse_args(argv)


def main(argv=None) -> int:
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
