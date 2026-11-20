"""Static world model from the precomputed grid (tools/build_grid.py -> src/grid.bin).

Fine grid: 2 m cells (ground height, top of any solid that blocks a walking mech, canopy count).
Nav grid: 4 m cells (2x2 fine cells), walkable when none of its fine cells holds a solid.
"""
import array
import heapq
import json
import math
import os
import struct
import zlib

NONE = -32768
HERE = os.path.dirname(os.path.abspath(__file__))
SQ2 = math.sqrt(2)


class World:
    def __init__(self, path: str = os.path.join(HERE, "grid.bin")):
        data = open(path, "rb").read()
        hl = struct.unpack("<I", data[:4])[0]
        h = json.loads(data[4:4 + hl])
        body = zlib.decompress(data[4 + hl:])
        self.minx, self.miny, self.nx, self.ny, self.cell = h["minx"], h["miny"], h["nx"], h["ny"], h["cell"]
        n = self.nx * self.ny
        self.ground = array.array("h"); self.ground.frombytes(body[:2 * n])
        self.top = array.array("h"); self.top.frombytes(body[2 * n:4 * n])
        self.can = body[4 * n:5 * n]
        self.maxx = self.minx + self.nx * self.cell
        self.maxy = self.miny + self.ny * self.cell

        # nav grid: 4 m
        nx, ny = self.nx, self.ny
        self.NX, self.NY = nx // 2, ny // 2
        self.ncell = self.cell * 2
        top = self.top
        walk = bytearray(self.NX * self.NY)
        for J in range(self.NY):
            j = 2 * J
            for I in range(self.NX):
                i = 2 * I
                k = j * nx + i
                if top[k] == NONE and top[k + 1] == NONE and top[k + nx] == NONE and top[k + nx + 1] == NONE:
                    walk[J * self.NX + I] = 1
        # keep the 30 m map-edge margin out of plans (the edge is a wall)
        m = 3
        for J in range(self.NY):
            for I in range(self.NX):
                if I < m or J < m or I >= self.NX - m or J >= self.NY - m:
                    walk[J * self.NX + I] = 0
        self.walk = walk
        # cover score per nav cell: canopy over it plus solids in the 12 m square around it
        NX, NY = self.NX, self.NY
        cover = bytearray(NX * NY)
        wallnear = bytearray(NX * NY)
        for J in range(NY):
            for I in range(NX):
                if not walk[J * NX + I]:
                    continue
                c = 0
                for dj in (-1, 0, 1):
                    for di in (-1, 0, 1):
                        I2, J2 = I + di, J + dj
                        if 0 <= I2 < NX and 0 <= J2 < NY and not walk[J2 * NX + I2]:
                            c += 1
                wallnear[J * NX + I] = c
                k = (2 * J) * nx + 2 * I
                cv = self.can[k] + self.can[k + 1] + self.can[k + nx] + self.can[k + nx + 1]
                cover[J * NX + I] = min(255, cv)
        self.canopy4 = cover
        self.wallnear = wallnear

    # ---- fine grid lookups ----
    def fidx(self, x: float, y: float) -> int:
        i = int((x - self.minx) / self.cell)
        j = int((y - self.miny) / self.cell)
        if i < 0 or j < 0 or i >= self.nx or j >= self.ny:
            return -1
        return j * self.nx + i

    def ground_at(self, x: float, y: float) -> float:
        k = self.fidx(x, y)
        return self.ground[k] / 100.0 if k >= 0 else 0.0

    def solid_top(self, x: float, y: float) -> float:
        k = self.fidx(x, y)
        if k < 0:
            return 1e9
        t = self.top[k]
        return -1e9 if t == NONE else t / 100.0

    def canopy(self, x: float, y: float) -> int:
        k = self.fidx(x, y)
        return self.can[k] if k >= 0 else 0

    def los(self, a, b, step: float = 1.5) -> bool:
        """Line of sight between two 3D points against buildings (trees ignored)."""
        dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        d = math.hypot(dx, dy)
        n = max(1, int(d / step))
        for s in range(1, n):
            f = s / n
            x, y, z = a[0] + dx * f, a[1] + dy * f, a[2] + dz * f
            if self.solid_top(x, y) > z:
                return False
        return True

    # ---- nav grid ----
    def nidx(self, x: float, y: float):
        I = int((x - self.minx) / self.ncell)
        J = int((y - self.miny) / self.ncell)
        return I, J

    def ncenter(self, I: int, J: int):
        return (self.minx + (I + 0.5) * self.ncell, self.miny + (J + 0.5) * self.ncell)

    def nfree(self, I: int, J: int) -> bool:
        return 0 <= I < self.NX and 0 <= J < self.NY and self.walk[J * self.NX + I] == 1

    def nearest_free(self, I: int, J: int, rmax: int = 12):
        if self.nfree(I, J):
            return I, J
        for r in range(1, rmax):
            for dj in range(-r, r + 1):
                for di in (-r, r) if abs(dj) != r else range(-r, r + 1):
                    if self.nfree(I + di, J + dj):
                        return I + di, J + dj
        return None

    def line_free(self, a, b) -> bool:
        """Straight walk from a to b stays on walkable nav cells (with a little width)."""
        dx, dy = b[0] - a[0], b[1] - a[1]
        d = math.hypot(dx, dy)
        if d < 1e-6:
            return True
        n = max(1, int(d / 2.0))
        px, py = -dy / d * 2.0, dx / d * 2.0
        for s in range(n + 1):
            f = s / n
            x, y = a[0] + dx * f, a[1] + dy * f
            for ox, oy in ((0, 0), (px, py), (-px, -py)):
                I, J = self.nidx(x + ox, y + oy)
                if not self.nfree(I, J):
                    return False
        return True


class Path:
    """Resumable A* on the nav grid. Call step(budget) each tick until done."""

    def __init__(self, w: World, start, goal):
        self.w = w
        s = w.nearest_free(*w.nidx(start[0], start[1]))
        g = w.nearest_free(*w.nidx(goal[0], goal[1]))
        self.goal_xy = goal
        self.done = False
        self.points = None
        if s is None or g is None:
            self.done = True
            self.points = [goal]
            return
        self.s, self.g = s, g
        NX = w.NX
        self.si, self.gi = s[1] * NX + s[0], g[1] * NX + g[0]
        self.open = [(0.0, 0.0, self.si)]
        self.gs = {self.si: 0.0}
        self.came = {}
        self.closed = set()
        self.expanded = 0

    def h(self, k: int) -> float:
        NX = self.w.NX
        dx = abs(k % NX - self.g[0]); dy = abs(k // NX - self.g[1])
        return (dx + dy + (SQ2 - 2) * min(dx, dy)) * 1.0

    def step(self, budget: int = 1500) -> bool:
        if self.done:
            return True
        w = self.w
        NX, NY, walk, wn = w.NX, w.NY, w.walk, w.wallnear
        op, gs, came, closed = self.open, self.gs, self.came, self.closed
        gi = self.gi
        nb = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0), (1, 1, SQ2), (1, -1, SQ2), (-1, 1, SQ2), (-1, -1, SQ2))
        n = 0
        while op and n < budget:
            _, gk, k = heapq.heappop(op)
            if k in closed:
                continue
            if k == gi:
                self._finish(k)
                return True
            closed.add(k)
            n += 1
            I, J = k % NX, k // NX
            for di, dj, c in nb:
                I2, J2 = I + di, J + dj
                if I2 < 0 or J2 < 0 or I2 >= NX or J2 >= NY:
                    continue
                k2 = J2 * NX + I2
                if not walk[k2] or k2 in closed:
                    continue
                if di and dj and (not walk[J * NX + I2] or not walk[J2 * NX + I]):
                    continue  # no corner cutting
                g2 = gk + c * (1.0 + 0.15 * wn[k2])
                if g2 < gs.get(k2, 1e18):
                    gs[k2] = g2
                    came[k2] = k
                    heapq.heappush(op, (g2 + self.h(k2), g2, k2))
        self.expanded += n
        if not op:
            self.done = True
            self.points = [self.goal_xy]
            return True
        return False

    def _finish(self, k: int) -> None:
        pts = []
        while k in self.came:
            pts.append(self.w.ncenter(k % self.w.NX, k // self.w.NX))
            k = self.came[k]
        pts.reverse()
        pts.append(self.goal_xy)
        self.points = pts
        self.done = True
