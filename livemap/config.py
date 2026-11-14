import os
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


@dataclass
class Config:
    path: str
    data_dir: str
    host: str
    port: int
    token: str
    require_token_local: bool
    allow_remote_write: bool
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


def read_db(mangosd_conf, key):
    try:
        with open(mangosd_conf, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith(key):
                    return parse_db_info(line.split("=", 1)[1])
    except OSError:
        return None
    return None


def read_world_db(mangosd_conf):
    return read_db(mangosd_conf, "WorldDatabaseInfo")


def resolve(base, path):
    if not path:
        return ""
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(base, path))


def load(path):
    path = os.path.abspath(path)
    base = os.path.dirname(path)
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
    )
    spp_root = resolve(base, raw.get("spp_root", ""))
    for entry in raw.get("core", []):
        name = entry["name"]
        logs = resolve(base, entry.get("logs") or (spp_root and os.path.join(spp_root, "Settings", name, "logs")))
        dbc = resolve(base, entry.get("dbc") or (spp_root and os.path.join(spp_root, "Modules", name, "dbc")))
        conf = resolve(base, entry.get("mangosd_conf") or (spp_root and os.path.join(spp_root, "Settings", name, "mangosd.conf")))
        bot_conf = resolve(base, entry.get("aiplayerbot_conf") or (spp_root and os.path.join(spp_root, "Settings", name, "aiplayerbot.conf")))
        db = parse_db_info(entry["world_db"]) if entry.get("world_db") else read_world_db(conf)
        cfg.cores[name] = Core(
            name=name,
            logs=logs,
            dbc=dbc,
            client=resolve(base, entry.get("client", "")),
            world_db=db,
            locale=entry.get("locale", "enUS"),
            bot_conf=bot_conf,
            mangosd_conf=conf,
        )
    return cfg


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
