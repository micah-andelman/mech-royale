"""Sparring: several docker bots in one private play-server room (free-for-all; empty seats = server bots).

    python tools/spar.py -n 10 name1=image1 name2=image2 ...

Prints each match's places and the running mean points (16 - place) per bot. Logs in logs/spar/.
"""
import argparse, glob, json, os, subprocess, sys, time, collections

ap = argparse.ArgumentParser()
ap.add_argument("-n", type=int, default=5)
ap.add_argument("bots", nargs="+")
a = ap.parse_args()
bots = [b.split("=", 1) for b in a.bots]
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
arena = os.path.join(root, "kit", "arena")
pts = collections.defaultdict(list)
env = dict(os.environ, MSYS_NO_PATHCONV="1")
for k in range(a.n):
    out = os.path.join(root, "logs", "spar", time.strftime("%H%M%S"))
    os.makedirs(out, exist_ok=True)
    code_file = os.path.join(out, "code")
    procs = []
    for i, (name, image) in enumerate(bots):
        cmd = f"docker run --rm -i --network none --cpus 1 --cpuset-cpus=8-23 --memory 512m --user 65534:65534 -v {arena}:/arena:ro {image}"
        args = [sys.executable, os.path.join(root, "tools", "live.py"), "--bot", cmd, "--map", arena, "--hello-map-dir", "/arena",
                "--out", os.path.join(out, name), "--name", name]
        if i == 0:
            args += ["--wait-players", str(len(bots)), "--code-file", code_file]
        else:
            while not os.path.exists(code_file):
                time.sleep(0.3)
            args += ["--code", open(code_file).read().strip()]
        procs.append(subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    for p in procs:
        try:
            p.wait(timeout=900)
        except subprocess.TimeoutExpired:
            p.kill()
    res = None
    for f in glob.glob(os.path.join(out, "*", "*.jsonl")):
        if f.endswith(".live.jsonl"):
            continue
        for line in open(f):
            if '"results"' in line:
                m = json.loads(line)["m"]
                if m.get("type") == "results":
                    res = m
    if not res:
        print(f"match {k+1}: no results", flush=True)
        continue
    names = {n for n, _ in bots}
    row = []
    for m in res["mechs"]:
        if m["name"] in names:
            pts[m["name"]].append(16 - m["place"])
            row.append(f"{m['name']}={m['place']}")
    server = [16 - m["place"] for m in res["mechs"] if m["name"] not in names]
    pts["(server avg)"].append(sum(server) / max(1, len(server)))
    print(f"match {k+1}: " + " ".join(row), flush=True)
print("mean points:")
for n, v in sorted(pts.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
    print(f"  {n:14s} {sum(v)/len(v):5.2f}  n={len(v)}")
