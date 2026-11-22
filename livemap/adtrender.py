import io
import math
import os
import re
import struct
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image

from . import wmomodel
from .clientdata import ClientArchives, dbc_string, read_dbc

ADT_YD = 533.33333333
GRID_YD = 17066.66666667
ADT_PX = 1024
PPC = 64
REPEATS = 8
TILE_PX = 256
MIN_LEVEL = -1
MAX_LEVEL = 1
RENDER_VERSION = 4
CHUNK_YD = ADT_YD / 16
APRON_PX = ADT_PX + 128

ADT_PARSE_MB = 80
RENDER_MB = 80
TEXTURE_MB = 24
WMO_MB = 40
BUILD_FACING = 1.0
BUILD_MIN_UP = 0.15
BUILD_AMBIENT = 0.62
BUILD_BATCH = 1500000
BUILD_STEP = 1.5
BUILD_EDGE = 0.6
BUILD_TEX_PX = 32

SHADING = {
    "soft": {"light": (-0.30, -0.40, 0.87), "amb": 0.60, "k": 0.45, "lo": 0.50, "hi": 1.10, "sh": 0.15},
    "medium": {"light": (-0.35, -0.45, 0.82), "amb": 0.45, "k": 0.65, "lo": 0.35, "hi": 1.20, "sh": 0.25},
    "strong": {"light": (-0.50, -0.60, 0.62), "amb": 0.20, "k": 1.00, "lo": 0.12, "hi": 1.40, "sh": 0.30},
    "flat": {"light": None, "amb": 1.00, "k": 0.00, "lo": 1.00, "hi": 1.00, "sh": 0.00},
}
SHADE_NAMES = ("soft", "medium", "strong", "flat")
NO_BUILDINGS = "-nobld"

WATER_COLOUR = {
    "water": (0.16, 0.30, 0.36),
    "ocean": (0.10, 0.22, 0.34),
    "magma": (0.95, 0.35, 0.05),
    "slime": (0.35, 0.60, 0.15),
}

MISSING = object()
SAFE = re.compile(r"[^a-z0-9_.-]+")
SKIP_ARCHIVES = re.compile(r"^(sound|speech|fonts|item|dbc|interface|misc|backup|model)", re.I)


class Lru:
    def __init__(self, max_items=None, max_bytes=None, sizer=None):
        self.max_items = max_items
        self.max_bytes = max_bytes
        self.sizer = sizer
        self.items = OrderedDict()
        self.bytes = 0
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            if key not in self.items:
                return MISSING
            self.items.move_to_end(key)
            return self.items[key]

    def put(self, key, value):
        size = self.sizer(value) if self.sizer and value is not None else 0
        with self.lock:
            if key in self.items:
                self.bytes -= self.sizer(self.items[key]) if self.sizer and self.items[key] is not None else 0
                del self.items[key]
            self.items[key] = value
            self.bytes += size
            while self.items and ((self.max_items and len(self.items) > self.max_items)
                                  or (self.max_bytes and self.bytes > self.max_bytes)):
                _, old = self.items.popitem(last=False)
                self.bytes -= self.sizer(old) if self.sizer and old is not None else 0

    def clear(self):
        with self.lock:
            self.items.clear()
            self.bytes = 0


def subchunks(data, start, end):
    i = start
    out = {}
    while i + 8 <= end:
        tag = data[i:i + 4][::-1].decode("latin-1")
        n = struct.unpack_from("<I", data, i + 4)[0]
        out.setdefault(tag, []).append((i + 8, n))
        i += 8 + n
    return out


class Chunk:
    __slots__ = ("ix", "iy", "heights", "layers", "shadow", "holes", "liquid")


class Adt:
    __slots__ = ("col", "row", "textures", "chunks", "hf", "placements", "valid", "bytes")


def upsample_matrix(n_in, n_out):
    pos = (np.arange(n_out, dtype=np.float32) + 0.5) * (n_in / n_out) - 0.5
    pos = np.clip(pos, 0.0, n_in - 1.0)
    low = np.floor(pos).astype(np.int64)
    high = np.minimum(low + 1, n_in - 1)
    weight = (pos - low).astype(np.float32)
    matrix = np.zeros((n_out, n_in), np.float32)
    rows = np.arange(n_out)
    matrix[rows, low] += 1.0 - weight
    matrix[rows, high] += weight
    return matrix


HEIGHT_UP = upsample_matrix(9, PPC)


def cubic_matrix(n_in, n_out, scale):
    pos = np.arange(n_out, dtype=np.float32) / scale
    base = np.floor(pos).astype(np.int64)
    frac = (pos - base).astype(np.float32)
    weights = np.stack([
        -0.5 * frac ** 3 + frac ** 2 - 0.5 * frac,
        1.5 * frac ** 3 - 2.5 * frac ** 2 + 1.0,
        -1.5 * frac ** 3 + 2.0 * frac ** 2 + 0.5 * frac,
        0.5 * frac ** 3 - 0.5 * frac ** 2,
    ], axis=1).astype(np.float32)
    matrix = np.zeros((n_out, n_in), np.float32)
    for tap in range(4):
        index = base - 1 + tap
        valid = (index >= 0) & (index < n_in)
        rows = np.nonzero(valid)[0]
        matrix[rows, index[valid]] += weights[rows, tap]
    total = matrix.sum(axis=1)
    return matrix / np.where(total == 0, 1.0, total)[:, None]


RELIEF_UP = cubic_matrix(145, APRON_PX, 8.0)


def parse_adt(data, col, row):
    top = subchunks(data, 0, len(data))
    mtex = top.get("MTEX")
    if not mtex:
        return None
    offset, size = mtex[0]
    textures = [t.decode("latin-1") for t in data[offset:offset + size].split(b"\0") if t]
    adt = Adt()
    adt.col = col
    adt.row = row
    adt.textures = textures
    adt.chunks = []
    adt.bytes = len(data)
    hf = np.zeros((129, 129), np.float32)
    for mcnk_offset, mcnk_size in top.get("MCNK", []):
        base = mcnk_offset - 8
        chunk = parse_chunk(data, base, mcnk_offset, textures)
        if chunk is None:
            continue
        adt.chunks.append(chunk)
        h = chunk.heights
        outer = np.concatenate([h[r * 17:r * 17 + 9] for r in range(9)]).reshape(9, 9)
        hf[chunk.iy * 8:chunk.iy * 8 + 9, chunk.ix * 8:chunk.ix * 8 + 9] = outer
    adt.hf = hf
    try:
        adt.placements = wmomodel.adt_placements(data)
    except struct.error:
        adt.placements = []
    adt.valid = bool(adt.chunks)
    adt.bytes = sum(c.heights.nbytes + sum(a.nbytes for _, a in c.layers if a is not None)
                    + (c.shadow.nbytes if c.shadow is not None else 0) for c in adt.chunks)
    return adt


def parse_chunk(data, base, offset, textures):
    try:
        (flags, ix, iy, nlayers, _, ofs_h, ofs_n, ofs_l, _, ofs_a, size_a, ofs_s, size_s,
         area, _, holes) = struct.unpack_from("<15IH", data, offset)
        ofs_liq, size_liq = struct.unpack_from("<2I", data, offset + 96)
        pos = struct.unpack_from("<3f", data, offset + 104)
    except struct.error:
        return None
    if ix > 15 or iy > 15 or nlayers > 8:
        return None
    chunk = Chunk()
    chunk.ix = ix
    chunk.iy = iy
    chunk.holes = holes
    try:
        heights = np.frombuffer(data, "<f4", 145, base + ofs_h + 8) + pos[2]
    except ValueError:
        return None
    if heights.shape[0] != 145:
        return None
    chunk.heights = heights
    layers = []
    for k in range(nlayers):
        try:
            tex, lflags, aofs, _ = struct.unpack_from("<4I", data, base + ofs_l + 8 + k * 16)
        except struct.error:
            break
        if tex >= len(textures):
            continue
        alpha = read_alpha(data, base + ofs_a + 8, aofs, flags & 0x8000) if k else None
        if k and alpha is None:
            continue
        layers.append((tex, alpha))
    chunk.layers = layers
    chunk.shadow = None
    if flags & 1 and size_s:
        bits = np.unpackbits(np.frombuffer(data, np.uint8, 512, base + ofs_s + 8), bitorder="little")
        chunk.shadow = (bits.reshape(64, 64) * 255).astype(np.uint8)
    chunk.liquid = parse_liquid(data, base + ofs_liq + 8, flags) if size_liq and flags & 0x3C else None
    return chunk


def read_alpha(data, base, aofs, big):
    try:
        raw = np.frombuffer(data, np.uint8, 2048, base + aofs)
    except ValueError:
        return None
    if raw.shape[0] != 2048:
        return None
    alpha = np.empty(4096, np.uint8)
    alpha[0::2] = raw & 0x0F
    alpha[1::2] = raw >> 4
    alpha *= 17
    alpha = alpha.reshape(64, 64)
    if not big:
        alpha[:, 63] = alpha[:, 62]
        alpha[63, :] = alpha[62, :]
    return alpha


def parse_liquid(data, base, flags):
    try:
        verts = np.frombuffer(data, "<f4", 81 * 2, base + 8).reshape(81, 2)
        tiles = np.frombuffer(data, np.uint8, 64, base + 8 + 81 * 8).reshape(8, 8)
    except ValueError:
        return None
    height = np.array(verts[:, 1], np.float32)
    kind = "magma" if flags & 0x10 else "slime" if flags & 0x20 else "ocean" if flags & 8 else "water"
    return {"height": height.reshape(9, 9), "show": np.array((tiles & 0x0F) != 0x0F), "kind": kind}


class TextureCache:
    def __init__(self, archives, lru, prefix):
        self.archives = archives
        self.lru = lru
        self.prefix = prefix
        self.miss = np.zeros((PPC, PPC, 3), np.float32)
        self.miss[:, :, 0] = 1.0
        self.miss[:, :, 1] = 0.0
        self.miss[:, :, 2] = 1.0

    def tiled(self, name):
        key = (self.prefix, name.lower())
        hit = self.lru.get(key)
        if hit is not MISSING:
            return hit
        image = self.archives.image(name)
        if image is None:
            tile = self.miss
        else:
            small = image.convert("RGB").resize((PPC // REPEATS, PPC // REPEATS), Image.LANCZOS)
            tile = np.tile(np.asarray(small, np.float32) / 255.0, (REPEATS, REPEATS, 1))
        self.lru.put(key, tile)
        return tile


def render_adt(adt, textures, preset, pad, buildings=None):
    rgb = np.zeros((ADT_PX, ADT_PX, 3), np.float32)
    alpha = np.full((ADT_PX, ADT_PX), 255, np.uint8)
    darken = preset["sh"]
    for chunk in adt.chunks:
        if not chunk.layers:
            continue
        y0 = chunk.iy * PPC
        x0 = chunk.ix * PPC
        colour = textures.tiled(adt.textures[chunk.layers[0][0]])
        for tex, blend in chunk.layers[1:]:
            weight = blend.astype(np.float32)[..., None] * (1.0 / 255.0)
            colour = colour * (1.0 - weight) + textures.tiled(adt.textures[tex]) * weight
        if chunk.shadow is not None and darken > 0:
            shadow = chunk.shadow.astype(np.float32) * (1.0 / 255.0)
            colour = colour * (1.0 - darken * shadow)[..., None]
        rgb[y0:y0 + PPC, x0:x0 + PPC] = colour
        if chunk.holes:
            block = PPC // 4
            for bit in range(16):
                if chunk.holes & (1 << bit):
                    hy, hx = bit // 4, bit % 4
                    alpha[y0 + hy * block:y0 + (hy + 1) * block, x0 + hx * block:x0 + (hx + 1) * block] = 0
    heights = relief_field(pad)
    rgb *= shaded_relief(heights, preset)[..., None]
    for chunk in adt.chunks:
        liquid = chunk.liquid
        if liquid is None:
            continue
        y0 = chunk.iy * PPC
        x0 = chunk.ix * PPC
        terrain = np.concatenate([chunk.heights[r * 17:r * 17 + 9] for r in range(9)]).reshape(9, 9)
        depth = (HEIGHT_UP @ (liquid["height"] - terrain) @ HEIGHT_UP.T) / 12.0
        mask = np.repeat(np.repeat(liquid["show"], PPC // 8, axis=0), PPC // 8, axis=1) & (depth > 0)
        if liquid["kind"] in ("water", "ocean"):
            weight = np.where(mask, 0.30 + 0.55 * np.clip(depth, 0.0, 1.0), 0.0).astype(np.float32)
        else:
            weight = np.where(mask, 0.92, 0.0).astype(np.float32)
        if not weight.any():
            continue
        colour = np.array(WATER_COLOUR[liquid["kind"]], np.float32)
        block = rgb[y0:y0 + PPC, x0:x0 + PPC]
        block *= (1.0 - weight)[..., None]
        block += colour * weight[..., None]
    if buildings is not None:
        b_rgb, b_mask = buildings
        rgb[b_mask] = b_rgb[b_mask]
        alpha[b_mask] = 255
    out = np.empty((ADT_PX, ADT_PX, 4), np.uint8)
    out[:, :, :3] = np.clip(rgb * 255.0, 0, 255).astype(np.uint8)
    out[:, :, 3] = alpha
    return Image.fromarray(out, "RGBA")


def draw_buildings(renderer, placements, left, top):
    yard_px = CHUNK_YD / PPC
    size = ADT_PX
    zbuf = np.full(size * size, -np.inf, np.float32)
    rgb = np.zeros((size * size, 3), np.float32)
    atlas, slots = [], {}
    batches = []
    seen = set()
    for uid, name, position, rotation, scale in placements:
        mark = (name.lower(), uid)
        if mark in seen:
            continue
        seen.add(mark)
        root = renderer.wmo_root(name)
        if root is None:
            continue
        lo, hi = root["bbox"]
        if lo != hi and not wmomodel.box_in_view(lo, hi, position, rotation, scale, left, top, yard_px, size):
            continue
        textures = root["textures"]
        for index in range(min(root["n_groups"], len(root["groups"]))):
            gmin, gmax = root["groups"][index]
            if not wmomodel.box_in_view(gmin, gmax, position, rotation, scale, left, top, yard_px, size):
                continue
            group = renderer.wmo_group(name, index)
            if group is None:
                continue
            wx, wy, wz = wmomodel.transform(group["verts"], position, rotation, scale)
            px = ((left - wy) / yard_px).astype(np.float32)
            py = ((top - wx) / yard_px).astype(np.float32)
            tris = group["tris"]
            mats = group["mats"]
            a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
            e1 = np.stack([wx[b] - wx[a], wy[b] - wy[a], wz[b] - wz[a]], axis=1)
            e2 = np.stack([wx[c] - wx[a], wy[c] - wy[a], wz[c] - wz[a]], axis=1)
            normal = np.cross(e1, e2)
            upness = normal[:, 2] * BUILD_FACING / (np.linalg.norm(normal, axis=1) + 1e-6)
            keep = (mats != 255) & (upness > BUILD_MIN_UP)
            keep &= (np.maximum(np.maximum(px[a], px[b]), px[c]) >= 0) & (np.minimum(np.minimum(px[a], px[b]), px[c]) <= size)
            keep &= (np.maximum(np.maximum(py[a], py[b]), py[c]) >= 0) & (np.minimum(np.minimum(py[a], py[b]), py[c]) <= size)
            if not keep.any():
                continue
            sel = np.nonzero(keep)[0]
            uvs = group.get("uvs")
            if uvs is None:
                uvs = np.zeros((len(group["verts"]), 2), np.float32)
            tex = np.empty(sel.size, np.int32)
            for mat in set(mats[sel].tolist()):
                name_t = textures[mat] if mat < len(textures) else ""
                slot = slots.get(name_t)
                if slot is None:
                    slot = slots[name_t] = len(atlas)
                    atlas.append(renderer.wmo_texture(name_t))
                tex[mats[sel] == mat] = slot
            t = tris[sel]
            batches.append((px[t], py[t], wz[t].astype(np.float32), uvs[t], tex,
                            (BUILD_AMBIENT + (1.0 - BUILD_AMBIENT) * upness[sel]).astype(np.float32)))
    if not batches:
        return np.zeros((size, size, 3), np.float32), np.zeros((size, size), bool)
    tex_atlas = np.stack(atlas)
    tsize = tex_atlas.shape[1]
    X = np.concatenate([bt[0] for bt in batches])
    Y = np.concatenate([bt[1] for bt in batches])
    Z = np.concatenate([bt[2] for bt in batches])
    UV = np.concatenate([bt[3] for bt in batches])
    TEX = np.concatenate([bt[4] for bt in batches])
    LIGHT = np.concatenate([bt[5] for bt in batches])
    x0 = np.clip(np.floor(X.min(axis=1)), 0, size - 1).astype(np.int64)
    x1 = np.clip(np.ceil(X.max(axis=1)), 0, size - 1).astype(np.int64)
    y0 = np.clip(np.floor(Y.min(axis=1)), 0, size - 1).astype(np.int64)
    y1 = np.clip(np.ceil(Y.max(axis=1)), 0, size - 1).astype(np.int64)
    w = x1 - x0 + 1
    h = y1 - y0 + 1
    counts = w * h
    denom = (Y[:, 1] - Y[:, 2]) * (X[:, 0] - X[:, 2]) + (X[:, 2] - X[:, 1]) * (Y[:, 0] - Y[:, 2])
    valid = np.abs(denom) > 1e-4
    order = np.nonzero(valid)[0]
    start = 0
    while start < order.size:
        total = 0
        end = start
        while end < order.size and (total == 0 or total + counts[order[end]] <= BUILD_BATCH):
            total += counts[order[end]]
            end += 1
        ids = order[start:end]
        start = end
        n = counts[ids]
        tri = np.repeat(ids, n)
        local = np.arange(n.sum(), dtype=np.int64) - np.repeat(np.cumsum(n) - n, n)
        cx = x0[tri] + local % w[tri]
        cy = y0[tri] + local // w[tri]
        fx = cx.astype(np.float32) + 0.5
        fy = cy.astype(np.float32) + 0.5
        d = denom[tri]
        l0 = ((Y[tri, 1] - Y[tri, 2]) * (fx - X[tri, 2]) + (X[tri, 2] - X[tri, 1]) * (fy - Y[tri, 2])) / d
        l1 = ((Y[tri, 2] - Y[tri, 0]) * (fx - X[tri, 2]) + (X[tri, 0] - X[tri, 2]) * (fy - Y[tri, 2])) / d
        l2 = 1.0 - l0 - l1
        inside = (l0 >= -1e-3) & (l1 >= -1e-3) & (l2 >= -1e-3)
        if not inside.any():
            continue
        tri, cx, cy, l0, l1, l2 = tri[inside], cx[inside], cy[inside], l0[inside], l1[inside], l2[inside]
        z = l0 * Z[tri, 0] + l1 * Z[tri, 1] + l2 * Z[tri, 2]
        pix = cy * size + cx
        rank = np.lexsort((z, pix))
        pix, z, tri, l0, l1, l2 = pix[rank], z[rank], tri[rank], l0[rank], l1[rank], l2[rank]
        last = np.ones(pix.size, bool)
        last[:-1] = pix[1:] != pix[:-1]
        win = last & (z > zbuf[pix])
        if not win.any():
            continue
        pix, z, tri, l0, l1, l2 = pix[win], z[win], tri[win], l0[win], l1[win], l2[win]
        u = l0 * UV[tri, 0, 0] + l1 * UV[tri, 1, 0] + l2 * UV[tri, 2, 0]
        v = l0 * UV[tri, 0, 1] + l1 * UV[tri, 1, 1] + l2 * UV[tri, 2, 1]
        su = (np.mod(u, 1.0) * tsize).astype(np.int32) % tsize
        sv = (np.mod(v, 1.0) * tsize).astype(np.int32) % tsize
        zbuf[pix] = z
        rgb[pix] = tex_atlas[TEX[tri], sv, su] * LIGHT[tri, None]
    covered = np.isfinite(zbuf).reshape(size, size)
    data = rgb.reshape(size, size, 3)
    height = np.where(covered, zbuf.reshape(size, size), -1e9)
    edge = np.zeros_like(covered)
    inner = covered[1:-1, 1:-1]
    hc = height[1:-1, 1:-1]
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx or dy:
                nb = height[1 + dy:size - 1 + dy, 1 + dx:size - 1 + dx]
                edge[1:-1, 1:-1] |= inner & (nb < hc - BUILD_STEP)
    data[edge] *= BUILD_EDGE
    return data, covered


def relief_field(pad):
    if pad is None:
        return None
    big = RELIEF_UP @ pad @ RELIEF_UP.T
    return big[64:64 + ADT_PX, 64:64 + ADT_PX]


def shaded_relief(heights, preset):
    if preset["light"] is None or heights is None:
        return np.ones((ADT_PX, ADT_PX), np.float32)
    gy, gx = np.gradient(heights, CHUNK_YD / PPC)
    lx, ly, lz = preset["light"]
    norm = np.sqrt(gx * gx + gy * gy + 1.0)
    light_norm = math.sqrt(lx * lx + ly * ly + lz * lz)
    lambert = ((-gx) * lx + (-gy) * ly + lz) / (norm * light_norm)
    return np.clip(preset["amb"] + preset["k"] * lambert, preset["lo"], preset["hi"])


class Renderer:
    def __init__(self, core, client, locale, dbc_dir, caches):
        self.client = client
        self.locale = locale
        self.dbc_dir = dbc_dir
        self.key = f"{client}|{locale}"
        self.caches = caches
        self.archives = ClientArchives(client, locale, skip=SKIP_ARCHIVES)
        self.textures = TextureCache(self.archives, caches["tex"], self.key)
        self.adts = caches["adt"]
        self.images = caches["img"]
        self.wmo_roots = caches["wmo"]
        self.wmo_groups = caches["wmogroup"]
        self.wmo_colours = caches["wmocol"]
        self.wmo_pixels = caches["wmopix"]
        self.tiles = {}
        self.probes = {}
        self.lock = threading.RLock()
        self._map_dirs = None

    def adt_path(self, col, row, directory):
        return f"World\\Maps\\{directory}\\{directory}_{col}_{row}.adt"

    def map_dirs(self):
        with self.lock:
            if self._map_dirs is None:
                try:
                    records, strings = read_dbc(os.path.join(self.dbc_dir, "Map.dbc"))
                    self._map_dirs = {r[0]: dbc_string(strings, r[1]) for r in records}
                except OSError:
                    self._map_dirs = {}
            return self._map_dirs

    def map_dir(self, map_id):
        return self.map_dirs().get(int(map_id)) or None

    def supported(self):
        return 571 not in self.map_dirs()

    def adt(self, col, row, directory):
        key = (self.key, directory, col, row)
        hit = self.adts.get(key)
        if hit is not MISSING:
            return hit
        data = self.archives.read(self.adt_path(col, row, directory))
        adt = parse_adt(data, col, row) if data else None
        if adt is not None and not adt.valid:
            adt = None
        self.adts.put(key, adt)
        return adt

    def preload(self):
        self.archives.preload()

    def wmo_root(self, name):
        key = (self.key, name.lower())
        hit = self.wmo_roots.get(key)
        if hit is not MISSING:
            return hit
        data = self.archives.read(name)
        root = wmomodel.parse_root(data) if data else None
        self.wmo_roots.put(key, root)
        return root

    def wmo_group(self, name, index):
        key = (self.key, name.lower(), index)
        hit = self.wmo_groups.get(key)
        if hit is not MISSING:
            return hit
        path = f"{name[:-4]}_{index:03d}.wmo" if name.lower().endswith(".wmo") else f"{name}_{index:03d}.wmo"
        data = self.archives.read(path)
        group = wmomodel.parse_group(data) if data else None
        self.wmo_groups.put(key, group)
        return group

    def wmo_texture(self, texture):
        key = (self.key, texture.lower())
        hit = self.wmo_pixels.get(key)
        if hit is not MISSING:
            return hit
        image = self.archives.image(texture) if texture else None
        if image is None:
            tile = np.full((BUILD_TEX_PX, BUILD_TEX_PX, 3), 0.62, np.float32)
        else:
            tile = np.asarray(image.convert("RGB").resize((BUILD_TEX_PX, BUILD_TEX_PX), Image.BOX), np.float32) / 255.0
        self.wmo_pixels.put(key, tile)
        return tile

    def wmo_colour(self, texture):
        key = (self.key, texture.lower())
        hit = self.wmo_colours.get(key)
        if hit is not MISSING:
            return hit
        image = self.archives.image(texture) if texture else None
        if image is None:
            colour = (0.62, 0.60, 0.56)
        else:
            r, g, b = image.convert("RGB").resize((1, 1), Image.BOX).getpixel((0, 0))
            colour = (r / 255.0, g / 255.0, b / 255.0)
        self.wmo_colours.put(key, colour)
        return colour

    def prefetch(self, directory, col, row):
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx or dy:
                    self.adt(col + dx, row + dy, directory)

    def first_tile(self, directory):
        with self.lock:
            if directory in self.tiles:
                return self.tiles[directory]
        wdt = self.archives.read(f"World\\Maps\\{directory}\\{directory}.wdt")
        tile = None
        if wdt:
            parts = dict((tag, data) for tag, data in _chunks(wdt))
            main = parts.get("MAIN", b"")
            step = 8 if len(main) >= 4096 * 8 else 4
            for k in range(min(len(main) // step, 4096)):
                if struct.unpack_from("<I", main, k * step)[0] & 1:
                    tile = (k % 64, k // 64)
                    break
        with self.lock:
            self.tiles[directory] = tile
        return tile

    def probe(self, directory):
        if directory in self.probes:
            return self.probes[directory]
        ok = False
        tile = self.first_tile(directory)
        if tile:
            adt = self.adt(tile[0], tile[1], directory)
            ok = adt is not None and bool(adt.textures)
        self.probes[directory] = ok
        return ok

    def image(self, directory, col, row, shade):
        key = (self.key, directory, col, row, shade)
        hit = self.images.get(key)
        if hit is not MISSING:
            return hit
        adt = self.adt(col, row, directory)
        if adt is None:
            return None
        bare = shade.endswith(NO_BUILDINGS)
        preset = SHADING.get(shade[:-len(NO_BUILDINGS)] if bare else shade) or SHADING["soft"]
        pad = padded_heights(self, directory, col, row)
        buildings = None
        placements = None if bare else neighbour_placements(self, directory, col, row)
        if placements:
            left = GRID_YD - col * ADT_YD
            top = GRID_YD - row * ADT_YD
            drawn = draw_buildings(self, placements, left, top)
            if drawn[1].any():
                buildings = drawn
        image = render_adt(adt, self.textures, preset, pad, buildings)
        self.images.put(key, image)
        return image


def neighbour_placements(renderer, directory, col, row):
    out = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            adt = renderer.adt(col + dx, row + dy, directory)
            if adt is not None and adt.placements:
                out.extend(adt.placements)
    return out


def padded_heights(renderer, directory, col, row):
    centre = renderer.adt(col, row, directory)
    if centre is None:
        return None
    block = np.zeros((385, 385), np.float32)
    rows = np.clip(np.arange(129), 0, 128)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            adt = centre if (dx == 0 and dy == 0) else renderer.adt(col + dx, row + dy, directory)
            if adt is None:
                index = np.ix_(np.clip(rows + dy * 128, 0, 128), np.clip(rows + dx * 128, 0, 128))
                cells = centre.hf[index]
            else:
                cells = adt.hf
            y0 = (dy + 1) * 128
            x0 = (dx + 1) * 128
            block[y0:y0 + 129, x0:x0 + 129] = cells
    return block[120:265, 120:265]


def _chunks(data):
    i = 0
    while i + 8 <= len(data):
        tag = data[i:i + 4][::-1].decode("latin-1")
        size = struct.unpack_from("<I", data, i + 4)[0]
        yield tag, data[i + 8:i + 8 + size]
        i += 8 + size


def tile_coords(z, x, y):
    if z < MIN_LEVEL or z > MAX_LEVEL:
        return None
    shift = z + 1
    col = (x >> shift) + 32
    row = (y >> shift) + 32
    if not (0 <= col < 64 and 0 <= row < 64):
        return None
    return z, col, row, x & ((1 << shift) - 1), y & ((1 << shift) - 1)


class DiskCache:
    def __init__(self, data_dir, store):
        self.root = os.path.join(data_dir, "adtcache")
        self.store = store
        self.lock = threading.Lock()
        self.total = None
        self.generation = 0

    def enabled(self):
        return self.store.flag("keep_cache")

    def path(self, viewed, source, directory, col, row, shade):
        safe = lambda s: SAFE.sub("_", str(s).lower()) or "_"
        return os.path.join(self.root, safe(viewed), safe(source), safe(directory), f"v{RENDER_VERSION}",
                            str(col), f"{row}_{safe(shade)}.webp")

    def read(self, path):
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            image = Image.open(io.BytesIO(data))
            image.load()
            os.utime(path, None)
            return image.convert("RGBA")
        except (OSError, ValueError):
            return None

    def write(self, path, image, generation=None):
        if not self.enabled():
            return
        if generation is not None and generation != self.generation:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            buffer = io.BytesIO()
            image.save(buffer, "WEBP", quality=92, method=4)
            data = buffer.getvalue()
            previous = os.path.getsize(path) if os.path.exists(path) else 0
            with open(path, "wb") as fh:
                fh.write(data)
            if self.total is None:
                self.usage()
            with self.lock:
                self.total += len(data) - previous
            self.evict()
        except OSError:
            pass

    def usage(self):
        total = 0
        if os.path.isdir(self.root):
            for base, _, names in os.walk(self.root):
                for name in names:
                    try:
                        total += os.path.getsize(os.path.join(base, name))
                    except OSError:
                        pass
        with self.lock:
            self.total = total
        return total

    def clear(self, viewed=None):
        freed = 0
        with self.lock:
            self.generation += 1
        target = os.path.join(self.root, SAFE.sub("_", str(viewed).lower())) if viewed else self.root
        if not os.path.isdir(target):
            return 0
        for base, _, names in os.walk(target, topdown=False):
            for name in names:
                path = os.path.join(base, name)
                try:
                    freed += os.path.getsize(path)
                    os.remove(path)
                except OSError:
                    pass
            try:
                os.rmdir(base)
            except OSError:
                pass
        with self.lock:
            self.total = None
        return freed

    def evict(self):
        cap = max(16, self.store.number("cache_mb")) * 1024 * 1024
        with self.lock:
            total = self.total if self.total is not None else None
        if total is None:
            total = self.usage()
        if total <= cap:
            return 0
        entries = []
        for base, _, names in os.walk(self.root):
            for name in names:
                path = os.path.join(base, name)
                try:
                    info = os.stat(path)
                except OSError:
                    continue
                entries.append((info.st_mtime, info.st_size, path))
        entries.sort()
        freed = 0
        for _, size, path in entries:
            if total - freed <= cap:
                break
            try:
                os.remove(path)
                freed += size
            except OSError:
                pass
        with self.lock:
            self.total = max(0, total - freed)
        return freed


class Terrain:
    def __init__(self, app):
        self.app = app
        self.cfg = app.cfg
        self.store = app.mapstore
        self.lock = threading.RLock()
        self.renderers = {}
        self.resolved = {}
        self.pending = {}
        self.inflight = {}
        self.warmed = set()
        self.disk = DiskCache(app.cfg.data_dir, self.store)
        self.caches = {
            "adt": Lru(max_bytes=ADT_PARSE_MB * 1024 * 1024,
                       sizer=lambda a: int(a.bytes * 1.25) + 65536 if a else 256),
            "img": Lru(max_bytes=RENDER_MB * 1024 * 1024, sizer=lambda img: img.width * img.height * 4),
            "tex": Lru(max_bytes=TEXTURE_MB * 1024 * 1024, sizer=lambda a: a.nbytes),
            "wmo": Lru(max_items=128, sizer=lambda root: 2048),
            "wmogroup": Lru(max_bytes=WMO_MB * 1024 * 1024,
                            sizer=lambda g: (g["verts"].nbytes + g["tris"].nbytes + g["mats"].nbytes
                                             + (g["uvs"].nbytes if g["uvs"] is not None else 0)
                                             + 4096) if g else 256),
            "wmocol": Lru(max_items=2048, sizer=lambda c: 64),
            "wmopix": Lru(max_bytes=8 * 1024 * 1024, sizer=lambda a: a.nbytes),
        }
        self.executor = self.new_executor()

    def new_executor(self):
        return ThreadPoolExecutor(max_workers=max(1, min(8, self.store.number("render_workers"))),
                                  thread_name_prefix="adt")

    def workers_changed(self):
        wanted = max(1, min(8, self.store.number("render_workers")))
        if getattr(self.executor, "_max_workers", 0) != wanted:
            old, self.executor = self.executor, self.new_executor()
            old.shutdown(wait=False)

    def renderer(self, core_name):
        core = self.cfg.cores[core_name]
        path = self.store.client_dir(core_name) or core.client
        key = (path, core.locale, core.dbc)
        with self.lock:
            renderer = self.renderers.get(key)
            if renderer is None:
                renderer = Renderer(core_name, path, core.locale, core.dbc, self.caches)
                self.renderers[key] = renderer
            return renderer

    def relearn(self):
        with self.lock:
            self.resolved.clear()
            self.warmed.clear()

    def forget_clients(self):
        with self.lock:
            self.renderers.clear()
            self.resolved.clear()
            self.warmed.clear()
            for cache in self.caches.values():
                cache.clear()
        self.disk.evict()

    def resolve(self, core_name, map_id):
        key = (core_name, int(map_id))
        with self.lock:
            if key in self.resolved:
                return self.resolved[key]
            wait = self.pending.get(key)
            owner = wait is None
            if owner:
                wait = threading.Event()
                self.pending[key] = wait
        if not owner:
            wait.wait(timeout=120)
            with self.lock:
                return self.resolved.get(key)
        found = None
        try:
            found = self.locate(core_name, map_id)
        finally:
            with self.lock:
                self.resolved[key] = found
                self.pending.pop(key, None)
            wait.set()
        return found

    def locate(self, core_name, map_id):
        order = [core_name]
        if self.store.flag("fallback"):
            order += [name for name in self.store.order() if name != core_name]
        found = None
        for name in order:
            if name not in self.cfg.cores:
                continue
            renderer = self.renderer(name)
            try:
                if not renderer.supported():
                    continue
                directory = renderer.map_dir(map_id)
                if not directory or not renderer.probe(directory):
                    continue
            except Exception:
                continue
            found = {"core": name, "renderer": renderer, "directory": directory, "fallback": name != core_name}
            break
        return found

    def describe(self, core_name, map_id):
        base = {"available": False, "pending": False, "shade": self.store.shade(), "version": RENDER_VERSION,
                "min_zoom": MIN_LEVEL, "max_zoom": MAX_LEVEL, "shades": list(SHADE_NAMES)}
        if core_name not in self.cfg.cores or map_id is None:
            return base
        key = (core_name, int(map_id))
        with self.lock:
            known = key in self.resolved
            running = key in self.pending
        if not known:
            if not running:
                threading.Thread(target=self.resolve, args=(core_name, map_id),
                                 daemon=True, name="adt-probe").start()
            return dict(base, pending=True)
        info = self.resolved.get(key)
        if not info:
            return base
        self.warm(info)
        return dict(base, available=True, source=info["core"], fallback=info["fallback"],
                    directory=info["directory"])

    def warm(self, info):
        key = (info["core"], info["directory"], self.store.shade())
        with self.lock:
            if key in self.warmed:
                return
            self.warmed.add(key)
        def run():
            try:
                info["renderer"].preload()
                tile = info["renderer"].first_tile(info["directory"])
                if tile:
                    self.image(info["core"], info, self.store.shade(), tile[0], tile[1])
            except Exception:
                pass
        threading.Thread(target=run, daemon=True, name="adt-warm").start()

    def image(self, core_name, info, shade, col, row):
        renderer = info["renderer"]
        directory = info["directory"]
        key = (renderer.key, directory, col, row, shade)
        hit = renderer.images.get(key)
        if hit is not MISSING:
            return hit
        path = self.disk.path(core_name, info["core"], directory, col, row, shade)
        if self.disk.enabled():
            stored = self.disk.read(path)
            if stored is not None:
                renderer.images.put(key, stored)
                return stored
        job = (renderer.key, info["core"], directory, col, row, shade)
        with self.lock:
            future = self.inflight.get(job)
            if future is None:
                future = self.executor.submit(renderer.image, directory, col, row, shade)
                self.inflight[job] = future
                generation = self.disk.generation
                future.add_done_callback(
                    lambda f, j=job, r=renderer, p=path, g=generation: self.done(j, f, r, p, g))
        try:
            return future.result(timeout=45)
        except Exception:
            return None

    def done(self, job, future, renderer, path, generation):
        with self.lock:
            self.inflight.pop(job, None)
        try:
            image = future.result()
        except Exception:
            return
        if image is not None:
            renderer.images.put((job[0], job[2], job[3], job[4], job[5]), image)
            self.disk.write(path, image, generation)
            threading.Thread(target=renderer.prefetch, args=(job[2], job[3], job[4]),
                             daemon=True, name="adt-prefetch").start()

    def tile(self, core_name, map_id, z, x, y, shade=None, buildings=True):
        grabbed = tile_coords(int(z), int(x), int(y))
        if grabbed is None or core_name not in self.cfg.cores:
            return None
        level, col, row, qx, qy = grabbed
        info = self.resolve(core_name, int(map_id))
        if not info:
            return None
        shade = shade if shade in SHADING else self.store.shade()
        if not buildings:
            shade += NO_BUILDINGS
        image = self.image(core_name, info, shade, col, row)
        if image is None:
            return None
        size = ADT_PX >> (level + 1)
        crop = image.crop((qx * size, qy * size, (qx + 1) * size, (qy + 1) * size))
        if size != TILE_PX:
            crop = crop.convert("RGBa").resize((TILE_PX, TILE_PX), Image.LANCZOS).convert("RGBA")
        buffer = io.BytesIO()
        crop.save(buffer, "WEBP", quality=90, method=4)
        return buffer.getvalue()

    def settings_payload(self):
        values = self.store.all()
        clients = {}
        for name, core in self.cfg.cores.items():
            path = self.store.client_dir(name) or core.client
            clients[name] = {"path": path, "exists": bool(path) and os.path.isdir(path),
                             "configured": bool(self.store.client_dir(name))}
        cores_usage = {}
        if os.path.isdir(self.disk.root):
            for name in os.listdir(self.disk.root):
                core_path = os.path.join(self.disk.root, name)
                if os.path.isdir(core_path):
                    cores_usage[name] = sum(os.path.getsize(os.path.join(base, f))
                                            for base, _, files in os.walk(core_path) for f in files)
        return {"values": values, "defaults": dict(self.store.defaults), "shades": list(SHADE_NAMES),
                "usage": {"total_mb": round((self.disk.total if self.disk.total is not None else self.disk.usage()) / 1048576, 1),
                          "cores": {k: round(v / 1048576, 1) for k, v in cores_usage.items()}},
                "clients": clients, "version": RENDER_VERSION,
                "ram": {"adt_mb": round(self.caches["adt"].bytes / 1048576, 1),
                        "render_mb": round(self.caches["img"].bytes / 1048576, 1),
                        "tex_mb": round(self.caches["tex"].bytes / 1048576, 1),
                        "wmo_mb": round(self.caches["wmogroup"].bytes / 1048576, 1)}}

    def set_value(self, key, value):
        if key.lower() in ("cache_mb", "render_workers"):
            try:
                number = int(value)
            except (TypeError, ValueError):
                raise ValueError("that setting is a number")
            low, high = (16, 20000) if key.lower() == "cache_mb" else (1, 8)
            if not low <= number <= high:
                raise ValueError(f"must be between {low} and {high}")
            self.store.set(key.lower(), str(number))
            self.workers_changed()
            return
        if key.lower() in ("keep_cache", "fallback"):
            self.store.set(key.lower(), "1" if str(value) in ("1", "on", "true", "True", True) else "0")
            self.relearn()
            return
        if key.lower() == "shading":
            if value not in SHADING:
                raise ValueError("unknown shading preset")
            self.store.set("shading", value)
            return
        if key.lower() == "fallback_order":
            names = [p.strip() for p in str(value).replace(";", ",").split(",") if p.strip()]
            unknown = [n for n in names if n not in self.cfg.cores]
            if unknown:
                raise ValueError("unknown core: " + ", ".join(unknown))
            self.store.set("fallback_order", ",".join(names))
            self.relearn()
            return
        if key.lower().startswith("client."):
            name = key.split(".", 1)[1]
            if name not in self.cfg.cores:
                raise ValueError("unknown core")
            path = str(value).strip()
            if path == "-":
                self.store.set(f"client.{name}", "-")
            elif path:
                if not os.path.isdir(path):
                    raise ValueError("that folder does not exist")
                self.store.set(f"client.{name}", path)
            else:
                self.store.clear(f"client.{name}")
            self.forget_clients()
            self.app.reset_client(name)
            return
        raise ValueError(f"{key} is not a map setting")

    def clear_cache(self, core=None):
        freed = self.disk.clear(core)
        with self.lock:
            for cache in self.caches.values():
                cache.clear()
        return freed

    def check_client(self, core, path):
        fallback_path = ""
        if core in self.cfg.cores:
            fallback_path = self.store.client_dir(core) or self.cfg.cores[core].client
        path = (path or "").strip() or fallback_path
        result = {"path": path, "ok": False, "archives": 0, "locale": "", "sample": "", "sample_ok": False, "ms": 0}
        if not path or not os.path.isdir(path):
            result["error"] = "folder not found"
            return result
        started = time.time()
        archives = ClientArchives(path, self.cfg.cores[core].locale if core in self.cfg.cores else "enUS")
        result["archives"] = len(archives.paths)
        if not archives.paths:
            result["error"] = "no .mpq files in that folder (point at the client's Data folder)"
            return result
        locales = sorted({os.path.basename(os.path.dirname(item)) for item in archives.paths
                          if os.path.dirname(item) != path})
        result["locale"] = ", ".join(locales) or f"{archives.locale} (from livemap.toml)"
        for directory in ("Azeroth", "Kalimdor"):
            sample = f"World\\Maps\\{directory}\\{directory}_31_49.adt"
            data = archives.read(sample)
            if data:
                result["sample"] = sample
                result["sample_ok"] = True
                break
        if not result["sample_ok"]:
            result["sample"] = "World\\Maps\\Azeroth\\Azeroth_31_49.adt"
        result["ok"] = result["sample_ok"]
        result["ms"] = int((time.time() - started) * 1000)
        return result
