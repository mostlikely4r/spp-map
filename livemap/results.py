import re

RESULT_RE = re.compile(r"^\[BOTTEST\] (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| (.*?) \| (.*?) \| (\w+) \| (.*)$")
ELAPSED_RE = re.compile(r"after (\d+)\s*s\b")
QUEST_RE = re.compile(
    r"^quest_([ahn])_(\d+)_(\d+)_(.*)_(\d+)_(pickup_travel|pickup|handin|obj\d+_tick|obj\d+_clear|move_[a-z0-9_]+?|aimed|[a-z]+)_l(\d+)$"
)
SCENARIO_RE = re.compile(r"^scenario_(reach|boss|trash)_(.+?)(?:_(min|70|max))?$")
VARIANT_LEVEL_RE = re.compile(r"_l(\d+)$")
FAILING = ("FAIL", "ABORT", "IMPOSSIBLE")


def parse_line(line):
    m = RESULT_RE.match(line.strip())
    if not m:
        return None
    ts, host, test, verdict, msg = m.groups()
    elapsed = ELAPSED_RE.search(msg)
    row = {
        "ts": ts,
        "host": host.strip(),
        "test": test.strip(),
        "verdict": verdict.strip().upper(),
        "msg": msg.strip(),
        "elapsed_s": int(elapsed.group(1)) if elapsed else None,
    }
    row.update(parse_name(row["test"]))
    return row


def parse_name(name):
    out = {"family": family_of(name), "quest_id": None, "variant": None, "level": None, "subject": None}
    m = QUEST_RE.match(name)
    if m:
        faction, min_level, quest_level, title, quest_id, variant, level = m.groups()
        out.update(quest_id=int(quest_id), variant=variant, level=int(level), subject=title.replace("_", " "))
        return out
    m = SCENARIO_RE.match(name)
    if m:
        kind, rest, variant = m.groups()
        out.update(variant=kind + ("_" + variant if variant else ""), subject=rest)
        return out
    lv = VARIANT_LEVEL_RE.search(name)
    if lv:
        out["level"] = int(lv.group(1))
    return out


def family_of(name):
    head = name.split("_", 1)[0].lower()
    return head or "other"


def cluster_of(msg):
    text = re.sub(r"\d+(\.\d+)?", "#", msg)
    text = re.sub(r"\[[^\]]*\]", "[…]", text)
    return text[:160]
