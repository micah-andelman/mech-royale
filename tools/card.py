import json, glob, os, collections, math, sys
root = sys.argv[1]
agg = collections.defaultdict(lambda: collections.defaultdict(float))
for f in glob.glob(root + "/*/*/*.jsonl"):
    if f.endswith(".live.jsonl") or os.path.getsize(f) < 200000: continue
    bot = os.path.basename(os.path.dirname(f)); a = agg[bot]
    last_t = 0; prev_ammo = None; prev_cd = {}; vis_ticks = 0; n = 0; held = set(); first_seen = None; first_fire_delay = []
    acts = {}
    rows = []
    for l in open(f):
        r = json.loads(l); m = r["m"]
        if r["dir"] == "act": 
            if "tick" in m: acts[m["tick"]] = m
        elif m.get("type") == "obs": rows.append(m)
    if len(rows) < 100: continue
    a["matches"] += 1
    seen_at = None
    for o in rows:
        s = o["self"]; n += 1; last_t = o["t"]
        mechs = [m for m in o.get("mechs", []) if m.get("kind") == "mech"]
        act = acts.get(o["tick"], {})
        if mechs:
            a["vis_ticks"] += 1
            if any(act.get("fire", [])): a["vis_fire_ticks"] += 1
            if seen_at is None: seen_at = o["t"]
            # enemies looking at us / at our back
            for m in mechs:
                to_me = math.atan2(s["pos"][1]-m["pos"][1], s["pos"][0]-m["pos"][0])
                d = math.dist(m["pos"][:2], s["pos"][:2])
                a["dist_sum"] += d; a["dist_n"] += 1
        else:
            seen_at = None
        w0 = s["weapons"][0]
        if w0 and w0["kind"] == "gun":
            am = w0.get("ammo")
            if prev_ammo is not None and am is not None and am < prev_ammo: a["gun_shots"] += prev_ammo - am
            prev_ammo = am
        for e in o.get("events", []):
            t = e.get("type")
            if t == "hit" and isinstance(e.get("target"), int) and e["target"] < 16:
                a["dealt"] += e["amount"]; a["hits_" + e["weapon"]] += 1; a["dealt_" + e["weapon"]] += e["amount"]
                if e.get("rear"): a["dealt_rear"] += e["amount"]
            elif t == "damaged":
                a["taken"] += e["amount"]; a["taken_" + str(e.get("weapon"))] += e["amount"]
                if e.get("rear"): a["taken_rear"] += e["amount"]
                if e.get("by") is None: a["taken_zone"] += e["amount"]
            elif t == "kill": a["kills"] += 1
    a["secs"] += last_t; a["ticks"] += n
print(f"{'bot':8s} {'n':>2s} {'surv s':>6s} {'kills':>5s} {'dealt':>6s} {'taken':>6s} {'ratio':>5s} {'gun hit%':>8s} {'laser hits':>10s} {'dealt gun/laser/lightning':>26s} {'taken rear%':>11s} {'zone dmg':>8s} {'fire% when enemy seen':>21s} {'enemy seen%':>11s} {'avg dist':>8s}")
for bot, a in sorted(agg.items()):
    m = a["matches"]
    if not m: continue
    print(f"{bot:8s} {int(m):2d} {a['secs']/m:6.0f} {a['kills']/m:5.1f} {a['dealt']/m:6.0f} {a['taken']/m:6.0f} {a['dealt']/max(1,a['taken']):5.2f} {100*a['hits_gun']/max(1,a['gun_shots']):8.0f} {a['hits_laser']/m:10.1f} {a['dealt_gun']/m:8.0f}/{a['dealt_laser']/m:6.0f}/{a['dealt_lightning']/m:6.0f}    {100*a['taken_rear']/max(1,a['taken']):11.0f} {a['taken_zone']/m:8.0f} {100*a['vis_fire_ticks']/max(1,a['vis_ticks']):21.0f} {100*a['vis_ticks']/max(1,a['ticks']):11.0f} {a['dist_sum']/max(1,a['dist_n']):8.0f}")
