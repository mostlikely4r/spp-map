import hmac
import json
import mimetypes
import os
import re
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from . import snapshot

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PAGE_ROUTES = re.compile(r"^/($|r/\d+$|t/[^/]+/.+$|flips$|clusters$|test$)")
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
                return self.send_file(os.path.join(STATIC, "index.html"))
            if method == "GET" and path.startswith("/static/"):
                return self.send_file(os.path.join(STATIC, os.path.basename(path)), cache=True)
            if method == "GET" and path.startswith("/basemap/"):
                _, _, core, name = path.split("/", 3)
                ctx = self.app.cores.get(core)
                file = ctx.basemaps.path(name) if ctx else None
                return self.send_file(file, cache=True) if file else self.send_text(404, "no basemap")
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
        if method == "GET" and name == "tests":
            return self.send_json(cat.tests(
                core=q.get("core"), family=q.get("family"), verdict=q.get("verdict"), q=q.get("q"),
                bucket=q.get("bucket"), flipped=q.get("flipped") == "1",
                limit=min(int(q.get("limit", 100)), 500), offset=int(q.get("offset", 0))))
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
            view = app.run_view(int(name.split("/", 1)[1]))
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
        if method == "POST" and name == "triage":
            b = self.body()
            cat.set_triage(b["core"], b["test"], b.get("bucket"), b.get("note"))
            return self.send_json({"ok": True, "triage": cat.triage(b["core"], b["test"])})
        if method == "POST" and name == "ingest":
            return self.send_json({"added": app.ingest()})
        return self.send_json({"error": f"unknown endpoint {name}"}, 404)

    def send_json(self, payload, status=200):
        self.send_bytes(status, json.dumps(payload, default=str).encode("utf-8"), "application/json")

    def send_text(self, status, text):
        self.send_bytes(status, text.encode("utf-8"), "text/plain; charset=utf-8")

    def send_bytes(self, status, data, ctype, cache=False):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400" if cache else "no-store")
        self.end_headers()
        self.wfile.write(data)

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
    server = ThreadingHTTPServer((app.cfg.host, app.cfg.port), Handler)
    server.daemon_threads = True
    return server
