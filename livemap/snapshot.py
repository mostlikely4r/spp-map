import io

ROLE_COLORS = {"giver": (255, 210, 0), "taker": (60, 220, 90), "objective": (255, 70, 200)}
VERDICT_COLORS = {"PASS": (60, 200, 90), "FAIL": (230, 70, 60), "ABORT": (240, 160, 40), "IMPOSSIBLE": (150, 150, 160)}


def view_bounds(view):
    if view.get("basemap"):
        return view["basemap"]["bounds"]
    pts = [(p["x"], p["y"]) for p in view["track"]] + [(m["x"], m["y"]) for m in view["markers"]]
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    pad = max(40.0, (max(xs) - min(xs)) * 0.15, (max(ys) - min(ys)) * 0.15)
    return {"top": max(xs) + pad, "bottom": min(xs) - pad, "left": max(ys) + pad, "right": min(ys) - pad}


def font(size):
    from PIL import ImageFont
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_run(app, view, width=1400):
    from PIL import Image, ImageDraw
    run = view["run"]
    b = view_bounds(view)
    if view.get("basemap"):
        ctx = app.cores[run["core"]]
        base = Image.open(ctx.basemaps.path(view["basemap"]["image"])).convert("RGB")
        scale = width / base.width
        base = base.resize((width, int(base.height * scale)))
    else:
        height = int(width * 2 / 3)
        if b:
            ratio = abs(b["top"] - b["bottom"]) / max(1.0, abs(b["left"] - b["right"]))
            height = max(300, min(int(width * ratio), width))
        base = Image.new("RGB", (width, height), (28, 30, 36))
    w, h = base.size

    def px0(x, y):
        return ((b["left"] - y) / (b["left"] - b["right"]) * w, (b["top"] - x) / (b["top"] - b["bottom"]) * h)

    crop = (0.0, 0.0, 1.0)
    if b and view.get("basemap"):
        focus = [px0(p["x"], p["y"]) for p in view["track"]]
        focus += [px0(m["x"], m["y"]) for m in view["markers"] if m["role"] != "objective" or m.get("focus")]
        if focus:
            xs, ys = [p[0] for p in focus], [p[1] for p in focus]
            cw = max((max(xs) - min(xs)) * 1.5, w * 0.3)
            ch = max((max(ys) - min(ys)) * 1.5, h * 0.3)
            cw, ch = max(cw, ch * w / h), max(ch, cw * h / w)
            if cw < w * 0.95:
                cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
                left = min(max(0.0, cx - cw / 2), w - cw)
                top = min(max(0.0, cy - ch / 2), h - ch)
                base = base.crop((int(left), int(top), int(left + cw), int(top + ch))).resize((w, h), Image.LANCZOS)
                crop = (left, top, w / cw)
    d = ImageDraw.Draw(base, "RGBA")

    def px(x, y):
        ox, oy = px0(x, y)
        return ((ox - crop[0]) * crop[2], (oy - crop[1]) * crop[2])


    if b:
        for m in view["markers"]:
            cx, cy = px(m["x"], m["y"])
            col = ROLE_COLORS.get(m["role"], (255, 255, 255))
            r = 9 if m.get("focus") else 6
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col + (230,), outline=(0, 0, 0, 255), width=2)
        pts = [px(p["x"], p["y"]) for p in view["track"]]
        if len(pts) > 1:
            d.line(pts, fill=(40, 200, 255, 255), width=4, joint="curve")
        if pts:
            sx, sy = pts[0]
            ex, ey = pts[-1]
            d.ellipse((sx - 8, sy - 8, sx + 8, sy + 8), fill=(255, 255, 255, 255), outline=(0, 0, 0, 255), width=2)
            d.rectangle((ex - 8, ey - 8, ex + 8, ey + 8), fill=(40, 200, 255, 255), outline=(0, 0, 0, 255), width=2)
        for e in view["events"]:
            ex, ey = px(e["x"], e["y"])
            col = (230, 50, 50, 255) if "die" in e["event"].lower() or "dead" in e["event"].lower() else (255, 255, 255, 200)
            d.ellipse((ex - 4, ey - 4, ex + 4, ey + 4), fill=col, outline=(0, 0, 0, 255))

    head_h = 96
    out = Image.new("RGB", (w, h + head_h), (20, 22, 27))
    out.paste(base, (0, head_h))
    hd = ImageDraw.Draw(out)
    verdict = run["verdict"]
    hd.rounded_rectangle((16, 16, 150, 56), 8, fill=VERDICT_COLORS.get(verdict, (120, 120, 120)))
    hd.text((83, 36), verdict, fill=(255, 255, 255), font=font(22), anchor="mm")
    hd.text((166, 14), run["test"][:110], fill=(235, 235, 235), font=font(22))
    hd.text((166, 46), f"{run['core']} · {run['ts']} · host {run['host']}", fill=(170, 175, 185), font=font(17))
    hd.text((16, 70), run["msg"][:160], fill=(210, 210, 210), font=font(17))
    legend = "track: white dot = start, blue square = end   ● giver  ● objective  ● turn-in"
    if view.get("basemap"):
        legend += f"   map: {view['basemap']['name']}"
    hd.text((w - 16, h + head_h - 10), legend, fill=(255, 255, 255), font=font(15), anchor="rd",
            stroke_width=3, stroke_fill=(0, 0, 0))
    return out


def render_run_png(app, view):
    buf = io.BytesIO()
    render_run(app, view).save(buf, "PNG", optimize=True)
    return buf.getvalue()
