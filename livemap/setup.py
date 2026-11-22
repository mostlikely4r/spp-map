import glob
import os
import re
import string
import threading
import time
from types import SimpleNamespace

from . import config
from .capabilities import Probe

CLIENT_MARKERS = ("common.MPQ", "common-2.MPQ", "lichking.MPQ", "expansion.MPQ", "patch.MPQ")
CLIENT_PATTERNS = ("World of Warcraft/Data", "Program Files (x86)/World of Warcraft/Data", "Program Files/World of Warcraft/Data",
                   "Games/*/Data", "Games/*/*/Data", "Games/*/*/*/Data", "WoW*/Data", "*/WoW*/Data")
NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def norm(path):
    return os.path.normpath(path).replace("\\", "/")


def is_client(path):
    try:
        names = {n.lower() for n in os.listdir(path)}
    except OSError:
        return False
    return any(m.lower() in names for m in CLIENT_MARKERS)


def drives():
    if os.name != "nt":
        return ["/"]
    return [f"{d}:/" for d in string.ascii_uppercase[2:12] if os.path.isdir(f"{d}:/")]


SCAN = {"clients": [], "done": False, "started": False}
SCAN_LOCK = threading.Lock()


def detect_clients(limit=12, budget_s=60.0, into=None):
    found = [] if into is None else into
    t0 = time.time()
    for root in drives():
        for pattern in CLIENT_PATTERNS:
            for cand in glob.glob(os.path.join(root, pattern)):
                if os.path.isdir(cand) and is_client(cand):
                    p = norm(cand)
                    if p not in found:
                        found.append(p)
                if len(found) >= limit or time.time() - t0 > budget_s:
                    return found
    return found


def start_client_scan():
    with SCAN_LOCK:
        if SCAN["started"]:
            return
        SCAN["started"] = True

    def run():
        try:
            detect_clients(into=SCAN["clients"])
        finally:
            SCAN["done"] = True
    threading.Thread(target=run, daemon=True, name="client-scan").start()


def detect_confs(tool_dir, limit=12):
    found = []
    bases = []
    d = os.path.abspath(tool_dir)
    for _ in range(3):
        bases.append(d)
        d = os.path.dirname(d)
    patterns = ("mangosd.conf", "*/mangosd.conf", "Settings/*/mangosd.conf", "*/Settings/*/mangosd.conf",
                "Server/*/Settings/mangosd.conf", "*/Server/*/Settings/mangosd.conf", "*/bin/mangosd.conf", "*/etc/mangosd.conf")
    for base in bases:
        for pattern in patterns:
            for cand in sorted(glob.glob(os.path.join(base, pattern))):
                p = norm(cand)
                if p not in found:
                    found.append(p)
                if len(found) >= limit:
                    return found
    return found


def guess_name(conf, client, taken):
    text = (conf + " " + client).lower()
    for key, name in (("vmangos", "vmangos"), ("wotlk", "wotlk"), ("lich", "wotlk"), ("tbc", "tbc"), ("burning", "tbc"),
                      ("classic", "vanilla"), ("vanilla", "vanilla")):
        if key in text and name not in taken:
            return name
    n = 1
    while f"server{n}" in taken:
        n += 1
    return f"server{n}"


def detect(app):
    tool_dir = os.path.dirname(app.cfg.path)
    start_client_scan()
    clients = list(SCAN["clients"])
    confs = detect_confs(tool_dir)
    return {"toml": norm(app.cfg.path), "exists": os.path.isfile(app.cfg.path), "clients": clients,
            "scanning": not SCAN["done"], "confs": confs,
            "cores": list(app.cores), "suggest": guess_name(confs[0] if confs else "", clients[0] if clients else "", set(app.cores))}


def entry_from(body):
    entry = {"name": str(body.get("name") or "").strip()}
    for key in ("client", "mangosd_conf", "logs", "aiplayerbot_conf"):
        value = str(body.get(key) or "").strip().strip('"')
        if value:
            entry[key] = norm(value)
    return entry


def validate(app, entry, adding=True):
    if not NAME.match(entry["name"]):
        raise ValueError("name: letters, digits, - and _ only (up to 32)")
    if adding and entry["name"] in app.cores:
        raise ValueError(f"a server named {entry['name']} already exists")
    if not entry.get("client") and not entry.get("mangosd_conf"):
        raise ValueError("give at least the client Data folder or the mangosd.conf")
    for key in ("client", "mangosd_conf", "logs", "aiplayerbot_conf"):
        if entry.get(key) and any(c in entry[key] for c in '"\n\r'):
            raise ValueError(f"{key}: invalid characters")
    if entry.get("mangosd_conf") and not os.path.isfile(entry["mangosd_conf"]):
        raise ValueError(f"mangosd.conf not found: {entry['mangosd_conf']}")
    if entry.get("client") and not os.path.isdir(entry["client"]):
        raise ValueError(f"client folder not found: {entry['client']}")


def check(app, body):
    entry = entry_from(body)
    validate(app, entry, adding=False)
    scratch = config.Config(path=app.cfg.path, data_dir=app.cfg.data_dir, host="", port=0, token="",
                            require_token_local=False, allow_remote_write=False)
    core = config.add_core(scratch, os.path.dirname(app.cfg.path), "", entry)
    app.dbc_fallback(core)
    fake_app = SimpleNamespace(cfg=app.cfg, client_dir=lambda name: core.client)
    report = Probe(fake_app, SimpleNamespace(core=core)).report(True)
    report["derived"] = {"logs": norm(core.logs) if core.logs else "", "dbc": norm(core.dbc) if core.dbc else "",
                         "aiplayerbot_conf": norm(core.bot_conf) if core.bot_conf else ""}
    return report


def toml_block(entry):
    lines = ["", "[[core]]"]
    for key in ("name", "client", "mangosd_conf", "logs", "aiplayerbot_conf"):
        if entry.get(key):
            lines.append(f'{key} = "{entry[key]}"')
    return "\n".join(lines) + "\n"


def save(app, body):
    entry = entry_from(body)
    validate(app, entry)
    path = app.cfg.path
    if os.path.isfile(path):
        with open(path, "rb") as fh:
            raw = fh.read()
        nl = "\r\n" if b"\r\n" in raw else "\n"
        text = raw.decode("utf-8")
        if not text.endswith(("\n", "\r\n")):
            text += nl
        text += toml_block(entry).replace("\n", nl)
    else:
        text = ('[server]\nhost = "127.0.0.1"\nport = %d\ndata_dir = "data"\n' % app.cfg.port) + toml_block(entry)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return app.add_core(entry)
