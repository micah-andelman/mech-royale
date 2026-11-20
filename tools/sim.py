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
            if not world.nfree(I, J) or any(math.dist(p, s) < 100 for s in spots):
                continue
            spots.append(p)
        hello = dict(HELLO)
        for i, p in enumerate(spots):
            ours = i == 0 or mode == "ffa"
            h = dict(hello, slot=i, seed=self.rng.randrange(1 << 30))
            b = Brain(h) if ours else None
            if b is not None:
                b.w = world  # share the loaded grid between copies
            self.mechs.append(Mech(i, (p[0], p[1], world.ground_at(*p)), cfg, b))
        self.bullets = []
        self.alive = len(self.mechs)

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
                     "energy": m.energy, "max_energy": 100, "grounded": True, "cubes": m.cubes, "upgrades": [],
                     "in_zone": math.dist(m.pos[:2], c) <= r,
                     "weapons": [{"kind": "gun", "cooldown": max(0, m.cool), "ammo": m.ammo, "reload": max(0, m.reload),
                                  "stats": gun}, None],
                     "stats": dict(self.cfg["mech"])},
            "mechs": vis, "projectiles": [], "crates": [], "cubes": [], "pickups": [],
            "sounds": m.sounds, "beams": [], "events": m.events, "drops": [],
            "zone": {k: ph[k] for k in ("center", "radius", "next_center", "next_radius", "shrink_start", "shrink_end", "dps")},
            "offer": None,
        }

    def hunter_cmd(self, m, ph):
        """House-style hunter: go to the zone, then hunt the nearest visible (or, in squad mode, any) target."""
        c, r = self.zone_now(ph)
        tgts = [o for o in self.mechs if o.alive and o is not m and (self.mode != "squad" or o.brain is not None)]
        vis = [o for o in tgts if self.sees(m, o) or self.mode == "squad"]
        cmd = {}
        goal = ph["next_center"]
        if vis:
            o = min(vis, key=lambda o: math.dist(o.pos, m.pos))
            d = math.dist(o.pos[:2], m.pos[:2])
            yaw = math.atan2(o.pos[1] - m.pos[1], o.pos[0] - m.pos[0])
            cmd["aim"] = [yaw, 0.0]
            if d > 60:
                goal = o.pos[:2]
            cmd["fire"] = d < 200 and self.sees(m, o)
        else:
            cmd["aim"] = [math.atan2(goal[1] - m.pos[1], goal[0] - m.pos[0]), 0.0]
        dx, dy = goal[0] - m.pos[0], goal[1] - m.pos[1]
        n = math.hypot(dx, dy) or 1
        cmd["move"] = [dx / n, dy / n]
        return cmd

    def step(self):
        cfg = self.cfg
        ph = self.zone()
        dt = 1.0 / cfg["match"]["tick_hz"] * cfg["match"]["decide_every"]
        times = []
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
            if boost:
                m.energy -= mc["boost_drain"] * dt
                m.last_spend = self.t
            elif self.t - m.last_spend > mc["energy_regen_delay"]:
                m.energy = min(100, m.energy + mc["energy_regen"] * dt)
            tv = [mv[0] * spd, mv[1] * spd] if mag > 1e-6 else [0, 0]
            if mag > 1:
                tv = [tv[0] / mag, tv[1] / mag]
            for k in (0, 1):
                dv = tv[k] - m.vel[k]
                lim = mc["ground_accel"] * dt
                m.vel[k] += max(-lim, min(lim, dv))
            nx, ny = m.pos[0] + m.vel[0] * dt, m.pos[1] + m.vel[1] * dt
            if self.w.solid_top(nx, ny) > m.pos[2] + mc["step_up"] or not (self.w.minx + 30 < nx < self.w.maxx - 30 and self.w.miny + 30 < ny < self.w.maxy - 30):
                if self.w.solid_top(nx, m.pos[1]) <= m.pos[2] + mc["step_up"]:
                    ny = m.pos[1]; m.vel[1] = 0
                elif self.w.solid_top(m.pos[0], ny) <= m.pos[2] + mc["step_up"]:
                    nx = m.pos[0]; m.vel[0] = 0
                else:
                    nx, ny = m.pos[0], m.pos[1]; m.vel[0] = m.vel[1] = 0
            m.pos = [nx, ny, self.w.ground_at(nx, ny)]
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
            fire = cmd.get("fire")
            fire = fire[0] if isinstance(fire, list) else fire
            shots = 0
            while fire and m.cool <= 0 and m.ammo > 0 and m.reload <= 0 and shots < 1:
                m.cool += gun["cooldown"]
                m.ammo -= 1
                shots += 1
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
                        dmg = self.cfg["weapons"]["gun"]["damage"] * (1 + 0.04 * min(owner.cubes, 10))
                        yaw_from = math.atan2(-v[1], -v[0])
                        rear = abs((yaw_from - o.aim[0] + math.pi) % (2 * math.pi) - math.pi) > math.pi / 2
                        if rear:
                            dmg *= 1.5
                        o.hp -= dmg
                        o.cause = f"shot by {owner.id}"
                        o.last_hurt = self.t
                        owner.last_hurt = self.t
                        o.events.append({"type": "damaged", "amount": dmg, "weapon": "gun", "bearing": yaw_from, "by": owner.id, "rear": rear})
                        owner.events.append({"type": "hit", "target": o.id, "amount": dmg, "weapon": "gun"})
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
            if math.dist(m.pos[:2], c) > r:
                m.hp -= ph["dps"] * dt
                m.last_hurt = self.t
                m.cause = "zone"
            elif self.t - m.last_hurt > mc["regen_delay"]:
                m.hp = min(m.max_hp, m.hp + mc["regen_per_s"] * dt)
        dead = [m for m in self.mechs if m.alive and m.hp <= 0]
        for m in dead:
            m.alive = False
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
        while self.alive > 1 and self.t < self.cfg["match"]["duration_s"]:
            times += self.step()
        live = [m for m in self.mechs if m.alive]
        live.sort(key=lambda m: -m.hp)
        for k, m in enumerate(live):
            m.place = k + 1
        return times


def main():
    global HELLO
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=10)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--squad", action="store_true")
    ap.add_argument("--ffa", action="store_true")
    a = ap.parse_args()
    HELLO = load_hello()
    cfg = HELLO["config"]
    world = World()
    mode = "ffa" if a.ffa else "squad" if a.squad else "hunters"
    pts, places, all_t = [], [], []
    for k in range(a.n):
        s = Sim(cfg, world, a.seed + k, mode)
        all_t += s.run()
        m0 = s.mechs[0]
        places.append(m0.place)
        pts.append(16 - m0.place)
        cause = "alive" if m0.alive else ("zone" if m0.hp > -200 and not any(True for _ in []) else "?")
        print(f"match {k:3d}: place {m0.place:2d}  t_end {s.t:5.1f}s  hp {m0.hp:7.0f}  cubes {m0.cubes}  {'survived' if m0.alive else 'died: ' + m0.cause}  alive-at-death {m0.place}")
    all_t.sort()
    print(f"\nmode {mode}: mean points {sum(pts) / len(pts):.2f}  (16 - place)  places {places}")
    if all_t:
        print(f"brain act() ms: p50 {all_t[len(all_t) // 2] * 1000:.2f}  p99 {all_t[int(len(all_t) * 0.99)] * 1000:.2f}  max {all_t[-1] * 1000:.2f}")


if __name__ == "__main__":
    main()
