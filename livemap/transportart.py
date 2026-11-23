import json
import os
import struct
import sys
import threading
import time

import numpy as np
from PIL import Image

from . import wmomodel
from .clientdata import ClientArchives, dbc_string, read_dbc

PX = 288
TEX_PX = 64
MIN_UP = 0.05
AMBIENT = 0.55
BATCH_PX = 600000
VERSION = 1


def load_wmo(archives, path):
    base = path[:-4] if path.lower().endswith(".wmo") else path
    data = archives.read(path)
    root = wmomodel.parse_root(data) if data else None
    if not root:
        return None
    verts, tris, uvs, texs = [], [], [], []
    offset = 0
    for index in range(root["n_groups"]):
        data = archives.read(f"{base}_{index:03d}.wmo")
        group = wmomodel.parse_group(data) if data else None
        if not group:
            continue
        count = len(group["verts"])
        verts.append(group["verts"])
        tris.append(group["tris"] + offset)
        uvs.append(group["uvs"] if group["uvs"] is not None else np.zeros((count, 2), np.float32))
        texs.append(group["mats"])
        offset += count
    if not verts:
        return None
    return {"verts": np.concatenate(verts), "tris": np.concatenate(tris), "uvs": np.concatenate(uvs),
            "tex": np.concatenate(texs), "textures": root["textures"]}


def load_m2(archives, path):
    base = path.rsplit(".", 1)[0]
    data = archives.read(base + ".m2") or archives.read(path)
    if not data or len(data) < 8:
        return None
    skin = archives.read(base + "00.skin") if struct.unpack_from("<I", data, 4)[0] >= 264 else None
    model = None
    try:
        model = wmomodel.parse_m2(data, skin)
    except (struct.error, ValueError, IndexError):
        return None
    return model


def load_model(archives, path):
    if path.lower().endswith(".wmo"):
        return load_wmo(archives, path)
    return load_m2(archives, path)


def render(model, archives, px=PX):
    verts = model["verts"]
    tris = model["tris"]
    if not len(tris):
        return None
    low, high = verts.min(axis=0), verts.max(axis=0)
    ext = [float(low[0]), float(low[1]), float(high[0]), float(high[1])]
    span_x = max(1e-3, ext[2] - ext[0])
    span_y = max(1e-3, ext[3] - ext[1])
    scale = px / max(span_x, span_y)
    width = max(1, round(span_y * scale))
    height = max(1, round(span_x * scale))
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    e1 = verts[b] - verts[a]
    e2 = verts[c] - verts[a]
    normal = np.cross(e1, e2)
    upness = np.abs(normal[:, 2]) / (np.linalg.norm(normal, axis=1) + 1e-6)
    keep = np.nonzero(upness > MIN_UP)[0]
    if not len(keep):
        return None
    tris, a, b, c, upness = tris[keep], a[keep], b[keep], c[keep], upness[keep]
    textures = model["textures"]
    mats = model["tex"][keep]
    atlas, slots = [], {}
    for mat in set(mats.tolist()):
        name = textures[mat] if 0 <= mat < len(textures) else ""
        if name in slots:
            continue
        image = archives.image(name) if name else None
        if image is None:
            tile = np.full((TEX_PX, TEX_PX, 4), (148, 142, 132, 255), np.uint8)
        else:
            tile = np.asarray(image.convert("RGBA").resize((TEX_PX, TEX_PX), Image.BILINEAR), np.uint8)
        slots[name] = len(atlas)
        atlas.append(tile)
    tex_slot = np.array([slots[textures[m] if 0 <= m < len(textures) else ""] for m in mats.tolist()], np.int32)

    u = (high[1] - verts[:, 1]) * scale
    v = (high[0] - verts[:, 0]) * scale
    z = verts[:, 2]
    uvs = model["uvs"]
    x0 = np.floor(np.minimum(np.minimum(u[a], u[b]), u[c])).astype(np.int64)
    x1 = np.ceil(np.maximum(np.maximum(u[a], u[b]), u[c])).astype(np.int64)
    y0 = np.floor(np.minimum(np.minimum(v[a], v[b]), v[c])).astype(np.int64)
    y1 = np.ceil(np.maximum(np.maximum(v[a], v[b]), v[c])).astype(np.int64)
    x0 = np.clip(x0, 0, width - 1)
    x1 = np.clip(x1, 0, width - 1)
    y0 = np.clip(y0, 0, height - 1)
    y1 = np.clip(y1, 0, height - 1)
    ww = x1 - x0 + 1
    hh = y1 - y0 + 1
    counts = ww * hh
    order = np.argsort(-counts)
    light = (AMBIENT + (1.0 - AMBIENT) * upness).astype(np.float32)
    uv_tri = uvs[tris]
    tri_z = z[tris]
    ax = np.stack([u[a], u[b], u[c]], 1)
    ay = np.stack([v[a], v[b], v[c]], 1)
    zbuf = np.full(width * height, -np.inf, np.float32)
    rgb = np.zeros((width * height, 3), np.float32)
    atlas_arr = np.stack(atlas)
    start = 0
    while start < len(order):
        end = start
        total = 0
        while end < len(order) and total < BATCH_PX:
            total += int(counts[order[end]])
            end += 1
        ids = order[start:end]
        start = end
        occ = counts[ids].astype(np.int64)
        tri_pick = np.repeat(ids, occ)
        local = np.arange(occ.sum(), dtype=np.int64) - np.repeat(np.cumsum(occ) - occ, occ)
        cx = x0[tri_pick] + local % ww[tri_pick]
        cy = y0[tri_pick] + local // ww[tri_pick]
        fx = cx.astype(np.float32) + 0.5
        fy = cy.astype(np.float32) + 0.5
        X0, X1, X2 = ax[tri_pick].T
        Y0, Y1, Y2 = ay[tri_pick].T
        den = (Y1 - Y2) * (X0 - X2) + (X2 - X1) * (Y0 - Y2)
        ok = np.abs(den) > 1e-6
        den = np.where(ok, den, 1.0)
        l0 = ((Y1 - Y2) * (fx - X2) + (X2 - X1) * (fy - Y2)) / den
        l1 = ((Y2 - Y0) * (fx - X2) + (X0 - X2) * (fy - Y2)) / den
        l2 = 1.0 - l0 - l1
        inside = ok & (l0 >= -1e-3) & (l1 >= -1e-3) & (l2 >= -1e-3)
        if not inside.any():
            continue
        tri_pick, cx, cy = tri_pick[inside], cx[inside], cy[inside]
        l0, l1, l2 = l0[inside], l1[inside], l2[inside]
        tu = l0 * uv_tri[tri_pick, 0, 0] + l1 * uv_tri[tri_pick, 1, 0] + l2 * uv_tri[tri_pick, 2, 0]
        tv = l0 * uv_tri[tri_pick, 0, 1] + l1 * uv_tri[tri_pick, 1, 1] + l2 * uv_tri[tri_pick, 2, 1]
        su = (np.mod(tu, 1.0) * TEX_PX).astype(np.int32) % TEX_PX
        sv = (np.mod(tv, 1.0) * TEX_PX).astype(np.int32) % TEX_PX
        texel = atlas_arr[tex_slot[tri_pick], sv, su]
        solid = texel[:, 3] >= 128
        if not solid.any():
            continue
        tri_pick, cx, cy = tri_pick[solid], cx[solid], cy[solid]
        l0, l1, l2, texel = l0[solid], l1[solid], l2[solid], texel[solid]
        depth = l0 * tri_z[tri_pick, 0] + l1 * tri_z[tri_pick, 1] + l2 * tri_z[tri_pick, 2]
        pix = cy * width + cx
        rank = np.lexsort((depth, pix))
        pix, depth, tri_pick, texel = pix[rank], depth[rank], tri_pick[rank], texel[rank]
        last = np.ones(pix.size, bool)
        last[:-1] = pix[1:] != pix[:-1]
        win = last & (depth > zbuf[pix])
        if not win.any():
            continue
        pix, depth, tri_pick, texel = pix[win], depth[win], tri_pick[win], texel[win]
        zbuf[pix] = depth
        rgb[pix] = texel[:, :3].astype(np.float32) / 255.0 * light[tri_pick, None]
    covered = np.isfinite(zbuf).reshape(height, width)
    if not covered.any():
        return None
    out = np.zeros((height, width, 4), np.uint8)
    out[:, :, :3] = np.clip(rgb.reshape(height, width, 3) * 255.0, 0, 255).astype(np.uint8)
    out[:, :, 3] = np.where(covered, 255, 0).astype(np.uint8)
    return Image.fromarray(out, "RGBA"), ext


class TransportArt:
    def __init__(self, app, core, world):
        self.app = app
        self.core = core
        self.world = world
        self.lock = threading.Lock()
        self.info = {}
        self.failed = {}
        self.pending = set()
        self.render_lock = threading.Lock()
        self.archives = None
        self.display = None
        self.directory = os.path.join(app.cfg.data_dir, "models", "transports")

    def dbc(self):
        if self.display is None:
            self.display = {}
            path = os.path.join(self.core.dbc, "GameObjectDisplayInfo.dbc")
            try:
                records, strings = read_dbc(path)
                self.display = {r[0]: dbc_string(strings, r[1]) for r in records if len(r) > 1 and r[1]}
            except (OSError, IndexError, ValueError):
                self.display = {}
        return self.display

    def client(self):
        if self.archives is None:
            client = self.app.client_dir(self.core.name) or self.core.client
            self.archives = ClientArchives(client, self.core.locale)
        return self.archives

    def key(self, entry):
        return int(entry)

    def cached(self, entry):
        base = os.path.join(self.directory, f"{self.core.name}_{entry}")
        try:
            with open(base + ".json", encoding="utf-8") as fh:
                meta = json.load(fh)
            if meta.get("version") != VERSION or not os.path.isfile(base + ".webp"):
                return None
            return meta
        except (OSError, ValueError):
            return None

    def path(self, entry):
        return os.path.join(self.directory, f"{self.core.name}_{int(entry)}.webp")

    def describe(self, entry):
        entry = self.key(entry)
        with self.lock:
            if entry in self.info:
                return self.info[entry]
            failed = self.failed.get(entry)
            if failed and time.time() - failed < 300:
                return None
        meta = self.cached(entry)
        if meta is None:
            self.queue(entry)
            return None
        with self.lock:
            self.info[entry] = meta
            self.failed.pop(entry, None)
        return meta

    def queue(self, entry):
        with self.lock:
            if entry in self.pending or entry in self.info:
                return
            self.pending.add(entry)
        threading.Thread(target=self.build, args=(entry,), daemon=True, name=f"transport-art-{entry}").start()

    def build(self, entry):
        try:
            with self.render_lock:
                meta = self.render(entry)
        except Exception as exc:
            print(f"transport art {self.core.name} {entry} failed: {exc}", file=sys.stderr)
            meta = None
        with self.lock:
            self.pending.discard(entry)
            if meta:
                self.info[entry] = meta
                self.failed.pop(entry, None)
            else:
                self.info.pop(entry, None)
                self.failed[entry] = time.time()

    def render(self, entry):
        display = self.dbc()
        model = None
        try:
            rows = self.world.rows("select t.displayId from gameobject_template t where t.entry=%s"
                                   + self.world.tpl_filter("gameobject_template", "t"), (entry,))
        except Exception:
            rows = []
        if not rows:
            return None
        name = display.get(int(rows[0][0] or 0))
        if not name:
            return None
        model = load_model(self.client(), name)
        if model is None:
            return None
        out = render(model, self.client())
        if out is None:
            return None
        image, ext = out
        os.makedirs(self.directory, exist_ok=True)
        base = os.path.join(self.directory, f"{self.core.name}_{entry}")
        tmp = base + ".webp.tmp"
        image.save(tmp, "WEBP", quality=90, method=4)
        os.replace(tmp, base + ".webp")
        meta = {"version": VERSION, "entry": entry, "model": name,
                "x0": round(ext[0], 2), "y0": round(ext[1], 2), "x1": round(ext[2], 2), "y1": round(ext[3], 2),
                "w": image.width, "h": image.height, "built": time.strftime("%Y-%m-%d %H:%M")}
        tmp = base + ".json.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(meta, fh)
        os.replace(tmp, base + ".json")
        return meta
