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


def cmd_verify(cfg, args):
    app = App(cfg)
    if args.scene == "world":
        return cmd_verify_world(app, args)
    threshold = args.threshold
    for name in args.core or list(cfg.cores):
        ctx = app.cores[name]
        if not ctx.basemaps.archives.available() or not ctx.world.available():
            print(f"{name}: needs client data and the world database, skipped")
            continue
        bm = ctx.basemaps
        names = bm.map_names()
        points = ctx.world.spawn_points()
        continents = {a["map"] for a in bm.areas if not a["area"]}
        weak, checked, missing = [], 0, []
        for map_id in sorted(points):
            if map_id in continents or (args.map is not None and map_id != args.map):
                continue
            pts = points[map_id]
            source, inside = bm.verify(map_id, pts)
            label = f"{map_id:4} {names.get(map_id, '')}"
            if source is None:
                missing.append(label)
                continue
            checked += 1
            share = inside / len(pts)
            flag = "ok  " if share >= threshold else "LOW "
            if share < threshold:
                weak.append(label)
            print(f"{name}: {flag}{share:6.1%} {inside:5}/{len(pts):<5} {source:15} {label}", flush=True)
        print(f"{name}: {checked} maps checked, {len(weak)} below {threshold:.0%}, {len(missing)} without art")
        for m in missing:
            print(f"{name}:   no art  {m}")


def cmd_verify_world(app, args):
    from . import overlay as ov

    for name in args.core or list(app.cores):
        ctx = app.cores[name]
        if not ctx.basemaps.archives.available():
            print(f"{name}: needs client data, skipped")
            continue
        overrides = set(ov.load_anchors(ctx))
        real = app.overlay(name, "real")
        sea = app.overlay(name, "sea")
        land = ov.land_mask(ctx)
        sea_by_id = {i["id"]: i for i in sea["instances"]}
        outside, on_land, groups, placed = [], [], {}, 0
        for inst in real["instances"]:
            if inst.get("tray"):
                continue
            placed += 1
            e = inst["extent"]
            box = ov.box_of(e, inst["off"][0], inst["off"][1])
            bad = [p for p in inst["links"]
                   if not (box["bottom"] - 1 <= p[1][0] <= box["top"] + 1 and box["right"] - 1 <= p[1][1] <= box["left"] + 1)]
            if bad:
                outside.append(inst["id"])
            group = inst.get("cluster")
            if group is not None:
                groups.setdefault(group, []).append(inst["id"])
            other = sea_by_id.get(inst["id"])
            spill = bool(other) and bool(inst["links"]) and land.hits(ov.box_of(e, other["off"][0], other["off"][1]))
            if spill:
                on_land.append(inst["id"])
            extra = ""
            if spill:
                extra += " on-land"
            if inst["id"] in overrides:
                extra += " override"
            print(f"{name}: {'LOW ' if bad else 'ok  '}{inst['id']:5} {inst['source']:9} "
                  f"door {inst['parent'] if inst['parent'] is not None else inst['root']:>4} "
                  f"links {len(inst['links'])} cluster {group if group is not None else '-':>4}{extra} {inst['name']}", flush=True)
        print(f"{name}: {placed} placed, {len(real['tray'])} in the tray, {len(outside)} landing outside art, "
              f"{len(on_land)} box on land in offshore mode, {len(groups)} clusters", flush=True)
        if real["tray"]:
            print(f"{name}:   tray  {', '.join(str(t) for t in real['tray'])}")
        if outside:
            print(f"{name}:   outside art  {', '.join(str(t) for t in outside)}")
        for group, members in sorted(groups.items()):
            print(f"{name}:   cluster {group}  {', '.join(str(m) for m in members)}")


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
    p = sub.add_parser("verify", help="check that DB spawns land on each map's art")
    p.add_argument("--core", action="append")
    p.add_argument("--map", type=int)
    p.add_argument("--threshold", type=float, default=0.95)
    p.add_argument("--scene", choices=["art", "world"], default="art",
                   help="art: world-DB spawns land on each map's art; world: the World + dungeons placement")
    p = sub.add_parser("basemaps", help="prebuild painted basemaps from the client")
    p.add_argument("--core", action="append")
    args = parser.parse_args()
    cfg = config.load(args.config)
    {"serve": cmd_serve, "ingest": cmd_ingest, "snapshot": cmd_snapshot, "basemaps": cmd_basemaps, "verify": cmd_verify}[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
