import json
import math
import os
import threading

from .clientdata import ClientArchives, as_float, dbc_string, read_dbc

MAP_W, MAP_H = 1002, 668
CONTINENT_SIZE = 17066.66656


class Basemaps:
    def __init__(self, core, data_dir):
        self.core = core
        self.out_dir = os.path.join(data_dir, "basemaps", core.name)
        os.makedirs(self.out_dir, exist_ok=True)
        self.archives = ClientArchives(core.client, core.locale)
        self.lock = threading.Lock()
        self.areas = self.load_areas()
        self.overlays = self.load_overlays()
        self.floors = self.load_floors()
        self.wmo_floor = self.load_wmo_floors()

    def dbc_path(self, name):
        return os.path.join(self.core.dbc, name + ".dbc")

    def load_areas(self):
        try:
            recs, strings = read_dbc(self.dbc_path("WorldMapArea"))
        except OSError:
            return []
        areas = []
        self.area_names_by_map = {}
        for r in recs:
            left, right, top, bottom = (as_float(v) for v in r[4:8])
            self.area_names_by_map.setdefault(r[1], dbc_string(strings, r[3]))
            if left == right or top == bottom:
                self.zero_areas = getattr(self, "zero_areas", {})
                self.zero_areas.setdefault(r[1], []).append(dbc_string(strings, r[3]))
                continue
            areas.append({
                "kind": "zone", "id": r[0], "map": r[1], "area": r[2], "name": dbc_string(strings, r[3]),
                "left": left, "right": right, "top": top, "bottom": bottom,
            })
        return areas

    def load_overlays(self):
        try:
            recs, strings = read_dbc(self.dbc_path("WorldMapOverlay"))
        except OSError:
            return {}
        out = {}
        for r in recs:
            name = dbc_string(strings, r[8])
            if not name:
                continue
            out.setdefault(r[1], []).append({"name": name, "w": r[9], "h": r[10], "x": r[11], "y": r[12]})
        return out

    def load_floors(self):
        path = self.dbc_path("DungeonMap")
        if not os.path.exists(path):
            return []
        recs, _ = read_dbc(path)
        names = getattr(self, "area_names_by_map", {})
        floors = []
        for r in recs:
            min_y, max_y, min_x, max_x = (as_float(v) for v in r[3:7])
            name = names.get(r[1])
            if any(a["map"] == r[1] and not a["area"] for a in self.areas):
                cx, cy = (min_x + max_x) / 2, (min_y + max_y) / 2
                inside = [a for a in self.areas if a["map"] == r[1] and a["area"] and self.contains(a, cx, cy)]
                options = getattr(self, "zero_areas", {}).get(r[1], []) + [a["name"] for a in sorted(inside, key=self.area_size)]
                name = next((n for n in options if self.archives.available() and self.archives.read(
                    f"Interface\\WorldMap\\{n}\\{n}{r[2]}_1.blp")), None)
            if not name or min_y == max_y:
                continue
            floors.append({
                "kind": "floor", "id": r[0], "map": r[1], "floor": r[2], "name": name,
                "left": max_y, "right": min_y, "top": max_x, "bottom": min_x,
            })
        return floors

    def load_wmo_floors(self):
        chunk_path = self.dbc_path("DungeonMapChunk")
        area_path = self.dbc_path("WMOAreaTable")
        if not (os.path.exists(chunk_path) and os.path.exists(area_path)):
            return {}
        chunks, _ = read_dbc(chunk_path)
        areas, _ = read_dbc(area_path)
        floor_by_id = {f["id"]: f for f in self.floors}
        area_to_floor = {c[2]: floor_by_id[c[3]] for c in chunks if c[3] in floor_by_id}
        out = {}
        for a in areas:
            floor = area_to_floor.get(a[0])
            if floor:
                out[(a[1], a[3])] = floor
        return out

    def floor_for(self, map_id, wmo_ids):
        votes = {}
        for root, group in wmo_ids:
            floor = self.wmo_floor.get((root, group))
            if floor and floor["map"] == map_id:
                votes[floor["id"]] = votes.get(floor["id"], 0) + 1
        if not votes:
            return None
        best = max(votes, key=votes.get)
        return next(f for f in self.floors if f["id"] == best)

    def candidates(self, map_id):
        zones = [a for a in self.areas if a["map"] == map_id]
        floors = [f for f in self.floors if f["map"] == map_id]
        return floors + zones

    @staticmethod
    def contains(entry, x, y, margin=0.0):
        return (entry["bottom"] - margin <= x <= entry["top"] + margin
                and entry["right"] - margin <= y <= entry["left"] + margin)

    @staticmethod
    def area_size(entry):
        return abs(entry["top"] - entry["bottom"]) * abs(entry["left"] - entry["right"])

    def choose(self, map_id, points):
        if not points:
            return None
        options = []
        for entry in self.candidates(map_id):
            inside = sum(1 for x, y in points if self.contains(entry, x, y))
            if inside:
                options.append((inside / len(points), self.area_size(entry), entry))
        if not options:
            return None
        full = [o for o in options if o[0] >= 0.98]
        if full:
            return min(full, key=lambda o: o[1])[2]
        return max(options, key=lambda o: (o[0], -o[1]))[2]

    def bounds_of(self, entry):
        return {"left": entry["left"], "right": entry["right"], "top": entry["top"], "bottom": entry["bottom"]}

    def key(self, entry):
        if entry["kind"] == "floor":
            return f"floor_{entry['map']}_{entry['floor']}"
        return f"zone_{entry['id']}"

    def ensure(self, entry):
        key = self.key(entry)
        png = os.path.join(self.out_dir, key + ".webp")
        meta = os.path.join(self.out_dir, key + ".json")
        if os.path.exists(png) and os.path.exists(meta):
            with open(meta, encoding="utf-8") as fh:
                return json.load(fh)
        with self.lock:
            if os.path.exists(png) and os.path.exists(meta):
                with open(meta, encoding="utf-8") as fh:
                    return json.load(fh)
            image = self.render(entry)
            if image is None:
                return None
            image.save(png, "WEBP", quality=82, method=5)
            info = {"key": key, "image": key + ".webp", "kind": entry["kind"], "name": entry["name"],
                    "map": entry["map"], "floor": entry.get("floor"), "bounds": self.bounds_of(entry),
                    "size": list(image.size)}
            with open(meta, "w", encoding="utf-8") as fh:
                json.dump(info, fh)
            return info

    def render(self, entry):
        from PIL import Image
        if not self.archives.available():
            return None
        canvas = Image.new("RGB", (1024, 768), (40, 34, 26))
        name = entry["name"]
        found = 0
        for i in range(12):
            if entry["kind"] == "floor":
                tile = self.archives.image(f"Interface\\WorldMap\\{name}\\{name}{entry['floor']}_{i + 1}.blp")
            else:
                tile = self.archives.image(f"Interface\\WorldMap\\{name}\\{name}{i + 1}.blp")
            if tile is None:
                continue
            found += 1
            canvas.paste(tile.convert("RGB"), ((i % 4) * 256, (i // 4) * 256))
        if not found:
            return None
        if entry["kind"] == "zone":
            for ov in self.overlays.get(entry["id"], []):
                cols = math.ceil(ov["w"] / 256)
                rows = math.ceil(ov["h"] / 256)
                k = 0
                for ty in range(rows):
                    for tx in range(cols):
                        k += 1
                        tile = self.archives.image(f"Interface\\WorldMap\\{name}\\{ov['name']}{k}.blp")
                        if tile is None:
                            continue
                        tile = tile.convert("RGBA")
                        canvas.paste(tile, (ov["x"] + tx * 256, ov["y"] + ty * 256), tile)
        return canvas.crop((0, 0, MAP_W, MAP_H))

    def describe(self, entry):
        return {"key": self.key(entry), "kind": entry["kind"], "name": entry["name"], "area": entry.get("area"),
                "floor": entry.get("floor"), "bounds": self.bounds_of(entry)}

    def entries_for(self, map_id):
        return [self.describe(e) for e in self.floors + self.areas if e["map"] == map_id]

    def entry_by_key(self, key):
        for e in self.floors + self.areas:
            if self.key(e) == key:
                return e
        return None

    def image_for_key(self, key):
        found = self.path(key + ".webp")
        if found:
            return found
        entry = self.entry_by_key(key)
        info = self.ensure(entry) if entry else None
        return self.path(info["image"]) if info else None

    def area_name(self, area_id):
        if getattr(self, "_area_names", None) is None:
            names = {}
            try:
                recs, strings = read_dbc(self.dbc_path("AreaTable"))
                for r in recs:
                    if len(r) > 11:
                        names[r[0]] = dbc_string(strings, r[11])
            except OSError:
                pass
            self._area_names = names
        return self._area_names.get(area_id, "")

    def map_names(self):
        if getattr(self, "_map_names", None) is None:
            self._map_names = load_map_names(self.dbc_path("Map"))
        return self._map_names

    def path(self, key):
        safe = os.path.basename(key)
        path = os.path.join(self.out_dir, safe)
        return path if os.path.exists(path) else None

    def build_all(self, progress=None):
        done = 0
        for entry in self.floors + self.areas:
            info = self.ensure(entry)
            done += 1 if info else 0
            if progress:
                progress(entry, info)
        return done


def load_map_names(path):
    try:
        recs, strings = read_dbc(path)
    except OSError:
        return {}
    best, best_score = None, -1
    for idx in range(2, 7):
        score = 0
        for r in recs:
            if idx < len(r):
                s = dbc_string(strings, r[idx]) if 0 < r[idx] < len(strings) and strings[r[idx] - 1:r[idx]] == b"\0" else ""
                if s and s[0].isupper() and " " in s:
                    score += 1
        if score > best_score:
            best, best_score = idx, score
    out = {}
    for r in recs:
        v = r[best] if best is not None and best < len(r) else 0
        name = dbc_string(strings, v) if 0 < v < len(strings) and strings[v - 1:v] == b"\0" else ""
        out[r[0]] = name or dbc_string(strings, r[1]) or f"map {r[0]}"
    return out
