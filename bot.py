"""Team "5-hour limit reached" Mech Royale bot. Logic lives in src/brain.py.

SafeBrain: if the brain fails to start or raises on a tick, we still answer with a sensible action
(face and shoot the nearest visible mech, walk toward the next zone center) instead of the kit's
fallback, which repeats the last move without firing.
"""
import math
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mechfight as mf  # noqa: E402

try:
    from src.brain import Brain  # noqa: E402
except Exception:  # pragma: no cover
    traceback.print_exc(file=sys.stderr)
    Brain = None


def fallback(obs):
    me = obs.get("self", {})
    pos = me.get("pos", [0, 0, 0])
    act = {"tick": obs.get("tick"), "boost": False, "fire": [False, False]}
    z = obs.get("zone") or {}
    c = z.get("next_center") or z.get("center") or [0, 0]
    dx, dy = c[0] - pos[0], c[1] - pos[1]
    n = math.hypot(dx, dy)
    act["move"] = [dx / n, dy / n] if n > 5 else [0.0, 0.0]
    mechs = [m for m in obs.get("mechs", []) if m.get("kind") == "mech" and m.get("pos")]
    if mechs:
        m = min(mechs, key=lambda m: math.hypot(m["pos"][0] - pos[0], m["pos"][1] - pos[1]))
        d = math.hypot(m["pos"][0] - pos[0], m["pos"][1] - pos[1])
        yaw = math.atan2(m["pos"][1] - pos[1], m["pos"][0] - pos[0])
        pitch = math.atan2(m["pos"][2] + 4.5 - (pos[2] + 7), max(d, 1.0))
        act["aim"] = [yaw, pitch]
        act["fire"] = [d < 220, False]
    return act


class SafeBrain:
    name = "5hr-limit"

    def __init__(self, hello):
        self.b = None
        self.errors = 0
        if Brain is not None:
            try:
                self.b = Brain(hello)
            except Exception:
                traceback.print_exc(file=sys.stderr)

    def act(self, obs):
        if self.b is not None:
            try:
                return self.b.act(obs)
            except Exception:
                self.errors += 1
                if self.errors <= 5 or self.errors % 100 == 0:
                    print(f"brain error {self.errors} at tick {obs.get('tick')}", file=sys.stderr)
                    traceback.print_exc(file=sys.stderr)
        return fallback(obs)


mf.run(SafeBrain)
