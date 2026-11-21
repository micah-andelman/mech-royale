import sys, os, collections, multiprocessing as mp
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools.sim as S


def run(seed):
    if S.HELLO is None:
        S.HELLO = S.load_hello()
    S.BRAIN = S.load_brain('')
    s = S.Sim(S.HELLO['config'], S._world(), seed, 'squad'); s.run(); us = s.mechs[0]
    who = '-'
    if not us.alive and us.cause.startswith('shot'):
        who = 'hunter' if int(us.cause.split()[-1]) in s.hunters else 'non-hunter'
    return s.t, us.cause.split()[0] if us.cause else 'alive', who, 16 - us.place


if __name__ == '__main__':
    with mp.Pool(20) as p:
        r = p.map(run, range(1, 121))
    print(collections.Counter((x[1], x[2]) for x in r))
    for lo, hi in ((0, 60), (60, 150), (150, 300), (300, 999)):
        sel = [x for x in r if lo <= x[0] < hi]
        print(f"died {lo}-{hi}s: {len(sel)} matches, mean pts {sum(x[3] for x in sel) / max(1, len(sel)):.2f}")
