import csv
import json
import os
import re
import threading
from array import array
from datetime import datetime, timedelta

from . import telemetry
from .clientdata import dbc_string, read_dbc

POINT = re.compile(r"POINT\(([-\d.e]+) ([-\d.e]+)\)", re.I)
LINE = re.compile(r"LINESTRING\(([-\d.e]+) ([-\d.e]+),\s*([-\d.e]+) ([-\d.e]+)\)", re.I)
LINK = re.compile(r"\|c[0-9a-fA-F]{8}\|H[^|]*\|h(\[[^\]]*\])\|h\|r")
CELL = 150.0


def plain(text):
    return LINK.sub(lambda m: m.group(1), text or "")


def map_offsets(logs):
    offsets = telemetry.load_offsets(logs)
    try:
        with open(os.path.join(logs, "livemap_manifest.json"), encoding="utf-8") as fh:
            for row in json.load(fh).get("mapOffsets", []):
                offsets[int(row["mapId"])] = (float(row["offX"]), float(row["offY"]))
    except (OSError, ValueError, KeyError):
        pass
    return offsets


def file_sig(path):
    try:
        st = os.stat(path)
        return st.st_mtime, st.st_size
    except OSError:
        return None


class Telecache:
    def __init__(self, core, area_maps):
        self.core = core
        self.path = os.path.join(core.logs, "telecache.csv")
        self.area_maps = area_maps
        self.sig = None
        self.by_map = {}
        self.lock = threading.Lock()

    def load(self):
        sig = file_sig(self.path)
        if sig == self.sig:
            return
        with self.lock:
            if sig == self.sig:
                return
            by_map = {}
            offsets = map_offsets(self.core.logs)
            if sig:
                with open(self.path, newline="", encoding="utf-8", errors="replace") as fh:
                    for row in csv.reader(fh):
                        if len(row) < 5:
                            continue
                        m = POINT.match(row[1])
                        map_id = self.area_maps(row[4])
                        if not m or map_id is None or map_id not in offsets:
                            continue
                        x, y = telemetry.display_to_world(float(m.group(1)), float(m.group(2)), offsets[map_id])
                        cols = by_map.setdefault(map_id, (array("f"), array("f"), array("B"), array("B")))
                        cols[0].append(x)
                        cols[1].append(y)
                        cols[2].append(min(int(row[0] or 0), 255))
                        cols[3].append(1 if row[2] == "RPG" else 0)
            self.by_map, self.sig = by_map, sig

    def layer(self, map_id, bounds, kinds, limit=3000, bins=48):
        self.load()
        cols = self.by_map.get(map_id)
        if not cols:
            return {"binned": False, "total": 0, "features": []}
        xs, ys, levels, rpg = cols
        want = {"level": 0, "rpg": 1}
        allowed = {want[k] for k in kinds if k in want} or {0, 1}
        hits = [i for i in range(len(xs)) if rpg[i] in allowed
                and bounds["bottom"] <= xs[i] <= bounds["top"] and bounds["right"] <= ys[i] <= bounds["left"]]
        if len(hits) > limit:
            cell = max(bounds["top"] - bounds["bottom"], bounds["left"] - bounds["right"]) / bins
            cells = {}
            for i in hits:
                k = (int(xs[i] // cell), int(ys[i] // cell))
                c = cells.setdefault(k, [0, 0])
                c[0] += 1
                c[1] += levels[i]
            return {"binned": True, "total": len(hits), "cell": cell,
                    "bins": [[(i + 0.5) * cell, (j + 0.5) * cell, n, round(s / n)] for (i, j), (n, s) in cells.items()]}
        return {"binned": False, "total": len(hits),
                "features": [[round(xs[i], 1), round(ys[i], 1), levels[i], "rpg" if rpg[i] else "level"] for i in hits]}


def hull(points):
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


class Zones:
    def __init__(self, core):
        self.core = core
        self.path = os.path.join(core.logs, "zones.csv")
        self.sig = None
        self.by_map = {}
        self.lock = threading.Lock()

    def load(self):
        sig = file_sig(self.path)
        if sig == self.sig:
            return
        with self.lock:
            if sig == self.sig:
                return
            offsets = map_offsets(self.core.logs)
            found = {}
            if sig:
                with open(self.path, newline="", encoding="utf-8", errors="replace") as fh:
                    for r in csv.reader(fh):
                        if len(r) < 6 or not r[1].lstrip("-").isdigit():
                            continue
                        map_id = int(r[1])
                        off = offsets.get(map_id)
                        if off is None:
                            continue
                        coords = re.findall(r"([-\d.e]+) ([-\d.e]+)", r[4])
                        pts = [telemetry.display_to_world(float(a), float(b), off) for a, b in coords]
                        z = found.setdefault((map_id, r[2]), {"name": r[2], "zone": r[3], "level": None, "center": None, "points": []})
                        try:
                            z["level"] = int(float(r[5]))
                        except ValueError:
                            pass
                        if r[0] == "center" and pts:
                            z["center"] = [round(pts[0][0], 1), round(pts[0][1], 1)]
                        else:
                            z["points"] += pts
            by_map = {}
            for (map_id, _), z in found.items():
                shape = hull(z.pop("points"))
                if not shape and not z["center"]:
                    continue
                z["hull"] = [[round(x, 1), round(y, 1)] for x, y in shape]
                if not z["center"] and shape:
                    z["center"] = [round(sum(p[0] for p in shape) / len(shape), 1), round(sum(p[1] for p in shape) / len(shape), 1)]
                by_map.setdefault(map_id, []).append(z)
            self.by_map, self.sig = by_map, sig

    def for_map(self, map_id):
        self.load()
        return self.by_map.get(map_id, [])


class Intents:
    def __init__(self, core, map_of_display):
        self.path = os.path.join(core.logs, "travel_map.csv")
        self.map_of_display = map_of_display
        self.sig = None
        self.rows = []
        self.lock = threading.Lock()

    def load(self):
        sig = file_sig(self.path)
        if sig == self.sig:
            return
        with self.lock:
            if sig == self.sig:
                return
            rows = []
            if sig:
                with open(self.path, newline="", encoding="utf-8", errors="replace") as fh:
                    for r in csv.reader(fh):
                        if len(r) < 11:
                            continue
                        m = LINE.match(r[5])
                        if not m:
                            continue
                        a = (float(m.group(1)), float(m.group(2)))
                        b = (float(m.group(3)), float(m.group(4)))
                        src = self.map_of_display(*a)
                        if src is None:
                            continue
                        dst = self.map_of_display(*b, prefer=src[0])
                        rows.append({"t": r[0][:19], "bot": r[1], "level": r[4], "map": src[0],
                                     "from": src[1], "to": dst[1] if dst and dst[0] == src[0] else None,
                                     "to_map": dst[0] if dst else None, "dist": r[6],
                                     "purpose": plain(r[8]), "detail": plain(r[9]), "type": r[10]})
            self.rows, self.sig = rows, sig

    def recent(self, map_id, seconds=900):
        self.load()
        if not self.rows:
            return []
        newest = max(r["t"] for r in self.rows)
        t0 = (datetime.strptime(newest, telemetry.TS_FMT) - timedelta(seconds=seconds)).strftime(telemetry.TS_FMT)
        latest = {}
        for r in self.rows:
            if r["map"] == map_id and r["t"] >= t0:
                latest[r["bot"]] = r
        return list(latest.values())


class StaticLayers:
    def __init__(self, ctx):
        self.ctx = ctx
        self.core = ctx.core
        self._area_maps = None
        self._regions = None
        self._regions_sig = None
        self.telecache = Telecache(self.core, self.area_map)
        self.intents = Intents(self.core, self.map_of_display)
        self.zones = Zones(self.core)

    def area_map(self, name):
        if self._area_maps is None:
            out = {}
            try:
                recs, strings = read_dbc(os.path.join(self.core.dbc, "AreaTable.dbc"))
                for r in recs:
                    if len(r) > 11:
                        out.setdefault(dbc_string(strings, r[11]).lower(), r[1])
            except OSError:
                pass
            self._area_maps = out
        return self._area_maps.get((name or "").lower())

    def regions(self):
        sig = file_sig(os.path.join(self.core.logs, "livemap_manifest.json"))
        if self._regions is not None and sig == self._regions_sig:
            return self._regions
        offsets = map_offsets(self.core.logs)
        cells = {}
        if self.ctx.world.available():
            try:
                cells = self.ctx.world.occupancy(CELL)
            except Exception:
                cells = {}
        out = [(len(cells.get(m, ())), m, off, cells.get(m, set())) for m, off in offsets.items() if cells.get(m)]
        out.sort(reverse=True)
        self._regions, self._regions_sig = out, sig
        return out

    def map_of_display(self, dx, dy, prefer=None):
        regions = self.regions()
        if prefer is not None:
            regions = sorted(regions, key=lambda r: r[1] != prefer)
        for _, map_id, off, cells in regions:
            x, y = telemetry.display_to_world(dx, dy, off)
            cx, cy = int(x // CELL), int(y // CELL)
            if any((cx + i, cy + j) in cells for i in (-1, 0, 1) for j in (-1, 0, 1)):
                return map_id, [round(x, 1), round(y, 1)]
        return None
