import json
import os
import re
import shutil
import threading
import time
import urllib.parse
import urllib.request

from .server import STATIC, serve

LAYER_KINDS = ("creatures", "objects", "nodes", "telecache")
SKIP_STATIC = re.compile(r"\.(map|md)$")


def write_site(out, base=""):
    base = base.rstrip("/")
    dst = os.path.join(out, "static")
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(STATIC):
        if not SKIP_STATIC.search(name) and name != "index.html":
            shutil.copy2(os.path.join(STATIC, name), os.path.join(dst, name))
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as fh:
        html = fh.read()
    stamp = str(int(time.time()))
    html = re.sub(r'(src|href)="/static/([\w.]+)"', lambda m: f'{m.group(1)}="{base}/static/{m.group(2)}?v={stamp}"', html)
    for page in ("/", "/tests", "/help"):
        html = html.replace(f'href="{page}"', f'href="{base}{page}"')
    config = json.dumps({"mode": "static", "base": base})
    html = html.replace("<script ", f"<script>window.LM_CONFIG = {config};</script>\n<script ", 1)
    for name in ("index.html", "404.html"):
        with open(os.path.join(out, name), "w", encoding="utf-8") as fh:
            fh.write(html)
    with open(os.path.join(out, ".nojekyll"), "w") as fh:
        fh.write("")


def copy_site(src, out, base=""):
    src, out = os.path.abspath(src), os.path.abspath(out)
    if src != out:
        for sub in ("data", "basemap"):
            if os.path.isdir(os.path.join(src, sub)):
                shutil.copytree(os.path.join(src, sub), os.path.join(out, sub), dirs_exist_ok=True)
    write_site(out, base)


class Exporter:
    def __init__(self, app, out, core, area_map, dungeons, base="", log=print):
        self.app = app
        self.out = os.path.abspath(out)
        self.core = core
        self.area_map = area_map
        self.dungeons = list(dungeons)
        self.maps = {area_map, *self.dungeons}
        self.base = base.rstrip("/")
        self.log = log
        self.images = set()
        self.server = None
        self.url = ""

    def __enter__(self):
        self.app.cfg.port = 0
        self.app.cfg.host = "127.0.0.1"
        self.server = serve(self.app)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        return self

    def __exit__(self, *exc):
        self.server.shutdown()

    def get(self, path, raw=False, timeout=900):
        req = urllib.request.Request(self.url + path, headers={"X-Livemap-Token": self.app.cfg.token})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        return data if raw else json.loads(data)

    def api(self, name, **q):
        return self.get(f"/api/{name}?" + urllib.parse.urlencode({k: v for k, v in q.items() if v is not None}))

    def write(self, name, payload):
        path = os.path.join(self.out, "data", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"), default=str)

    def image(self, key):
        if not key or key in self.images:
            return
        self.images.add(key)
        path = os.path.join(self.out, "basemap", self.core, key + ".webp")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            data = self.get(f"/basemap/{self.core}/{urllib.parse.quote(key)}.webp", raw=True)
        except Exception as exc:
            self.log(f"  no image {key}: {exc}")
            return
        with open(path, "wb") as fh:
            fh.write(data)

    def site(self):
        write_site(self.out, self.base)

    def capabilities(self, tests_ok):
        report = self.api("capabilities", core=self.core)
        for key in ("steep", "swim"):
            report["layers"][key] = dict(report["layers"][key], status="missing", detail="needs the game client (not in the demo)")
        if not tests_ok:
            report["layers"]["tests"]["status"] = "missing"
        report["features"].update({"tests": tests_ok, "map": True, "live": True, "players": True})
        generic = {"client": "game client", "dbc": "DBC tables", "world_db": "world database", "travel_db": "world database",
                   "char_db": "characters database", "bot_module": "aiplayerbot.conf", "archive": "aiplayerbot.conf"}
        names = {}
        for cap in report["caps"]:
            cap["fix"] = []
            if cap["id"] in generic:
                if cap["source"]:
                    names[cap["source"]] = generic[cap["id"]]
                cap["source"] = generic[cap["id"]] if cap["source"] else ""
                if re.search(r"[A-Za-z]:[\\/]|/home/|\d+\.\d+\.\d+\.\d+", cap["detail"]):
                    cap["detail"] = ""
        for layer in report["layers"].values():
            parts = [names.get(x.strip(), x.strip()) for x in layer["source"].split(", ") if x.strip()]
            layer["source"] = ", ".join(dict.fromkeys(parts))
            if re.search(r"[A-Za-z]:[\\/]", layer["detail"]):
                layer["detail"] = ""
        self.write("capabilities.json", {"cores": {self.core: report}, "remote": True, "can_write": False, "demo": True})

    def overlay(self):
        ov = self.api("overlay", core=self.core, mode="real")
        ov["continents"] = [c for c in ov["continents"] if c["id"] == self.area_map]
        ov["instances"] = [i for i in ov["instances"] if i["id"] in self.dungeons and not i.get("tray")]
        ov["tray"] = []
        for item in ov["continents"] + ov["instances"]:
            base = item.get("base") or {}
            self.image(base.get("key"))
            model = item.get("model") or {}
            self.image(model.get("key"))
        self.write("overlay.json", ov)
        return ov

    def mapinfo(self):
        for map_id in sorted(self.maps):
            info = self.api("mapinfo", core=self.core, map=map_id)
            info["terrain_render"] = {"available": False}
            for b in info.get("basemaps") or []:
                self.image(b.get("key"))
            if info.get("terrain"):
                self.image(info["terrain"].get("key"))
            self.write(f"mapinfo_{map_id}.json", info)

    def box(self, ov):
        boxes = []
        for c in ov["continents"]:
            e = (c.get("base") or {}).get("bounds") or c.get("extent")
            if e:
                boxes.append((e["top"] + c["off"][0], e["bottom"] + c["off"][0], e["left"] + c["off"][1], e["right"] + c["off"][1]))
        top = max(b[0] for b in boxes)
        bottom = min(b[1] for b in boxes)
        left = max(b[2] for b in boxes)
        right = min(b[3] for b in boxes)
        return {"top": top, "bottom": bottom, "left": left, "right": right}

    def gridded(self, kind, box, cells=12):
        merged = {"scene": True, "maps": [], "features": [], "bins": [], "links": [], "nodes": [], "total": 0, "binned": False}
        seen = set()
        dx = (box["top"] - box["bottom"]) / cells
        dy = (box["left"] - box["right"]) / cells
        for i in range(cells):
            for j in range(cells):
                cell = {"bottom": box["bottom"] + i * dx, "top": box["bottom"] + (i + 1) * dx,
                        "right": box["right"] + j * dy, "left": box["right"] + (j + 1) * dy}
                data = self.api("layer", core=self.core, map="world", kind=kind, mode="real", **cell)
                for r in data.get("features") or []:
                    if (r[0], r[1]) not in seen:
                        seen.add((r[0], r[1]))
                        merged["features"].append(r)
                merged["bins"] += data.get("bins") or []
                merged["binned"] = merged["binned"] or bool(data.get("binned"))
                merged["maps"] = sorted(set(merged["maps"]) | set(data.get("maps") or []))
        merged["total"] = len(merged["features"]) + sum(b[-1] for b in merged["bins"])
        return merged

    def layers(self, box):
        for kind in LAYER_KINDS:
            try:
                if kind in ("creatures", "objects"):
                    data = self.gridded(kind, box)
                else:
                    data = self.api("layer", core=self.core, map="world", kind=kind, mode="real", **box)
            except Exception as exc:
                self.log(f"  layer {kind}: {exc}")
                continue
            for key in ("features", "bins"):
                data[key] = [r for r in data.get(key) or [] if r[0] in self.maps]
            for key in ("links", "nodes"):
                data[key] = [r for r in data.get(key) or [] if r.get("map") in self.maps]
            data["maps"] = [m for m in data.get("maps") or [] if m in self.maps]
            self.write(f"layer_{kind}.json", data)
        try:
            zl = self.api("layer", core=self.core, map="world", kind="zonelevels", left=0, right=0, top=0, bottom=0)
            self.write("layer_zonelevels.json", [z for z in zl if z.get("map", self.area_map) in self.maps])
        except Exception as exc:
            self.log(f"  zone levels: {exc}")
        try:
            ev = self.api("live/events", core=self.core, map="world", seconds=1800)
            self.write("events.json", [e for e in ev if e.get("map") in self.maps])
        except Exception as exc:
            self.write("events.json", [])
            self.log(f"  events: {exc}")
        try:
            tr = self.api("live/transports", core=self.core, map="world")
            tr["transports"] = [t for t in tr.get("transports") or [] if t.get("map") in self.maps]
            self.write("transports.json", tr)
        except Exception as exc:
            self.write("transports.json", {"transports": []})
            self.log(f"  transports: {exc}")
        try:
            it = self.api("live/intents", core=self.core, map="world", seconds=900)
            self.write("intents.json", [r for r in it if r.get("map") in self.maps or r.get("from_map") in self.maps])
        except Exception as exc:
            self.write("intents.json", [])
            self.log(f"  intents: {exc}")

    def tests(self, limit):
        try:
            where = self.api("tests/where", core=self.core, map="world")
        except Exception:
            where = []
        picked, seen, kinds = [], set(), {}
        pool = [r for r in where if r.get("map") in self.maps and r.get("loc") == "track" and (r.get("points") or 0) >= 5]
        pool.sort(key=lambda r: -(r.get("points") or 0))
        while pool and len(picked) < limit:
            def rank(r):
                stem = re.sub(r"_m\d$", "", r["test"])
                return (stem in seen, kinds.get(r["test"].split("_", 1)[0], 0), kinds.get(r["verdict"], 0))
            r = min(pool, key=rank)
            pool.remove(r)
            stem = re.sub(r"_m\d$", "", r["test"])
            if stem in seen and any(re.sub(r"_m\d$", "", p["test"]) != stem for p in pool):
                continue
            seen.add(stem)
            for k in (r["test"].split("_", 1)[0], r["verdict"]):
                kinds[k] = kinds.get(k, 0) + 1
            picked.append(r)
        rows, pages, verdicts = [], {}, {}
        for r in picked:
            run = self.api(f"run/{r['id']}")
            run["terrain_render"] = None
            if run.get("basemap"):
                self.image(run["basemap"].get("key") or run["basemap"]["image"].rsplit(".", 1)[0])
            self.write(f"run_{r['id']}.json", run)
            page = self.api("test", core=self.core, test=r["test"])
            page["history"] = [h for h in page["history"] if h["id"] == r["id"]]
            page["triage"] = None
            pages[f"{self.core}/{r['test']}"] = page
            listed = self.api("tests", core=self.core, q=r["test"].replace("_", " "), limit=5)
            row = next((t for t in listed if t["test"] == r["test"]), None)
            if row:
                row.update({"id": r["id"], "runs": 1, "passes": 1 if r["verdict"] == "PASS" else 0, "bucket": None, "preview": 1})
                rows.append(row)
                verdicts[row["verdict"]] = verdicts.get(row["verdict"], 0) + 1
        self.write("tests_where.json", [r for r in picked])
        self.write("tests.json", rows)
        self.write("test_pages.json", pages)
        self.write("families.json", [{"core": c, "family": f} for c, f in sorted({(r["core"], r["family"]) for r in rows})])
        last = max((r["ts"] for r in rows), default=None)
        self.write("summary.json", {
            "summary": {"verdicts": [{"core": self.core, "verdict": v, "n": n} for v, n in verdicts.items()],
                        "tests": [{"core": self.core, "n": len(rows), "last": last}]},
            "flips": [], "regressions": [], "cores": [self.core], "last_ingest": last, "remote": True})
        return bool(rows)

    def record(self, minutes, frame_s):
        frames, names, known, cols = [], {}, [], None
        n = max(1, int(minutes * 60 / frame_s))
        for k in range(n):
            t0 = time.time()
            snap = self.api("live", core=self.core, map="world")
            cols = snap["cols"]
            ci = {c: i for i, c in enumerate(cols)}
            for m in snap["maps"]:
                names[m["id"]] = m.get("name", f"map {m['id']}")
            known = [m for m in snap.get("known", []) if m["id"] in self.maps]
            bots = [r for r in snap["bots"] if r[ci["map"]] in self.maps and r[ci["bot"]]]
            if not frames or snap["epoch"] != frames[-1]["epoch"]:
                frames.append({"ts": snap["newest"], "epoch": snap["epoch"], "bots": bots})
            if k % 12 == 0:
                self.log(f"  recorded {len(frames)} frames ({len(bots)} bots in the demo area)")
            if k < n - 1:
                time.sleep(max(0.0, frame_s - (time.time() - t0)))
        ctx = self.app.cores[self.core]
        zones = set()
        for f in frames:
            for r in f["bots"]:
                zones.add(r[ci["zone"]])
                zones.add(r[ci["area"]])
        areas = {z: ctx.basemaps.area_name(z) for z in zones if z}
        self.write("live.json", {"core": self.core, "frame_s": frame_s, "cols": cols, "frames": frames,
                                 "map_names": {m: names.get(m, f"map {m}") for m in self.maps}, "known": known,
                                 "areas": areas})
        return len(frames)

    def run(self, minutes, frame_s, tests):
        os.makedirs(self.out, exist_ok=True)
        self.log("copying the web client ...")
        self.site()
        self.log("overlay, map info and art ...")
        ov = self.overlay()
        self.mapinfo()
        self.log("layers ...")
        self.layers(self.box(ov))
        self.log("tests ...")
        tests_ok = self.tests(tests)
        self.capabilities(tests_ok)
        self.log(f"recording live positions for {minutes} min ...")
        frames = self.record(minutes, frame_s)
        self.write("manifest.json", {"core": self.core, "area_map": self.area_map, "dungeons": self.dungeons,
                                     "frames": frames, "frame_s": frame_s, "recorded": time.strftime("%Y-%m-%d %H:%M")})
        size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(self.out) for f in fs)
        self.log(f"done: {self.out} ({size / 1048576:.1f} MB, {len(self.images)} images, {frames} frames)")
        return size
