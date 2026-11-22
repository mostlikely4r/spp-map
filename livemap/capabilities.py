import glob
import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field

from . import botconf
from .clientdata import archive_order

TTL_S = 30
FRESH_S = 120
LEVELS = ["L0", "L1", "L2", "L3", "L4", "L5"]
LEVEL_TEXT = {
    "L0": "browse the world and dungeon maps",
    "L1": "online players, NPCs and objects from your database",
    "L2": "bots on the map (from the characters database)",
    "L3": "bot travel, zone levels, teleport cache and events from the bot log exports",
    "L4": "the real live map: positions every few seconds, trails, follow",
    "L5": "the bot test explorer: test list, tests on the map, run pages",
}

EXPORT_GROUPS = {
    "live": {"label": "live positions + test runs", "files": ["livemap_manifest.json", "bot_positions.csv", "bot_test_runs.csv"],
             "note": "about 1 GB a day at 3000 bots; turn on log archiving to keep old boots in check"},
    "travel": {"label": "travel layers", "files": ["travel_map.csv", "telecache.csv", "map_offsets.csv"]},
    "events": {"label": "deaths, quests and level-ups", "files": ["bot_events.csv"]},
    "zones": {"label": "zone levels", "files": ["zones.csv"]},
}

LAYERS = {
    "bots": ("Bots and players", ["positions", "char_db"]),
    "names": ("Names over dots", ["positions", "char_db"]),
    "labels": ("Zone and map names", ["dbc"]),
    "zones": ("Zone outlines (painted art)", ["client", "dbc"]),
    "dungeons": ("Dungeons at their entrances", ["client", "dbc"]),
    "steep": ("Too steep to walk", ["client"]),
    "swim": ("Swimming water", ["client"]),
    "events": ("Deaths, quests, level-ups", ["exp_events"]),
    "intents": ("Travel intent", ["exp_travel"]),
    "tele": ("Teleport cache", ["exp_telecache"]),
    "zonelv": ("Zone levels", ["exp_zones"]),
    "npcs": ("NPCs", ["world_db"]),
    "objects": ("Game objects", ["world_db"]),
    "nodes": ("Travel network", ["travel_db"]),
    "tests": ("Tests run here", ["tests"]),
}


@dataclass
class Capability:
    id: str
    label: str
    level: str
    status: str = "missing"
    source: str = ""
    detail: str = ""
    fix: list = field(default_factory=list)


def step(text, snippet=None, file=None, setting=None, action=None):
    out = {"text": text}
    if snippet:
        out["snippet"] = snippet
    if file:
        out["file"] = file
    if setting:
        out["setting"] = setting
    if action:
        out["action"] = action
    return out


def tpath(path):
    return (path or "").replace("\\", "/")


def age_text(seconds):
    if seconds < 120:
        return f"{int(seconds)} s old"
    if seconds < 7200:
        return f"{int(seconds // 60)} min old"
    if seconds < 172800:
        return f"{int(seconds // 3600)} h old"
    return f"{int(seconds // 86400)} days old"


def file_age(path):
    try:
        return time.time() - os.path.getmtime(path)
    except OSError:
        return None


def client_build(paths):
    names = {os.path.basename(p).lower() for p in paths}
    if any(n.startswith("lichking") for n in names):
        return 12340, "wotlk"
    if any(n.startswith("expansion") for n in names):
        return 8606, "tbc"
    return 5875, "classic"


def allowed_logs(bot_conf):
    try:
        with open(bot_conf, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = re.match(r"^\s*AiPlayerbot\.AllowedLogFiles\s*=\s*(.*?)\s*$", line)
                if m:
                    return [f.strip() for f in m.group(1).split(",") if f.strip()]
    except OSError:
        return None
    return []


def db_probe(info, sql):
    import pymysql
    conn = pymysql.connect(host=info.host, port=info.port, user=info.user, password=info.password,
                           database=info.database, connect_timeout=2, read_timeout=5, autocommit=True)
    try:
        with conn.cursor() as cur:
            out = []
            for q in sql:
                cur.execute(q)
                out.append(cur.fetchall())
            return out
    finally:
        conn.close()


def db_error(exc):
    text = str(exc)
    m = re.search(r"\((\d+), ['\"](.*?)['\"]\)", text)
    return m.group(2) if m else text[:200]


class Probe:
    def __init__(self, app, ctx):
        self.app = app
        self.ctx = ctx
        self.core = ctx.core
        self.lock = threading.Lock()
        self.cached = None
        self.at = 0.0

    def report(self, refresh=False):
        with self.lock:
            if refresh or self.cached is None or time.time() - self.at > TTL_S:
                self.cached = self.build()
                self.at = time.time()
            return self.cached

    def build(self):
        caps = {}
        self.listed = None
        self.sample_s = 5.0
        probes = (("client", self.p_client), ("dbc", self.p_dbc), ("world_db", self.p_world_db), ("char_db", self.p_char_db),
                  ("bot_module", self.p_bot_module), ("exports", self.p_exports), ("contract", self.p_contract),
                  ("positions", self.p_positions), ("tests", self.p_tests), ("archive", self.p_archive))
        for name, fn in probes:
            try:
                found = fn(caps)
            except Exception as exc:
                ids = ["exp_travel", "exp_telecache", "exp_events", "exp_zones"] if name == "exports" else \
                    (["world_db", "travel_db"] if name == "world_db" else [name])
                found = [Capability(i, i, "L0", "error", "", f"probe failed: {exc}") for i in ids]
            for cap in found:
                caps[cap.id] = cap
        layers = {}
        for key, (label, needs) in LAYERS.items():
            layers[key] = self.layer_status(label, [caps[n] for n in needs if n in caps], key)
        ladder = self.ladder(caps)
        reached = [l["level"] for l in ladder if l["ok"]]
        return {
            "core": self.core.name,
            "flavor": self.core.flavor,
            "expansion": self.core.expansion,
            "level": reached[-1] if reached else None,
            "ladder": ladder,
            "caps": [asdict(c) for c in caps.values()],
            "layers": layers,
            "features": {
                "tests": caps["tests"].status == "ok",
                "map": caps["client"].status == "ok" or caps["world_db"].status == "ok" or caps["char_db"].status == "ok",
                "live": caps["positions"].status == "ok",
                "players": caps["positions"].status in ("ok", "degraded"),
            },
            "checked": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    @staticmethod
    def layer_status(label, caps, key):
        if not caps:
            return {"label": label, "status": "missing", "source": "", "detail": "", "needs": []}
        if key in ("bots", "names"):
            pos, char = caps[0], caps[1] if len(caps) > 1 else None
            if pos.status == "ok":
                return {"label": label, "status": "ok", "source": pos.source, "detail": pos.detail, "needs": [pos.id]}
            if char and char.status == "ok" and pos.status == "degraded":
                return {"label": label, "status": "degraded", "source": pos.source, "detail": pos.detail, "needs": [pos.id]}
            return {"label": label, "status": "missing", "source": "", "detail": pos.detail, "needs": [pos.id]}
        worst = "ok"
        for c in caps:
            if c.status in ("missing", "error"):
                worst = "missing"
            elif c.status == "degraded" and c.id != "dbc" and worst == "ok":
                worst = "degraded"
        src = ", ".join(c.source for c in caps if c.source)
        detail = "; ".join(c.detail for c in caps if c.status != "ok" and c.detail) or caps[0].detail
        return {"label": label, "status": worst, "source": src, "detail": detail,
                "needs": [c.id for c in caps if c.status != "ok"] or [caps[0].id]}

    def ladder(self, caps):
        ok = lambda *ids: all(caps[i].status in ("ok", "degraded") for i in ids)
        exports = [k for k in ("exp_travel", "exp_events", "exp_zones", "exp_telecache") if caps[k].status == "ok"]
        rows = [
            ("L0", ok("client")),
            ("L1", ok("world_db") or ok("char_db")),
            ("L2", ok("char_db") and caps["bot_module"].status == "ok"),
            ("L3", bool(exports)),
            ("L4", caps["contract"].status == "ok"),
            ("L5", caps["tests"].status == "ok"),
        ]
        return [{"level": l, "ok": bool(v), "text": LEVEL_TEXT[l]} for l, v in rows]

    def toml_hint(self):
        return self.app.cfg.path or "livemap.toml"

    def p_client(self, caps):
        cap = Capability("client", "Game client (map art)", "L0")
        path = self.app.client_dir(self.core.name)
        if not path:
            cap.detail = "no client Data folder is set for this core"
            cap.fix = [step("Point the core at the Data folder of your game client (the one with the .MPQ files).",
                            snippet=f'client = "C:/Games/WoW/Data"', file=self.toml_hint()),
                       step("Or set it on the Settings part of this page under Client data folders.")]
            return [cap]
        if not os.path.isdir(path):
            cap.status = "error"
            cap.detail = f"folder not found: {path}"
            cap.fix = [step("Fix the client path; it must be the Data folder inside the game folder.", file=self.toml_hint())]
            return [cap]
        paths = archive_order(path, self.core.locale)
        if not paths:
            cap.status = "error"
            cap.detail = f"no .MPQ files in {path}"
            cap.fix = [step("Use the Data folder itself (it holds common.MPQ / patch.MPQ), not the game folder.")]
            return [cap]
        build, exp = client_build(paths)
        cap.status = "ok"
        cap.source = path
        cap.detail = f"{len(paths)} archives, client build {build} ({exp})"
        if not self.core.expansion:
            self.core.expansion = exp
        return [cap]

    def p_dbc(self, caps):
        cap = Capability("dbc", "Zone and map tables (DBC)", "L0")
        if self.core.dbc and os.path.isfile(os.path.join(self.core.dbc, "Map.dbc")):
            cap.status = "ok"
            cap.source = self.core.dbc
            cap.detail = "DBC folder found"
            if self.core.dbc_from_client:
                cap.status = "degraded"
                cap.source = "client MPQ"
                cap.detail = "read from the game client (no server dbc folder found)"
            return [cap]
        cap.detail = f"no Map.dbc in {self.core.dbc}" if self.core.dbc else "no DBC folder set"
        if caps["client"].status == "ok":
            cap.detail += "; the client archives could not provide the tables"
        cap.fix = [step("Point dbc at your server's dbc folder (the one the world server loads).",
                        snippet='dbc = "C:/Server/data/dbc"', file=self.toml_hint()),
                   step("Or set a game client: the tables are read from its archives.")]
        return [cap]

    def p_world_db(self, caps):
        cap = Capability("world_db", "World database (NPCs, objects, quests)", "L1")
        travel = Capability("travel_db", "Bot travel network (world DB)", "L3")
        info = self.core.world_db
        if not info:
            cap.detail = "no world database configured"
            cap.fix = [step("Give the path to your mangosd.conf; the database login is read from it.",
                            snippet='mangosd_conf = "C:/Server/mangosd.conf"', file=self.toml_hint()),
                       step("Or give the login directly (host;port;user;password;database).",
                            snippet='world_db = "127.0.0.1;3306;mangos;mangos;mangos"', file=self.toml_hint())]
            travel.detail = "needs the world database"
            travel.fix = [step("Set up the world database first.")]
            return [cap, travel]
        try:
            res = db_probe(info, ["select 1", "show tables like 'ai_playerbot_travelnode'", "show tables like 'migrations'"])
        except Exception as exc:
            cap.status = "error"
            cap.detail = f"{info.host}:{info.port}/{info.database}: {db_error(exc)}"
            cap.fix = [step("Check that MySQL is running and the login in the conf is right.")]
            travel.detail = "world database not reachable"
            return [cap, travel]
        cap.status = "ok"
        cap.source = f"{info.database} on {info.host}:{info.port}"
        cap.detail = "connected"
        if res[2] and not self.core.flavor:
            self.core.flavor = "vmangos"
        if res[1]:
            travel.status = "ok"
            travel.source = cap.source
            travel.detail = "ai_playerbot_travelnode present"
        else:
            travel.detail = "no ai_playerbot_travelnode table in the world database"
            if self.core.flavor == "vmangos":
                travel.detail += " (not available on vmangos yet, BL-V11)"
                travel.fix = [step("Not available on vmangos yet: the fork has no travel-node tables (tracked as BL-V11).")]
            else:
                travel.fix = [step("Install the PlayerBots world SQL (it creates ai_playerbot_travelnode*), or let the bots generate the nodes once.")]
        return [cap, travel]

    def p_char_db(self, caps):
        cap = Capability("char_db", "Characters database (who is online)", "L1")
        info = self.core.char_db
        if not info:
            cap.detail = "no characters database configured"
            cap.fix = [step("Give the path to your mangosd.conf; CharacterDatabaseInfo / CharacterDatabase.Info is read from it.",
                            snippet='mangosd_conf = "C:/Server/mangosd.conf"', file=self.toml_hint()),
                       step("Or give the login directly.", snippet='char_db = "127.0.0.1;3306;mangos;mangos;characters"',
                            file=self.toml_hint())]
            return [cap]
        try:
            res = db_probe(info, ["select count(*) from characters where online = 1", "select count(*) from characters"])
        except Exception as exc:
            cap.status = "error"
            cap.detail = f"{info.host}:{info.port}/{info.database}: {db_error(exc)}"
            cap.fix = [step("Check that MySQL is running and the login in the conf is right.")]
            return [cap]
        cap.status = "ok"
        cap.source = f"{info.database} on {info.host}:{info.port}"
        cap.detail = f"{res[0][0][0]} online of {res[1][0][0]} characters"
        return [cap]

    def p_bot_module(self, caps):
        cap = Capability("bot_module", "PlayerBots module", "L2")
        path = self.core.bot_conf
        if not path or not os.path.isfile(path):
            cap.detail = "no aiplayerbot.conf found" + (f" at {path}" if path else "")
            cap.fix = [step("If your server runs PlayerBots, point at its aiplayerbot.conf (it usually sits next to mangosd.conf).",
                            snippet='aiplayerbot_conf = "C:/Server/aiplayerbot.conf"', file=self.toml_hint()),
                       step("No PlayerBots? Everything up to level L1 still works; bots need the module.")]
            return [cap]
        cap.status = "ok"
        cap.source = path
        listed = allowed_logs(path) or []
        cap.detail = f"{len(listed)} log exports listed in AllowedLogFiles"
        self.listed = listed
        return [cap]

    def export_cap(self, cid, label, level, filename, group):
        cap = Capability(cid, label, level)
        path = os.path.join(self.core.logs, filename) if self.core.logs else ""
        age = file_age(path) if path else None
        listed = self.listed
        if age is not None:
            cap.status = "ok"
            cap.source = filename
            cap.detail = f"{filename} is {age_text(age)}"
            return cap
        bot_conf = self.core.bot_conf if self.core.bot_conf and os.path.isfile(self.core.bot_conf) else ""
        if not bot_conf:
            cap.detail = "needs the PlayerBots module"
            cap.fix = [step("These layers come from the PlayerBots module's log exports; set up PlayerBots first.")]
            return cap
        if listed is not None and filename in listed:
            cap.detail = f"{filename} is listed but not written yet" + (f" in {self.core.logs}" if self.core.logs else "")
            cap.fix = [step("Restart the world server; the file appears once bots start writing it."),
                       step("If it still does not appear, check that the logs folder livemap reads is the server's LogsDir.",
                            snippet=f'logs = "{tpath(self.core.logs) or "C:/Server/logs"}"', file=self.toml_hint())]
            return cap
        if not self.core.logs:
            cap.detail = "no logs folder set"
        else:
            cap.detail = f"{filename} is not in AiPlayerbot.AllowedLogFiles"
        wanted = (listed or []) + [f for f in EXPORT_GROUPS[group]["files"] if f not in (listed or [])]
        cap.fix = [step(f"Add {filename} to AiPlayerbot.AllowedLogFiles in aiplayerbot.conf, then restart the world server.",
                        snippet="AiPlayerbot.AllowedLogFiles = " + ",".join(wanted), file=bot_conf,
                        action={"enable": group})]
        return cap

    def p_exports(self, caps):
        return [
            self.export_cap("exp_travel", "Travel intent export", "L3", "travel_map.csv", "travel"),
            self.export_cap("exp_telecache", "Teleport cache export", "L3", "telecache.csv", "travel"),
            self.export_cap("exp_events", "Bot events export", "L3", "bot_events.csv", "events"),
            self.export_cap("exp_zones", "Zone levels export", "L3", "zones.csv", "zones"),
        ]

    def p_contract(self, caps):
        cap = Capability("contract", "Live map data contract", "L4")
        manifest = os.path.join(self.core.logs, "livemap_manifest.json") if self.core.logs else ""
        try:
            with open(manifest, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = None
        if not isinstance(data, dict):
            data = None
        try:
            contract = int(data.get("contract") or 0) if data else 0
        except (TypeError, ValueError):
            contract = 0
        if contract >= 1:
            cap.status = "ok"
            cap.source = "livemap_manifest.json"
            cap.detail = f"contract {contract}, boot {data.get('bootStamp', '?')}"
            try:
                self.sample_s = float(data.get("liveMapSampleSec") or 5)
            except (TypeError, ValueError):
                self.sample_s = 5.0
            return [cap]
        if self.core.flavor == "vmangos":
            cap.detail = "not available on vmangos yet (BL-V11)"
            cap.fix = [step("The vmangos fork does not write the live-map files yet (tracked as BL-V11). Bots still show from the characters database.")]
            return [cap]
        cap.detail = "no usable livemap_manifest.json in the logs folder" if data is None and os.path.isfile(manifest) \
            else "no livemap_manifest.json in the logs folder"
        cap.fix = [step("Needs a PlayerBots build from 2026-10 or later (cmangos).")]
        cap.fix += self.export_cap("x", "", "L4", "livemap_manifest.json", "live").fix
        return [cap]

    def p_positions(self, caps):
        cap = Capability("positions", "Live positions", "L4")
        path = os.path.join(self.core.logs, "bot_positions.csv") if self.core.logs else ""
        age = file_age(path) if path else None
        sample = self.sample_s
        if age is not None and age < max(FRESH_S, 2 * sample):
            cap.status = "ok"
            cap.source = "bot_positions.csv"
            cap.detail = f"live, updated {age_text(age)} ago".replace(" old ago", " ago")
            return [cap]
        if caps["char_db"].status == "ok":
            cap.status = "degraded"
            cap.source = "characters DB (position at last save)"
            cap.detail = ("bot_positions.csv is " + age_text(age) + "; " if age is not None else "") + \
                "positions come from the characters table and only move when a character is saved"
            save = "PlayerSave.Interval" if self.core.flavor == "vmangos" else "PlayerSaveInterval"
            cap.fix = [step("Dots jump because characters are only saved every few minutes. Saving more often makes them "
                            "move more smoothly (costs some database load).", snippet=f"{save} = 60000",
                            file=self.core.mangosd_conf or "mangosd.conf")]
            cap.fix += caps["contract"].fix if caps["contract"].status != "ok" else \
                [step("Start the world server; live positions resume as soon as bot_positions.csv is written again.")]
            return [cap]
        cap.detail = "no live positions and no characters database"
        cap.fix = caps["char_db"].fix
        return [cap]

    def p_tests(self, caps):
        cap = Capability("tests", "Bot test results", "L5")
        found = glob.glob(os.path.join(self.core.logs, "bot_test_results*.log")) if self.core.logs else []
        if found:
            cap.status = "ok"
            cap.source = "bot_test_results*.log"
            cap.detail = f"{len(found)} result files"
            return [cap]
        cap.detail = "no bot_test_results*.log in the logs folder"
        cap.fix = [step("Only for bot developers: build PlayerBots with GenerateBotTests enabled and run the test suite.")]
        return [cap]

    def p_archive(self, caps):
        cap = Capability("archive", "Log archive at boot", "L3")
        values = botconf.read_values(self.core.bot_conf) if self.core.bot_conf else None
        if not values:
            cap.detail = "no aiplayerbot.conf"
            return [cap]
        v = values["AiPlayerbot.LogArchiveOnBoot"]["value"]
        cap.status = "ok" if v == "1" else "degraded"
        cap.source = "aiplayerbot.conf"
        cap.detail = "old logs are kept at boot" if v == "1" else "logs are wiped at every boot"
        if v != "1":
            cap.fix = [step("Turn on Keep old logs at boot in Settings to keep test runs and trails across restarts.",
                            setting="AiPlayerbot.LogArchiveOnBoot")]
        return [cap]


def doctor_text(report):
    lines = []
    lines.append(f"== {report['core']}  ({report['flavor'] or 'flavor unknown'}, {report['expansion'] or 'expansion unknown'})"
                 f"  level {report['level'] or '-'}")
    for step_ in report["ladder"]:
        lines.append(f"   {'[x]' if step_['ok'] else '[ ]'} {step_['level']} {step_['text']}")
    lines.append("   sources:")
    for c in report["caps"]:
        lines.append(f"     {c['status']:8} {c['id']:13} {c['detail']}")
        if c["status"] != "ok":
            for f in c["fix"]:
                lines.append(f"              fix: {f['text']}")
                if f.get("snippet"):
                    lines.append(f"                   {f['snippet']}" + (f"   ({f['file']})" if f.get("file") else ""))
    lines.append("   layers:")
    for key, l in report["layers"].items():
        lines.append(f"     {l['status']:8} {key:9} {l['label']}" + (f"  <- {l['source']}" if l["source"] and l["status"] != "missing" else ""))
    return "\n".join(lines)
