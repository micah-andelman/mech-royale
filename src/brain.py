"""Ghost / vulture / tank brain.

Plan in one paragraph: stay silent and out of sight, sit in covered spots on the quiet side of the next
circle, avoid supply drops and fights, pick up cubes, drones and weapons only when nobody is around,
stack max HP for the last circle, shoot back when attacked, and finish mechs that someone else has
already hurt. See docs/STRATEGY.md for the reasoning.
"""
import math
import random

from .world import Path, World

TAU = 2 * math.pi

UPGRADE_ORDER = [
    "plating", "armor", "nanites", "sensors", "capacitor", "reactor", "jets", "gyros", "servos",
    "lightning_efficiency", "laser_charge", "laser_damage", "gun_rate", "gun_damage", "gun_velocity",
    "gun_mag", "lightning_range", "lightning_chain", "laser_pierce",
    "mortar_reload", "mortar_velocity", "mortar_splash",
]
WEAPON_PREF = {"laser": 3, "lightning": 2, "mortar": 0, "gun": 1}
LOUD = {"gun": 1.0, "laser": 1.0, "lightning": 1.0, "mortar": 1.0, "explosion": 0.8, "death": 0.6,
        "laser_charge": 1.0, "step": 1.2, "boost": 1.0, "jet": 1.0, "reload": 1.0, "crate": 0.5, "npc_gun": 0.3}
import os as _os
STRAFE_MIN = float(_os.environ.get("STRAFE_MIN", "0.6"))
STRAFE_MAX = float(_os.environ.get("STRAFE_MAX", "1.4"))
STRAFE_GOAL = float(_os.environ.get("STRAFE_GOAL", "0.5"))
STRAFE_BOOST_E = float(_os.environ.get("STRAFE_BOOST_E", "999"))
LONE = _os.environ.get("LONE", "0") == "1"
BAND = {"near": 35.0, "mid": 110.0, "far": 240.0}


def angdiff(a: float, b: float) -> float:
    return (a - b + math.pi) % TAU - math.pi


def dist2(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


class Brain:
    name = "5hr-limit"

    def __init__(self, hello):
        self.cfg = hello["config"]
        self.rng = random.Random(hello.get("seed", 0))
        self.w = World()
        self.spot = None          # (x, y) where we want to sit
        self.spot_t = -1e9
        self.spot_phase = None
        self.path = None
        self.path_goal = None
        self.wp = 0
        self.heat = []            # (x, y, t, weight): where we think other mechs are or were
        self.seen = {}            # mech id -> (t, pos, hp, max_hp, weapons)
        self.attackers = {}       # id -> last t they hurt us
        self.last_dmg = None      # (t, bearing)
        self.hist = []            # (t, x, y) recent positions for stuck detection
        self.unstick_until = -1
        self.unstick_dir = (0.0, 0.0)
        self.scan_yaw = None
        self.scan_t = 0
        self.strafe = 1
        self.strafe_t = 0
        self.strafe_len = 1.0
        self.laser_hold = False
        self.drops_seen = {}      # id -> pos
        self.mode = "start"

    # ------------------------------------------------------------------ zone
    def zone_at(self, z, t: float):
        if t <= z["shrink_start"]:
            return z["center"], z["radius"]
        if t >= z["shrink_end"]:
            return z["next_center"], z["next_radius"]
        f = (t - z["shrink_start"]) / max(1e-6, z["shrink_end"] - z["shrink_start"])
        c = (z["center"][0] + (z["next_center"][0] - z["center"][0]) * f,
             z["center"][1] + (z["next_center"][1] - z["center"][1]) * f)
        return c, z["radius"] + (z["next_radius"] - z["radius"]) * f

    def time_safe(self, z, t: float, p) -> float:
        """Seconds until the zone edge reaches point p (inf if p is inside the next circle)."""
        if dist2(p, z["next_center"]) <= z["next_radius"] - 3:
            return 1e9
        c, r = self.zone_at(z, t)
        if dist2(p, c) > r:
            return 0.0
        for k in range(1, 41):
            tt = t + k * max(1.0, (z["shrink_end"] - t) / 40)
            c, r = self.zone_at(z, tt)
            if dist2(p, c) > r - 3:
                return tt - t
        return 1e9

    # ------------------------------------------------------------------ memory
    def remember(self, obs) -> None:
        t = obs["t"]
        me = obs["self"]
        x, y = me["pos"][0], me["pos"][1]
        for m in obs["mechs"]:
            if m["kind"] == "mech":
                self.seen[m["id"]] = (t, m["pos"], m.get("hp", 1400), m.get("max_hp", 1400), m.get("weapons", []))
                self.heat.append((m["pos"][0], m["pos"][1], t, 1.5))
        for s in obs["sounds"]:
            w = LOUD.get(s["kind"], 0.5)
            d = BAND.get(s["dist"], 200.0)
            b = s["bearing"]
            self.heat.append((x + math.cos(b) * d, y + math.sin(b) * d, t - s.get("age", 0), w))
        for e in obs["events"]:
            if e["type"] == "damaged" and e.get("by") is not None:
                self.attackers[e["by"]] = t
                if e.get("bearing") is not None:
                    self.last_dmg = (t, e["bearing"])
        for d in obs.get("drops", []):
            self.drops_seen[d["id"]] = d["pos"]
        self.heat = [h for h in self.heat if t - h[2] < 40][-200:]
        self.hist.append((t, x, y))
        self.hist = [h for h in self.hist if t - h[0] < 2.0]

    def danger_at(self, p, t: float, heat=None) -> float:
        s = 0.0
        for hx, hy, ht, w in (self.heat if heat is None else heat):
            d = math.hypot(p[0] - hx, p[1] - hy)
            if d < 150:
                s += w * (1 - d / 150) * max(0.0, 1 - (t - ht) / 40)
        for dp in self.drops_seen.values():
            if math.hypot(p[0] - dp[0], p[1] - dp[1]) < 90:
                s += 6.0
        return s

    # ------------------------------------------------------------------ spots
    def choose_spot(self, obs) -> None:
        z = obs["zone"]
        t = obs["t"]
        me = obs["self"]["pos"]
        w = self.w
        nc, nr = z["next_center"], z["next_radius"]
        if nr < 30:
            # endgame: hug the final point but keep whatever cover is there
            target_r = max(2.0, nr * 0.5)
        else:
            target_r = nr * 0.72
        best, bestv = None, -1e18
        # merge heat points into 30 m buckets so scoring stays cheap
        buck = {}
        for hx, hy, ht, hw in self.heat:
            k = (int(hx // 30), int(hy // 30))
            f = max(0.0, 1 - (t - ht) / 40) * hw
            if f > 0:
                b = buck.get(k)
                buck[k] = (hx, hy, t, (b[3] if b else 0.0) + f)
        heat = list(buck.values())
        I0, J0 = w.nidx(nc[0] - target_r, nc[1] - target_r)
        I1, J1 = w.nidx(nc[0] + target_r, nc[1] + target_r)
        span = max(1, max(I1 - I0, J1 - J0))
        stride = max(1, span // 26)
        for J in range(max(0, J0), min(w.NY, J1 + 1), stride):
            for I in range(max(0, I0), min(w.NX, I1 + 1), stride):
                if not w.walk[J * w.NX + I]:
                    continue
                p = w.ncenter(I, J)
                dc = dist2(p, nc)
                if dc > target_r:
                    continue
                k = J * w.NX + I
                v = 0.0
                v += min(w.canopy4[k], 8) * 1.2           # hidden under trees
                v += min(w.wallnear[k], 4) * 0.8          # walls to break line of sight
                v -= self.danger_at(p, t, heat) * 3.0           # away from fights and drops
                v -= dist2(p, me) / 60.0                  # less walking = less noise
                if nr >= 30:
                    v -= abs(dc / max(nr, 1) - 0.5) * 3.0  # between center and edge
                v += self.rng.random() * 0.3
                if v > bestv:
                    best, bestv = p, v
        if best is None:
            best = tuple(nc)
        self.spot = best
        self.spot_t = t
        self.spot_phase = (round(nc[0]), round(nc[1]), round(nr))
        self.path = None

    # ------------------------------------------------------------------ navigation
    def nav_vector(self, pos, goal):
        """Unit-ish direction from pos toward goal along a path."""
        w = self.w
        if dist2(pos, goal) < 3.0:
            return (0.0, 0.0)
        if w.line_free(pos, goal):
            d = dist2(pos, goal)
            return ((goal[0] - pos[0]) / d, (goal[1] - pos[1]) / d)
        if self.path is None or self.path_goal != goal:
            self.path = Path(w, pos, goal)
            self.path_goal = goal
            self.wp = 0
        if not self.path.done:
            self.path.step(1500)
        if not self.path.done:
            d = dist2(pos, goal)
            return ((goal[0] - pos[0]) / d, (goal[1] - pos[1]) / d)
        pts = self.path.points
        # skip ahead to the farthest point we can walk to in a straight line
        best = self.wp
        for k in range(self.wp, min(len(pts), self.wp + 12)):
            if w.line_free(pos, pts[k]):
                best = k
        self.wp = best
        if self.wp < len(pts) and dist2(pos, pts[self.wp]) < 2.5 and self.wp < len(pts) - 1:
            self.wp += 1
        tgt = pts[min(self.wp, len(pts) - 1)]
        d = dist2(pos, tgt)
        if d < 1e-6:
            return (0.0, 0.0)
        return ((tgt[0] - pos[0]) / d, (tgt[1] - pos[1]) / d)

    def stuck(self, t: float) -> bool:
        if len(self.hist) < 8 or t - self.hist[0][0] < 1.2:
            return False
        _, x0, y0 = self.hist[0]
        _, x1, y1 = self.hist[-1]
        return math.hypot(x1 - x0, y1 - y0) < 1.0

    # ------------------------------------------------------------------ aiming
    def lead(self, me, tgt, speed: float):
        mz = me["pos"][2] + me["stats"].get("muzzle_height", 7)
        px, py, pz = tgt["pos"]
        vx, vy, vz = tgt.get("vel", [0, 0, 0])
        pz += 4.5 if tgt["kind"] == "mech" else 1.75
        tt = 0.0
        for _ in range(3):
            ax, ay, az = px + vx * tt, py + vy * tt, pz + vz * tt * 0.3
            d = math.sqrt((ax - me["pos"][0]) ** 2 + (ay - me["pos"][1]) ** 2 + (az - mz) ** 2)
            tt = d / speed
        yaw = math.atan2(ay - me["pos"][1], ax - me["pos"][0])
        pitch = math.atan2(az - mz, math.hypot(ax - me["pos"][0], ay - me["pos"][1]))
        return yaw, pitch, d

    # ------------------------------------------------------------------ main
    def act(self, obs):
        t = obs["t"]
        me = obs["self"]
        pos = me["pos"]
        z = obs["zone"]
        st = me["stats"]
        act = {"tick": obs["tick"]}
        self.remember(obs)

        # upgrades
        if obs.get("offer"):
            ch = obs["offer"]["choices"]
            rank = {u: i for i, u in enumerate(UPGRADE_ORDER)}
            act["pick"] = min(range(len(ch)), key=lambda i: rank.get(ch[i]["id"], 99))

        hpf = me["hp"] / max(1.0, me["max_hp"])
        mechs = [m for m in obs["mechs"] if m["kind"] == "mech"]
        npcs = [m for m in obs["mechs"] if m["kind"] == "npc"]
        recent_att = {i for i, at in self.attackers.items() if t - at < 4.0}
        # a fight we are in stays our fight while we can still see them (memory covers 5 s)
        self.engaged = {i: et for i, et in getattr(self, "engaged", {}).items() if t - et < 12.0}
        for i in recent_att:
            self.engaged[i] = t

        # ---------------- pick a combat target
        target = None
        for m in mechs:
            d = dist2(m["pos"], pos)
            mh = m.get("hp", 1400) / max(1.0, m.get("max_hp", 1400))
            score = None
            if m["id"] in recent_att or m["id"] in self.engaged:
                score = 100 - d / 10                                   # shoot back, and finish the fight
            elif d < 40:
                score = 80 - d                                          # it can see us anyway
            elif mh < 0.4 and d < 170 and hpf > 0.6 and len(mechs) <= 2:
                score = 60 - d / 10 + (1 - mh) * 20                     # vulture
            elif LONE and len(mechs) == 1 and d < 170 and hpf > 0.5:
                # a lone mech facing us or closing in is hunting us: shoot first
                to_me = math.atan2(pos[1] - m["pos"][1], pos[0] - m["pos"][0])
                facing = abs(angdiff(m["aim"][0], to_me)) < 0.5
                v = m.get("vel", [0, 0, 0])
                closing = (v[0] * math.cos(to_me) + v[1] * math.sin(to_me)) > 3.0
                if facing or closing:
                    score = 70 - d / 10
            if score is not None and (target is None or score > target[0]):
                target = (score, m)
        if target is not None and target[1]["kind"] == "mech":
            self.engaged[target[1]["id"]] = t
        if target is None:
            for m in npcs:
                d = dist2(m["pos"], pos)
                if m["id"] in recent_att or (d < 140 and hpf > 0.7 and t < 200 and not mechs
                                              and all(t - s[0] > 8 for s in self.seen.values())):
                    sc = 30 - d / 10
                    if target is None or sc > target[0]:
                        target = (sc, m)
        target = target[1] if target else None

        # ---------------- where to go
        if (self.spot is None or t - self.spot_t > 45
                or self.spot_phase != (round(z["next_center"][0]), round(z["next_center"][1]), round(z["next_radius"]))
                or self.danger_at(self.spot, t) > 4.0 and t - self.spot_t > 6):
            self.choose_spot(obs)
        goal = self.spot
        loot_goal = None
        calm = target is None and not recent_att and all(t - s[0] > 5 for s in self.seen.values())
        if calm:
            slot1 = me["weapons"][1]
            have = {wp["kind"] for wp in me["weapons"] if wp}
            for p in obs["pickups"]:
                if p["kind"] in have or WEAPON_PREF.get(p["kind"], 0) < 2:
                    continue
                if slot1 is not None and WEAPON_PREF.get(slot1["kind"], 0) >= WEAPON_PREF[p["kind"]]:
                    continue
                if dist2(p["pos"], pos) < (90 if t < 100 else 45) and self.time_safe(z, t, p["pos"]) > 20 and abs(p["pos"][2] - pos[2]) < 4:
                    loot_goal = (p["pos"][0], p["pos"][1])
                    if dist2(p["pos"], pos) < self.cfg["loot"]["pickup_radius"] - 0.5:
                        act["take"] = p["id"]
                        if slot1 is not None:
                            act["drop"] = 1
                    break
            if loot_goal is None and me["cubes"] < 10:
                for c in obs["cubes"]:
                    if dist2(c["pos"], pos) < 35 and abs(c["pos"][2] - pos[2]) < 4 and self.time_safe(z, t, c["pos"]) > 20:
                        loot_goal = (c["pos"][0], c["pos"][1])
                        break
        if loot_goal:
            goal = loot_goal

        # ---------------- movement speed: silent unless the zone forces us
        walk = st.get("walk_speed", 11.7)
        sneak = min(1.0, (self.cfg["sound"]["step_speed"] - 0.4) / walk)
        dgoal = dist2(pos, goal)
        tsafe = self.time_safe(z, t, pos)
        need = dgoal * 1.4 / max(1.0, tsafe - 8)
        boost = False
        if not me["in_zone"]:
            mag = 1.0
            boost = me["energy"] > 15
            c, _ = self.zone_at(z, t)
            goal = goal if dist2(goal, c) < dist2(pos, c) else (c[0], c[1])
        elif need > sneak * walk or tsafe < 15:
            mag = 1.0
            boost = need > walk and me["energy"] > 35
        else:
            mag = sneak
        mv = self.nav_vector(pos, goal)
        if mv == (0.0, 0.0):
            mag = 0.0

        # ---------------- combat movement
        if target is not None:
            dvec = (target["pos"][0] - pos[0], target["pos"][1] - pos[1])
            dd = math.hypot(*dvec) or 1.0
            perp = (-dvec[1] / dd, dvec[0] / dd)
            if t - self.strafe_t > self.strafe_len:
                self.strafe = -self.strafe
                self.strafe_t = t
                self.strafe_len = self.rng.uniform(STRAFE_MIN, STRAFE_MAX)
            if hpf < 0.35 or len(recent_att) > 1:
                # break off: back away toward our spot while still facing them
                away = (-dvec[0] / dd, -dvec[1] / dd)
                mv = (away[0] * 0.7 + mv[0] * 0.5 + perp[0] * self.strafe * 0.4,
                      away[1] * 0.7 + mv[1] * 0.5 + perp[1] * self.strafe * 0.4)
                mag = 1.0
                boost = me["energy"] > 20
            else:
                keep = STRAFE_GOAL if me["in_zone"] else 0.6
                mv = (perp[0] * self.strafe + mv[0] * keep, perp[1] * self.strafe + mv[1] * keep)
                mag = 1.0
                boost = me["energy"] > STRAFE_BOOST_E
        # dodge charged lasers aimed at us
        for m in mechs:
            if m.get("charging"):
                to_me = math.atan2(pos[1] - m["pos"][1], pos[0] - m["pos"][0])
                if abs(angdiff(m["aim"][0], to_me)) < 0.15:
                    dvec = (pos[0] - m["pos"][0], pos[1] - m["pos"][1])
                    dd = math.hypot(*dvec) or 1.0
                    mv = (-dvec[1] / dd * self.strafe, dvec[0] / dd * self.strafe)
                    mag, boost = 1.0, me["energy"] > 10

        # ---------------- stuck recovery
        if t < self.unstick_until:
            mv = self.unstick_dir
            mag = 1.0
            act["jump"] = me["grounded"]
            boost = me["energy"] > 10
        elif mag > 0.3 and self.stuck(t):
            a = math.atan2(mv[1], mv[0]) + self.rng.choice((-1, 1)) * self.rng.uniform(0.8, 1.6)
            self.unstick_dir = (math.cos(a), math.sin(a))
            self.unstick_until = t + 0.8
            self.path = None
            self.hist = []

        n = math.hypot(*mv)
        if n > 1e-6:
            act["move"] = [mv[0] / n * mag, mv[1] / n * mag]
        else:
            act["move"] = [0.0, 0.0]
        act["boost"] = bool(boost)

        # ---------------- aim, look, fire
        fire = [False, False]
        if target is not None:
            gun = me["weapons"][0]
            spd = gun["stats"]["speed"] if gun and gun["kind"] == "gun" else 252
            yaw, pitch, d = self.lead(me, target, spd)
            act["aim"] = [yaw, pitch]
            err = abs(angdiff(me["aim"][0], yaw)) + abs(me["aim"][1] - pitch) * 0.5
            tol = math.atan2(2.0, max(d, 1.0)) + 0.01
            for i, wpn in enumerate(me["weapons"]):
                if not wpn:
                    continue
                k = wpn["kind"]
                s = wpn["stats"]
                if k == "gun":
                    if d < s["range"] * 0.95 and err < tol and wpn.get("reload", 0) == 0:
                        fire[i] = True
                elif k == "lightning":
                    if d < s["range"] * 0.95 and abs(angdiff(me["aim"][0], yaw)) < math.radians(s["cone_deg"]) * 0.4:
                        fire[i] = True
                elif k == "laser":
                    tgt_yaw = math.atan2(target["pos"][1] - pos[1], target["pos"][0] - pos[0])
                    charge = wpn.get("charge", 0)
                    if d < s["range"] * 0.95 and wpn.get("cooldown", 0) == 0:
                        if charge >= s["charge"] * 0.92 and abs(angdiff(me["aim"][0], tgt_yaw)) < tol * 1.5:
                            fire[i] = False  # let go: fire
                        else:
                            fire[i] = True   # keep charging
            if me["weapons"][0] and me["weapons"][0]["kind"] == "gun" and me["weapons"][0].get("ammo", 1) == 0:
                act["reload"] = True
            self.scan_yaw = yaw
        else:
            look = None
            if self.last_dmg and t - self.last_dmg[0] < 3:
                look = self.last_dmg[1]
            else:
                loud = [s for s in obs["sounds"] if s["kind"] in LOUD and s["dist"] != "far"]
                if loud:
                    look = loud[0]["bearing"]
            if look is None:
                if mag > 0 and n > 1e-6:
                    base = math.atan2(mv[1], mv[0])
                    if t - self.scan_t > 1.0:
                        self.scan_t = t
                        self.scan_yaw = base + self.rng.choice((-1.0, 0.0, 1.0))
                    look = self.scan_yaw if self.scan_yaw is not None else base
                else:
                    if self.scan_yaw is None or t - self.scan_t > 1.2:
                        self.scan_t = t
                        self.scan_yaw = (self.scan_yaw or 0.0) + 2.0
                    look = self.scan_yaw
            act["aim"] = [look, 0.0]
            gun = me["weapons"][0]
            if gun and gun["kind"] == "gun" and gun.get("ammo", 12) < gun["stats"]["mag"] * 0.5 and not mechs:
                act["reload"] = True
        act["fire"] = fire
        return act
