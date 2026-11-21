import json, glob, os, collections, math, sys
wrap = lambda x: (x + math.pi) % (2*math.pi) - math.pi
agg = collections.defaultdict(lambda: collections.defaultdict(float))
for f in glob.glob(sys.argv[1] + "/*/*/*.jsonl"):
    if f.endswith(".live.jsonl") or os.path.getsize(f) < 200000: continue
    bot = os.path.basename(os.path.dirname(f)); a = agg[bot]
    prev_vis = set(); hist = []
    for l in open(f):
        if '"dir": "in"' not in l[:20]: continue
        o = json.loads(l)["m"]
        if o.get("type") != "obs": continue
        s = o["self"]; vis = {m["id"] for m in o.get("mechs", []) if m.get("kind") == "mech"}
        attackers = set()
        for e in o.get("events", []):
            if e.get("type") == "damaged" and e.get("by") is not None and e.get("weapon") != "zone":
                amt = e["amount"]; a["dmg"] += amt
                off = abs(wrap(s["aim"][0] - e["bearing"]))   # angle between where we face and where the shot came from
                a["off_sum"] += off * amt
                if off > math.pi/2: a["dmg_behind"] += amt
                if e["by"] in vis or e["by"] in prev_vis: a["dmg_seen"] += amt
                else: a["dmg_unseen"] += amt
                attackers.add(e["by"])
        hist.append((o["t"], attackers)); hist = [h for h in hist if o["t"] - h[0] <= 3.0]
        na = len(set().union(*[h[1] for h in hist])) if hist else 0
        if na: a["under_fire_ticks"] += 1; a["attackers_sum"] += na
        if na >= 2: a["multi_ticks"] += 1
        for e in o.get("events", []):
            if e.get("type") == "damaged" and e.get("by") is not None and na >= 2: a["dmg_multi"] += e["amount"]
        prev_vis = vis
print(f"{'bot':8s} {'dmg from attacker we did not see%':>33s} {'dmg from behind our facing%':>28s} {'mean angle off':>14s} {'dmg while 2+ attackers%':>24s} {'avg attackers':>13s}")
for b, a in sorted(agg.items()):
    d = max(1, a["dmg"])
    print(f"{b:8s} {100*a['dmg_unseen']/d:33.0f} {100*a['dmg_behind']/d:28.0f} {math.degrees(a['off_sum']/d):13.0f}° {100*a['dmg_multi']/d:24.0f} {a['attackers_sum']/max(1,a['under_fire_ticks']):13.2f}")
