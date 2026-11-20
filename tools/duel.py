"""1-v-1 duel test: our brain vs one house hunter of a given skill, on open ground near the map center.
Usage: python tools/duel.py --brain REV --skill 0.07 -n 40"""
import argparse, math, os, random, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import tools.sim as S


def one(args):
    spec, seed, skill, dist = args
    if S.HELLO is None:
        S.HELLO = S.load_hello()
    S.BRAIN = S.load_brain(spec)
    s = S.Sim(S.HELLO["config"], S._world(), seed, "squad")
    rng = random.Random(seed)
    us, h = s.mechs[0], s.mechs[1]
    for m in s.mechs[2:]:
        m.alive = False
        m.pos = [9999, 9999, 0]
    s.alive = 2
    s.hunters = {1}
    h.skill_err = skill
    w = s.w
    while True:
        p = (rng.uniform(-200, 250), rng.uniform(-150, 150))
        a = rng.uniform(0, 2 * math.pi)
        q = (p[0] + math.cos(a) * dist, p[1] + math.sin(a) * dist)
        if w.nfree(*w.nidx(*p)) and w.nfree(*w.nidx(*q)):
            break
    us.pos = [p[0], p[1], w.ground_at(*p)]
    h.pos = [q[0], q[1], w.ground_at(*q)]
    s.t = 20.0
    while us.alive and h.alive and s.t < 140:
        s.step()
    return (not h.alive) and us.alive, us.hp / us.max_hp if us.alive else 0.0, round(s.t - 20, 1), us.shots, us.hits, h.shots, h.hits


if __name__ == "__main__":
    import multiprocessing as mp
    ap = argparse.ArgumentParser()
    ap.add_argument("--brain", default="")
    ap.add_argument("--skill", type=float, default=0.07)
    ap.add_argument("--dist", type=float, default=120)
    ap.add_argument("-n", type=int, default=40)
    a = ap.parse_args()
    S.load_brain(a.brain)
    with mp.Pool(20) as pool:
        r = pool.map(one, [(a.brain, k + 1, a.skill, a.dist) for k in range(a.n)])
    wins = sum(x[0] for x in r)
    print(f"  our hit rate {sum(x[4] for x in r) / max(1, sum(x[3] for x in r)):.2f} ({sum(x[3] for x in r) / len(r):.0f} shots/duel)  their hit rate {sum(x[6] for x in r) / max(1, sum(x[5] for x in r)):.2f} ({sum(x[5] for x in r) / len(r):.0f} shots/duel)")
    print(f"brain={a.brain or 'worktree'} skill={a.skill}: won {wins}/{a.n}  avg hp left when won {sum(x[1] for x in r if x[0]) / max(1, wins):.2f}  avg duel {sum(x[2] for x in r) / len(r):.0f}s")
