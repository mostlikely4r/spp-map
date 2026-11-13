import threading

SPAWN_LIMIT = 40


class WorldDb:
    def __init__(self, core):
        self.core = core
        self.info = core.world_db
        self.local = threading.local()
        self.cache = {}

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
                "select map, position_x, position_y, position_z from creature where id=%s limit %s", (entry, limit))
        ]

    def gameobject_spawns(self, entry, limit=SPAWN_LIMIT):
        return [
            {"map": m, "x": float(x), "y": float(y), "z": float(z)}
            for m, x, y, z in self.rows(
                "select map, position_x, position_y, position_z from gameobject where id=%s limit %s", (entry, limit))
        ]

    def creature_name(self, entry):
        r = self.rows("select Name from creature_template where Entry=%s", (entry,))
        return r[0][0] if r else f"creature {entry}"

    def gameobject_name(self, entry):
        r = self.rows("select name from gameobject_template where entry=%s", (entry,))
        return r[0][0] if r else f"object {entry}"

    def item_name(self, entry):
        r = self.rows("select name from item_template where entry=%s", (entry,))
        return r[0][0] if r else f"item {entry}"

    def actors(self, table, quest_id, kind):
        out = []
        for (entry,) in self.rows(f"select id from {table} where quest=%s", (quest_id,)):
            if kind == "creature":
                out.append({"entry": entry, "name": self.creature_name(entry), "spawns": self.creature_spawns(entry)})
            else:
                out.append({"entry": entry, "name": self.gameobject_name(entry), "spawns": self.gameobject_spawns(entry)})
        return out

    def item_droppers(self, item, limit=6):
        rows = self.rows(
            "select distinct ct.Entry from creature_loot_template l join creature_template ct on ct.LootId=l.entry"
            " where l.item=%s limit %s", (item, limit))
        return [r[0] for r in rows]

    def quest(self, quest_id):
        key = ("quest", quest_id)
        if key in self.cache:
            return self.cache[key]
        r = self.rows(
            "select entry, Title, MinLevel, QuestLevel, ReqCreatureOrGOId1, ReqCreatureOrGOId2, ReqCreatureOrGOId3,"
            " ReqCreatureOrGOId4, ReqItemId1, ReqItemId2, ReqItemId3, ReqItemId4 from quest_template where entry=%s",
            (quest_id,))
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

    def travel_nodes(self, map_id, bounds, limit=4000):
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
        links = self.rows(
            f"select node_id, to_node_id, type from ai_playerbot_travelnode_link where node_id in ({marks})", ids)
        walk = {(a, b) for a, b, t in links if t == 1 and a in nodes and b in nodes}
        paths = {}
        if walk:
            for a, b, x, y in self.rows(
                    f"select node_id, to_node_id, x, y from ai_playerbot_travelnode_path where map_id=%s"
                    f" and node_id in ({marks}) order by node_id, to_node_id, nr", [map_id] + ids):
                if (a, b) in walk:
                    paths.setdefault((a, b), []).append([float(x), float(y)])
        out_links = []
        seen = set()
        for a, b, t in links:
            if b not in nodes and t == 1:
                continue
            pair = (min(a, b), max(a, b), t)
            if pair in seen:
                continue
            seen.add(pair)
            pts = paths.get((a, b)) or paths.get((b, a))
            if not pts:
                if b not in nodes:
                    continue
                pts = [[nodes[a]["x"], nodes[a]["y"]], [nodes[b]["x"], nodes[b]["y"]]]
            out_links.append({"type": t, "points": pts})
        return {"nodes": list(nodes.values()), "links": out_links}
