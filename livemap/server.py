import hmac
import json
import mimetypes
import os
import re
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from . import botconf, snapshot


class Server(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True


STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PAGE_ROUTES = re.compile(r"^/($|r/\d+$|t/[^/]+/.+$|flips$|clusters$|test$|tests$|help$|settings$|w$|w/.*$)")
COOKIE = "lm_token"


class Handler(BaseHTTPRequestHandler):
    app = None
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def is_remote(self):
        h = self.headers
        return bool(h.get("Cf-Connecting-Ip") or h.get("X-Forwarded-For")) or self.client_address[0] not in ("127.0.0.1", "::1")

    def cookie_token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                return v
        return None

    def authorized(self, query):
        cfg = self.app.cfg
        if not self.is_remote() and not cfg.require_token_local:
            return True
        offered = self.cookie_token() or self.headers.get("X-Livemap-Token") or query.get("token")
        return bool(offered) and hmac.compare_digest(offered, cfg.token)

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def route(self, method):
        parsed = urlparse(self.path)
        path = parsed.path
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        try:
            if query.get("token") and self.authorized(query):
                clean = parsed.path + ("?" + "&".join(f"{k}={quote(v)}" for k, v in query.items() if k != "token")
                                       if len(query) > 1 else "")
                self.send_response(302)
                self.send_header("Location", clean)
                self.send_header("Set-Cookie", f"{COOKIE}={self.app.cfg.token}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if not self.authorized(query):
                return self.send_text(401, "Unauthorized: open the access link with ?token=... once on this device.")
            if method == "GET" and PAGE_ROUTES.match(path):
                return self.send_index()
            if method == "GET" and path.startswith("/static/"):
                name = os.path.basename(path)
                return self.send_file(os.path.join(STATIC, name), cache=name.startswith("leaflet"))
            if method == "GET" and path.startswith("/basemap/"):
                _, _, core, name = path.split("/", 3)
                ctx = self.app.cores.get(core)
                if not ctx:
                    file = None
                elif name.endswith(".webp") and parse_qs(urlparse(self.path).query).get("turn"):
                    try:
                        deg = float(parse_qs(urlparse(self.path).query)["turn"][0])
                    except ValueError:
                        deg = 0.0
                    file = self.app.turned_image(core, name[:-5], deg)
                elif name.startswith("model_") and name.endswith(".webp") and name[6:-5].isdigit():
                    file = self.app.terrain.model_image(core, int(name[6:-5]))
                elif name.endswith(".webp"):
                    file = ctx.basemaps.image_for_key(name[:-5])
                else:
                    file = ctx.basemaps.path(name)
                return self.send_file(file, cache=True) if file else self.send_text(404, "no basemap")
            if method == "GET" and path.startswith("/tiles/"):
                return self.tiles(path)
            if method == "GET" and path.startswith("/snapshot/run/"):
                run_id = int(re.sub(r"\D", "", path.rsplit("/", 1)[1]))
                view = self.app.run_view(run_id)
                if not view:
                    return self.send_text(404, "no such run")
                data = snapshot.render_run_png(self.app, view)
                return self.send_bytes(200, data, "image/png")
            if path.startswith("/api/"):
                return self.api(method, path[5:], query)
            self.send_text(404, "not found")
        except Exception as exc:
            traceback.print_exc()
            self.send_json({"error": str(exc)}, 500)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def api(self, method, name, q):
        app, cat = self.app, self.app.catalog
        if method == "GET" and name == "summary":
            return self.send_json({
                "summary": cat.summary(),
                "flips": cat.transitions(q.get("core"), "flip", 10),
                "regressions": cat.transitions(q.get("core"), "regression", 10),
                "cores": list(app.cores),
                "last_ingest": app.last_ingest,
                "remote": self.is_remote(),
            })
        if method == "GET" and name == "capabilities":
            refresh = q.get("refresh") == "1"
            if q.get("core"):
                if q["core"] not in app.cores:
                    return self.send_json({"error": "unknown core"}, 404)
                return self.send_json(app.capabilities(q["core"], refresh))
            return self.send_json({"cores": app.capabilities(None, refresh), "remote": self.is_remote(),
                                   "can_write": not self.is_remote() or app.cfg.allow_remote_write})
        if name.startswith("setup/") or name == "enable":
            from . import setup
            if method == "POST" and self.is_remote() and not app.cfg.allow_remote_write:
                return self.send_json({"error": "setup writes files on this computer; it only works from the computer "
                                                 "livemap runs on (or set allow_remote_write in livemap.toml)"}, 403)
            try:
                if method == "GET" and name == "setup/detect":
                    return self.send_json(setup.detect(app))
                if method == "POST" and name == "setup/check":
                    return self.send_json(setup.check(app, self.body()))
                if method == "POST" and name == "setup/save":
                    return self.send_json({"ok": True, "report": setup.save(app, self.body())})
                if method == "POST" and name == "enable":
                    b = self.body()
                    if b.get("core") not in app.cores:
                        return self.send_json({"error": "unknown core"}, 400)
                    return self.send_json(dict(app.enable_exports(b["core"], str(b.get("group"))), ok=True))
            except (ValueError, OSError) as exc:
                return self.send_json({"error": str(exc)}, 400)
            return self.send_json({"error": f"unknown endpoint {name}"}, 404)
        if method == "GET" and name == "tests":
            return self.send_json(cat.tests(
                core=q.get("core"), family=q.get("family"), verdict=q.get("verdict"), q=q.get("q"),
                bucket=q.get("bucket"), flipped=q.get("flipped") == "1",
                limit=min(int(q.get("limit", 100)), 500), offset=int(q.get("offset", 0)), preview=q.get("preview")))
        if method == "GET" and name == "test":
            core, test = q["core"], q["test"]
            history = cat.history(core, test)
            quest = None
            if history:
                first = cat.run(history[0]["id"])
                quest = app.quest_info(core, first["quest_id"]) if first["quest_id"] else None
            return self.send_json({"core": core, "test": test, "history": history,
                                   "triage": cat.triage(core, test), "quest": quest})
        if method == "GET" and name.startswith("run/"):
            view = app.run_view(int(name.split("/", 1)[1]), q.get("art"))
            return self.send_json(view) if view else self.send_json({"error": "no such run"}, 404)
        if method == "GET" and name == "transitions":
            return self.send_json(cat.transitions(q.get("core"), q.get("kind", "flip"), int(q.get("limit", 100))))
        if method == "GET" and name == "clusters":
            return self.send_json(cat.clusters(q.get("core"), int(q.get("limit", 60))))
        if method == "GET" and name == "families":
            return self.send_json(cat.families())
        if method == "GET" and name == "nodes":
            ctx = app.cores[q["core"]]
            bounds = {k: float(q[k]) for k in ("left", "right", "top", "bottom")}
            return self.send_json(ctx.world.travel_nodes(int(q["map"]), bounds))
        if method == "GET" and name == "live":
            core = q.get("core") or app.live_core()
            if core not in app.cores:
                return self.send_json({"error": "no core with live data"}, 404)
            map_id = int(q["map"]) if q.get("map", "") not in ("", "world") else None
            return self.send_json(app.live_snapshot(core, map_id))
        if method == "GET" and name == "live/bot":
            card = app.bot_card(q["core"], int(q["guid"]))
            return self.send_json(card) if card else self.send_json({"error": "bot not online"}, 404)
        if method == "GET" and name == "live/find":
            return self.send_json(app.cores[q["core"]].live.find(q.get("q", "")))
        if method == "GET" and name == "live/events":
            kinds = set(q["kinds"].split(",")) if q.get("kinds") else None
            map_id = int(q["map"]) if q.get("map", "") not in ("", "world") else None
            return self.send_json(app.live_events(q["core"], map_id, min(int(q.get("seconds", 1800)), 86400), kinds))
        if method == "GET" and name == "tests/where":
            map_id = int(q["map"]) if q.get("map", "") not in ("", "world") else None
            return self.send_json(cat.tests_on_map(q["core"], map_id))
        if method == "GET" and name == "live/intents":
            seconds = min(int(q.get("seconds", 900)), 86400)
            if q.get("map", "") == "world":
                return self.send_json(app.scene_intents(q["core"], seconds))
            return self.send_json(app.live_intents(q["core"], int(q["map"]), seconds))
        if method == "GET" and name == "overlay":
            return self.send_json(app.overlay(q["core"], q.get("mode", "real")))
        if method == "GET" and name == "terrain":
            return self.send_json(app.terrain.describe(q["core"], int(q["map"])))
        if method == "GET" and name == "mapinfo":
            return self.send_json(app.cores[q["core"]].map_info(int(q["map"])))
        if method == "GET" and name == "layer":
            ctx = app.cores[q["core"]]
            bounds = {k: float(q[k]) for k in ("left", "right", "top", "bottom")}
            if q.get("map") == "world":
                if q["kind"] == "zonelevels":
                    return self.send_json(app.scene_zone_levels(q["core"]))
                kinds = set(q.get("kinds", "").split(",")) - {""}
                mode = "sea" if q.get("mode") == "sea" else "real"
                honest = q.get("honest") == "1"
                focus = int(q["focus"]) if q.get("focus", "").lstrip("-").isdigit() else None
                return self.send_json(app.scene_layer(q["core"], q["kind"], mode, honest, bounds, kinds,
                                                      focus=focus, dungeons=q.get("dungeons") != "0",
                                                      turn=q.get("turn") == "1"))
            if q["kind"] == "nodes":
                return self.send_json(ctx.world.travel_nodes(int(q["map"]), bounds))
            if q["kind"] == "zonelevels":
                return self.send_json(ctx.layers.zones.for_map(int(q["map"])))
            if q["kind"] == "telecache":
                kinds = set(q.get("kinds", "").split(",")) - {""}
                return self.send_json(ctx.layers.telecache.layer(int(q["map"]), bounds, kinds))
            if q["kind"] not in ("creatures", "objects"):
                return self.send_json({"error": "unknown layer"}, 404)
            return self.send_json(ctx.world.spawn_layer(q["kind"], int(q["map"]), bounds))
        if method == "POST" and name == "triage":
            b = self.body()
            cat.set_triage(b["core"], b["test"], b.get("bucket"), b.get("note"))
            return self.send_json({"ok": True, "triage": cat.triage(b["core"], b["test"])})
        if method == "GET" and name == "settings":
            return self.send_json({
                "remote": self.is_remote(),
                "can_write": not self.is_remote() or app.cfg.allow_remote_write,
                "cores": {n: botconf.read_values(c.core.bot_conf) for n, c in app.cores.items()},
                "map": app.terrain.settings_payload(),
            })
        if method == "POST" and name == "settings":
            b = self.body()
            if b.get("section") == "map":
                key = str(b.get("key") or "")
                if key.startswith("client.") and self.is_remote() and not app.cfg.allow_remote_write:
                    return self.send_json({"error": "client folders are filesystem paths; set allow_remote_write in livemap.toml to change them remotely"}, 403)
                try:
                    app.terrain.set_value(key, b.get("value"))
                except ValueError as exc:
                    return self.send_json({"error": str(exc)}, 400)
                return self.send_json({"ok": True, "map": app.terrain.settings_payload()})
            if self.is_remote() and not app.cfg.allow_remote_write:
                return self.send_json({"error": "changing server settings from a remote session is off (allow_remote_write in livemap.toml)"}, 403)
            botconf.write_value(app.cores[b["core"]].core.bot_conf, b["key"], b["value"])
            return self.send_json({"ok": True, "values": botconf.read_values(app.cores[b["core"]].core.bot_conf)})
        if method == "POST" and name == "mapcache":
            b = self.body()
            core = b.get("core") or None
            if core and core not in app.cores:
                return self.send_json({"error": "unknown core"}, 400)
            freed = app.terrain.clear_cache(core)
            return self.send_json({"ok": True, "freed_mb": round(freed / 1048576, 1), "map": app.terrain.settings_payload()})
        if method == "POST" and name == "clientcheck":
            b = self.body()
            return self.send_json(app.terrain.check_client(b.get("core"), b.get("path")))
        if method == "POST" and name == "ingest":
            return self.send_json({"added": app.ingest()})
        return self.send_json({"error": f"unknown endpoint {name}"}, 404)

    def tiles(self, path):
        parts = path.split("/")
        if len(parts) != 8 or parts[3] != "adt":
            return self.send_text(404, "bad tile path")
        core, map_id, z, x = parts[2], parts[4], parts[5], parts[6]
        y = parts[7].split(".", 1)[0]
        try:
            query = parse_qs(urlparse(self.path).query)
            data = self.app.terrain.tile(core, int(map_id), int(z), int(x), int(y), query.get("shade", [None])[0],
                                         {"0": "off", "2": "inside"}.get(query.get("bld", ["1"])[0], "roofs"),
                                         int(query["cut"][0]) if query.get("cut", [""])[0].lstrip("-").isdigit() else None,
                                         query.get("trees", ["1"])[0] != "0",
                                         query.get("ov", [None])[0] if query.get("ov", [None])[0] in ("slope", "swim") else None)
        except (TypeError, ValueError):
            return self.send_text(404, "bad tile path")
        if not data:
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_bytes(200, data, "image/webp", cache=604800)

    def send_json(self, payload, status=200):
        self.send_bytes(status, json.dumps(payload, default=str).encode("utf-8"), "application/json")

    def send_text(self, status, text):
        self.send_bytes(status, text.encode("utf-8"), "text/plain; charset=utf-8")

    def send_bytes(self, status, data, ctype, cache=False):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        seconds = cache if isinstance(cache, int) and not isinstance(cache, bool) else 86400
        self.send_header("Cache-Control", f"public, max-age={seconds}" if cache else "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_index(self):
        with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as fh:
            html = fh.read()

        def stamp(m):
            name = m.group(1)
            try:
                v = int(os.path.getmtime(os.path.join(STATIC, name)))
            except OSError:
                v = 0
            return f'/static/{name}?v={v}"'
        html = re.sub(r'/static/([\w.]+)"', stamp, html)
        self.send_bytes(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def send_file(self, path, cache=False):
        if not path or not os.path.isfile(path):
            return self.send_text(404, "not found")
        with open(path, "rb") as fh:
            data = fh.read()
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self.send_bytes(200, data, ctype, cache)


def serve(app):
    Handler.app = app
    server = Server((app.cfg.host, app.cfg.port), Handler)
    server.daemon_threads = True
    return server
