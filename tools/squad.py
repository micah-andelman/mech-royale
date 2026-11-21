"""Practice-squad room: one candidate (docker image) plus 15 hunter.py clients in one private room.
usage: python squad.py <repo root> <matches> <name>=<image> [cpuset]"""
import glob
import json
import os
import subprocess
import sys
import time

ROOT, N, spec = sys.argv[1], int(sys.argv[2]), sys.argv[3]
cpuset = sys.argv[4] if len(sys.argv) > 4 else "0-7"
name, image = spec.split("=", 1)
HERE = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
arena = os.path.join(ROOT, "kit", "arena").replace("\\", "/")
live = os.path.join(ROOT, "tools", "live.py")
py = sys.executable.replace("\\", "/")
env = dict(os.environ, MSYS_NO_PATHCONV="1")
TIERS = [(0.07, 0)] * 4 + [(0.045, 1)] * 4 + [(0.03, 2)] * 4 + [(0.018, 3)] * 3
SQUAD_NAMES = {"easy", "medium", "hard", "expert"}
pts = []
for k in range(N):
    out = HERE + "/runs/" + name + "/" + time.strftime("%H%M%S")
    bb = out + "/bb"
    os.makedirs(bb, exist_ok=True)
    code_file = out + "/code"
    cmd = ("docker run --rm -i --network none --cpus 1 --cpuset-cpus=%s --memory 512m --user 65534:65534 "
           "-v %s:/arena:ro %s" % (cpuset, arena, image))
    procs = [subprocess.Popen(
        [sys.executable, live, "--bot", cmd, "--map", arena, "--hello-map-dir", "/arena", "--out", out + "/" + name,
         "--name", name, "--wait-players", "16", "--code-file", code_file],
        env=env, stdout=subprocess.DEVNULL, stderr=open(out + "/host.err", "w"))]
    t0 = time.time()
    while not os.path.exists(code_file) and time.time() - t0 < 30:
        time.sleep(0.3)
    if not os.path.exists(code_file):
        print("match %d: no room code (see %s/host.err)" % (k + 1, out), flush=True)
        procs[0].kill()
        continue
    code = open(code_file).read().strip()
    for i, (err, rank) in enumerate(TIERS):
        hc = "%s %s/hunter.py %s %s %s %d" % (py, HERE, ROOT.replace("\\", "/"), bb, err, rank)
        procs.append(subprocess.Popen(
            [sys.executable, live, "--bot", hc, "--map", arena, "--out", "%s/h%02d" % (out, i), "--name", "h%02d" % i,
             "--code", code],
            env=env, stdout=subprocess.DEVNULL, stderr=open("%s/h%02d.err" % (out, i), "w")))
        time.sleep(0.25)
    res = None
    t0 = time.time()
    while res is None and time.time() - t0 < 420:
        time.sleep(3)
        # the candidate's own "end" message is enough: stop the room as soon as it dies
        for f in glob.glob(out + "/" + name + "/*.jsonl"):
            with open(f, "rb") as fh:
                fh.seek(max(0, os.path.getsize(f) - 4000))
                tail = fh.read().decode(errors="replace")
            for line in tail.splitlines():
                if '"type": "end"' in line:
                    try:
                        e = json.loads(line)
                        res = {"mechs": [{"name": name, "place": e["m"]["place"], "kills": e["m"]["kills"],
                                          "damage": -1}], "died_at": round(time.time() - t0)}
                    except (ValueError, KeyError):
                        pass
        for f in glob.glob(out + "/h*/*.jsonl"):
            if res or os.path.getsize(f) < 1000:
                continue
            with open(f, "rb") as fh:
                fh.seek(max(0, os.path.getsize(f) - 6000))
                tail = fh.read().decode(errors="replace")
            for line in tail.splitlines():
                if '"type": "results"' in line:
                    try:
                        res = json.loads(line)["m"]
                    except ValueError:
                        pass
    time.sleep(1)
    for p in procs:
        if p.poll() is None:
            p.kill()
    subprocess.run("docker ps -q --filter ancestor=%s" % image, shell=True, capture_output=True)
    if not res:
        print("match %d: no results (see %s)" % (k + 1, out), flush=True)
        continue
    me = [m for m in res["mechs"] if m["name"] == name][0]
    stray = sorted({m["name"] for m in res["mechs"]} - SQUAD_NAMES - {name})
    pts.append(16 - me["place"])
    print("match %d: %s place %d kills %d (room stopped after %s s) dir %s"
          % (k + 1, name, me["place"], me["kills"], res.get("died_at", "full"), out.split("/runs/")[-1]), flush=True)
if pts:
    print("%s: mean points %.2f over %d matches: %s" % (name, sum(pts) / len(pts), len(pts), pts))
