import csv
import io
import os
import re
from datetime import datetime, timedelta

from .clientdata import dbc_string, read_dbc

CONTINENT_OFFSETS = {0: (0.0, 0.0), 1: (-3680.0, 13670.0), 530: (15000.0, -20000.0), 571: (10000.0, 5000.0)}
POINT_RE = re.compile(r"POINT\((-?[\d.]+) (-?[\d.]+)\)")
TS_FMT = "%Y-%m-%d %H:%M:%S"
DEFAULT_LOOKBACK = 1800
TELEPORT_JUMP = 250.0


def ts_of(line):
    return line[:19]


def seek_time(fh, size, target):
    lo, hi = 0, size
    while hi - lo > 65536:
        mid = (lo + hi) // 2
        fh.seek(mid)
        fh.readline()
        line = fh.readline()
        if not line:
            hi = mid
            continue
        if ts_of(line.decode("utf-8", errors="replace")) < target:
            lo = mid
        else:
            hi = mid
    fh.seek(lo)
    if lo:
        fh.readline()


def window_lines(path, t0, t1, needle=None):
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    if size == 0:
        return
    needle_b = needle.encode("utf-8") if needle else None
    with open(path, "rb") as fh:
        seek_time(fh, size, t0)
        for raw in fh:
            if needle_b and needle_b not in raw:
                if raw[:19].decode("ascii", errors="replace") > t1:
                    break
                continue
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            ts = ts_of(line)
            if ts < t0:
                continue
            if ts > t1:
                break
            yield line


def parse_csv(line):
    try:
        return next(csv.reader(io.StringIO(line)))
    except (StopIteration, csv.Error):
        return []


def point_of(text):
    m = POINT_RE.search(text or "")
    return (float(m.group(1)), float(m.group(2))) if m else None


def load_offsets(logs):
    offsets = dict(CONTINENT_OFFSETS)
    path = os.path.join(logs, "map_offsets.csv")
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                offsets[int(row["mapId"])] = (float(row["offsetX"]), float(row["offsetY"]))
    except (OSError, KeyError, ValueError):
        pass
    return offsets


def display_to_world(dx, dy, offset):
    off_x, off_y = offset
    return dy - off_x, -dx - off_y


def map_names(dbc_dir):
    try:
        recs, strings = read_dbc(os.path.join(dbc_dir, "Map.dbc"))
    except OSError:
        return []
    out = []
    for r in recs:
        names = set()
        for v in r[1:]:
            if 0 < v < len(strings) and strings[v - 1:v] == b"\0":
                s = dbc_string(strings, v)
                if s and len(s) > 2:
                    names.add(s.lower().replace(" ", "_"))
        out.append((r[0], names))
    return out


def scenario_map(dbc_dir, subject):
    if not subject:
        return None
    best = None
    for map_id, names in map_names(dbc_dir):
        for n in names:
            if subject.startswith(n + "_") or subject == n:
                if best is None or len(n) > best[1]:
                    best = (map_id, len(n))
    return best[0] if best else None


def window_for(run):
    end = datetime.strptime(run["ts"], TS_FMT)
    lookback = (run["elapsed_s"] or DEFAULT_LOOKBACK) + 30
    return (end - timedelta(seconds=lookback)).strftime(TS_FMT), (end + timedelta(seconds=15)).strftime(TS_FMT)


def collect(logs, host, t0, t1):
    track, events, states = [], [], []
    needle = f",{host},"
    for line in window_lines(os.path.join(logs, "bot_heartbeat.csv"), t0, t1, needle):
        f = parse_csv(line)
        if len(f) >= 7 and f[1] == host:
            p = point_of(f[6])
            if p:
                track.append({"t": f[0][:19], "d": p, "state": f[5], "level": f[2], "src": "heartbeat"})
    for line in window_lines(os.path.join(logs, "bot_states.csv"), t0, t1, needle):
        f = parse_csv(line)
        if len(f) >= 8 and f[1] == host:
            p = point_of(f[7])
            if p:
                states.append({"t": f[0][:19], "d": p, "state": "/".join(f[2:6]), "target": f[6], "src": "states"})
    for line in window_lines(os.path.join(logs, "bot_events.csv"), t0, t1, needle):
        f = parse_csv(line)
        if len(f) >= 4 and f[1] == host:
            events.append({"t": f[0][:19], "d": point_of(f[3]), "event": f[2],
                           "detail": ", ".join(x for x in f[4:] if x)[:200]})
    if not track:
        track = states
    return track, events, states


def trim_to_test_start(track, elapsed_known):
    if elapsed_known or len(track) < 2:
        return track, None
    cut = 0
    for i in range(1, len(track)):
        (ax, ay), (bx, by) = track[i - 1]["d"], track[i]["d"]
        if ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5 > TELEPORT_JUMP:
            cut = i
    return track[cut:], (track[cut]["t"] if cut else None)
