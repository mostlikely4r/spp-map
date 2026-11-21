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
    return {"verts": verts, "tris": tris, "mats": mats, "uvs": uvs, "bbox": (bbox_min, bbox_max)}


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
