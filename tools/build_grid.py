"""Offline precompute: turn kit/arena/v2 into src/grid.bin, a 2 m grid the bot loads in milliseconds.

Per 2 m cell (row-major, row j = y):
  ground  int16  ground height, cm (terrain sampled at the cell center)
  top     int16  top of the tallest solid that blocks a walking mech here, cm (-32768 = none)
  canopy  uint8  how many tree crowns cover the cell at mech height (0-255)

Run: python tools/build_grid.py   (pure standard library; takes about a minute)
"""
import array
import json
import math
import os
import struct
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARENA = os.path.join(ROOT, "kit", "arena", "v2")
OUT = os.path.join(ROOT, "src", "grid.bin")
CELL = 2.0
MECH_H = 9.0
STEP_UP = 1.2


def main() -> None:
    w = json.load(open(os.path.join(ARENA, "world.json")))
    t = w["terrain"]
    terr = array.array("h")
    with open(os.path.join(ARENA, "terrain.i16"), "rb") as f:
        terr.frombytes(f.read())
    if struct.pack("=h", 1) != struct.pack("<h", 1):
        terr.byteswap()
    tnx, tny, tminx, tminy, tstep = t["nx"], t["ny"], t["minX"], t["minY"], t["step"]

    def ground(x: float, y: float) -> float:
        fx = min(max((x - tminx) / tstep, 0), tnx - 1.001)
        fy = min(max((y - tminy) / tstep, 0), tny - 1.001)
        i, j = int(fx), int(fy)
        ax, ay = fx - i, fy - j
        a = terr[j * tnx + i]; b = terr[j * tnx + i + 1]
        c = terr[(j + 1) * tnx + i]; d = terr[(j + 1) * tnx + i + 1]
        return ((a * (1 - ax) + b * ax) * (1 - ay) + (c * (1 - ax) + d * ax) * ay) / 100.0

    b = w["bounds"]
    minx, miny = math.floor(b["minX"]), math.floor(b["minY"])
    nx = int(math.ceil((b["maxX"] - minx) / CELL))
    ny = int(math.ceil((b["maxY"] - miny) / CELL))
    n = nx * ny
    gr = array.array("h", [0]) * n
    top = array.array("h", [-32768]) * n
    can = bytearray(n)

    for j in range(ny):
        y = miny + (j + 0.5) * CELL
        for i in range(nx):
            gr[j * nx + i] = int(round(ground(minx + (i + 0.5) * CELL, y) * 100))

    def inside(px: float, py: float, ring: list) -> bool:
        c = False
        m = len(ring) // 2
        x1, y1 = ring[-2], ring[-1]
        for k in range(m):
            x2, y2 = ring[2 * k], ring[2 * k + 1]
            if (y2 > py) != (y1 > py) and px < (x1 - x2) * (py - y2) / (y1 - y2) + x2:
                c = not c
            x1, y1 = x2, y2
        return c

    for p in w["prisms"]:
        ring = p["ring"]
        xs, ys = ring[0::2], ring[1::2]
        i0 = max(0, int((min(xs) - minx) / CELL)); i1 = min(nx - 1, int((max(xs) - minx) / CELL))
        j0 = max(0, int((min(ys) - miny) / CELL)); j1 = min(ny - 1, int((max(ys) - miny) / CELL))
        for j in range(j0, j1 + 1):
            cy = miny + (j + 0.5) * CELL
            for i in range(i0, i1 + 1):
                cx = minx + (i + 0.5) * CELL
                if not inside(cx, cy, ring) or any(inside(cx, cy, h) for h in p.get("holes") or []):
                    continue
                k = j * nx + i
                g = gr[k] / 100.0
                if p["base"] - g >= MECH_H or p["top"] - g <= STEP_UP:
                    continue  # high bridge you walk under, or a deck you walk onto
                top[k] = max(top[k], int(round(p["top"] * 100)))

    tr = w["trees"]
    f = tr["fields"]; d = tr["data"]; m = len(f)
    ix = {k: f.index(k) for k in ("crownX", "crownY", "crownR", "crownBase", "height")}
    for q in range(len(d) // m):
        row = d[q * m:(q + 1) * m]
        cx, cy, r = row[ix["crownX"]], row[ix["crownY"]], row[ix["crownR"]]
        lo, hi = row[ix["crownBase"]], row[ix["height"]]
        i0 = max(0, int((cx - r - minx) / CELL)); i1 = min(nx - 1, int((cx + r - minx) / CELL))
        j0 = max(0, int((cy - r - miny) / CELL)); j1 = min(ny - 1, int((cy + r - miny) / CELL))
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                k = j * nx + i
                g = gr[k] / 100.0
                if hi < g + 4 or lo > g + MECH_H + 2:
                    continue  # crown too low or too high to hide a 9 m mech
                if math.hypot(minx + (i + 0.5) * CELL - cx, miny + (j + 0.5) * CELL - cy) <= r:
                    can[k] = min(255, can[k] + 1)

    head = json.dumps({"minx": minx, "miny": miny, "nx": nx, "ny": ny, "cell": CELL}).encode()
    body = gr.tobytes() + top.tobytes() + bytes(can)
    with open(OUT, "wb") as fo:
        fo.write(struct.pack("<I", len(head)) + head + zlib.compress(body, 9))
    blocked = sum(1 for v in top if v != -32768)
    print(f"{nx}x{ny} cells, {blocked} blocked, {sum(1 for v in can if v)} under canopy -> {OUT} ({os.path.getsize(OUT)} bytes)")


if __name__ == "__main__":
    main()
