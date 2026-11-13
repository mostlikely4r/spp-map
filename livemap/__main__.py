import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import threading

from . import config, snapshot
from .app import App

DEFAULT_CONFIG = "livemap.toml"


def find_cloudflared():
    found = shutil.which("cloudflared")
    if found:
        return found
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\cloudflared.exe"),
        r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
        r"C:\Program Files\cloudflared\cloudflared.exe",
    ]
    candidates += glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Cloudflare.cloudflared*\*.exe"))
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def start_tunnel(cfg, url_file):
    exe = find_cloudflared()
    if not exe:
        print("cloudflared not found; install it (winget install Cloudflare.cloudflared) or run without --tunnel")
        return None
    proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://{cfg.host}:{cfg.port}"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")

    def watch():
        for line in proc.stdout:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m:
                link = f"{m.group(0)}/?token={cfg.token}"
                print("remote access link:", link, flush=True)
                with open(url_file, "w", encoding="utf-8") as fh:
                    fh.write(link + "\n")
    threading.Thread(target=watch, daemon=True).start()
    return proc


def cmd_serve(cfg, args):
    from .server import serve
    app = App(cfg)
    print(f"indexing results for {', '.join(cfg.cores)} ...", flush=True)
    print(f"added {app.ingest()} results", flush=True)
    app.start_ingest_loop()
    server = serve(app)
    print(f"livemap on http://{cfg.host}:{cfg.port}  (local access needs no token)", flush=True)
    tunnel = start_tunnel(cfg, os.path.join(cfg.data_dir, "remote_url.txt")) if args.tunnel else None
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if tunnel:
            tunnel.terminate()


def cmd_ingest(cfg, args):
    app = App(cfg)
    print("added", app.ingest())
    for row in app.catalog.summary()["verdicts"]:
        print(f"  {row['core']:8} {row['verdict']:10} {row['n']}")


def cmd_snapshot(cfg, args):
    app = App(cfg)
    app.ingest()
    run_id = args.run
    if run_id is None:
        rows = app.catalog.tests(q=args.test, limit=1) if args.test else []
        if not rows:
            sys.exit("no matching run")
        run_id = rows[0]["id"]
    view = app.run_view(run_id)
    if not view:
        sys.exit("no such run")
    out = args.out or os.path.join(cfg.data_dir, "snapshots", f"run_{run_id}.png")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    snapshot.render_run(app, view).save(out)
    print(out)
    for n in view["notice"]:
        print("note:", n)


def cmd_basemaps(cfg, args):
    app = App(cfg)
    for name in args.core or list(cfg.cores):
        ctx = app.cores[name]
        if not ctx.basemaps.archives.available():
            print(f"{name}: no client data configured, skipped")
            continue
        def progress(entry, info):
            print(f"{name}: {'ok ' if info else 'none'} {entry['kind']} {entry['name']} {entry.get('floor') or ''}", flush=True)
        print(f"{name}: built {ctx.basemaps.build_all(progress)}")


def main():
    parser = argparse.ArgumentParser(prog="livemap")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("serve", help="run the web interface")
    p.add_argument("--tunnel", action="store_true", help="also open a cloudflared quick tunnel")
    sub.add_parser("ingest", help="index test results once and print counts")
    p = sub.add_parser("snapshot", help="render a run page to PNG")
    p.add_argument("--run", type=int)
    p.add_argument("--test", help="search text; renders the latest matching run")
    p.add_argument("--out")
    p = sub.add_parser("basemaps", help="prebuild painted basemaps from the client")
    p.add_argument("--core", action="append")
    args = parser.parse_args()
    cfg = config.load(args.config)
    {"serve": cmd_serve, "ingest": cmd_ingest, "snapshot": cmd_snapshot, "basemaps": cmd_basemaps}[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
