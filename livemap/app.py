import collections
import math
import os
from dataclasses import replace
from datetime import datetime, timedelta
import threading
import time

from PIL import Image

from . import telemetry
from .adtrender import Terrain
from .basemap import Basemaps
from .capabilities import Probe
from .catalog import Catalog
from .clientdata import client_dbc
from . import overlay
from .layers import StaticLayers
from .live import DbFeed, LiveFeed, recent_events
from .mapstore import MapStore
from .worlddb import WorldDb

MARGIN = 0.35
SCENE_LIMIT = 600
SCENE_NODES = 240
TURN_MAX_PX = 4096
FOCUS_NODES = 4000
SCENE_PATH_SPAN = 10000


class CoreContext:
    def __init__(self, app, core, data_dir):
        self.app = app
        self.core = core
        self.data_dir = data_dir
        self._basemaps = None
        self._live = None
        self._db_live = None
        self._layers = None
        self.world = WorldDb(core)
        self.probe = Probe(app, self)

    @property
    def layers(self):
        if self._layers is None:
            self._layers = StaticLayers(self)
        return self._layers

    @property
    def basemaps(self):
        if self._basemaps is None:
            client = self.app.client_dir(self.core.name)
            core = self.core if client == self.core.client else replace(self.core, client=client)
            self._basemaps = Basemaps(core, self.data_dir)
            if self.world.available():
                self._basemaps.extent_hint = self.safe_extent
        return self._basemaps

    def safe_extent(self, map_id):
        try:
            return self.world.map_extent(map_id)
        except Exception:
            return None

    @property
    def live(self):
        if self._live is None:
            self._live = LiveFeed(self.core, self.basemaps.wmo_floor)
        if not self.core.char_db or self._live.is_live():
            return self._live
        if self._db_live is None:
            self._db_live = DbFeed(self.core)
        return self._db_live

    def map_info(self, map_id):
        bm = self.basemaps
        entries = bm.entries_for(map_id)
        zones = [{"name": bm.area_name(e["area"]) or e["name"], "x": (e["bounds"]["top"] + e["bounds"]["bottom"]) / 2,
                  "y": (e["bounds"]["left"] + e["bounds"]["right"]) / 2, "key": e["key"]}
                 for e in entries if e["kind"] == "zone" and e["area"]]
        extent = None
        if not entries and self.world.available():
            try:
                extent = self.world.map_extent(map_id)
            except Exception:
                extent = None
        terrain = bm.terrain_entry(map_id)
        return {"id": map_id, "name": bm.map_names().get(map_id, f"map {map_id}"), "basemaps": entries, "zones": zones,
                "extent": extent, "terrain": bm.describe(terrain) if terrain else None,
                "terrain_render": self.app.terrain.describe(self.core.name, map_id)}


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.catalog = Catalog(os.path.join(cfg.data_dir, "livemap.sqlite"))
        self.mapstore = MapStore(os.path.join(cfg.data_dir, "livemap.sqlite"), cfg.map)
        self.terrain = Terrain(self)
        for name, core in cfg.cores.items():
            self.dbc_fallback(core)
        self.cores = {name: CoreContext(self, core, cfg.data_dir) for name, core in cfg.cores.items()}
        self.last_ingest = None
        self.listeners = []
        self.overlay_cache = {}
        self.known_cache = {}

    def dbc_fallback(self, core):
        if core.dbc and os.path.isfile(os.path.join(core.dbc, "Map.dbc")):
            return
        client = self.client_dir(core.name)
        if not client:
            return
        try:
            out = client_dbc(client, core.locale, os.path.join(self.cfg.data_dir, "dbc", core.name))
        except Exception as exc:
            print(f"{core.name}: could not read DBC tables from the client: {exc}")
            return
        if out:
            core.dbc = out
            core.dbc_from_client = True

    def add_core(self, entry):
        from .config import add_core
        core = add_core(self.cfg, os.path.dirname(self.cfg.path), "", entry)
        self.dbc_fallback(core)
        cores = dict(self.cores)
        cores[core.name] = CoreContext(self, core, self.cfg.data_dir)
        self.cores = cores
        return cores[core.name].probe.report(True)

    def enable_exports(self, core, group):
        from . import botconf
        from .capabilities import EXPORT_GROUPS
        ctx = self.cores[core]
        if group not in EXPORT_GROUPS:
            raise ValueError(f"unknown export group {group}")
        path = ctx.core.bot_conf
        if not path or not os.path.isfile(path):
            raise ValueError("no aiplayerbot.conf for this server")
        added = botconf.add_log_files(path, EXPORT_GROUPS[group]["files"])
        if group == "live":
            values = botconf.read_values(path) or {}
            if (values.get("AiPlayerbot.LiveMapSampleSec") or {}).get("value") is None:
                botconf.write_value(path, "AiPlayerbot.LiveMapSampleSec", "5")
        return {"added": added, "report": ctx.probe.report(True)}

    def capabilities(self, core=None, refresh=False):
        if core:
            return self.cores[core].probe.report(refresh)
        if len(self.cores) < 2:
            return {name: ctx.probe.report(refresh) for name, ctx in self.cores.items()}
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(8, len(self.cores))) as pool:
            reports = list(pool.map(lambda ctx: ctx.probe.report(refresh), self.cores.values()))
        return dict(zip(self.cores, reports))

    def client_dir(self, name):
        core = self.cfg.cores.get(name)
        return self.mapstore.client_dir(name) or (core.client if core else "")

    def reset_client(self, name):
        ctx = self.cores.get(name)
        if ctx is not None:
            ctx._basemaps = None
            ctx.probe.cached = None

    def ingest(self):
        added = 0
        for ctx in self.cores.values():
            added += self.catalog.ingest_core(ctx.core)
        self.last_ingest = time.strftime("%Y-%m-%d %H:%M:%S")
        return added

    def start_ingest_loop(self, every=5.0):
        def loop():
            while True:
                try:
                    self.ingest()
                except Exception as exc:
                    print("ingest failed:", exc)
                time.sleep(every)
        threading.Thread(target=loop, daemon=True, name="ingest").start()

    def start_preview_loop(self, pause=0.2, idle=30.0):
        def loop():
            while True:
                ids = []
                try:
                    ids = self.catalog.missing_previews(20)
                    for run_id in ids:
                        try:
                            self.run_view(run_id)
                        except Exception:
                            self.catalog.set_preview(run_id, -1, None)
                        time.sleep(pause)
                except Exception as exc:
                    print("preview check failed:", exc)
                if not ids:
                    time.sleep(idle)
        threading.Thread(target=loop, daemon=True, name="previews").start()

    def live_core(self):
        def score(ctx):
            try:
                ctx.live.poll()
                bots = len(ctx.live.bots)
            except Exception:
                bots = 0
            return (ctx.live.is_live() and bots > 0, bots > 0, ctx.live.mtime())
        best = max(self.cores.values(), key=score, default=None)
        return best.core.name if best else None

    def live_snapshot(self, core, map_id):
        ctx = self.cores[core]
        snap = ctx.live.snapshot(map_id)
        names = ctx.basemaps.map_names()
        for m in snap["maps"]:
            m["name"] = names.get(m["id"], f"map {m['id']}")
        snap["known"] = self.known_maps(core)
        return snap

    def known_maps(self, core):
        cached = self.known_cache.get(core)
        if cached is not None:
            return cached
        ctx = self.cores[core]
        names = ctx.basemaps.map_names()
        ids = overlay.spawned_maps(ctx) | {m for m in overlay.CONTINENT_OFFSETS if m in ctx.basemaps.map_dirs()}
        rows = [{"id": m, "name": names.get(m, f"map {m}")} for m in sorted(ids)]
        rows = [r for r in rows if not overlay.SKIP_NAME.search(r["name"] or "")]
        self.known_cache[core] = rows
        return rows

    def bot_card(self, core, guid):
        ctx = self.cores[core]
        card = ctx.live.bot(guid)
        if not card:
            return None
        names = ctx.basemaps.map_names()
        card["map_name"] = names.get(card["map"], f"map {card['map']}")
        card["zone_name"] = ctx.basemaps.area_name(card["zone"])
        card["area_name"] = ctx.basemaps.area_name(card["area"])
        card["events"] = recent_events(ctx.core.logs, ctx.live.newest_ts, None, 3600, name=card["name"], limit=40)
        card["runs"] = self.catalog.runs_by_host(core, card["name"])
        return card

    def overlay(self, core, mode):
        mode = "sea" if mode == "sea" else "real"
        ctx = self.cores[core]
        sig = overlay.signature(ctx)
        cached = self.overlay_cache.get(core)
        if cached and cached[0] == (mode, sig):
            return cached[1]
        result = overlay.build(ctx, mode, pick_base)
        self.overlay_cache[core] = ((mode, sig), result)
        self.warm_models(core, [i["id"] for i in result["instances"] if i.get("model")])
        return result

    def turned_image(self, core, key, deg):
        ctx = self.cores.get(core)
        if ctx is None:
            return None
        if key.startswith("model_") and key[6:].isdigit():
            src = self.terrain.model_image(core, int(key[6:]))
            entry = self.terrain.model_entry(core, int(key[6:]))
            bounds = entry["bounds"] if entry else None
        else:
            src = ctx.basemaps.image_for_key(key)
            entry = ctx.basemaps.entry_by_key(key)
            bounds = ctx.basemaps.describe(entry)["bounds"] if entry else None
        if not src or not bounds:
            return None
        deg = round(deg, 1)
        safe = lambda v: "".join(ch if ch.isalnum() or ch in "_-." else "_" for ch in str(v))
        path = os.path.join(self.cfg.data_dir, "turned", safe(core), f"{safe(key)}_{deg:+.1f}.webp")
        if os.path.exists(path) and os.path.getmtime(path) >= os.path.getmtime(src):
            return path
        img = Image.open(src).convert("RGBA")
        w_yd = abs(bounds["left"] - bounds["right"])
        h_yd = abs(bounds["top"] - bounds["bottom"])
        k = min(max(img.width / w_yd, img.height / h_yd), TURN_MAX_PX / max(w_yd, h_yd))
        img = img.resize((max(1, round(w_yd * k)), max(1, round(h_yd * k))), Image.BILINEAR)
        out = img.rotate(deg, resample=Image.BICUBIC, expand=True)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        out.save(tmp, "WEBP", quality=88, method=4)
        os.replace(tmp, path)
        return path

    def warm_models(self, core, map_ids):
        def run():
            for map_id in map_ids:
                try:
                    self.terrain.model_image(core, map_id)
                except Exception as exc:
                    print("model render failed:", core, map_id, exc)
        threading.Thread(target=run, daemon=True, name="model-warm").start()

    def scene_offsets(self, core, mode, honest, focus=None, dungeons=True, turn=False):
        ov = self.overlay(core, mode)
        base = (0.0, 0.0)
        if focus is not None:
            base = next((c["off"] for c in ov["continents"] if c["id"] == focus), base)
        out = {}
        for item in ov["continents"] + (ov["instances"] if dungeons else []):
            if item.get("tray"):
                continue
            if focus is not None and item["id"] != focus and item.get("root") != focus:
                continue
            angle = 0.0
            if honest and turn and item.get("turn") is not None:
                off, angle = item["off_turn"], item["turn"]
            else:
                off = (item.get("off_honest") or item["off"]) if honest else item["off"]
            out[item["id"]] = (off[0] - base[0], off[1] - base[1], angle)
        return out

    def scene_layer(self, core, kind, mode, honest, bounds, kinds=None, focus=None, dungeons=True, turn=False):
        ctx = self.cores[core]
        out = {"scene": True, "maps": [], "features": [], "bins": [], "links": [], "nodes": [], "total": 0, "binned": False}
        for map_id, off in sorted(self.scene_offsets(core, mode, honest, focus, dungeons, turn).items()):
            b = local_bounds(bounds, off)
            if b["bottom"] > b["top"] or b["right"] > b["left"]:
                continue
            if kind in ("creatures", "objects"):
                data = ctx.world.spawn_layer(kind, map_id, b, limit=SCENE_LIMIT)
            elif kind == "nodes":
                close = max(bounds["top"] - bounds["bottom"], bounds["left"] - bounds["right"]) <= SCENE_PATH_SPAN
                data = ctx.world.travel_nodes(map_id, b, limit=SCENE_NODES if focus is None else FOCUS_NODES,
                                              paths=close)
            else:
                data = ctx.layers.telecache.layer(map_id, b, kinds, limit=SCENE_LIMIT)
            out["maps"].append(map_id)
            out["total"] += data.get("total", 0)
            out["binned"] = out["binned"] or bool(data.get("binned"))
            for key in ("features", "bins"):
                for row in data.get(key) or ():
                    out[key].append([map_id] + list(row))
            for key in ("links", "nodes"):
                for row in data.get(key) or ():
                    out[key].append(dict(row, map=map_id))
        return out

    def scene_zone_levels(self, core):
        return self.cores[core].layers.zones.for_map(None)

    def scene_intents(self, core, seconds):
        ctx = self.cores[core]
        rows = ctx.layers.intents.recent(None, seconds)
        names = ctx.basemaps.map_names()
        for r in rows:
            if r["to"] is None and r["to_map"] is not None:
                r["to_name"] = names.get(r["to_map"], f"map {r['to_map']}")
        return rows

    def live_intents(self, core, map_id, seconds):
        ctx = self.cores[core]
        rows = ctx.layers.intents.recent(map_id, seconds)
        names = ctx.basemaps.map_names()
        for r in rows:
            if r["to"] is None and r["to_map"] is not None:
                r["to_name"] = names.get(r["to_map"], f"map {r['to_map']}")
        return rows

    def live_events(self, core, map_id, seconds, kinds):
        ctx = self.cores[core]
        return recent_events(ctx.core.logs, ctx.live.newest_ts, map_id, seconds, kinds=kinds)

    def quest_info(self, core, quest_id):
        ctx = self.cores[core]
        if not quest_id or not ctx.world.available():
            return None
        try:
            return ctx.world.quest(quest_id)
        except Exception as exc:
            return {"error": str(exc)}

    def run_view(self, run_id, art=None):
        run = self.catalog.run(run_id)
        if not run:
            return None
        ctx = self.cores.get(run["core"])
        if ctx is None:
            self.catalog.set_preview(run_id, -1, None)
            return {"run": run, "notice": [f"core {run['core']} is not configured"]}
        logs = ctx.core.logs
        record = telemetry.test_records(logs, run["host"], run["test"], run["ts"])
        if record and record.get("start"):
            t0 = shift(record["start"], -5)
            t1 = shift(record["end"], 5)
        else:
            t0, t1 = telemetry.window_for(run)
        track, events, states = telemetry.collect(logs, run["host"], t0, t1)
        test_start = record.get("start") if record else None
        if not test_start:
            track, test_start = telemetry.trim_to_test_start(track, run["elapsed_s"] is not None)
            if test_start:
                events = [e for e in events if e["t"] >= test_start]
        exact = any(p.get("w") for p in track)
        if exact:
            track, arrived = telemetry.cut_teleports(track)
            if arrived:
                events = [e for e in events if e["t"] >= arrived]
        quest = self.quest_info(run["core"], run["quest_id"])
        markers = quest_markers(quest, run["variant"]) if quest and "error" not in quest else []
        offsets = telemetry.load_offsets(logs)
        if exact:
            maps = collections.Counter(p["w"][0] for p in track if p.get("w"))
            end_map = record["end_world"][0] if record and record.get("end_world") else None
            map_id = end_map if end_map is not None else maps.most_common(1)[0][0]
            map_source = "logged"
        else:
            map_id, map_source = self.pick_map(ctx, run, markers, track, offsets)
        world_track = to_world(track, offsets.get(map_id), map_id) if map_id is not None else []
        world_events = to_world([e for e in events if e.get("w") or e["d"]], offsets.get(map_id), map_id) if map_id is not None else []
        focus = [(p["x"], p["y"]) for p in world_track] + [(m["x"], m["y"]) for m in markers if m["map"] == map_id]
        basemap = None
        notice = []
        if map_id is not None and focus:
            wmo_ids = [tuple(p["wmo"]) for p in track if p.get("wmo") and p["wmo"][1] > 0]
            entry = None
            if art == "minimap":
                entry = ctx.basemaps.minimap_entry(map_id)
            else:
                entry = ctx.basemaps.floor_for(map_id, wmo_ids) if wmo_ids else None
            if entry is None:
                entry = ctx.basemaps.choose(map_id, focus)
            if entry:
                basemap = ctx.basemaps.ensure(entry)
        if basemap and not exact:
            b = basemap["bounds"]
            keep = lambda p: inside(b, p["x"], p["y"], MARGIN)
            dropped = len(world_track) - sum(1 for p in world_track if keep(p))
            world_track = [p for p in world_track if keep(p)]
            world_events = [e for e in world_events if keep(e)]
            if dropped:
                notice.append(f"{dropped} track points outside this map were hidden (bot left the map or another map overlaps in the log coordinates)")
        if not track:
            notice.append("no position telemetry for this run (logs from that boot were truncated, or the logs are not enabled)")
        elif not exact:
            notice.append("approximate positions (older log format without map id)")
            if track[0]["src"] == "states":
                notice.append("coarse track from bot_states.csv (heartbeat not available for this run)")
        if map_id is not None and not basemap:
            notice.append("no painted map for this area yet; showing positions on a plain grid")
        self.catalog.set_preview(run_id, len(world_track), map_id, run_location(map_id, world_track, markers, record))
        return {
            "run": run,
            "terrain_render": self.terrain.describe(run["core"], map_id) if map_id is not None else None,
            "record": record,
            "exact": exact,
            "window": {"start": test_start or t0, "end": t1},
            "map": {"id": map_id, "source": map_source},
            "art": art or "painted",
            "basemap": basemap,
            "track": world_track,
            "events": world_events,
            "quest": quest,
            "markers": [m for m in markers if map_id is None or m["map"] == map_id],
            "other_markers": sum(1 for m in markers if map_id is not None and m["map"] != map_id),
            "notice": notice,
        }

    def pick_map(self, ctx, run, markers, track, offsets):
        if markers:
            counts = collections.Counter(m["map"] for m in markers if m["role"] == focus_role(run["variant"]))
            if not counts:
                counts = collections.Counter(m["map"] for m in markers)
            return counts.most_common(1)[0][0], "quest"
        if run["family"] == "scenario":
            m = telemetry.scenario_map(ctx.core.dbc, run["subject"])
            if m is not None:
                return m, "scenario"
        if not track:
            return None, None
        best = None
        for map_id, off in offsets.items():
            pts = to_world(track, off)
            zones = [a for a in ctx.basemaps.areas if a["map"] == map_id and a["area"]]
            score = sum(1 for p in pts if any(inside(z, p["x"], p["y"]) for z in zones))
            if best is None or score > best[1]:
                best = (map_id, score)
        return (best[0], "guess") if best and best[1] else (None, None)


def local_bounds(bounds, place):
    ox, oy, angle = place
    if not angle:
        return {"top": bounds["top"] - ox, "bottom": bounds["bottom"] - ox,
                "left": bounds["left"] - oy, "right": bounds["right"] - oy}
    c, s = math.cos(-angle), math.sin(-angle)
    xs, ys = [], []
    for x in (bounds["top"], bounds["bottom"]):
        for y in (bounds["left"], bounds["right"]):
            dx, dy = x - ox, y - oy
            xs.append(dx * c - dy * s)
            ys.append(dx * s + dy * c)
    return {"top": max(xs), "bottom": min(xs), "left": max(ys), "right": min(ys)}


def pick_base(basemaps):
    def size(b):
        return abs(b["bounds"]["top"] - b["bounds"]["bottom"]) * abs(b["bounds"]["left"] - b["bounds"]["right"])
    for kind, test in (("zone", lambda b: not b["area"]), ("floor", None), ("zone", None), ("wmo", None)):
        found = [b for b in basemaps if b["kind"] == kind and (test is None or test(b))]
        if found:
            return min(found, key=lambda b: b.get("floor") or 0) if kind == "floor" else max(found, key=size)
    return None


def inside(b, x, y, margin=0.0):
    mx = abs(b["top"] - b["bottom"]) * margin
    my = abs(b["left"] - b["right"]) * margin
    return b["bottom"] - mx <= x <= b["top"] + mx and b["right"] - my <= y <= b["left"] + my


def shift(ts, seconds):
    return (datetime.strptime(ts, telemetry.TS_FMT) + timedelta(seconds=seconds)).strftime(telemetry.TS_FMT)


def to_world(items, offset, map_id=None):
    if offset is None:
        offset = (0.0, 0.0)
    out = []
    for it in items:
        w = it.get("w")
        if w:
            if map_id is not None and w[0] != map_id:
                continue
            x, y = w[1], w[2]
        elif it.get("d"):
            x, y = telemetry.display_to_world(it["d"][0], it["d"][1], offset)
        else:
            continue
        o = {k: v for k, v in it.items() if k not in ("d", "w", "wmo")}
        o["x"], o["y"] = round(x, 2), round(y, 2)
        out.append(o)
    return out


def focus_role(variant):
    v = variant or ""
    if v.startswith("pickup"):
        return "giver"
    if v.startswith("handin"):
        return "taker"
    if v.startswith("obj"):
        return "objective"
    return "giver"


def run_location(map_id, track, markers, record=None):
    if map_id is not None and track:
        return map_id, track[-1]["x"], track[-1]["y"], "track"
    point = record and (record.get("end_world") or record.get("start_world"))
    if point:
        return point[0], point[1], point[2], "record"
    if markers:
        best = next((m for m in markers if m.get("focus")), None) or next((m for m in markers if m["role"] == "objective"), None) or markers[0]
        return best["map"], best["x"], best["y"], "quest"
    return None


def quest_markers(quest, variant):
    out = []
    obj_index = None
    if variant and variant.startswith("obj"):
        try:
            obj_index = int(variant[3:].split("_", 1)[0])
        except ValueError:
            obj_index = None
    for role, key in (("giver", "givers"), ("taker", "takers")):
        for actor in quest.get(key, []):
            for s in actor["spawns"]:
                out.append({"role": role, "name": actor["name"], "map": s["map"], "x": s["x"], "y": s["y"]})
    for n, obj in enumerate(quest.get("objectives", [])):
        for s in obj["spawns"]:
            out.append({"role": "objective", "name": obj["name"], "kind": obj["kind"], "map": s["map"],
                        "x": s["x"], "y": s["y"], "focus": obj_index is not None and n == obj_index})
    return out
