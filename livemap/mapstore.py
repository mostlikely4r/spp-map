import sqlite3
import threading

SCHEMA = "create table if not exists settings (key text primary key, value text not null)"

DEFAULTS = {
    "keep_cache": "0",
    "cache_mb": "500",
    "shading": "soft",
    "render_workers": "2",
    "fallback": "1",
    "fallback_order": "tbc,vanilla,wotlk",
}

BOOL_KEYS = ("keep_cache", "fallback")
INT_KEYS = {"cache_mb": (16, 20000), "render_workers": (1, 8)}


def as_text(value):
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    return str(value)


class MapStore:
    def __init__(self, path, defaults=None):
        self.path = path
        self.lock = threading.Lock()
        self.defaults = dict(DEFAULTS)
        for key, value in (defaults or {}).items():
            key = str(key).replace("-", "_")
            if key in self.defaults or kind_key(key):
                self.defaults[key] = as_text(value)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def stored(self):
        with self.lock:
            return {r["key"]: r["value"] for r in self.db.execute("select key, value from settings")}

    def all(self):
        values = {k: v for k, v in self.defaults.items() if not k.startswith("client.")}
        values.update({k: v for k, v in self.stored().items() if not k.startswith("client.")})
        return values

    def get(self, key):
        row = self.stored().get(key)
        if row is not None:
            return row
        return self.defaults.get(key, "")

    def set(self, key, value):
        text = as_text(value)
        with self.lock:
            self.db.execute("insert into settings (key, value) values (?, ?) on conflict(key) do update set value = excluded.value",
                            (key, text))
            self.db.commit()
        return text

    def clear(self, key):
        with self.lock:
            self.db.execute("delete from settings where key = ?", (key,))
            self.db.commit()

    def default(self, key):
        return self.defaults.get(key, "")

    def flag(self, key):
        return self.get(key) == "1"

    def number(self, key):
        try:
            return int(self.get(key))
        except (TypeError, ValueError):
            return int(self.defaults.get(key) or 0)

    def shade(self):
        shade = self.get("shading")
        return shade if shade in ("soft", "medium", "strong", "flat") else "soft"

    def order(self):
        raw = self.get("fallback_order")
        return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]

    def client_dir(self, core):
        return self.stored().get(f"client.{core}", "") or self.defaults.get(f"client.{core}", "")


def kind_key(key):
    if key in BOOL_KEYS or key in INT_KEYS or key == "shading" or key == "fallback_order":
        return True
    return bool(key and key.startswith("client."))
