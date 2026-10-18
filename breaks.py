# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "soundfile", "alsa-midi"]
# ///
"""Drum breaks on fader 8: a break sliced into 16ths, played on m00Dr's clock
and re-chopped as the progression goes on.

The breaks are synths/breaks/NN.wav, listed in synths/breaks.json (built by
tools/build_breaks.py: 64 of the GAMEBREAKS breaks, one per hexagram, each a
loop of 1, 2 or 4 bars). Listens on ALSA sequencer client "m00Dr-Breaks"
and plays through pw-cat into the m800Dr Mixer's breaks strip, like
sunvox303.py.

Timing: m00Dr's MIDI Beat Clock (on its note port). Start begins the break's
first bar, every 6th pulse is a 16th, Stop silences it. Each 16th plays one
slice of the current bar of the break, eased towards m00Dr's tempo by
playing it faster or slower (within RATE_LIMITS; beyond that the slice is
cut short or leaves a gap, as a sampler's chopped break would).

What plays comes from m00Drc's roller, on MIDI ch8 (BREAKS_CHANNEL):
  * a program change 0..63 picks the break: the roller sends each landed
    Roll's hexagram, so every Roll brings a new break, from the next bar line.
  * CC CHOP_LINES_CC / CHOP_CHANGING_CC carry six I Ching lines (yang bits,
    changing bits, bottom line first) that re-chop the bar; the roller casts
    a fresh six on every chord change. A static line leaves its pair of
    16ths alone. A changing line edits the pair at CHOP_STEPS[line]: changing
    yin stutters it (the pair plays its first slice twice); changing yang in
    the lower trigram swaps it with the pair half a bar on, and in the upper
    trigram repeats the pair a beat earlier, an echo. (Swaps for both
    trigrams would pair up and undo each other.)

    uv run breaks.py                    # run (the m00dr-breaks user service does this)
    uv run breaks.py --demo x.wav [N]   # render 4 bars of break N, re-chopped, for listening
"""
import fcntl, json, random, subprocess, sys, threading
from pathlib import Path
import numpy as np, soundfile as sf

SR, BLOCK = 48000, 256
BREAKS_CHANNEL = 7                        # 0-based: MIDI ch8
HERE = Path(__file__).resolve().parent
INDEX = HERE / "synths" / "breaks.json"
CHOP_LINES_CC, CHOP_CHANGING_CC = 20, 21
CHOP_STEPS = (2, 4, 6, 10, 12, 14)        # the 16th each line's pair starts on, bottom line first
CLOCKS_PER_16TH = 6                       # MIDI Beat Clock: 24 per beat
RATE_LIMITS = (0.75, 1.33)                # how far a slice is sped up/slowed to fit the 16th
FADE = 96                                 # samples (2 ms) faded at each slice's ends: no clicks


def chop(lines: int, changing: int) -> list[int]:
    """The order the bar's 16 slices play in, for six lines (bit i = line i)."""
    order = list(range(16))
    for line, t in enumerate(CHOP_STEPS):
        if not changing >> line & 1:
            continue
        if lines >> line & 1 and line < 3:   # changing yang, lower: swap half a bar on
            u = t + 8
            order[t:t + 2], order[u:u + 2] = order[u:u + 2], order[t:t + 2]
        elif lines >> line & 1:              # changing yang, upper: echo the beat before
            order[t:t + 2] = order[t - 4:t - 2]
        else:                                # changing yin: stutter
            order[t + 1] = order[t]
    return order


class Breaks:
    def __init__(self):
        index = json.loads(INDEX.read_text())["breaks"]
        self.breaks = []
        for entry in index:
            audio, rate = sf.read(INDEX.parent / "breaks" / entry["file"], dtype="float32", always_2d=True)
            assert rate == SR, f"{entry['file']} is {rate} Hz"
            self.breaks.append((audio, entry["bars"], entry["name"]))
        self.lock = threading.Lock()
        self.current = self.pending = 0
        self.order = list(range(16))
        self.lines = self.changing = 0
        self.count = None                 # clock pulses since Start; None while stopped
        self.pulse_times: list[float] = []
        self.sixteenth = SR * 0.125       # output samples per 16th, from the clock (120 bpm until known)
        self.voices = []                  # [audio, position, rate, end, fade-out countdown or None]
        self.now = 0                      # output samples rendered
        self.buf = np.zeros((BLOCK, 2), dtype="<f4")

    # MIDI thread ------------------------------------------------------------
    def start(self):
        with self.lock:
            self.count = 0
            self.pulse_times.clear()

    def stop(self):
        with self.lock:
            self.count = None
            for voice in self.voices:
                voice[4] = voice[4] if voice[4] is not None else FADE

    def clock(self):
        with self.lock:
            if self.count is None:
                return
            self.pulse_times = (self.pulse_times + [self.now])[-25:]
            if len(self.pulse_times) == 25 and self.pulse_times[-1] > self.pulse_times[0]:
                self.sixteenth = (self.pulse_times[-1] - self.pulse_times[0]) / 4
            if self.count % CLOCKS_PER_16TH == 0:
                self._trigger(self.count // CLOCKS_PER_16TH)
            self.count += 1

    def program(self, number):
        with self.lock:
            self.pending = number % len(self.breaks)

    def control(self, cc, value):
        with self.lock:
            if cc == CHOP_LINES_CC: self.lines = value & 63
            elif cc == CHOP_CHANGING_CC: self.changing = value & 63
            else: return
            self.order = chop(self.lines, self.changing)

    # Called under the lock --------------------------------------------------
    def _trigger(self, step):
        if step % 16 == 0 and self.pending != self.current:
            self.current = self.pending    # a new break waits for the bar line
            print(f"break {self.current}: {self.breaks[self.current][2]}", flush=True)
        audio, bars, _ = self.breaks[self.current]
        length = len(audio) / (bars * 16)  # source samples per slice
        bar = step // 16 % bars
        start = (bar * 16 + self.order[step % 16]) * length
        rate = min(max(length / self.sixteenth, RATE_LIMITS[0]), RATE_LIMITS[1])
        for voice in self.voices:          # the last slice fades out under this one
            voice[4] = voice[4] if voice[4] is not None else FADE
        self.voices.append([audio, start, rate, start + length, None])

    def render(self):
        with self.lock:
            out = np.zeros((BLOCK, 2), dtype=np.float32)
            keep = []
            for voice in self.voices:
                audio, pos, rate, end, fading = voice
                n = BLOCK if fading is None else min(BLOCK, fading)
                idx = pos + np.arange(n) * rate
                n = int(np.searchsorted(idx, end))            # stop at the slice's end
                if n > 0:
                    i0 = idx[:n].astype(int)
                    frac = (idx[:n] - i0)[:, None]
                    i1 = np.minimum(i0 + 1, len(audio) - 1)
                    chunk = audio[i0] * (1 - frac) + audio[i1] * frac
                    left = (end - idx[:n]) / rate                # output samples to the slice's end
                    gain = np.clip(left / FADE, 0, 1)
                    if fading is not None:
                        gain = np.minimum(gain, (fading - np.arange(n)) / FADE)
                    out[:n] += chunk * gain[:, None]
                voice[1] = pos + n * rate
                if fading is not None:
                    voice[4] = fading - n
                if n == BLOCK and (fading is None or voice[4] > 0):
                    keep.append(voice)
            self.voices = keep
            self.now += BLOCK
            self.buf[:] = out
        return self.buf


def open_midi():
    from alsa_midi import SequencerClient, WRITE_PORT, PortType
    client = SequencerClient("m00Dr-Breaks")
    # Keep a reference: alsa-midi deletes the port when its object is garbage-collected.
    client.breaks_in = client.create_port("breaks in", caps=WRITE_PORT, type=PortType.MIDI_GENERIC | PortType.APPLICATION)
    print(f"MIDI in: ALSA client {client.client_id} 'm00Dr-Breaks', port 0 'breaks in'", flush=True)
    return client


def midi_loop(client, breaks):
    from alsa_midi import (ClockEvent, ContinueEvent, ControlChangeEvent, ProgramChangeEvent,
                           StartEvent, StopEvent)
    while True:
        ev = client.event_input()
        if isinstance(ev, ClockEvent): breaks.clock(); continue
        if isinstance(ev, StartEvent): breaks.start(); continue
        if isinstance(ev, ContinueEvent): breaks.start(); continue
        if isinstance(ev, StopEvent): breaks.stop(); continue
        if getattr(ev, "channel", None) != BREAKS_CHANNEL: continue
        if isinstance(ev, ProgramChangeEvent): breaks.program(ev.value)
        elif isinstance(ev, ControlChangeEvent): breaks.control(ev.param, ev.value)


def demo(path, number=0):
    """Four bars at 120 bpm: straight, then three random chops."""
    b = Breaks(); b.program(number); b.start()
    rng, blocks, pulse = random.Random(3), [], SR * 0.5 / 24
    next_pulse = 0.0
    for bar in range(4):
        if bar:
            b.control(CHOP_LINES_CC, rng.getrandbits(6)); b.control(CHOP_CHANGING_CC, rng.getrandbits(6))
            print(f"bar {bar + 1} order {b.order}")
        for _ in range(int(2 * SR / BLOCK)):      # a bar at 120 bpm is 2 s
            while next_pulse < b.now + BLOCK:
                b.clock(); next_pulse += pulse
            blocks.append(b.render().copy())
    y = np.concatenate(blocks)
    sf.write(path, y, SR, subtype="PCM_16")
    print(f"peak {np.abs(y).max():.2f}")


def main():
    breaks = Breaks()
    threading.Thread(target=midi_loop, args=(open_midi(), breaks), daemon=True).start()
    # --target 0: left unlinked; synths/links.sh wires it into the mixer's breaks strip.
    p = subprocess.Popen(["pw-cat", "--playback", "--raw", "--format=f32", f"--rate={SR}", "--channels=2",
                          "--target=0", f"--latency={BLOCK}",
                          "-P", "{ node.name=m00Dr-Breaks media.name=\"breaks\" }", "-"], stdin=subprocess.PIPE)
    fcntl.fcntl(p.stdin.fileno(), fcntl.F_SETPIPE_SZ, 4096)   # keep the pipe from adding ~170 ms of latency
    while True:
        p.stdin.write(breaks.render().tobytes())              # blocks at real-time pace


if __name__ == "__main__":
    if sys.argv[1:2] == ["--demo"]:
        demo(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 0)
    else:
        main()
