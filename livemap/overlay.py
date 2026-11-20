import math

from . import wmomap
from .clientdata import as_float, read_dbc

CONTINENT_OFFSETS = {0: (0.0, 0.0), 1: (-3680.0, 13670.0), 530: (15000.0, -20000.0), 571: (10000.0, 5000.0)}
KINDS = {1: "dungeon", 2: "raid", 3: "battleground", 4: "arena"}

ANCHORS = {
    30: {"doors": [(0, 97.0, -176.0), (0, 545.0, -1080.0)], "note": "AV entrances, Alterac Mountains"},
    489: {"doors": [(1, 1422.0, -1855.0), (1, 1034.0, -2088.0)], "note": "WSG: Silverwing Grove / Mor'shan"},
    529: {"doors": [(0, -1189.0, -2532.0), (0, -811.0, -3506.0)], "note": "AB: Refuge Pointe / Hammerfall"},
    566: {"doors": [(530, 3044.0, 3611.0)], "note": "EotS, Netherstorm (approximate)"},
    607: {"doors": [(571, 5333.0, 2840.0)], "note": "SotA, no world entrance: Wintergrasp"},
    628: {"doors": [(571, 5333.0, 2840.0)], "note": "IoC, no world entrance: Wintergrasp"},
    559: {"doors": [(530, -2160.0, 6666.0)], "note": "Nagrand Arena: Ring of Trials"},
    562: {"doors": [(530, 2879.0, 5980.0)], "note": "Blade's Edge Arena: Circle of Blood"},
    572: {"doors": [(0, 1902.0, 273.0)], "note": "Ruins of Lordaeron"},
    617: {"doors": [(571, 5810.0, 671.0)], "note": "Dalaran Sewers: Dalaran"},
    618: {"doors": [(1, 1806.0, -4382.0)], "note": "Ring of Valor: Orgrimmar (approximate)"},
}

STEP = 250.0
ANGLES = 24
MAX_RINGS = 80
GAP = 60.0
AGREE = 50.0


def box_of(extent, dx, dy):
    return {"top": extent["top"] + dx, "bottom": extent["bottom"] + dx, "left": extent["left"] + dy, "right": extent["right"] + dy}


def hits(a, b, margin=GAP):
    return (a["bottom"] < b["top"] + margin and a["top"] > b["bottom"] - margin
            and a["right"] < b["left"] + margin and a["left"] > b["right"] - margin)


class LandMask:
    def __init__(self):
        self.tiles = {}

    def add(self, map_id, tiles):
        self.tiles[map_id] = {(tx, ty) for tx, ty, _ in tiles}

    def hits(self, box):
        for map_id, tiles in self.tiles.items():
            ox, oy = CONTINENT_OFFSETS[map_id]
            x0, x1 = box["bottom"] - ox - GAP, box["top"] - ox + GAP
            y0, y1 = box["right"] - oy - GAP, box["left"] - oy + GAP
            tx0 = math.floor((wmomap.GRID - y1) / wmomap.ADT)
            tx1 = math.floor((wmomap.GRID - y0) / wmomap.ADT)
            ty0 = math.floor((wmomap.GRID - x1) / wmomap.ADT)
            ty1 = math.floor((wmomap.GRID - x0) / wmomap.ADT)
            if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) > len(tiles):
                if any(tx0 <= tx <= tx1 and ty0 <= ty <= ty1 for tx, ty in tiles):
                    return True
                continue
            for tx in range(tx0, tx1 + 1):
                for ty in range(ty0, ty1 + 1):
                    if (tx, ty) in tiles:
                        return True
        return False


def doors_by_map(world, dbc_path):
    recs, _ = read_dbc(dbc_path)
    triggers = {r[0]: (r[1], as_float(r[2]), as_float(r[3])) for r in recs}
    out = {}
    for tid, tmap, tx, ty in world.rows("SELECT id, target_map, target_position_x, target_position_y FROM areatrigger_teleport"):
        src = triggers.get(tid)
        if not src or src[0] == tmap:
            continue
        out.setdefault(int(tmap), []).append({"id": int(tid), "map": src[0], "x": src[1], "y": src[2],
                                              "lx": float(tx), "ly": float(ty)})
    return out


def map_kinds(dbc_path):
    recs, _ = read_dbc(dbc_path)
    return {r[0]: KINDS.get(r[2], "other") for r in recs}


def choose_door(doors, placed_maps):
    cont = sorted((d for d in doors if d["map"] in CONTINENT_OFFSETS), key=lambda d: d["id"])
    if cont:
        shifts = [(d["x"] - d["lx"], d["y"] - d["ly"]) for d in cont]
        mx = sum(s[0] for s in shifts) / len(shifts)
        my = sum(s[1] for s in shifts) / len(shifts)
        if all(math.hypot(s[0] - mx, s[1] - my) <= AGREE for s in shifts):
            return cont, (mx, my), None
        return cont, shifts[0], None
    chained = sorted((d for d in doors if d["map"] in placed_maps), key=lambda d: d["id"])
    if chained:
        d = chained[0]
        return [d], (d["x"] - d["lx"], d["y"] - d["ly"]), d["map"]
    return [], None, None


def search(ideal, door, centre, blocked):
    if not blocked(ideal):
        return ideal, 0.0, 0.0
    away = math.atan2(door[1] - centre[1], door[0] - centre[0])
    order = sorted(range(ANGLES), key=lambda i: abs(((i * 2 * math.pi / ANGLES) - away + math.pi) % (2 * math.pi) - math.pi))
    for ring in range(1, MAX_RINGS + 1):
        r = ring * STEP
        for i in order:
            a = i * 2 * math.pi / ANGLES
            dx, dy = r * math.cos(a), r * math.sin(a)
            box = box_of(ideal, dx, dy)
            if not blocked(box):
                return box, dx, dy
    return ideal, 0.0, 0.0


def build(ctx, mode, pick_base):
    bm = ctx.basemaps
    names = bm.map_names()
    kinds = map_kinds(bm.dbc_path("Map"))
    doors = doors_by_map(ctx.world, bm.dbc_path("AreaTrigger"))
    for map_id, anchor in ANCHORS.items():
        if map_id in kinds and map_id not in doors:
            doors[map_id] = [{"id": 0, "map": m, "x": x, "y": y, "lx": None, "ly": None} for m, x, y in anchor["doors"]]

    continents, land = [], LandMask()
    for map_id, off in CONTINENT_OFFSETS.items():
        if map_id not in bm.map_dirs():
            continue
        terrain = bm.terrain_entry(map_id)
        if not terrain:
            continue
        base = bm.describe(terrain)
        continents.append({"id": map_id, "name": names.get(map_id, f"map {map_id}"), "off": list(off), "base": base,
                           "extent": base["bounds"]})
        land.add(map_id, bm.terrain_layout(map_id)["items"]["terrain"])

    candidates = []
    for map_id, ds in doors.items():
        if map_id in CONTINENT_OFFSETS:
            continue
        info = ctx.map_info(map_id)
        base = instance_base(info["basemaps"], pick_base)
        extent = base["bounds"] if base else info["extent"]
        if extent is None and ctx.world.available():
            try:
                extent = ctx.world.map_extent(map_id)
            except Exception:
                extent = None
        if extent is None:
            continue
        candidates.append({"id": map_id, "name": info["name"], "kind": kinds.get(map_id, "other"), "doors": ds,
                           "base": base, "extent": extent})

    centre = {c["id"]: ((c["extent"]["top"] + c["extent"]["bottom"]) / 2 + c["off"][0],
                        (c["extent"]["left"] + c["extent"]["right"]) / 2 + c["off"][1]) for c in continents}
    placed, boxes, pending = {}, [], sorted(candidates, key=lambda c: -area(c["extent"]))
    for _ in range(4):
        rest = []
        for c in pending:
            if any(d["lx"] is None for d in c["doors"]):
                used = [d for d in c["doors"] if d["map"] in CONTINENT_OFFSETS]
                parent = None
                cx = (c["extent"]["top"] + c["extent"]["bottom"]) / 2
                cy = (c["extent"]["left"] + c["extent"]["right"]) / 2
                shift = (sum(d["x"] for d in used) / len(used) - cx, sum(d["y"] for d in used) / len(used) - cy) if used else None
            else:
                used, shift, parent = choose_door(c["doors"], placed)
            if shift is None:
                rest.append(c)
                continue
            root = parent if parent is not None else used[0]["map"]
            if parent is not None:
                p = placed[parent]
                door_pts = [(d["x"] + p["off"][0], d["y"] + p["off"][1]) for d in used]
                off = (shift[0] + p["off"][0], shift[1] + p["off"][1])
                root = p["root"]
            else:
                door_pts = [(d["x"] + CONTINENT_OFFSETS[d["map"]][0], d["y"] + CONTINENT_OFFSETS[d["map"]][1]) for d in used]
                co = CONTINENT_OFFSETS[root]
                off = (shift[0] + co[0], shift[1] + co[1])
            ideal = box_of(c["extent"], off[0], off[1])
            door = (sum(p[0] for p in door_pts) / len(door_pts), sum(p[1] for p in door_pts) / len(door_pts))
            if mode == "sea":
                blocked = lambda b: land.hits(b) or any(hits(b, o) for o in boxes)
            else:
                blocked = lambda b: any(hits(b, o) for o in boxes)
            box, dx, dy = search(ideal, door, centre.get(root, door), blocked)
            boxes.append(box)
            landings = [(d["lx"] + off[0] + dx, d["ly"] + off[1] + dy) if d["lx"] is not None else
                        ((box["top"] + box["bottom"]) / 2, (box["left"] + box["right"]) / 2) for d in used]
            placed[c["id"]] = {"id": c["id"], "name": c["name"], "kind": c["kind"], "parent": parent, "root": root,
                               "off": [off[0] + dx, off[1] + dy], "base": c["base"], "extent": c["extent"],
                               "moved": bool(dx or dy), "links": [[list(a), list(b)] for a, b in zip(door_pts, landings)]}
        if not rest or len(rest) == len(pending):
            pending = rest
            break
        pending = rest
    tray = [{"id": c["id"], "name": c["name"], "kind": c["kind"]} for c in pending]
    return {"mode": mode, "continents": continents, "instances": sorted(placed.values(), key=lambda p: p["id"]), "tray": tray}


def area(e):
    return abs(e["top"] - e["bottom"]) * abs(e["left"] - e["right"])


def instance_base(basemaps, pick_base):
    wmo = [b for b in basemaps if b["kind"] == "wmo"]
    if wmo:
        return max(wmo, key=lambda b: area(b["bounds"]))
    return pick_base(basemaps)
