import collections
import os
from datetime import datetime, timedelta
import threading
import time

from . import telemetry
from .basemap import Basemaps
from .catalog import Catalog
from . import overlay
from .layers import StaticLayers, file_sig, map_offsets
from .live import LiveFeed, recent_events
from .worlddb import WorldDb

MARGIN = 0.35


class CoreContext:
    def __init__(self, core, data_dir):
        self.core = core
        self.data_dir = data_dir
        self._basemaps = None
        self._live = None
        self._layers = None
        self.world = WorldDb(core)

    @property
    def layers(self):
        if self._layers is None:
            self._layers = StaticLayers(self)
        return self._layers

    @property
    def basemaps(self):
        if self._basemaps is None:
            self._basemaps = Basemaps(self.core, self.data_dir)
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
        return self._live

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
        return {"id": map_id, "name": bm.map_names().get(map_id, f"map {map_id}"), "basemaps": entries, "zones": zones,
                "extent": extent}


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.catalog = Catalog(os.path.join(cfg.data_dir, "livemap.sqlite"))
        self.cores = {name: CoreContext(core, cfg.data_dir) for name, core in cfg.cores.items()}
        self.last_ingest = None
        self.listeners = []
        self.atlas_cache = {}
        self.overlay_cache = {}

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
        return snap

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

    def atlas(self, core):
        ctx = self.cores[core]
        sig = file_sig(os.path.join(ctx.core.logs, "livemap_manifest.json"))
        cached = self.atlas_cache.get(core)
        if cached and cached[0] == sig:
            return cached[1]
        out = []
        for map_id, (ox, oy) in sorted(map_offsets(ctx.core.logs).items()):
            info = ctx.map_info(map_id)
            base = pick_base(info["basemaps"])
            if base and base["kind"] == "zone" and not base["area"]:
                terrain = ctx.basemaps.terrain_entry(map_id)
                base = ctx.basemaps.describe(terrain) if terrain else base
            extent = base["bounds"] if base else info["extent"]
            if extent is None and ctx.world.available():
                try:
                    extent = ctx.world.map_extent(map_id)
                except Exception:
                    extent = None
            if extent is None:
                continue
            out.append({"id": map_id, "name": info["name"], "off": [ox, oy], "base": base, "extent": extent})
        result = {"core": core, "maps": out}
        self.atlas_cache[core] = (sig, result)
        return result

    def overlay(self, core, mode):
        mode = "sea" if mode == "sea" else "real"
        ctx = self.cores[core]
        sig = overlay.signature(ctx)
        cached = self.overlay_cache.get(core)
        if cached and cached[0] == (mode, sig):
            return cached[1]
        result = overlay.build(ctx, mode, pick_base)
        self.overlay_cache[core] = ((mode, sig), result)
        return result

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

    def run_view(self, run_id):
        run = self.catalog.run(run_id)
        if not run:
            return None
        ctx = self.cores.get(run["core"])
        if ctx is None:
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
        return {
            "run": run,
            "record": record,
            "exact": exact,
            "window": {"start": test_start or t0, "end": t1},
            "map": {"id": map_id, "source": map_source},
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
