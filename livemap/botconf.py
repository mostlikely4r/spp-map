import re

EDITABLE = {
    "AiPlayerbot.LogArchiveOnBoot": ("0", "Keep old logs at boot (rename instead of wipe)"),
    "AiPlayerbot.LogArchiveKeepBoots": ("10", "How many old boots to keep"),
    "AiPlayerbot.LiveMapSampleSec": ("5", "Seconds between live position samples"),
}


def read_values(path):
    out = {k: {"value": None, "default": d, "label": label} for k, (d, label) in EDITABLE.items()}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = re.match(r"^\s*(AiPlayerbot\.\w+)\s*=\s*(.*?)\s*$", line)
                if m and m.group(1) in out:
                    out[m.group(1)]["value"] = m.group(2)
    except OSError:
        return None
    return out


LOG_KEY = re.compile(rb"^\s*AiPlayerbot\.AllowedLogFiles\s*=\s*(.*?)\s*$")


def add_log_files(path, files):
    with open(path, "rb") as fh:
        raw = fh.read()
    nl = b"\r\n" if b"\r\n" in raw else b"\n"
    lines = raw.split(nl)
    for i, line in enumerate(lines):
        m = LOG_KEY.match(line)
        if m:
            have = [f.strip() for f in m.group(1).decode("utf-8", "replace").split(",") if f.strip()]
            added = [f for f in files if f not in have]
            if not added:
                return []
            lines[i] = ("AiPlayerbot.AllowedLogFiles = " + ",".join(have + added)).encode()
            break
    else:
        added = list(files)
        anchor = next((i for i, l in enumerate(lines) if l.strip() and not l.lstrip().startswith(b"#")), len(lines))
        lines.insert(anchor, ("AiPlayerbot.AllowedLogFiles = " + ",".join(added)).encode())
    with open(path + ".bak", "wb") as fh:
        fh.write(raw)
    with open(path, "wb") as fh:
        fh.write(nl.join(lines))
    return added


def write_value(path, key, value):
    if key not in EDITABLE:
        raise ValueError(f"{key} is not editable here")
    if not re.fullmatch(r"\d{1,6}", str(value)):
        raise ValueError("value must be a number")
    with open(path, "rb") as fh:
        raw = fh.read()
    nl = b"\r\n" if b"\r\n" in raw else b"\n"
    lines = raw.split(nl)
    pattern = re.compile(rb"^\s*" + re.escape(key.encode()) + rb"\s*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key} = {value}".encode()
            break
    else:
        anchor = next((i for i, l in enumerate(lines) if l.startswith(b"AiPlayerbot.AllowedLogFiles")), len(lines) - 1)
        lines.insert(anchor + 1, f"{key} = {value}".encode())
    with open(path, "wb") as fh:
        fh.write(nl.join(lines))
