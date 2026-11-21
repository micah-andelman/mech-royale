"""Homemade simulator, v0. Rough, but it follows protocol.md where it matters for testing:
movement with acceleration and wall blocking (grid), the zone and its damage, regen, vision (120 deg wedge
to 200 m plus 45 m all round, buildings and canopy block), the gun with travel time and lead-less house
hunters, cubes from kills, sounds with bearing noise, and placement scoring.

Not modelled yet: jumping/roofs, mortar/laser/lightning for opponents, crates, drones, loot, drops, upgrades.

Usage:
  python tools/sim.py                       # 10 matches, our brain vs 15 simple hunters, prints placements
  python tools/sim.py -n 50 --squad         # house-style squad: share vision, hunt us
  python tools/sim.py --ffa                 # 16 copies of our brain (closer to the final)
"""
import argparse
import json
import math
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from src.brain import Brain  # noqa: E402
from src.world import World  # noqa: E402

HELLO = None
BRAIN = Brain


def load_brain(spec):
    """'' = working tree; 'example' = the kit's example bot; anything else = a git revision."""
    import importlib
    import shutil
    import subprocess
    import types
    if not spec:
        return Brain
    if spec == "example":
        import mechfight as mf
        cap = {}
        real = mf.run
        mf.run = lambda cls: cap.setdefault("cls", cls)
        src = open(os.path.join(ROOT, "tools", "example_bot.py")).read()
        g = {"__name__": "example_bot"}
        exec(compile(src, "example_bot.py", "exec"), g)
        mf.run = real
        return cap["cls"]
    d = os.path.join(ROOT, "tools", "_rev", spec)
    pkg = os.path.join(d, "revsrc_" + spec)
    if not os.path.exists(os.path.join(pkg, "brain.py")):
        os.makedirs(pkg, exist_ok=True)
        for f in ("brain.py", "world.py"):
            txt = subprocess.check_output(["git", "-C", ROOT, "show", f"{spec}:src/{f}"])
            open(os.path.join(pkg, f), "wb").write(txt)
        open(os.path.join(pkg, "__init__.py"), "w").close()
        shutil.copy(os.path.join(ROOT, "src", "grid.bin"), os.path.join(pkg, "grid.bin"))
    sys.path.insert(0, d)
    return importlib.import_module("revsrc_" + spec + ".brain").Brain


def load_hello():
    import gzip
    with gzip.open(os.path.join(ROOT, "kit", "check", "stream.jsonl.gz"), "rt") as f:
        return json.loads(f.readline())


class Mech:
    def __init__(self, i, pos, cfg, brain=None):
        self.id = i
        self.pos = list(pos)
        self.vel = [0.0, 0.0, 0.0]
        self.aim = [random.uniform(-math.pi, math.pi), 0.0]
        self.cmd_aim = list(self.aim)
        m = cfg["mech"]
        self.hp = self.max_hp = m["hp"]
        self.energy = m["energy"]
        self.alive = True
        self.place = None
        self.brain = brain
        self.cool = 0.0
        self.ammo = cfg["weapons"]["gun"]["mag"]
        self.reload = 0.0
        self.last_hurt = -99
        self.last_spend = -99
        self.cubes = 0
        self.events = []
        self.sounds = []
        self.memory = {}
        self.step_t = 0.0
        self.fire = False
        self.cmd = {}
        self.cause = ''
        self.skill_err = 0.03
        self.vz = 0.0
        self.slot1 = None
        self.shots = 0
        self.hits = 0
        self.roam = None
        self.hurt_by_us = -99
        self.grounded = True
        self.path = None
        self.path_goal = None
        self.stuck_t = 0.0


class Sim:
    def __init__(self, cfg, world, seed, mode):
        self.cfg = cfg
        self.w = world
        self.rng = random.Random(seed)
        random.seed(seed)
        self.mode = mode
        self.t = 0.0
        self.tick = 0
        z = cfg["zone"]
        self.zc, self.zr = list(z["start_center"]), z["start_radius"]
        self.phases = []
        c, r, t0 = list(z["start_center"]), z["start_radius"], 0.0
        for ph in z["phases"]:
            nr = ph["radius"]
            ang = self.rng.uniform(0, 2 * math.pi)
            off = self.rng.uniform(0, max(0.0, r - nr))
            nc = [c[0] + math.cos(ang) * off, c[1] + math.sin(ang) * off]
            self.phases.append(dict(center=c, radius=r, next_center=nc, next_radius=nr,
                                    shrink_start=t0 + ph["hold_s"], shrink_end=t0 + ph["hold_s"] + ph["shrink_s"], dps=ph["dps"]))
            c, r, t0 = nc, nr, t0 + ph["hold_s"] + ph["shrink_s"]
        self.mechs = []
        spots = []
        while len(spots) < cfg["match"]["mechs"]:
            ang = self.rng.uniform(0, 2 * math.pi)
            rr = self.rng.uniform(0, z["start_radius"] * 0.8)
            p = (z["start_center"][0] + math.cos(ang) * rr, z["start_center"][1] + math.sin(ang) * rr)
            I, J = world.nidx(p[0], p[1])
            if not world.nfree(I, J) or any(math.dist(p, s) < cfg['match']['spawn_min_separation'] for s in spots):
                continue
            spots.append(p)
        hello = dict(HELLO)
        for i, p in enumerate(spots):
            ours = i == 0 or mode == "ffa"
            h = dict(hello, slot=i, seed=self.rng.randrange(1 << 30))
            b = BRAIN(h) if ours else None
            if b is not None and hasattr(b, "w"):
                b.w = world  # share the loaded grid between copies
            self.mechs.append(Mech(i, (p[0], p[1], world.ground_at(*p)), cfg, b))
        self.bullets = []
        # floor weapon loot (laser / lightning / mortar, 1:1:1), on open ground
        self.pickups = []
        while len(self.pickups) < cfg["loot"]["floor_count"]:
            p = (self.rng.uniform(world.minx + 40, world.maxx - 40), self.rng.uniform(world.miny + 40, world.maxy - 40))
            if world.nfree(*world.nidx(p[0], p[1])):
                self.pickups.append({"id": 8000 + len(self.pickups), "kind": self.rng.choice(["mortar", "laser", "lightning"]),
                                     "pos": [p[0], p[1], world.ground_at(*p)]})
        self.alive = len(self.mechs)
        # house bot skill: 4 easy, 4 medium, 4 hard, 3 expert (aim noise in radians)
        errs = [0.07] * 4 + [0.045] * 4 + [0.03] * 4 + [0.018] * 3
        house = [m for m in self.mechs if m.brain is None]
        for m, e in zip(house, errs):
            m.skill_err = e
        self.order = [m.id for m in house]       # weakest first
        self.hunters = set(self.order[:1])
        self.squad_seen_t = -99
        self.prox = os.environ.get('SIM_PROX', '0') == '1'
        self.squad_seen_pos = (0.0, 0.0)

    def zone(self):
        for ph in self.phases:
            if self.t < ph["shrink_end"] or ph is self.phases[-1]:
                return ph
        return self.phases[-1]

    def zone_now(self, ph):
        t = self.t
        if t <= ph["shrink_start"]:
            return ph["center"], ph["radius"]
        if t >= ph["shrink_end"]:
            return ph["next_center"], ph["next_radius"]
        f = (t - ph["shrink_start"]) / (ph["shrink_end"] - ph["shrink_start"])
        return ([ph["center"][k] + (ph["next_center"][k] - ph["center"][k]) * f for k in (0, 1)],
                ph["radius"] + (ph["next_radius"] - ph["radius"]) * f)

    def sees_point(self, m, q):
        d = math.dist(q[:2], m.pos[:2])
        if d > 200:
            return False
        if d > 45:
            yaw = math.atan2(q[1] - m.pos[1], q[0] - m.pos[0])
            if abs((yaw - m.aim[0] + math.pi) % (2 * math.pi) - math.pi) > math.radians(60):
                return False
        return self.w.los((m.pos[0], m.pos[1], m.pos[2] + 8), (q[0], q[1], q[2] + 1), 3.0)

    def slot_obs(self, m):
        w = m.slot1
        if not w:
            return None
        o = {"kind": w["kind"], "cooldown": max(0.0, w["cool"]), "stats": self.cfg["weapons"][w["kind"]]}
        if w["kind"] == "laser":
            o["charge"] = w["charge"]
        return o

    def damage(self, owner, o, dmg, weapon, from_yaw):
        dmg *= 1 + 0.04 * min(owner.cubes, 10)
        rear = abs((from_yaw - o.aim[0] + math.pi) % (2 * math.pi) - math.pi) > math.pi / 2
        if rear:
            dmg *= 1.5
        o.hp -= dmg
        o.cause = f"shot by {owner.id}"
        if owner.id == 0:
            o.hurt_by_us = self.t
        o.last_hurt = self.t
        owner.last_hurt = self.t
        owner.hits += 1
        o.events.append({"type": "damaged", "amount": dmg, "weapon": weapon, "bearing": from_yaw, "by": owner.id, "rear": rear})
        owner.events.append({"type": "hit", "target": o.id, "amount": dmg, "weapon": weapon})

    def slot1_step(self, m, cmd, dt):
        cfg = self.cfg
        t = cmd.get("take")
        if t is not None:
            for p in self.pickups:
                if p["id"] == t and math.dist(p["pos"][:2], m.pos[:2]) <= cfg["loot"]["pickup_radius"]:
                    if m.slot1 is not None and cmd.get("drop") == 1:
                        self.pickups.append({"id": 9000 + len(self.pickups), "kind": m.slot1["kind"], "pos": list(m.pos)})
                        m.slot1 = None
                    if m.slot1 is None:
                        self.pickups.remove(p)
                        k = p["kind"]
                        m.slot1 = {"kind": k, "cool": max(cfg["loot"]["equip_s"], cfg["weapons"][k]["cooldown"]), "charge": 0.0}
                    break
        w = m.slot1
        if not w:
            return
        w["cool"] -= dt
        fire = cmd.get("fire")
        held = bool(fire[1]) if isinstance(fire, list) and len(fire) > 1 else False
        ws = cfg["weapons"][w["kind"]]
        eye = (m.pos[0], m.pos[1], m.pos[2] + 7)
        if w["kind"] == "lightning":
            if held and w["cool"] <= 0 and m.energy >= ws["energy"]:
                w["cool"] = ws["cooldown"]
                m.energy -= ws["energy"]
                m.last_spend = self.t
                m.shots += 1
                self.sound(m, "lightning")
                best = None
                for o in self.mechs:
                    if o is m or not o.alive:
                        continue
                    d = math.dist(o.pos[:2], m.pos[:2])
                    b = math.atan2(o.pos[1] - m.pos[1], o.pos[0] - m.pos[0])
                    if d <= ws["range"] and abs((b - m.aim[0] + math.pi) % (2 * math.pi) - math.pi) <= math.radians(ws["cone_deg"]) / 2                             and self.w.los(eye, (o.pos[0], o.pos[1], o.pos[2] + 4.5), 2.0) and (best is None or d < best[0]):
                        best = (d, o, b)
                if best:
                    self.damage(m, best[1], ws["damage"], "lightning", best[2] + math.pi)
        elif w["kind"] == "laser":
            if held and w["cool"] <= 0:
                w["charge"] = min(ws["charge"] + ws["hold_s"], w["charge"] + dt)
                if w["charge"] < ws["charge"] + ws["hold_s"]:
                    return
            if w["charge"] > 0 and (not held or w["charge"] >= ws["charge"] + ws["hold_s"]):
                if w["charge"] < ws["min_charge"]:
                    w["charge"] += dt
                    return
                frac = ws["min_frac"] + (1 - ws["min_frac"]) * min(1.0, w["charge"] / ws["charge"])
                w["charge"] = 0.0
                w["cool"] = ws["cooldown"]
                m.shots += 1
                self.sound(m, "laser")
                d3 = (math.cos(m.aim[1]) * math.cos(m.aim[0]), math.cos(m.aim[1]) * math.sin(m.aim[0]), math.sin(m.aim[1]))
                best = None
                for o in self.mechs:
                    if o is m or not o.alive:
                        continue
                    c = (o.pos[0] - eye[0], o.pos[1] - eye[1], o.pos[2] + 4.5 - eye[2])
                    along = c[0] * d3[0] + c[1] * d3[1] + c[2] * d3[2]
                    if along <= 0 or along > ws["range"]:
                        continue
                    perp = math.sqrt(max(0.0, c[0] ** 2 + c[1] ** 2 + c[2] ** 2 - along ** 2))
                    if perp < 2.5 + 2.0 * 0 + 1.0 and self.w.los(eye, (o.pos[0], o.pos[1], o.pos[2] + 4.5), 2.0) and (best is None or along < best[0]):
                        best = (along, o)
                if best:
                    self.damage(m, best[1], ws["damage"] * frac, "laser", m.aim[0] + math.pi)

    def surface(self, x, y, z):
        g = self.w.ground_at(x, y)
        t = self.w.solid_top(x, y)
        if t > g and t <= z + self.cfg["mech"]["step_up"]:
            return t
        return g

    def blocked(self, x, y, z):
        t = self.w.solid_top(x, y)
        return t > self.w.ground_at(x, y) and t > z + self.cfg["mech"]["step_up"]

    def sees(self, a, b):
        d = math.dist(a.pos[:2], b.pos[:2])
        eye = (a.pos[0], a.pos[1], a.pos[2] + 8)
        tgt = (b.pos[0], b.pos[1], b.pos[2] + 4.5)
        if d > 200:
            return False
        if d > 45:
            yaw = math.atan2(b.pos[1] - a.pos[1], b.pos[0] - a.pos[0])
            if abs((yaw - a.aim[0] + math.pi) % (2 * math.pi) - math.pi) > math.radians(60):
                return False
            if self.w.canopy(*b.pos[:2]) >= 2 or self.w.canopy(*a.pos[:2]) >= 3:
                return False
        return self.w.los(eye, tgt, 2.0)

    def sound(self, src, kind):
        rng = self.cfg["sound"]["range"].get(kind, 100)
        if self.mode == "squad" and src.id == 0 and any(o.alive and math.dist(o.pos[:2], src.pos[:2]) <= rng for o in self.mechs[1:]):
            # the squad hears us: a rough fix on our position (bearing noise ~7 deg)
            self.squad_seen_t = self.t
            self.squad_seen_pos = (src.pos[0] + random.gauss(0, 15), src.pos[1] + random.gauss(0, 15))
        for m in self.mechs:
            if m is src or not m.alive or m.brain is None:
                continue
            d = math.dist(m.pos[:2], src.pos[:2])
            if d <= rng:
                b = math.atan2(src.pos[1] - m.pos[1], src.pos[0] - m.pos[0]) + random.gauss(0, math.radians(7))
                band = "near" if d < 60 else "mid" if d < 160 else "far"
                m.sounds.append({"kind": kind, "bearing": b, "dist": band, "age": 0.0})

    def obs_for(self, m, ph):
        vis = []
        for o in self.mechs:
            if o is m or not o.alive:
                continue
            if self.sees(m, o) or (self.mode == "squad" and m.brain is None):
                m.memory[o.id] = self.t
            if self.t - m.memory.get(o.id, -99) <= 5:
                vis.append({"id": o.id, "kind": "mech", "pos": [round(v, 2) for v in o.pos],
                            "vel": [round(v, 2) for v in o.vel], "aim": o.aim, "hp": o.hp, "max_hp": o.max_hp,
                            "cubes": o.cubes, "weapons": ["gun"], "charging": False})
        c, r = self.zone_now(ph)
        gun = self.cfg["weapons"]["gun"]
        return {
            "type": "obs", "tick": self.tick, "t": round(self.t, 2), "alive": self.alive,
            "self": {"id": m.id, "pos": m.pos, "vel": m.vel, "aim": m.aim, "hp": m.hp, "max_hp": m.max_hp,
                     "energy": m.energy, "max_energy": 100, "grounded": m.grounded, "cubes": m.cubes, "upgrades": [],
                     "in_zone": math.dist(m.pos[:2], c) <= r,
                     "weapons": [{"kind": "gun", "cooldown": max(0, m.cool), "ammo": m.ammo, "reload": max(0, m.reload),
                                  "stats": gun}, self.slot_obs(m)],
                     "stats": dict(self.cfg["mech"])},
            "mechs": vis, "projectiles": [], "crates": [], "cubes": [], "pickups": [p for p in self.pickups if self.sees_point(m, p["pos"])],
            "sounds": m.sounds, "beams": [], "events": m.events, "drops": [],
            "zone": {k: ph[k] for k in ("center", "radius", "next_center", "next_radius", "shrink_start", "shrink_end", "dps")},
            "offer": None,
        }

    def hunter_cmd(self, m, ph):
        """House-style hunter: go to the zone, then hunt the nearest visible (or, in squad mode, any) target."""
        c, r = self.zone_now(ph)
        cmd = {}
        goal = ph["next_center"]
        if self.mode == "squad":
            # house squad (track page rules): never fight each other, share what they see,
            # one hunter at the start, one more per house bot we kill, all of them late
            us = self.mechs[0]
            hunting = m.id in self.hunters or self.t > 300
            shared = any(self.sees(o, us) for o in self.mechs if o.alive and o.brain is None) if us.alive else False
            provoked = self.t - m.hurt_by_us < 10
            sees_us = us.alive and self.sees(m, us)
            vis = [us] if sees_us and (hunting or provoked or (self.prox and math.dist(m.pos, us.pos) < 45)) else []
            if not vis and hunting and us.alive:
                # no sight: go where the squad last saw (or heard) us; otherwise sweep toward the circle
                if self.t - self.squad_seen_t < 30:
                    goal = self.squad_seen_pos
                elif m.roam is None or math.dist(m.pos[:2], m.roam) < 10:
                    ang = random.uniform(0, 2 * math.pi)
                    rr = random.uniform(0, ph["next_radius"] * 0.8)
                    m.roam = (ph["next_center"][0] + math.cos(ang) * rr, ph["next_center"][1] + math.sin(ang) * rr)
                    goal = m.roam
                else:
                    goal = m.roam
        else:
            tgts = [o for o in self.mechs if o.alive and o is not m]
            vis = [o for o in tgts if self.sees(m, o)]
        if vis:
            o = min(vis, key=lambda o: math.dist(o.pos, m.pos))
            d = math.dist(o.pos[:2], m.pos[:2])
            lead = d / self.cfg["weapons"]["gun"]["speed"] if m.skill_err < 0.05 else 0.0
            tx, ty = o.pos[0] + o.vel[0] * lead, o.pos[1] + o.vel[1] * lead
            yaw = math.atan2(ty - m.pos[1], tx - m.pos[0]) + random.gauss(0, m.skill_err)
            pitch = math.atan2(o.pos[2] + 4.5 - (m.pos[2] + 7), max(d, 1.0))
            cmd["aim"] = [yaw, pitch]
            if d > 50 and (self.mode != "squad" or m.id in self.hunters or self.t > 300):
                goal = o.pos[:2]
            cmd["fire"] = d < 200 and self.sees(m, o)
        else:
            cmd["aim"] = [math.atan2(goal[1] - m.pos[1], goal[0] - m.pos[0]), 0.0]
        # stay inside the zone: clamp the goal into the current circle
        zc, zr = c, r
        gd = math.dist(goal, zc)
        if gd > zr * 0.8:
            f = zr * 0.8 / max(gd, 1e-6)
            goal = (zc[0] + (goal[0] - zc[0]) * f, zc[1] + (goal[1] - zc[1]) * f)
        mv = self.house_nav(m, goal)
        cmd["move"] = list(mv)
        sp = math.hypot(m.vel[0], m.vel[1])
        m.stuck_t = m.stuck_t + 0.1 if (sp < 1.0 and math.hypot(*mv) > 0.5) else 0.0
        if m.stuck_t > 0.8:
            cmd["jump"] = True
            cmd["boost"] = True
        elif not m.grounded and m.stuck_t > 0:
            cmd["boost"] = True
        if math.dist(m.pos[:2], c) > r - 5:
            cmd["boost"] = True
        return cmd

    def house_nav(self, m, goal):
        from src.world import Path
        w = self.w
        if math.dist(m.pos[:2], goal) < 4:
            return (0.0, 0.0)
        if w.line_free(m.pos, goal):
            d = math.dist(m.pos[:2], goal)
            return ((goal[0] - m.pos[0]) / d, (goal[1] - m.pos[1]) / d)
        if m.path is None or m.path_goal is None or math.dist(m.path_goal, goal) > 25 or self.tick % 100 == m.id:
            m.path = Path(w, m.pos, goal)
            while not m.path.step(20000):
                pass
            m.path_goal = goal
        pts = m.path.points or [goal]
        tgt = pts[-1]
        for k, p in enumerate(pts[:14]):
            if w.line_free(m.pos, p):
                tgt = p
        d = math.dist(m.pos[:2], tgt) or 1.0
        return ((tgt[0] - m.pos[0]) / d, (tgt[1] - m.pos[1]) / d)

    def step(self):
        cfg = self.cfg
        ph = self.zone()
        dt = 1.0 / cfg["match"]["tick_hz"] * cfg["match"]["decide_every"]
        times = []
        us = self.mechs[0]
        if self.mode == "squad" and us.alive:
            for o in self.mechs[1:]:
                if o.alive and self.sees(o, us):
                    self.squad_seen_t, self.squad_seen_pos = self.t, tuple(us.pos[:2])
                    break
        for m in self.mechs:
            if not m.alive:
                continue
            if m.brain is not None:
                o = self.obs_for(m, ph)
                t0 = time.perf_counter()
                m.cmd = m.brain.act(o)
                times.append(time.perf_counter() - t0)
            else:
                m.cmd = self.hunter_cmd(m, ph)
            m.events, m.sounds = [], []
        mc = cfg["mech"]
        for m in self.mechs:
            if not m.alive:
                continue
            cmd = m.cmd
            mv = cmd.get("move", [0, 0])
            mag = min(1.0, math.hypot(*mv))
            boost = cmd.get("boost") and m.energy > 0 and mag > 0
            spd = mc["boost_speed"] if boost else mc["walk_speed"]
            if m.slot1 and m.slot1["kind"] == "laser" and m.slot1["charge"] > 0:
                spd *= mc["charge_move_mult"]
            if boost:
                m.energy -= mc["boost_drain"] * dt
                m.last_spend = self.t
            elif self.t - m.last_spend > mc["energy_regen_delay"]:
                m.energy = min(100, m.energy + mc["energy_regen"] * dt)
            tv = [mv[0] * spd, mv[1] * spd] if mag > 1e-6 else [0, 0]
            if mag > 1:
                tv = [tv[0] / mag, tv[1] / mag]
            grounded = m.pos[2] <= self.surface(m.pos[0], m.pos[1], m.pos[2]) + 0.05
            acc = mc["ground_accel"] if grounded else mc["air_accel"]
            for k in (0, 1):
                dv = tv[k] - m.vel[k]
                lim = acc * dt
                m.vel[k] += max(-lim, min(lim, dv))
            # vertical: jump, jets, gravity, landing on roofs
            if cmd.get("jump") and grounded:
                m.vz = mc["jump_speed"]
                grounded = False
            if cmd.get("boost") and not grounded and m.energy > 0:
                m.vz = min(mc["max_rise_speed"], m.vz + mc["jet_accel"] * dt)
                m.energy -= mc["jet_drain"] * dt
                m.last_spend = self.t
            m.vz -= mc["gravity"] * dt
            z = m.pos[2] + m.vz * dt
            nx, ny = m.pos[0] + m.vel[0] * dt, m.pos[1] + m.vel[1] * dt
            inb = lambda x, y: self.w.minx + 30 < x < self.w.maxx - 30 and self.w.miny + 30 < y < self.w.maxy - 30
            if self.blocked(nx, ny, max(z, m.pos[2])) or not inb(nx, ny):
                if not self.blocked(nx, m.pos[1], max(z, m.pos[2])) and inb(nx, m.pos[1]):
                    ny = m.pos[1]; m.vel[1] = 0
                elif not self.blocked(m.pos[0], ny, max(z, m.pos[2])) and inb(m.pos[0], ny):
                    nx = m.pos[0]; m.vel[0] = 0
                else:
                    nx, ny = m.pos[0], m.pos[1]; m.vel[0] = m.vel[1] = 0
            surf = self.surface(nx, ny, max(z, m.pos[2]))
            if z <= surf:
                z, m.vz = surf, 0.0
            m.pos = [nx, ny, z]
            m.grounded = z <= surf + 0.05
            if math.hypot(m.vel[0], m.vel[1]) > cfg["sound"]["step_speed"]:
                m.step_t += dt
                if m.step_t >= cfg["sound"]["step_every"]:
                    m.step_t = 0
                    self.sound(m, "boost" if boost else "step")
            want = cmd.get("aim", m.cmd_aim)
            m.cmd_aim = want
            dyaw = (want[0] - m.aim[0] + math.pi) % (2 * math.pi) - math.pi
            m.aim[0] += max(-mc["turn_rate"] * dt, min(mc["turn_rate"] * dt, dyaw))
            m.aim[1] += max(-mc["pitch_rate"] * dt, min(mc["pitch_rate"] * dt, want[1] - m.aim[1]))
            gun = cfg["weapons"]["gun"]
            m.cool -= dt
            if m.reload > 0:
                m.reload -= dt
                if m.reload <= 0:
                    m.ammo = gun["mag"]
            if cmd.get("reload") and m.reload <= 0 and m.ammo < gun["mag"]:
                m.reload = gun["reload"]
            self.slot1_step(m, cmd, dt)
            fire = cmd.get("fire")
            fire = fire[0] if isinstance(fire, list) else fire
            shots = 0
            while fire and m.cool <= 0 and m.ammo > 0 and m.reload <= 0 and shots < 1:
                m.cool += gun["cooldown"]
                m.ammo -= 1
                shots += 1
                m.shots += 1
                if m.ammo == 0:
                    m.reload = gun["reload"]
                sp = math.radians(gun["spread_deg"])
                yaw = m.aim[0] + random.gauss(0, sp); pit = m.aim[1] + random.gauss(0, sp)
                d = [math.cos(pit) * math.cos(yaw), math.cos(pit) * math.sin(yaw), math.sin(pit)]
                p = [m.pos[0] + d[0] * 3, m.pos[1] + d[1] * 3, m.pos[2] + 7 + d[2] * 3]
                self.bullets.append([p, [d[0] * gun["speed"], d[1] * gun["speed"], d[2] * gun["speed"]], m, gun["range"]])
                self.sound(m, "gun")
            if m.cool < 0 and not fire:
                m.cool = 0
        # bullets
        keep = []
        for b in self.bullets:
            p, v, owner, rem = b
            hit = False
            for s in range(4):
                p = [p[k] + v[k] * dt / 4 for k in range(3)]
                rem -= math.hypot(*v) * dt / 4
                if rem <= 0 or self.w.solid_top(p[0], p[1]) > p[2] or p[2] < self.w.ground_at(p[0], p[1]):
                    hit = True
                    break
                for o in self.mechs:
                    if o is owner or not o.alive:
                        continue
                    if math.dist(o.pos[:2], p[:2]) < 2.5 + 0.3 and o.pos[2] <= p[2] <= o.pos[2] + 9:
                        self.damage(owner, o, self.cfg["weapons"]["gun"]["damage"], "gun", math.atan2(-v[1], -v[0]))
                        hit = True
                        break
                if hit:
                    break
            if not hit:
                keep.append([p, v, owner, rem])
        self.bullets = keep
        # zone + regen
        c, r = self.zone_now(ph)
        for m in self.mechs:
            if not m.alive:
                continue
            if math.dist(m.pos[:2], c) > r and (m.brain is not None or self.mode != "squad" or ph["next_radius"] <= 0):
                # real house bots handle the zone; they only take zone damage in the final closing circle
                m.hp -= ph["dps"] * dt
                m.last_hurt = self.t
                m.cause = "zone"
            elif self.t - m.last_hurt > mc["regen_delay"]:
                m.hp = min(m.max_hp, m.hp + mc["regen_per_s"] * dt)
        dead = [m for m in self.mechs if m.alive and m.hp <= 0]
        for m in dead:
            m.alive = False
            if self.mode == "squad" and m.brain is None and m.cause == "shot by 0":
                nxt = [i for i in self.order if i not in self.hunters and self.mechs[i].alive]
                if nxt:
                    self.hunters.add(nxt[0])
            m.place = self.alive - len(dead) + 1
            self.sound(m, "death")
        if dead:
            # cubes to the nearest living mech (stand-in for walking over the drop)
            for m in dead:
                near = [o for o in self.mechs if o.alive]
                if near:
                    o = min(near, key=lambda o: math.dist(o.pos, m.pos))
                    if math.dist(o.pos, m.pos) < 40:
                        o.cubes += m.cubes + 2
                        o.max_hp = self.cfg["mech"]["hp"] * (1 + 0.04 * min(o.cubes, 10))
        self.alive -= len(dead)
        self.t += dt
        self.tick += cfg["match"]["decide_every"]
        return times

    def run(self):
        times = []
        while self.alive > 1 and self.t < self.cfg["match"]["duration_s"] and self.mechs[0].alive:
            times += self.step()
        live = [m for m in self.mechs if m.alive]
        live.sort(key=lambda m: -m.hp)
        for k, m in enumerate(live):
            m.place = k + 1
        return times


def play(args):
    global HELLO, BRAIN
    spec, seed, mode = args
    if HELLO is None:
        HELLO = load_hello()
    BRAIN = load_brain(spec)
    world = _world()
    s = Sim(HELLO["config"], world, seed, mode)
    s.run()
    m0 = s.mechs[0]
    kills = sum(1 for o in s.mechs if o is not m0 and o.cause == "shot by 0" and not o.alive)
    return seed, m0.place, kills, round(s.t, 1), (m0.cause if not m0.alive else "survived"), (m0.slot1 or {}).get("kind", "-")


_W = None


def _world():
    global _W
    if _W is None:
        _W = World()
    return _W


def main():
    import multiprocessing as mp
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--squad", action="store_true")
    ap.add_argument("--ffa", action="store_true")
    ap.add_argument("--brain", default="", help="'' (working tree), 'example', or a git revision")
    ap.add_argument("-j", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    mode = "ffa" if a.ffa else "squad" if a.squad else "hunters"
    load_brain(a.brain)  # extract a revision once, before the workers start
    jobs = [(a.brain, a.seed + k, mode) for k in range(a.n)]
    with mp.Pool(a.j) as pool:
        res = pool.map(play, jobs)
    pts = [16 - r[1] for r in res]
    if a.v:
        for r in res:
            print(f"seed {r[0]:4d}: place {r[1]:2d} kills {r[2]} t_end {r[3]:5.1f} {r[4]}")
    import statistics
    se = statistics.stdev(pts) / math.sqrt(len(pts)) if len(pts) > 1 else 0
    import collections
    wk = collections.Counter(r[5] for r in res)
    print(f"brain={a.brain or 'worktree'} mode={mode} n={a.n}: mean {sum(pts) / len(pts):.2f} +- {se:.2f}  kills {sum(r[2] for r in res) / len(res):.2f}  slot1 at end {dict(wk)}")


if __name__ == "__main__":
    main()
