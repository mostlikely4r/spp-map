import math
import re
import struct

GRID = 17066.666
TILE = 128.0
ADT = 533.33333
FILLER_REPEATS = 3
MAX_ADT_TILES = 256
MAX_PX = 4096
TILE_NAME = re.compile(r"(.*)_(\d{3})_(\d\d)_(\d\d)\.blp$", re.I)
ADT_NAME = re.compile(r"^([^\\]+)\\map(\d+)_(\d+)\.blp$", re.I)


def chunks(data):
    i = 0
    while i + 8 <= len(data):
        tag = data[i:i + 4][::-1].decode("latin-1")
        n = struct.unpack_from("<I", data, i + 4)[0]
        yield tag, data[i + 8:i + 8 + n]
        i += 8 + n


def tile_index(archives):
    raw = archives.read("textures\\Minimap\\md5translate.trs")
    wmo, terrain = {}, {}
    if not raw:
        return {"wmo": wmo, "terrain": terrain}
    for line in raw.decode("utf-8", "replace").splitlines():
        if "\t" not in line:
            continue
        name, blp = (v.strip() for v in line.split("\t", 1))
        m = TILE_NAME.match(name)
        if m and name.lower().startswith("wmo\\"):
            wmo.setdefault(m.group(1).lower(), {})[(int(m.group(2)), int(m.group(3)), int(m.group(4)))] = blp
            continue
        m = ADT_NAME.match(name)
        if m:
            terrain.setdefault(m.group(1).lower(), []).append((int(m.group(2)), int(m.group(3)), blp))
    for key, tiles in terrain.items():
        repeats = {}
        for _, _, blp in tiles:
            repeats[blp] = repeats.get(blp, 0) + 1
        terrain[key] = [t for t in tiles if repeats[t[2]] < FILLER_REPEATS]
    return {"wmo": wmo, "terrain": terrain}


def has_detail(archives, blp):
    from PIL import ImageChops, ImageFilter, ImageStat
    img = archives.image("textures\\Minimap\\" + blp)
    if img is None:
        return False
    img = img.convert("L").resize((128, 128))
    edge = ImageStat.Stat(ImageChops.difference(img, img.filter(ImageFilter.BoxBlur(2)))).mean[0]
    bright = ImageStat.Stat(img).mean[0]
    return edge >= 1.2 or (edge >= 0.3 and bright < 200)


def overlaps(a, b, margin=0.0):
    return (a["bottom"] <= b["top"] + margin and a["top"] >= b["bottom"] - margin
            and a["right"] <= b["left"] + margin and a["left"] >= b["right"] - margin)


def terrain_rect(tx, ty):
    left = GRID - tx * ADT
    top = GRID - ty * ADT
    return {"top": top, "bottom": top - ADT, "left": left, "right": left - ADT}


def read_modf(chunk_map, wdt=False):
    names = chunk_map.get("MWMO", b"")
    offsets = struct.unpack_from(f"<{len(chunk_map.get('MWID', b'')) // 4}I", chunk_map.get("MWID", b""))
    out = []
    data = chunk_map.get("MODF", b"")
    for i in range(len(data) // 64):
        name_id, uid, px, py, pz, ra, rb, rc = struct.unpack_from("<II3f3f", data, i * 64)
        start = offsets[name_id] if name_id < len(offsets) else (0 if name_id == 0 else None)
        if start is None:
            continue
        name = names[start:names.find(b"\0", start)].decode("latin-1")
        out.append({"uid": uid, "wmo": name, "pos": (px, py, pz), "yaw": rb, "wdt": wdt})
    return out


def placements(archives, directory):
    wdt = archives.read(f"World\\Maps\\{directory}\\{directory}.wdt")
    if not wdt:
        return []
    parts = dict(chunks(wdt))
    found = read_modf(parts, True) if parts.get("MWMO", b"").strip(b"\0") else []
    main = parts.get("MAIN", b"")
    step = 8 if len(main) >= 4096 * 8 else 4
    tiles = [(k % 64, k // 64) for k in range(len(main) // step) if struct.unpack_from("<I", main, k * step)[0] & 1]
    if len(tiles) <= MAX_ADT_TILES:
        for x, y in tiles:
            adt = archives.read(f"World\\Maps\\{directory}\\{directory}_{x}_{y}.adt")
            if adt:
                found += read_modf(dict(chunks(adt)))
    seen, out = set(), []
    for p in found:
        if p["uid"] in seen:
            continue
        seen.add(p["uid"])
        out.append(p)
    return out


def groups(archives, wmo):
    root = archives.read(wmo)
    if not root:
        return []
    for tag, data in chunks(root):
        if tag == "MOGI":
            return [struct.unpack_from("<I6f", data, i * 32)[1:] for i in range(len(data) // 32)]
    return []


class Placement:
    def __init__(self, pos, yaw, wdt=False):
        base = 0.0 if wdt else GRID
        self.wx = base - pos[2]
        self.wy = base - pos[0]
        self.wz = pos[1]
        r = math.radians(yaw)
        self.c, self.s = math.cos(r), math.sin(r)

    def to_world(self, lx, ly):
        rx, ry = -lx, -ly
        return self.wx + rx * self.c - ry * self.s, self.wy + rx * self.s + ry * self.c

    def to_local(self, x, y):
        dx, dy = x - self.wx, y - self.wy
        return -(dx * self.c + dy * self.s), -(-dx * self.s + dy * self.c)


def layout(archives, directory, index):
    terrain = [t for t in index["terrain"].get(directory.lower(), []) if has_detail(archives, t[2])]
    index = index["wmo"]
    out = []
    for p in placements(archives, directory):
        key = p["wmo"].lower().replace("/", "\\")
        key = key[6:] if key.startswith("world\\") else key
        key = key[:-4] if key.endswith(".wmo") else key
        tiles = index.get(key)
        if not tiles:
            continue
        bbs = groups(archives, p["wmo"])
        rects = {}
        for (g, tx, ty), blp in tiles.items():
            if g >= len(bbs):
                continue
            bb = bbs[g]
            rects.setdefault(g, {"z": bb[2], "tiles": []})["tiles"].append(
                (bb[0] + TILE * tx, bb[1] + TILE * ty, blp))
        if rects:
            out.append({"wmo": p["wmo"], "pos": p["pos"], "yaw": p["yaw"], "wdt": p["wdt"], "groups": rects})
    return {"wmo": out, "terrain": terrain if out or terrain else []}


def world_bounds(lay):
    xs, ys = [], []
    for tx, ty, _ in lay["terrain"]:
        r = terrain_rect(tx, ty)
        xs += [r["top"], r["bottom"]]
        ys += [r["left"], r["right"]]
    for it in lay["wmo"]:
        pl = Placement(it["pos"], it["yaw"], it["wdt"])
        for g in it["groups"].values():
            for lx, ly, _ in g["tiles"]:
                for cx, cy in ((lx, ly), (lx + TILE, ly), (lx, ly + TILE), (lx + TILE, ly + TILE)):
                    x, y = pl.to_world(cx, cy)
                    xs.append(x)
                    ys.append(y)
    if not xs:
        return None
    return {"top": max(xs), "bottom": min(xs), "left": max(ys), "right": min(ys)}


def render(archives, lay, bounds, px_per_yd=2.0):
    from PIL import Image
    w_yd = bounds["left"] - bounds["right"]
    h_yd = bounds["top"] - bounds["bottom"]
    scale = min(px_per_yd, MAX_PX / max(w_yd, h_yd))
    out = Image.new("RGBA", (max(1, round(w_yd * scale)), max(1, round(h_yd * scale))), (0, 0, 0, 0))
    tile_px = 256
    for tx, ty, blp in lay["terrain"]:
        img = archives.image("textures\\Minimap\\" + blp)
        if img is None:
            continue
        r = terrain_rect(tx, ty)
        size = max(1, round(ADT * scale))
        img = img.convert("RGBA").resize((size, size), Image.BILINEAR)
        out.alpha_composite(img, (round((bounds["left"] - r["left"]) * scale), round((bounds["top"] - r["top"]) * scale)))
    for it in lay["wmo"]:
        pl = Placement(it["pos"], it["yaw"], it["wdt"])
        all_tiles = [t for g in it["groups"].values() for t in g["tiles"]]
        x0 = min(t[0] for t in all_tiles)
        y0 = min(t[1] for t in all_tiles)
        x1 = max(t[0] for t in all_tiles) + TILE
        y1 = max(t[1] for t in all_tiles) + TILE
        k = tile_px / TILE
        local = Image.new("RGBA", (round((x1 - x0) * k), round((y1 - y0) * k)), (0, 0, 0, 0))
        for g in sorted(it["groups"].values(), key=lambda g: g["z"]):
            for lx, ly, blp in g["tiles"]:
                img = archives.image("textures\\Minimap\\" + blp)
                if img is None:
                    continue
                img = img.convert("RGBA")
                if img.size != (tile_px, tile_px):
                    img = img.resize((tile_px, tile_px))
                local.paste(img, (round((lx - x0) * k), round((y1 - (ly + TILE)) * k)), img)
        ox, oy = pl.to_local(bounds["top"], bounds["left"])
        ax, ay = pl.to_local(bounds["top"], bounds["left"] - 1.0 / scale)
        bx, by = pl.to_local(bounds["top"] - 1.0 / scale, bounds["left"])
        coeffs = ((ax - ox) * k, (bx - ox) * k, (ox - x0) * k,
                  -(ay - oy) * k, -(by - oy) * k, (y1 - oy) * k)
        warped = local.transform(out.size, Image.AFFINE, coeffs, resample=Image.BILINEAR)
        out.alpha_composite(warped)
    return out
