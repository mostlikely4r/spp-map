import collections
import os
import threading
import time
from datetime import datetime, timedelta

from . import telemetry

POSITIONS = "bot_positions.csv"
STALE_S = 90
TRAIL_S = 900
TRAIL_MAX = 600
TRAIL_CACHE_S = 30
TRAIL_CACHE_KEYS = 64
TRAIL_BUCKET_S = 30
TRAIL_LOG_MAX_S = 7200
BACKFILL_S = 900
POLL_MIN_S = 1.0

_trail_log = collections.OrderedDict()
_trail_log_lock = threading.Lock()

COLS = ["guid", "name", "bot", "map", "inst", "x", "y", "z", "o", "zone", "area", "level", "hp", "power",
        "flags", "leader", "money", "race", "cls", "floor", "age"]

RACES = {1: "Human", 2: "Orc", 3: "Dwarf", 4: "Night Elf", 5: "Undead", 6: "Tauren", 7: "Gnome", 8: "Troll",
         10: "Blood Elf", 11: "Draenei"}
CLASSES = {1: "Warrior", 2: "Paladin", 3: "Hunter", 4: "Rogue", 5: "Priest", 6: "Death Knight", 7: "Shaman",
           8: "Mage", 9: "Warlock", 11: "Druid"}
HORDE = {2, 5, 6, 8, 10}

EVENT_TYPES = [
    ("death", "died"),
    ("levelup", "level up"),
    ("quest_accept", "accepted"),
    ("quest_turnin", "completed"),
    ("quest_progress", "progress"),
    ("quest_fail", "failed"),
    ("quest_item", "item used"),
    ("revive", "revived"),
    ("auction", "auction"),
    ("trade", "vendor"),
    ("loot", "loot"),
    ("gear", "gear"),
    ("trainer", "trainer"),
    ("talents", "talents"),
    ("group", "group"),
    ("guild", "guild"),
    ("travel", "hearth"),
    ("pvp", "battleground"),
    ("kill", "kill"),
    ("combat", "combat"),
    ("mount", "mount check"),
    ("other", "other"),
]
EVENT_LABEL = {t[0]: t[1] for t in EVENT_TYPES}

EVENT_KINDS = {
    "AutoReleaseSpiritAction": "death", "RepopAction": "death",
    "AcceptQuestAction": "quest_accept",
    "TalkToQuestGiverAction": "quest_turnin",
    "QuestUpdateAddKillAction": "quest_progress", "QuestUpdateAddItemAction": "quest_progress",
    "QuestObjectiveCompletedAction": "quest_progress", "QuestUpdateCompleteAction": "quest_progress",
    "QuestUpdateFailedTimerAction": "quest_fail",
    "QueryItemUsageAction": "quest_item",
    "ReviveFromCorpseAction": "revive", "ReviveFromSpiritHealerAction": "revive",
    "AhAction": "auction", "AhBidAction": "auction",
    "SellAction": "trade", "BuyAction": "trade", "RepairAllAction": "trade",
    "StoreLootAction": "loot", "EquipAction": "gear", "TrainerAction": "trainer",
    "AutoSetTalentsAction": "talents",
    "AcceptInvitationAction": "group", "LeaveGroupAction": "group",
    "GuildAcceptAction": "guild", "GuildLeaveAction": "guild",
    "UseHearthStoneAction": "travel",
    "BGJoinAction": "pvp",
    "KillCreature": "kill",
    "AttackAnythingAction": "combat",
    "CheckMountStateAction": "mount",
}


def event_kind(name):
    return EVENT_KINDS.get(name, "other")


def event_label(kind):
    return EVENT_LABEL.get(kind, kind)


class CharDb:
    def __init__(self, info):
        self.info = info
        self.lock = threading.Lock()
        self.conn = None
        self.cache = {}
        self.failed_at = 0.0

    def lookup(self, guids):
        missing = [g for g in guids if g not in self.cache]
        if missing and self.info and time.time() - self.failed_at > 60:
            try:
                with self.lock:
                    if self.conn is None or not self.conn.open:
                        import pymysql
                        i = self.info
                        self.conn = pymysql.connect(host=i.host, port=i.port, user=i.user, password=i.password,
                                                    database=i.database, connect_timeout=5, autocommit=True)
                    for k in range(0, len(missing), 500):
                        chunk = missing[k:k + 500]
                        with self.conn.cursor() as cur:
                            cur.execute("select guid, race, class from characters where guid in (%s)"
                                        % ",".join(["%s"] * len(chunk)), chunk)
                            for guid, race, cls in cur.fetchall():
                                self.cache[int(guid)] = (int(race), int(cls))
                    for g in missing:
                        self.cache.setdefault(g, (0, 0))
            except Exception:
                self.conn = None
                self.failed_at = time.time()
        return {g: self.cache.get(g, (0, 0)) for g in guids}


def downsample(points):
    if len(points) <= TRAIL_MAX:
        return list(points)
    step = len(points) / TRAIL_MAX
    picked = [points[int(i * step)] for i in range(TRAIL_MAX)]
    picked[-1] = points[-1]
    return picked


def trail_from_log(path, guid, seconds, until_ts=None):
    if not path:
        return [], None
    seconds = min(max(int(seconds or TRAIL_S), 60), TRAIL_LOG_MAX_S)
    try:
        end = datetime.strptime((until_ts or telemetry.last_ts(path) or "")[:19], telemetry.TS_FMT)
    except ValueError:
        return [], None
    key = (path, guid, seconds, end.replace(second=(end.second // TRAIL_BUCKET_S) * TRAIL_BUCKET_S).strftime(telemetry.TS_FMT))
    now = time.time()
    with _trail_log_lock:
        hit = _trail_log.get(key)
        if hit and hit[0] > now:
            _trail_log.move_to_end(key)
            return hit[1], hit[2]
    points = []
    last = None
    t0 = (end - timedelta(seconds=seconds)).strftime(telemetry.TS_FMT)
    for line in telemetry.window_lines(path, t0, "9999-12-31 23:59:59", f",{guid},"):
        f = telemetry.parse_csv(line)
        if len(f) < 22:
            continue
        try:
            if int(f[2]) != guid:
                continue
            points.append([round(float(f[7]), 1), round(float(f[8]), 1), int(f[19]), int(f[5])])
        except ValueError:
            continue
        last = f[0][:19]
    points = downsample(points)
    with _trail_log_lock:
        _trail_log[key] = (now + TRAIL_CACHE_S, points, last)
        _trail_log.move_to_end(key)
        while len(_trail_log) > TRAIL_CACHE_KEYS:
            _trail_log.popitem(last=False)
    return points, last


class LiveFeed:
    def __init__(self, core, floors=None):
        self.core = core
        self.path = os.path.join(core.logs, POSITIONS) if core.logs else ""
        self.chars = CharDb(core.char_db)
        self.floors = floors
        self.lock = threading.Lock()
        self.offset = 0
        self.inode = None
        self.bots = {}
        self.trails = collections.defaultdict(collections.deque)
        self.newest = 0
        self.newest_ts = None
        self.polled = 0.0

    def reset(self):
        self.offset = 0
        self.bots.clear()
        self.trails.clear()
        self.newest = 0
        self.newest_ts = None

    def mtime(self):
        try:
            return os.path.getmtime(self.path) if self.path else 0.0
        except OSError:
            return 0.0

    def poll(self):
        with self.lock:
            now = time.time()
            if now - self.polled < POLL_MIN_S:
                return
            self.polled = now
            try:
                if not self.path:
                    raise OSError
                st = os.stat(self.path)
            except OSError:
                self.reset()
                return
            ident = (st.st_ctime_ns, getattr(st, "st_ino", 0))
            if self.inode is not None and (ident != self.inode or st.st_size < self.offset):
                self.reset()
            self.inode = ident
            if st.st_size <= self.offset:
                return
            with open(self.path, "rb") as fh:
                if self.offset == 0 and st.st_size > 4 * 1024 * 1024:
                    target = telemetry.last_ts(self.path, tail=8192)
                    if target:
                        start = (datetime.strptime(target, telemetry.TS_FMT) - timedelta(seconds=BACKFILL_S)).strftime(telemetry.TS_FMT)
                        telemetry.seek_time(fh, st.st_size, start)
                else:
                    fh.seek(self.offset)
                data = fh.read(st.st_size - fh.tell())
            end = data.rfind(b"\n")
            if end < 0:
                return
            consumed = st.st_size - len(data) + end + 1
            self.offset = consumed
            for raw in data[:end].split(b"\n"):
                self.ingest(raw)
            self.expire()

    def ingest(self, raw):
        if not raw[:1].isdigit():
            return
        f = raw.decode("utf-8", errors="replace").rstrip("\r").split(",")
        if len(f) < 22:
            return
        try:
            epoch = int(f[1])
            guid = int(f[2])
            row = {
                "guid": guid, "name": f[3], "bot": f[4] == "1", "map": int(f[5]), "inst": int(f[6]),
                "x": float(f[7]), "y": float(f[8]), "z": float(f[9]),
                "wmo": (int(f[11]), int(f[12])), "o": float(f[13]), "zone": int(f[14]), "area": int(f[15]),
                "level": int(f[16]), "hp": int(float(f[17])), "power": int(float(f[18])), "flags": int(f[19]),
                "leader": int(f[20]), "money": int(f[21]), "epoch": epoch, "ts": f[0][:19],
            }
        except ValueError:
            return
        self.bots[guid] = row
        trail = self.trails[guid]
        trail.append((epoch, row["map"], row["x"], row["y"], row["flags"]))
        while len(trail) > TRAIL_MAX:
            trail.popleft()
        if epoch > self.newest:
            self.newest = epoch
            self.newest_ts = row["ts"]

    def expire(self):
        cutoff = self.newest - TRAIL_S
        for guid in list(self.trails):
            trail = self.trails[guid]
            while trail and trail[0][0] < cutoff:
                trail.popleft()
            if not trail:
                del self.trails[guid]
        gone = self.newest - 3 * STALE_S
        for guid in [g for g, r in self.bots.items() if r["epoch"] < gone]:
            del self.bots[guid]

    def is_live(self):
        return time.time() - self.mtime() < 120

    def floor_of(self, row):
        if not self.floors or row["wmo"][1] <= 0:
            return None
        floor = self.floors.get(row["wmo"])
        return floor["floor"] if floor and floor["map"] == row["map"] else None

    def snapshot(self, map_id=None):
        self.poll()
        with self.lock:
            rows = [r for r in self.bots.values() if self.newest - r["epoch"] <= STALE_S]
            newest, newest_ts = self.newest, self.newest_ts
        maps = collections.Counter(r["map"] for r in rows)
        players = collections.Counter(r["map"] for r in rows if not r["bot"])
        if map_id is not None:
            rows = [r for r in rows if r["map"] == map_id]
        info = self.chars.lookup([r["guid"] for r in rows])
        out = []
        for r in rows:
            race, cls = info.get(r["guid"], (0, 0))
            out.append([r["guid"], r["name"], 1 if r["bot"] else 0, r["map"], r["inst"], round(r["x"], 1),
                        round(r["y"], 1), round(r["z"], 1), r["o"], r["zone"], r["area"], r["level"], r["hp"],
                        r["power"], r["flags"], r["leader"], r["money"], race, cls, self.floor_of(r),
                        newest - r["epoch"]])
        return {
            "core": self.core.name, "live": self.is_live(), "newest": newest_ts, "epoch": newest,
            "maps": [{"id": m, "bots": n, "players": players.get(m, 0)} for m, n in maps.most_common()],
            "cols": COLS, "bots": out,
        }

    def log_trail(self, guid, seconds, newest_ts):
        return trail_from_log(self.path, guid, seconds, newest_ts)[0]

    def bot(self, guid, seconds=None):
        self.poll()
        with self.lock:
            row = self.bots.get(guid)
            trail = list(self.trails.get(guid, ()))
            newest = self.newest
            newest_ts = self.newest_ts
        if not row:
            return None
        ring = [[t[2], t[3], t[4], t[1]] for t in trail]
        if seconds and seconds > TRAIL_S:
            trail_out = self.log_trail(guid, seconds, newest_ts) or ring
        else:
            cutoff = newest - (seconds or TRAIL_S)
            trail_out = [[t[2], t[3], t[4], t[1]] for t in trail if not seconds or t[0] >= cutoff]
        race, cls = self.chars.lookup([guid]).get(guid, (0, 0))
        out = {k: v for k, v in row.items() if k != "wmo"}
        out.update({"race": race, "cls": cls, "race_name": RACES.get(race, ""), "class_name": CLASSES.get(cls, ""),
                    "faction": "Horde" if race in HORDE else ("Alliance" if race else ""),
                    "age": newest - row["epoch"], "floor": self.floor_of(row),
                    "state": telemetry.flags_text(row["flags"])})
        leader = self.bots.get(row["leader"]) if row["leader"] else None
        out["leader_name"] = leader["name"] if leader else ("" if not row["leader"] else str(row["leader"]))
        out["trail"] = trail_out
        out["seconds"] = seconds or TRAIL_S
        return out

    def find(self, text, limit=20):
        self.poll()
        text = text.lower()
        with self.lock:
            hits = [r for r in self.bots.values() if text in r["name"].lower()]
        hits.sort(key=lambda r: (not r["name"].lower().startswith(text), r["bot"], r["name"]))
        return [{"guid": r["guid"], "name": r["name"], "map": r["map"], "level": r["level"], "bot": r["bot"]}
                for r in hits[:limit]]


def recent_events(logs, newest_ts, map_id, seconds=1800, name=None, kinds=None, limit=2000):
    path = os.path.join(logs, "bot_events.csv")
    if not os.path.exists(path):
        return []
    newest_ts = telemetry.last_ts(path, tail=8192) or newest_ts
    if not newest_ts:
        return []
    t0 = (datetime.strptime(newest_ts, telemetry.TS_FMT) - timedelta(seconds=seconds)).strftime(telemetry.TS_FMT)
    needle = f",{name}," if name else None
    out = []
    last_level = {}
    for line in telemetry.window_lines(path, t0, "9999-12-31 23:59:59", needle):
        f = telemetry.parse_csv(line)
        if len(f) < 15:
            continue
        if name and f[1] != name:
            continue
        kind = event_kind(f[2])
        try:
            level = int(float(f[6]))
        except ValueError:
            level = 0
        if f[2] == "XpGainAction":
            prev = last_level.get(f[1])
            last_level[f[1]] = level
            kind = "levelup" if prev is not None and level > prev else "other"
        w = telemetry.world_of(f, 9)
        if not w or (map_id is not None and w[0] != map_id):
            continue
        if kinds and kind not in kinds:
            continue
        try:
            guid = int(f[14])
        except ValueError:
            guid = 0
        out.append({"t": f[0][:19], "bot": f[1], "guid": guid, "event": f[2], "kind": kind,
                    "label": event_label(kind), "level": level, "map": w[0],
                    "detail": ", ".join(x for x in f[7:9] if x)[:120], "x": w[1], "y": w[2]})
    return out[-limit:]


DB_TRAIL_MAX = 240
ACCOUNTS_TTL_S = 300


class DbFeed:
    def __init__(self, core):
        self.core = core
        self.info = core.char_db
        self.login = core.login_db
        self.lock = threading.Lock()
        self.bots = {}
        self.trails = collections.defaultdict(collections.deque)
        self.polled = 0.0
        self.ok_at = 0.0
        self.error = ""
        self.bot_accounts = set()
        self.accounts_at = 0.0
        self.newest = 0
        self.newest_ts = None
        self.guilds = {}
        self.log_path = os.path.join(core.logs, POSITIONS) if core.logs else ""
        self.log_ts = None
        self.log_at = 0.0

    def log_anchor(self):
        now = time.time()
        if now - self.log_at > 60:
            self.log_at = now
            self.log_ts = telemetry.last_ts(self.log_path) if self.log_path else None
        return self.log_ts

    @staticmethod
    def connect(info):
        import pymysql
        return pymysql.connect(host=info.host, port=info.port, user=info.user, password=info.password,
                               database=info.database, connect_timeout=5, read_timeout=15, autocommit=True)

    def refresh_accounts(self):
        if not self.login or not self.core.bot_prefix or time.time() - self.accounts_at < ACCOUNTS_TTL_S:
            return
        self.accounts_at = time.time()
        try:
            conn = self.connect(self.login)
            try:
                with conn.cursor() as cur:
                    cur.execute("select id from account where username like %s", (self.core.bot_prefix + "%",))
                    self.bot_accounts = {int(r[0]) for r in cur.fetchall()}
            finally:
                conn.close()
        except Exception:
            pass

    def fetch(self):
        gid = "guild_id" if self.core.flavor == "vmangos" else "guildid"
        conn = self.connect(self.info)
        try:
            with conn.cursor() as cur:
                cur.execute("select guid, account, name, race, class, level, map, zone, position_x, position_y,"
                            " position_z, orientation, money, health from characters where online = 1")
                rows = cur.fetchall()
                try:
                    cur.execute(f"select m.guid, g.name from guild_member m join guild g on g.{gid} = m.{gid}"
                                " join characters c on c.guid = m.guid where c.online = 1")
                    guilds = {int(g): n for g, n in cur.fetchall()}
                except Exception:
                    guilds = {}
        finally:
            conn.close()
        return rows, guilds

    def poll(self):
        with self.lock:
            now = time.time()
            if not self.info or now - self.polled < self.core.db_poll_s:
                return
            self.polled = now
        self.refresh_accounts()
        try:
            rows, guilds = self.fetch()
        except Exception as exc:
            with self.lock:
                self.error = str(exc)[:200]
            return
        with self.lock:
            self.guilds = guilds
            self.error = ""
            self.ok_at = now
            epoch = int(now)
            self.newest = epoch
            self.newest_ts = time.strftime(telemetry.TS_FMT, time.localtime(now))
            seen = set()
            for guid, account, name, race, cls, level, map_id, zone, x, y, z, o, money, health in rows:
                guid = int(guid)
                seen.add(guid)
                alive = (health or 0) > 0
                row = {"guid": guid, "name": name, "bot": int(account) in self.bot_accounts, "map": int(map_id),
                       "inst": 0, "x": float(x), "y": float(y), "z": float(z), "o": float(o), "zone": int(zone or 0),
                       "area": 0, "level": int(level), "hp": 100 if alive else 0, "power": 0, "flags": 16 if alive else 2,
                       "leader": 0, "money": int(money or 0), "race": int(race), "cls": int(cls), "epoch": epoch}
                prev = self.bots.get(guid)
                if prev is None or (prev["x"], prev["y"], prev["map"]) != (row["x"], row["y"], row["map"]):
                    row["moved"] = epoch
                    trail = self.trails[guid]
                    trail.append((epoch, row["map"], row["x"], row["y"], row["flags"]))
                    while len(trail) > DB_TRAIL_MAX:
                        trail.popleft()
                else:
                    row["moved"] = prev["moved"]
                self.bots[guid] = row
            for guid in [g for g in self.bots if g not in seen]:
                del self.bots[guid]
                self.trails.pop(guid, None)

    def is_live(self):
        return time.time() - self.ok_at < 3 * max(self.core.db_poll_s, 20)

    def mtime(self):
        return self.ok_at

    def snapshot(self, map_id=None):
        self.poll()
        with self.lock:
            rows = list(self.bots.values())
            newest, newest_ts = self.newest, self.newest_ts
        maps = collections.Counter(r["map"] for r in rows)
        players = collections.Counter(r["map"] for r in rows if not r["bot"])
        if map_id is not None:
            rows = [r for r in rows if r["map"] == map_id]
        out = [[r["guid"], r["name"], 1 if r["bot"] else 0, r["map"], r["inst"], round(r["x"], 1), round(r["y"], 1),
                round(r["z"], 1), r["o"], r["zone"], r["area"], r["level"], r["hp"], r["power"], r["flags"], r["leader"],
                r["money"], r["race"], r["cls"], None, newest - r["moved"]] for r in rows]
        return {
            "core": self.core.name, "live": self.is_live(), "newest": newest_ts, "epoch": newest, "source": "db",
            "poll_s": self.core.db_poll_s, "error": self.error,
            "maps": [{"id": m, "bots": n, "players": players.get(m, 0)} for m, n in maps.most_common()],
            "cols": COLS, "bots": out,
        }

    def bot(self, guid, seconds=None):
        self.poll()
        with self.lock:
            row = self.bots.get(guid)
            trail = list(self.trails.get(guid, ()))
            newest = self.newest
        if not row:
            return None
        points, until = trail_from_log(self.log_path, guid, seconds or TRAIL_S, self.log_anchor())
        race = row["race"]
        out = {k: v for k, v in row.items() if k != "moved"}
        out.update({"race_name": RACES.get(race, ""), "class_name": CLASSES.get(row["cls"], ""),
                    "faction": "Horde" if race in HORDE else ("Alliance" if race else ""), "age": newest - row["moved"],
                    "floor": None, "state": "position at last save", "source": "db", "leader_name": "",
                    "guild": self.guilds.get(guid, ""), "ts": self.newest_ts})
        out["trail"] = points or [[t[2], t[3], t[4], t[1]] for t in trail]
        out["trail_until"] = until if points else None
        out["seconds"] = seconds or TRAIL_S
        return out

    def find(self, text, limit=20):
        self.poll()
        text = text.lower()
        with self.lock:
            hits = [r for r in self.bots.values() if text in r["name"].lower()]
        hits.sort(key=lambda r: (not r["name"].lower().startswith(text), r["bot"], r["name"]))
        return [{"guid": r["guid"], "name": r["name"], "map": r["map"], "level": r["level"], "bot": r["bot"]}
                for r in hits[:limit]]
