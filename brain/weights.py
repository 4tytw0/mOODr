"""Model-weighted moves for the roller's I Ching arcs (--weights model).

With the dice, each of a section's six lines turns on a 1-in-4 toss. With the
model, a local LLM (llama-swap's OpenAI API) is asked how the arc should move
into the next section, as one of MOVES. It answers with one word, and its
probabilities for each move are rolled, flattened by `temperature`, so the
choice stays a roll, only with weights that come from the music so far:

    hold    no line turns
    nudge   one of the two bottom lines turns (a small step in the int)
    shift   one of the two middle lines turns
    leap    one of the two top lines turns (a big step)

The prompt is a stable prefix (the instructions, then the arc so far, one line
per section) with the question at the end, so the server's prompt cache only
reads what's new: each decision costs well under a second after the first.

Anything that goes wrong (server down, slow, no move among the answers)
returns None, and the roller rolls that section with the dice instead.
"""

import json
import math
import random
import urllib.request

MOVES = ("hold", "nudge", "shift", "leap")
# Which lines (bottom first) each move may turn.
MOVE_LINES = {"hold": (), "nudge": (0, 1), "shift": (2, 3), "leap": (4, 5)}

INSTRUCTIONS = """\
You steer a generative techno set. Every 2 to 4 minutes a Roll picks a new key, \
scale and progression, and opens an arc of 8 sections. Each section is a \
hexagram whose number (0-127) sets the instruments' parameters: the scale mode, \
the acid line's noise and the acid synth's filter, resonance, decay, accent, \
glide and drive. Between sections the hexagram can hold, or turn one line:
- hold: nothing changes, the groove settles in
- nudge: a bottom line turns, a small change in the number
- shift: a middle line turns, a medium change
- leap: a top line turns, a big change
Shape the arc like a DJ: let grooves breathe, build tension, release it, and \
land somewhere new by the end of the arc. Answer with exactly one word: \
hold, nudge, shift or leap."""


def choice_odds(api: str, model: str, options: tuple[str, ...], system: str, user: str,
                timeout: float = 20.0) -> dict[str, float] | None:
    """A model's odds for each of `options` as a one-word answer, read from the
    first token's top log-probabilities, or None. Blocking: call it off the event loop.

    An option may come back as the whole word or as its first token ("le" for
    leap); each token is credited to the one option it can be the start of."""
    body = {
        "model": model, "max_tokens": 1, "temperature": 0,
        "logprobs": True, "top_logprobs": 20,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    request = urllib.request.Request(f"{api.rstrip('/')}/v1/chat/completions", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            top = json.load(response)["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return None
    probs = dict.fromkeys(options, 0.0)
    for entry in top:
        token = entry["token"].strip().lower()
        matches = [option for option in options if token and option.startswith(token)]
        if len(matches) == 1:
            probs[matches[0]] += math.exp(entry["logprob"])
    return probs if sum(probs.values()) > 0 else None


def flatten(probs: dict[str, float], temperature: float) -> dict[str, float]:
    """Raises each probability to 1/temperature and renormalises: above 1
    evens them out (a model's own odds run overconfident), 1 keeps them."""
    weights = {move: p ** (1.0 / temperature) for move, p in probs.items() if p > 0}
    total = sum(weights.values())
    return {move: w / total for move, w in weights.items()}


def with_floor(probs: dict[str, float], floor: float) -> dict[str, float]:
    """Mixes the odds with an even spread: each of the n options gets at least
    floor/n, so a model sure of one answer still leaves room for the others
    (0 keeps the odds, 1 is plain dice). Flattening alone can't do that: it
    turns 1.00 vs 0.001 into about 0.97 vs 0.03."""
    total = sum(probs.values())
    return {k: (1 - floor) * p / total + floor / len(probs) for k, p in probs.items()}


def turn(lines: list[bool], move: str, rng: random.Random) -> list[bool]:
    """The hexagram after `move`: one line of the move's pair turned."""
    lines = list(lines)
    if MOVE_LINES[move]:
        i = rng.choice(MOVE_LINES[move])
        lines[i] = not lines[i]
    return lines


class ModelMover:
    def __init__(self, api: str, model: str, temperature: float, floor: float = 0.0, timeout: float = 20.0):
        self.api, self.model, self.temperature, self.timeout = api.rstrip("/"), model, temperature, timeout
        self.floor = floor

    def probabilities(self, arc_lines: list[str], now: str) -> dict[str, float] | None:
        """The model's odds for each move, from the arc so far and the state
        now, or None. Blocking: call it off the event loop."""
        user = ("The arc so far:\n" + "\n".join(arc_lines)
                + f"\n\nNow: {now}\nHow does the arc move into the next section?")
        return choice_odds(self.api, self.model, MOVES, INSTRUCTIONS, user, self.timeout)

    def roll(self, probs: dict[str, float], rng: random.Random) -> str:
        weights = with_floor(flatten(dict.fromkeys(MOVES, 0.0) | probs, self.temperature), self.floor)
        return rng.choices(list(weights), weights=list(weights.values()))[0]
