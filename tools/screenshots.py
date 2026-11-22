import argparse
import io
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "screenshots")
MAX_BYTES = 300 * 1024
CORE = "wotlk"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def save_webp(png, name, width=None, max_bytes=MAX_BYTES):
    img = Image.open(io.BytesIO(png)).convert("RGB") if isinstance(png, bytes) else png.convert("RGB")
    if width and img.width > width:
        img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    path = os.path.join(OUT, name + ".webp")
    for quality in (82, 74, 66, 58, 50, 42):
        buf = io.BytesIO()
        img.save(buf, "WEBP", quality=quality, method=6)
        if buf.tell() <= max_bytes:
            break
        if quality == 42:
            img = img.resize((img.width * 3 // 4, img.height * 3 // 4), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, "WEBP", quality=60, method=6)
    with open(path, "wb") as fh:
        fh.write(buf.getvalue())
    print(f"{name}.webp {img.width}x{img.height} {buf.tell() // 1024} KB", flush=True)


def font(size):
    for cand in ("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if os.path.exists(cand):
            return ImageFont.truetype(cand, size)
    return ImageFont.load_default(size=size)


def callouts(img, items, scale):
    draw = ImageDraw.Draw(img)
    f = font(round(17 * scale))
    for text, (px, py), (lx, ly) in items:
        px, py, lx, ly = px * scale, py * scale, lx * scale, ly * scale
        box = draw.textbbox((0, 0), text, font=f)
        w, h = box[2] - box[0] + 24 * scale, box[3] - box[1] + 16 * scale
        x0, y0 = lx - w / 2, ly - h / 2
        draw.line([(lx, ly), (px, py)], fill=(255, 210, 0), width=round(3 * scale))
        draw.ellipse([px - 6 * scale, py - 6 * scale, px + 6 * scale, py + 6 * scale], outline=(255, 210, 0), width=round(3 * scale))
        draw.rounded_rectangle([x0, y0, x0 + w, y0 + h], radius=10 * scale, fill=(16, 18, 24), outline=(255, 210, 0), width=round(2 * scale))
        draw.text((x0 + 12 * scale, y0 + 8 * scale - box[1]), text, font=f, fill=(255, 236, 170))
    return img


def wait_map(page, extra=2.5):
    page.wait_for_function("() => typeof W !== 'undefined' && W.snap && W.ui && document.querySelector('#wmap')", timeout=60000)
    page.wait_for_load_state("networkidle")
    time.sleep(extra)


def walking_bot(page, near=None):
    return page.evaluate("""async (near) => {
      const live = await (await fetch(LM.base + '/data/live.json')).json();
      const ci = Object.fromEntries(live.cols.map((c, k) => [c, k]));
      const path = {}, last = {};
      for (const f of live.frames) for (const r of f.bots) {
        const g = r[ci.guid], p = [r[ci.x], r[ci.y], r[ci.map]];
        const q = last[g];
        if (q && q[2] === p[2]) { const d = Math.hypot(p[0] - q[0], p[1] - q[1]); if (d < 150) path[g] = (path[g] || 0) + d; }
        last[g] = p;
      }
      let best = null, n = 0;
      for (const [g, d] of Object.entries(path)) {
        if (last[g][2] !== 0) continue;
        if (near && Math.hypot(last[g][0] - near[0], last[g][1] - near[1]) > near[2]) continue;
        if (d > n) { n = d; best = +g; }
      }
      return best;
    }""", near)


def point_of(page, js):
    return page.evaluate(f"""() => {{ const ll = ({js}); if (!ll) return null; const p = mapObj.latLngToContainerPoint(ll);
      const r = document.querySelector('#wmap').getBoundingClientRect(); return [p.x + r.left, p.y + r.top]; }}""")


def main():
    ap = argparse.ArgumentParser(description="regenerate the README screenshots and poster from the demo")
    ap.add_argument("--url", help="use a running demo/livemap instead of starting the bundled demo")
    ap.add_argument("--dir", default=os.path.join(ROOT, "demo"))
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    proc = None
    base = args.url
    if not base:
        port = free_port()
        proc = subprocess.Popen([sys.executable, "-m", "livemap", "demo", "--dir", args.dir, "--port", str(port)], cwd=ROOT)
        base = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                urllib.request.urlopen(base + "/", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
    with open(os.path.join(args.dir, "data", "tests.json"), encoding="utf-8") as fh:
        run_id = (json.load(fh) or [{"id": 0}])[0]["id"]
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            desk = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2, color_scheme="dark")
            page = desk.new_page()

            page.goto(base + f"/w/{CORE}/world?art=painted&layers=bots,labels,zones")
            wait_map(page)
            save_webp(page.screenshot(), "world", 1440)

            page.goto(base + f"/w/{CORE}/world?art=minimap&layers=bots,labels,dungeons&honest=0")
            wait_map(page)
            page.evaluate("() => { const i = W.inst.find(x => x.m.id === 36) || W.inst[0]; if (i) mapObj.setView(i.box.getCenter(), -3.25); }")
            time.sleep(2.5)
            save_webp(page.screenshot(), "dungeons", 1440)

            page.goto(base + f"/w/{CORE}/world?art=painted&layers=bots,labels,zones,npcs,nodes&x=-9300&y=200&z=-2.5")
            wait_map(page, 4)
            save_webp(page.screenshot(), "layers", 1440)

            page.goto(base + f"/w/{CORE}/world?art=painted&layers=bots,labels,zones,events")
            wait_map(page)
            guid = walking_bot(page)
            page.evaluate(f"() => selectBot({guid}, true)")
            time.sleep(3)
            save_webp(page.screenshot(), "botcard", 1440)

            page.goto(base + "/help")
            page.wait_for_load_state("networkidle")
            time.sleep(1.5)
            save_webp(page.screenshot(), "help", 1440)

            page.goto(base + "/tests")
            page.wait_for_load_state("networkidle")
            time.sleep(1.5)
            save_webp(page.screenshot(), "tests", 1440)

            page.goto(base + f"/r/{run_id}")
            page.wait_for_load_state("networkidle")
            time.sleep(2.5)
            save_webp(page.screenshot(), "run", 1440)

            poster = browser.new_context(viewport={"width": 1600, "height": 900}, device_scale_factor=1.5, color_scheme="dark")
            page = poster.new_page()
            page.goto(base + f"/w/{CORE}/world?art=minimap&layers=bots,labels,dungeons,intents,tests&honest=0")
            wait_map(page, 3)
            page.evaluate("() => { const i = W.inst.find(x => x.m.id === 36) || W.inst[0]; if (i) mapObj.setView([i.box.getCenter().lat + 1500, i.box.getCenter().lng - 2600], -3.5); }")
            time.sleep(2)
            near = page.evaluate("() => { const i = W.inst.find(x => x.m.id === 36) || W.inst[0]; const c = i.box.getCenter(); return [c.lat, -c.lng, 3500]; }")
            guid = walking_bot(page, near)
            if guid:
                page.evaluate(f"() => selectBot({guid}, false)")
            time.sleep(3)
            page.evaluate("() => { document.querySelectorAll('.wfab').forEach(n => n.classList.add('hidden')); }")
            png = page.screenshot()
            marks = []
            p = point_of(page, "W.selected && W.markers.get(W.selected) ? W.markers.get(W.selected).getLatLng() : null")
            if p:
                marks.append(("live bot, with its trail and card", p, (p[0] + 210, p[1] - 150)))
            p = point_of(page, "(W.inst.find(x => x.m.id === 36) || W.inst[0] || {box: null}).box && (W.inst.find(x => x.m.id === 36) || W.inst[0]).box.getCenter()")
            if p:
                marks.append(("dungeon drawn at its entrance", p, (p[0] - 150, p[1] + 170)))
            p = point_of(page, "(() => { const c = mapObj.getCenter(); let b = null, d = 1e12; for (const l of W.layers.tests.getLayers()) { const p = l.getLatLng(); const k = Math.hypot(p.lat - c.lat, p.lng - c.lng); if (k < d) { d = k; b = p; } } return b; })()")
            if p:
                marks.append(("bot test run here (green = pass)", p, (p[0] + 220, p[1] + 110)))
            p = point_of(page, "(() => { const c = mapObj.getCenter(); let b = null, d = 1e12; for (const l of W.layers.intents.getLayers()) { const ls = l.getLatLngs ? l.getLatLngs() : null; if (!ls || ls.length < 2) continue; const p = L.latLng((ls[0].lat + ls[1].lat) / 2, (ls[0].lng + ls[1].lng) / 2); const k = Math.hypot(p.lat - c.lat, p.lng - c.lng); if (k < d) { d = k; b = p; } } return b; })()")
            if p:
                marks.append(("where each bot is heading, and why", p, (p[0] - 230, p[1] - 130)))
            img = Image.open(io.BytesIO(png))
            marks.sort(key=lambda m: m[1][1])
            step = 640 / max(1, len(marks))
            placed = [(t, a, (330, 190 + step * (k + 0.5))) for k, (t, a, _) in enumerate(marks)]
            img = callouts(img, placed, 1.5)
            save_webp(img.resize((1600, 900), Image.LANCZOS), "poster", 1600, 450 * 1024)

            phone = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True,
                                        has_touch=True, color_scheme="dark")
            page = phone.new_page()
            page.goto(base + f"/w/{CORE}/world?art=painted&layers=bots,labels,zones")
            wait_map(page)
            save_webp(page.screenshot(), "phone_map", 600)
            page.click(".wfab button")
            time.sleep(1)
            save_webp(page.screenshot(), "phone_drawer", 600)
            page.goto(base + "/help")
            page.wait_for_load_state("networkidle")
            time.sleep(1)
            save_webp(page.screenshot(), "phone_help", 600)
            page.goto(base + f"/r/{run_id}")
            page.wait_for_load_state("networkidle")
            time.sleep(2)
            save_webp(page.screenshot(), "phone_run", 600)
            overflow = {}
            for path in ("/", "/help", f"/r/{run_id}", "/tests"):
                page.goto(base + path)
                page.wait_for_load_state("networkidle")
                time.sleep(1.5)
                overflow[path] = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
            print("phone horizontal overflow (px):", overflow)
            browser.close()
    finally:
        if proc:
            proc.terminate()


if __name__ == "__main__":
    main()
