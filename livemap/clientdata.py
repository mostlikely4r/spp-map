import io
import os
import re
import struct
import threading

SKIP_ARCHIVES = re.compile(r"(speech|sound|backup|base-)", re.I)


def read_dbc(path):
    with open(path, "rb") as fh:
        data = fh.read()
    magic, count, fields, recsize, strsize = struct.unpack_from("<4sIIII", data, 0)
    if magic != b"WDBC":
        raise ValueError(f"{path}: not a WDBC file")
    strings = data[20 + count * recsize: 20 + count * recsize + strsize]
    return [struct.unpack_from("<" + "I" * fields, data, 20 + i * recsize) for i in range(count)], strings


def dbc_string(strings, offset):
    if offset <= 0 or offset >= len(strings):
        return ""
    end = strings.find(b"\0", offset)
    return strings[offset:end].decode("utf-8", errors="replace")


def as_float(value):
    return struct.unpack("<f", struct.pack("<I", value))[0]


def archive_order(data_dir, locale):
    found = []
    for folder in (os.path.join(data_dir, locale), data_dir):
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            if name.lower().endswith(".mpq") and not SKIP_ARCHIVES.search(name):
                found.append(os.path.join(folder, name))

    def rank(path):
        name = os.path.basename(path).lower()
        is_locale = os.path.dirname(path).lower().endswith(locale.lower())
        m = re.match(r"patch(?:-[a-z]{4})?(?:-(\w+))?\.mpq$", name)
        if m:
            suffix = m.group(1)
            level = int(suffix) if suffix and suffix.isdigit() else (1 if not suffix else 0)
            return (0, -level, 0 if is_locale else 1, name)
        return (1, 0, 0 if is_locale else 1, name)

    return sorted(found, key=rank)


FILE_EXISTS = 0x80000000
FILE_PACKED = 0x00000100 | 0x00000200 | 0x00010000


def read_entry(arc, name):
    entry = arc.get_hash_table_entry(name)
    if entry is None:
        return None
    block = arc.block_table[entry.block_table_index]
    if block.flags & FILE_EXISTS and not block.flags & FILE_PACKED and block.archived_size == block.size:
        arc.file.seek(block.offset + arc.header["offset"])
        return arc.file.read(block.size)
    return arc.read_file(name)


class ClientArchives:
    def __init__(self, data_dir, locale="enUS", skip=None):
        self.data_dir = data_dir
        self.locale = locale
        self.paths = archive_order(data_dir, locale) if data_dir and os.path.isdir(data_dir) else []
        if skip is not None:
            self.paths = [p for p in self.paths if not skip.search(os.path.basename(p))]
        self.opened = {}
        self.lock = threading.Lock()
        self.open_lock = threading.Lock()

    def available(self):
        return bool(self.paths)

    def archive(self, path):
        if path in self.opened:
            return self.opened[path]
        with self.open_lock:
            if path not in self.opened:
                try:
                    import mpyq
                    self.opened[path] = mpyq.MPQArchive(path, listfile=False)
                except Exception:
                    self.opened[path] = None
            return self.opened[path]

    def preload(self, threads=4):
        from concurrent.futures import ThreadPoolExecutor
        pending = [p for p in self.paths if p not in self.opened]
        if not pending:
            return
        with ThreadPoolExecutor(max_workers=max(1, min(threads, len(pending)))) as pool:
            list(pool.map(self.archive, pending))

    def read(self, name):
        with self.lock:
            for path in self.paths:
                arc = self.archive(path)
                if arc is None:
                    continue
                try:
                    data = read_entry(arc, name)
                except Exception:
                    data = None
                if data:
                    return data
        return None

    def image(self, name):
        data = self.read(name)
        if not data:
            return None
        from PIL import Image
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
            return img
        except Exception:
            return None
