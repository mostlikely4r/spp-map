import collections
import os
import threading
import time

from . import telemetry
from .basemap import Basemaps
from .catalog import Catalog
from .worlddb import WorldDb

MARGIN = 0.35


class CoreContext:
    def __init__(self, core, data_dir):
        self.core = core
        self.data_dir = data_dir
        self._basemaps = None
        self.world = WorldDb(core)

    @property
    def basemaps(self):
        if self._basemaps is None:
            self._basemaps = Basemaps(self.core, self.data_dir)
        return self._basemaps


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.catalog = Catalog(os.path.join(cfg.data_dir, "livemap.sqlite"))
        self.cores = {name: CoreContext(core, cfg.data_dir) for name, core in cfg.cores.items()}
        self.last_ingest = None
        self.listeners = []

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
            return {"run": run, "notice": f"core {run['core']} is not configured"}
        logs = ctx.core.logs
        t0, t1 = telemetry.window_for(run)
        track, events, states = telemetry.collect(logs, run["host"], t0, t1)
        track, test_start = telemetry.trim_to_test_start(track, run["elapsed_s"] is not None)
        if test_start:
            events = [e for e in events if e["t"] >= test_start]
        quest = self.quest_info(run["core"], run["quest_id"])
        markers = quest_markers(quest, run["variant"]) if quest and "error" not in quest else []
        offsets = telemetry.load_offsets(logs)
        map_id, map_source = self.pick_map(ctx, run, markers, track, offsets)
        world_track = to_world(track, offsets.get(map_id)) if map_id is not None else []
        world_events = to_world([e for e in events if e["d"]], offsets.get(map_id)) if map_id is not None else []
        focus = [(p["x"], p["y"]) for p in world_track] + [(m["x"], m["y"]) for m in markers if m["map"] == map_id]
        basemap = None
        notice = []
        if map_id is not None and focus:
            entry = ctx.basemaps.choose(map_id, focus)
            if entry:
                basemap = ctx.basemaps.ensure(entry)
        if basemap:
            b = basemap["bounds"]
            keep = lambda p: inside(b, p["x"], p["y"], MARGIN)
            dropped = len(world_track) - sum(1 for p in world_track if keep(p))
            world_track = [p for p in world_track if keep(p)]
            world_events = [e for e in world_events if keep(e)]
            if dropped:
                notice.append(f"{dropped} track points outside this map were hidden (bot left the map or another map overlaps in the log coordinates)")
        if not track:
            notice.append("no position telemetry for this run (logs from that boot were truncated, or the logs are not enabled)")
        elif track[0]["src"] == "states":
            notice.append("coarse track from bot_states.csv (heartbeat not available for this run)")
        if map_id is not None and not basemap:
            notice.append("no painted map for this area yet; showing positions on a plain grid")
        return {
            "run": run,
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


def inside(b, x, y, margin=0.0):
    mx = abs(b["top"] - b["bottom"]) * margin
    my = abs(b["left"] - b["right"]) * margin
    return b["bottom"] - mx <= x <= b["top"] + mx and b["right"] - my <= y <= b["left"] + my


def to_world(items, offset):
    if offset is None:
        offset = (0.0, 0.0)
    out = []
    for it in items:
        x, y = telemetry.display_to_world(it["d"][0], it["d"][1], offset)
        o = {k: v for k, v in it.items() if k != "d"}
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
