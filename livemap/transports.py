import collections
import os
import re
import threading
import time
from datetime import datetime, timedelta

from . import telemetry
from .layers import map_offsets

SNAPSHOT = "transport.csv"
TRACE = "bot_transport.csv"
STALE_S = 1800
LIVE_S = 180
RIDER_S = 20
TRAIL_S = 1800
TRAIL_MAX = 600
POLL_MIN_S = 1.0
BACKFILL_S = 900
BACKFILL_BYTES = 4 * 1024 * 1024

ROW = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\+00,'
    r'(?P<name>.*),(?P<entry>\d+),'
    r'"POINT\((?P<dx>-?[\d.]+) (?P<dy>-?[\d.]+)\)",'
    r'(?P<o>-?[\d.]+),(?P<map>\d+),(?P<guid>\d+),(?P<passengers>\d+)$')


class TransportFeed:
    def __init__(self, core, world):
        self.core = core
        self.world = world
        logs = core.logs
        self.snapshot_path = os.path.join(logs, SNAPSHOT) if logs else ""
        self.trace_path = os.path.join(logs, TRACE) if logs else ""
        self.lock = threading.Lock()
        self.source = None
        self.found = {}
        self.trails = collections.defaultdict(collections.deque)
        self.riders = collections.defaultdict(dict)
        self.names = {}
        self.clock = 0
        self.polled = 0.0
        self.offset = 0
        self.inode = None
        self.newest = 0
        self.newest_ts = None
        self.offsets = None
        self.offsets_sig = None

    def reset_trace(self):
        self.offset = 0
        self.inode = None
        self.newest = 0
        self.newest_ts = None

    def offsets_for(self):
        path = os.path.join(self.core.logs, "map_offsets.csv") if self.core.logs else ""
        try:
            sig = (os.path.getmtime(path), os.path.getsize(path))
        except OSError:
            sig = None
        if self.offsets is None or sig != self.offsets_sig:
            self.offsets, self.offsets_sig = map_offsets(self.core.logs), sig
        return self.offsets

    def poll(self):
        with self.lock:
            now = time.time()
            if now - self.polled < POLL_MIN_S:
                return
            self.polled = now
            if self.poll_snapshot():
                return
            self.poll_trace()

    def poll_snapshot(self):
        try:
            st = os.stat(self.snapshot_path)
            with open(self.snapshot_path, newline="", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            return False
        offsets = self.offsets_for()
        rows = {}
        for line in text.splitlines():
            m = ROW.match(line)
            if not m:
                continue
            try:
                entry = int(m.group("entry"))
                map_id = int(m.group("map"))
                guid = int(m.group("guid"))
                off = offsets.get(map_id)
                if not off:
                    continue
                x, y = telemetry.display_to_world(float(m.group("dx")), float(m.group("dy")), off)
                rows[guid] = {"guid": guid, "entry": entry, "name": m.group("name"), "map": map_id,
                              "x": x, "y": y, "z": 0.0, "o": float(m.group("o")),
                              "passengers": int(m.group("passengers")), "speed": 0.0, "progress": 0,
                              "epoch": int(st.st_mtime), "ts": m.group("ts"), "state": ""}
            except ValueError:
                continue
        if not rows and not text.lstrip().startswith("ts,"):
            return False
        if not rows and len([line for line in text.splitlines() if line.strip()]) > 1:
            return False
        if self.source != SNAPSHOT:
            self.trails.clear()
            self.riders.clear()
            self.source = SNAPSHOT
        self.found = rows
        self.clock = int(st.st_mtime)
        epoch = int(st.st_mtime)
        for row in rows.values():
            trail = self.trails[row["guid"]]
            if not trail or trail[-1][1] != row["map"] or abs(trail[-1][2] - row["x"]) > 0.5 \
                    or abs(trail[-1][3] - row["y"]) > 0.5:
                trail.append((epoch, row["map"], row["x"], row["y"]))
                while len(trail) > TRAIL_MAX:
                    trail.popleft()
        self.expire_trails()
        return True

    def poll_trace(self):
        try:
            if not self.trace_path:
                raise OSError
            st = os.stat(self.trace_path)
        except OSError:
            if self.source == TRACE:
                self.found.clear()
                self.trails.clear()
                self.riders.clear()
            self.source = None
            self.reset_trace()
            return
        if self.source != TRACE:
            self.found.clear()
            self.trails.clear()
            self.riders.clear()
            self.source = TRACE
            self.reset_trace()
        ident = (st.st_ctime_ns, getattr(st, "st_ino", 0))
        if self.inode is not None and (ident != self.inode or st.st_size < self.offset):
            self.reset_trace()
            self.found.clear()
        self.inode = ident
        if st.st_size <= self.offset:
            return
        with open(self.trace_path, "rb") as fh:
            if self.offset == 0 and st.st_size > BACKFILL_BYTES:
                target = last_ts(fh, st.st_size)
                if target:
                    start = (datetime.strptime(target, telemetry.TS_FMT) - timedelta(seconds=BACKFILL_S)).strftime(telemetry.TS_FMT)
                    telemetry.seek_time(fh, st.st_size, start)
            else:
                fh.seek(self.offset)
            data = fh.read(st.st_size - fh.tell())
        end = data.rfind(b"\n")
        if end < 0:
            return
        self.offset = st.st_size - len(data) + end + 1
        for raw in data[:end].split(b"\n"):
            self.ingest(raw)
        self.expire_trace()

    def ingest(self, raw):
        if not raw[:1].isdigit():
            return
        f = raw.decode("utf-8", errors="replace").rstrip("\r").split(",")
        if len(f) < 28:
            return
        try:
            epoch = int(f[1])
            bot = int(f[3])
            entry = int(f[8])
            tguid = int(f[9])
            tmap = int(f[10])
            tx, ty, tz = float(f[18]), float(f[19]), float(f[20])
            speed = float(f[21])
            progress = int(float(f[22] or 0))
        except ValueError:
            return
        if not tguid or not entry:
            return
        row = {"guid": tguid, "entry": entry, "map": tmap, "x": tx, "y": ty, "z": tz, "o": None,
               "passengers": 0, "speed": speed, "progress": progress, "epoch": epoch,
               "ts": f[0][:19], "state": f[4]}
        self.found[tguid] = row
        if f[23] == "1" or f[24] == "1":
            self.riders[tguid][bot] = epoch
        trail = self.trails[tguid]
        if not trail or trail[-1][1] != tmap or trail[-1][2] != tx or trail[-1][3] != ty:
            trail.append((epoch, tmap, tx, ty))
            while len(trail) > TRAIL_MAX:
                trail.popleft()
        if epoch > self.newest:
            self.newest = epoch
            self.newest_ts = row["ts"]

    def expire_trails(self):
        clock = int(time.time())
        cutoff = clock - TRAIL_S
        for guid in list(self.trails):
            trail = self.trails[guid]
            while trail and trail[0][0] < cutoff:
                trail.popleft()
            if not trail:
                del self.trails[guid]

    def expire_trace(self):
        cutoff = self.newest - TRAIL_S
        for guid in list(self.trails):
            trail = self.trails[guid]
            while trail and trail[0][0] < cutoff:
                trail.popleft()
            if not trail:
                del self.trails[guid]
        gone = self.newest - 3 * STALE_S
        for guid in [g for g, r in self.found.items() if r["epoch"] < gone]:
            del self.found[guid]
            self.riders.pop(guid, None)

    def name(self, entry):
        if entry not in self.names:
            try:
                self.names[entry] = self.world.gameobject_name(entry)
            except Exception:
                return f"transport {entry}"
        return self.names[entry]

    def snapshot(self, map_id=None, seconds=STALE_S):
        self.poll()
        clock = int(time.time())
        with self.lock:
            cutoff = clock - seconds
            rows = [dict(r) for r in self.found.values() if r["epoch"] >= cutoff and (map_id is None or r["map"] == map_id)]
            rider_cutoff = clock - RIDER_S
            trails = {r["guid"]: [(t[1], t[2], t[3]) for t in self.trails.get(r["guid"], ())
                                  if t[1] == r["map"] and t[0] >= cutoff] for r in rows}
            riders = {r["guid"]: sum(1 for epoch in self.riders.get(r["guid"], {}).values() if epoch >= rider_cutoff)
                      for r in rows}
            source = self.source
            newest = max((r["epoch"] for r in rows), default=0)
        out = []
        for r in rows:
            age = max(0, clock - r["epoch"])
            passengers = r.get("passengers", 0) if source == SNAPSHOT else riders[r["guid"]]
            row = {"guid": r["guid"], "entry": r["entry"], "name": r.get("name") or self.name(r["entry"]),
                   "map": r["map"], "x": round(r["x"], 1), "y": round(r["y"], 1), "z": round(r["z"], 1),
                   "speed": round(r["speed"], 1), "progress": r["progress"], "passengers": passengers,
                   "age": age, "live": age <= LIVE_S, "ts": r["ts"], "state": r["state"],
                   "trail": [[round(x, 1), round(y, 1), m] for m, x, y in trails[r["guid"]]]}
            if r.get("o") is not None:
                row["o"] = round(r["o"], 3)
            out.append(row)
        out.sort(key=lambda r: r["name"])
        newest_ts = time.strftime(telemetry.TS_FMT, time.localtime(newest)) if newest else None
        return {"newest": newest_ts, "epoch": newest, "source": source, "transports": out}


def last_ts(fh, size):
    fh.seek(max(0, size - 8192))
    for raw in reversed(fh.read().split(b"\n")):
        if raw[:1].isdigit():
            return raw[:19].decode("ascii", errors="replace")
    return None
