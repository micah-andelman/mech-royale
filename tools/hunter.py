"""Practice-squad hunter for private rooms (a test opponent, never submitted).
Fifteen of these share sightings through files in a blackboard folder, never shoot each other, and hunt the
one mech that is not in the squad: one hunter at the start, one more per squad death, all of them late.
usage: python hunter.py <repo root> <blackboard dir> <aim noise rad> <tier rank 0-3>"""
import json
import math
import os
import random
import sys

ROOT, BB, ERR, RANK = sys.argv[1], sys.argv[2], float(sys.argv[3]), int(sys.argv[4])
sys.path.insert(0, ROOT)
from src.world import World, Path  # noqa: E402  (our own nav grid and A*)

CAP = {0: 80.0, 1: 120.0, 2: 170.0, 3: 230.0}[RANK]      # aim falls apart past the tier's range
LEADS = RANK >= 2                                         # only hard/expert lead their shots
LATE_FRAC = float(os.environ.get("SQUAD_LATE", "0.72"))   # everyone hunts after this share of the match
NAMES = ["easy", "medium", "hard", "expert"]
os.makedirs(os.path.join(BB, "ids"), exist_ok=True)
w = World()
out = sys.stdout


def send(m):
    out.write(json.dumps(m, separators=(",", ":")) + "\n")
    out.flush()


def put(name, obj):
    p = os.path.join(BB, name)
    tmp = p + ".%d.tmp" % os.getpid()
    try:
        with open(tmp, "w") as f:
            json.dump(obj, f)
        os.replace(tmp, p)
    except OSError:
        pass


def get(name):
    try:
        with open(os.path.join(BB, name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


me_id = None
squad = {}
dur = 240.0
gun_speed = 252.0
path = None
path_goal = None
roam = None
stuck = 0.0
provoked_t = -99.0
ids_read = -9.0
rng = random.Random()


def nav(pos, goal, tick):
    global path, path_goal
    if math.dist(pos[:2], goal) < 4:
        return [0.0, 0.0]
    if w.line_free(pos, goal):
        d = math.dist(pos[:2], goal)
        return [(goal[0] - pos[0]) / d, (goal[1] - pos[1]) / d]
    if path is None or path_goal is None or math.dist(path_goal, goal) > 25 or tick % 200 == (me_id or 0) * 2:
        path = Path(w, pos, goal)
        n = 0
        while not path.step(20000) and n < 40:
            n += 1
        path_goal = goal
    pts = path.points or [goal]
    tgt = pts[-1]
    for p in pts[:14]:
        if w.line_free(pos, p):
            tgt = p
    d = math.dist(pos[:2], tgt) or 1.0
    return [(tgt[0] - pos[0]) / d, (tgt[1] - pos[1]) / d]


for line in sys.stdin:
    try:
        m = json.loads(line)
    except ValueError:
        continue
    ty = m.get("type")
    if ty == "hello":
        me_id = m["slot"]
        dur = m["config"]["match"].get("duration_s", 240)
        gun_speed = m["config"]["weapons"]["gun"]["speed"]
        put("ids/%d.json" % me_id, {"rank": RANK})
        send({"type": "ready", "name": NAMES[RANK]})
        continue
    if ty in ("end", "results"):
        put("ids/%d.json" % me_id, {"rank": RANK, "dead": True})
        break
    if ty != "obs":
        continue
    t = m["t"]
    s = m["self"]
    pos = s["pos"]
    z = m["zone"]
    if t - ids_read > 1.0:
        ids_read = t
        try:
            squad = {int(f.split(".")[0]): (get("ids/" + f) or {})
                     for f in os.listdir(os.path.join(BB, "ids")) if f.endswith(".json")}
        except OSError:
            pass
    for e in m.get("events", []):
        if e.get("type") == "damaged" and e.get("by") is not None and e["by"] not in squad:
            provoked_t = t
    living = sorted((v.get("rank", 0), k) for k, v in squad.items() if not v.get("dead"))
    n_dead = sum(1 for v in squad.values() if v.get("dead"))
    active = {k for _, k in living[:1 + n_dead]}
    hunting = me_id in active or t > dur * LATE_FRAC
    tgts = [x for x in m.get("mechs", []) if x.get("kind") == "mech" and x["id"] not in squad]
    o = None
    if tgts:
        o = min(tgts, key=lambda x: math.dist(x["pos"], pos))
        put("seen.json", {"t": t, "pos": o["pos"][:2]})
    act = {"tick": m["tick"], "fire": [False, False], "boost": False}
    goal = z.get("next_center") or z["center"]
    if o is not None and (hunting or t - provoked_t < 10):
        d = math.dist(o["pos"][:2], pos[:2])
        lead = d / gun_speed if LEADS else 0.0
        err = ERR if d <= CAP else 0.12
        v = o.get("vel", [0, 0, 0])
        tx = o["pos"][0] + v[0] * lead
        ty2 = o["pos"][1] + v[1] * lead
        act["aim"] = [math.atan2(ty2 - pos[1], tx - pos[0]) + rng.gauss(0, err),
                      math.atan2(o["pos"][2] + 4.5 - (pos[2] + 7), max(d, 1.0))]
        if d > 50 and hunting:
            goal = o["pos"][:2]
        g = s["weapons"][0]
        if g and g.get("ammo", 1) == 0:
            act["reload"] = True
        act["fire"] = [d < 200, False]
    else:
        if hunting:
            seen = get("seen.json")
            if seen and t - seen["t"] < 30:
                goal = seen["pos"]
            else:
                if roam is None or math.dist(pos[:2], roam) < 10:
                    a = rng.uniform(0, 2 * math.pi)
                    rr = rng.uniform(0, z.get("next_radius", z["radius"]) * 0.8)
                    c = z.get("next_center") or z["center"]
                    roam = [c[0] + math.cos(a) * rr, c[1] + math.sin(a) * rr]
                goal = roam
        act["aim"] = [math.atan2(goal[1] - pos[1], goal[0] - pos[0]), 0.0]
    zc, zr = z["center"], z["radius"]
    gd = math.dist(goal, zc)
    if gd > zr * 0.8:
        f = zr * 0.8 / max(gd, 1e-6)
        goal = [zc[0] + (goal[0] - zc[0]) * f, zc[1] + (goal[1] - zc[1]) * f]
    try:
        mv = nav(pos, goal, m["tick"])
    except Exception:
        d = math.dist(pos[:2], goal) or 1.0
        mv = [(goal[0] - pos[0]) / d, (goal[1] - pos[1]) / d]
    act["move"] = mv
    sp = math.hypot(s["vel"][0], s["vel"][1])
    stuck = stuck + 0.1 if (sp < 1.0 and math.hypot(mv[0], mv[1]) > 0.5) else 0.0
    if stuck > 0.8:
        act["jump"] = True
        act["boost"] = True
    if math.dist(pos[:2], zc) > zr - 5:
        act["boost"] = True
    send(act)
