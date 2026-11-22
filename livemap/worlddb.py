import threading

SPAWN_LIMIT = 40

VANILLA_NPC_FLAGS = ((0x8, "flightmaster"), (0x80, "innkeeper"), (0x1000, "auctioneer"), (0x100, "banker"),
                     (0x800, "battlemaster"), (0x20, "spirithealer"), (0x10, "trainer"), (0x4, "vendor"),
                     (0x4000, "vendor"), (0x2, "questgiver"))
LATER_NPC_FLAGS = ((0x2000, "flightmaster"), (0x10000, "innkeeper"), (0x200000, "auctioneer"), (0x20000, "banker"),
                   (0x100000, "battlemaster"), (0x4000, "spirithealer"), (0x10, "trainer"), (0x80, "vendor"),
                   (0x1000, "vendor"), (0x2, "questgiver"))
GO_TYPES = {0: "door", 1: "button", 2: "questgiver", 3: "chest", 5: "generic", 6: "trap", 7: "chair", 8: "spellfocus",
            9: "text", 10: "goober", 11: "transport", 13: "camera", 15: "transport", 17: "fishing", 18: "ritual",
            19: "mailbox", 22: "spellcaster", 23: "meetingstone", 24: "flagstand", 25: "fishinghole",
            26: "flagdrop", 29: "capturepoint"}


CMANGOS = {
    "ct_entry": "Entry", "ct_name": "Name", "ct_min": "MinLevel", "ct_max": "MaxLevel", "ct_rank": "Rank",
    "ct_flags": "NpcFlags", "ct_faction": "Faction", "ct_loot": "LootId",
}
VMANGOS = {
    "ct_entry": "entry", "ct_name": "name", "ct_min": "level_min", "ct_max": "level_max", "ct_rank": "rank",
    "ct_flags": "npc_flags", "ct_faction": "faction", "ct_loot": "loot_id",
}


class WorldDb:
    def __init__(self, core):
        self.core = core
        self.info = core.world_db
        self.local = threading.local()
        self.cache = {}
        self.vm = core.flavor == "vmangos"
        self.s = VMANGOS if self.vm else CMANGOS
        self.patch = 10
        if self.vm:
            from .config import read_conf
            value = read_conf(core.mangosd_conf).get("WowPatch", "10")
            self.patch = int(value) if value.isdigit() else 10

    def spawn_filter(self, alias):
        return f" and {alias}.patch_min <= {self.patch} and {alias}.patch_max >= {self.patch}" if self.vm else ""

    def tpl_filter(self, table, alias):
        if not self.vm:
            return ""
        return (f" and {alias}.patch = (select max(px.patch) from {table} px"
                f" where px.entry = {alias}.entry and px.patch <= {self.patch})")

    def available(self):
        return self.info is not None

    def conn(self):
        c = getattr(self.local, "conn", None)
        if c is None or not c.open:
            import pymysql
            i = self.info
            c = pymysql.connect(host=i.host, port=i.port, user=i.user, password=i.password,
                                database=i.database, connect_timeout=5, autocommit=True)
            self.local.conn = c
        return c

    def rows(self, sql, args=()):
        try:
            with self.conn().cursor() as cur:
                cur.execute(sql, args)
                return cur.fetchall()
        except Exception:
            self.local.conn = None
            raise

    def creature_spawns(self, entry, limit=SPAWN_LIMIT):
        return [
            {"map": m, "x": float(x), "y": float(y), "z": float(z)}
            for m, x, y, z in self.rows(
                "select map, position_x, position_y, position_z from creature c where id=%s" + self.spawn_filter("c")
                + " limit %s", (entry, limit))
        ]

    def gameobject_spawns(self, entry, limit=SPAWN_LIMIT):
        return [
            {"map": m, "x": float(x), "y": float(y), "z": float(z)}
            for m, x, y, z in self.rows(
                "select map, position_x, position_y, position_z from gameobject c where id=%s" + self.spawn_filter("c")
                + " limit %s", (entry, limit))
        ]

    def creature_name(self, entry):
        r = self.rows("select t.name from creature_template t where t.entry=%s" + self.tpl_filter("creature_template", "t"),
                      (entry,))
        return r[0][0] if r else f"creature {entry}"

    def gameobject_name(self, entry):
        r = self.rows("select t.name from gameobject_template t where t.entry=%s" + self.tpl_filter("gameobject_template", "t"),
                      (entry,))
        return r[0][0] if r else f"object {entry}"

    def item_name(self, entry):
        r = self.rows("select t.name from item_template t where t.entry=%s" + self.tpl_filter("item_template", "t"), (entry,))
        return r[0][0] if r else f"item {entry}"

    def actors(self, table, quest_id, kind):
        out = []
        for (entry,) in self.rows(f"select distinct id from {table} c where quest=%s" + self.spawn_filter("c"), (quest_id,)):
            if kind == "creature":
                out.append({"entry": entry, "name": self.creature_name(entry), "spawns": self.creature_spawns(entry)})
            else:
                out.append({"entry": entry, "name": self.gameobject_name(entry), "spawns": self.gameobject_spawns(entry)})
        return out

    def item_droppers(self, item, limit=6):
        rows = self.rows(
            f"select distinct ct.entry from creature_loot_template l join creature_template ct on ct.{self.s['ct_loot']}=l.entry"
            " where l.item=%s limit %s", (item, limit))
        return [r[0] for r in rows]

    def quest(self, quest_id):
        key = ("quest", quest_id)
        if key in self.cache:
            return self.cache[key]
        r = self.rows(
            "select entry, Title, MinLevel, QuestLevel, ReqCreatureOrGOId1, ReqCreatureOrGOId2, ReqCreatureOrGOId3,"
            " ReqCreatureOrGOId4, ReqItemId1, ReqItemId2, ReqItemId3, ReqItemId4 from quest_template t where entry=%s"
            + self.tpl_filter("quest_template", "t"), (quest_id,))
        if not r:
            return None
        entry, title, min_level, quest_level, *req = r[0]
        objectives = []
        for i, target in enumerate(req[:4]):
            if not target:
                continue
            if target > 0:
                objectives.append({"index": i, "kind": "creature", "entry": target, "name": self.creature_name(target),
                                   "spawns": self.creature_spawns(target)})
            else:
                objectives.append({"index": i, "kind": "object", "entry": -target, "name": self.gameobject_name(-target),
                                   "spawns": self.gameobject_spawns(-target)})
        for i, item in enumerate(req[4:]):
            if not item:
                continue
            spawns = []
            for dropper in self.item_droppers(item):
                spawns.extend(self.creature_spawns(dropper, 15))
            objectives.append({"index": 4 + i, "kind": "item", "entry": item, "name": self.item_name(item),
                               "spawns": spawns[:SPAWN_LIMIT]})
        out = {
            "id": entry, "title": title, "min_level": min_level, "level": quest_level,
            "givers": self.actors("creature_questrelation", quest_id, "creature")
            + self.actors("gameobject_questrelation", quest_id, "object"),
            "takers": self.actors("creature_involvedrelation", quest_id, "creature")
            + self.actors("gameobject_involvedrelation", quest_id, "object"),
            "objectives": objectives,
        }
        self.cache[key] = out
        return out

    def npc_flags(self):
        classic = self.core.expansion == "classic" or (not self.core.expansion and self.core.name == "vanilla")
        return VANILLA_NPC_FLAGS if classic else LATER_NPC_FLAGS

    def npc_role(self, flags, rank):
        for bit, role in self.npc_flags():
            if flags & bit:
                return role
        if rank in (1, 2, 4):
            return "elite"
        if rank == 3:
            return "boss"
        return "mob"

    def reactions(self):
        if "reactions" not in self.cache:
            out = {}
            try:
                from .clientdata import read_dbc
                import os
                recs, _ = read_dbc(os.path.join(self.core.dbc, "FactionTemplate.dbc"))
                for r in recs:
                    ours, friend, hostile = r[3], r[4], r[5]
                    a_friend = bool((friend | ours) & 2) and not hostile & 2
                    h_friend = bool((friend | ours) & 4) and not hostile & 4
                    if a_friend and h_friend:
                        out[r[0]] = "both"
                    elif a_friend:
                        out[r[0]] = "alliance"
                    elif h_friend:
                        out[r[0]] = "horde"
                    elif hostile & 7:
                        out[r[0]] = "hostile"
                    else:
                        out[r[0]] = "neutral"
            except (OSError, ValueError):
                pass
            self.cache["reactions"] = out
        return self.cache["reactions"]

    def spawn_layer(self, kind, map_id, bounds, limit=2500, bins=48):
        if kind == "creatures":
            table, alias, tmpl, key = "creature", "c", "creature_template", "entry"
            s = self.s
            cols = (f"c.guid, c.id, c.position_x, c.position_y, t.{s['ct_name']}, t.{s['ct_min']}, t.{s['ct_max']},"
                    f" t.{s['ct_rank']}, t.{s['ct_flags']}, t.{s['ct_faction']}")
        else:
            table, alias, tmpl, key = "gameobject", "c", "gameobject_template", "entry"
            cols = "c.guid, c.id, c.position_x, c.position_y, t.name, t.type"
        where = (f"from {table} c join {tmpl} t on t.{key}=c.id{self.tpl_filter(tmpl, 't')} where c.map=%s"
                 " and c.position_x between %s and %s and c.position_y between %s and %s" + self.spawn_filter("c"))
        args = (map_id, bounds["bottom"], bounds["top"], bounds["right"], bounds["left"])
        total = self.rows(f"select count(*) {where}", args)[0][0]
        if total > limit:
            cell = max(bounds["top"] - bounds["bottom"], bounds["left"] - bounds["right"]) / bins
            rows = self.rows(
                f"select floor(c.position_x/%s), floor(c.position_y/%s), count(*) {where} group by 1, 2",
                (cell, cell) + args)
            return {"binned": True, "total": total, "cell": cell,
                    "bins": [[(float(i) + 0.5) * cell, (float(j) + 0.5) * cell, int(n)] for i, j, n in rows]}
        feats = []
        reactions = self.reactions() if kind == "creatures" else {}
        for r in self.rows(f"select {cols} {where}", args):
            if kind == "creatures":
                guid, entry, x, y, name, lo, hi, rank, flags, faction = r
                level = f"{lo}" if lo == hi else f"{lo}-{hi}"
                feats.append([guid, entry, round(float(x), 1), round(float(y), 1), name, level,
                              self.npc_role(int(flags or 0), int(rank or 0)), reactions.get(int(faction or 0), "neutral"),
                              int(hi or 0)])
            else:
                guid, entry, x, y, name, gtype = r
                feats.append([guid, entry, round(float(x), 1), round(float(y), 1), name,
                              GO_TYPES.get(int(gtype), "other")])
        return {"binned": False, "total": total, "features": feats}

    def spawn_points(self):
        out = {}
        for m, x, y in self.rows("select map, position_x, position_y from creature c where 1=1" + self.spawn_filter("c")):
            out.setdefault(int(m), []).append((float(x), float(y)))
        return out

    def occupancy(self, cell):
        key = ("occupancy", cell)
        if key not in self.cache:
            out = {}
            for table in ("creature", "gameobject"):
                for m, i, j in self.rows(f"select map, floor(position_x/%s), floor(position_y/%s) from {table} c"
                                         f" where 1=1{self.spawn_filter('c')} group by 1, 2, 3",
                                         (cell, cell)):
                    out.setdefault(int(m), set()).add((int(i), int(j)))
            self.cache[key] = out
        return self.cache[key]

    def map_extent(self, map_id):
        key = ("extent", map_id)
        if key not in self.cache:
            r = self.rows("select min(position_x), max(position_x), min(position_y), max(position_y), count(*)"
                          " from creature c where map=%s" + self.spawn_filter("c"), (map_id,))[0]
            if not r[4]:
                r = self.rows("select min(x), max(x), min(y), max(y), count(*) from ai_playerbot_travelnode"
                              " where map_id=%s", (map_id,))[0]
            self.cache[key] = None if not r[4] else {
                "bottom": float(r[0]) - 60, "top": float(r[1]) + 60, "right": float(r[2]) - 60, "left": float(r[3]) + 60}
        return self.cache[key]

    def travel_nodes(self, map_id, bounds, limit=4000, paths=True):
        args = (map_id, bounds["bottom"], bounds["top"], bounds["right"], bounds["left"])
        nodes = {
            nid: {"id": nid, "name": name, "x": float(x), "y": float(y), "z": float(z)}
            for nid, name, x, y, z in self.rows(
                "select id, name, x, y, z from ai_playerbot_travelnode where map_id=%s"
                " and x between %s and %s and y between %s and %s limit %s", args + (limit,))
        }
        if not nodes:
            return {"nodes": [], "links": []}
        ids = list(nodes)
        marks = ",".join(["%s"] * len(ids))
        rows = self.rows(
            f"select node_id, to_node_id, type, max_creature_0, max_creature_1, max_creature_2"
            f" from ai_playerbot_travelnode_link where node_id in ({marks})", ids)
        links = [(a, b, t) for a, b, t, *_ in rows]
        danger = {(a, b): [int(m0), int(m1), int(m2)] for a, b, t, m0, m1, m2 in rows}
        far = {}
        missing = sorted({b for a, b, t in links if b not in nodes})
        if missing:
            fmarks = ",".join(["%s"] * len(missing))
            far = {nid: {"x": float(x), "y": float(y)} for nid, x, y in self.rows(
                f"select id, x, y from ai_playerbot_travelnode where map_id=%s and id in ({fmarks})", [map_id] + missing)}
        ends = {**far, **nodes}
        walk = {(a, b) for a, b, t in links if t == 1 and a in nodes and b in ends}
        walk |= {(b, a) for a, b in walk}
        walked = {}
        if walk and paths:
            for a, b, x, y in self.rows(
                    f"select node_id, to_node_id, x, y from ai_playerbot_travelnode_path where map_id=%s"
                    f" and (node_id in ({marks}) or to_node_id in ({marks})) order by node_id, to_node_id, nr",
                    [map_id] + ids + ids):
                if (a, b) in walk:
                    walked.setdefault((a, b), []).append([float(x), float(y)])
        out_links = []
        seen = set()
        for a, b, t in links:
            if b not in ends:
                continue
            pair = (min(a, b), max(a, b), t)
            if pair in seen:
                continue
            seen.add(pair)
            pts = walked.get((a, b)) or walked.get((b, a))
            if not pts:
                pts = [[nodes[a]["x"], nodes[a]["y"]], [ends[b]["x"], ends[b]["y"]]]
            out_links.append({"type": t, "points": pts, "danger": danger.get((a, b)) or danger.get((b, a)) or [0, 0, 0]})
        return {"nodes": list(nodes.values()), "links": out_links}
