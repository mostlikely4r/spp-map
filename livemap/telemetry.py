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


def last_ts(path, tail=65536):
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if not size:
        return None
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, size - tail))
            lines = fh.read().split(b"\n")
    except OSError:
        return None
    for raw in reversed(lines):
        if len(raw) >= 19 and raw[:1].isdigit():
            return raw[:19].decode("ascii", errors="replace")
    return None


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
                if raw[:1].isdigit() and raw[:19].decode("ascii", errors="replace") > t1:
                    break
                continue
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line[:1].isdigit():
                continue
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


ARCHIVE_RE = re.compile(r"_\d{8}_\d{6}$")


def stream_files(logs, filename):
    stem, ext = os.path.splitext(filename)
    try:
        names = os.listdir(logs)
    except OSError:
        return []
    out = []
    for name in names:
        s, e = os.path.splitext(name)
        if e != ext:
            continue
        if s == stem or (len(s) == len(stem) + 16 and s.startswith(stem + "_") and ARCHIVE_RE.search(s)):
            out.append(os.path.join(logs, name))
    return sorted(out)


def stream_lines(logs, filename, t0, t1, needle=None):
    for path in stream_files(logs, filename):
        yield from window_lines(path, t0, t1, needle)


def world_of(fields, start):
    try:
        return (int(fields[start]), float(fields[start + 2]), float(fields[start + 3]), float(fields[start + 4]))
    except (IndexError, ValueError):
        return None


def wmo_of(fields, start):
    try:
        return (int(fields[start]), int(fields[start + 1]))
    except (IndexError, ValueError):
        return None


FLAG_NAMES = ((1, "combat"), (2, "dead"), (4, "ghost"), (8, "group"), (32, "delayed"), (64, "taxi"), (128, "test"))


def flags_text(flags):
    return " ".join(n for bit, n in FLAG_NAMES if flags & bit) or "idle"


def collect(logs, host, t0, t1):
    heartbeat, positions, events, states = [], [], [], []
    needle = f",{host},"
    for line in stream_lines(logs, "bot_heartbeat.csv", t0, t1, needle):
        f = parse_csv(line)
        if len(f) >= 7 and f[1] == host:
            p = point_of(f[6])
            if p:
                heartbeat.append({"t": f[0][:19], "d": p, "w": world_of(f, 7), "wmo": wmo_of(f, 13),
                                  "state": f[5], "level": f[2], "src": "heartbeat"})
    for line in stream_lines(logs, "bot_positions.csv", t0, t1, needle):
        f = parse_csv(line)
        if len(f) >= 22 and f[3] == host:
            try:
                w = (int(f[5]), float(f[7]), float(f[8]), float(f[9]))
                flags = int(f[19])
            except ValueError:
                continue
            positions.append({"t": f[0][:19], "d": None, "w": w, "wmo": wmo_of(f, 11),
                              "state": flags_text(flags), "level": f[16], "src": "positions"})
    for line in stream_lines(logs, "bot_states.csv", t0, t1, needle):
        f = parse_csv(line)
        if len(f) >= 8 and f[1] == host:
            p = point_of(f[7])
            if p:
                states.append({"t": f[0][:19], "d": p, "w": world_of(f, 8), "state": "/".join(f[2:6]),
                               "target": f[6], "src": "states"})
    for line in stream_lines(logs, "bot_events.csv", t0, t1, needle):
        f = parse_csv(line)
        if len(f) >= 4 and f[1] == host:
            events.append({"t": f[0][:19], "d": point_of(f[3]), "w": world_of(f, 9), "event": f[2],
                           "detail": ", ".join(x for x in f[7:9] if x)[:200]})
    track = heartbeat or positions or states
    return track, events, states


def test_records(logs, host, test, end_ts):
    end = datetime.strptime(end_ts, TS_FMT)
    lo = (end - timedelta(seconds=5)).strftime(TS_FMT)
    hi = (end + timedelta(seconds=5)).strftime(TS_FMT)
    start_lo = (end - timedelta(hours=6)).strftime(TS_FMT)
    rows = []
    for line in stream_lines(logs, "bot_test_runs.csv", start_lo, hi, f",{host},"):
        f = parse_csv(line)
        if len(f) >= 17 and f[4] == host and f[5] == test:
            rows.append(f)
    end_row = next((r for r in reversed(rows) if r[2] == "END" and lo <= r[0][:19] <= hi), None)
    if end_row is None:
        return None
    start_row = next((r for r in reversed(rows) if r[2] == "START" and r[0][:19] <= end_row[0][:19]), None)
    out = {
        "end": end_row[0][:19], "verdict": end_row[6], "elapsed_ms": int(end_row[7] or 0),
        "end_world": world_of(end_row, 8), "msg": end_row[14], "last_action": end_row[15],
        "travel_target": end_row[16],
    }
    if start_row:
        out["start"] = start_row[0][:19]
        out["start_world"] = world_of(start_row, 8)
    return out


def xy(p):
    if p.get("w"):
        return p["w"][1], p["w"][2]
    return p["d"]


TELEPORT_SPEED = 60.0


def cut_teleports(track):
    cut = 0
    for i in range(1, len(track)):
        a, b = track[i - 1], track[i]
        if not (a.get("w") and b.get("w")):
            continue
        if a["w"][0] != b["w"][0]:
            cut = i
            continue
        if "taxi" in (a.get("state") or "") or "taxi" in (b.get("state") or ""):
            continue
        dist = ((a["w"][1] - b["w"][1]) ** 2 + (a["w"][2] - b["w"][2]) ** 2) ** 0.5
        dt = max(1.0, (datetime.strptime(b["t"], TS_FMT) - datetime.strptime(a["t"], TS_FMT)).total_seconds())
        if dist > TELEPORT_JUMP and dist / dt > TELEPORT_SPEED:
            cut = i
    return track[cut:], (track[cut]["t"] if cut else None)


def trim_to_test_start(track, elapsed_known):
    if elapsed_known or len(track) < 2:
        return track, None
    cut = 0
    for i in range(1, len(track)):
        (ax, ay), (bx, by) = xy(track[i - 1]), xy(track[i])
        if ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5 > TELEPORT_JUMP:
            cut = i
    return track[cut:], (track[cut]["t"] if cut else None)
