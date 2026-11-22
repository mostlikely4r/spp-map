import struct

import numpy as np

GRID = 17066.66666667


def chunks(data, start=0, end=None):
    end = len(data) if end is None else end
    i = start
    out = {}
    while i + 8 <= end:
        tag = data[i:i + 4][::-1].decode("latin-1")
        size = struct.unpack_from("<I", data, i + 4)[0]
        out.setdefault(tag, []).append((i + 8, size))
        i += 8 + size
    return out


def cstring(blob, offset):
    if offset < 0 or offset >= len(blob):
        return ""
    end = blob.find(b"\0", offset)
    return blob[offset:end if end >= 0 else len(blob)].decode("latin-1")


def adt_placements(data):
    parts = chunks(data)
    if "MODF" not in parts or "MWMO" not in parts:
        return []
    names_off, names_size = parts["MWMO"][0]
    names = data[names_off:names_off + names_size]
    offsets = []
    if "MWID" in parts:
        wid_off, wid_size = parts["MWID"][0]
        offsets = list(struct.unpack_from(f"<{wid_size // 4}I", data, wid_off))
    modf_off, modf_size = parts["MODF"][0]
    out = []
    for i in range(modf_size // 64):
        base = modf_off + i * 64
        name_id, uid, px, py, pz, ra, rb, rc = struct.unpack_from("<II3f3f", data, base)
        scale = struct.unpack_from("<H", data, base + 62)[0]
        if name_id < len(offsets):
            name = cstring(names, offsets[name_id])
        else:
            name = cstring(names, 0) if names else ""
        if not name:
            continue
        out.append((uid, name, (px, py, pz), (ra, rb, rc), (scale / 1024.0) if scale else 1.0))
    return out


def parse_root(data):
    parts = chunks(data)
    if "MOHD" not in parts:
        return None
    head, _ = parts["MOHD"][0]
    n_textures, n_groups = struct.unpack_from("<2I", data, head)
    bbox_min = struct.unpack_from("<3f", data, head + 36)
    bbox_max = struct.unpack_from("<3f", data, head + 48)
    strings = b""
    if "MOTX" in parts:
        off, size = parts["MOTX"][0]
        strings = data[off:off + size]
    textures = []
    if "MOMT" in parts:
        off, size = parts["MOMT"][0]
        for i in range(min(size // 64, 256)):
            tex_offset = struct.unpack_from("<I", data, off + i * 64 + 12)[0]
            textures.append(cstring(strings, tex_offset))
    groups = []
    if "MOGI" in parts:
        off, size = parts["MOGI"][0]
        for i in range(size // 32):
            flags, x0, y0, z0, x1, y1, z1 = struct.unpack_from("<I6f", data, off + i * 32)
            groups.append(((x0, y0, z0), (x1, y1, z1)))
    if not groups:
        groups = [(bbox_min, bbox_max)] * max(1, n_groups)
    return {"n_groups": n_groups, "bbox": (bbox_min, bbox_max), "textures": textures, "groups": groups}


def parse_group(data):
    parts = chunks(data)
    if "MOGP" not in parts:
        return None
    off, size = parts["MOGP"][0]
    if size < 68:
        return None
    flags = struct.unpack_from("<I", data, off + 8)[0]
    bbox_min = struct.unpack_from("<3f", data, off + 12)
    bbox_max = struct.unpack_from("<3f", data, off + 24)
    inner = chunks(data, off + 68, off + size)
    if "MOVT" not in inner or "MOVI" not in inner:
        return None
    verts_off, verts_size = inner["MOVT"][0]
    idx_off, idx_size = inner["MOVI"][0]
    if verts_size < 12 or idx_size < 6:
        return None
    verts = np.frombuffer(data, "<f4", verts_size // 4, verts_off).reshape(-1, 3).astype(np.float32)
    indices = np.frombuffer(data, "<H", idx_size // 2, idx_off).astype(np.int32)
    count = len(indices) // 3
    if not count:
        return None
    tris = indices[:count * 3].reshape(-1, 3)
    tris = tris[np.all(tris < len(verts), axis=1)]
    if not len(tris):
        return None
    count = len(tris)
    mats = np.zeros(count, np.int32)
    if "MOPY" in inner:
        mopy_off, mopy_size = inner["MOPY"][0]
        stride = mopy_size // max(1, len(indices) // 3)
        if stride in (2, 4):
            raw = np.frombuffer(data, np.uint8, min(mopy_size, count * stride), mopy_off)
            if raw.shape[0] >= count * stride:
                mats = raw[1::stride][:count].astype(np.int32)
    uvs = None
    if "MOTV" in inner:
        uv_off, uv_size = inner["MOTV"][0]
        if uv_size // 8 == len(verts):
            uvs = np.frombuffer(data, "<f4", uv_size // 4, uv_off).reshape(-1, 2).astype(np.float32)
    return {"verts": verts, "tris": tris, "mats": mats, "uvs": uvs, "bbox": (bbox_min, bbox_max), "flags": flags}


def transform(verts, position, rotation, scale):
    yaw = np.radians(rotation[1])
    c, s = np.cos(yaw), np.sin(yaw)
    x = verts[:, 0] * scale
    y = verts[:, 1] * scale
    z = verts[:, 2] * scale
    storage_x = position[0] + x * s + y * c
    storage_z = position[2] + x * c - y * s
    return GRID - storage_z, GRID - storage_x, position[1] + z


def box_in_view(bbox_min, bbox_max, position, rotation, scale, left, top, yard_px, size):
    corners = np.array([[bbox_min[0], bbox_min[1], bbox_min[2]], [bbox_max[0], bbox_min[1], bbox_min[2]],
                        [bbox_min[0], bbox_max[1], bbox_min[2]], [bbox_max[0], bbox_max[1], bbox_min[2]],
                        [bbox_min[0], bbox_min[1], bbox_max[2]], [bbox_max[0], bbox_min[1], bbox_max[2]],
                        [bbox_min[0], bbox_max[1], bbox_max[2]], [bbox_max[0], bbox_max[1], bbox_max[2]]], np.float32)
    wx, wy, _ = transform(corners, position, rotation, scale)
    px = (left - wy) / yard_px
    py = (top - wx) / yard_px
    return not (px.max() < -8 or py.max() < -8 or px.min() > size + 8 or py.min() > size + 8)


def adt_doodads(data):
    parts = chunks(data)
    if "MDDF" not in parts or "MMDX" not in parts:
        return []
    names_off, names_size = parts["MMDX"][0]
    names = data[names_off:names_off + names_size]
    offsets = []
    if "MMID" in parts:
        mid_off, mid_size = parts["MMID"][0]
        offsets = list(struct.unpack_from(f"<{mid_size // 4}I", data, mid_off))
    ddf_off, ddf_size = parts["MDDF"][0]
    out = []
    for i in range(ddf_size // 36):
        name_id, uid, px, py, pz, ra, rb, rc, scale, _ = struct.unpack_from("<II3f3fHH", data, ddf_off + i * 36)
        if name_id >= len(offsets):
            continue
        name = cstring(names, offsets[name_id])
        if name:
            out.append((uid, name, (px, py, pz), (ra, rb, rc), (scale / 1024.0) if scale else 1.0))
    return out


def m2_array(data, offset):
    return struct.unpack_from("<2I", data, offset)


def parse_m2(data, skin=None):
    if not data or len(data) < 200 or data[:4] != b"MD20":
        return None
    version = struct.unpack_from("<I", data, 4)[0]
    legacy = version < 264
    count, start = m2_array(data, 68 if legacy else 60)
    if not count or start + count * 48 > len(data):
        return None
    raw = np.frombuffer(data, np.uint8, count * 48, start).reshape(count, 48)
    verts = np.ascontiguousarray(raw[:, 0:12]).view("<f4").reshape(count, 3).astype(np.float32)
    uvs = np.ascontiguousarray(raw[:, 32:40]).view("<f4").reshape(count, 2).astype(np.float32)
    n_tex, tex_ofs = m2_array(data, 92 if legacy else 80)
    textures = []
    for i in range(min(n_tex, 64)):
        kind, _, length, name_ofs = struct.unpack_from("<4I", data, tex_ofs + i * 16)
        textures.append(data[name_ofs:name_ofs + max(0, length - 1)].decode("latin-1") if kind == 0 and length > 1 else "")
    n_look, look_ofs = m2_array(data, 148 if legacy else 128)
    lookup_tex = list(struct.unpack_from(f"<{n_look}H", data, look_ofs)) if n_look else []
    if legacy:
        n_views, view_ofs = m2_array(data, 76)
        if not n_views:
            return None
        view, base, sub_size = data, view_ofs, 32
    else:
        if not skin or skin[:4] != b"SKIN":
            return None
        view, base, sub_size = skin, 4, 48
    n_vl, vl_ofs = m2_array(view, base)
    n_ix, ix_ofs = m2_array(view, base + 8)
    n_sub, sub_ofs = m2_array(view, base + 24)
    n_bat, bat_ofs = m2_array(view, base + 32)
    if not n_vl or not n_ix:
        return None
    local = np.frombuffer(view, "<u2", n_vl, vl_ofs).astype(np.int32)
    index = np.frombuffer(view, "<u2", n_ix, ix_ofs).astype(np.int32)
    tris, tex = [], []
    for b in range(n_bat):
        at = bat_ofs + b * 24
        section, = struct.unpack_from("<H", view, at + 4)
        layer, = struct.unpack_from("<H", view, at + 12)
        combo, = struct.unpack_from("<H", view, at + 16)
        if layer != 0 or section >= n_sub:
            continue
        level, _, _, first, total = struct.unpack_from("<5H", view, sub_ofs + section * sub_size + 2)
        first += level << 16
        part = index[first:first + total]
        part = part[:len(part) // 3 * 3]
        if not len(part):
            continue
        part = local[np.clip(part, 0, n_vl - 1)].reshape(-1, 3)
        part = part[np.all(part < count, axis=1)]
        tris.append(part)
        slot = lookup_tex[combo] if combo < len(lookup_tex) else -1
        tex.append(np.full(len(part), slot if 0 <= slot < len(textures) else -1, np.int32))
    if not tris:
        return None
    return {"verts": verts, "uvs": uvs, "tris": np.concatenate(tris), "tex": np.concatenate(tex), "textures": textures}
