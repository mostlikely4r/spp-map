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
