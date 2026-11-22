import glob
import os
import re
import secrets
import tomllib
from dataclasses import dataclass, field


@dataclass
class DbInfo:
    host: str
    port: int
    user: str
    password: str
    database: str


@dataclass
class Core:
    name: str
    logs: str
    dbc: str
    client: str
    world_db: DbInfo | None
    locale: str = "enUS"
    bot_conf: str = ""
    mangosd_conf: str = ""
    mmap_config: str = ""
    char_db: DbInfo | None = None
    login_db: DbInfo | None = None
    flavor: str = ""
    expansion: str = ""
    dbc_from_client: bool = False
    db_poll_s: int = 20
    bot_prefix: str = ""


@dataclass
class Config:
    path: str
    data_dir: str
    host: str
    port: int
    token: str
    require_token_local: bool
    allow_remote_write: bool
    map: dict = field(default_factory=dict)
    cores: dict[str, Core] = field(default_factory=dict)

    def core(self, name):
        if name not in self.cores:
            raise KeyError(f"unknown core {name!r}")
        return self.cores[name]


def parse_db_info(text):
    parts = text.strip().strip('"').split(";")
    if len(parts) != 5:
        return None
    host, port, user, password, database = parts
    return DbInfo(host, int(port), user, password, database)


DB_KEYS = {
    "world": ("WorldDatabaseInfo", "WorldDatabase.Info"),
    "char": ("CharacterDatabaseInfo", "CharacterDatabase.Info"),
    "login": ("LoginDatabaseInfo", "LoginDatabase.Info"),
}
EXPANSIONS = {"vanilla": "classic", "classic": "classic", "tbc": "tbc", "wotlk": "wotlk"}


def read_conf(path):
    out = {}
    if not path:
        return out
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = re.match(r"^\s*([A-Za-z][\w.]*)\s*=\s*(.*?)\s*$", line)
                if m:
                    out.setdefault(m.group(1), m.group(2).strip().strip('"'))
    except OSError:
        pass
    return out


def conf_db(values, kind):
    for key in DB_KEYS[kind]:
        if values.get(key):
            return parse_db_info(values[key])
    return None


def read_db(mangosd_conf, kind):
    return conf_db(read_conf(mangosd_conf), kind)


def read_world_db(mangosd_conf):
    return read_db(mangosd_conf, "world")


def conf_dbc(values, conf_dir):
    data = values.get("DataDir")
    if not data:
        return ""
    data = data if os.path.isabs(data) else os.path.join(conf_dir, data)
    for cand in [os.path.join(data, "dbc")] + sorted(glob.glob(os.path.join(data, "*", "dbc"))):
        if os.path.isfile(os.path.join(cand, "Map.dbc")):
            return os.path.normpath(cand)
    return os.path.normpath(os.path.join(data, "dbc"))


def resolve(base, path):
    if not path:
        return ""
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(base, path))


def load(path):
    path = os.path.abspath(path)
    base = os.path.dirname(path)
    raw = {}
    if os.path.isfile(path):
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    server = raw.get("server", {})
    data_dir = resolve(base, server.get("data_dir", "data"))
    os.makedirs(data_dir, exist_ok=True)
    token = server.get("token") or load_or_create_token(data_dir)
    cfg = Config(
        path=path,
        data_dir=data_dir,
        host=server.get("host", "127.0.0.1"),
        port=int(server.get("port", 8420)),
        token=token,
        require_token_local=bool(server.get("require_token_local", False)),
        allow_remote_write=bool(server.get("allow_remote_write", False)),
        map=raw.get("map", {}),
    )
    spp_root = resolve(base, raw.get("spp_root", ""))
    for entry in raw.get("core", []):
        add_core(cfg, base, spp_root, entry)
    return cfg


def add_core(cfg, base, spp_root, entry):
    name = entry["name"]
    use_spp = spp_root and not entry.get("mangosd_conf")
    spp = lambda *p: os.path.join(spp_root, *p) if use_spp else ""
    conf = resolve(base, entry.get("mangosd_conf") or spp("Settings", name, "mangosd.conf"))
    conf_dir = os.path.dirname(conf) if conf else ""
    values = read_conf(conf) if conf and os.path.isfile(conf) else {}
    flavor = entry.get("flavor") or ("vmangos" if "WorldDatabase.Info" in values else ("cmangos" if values else ""))
    logs = entry.get("logs") or spp("Settings", name, "logs")
    if not logs and values:
        logs_dir = values.get("LogsDir", "")
        logs = logs_dir if os.path.isabs(logs_dir) else os.path.join(conf_dir, logs_dir)
    dbc = entry.get("dbc") or ""
    if not dbc:
        cand = spp("Modules", name, "dbc")
        if cand and os.path.isfile(os.path.join(cand, "Map.dbc")):
            dbc = cand
        elif values:
            dbc = conf_dbc(values, conf_dir)
        else:
            dbc = cand
    bot_conf = entry.get("aiplayerbot_conf") or spp("Settings", name, "aiplayerbot.conf") or         (os.path.join(conf_dir, "aiplayerbot.conf") if conf_dir else "")
    world = parse_db_info(entry["world_db"]) if entry.get("world_db") else conf_db(values, "world")
    chars = parse_db_info(entry["char_db"]) if entry.get("char_db") else conf_db(values, "char")
    tree = {"vanilla": "mangos-classic", "tbc": "mangos-tbc", "wotlk": "mangos-wotlk"}.get(name, "")
    mmap = resolve(base, entry.get("mmap_config") or (spp_root and tree and os.path.join(
        spp_root, "builds", tree, "contrib", "extractor_scripts", "config.json")))
    bot_values = read_conf(resolve(base, bot_conf)) if bot_conf else {}
    cfg.cores[name] = Core(
        name=name,
        logs=resolve(base, logs),
        dbc=resolve(base, dbc),
        client=resolve(base, entry.get("client", "")),
        world_db=world,
        locale=entry.get("locale", "enUS"),
        bot_conf=resolve(base, bot_conf),
        mangosd_conf=conf,
        mmap_config=mmap if mmap and os.path.exists(mmap) else "",
        char_db=chars,
        login_db=parse_db_info(entry["login_db"]) if entry.get("login_db") else conf_db(values, "login"),
        flavor=flavor,
        expansion=entry.get("expansion") or EXPANSIONS.get(name, "") or ("classic" if flavor == "vmangos" else ""),
        db_poll_s=max(5, int(entry.get("db_poll_s", 20))),
        bot_prefix=(bot_values.get("AiPlayerbot.RandomBotAccountPrefix") or ("rndbot" if bot_values else "")).lower(),
    )
    return cfg.cores[name]


def load_or_create_token(data_dir):
    token_path = os.path.join(data_dir, "access_token.txt")
    try:
        with open(token_path, encoding="utf-8") as fh:
            token = fh.read().strip()
            if token:
                return token
    except OSError:
        pass
    token = secrets.token_urlsafe(24)
    with open(token_path, "w", encoding="utf-8") as fh:
        fh.write(token)
    return token
