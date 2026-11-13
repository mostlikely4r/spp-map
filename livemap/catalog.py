import glob
import os
import sqlite3
import threading
import time

from . import results

SCHEMA = """
create table if not exists runs (
    id integer primary key,
    core text not null,
    ts text not null,
    host text not null,
    test text not null,
    verdict text not null,
    msg text not null,
    elapsed_s integer,
    family text,
    quest_id integer,
    variant text,
    level integer,
    subject text,
    cluster text,
    source text,
    unique (core, ts, host, test, verdict, msg)
);
create index if not exists runs_test on runs (core, test, ts);
create index if not exists runs_ts on runs (ts);
create index if not exists runs_quest on runs (core, quest_id);
create table if not exists files (
    path text primary key,
    size integer,
    mtime real,
    offset integer
);
create table if not exists triage (
    core text not null,
    test text not null,
    bucket text,
    note text,
    updated text,
    primary key (core, test)
);
create table if not exists favorites (
    core text not null,
    bot text not null,
    primary key (core, bot)
);
"""

BUCKETS = ("bug", "fluke", "gap", "fixed")


class Catalog:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def query(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def ingest_core(self, core):
        added = 0
        for path in sorted(glob.glob(os.path.join(core.logs, "bot_test_results*.log"))):
            added += self.ingest_file(core.name, path)
        return added

    def ingest_file(self, core_name, path):
        try:
            st = os.stat(path)
        except OSError:
            return 0
        with self.lock:
            known = self.db.execute("select size, mtime, offset from files where path=?", (path,)).fetchone()
        if known and known["size"] == st.st_size and known["mtime"] == st.st_mtime:
            return 0
        start = known["offset"] if known and st.st_size >= known["offset"] else 0
        rows = []
        with open(path, "rb") as fh:
            fh.seek(start)
            data = fh.read()
        end = data.rfind(b"\n") + 1
        for raw in data[:end].split(b"\n"):
            row = results.parse_line(raw.decode("utf-8", errors="replace"))
            if row:
                row["cluster"] = results.cluster_of(row["msg"])
                rows.append(row)
        with self.lock:
            before = self.db.total_changes
            self.db.executemany(
                "insert or ignore into runs (core, ts, host, test, verdict, msg, elapsed_s, family, quest_id, variant, level, subject, cluster, source)"
                " values (:core, :ts, :host, :test, :verdict, :msg, :elapsed_s, :family, :quest_id, :variant, :level, :subject, :cluster, :source)",
                [dict(r, core=core_name, source=os.path.basename(path)) for r in rows],
            )
            self.db.execute(
                "insert into files (path, size, mtime, offset) values (?, ?, ?, ?)"
                " on conflict(path) do update set size=excluded.size, mtime=excluded.mtime, offset=excluded.offset",
                (path, st.st_size, st.st_mtime, start + end),
            )
            self.db.commit()
            return self.db.total_changes - before - 1

    def summary(self):
        cores = self.query(
            "select core, verdict, count(*) n from runs group by core, verdict order by core"
        )
        tests = self.query(
            "select core, count(distinct test) n, max(ts) last from runs group by core"
        )
        return {"verdicts": cores, "tests": tests}

    def tests(self, core=None, family=None, verdict=None, q=None, bucket=None, flipped=False, limit=100, offset=0):
        where, args = [], []
        if core:
            where.append("l.core=?"); args.append(core)
        if family:
            where.append("l.family=?"); args.append(family)
        if verdict:
            where.append("l.verdict=?"); args.append(verdict.upper())
        if q:
            for word in q.lower().split():
                where.append("lower(l.test) like ?"); args.append(f"%{word}%")
        if bucket:
            where.append("t.bucket=?"); args.append(bucket)
        if flipped:
            where.append("l.verdict='PASS' and l.prev in ('FAIL','ABORT','IMPOSSIBLE')")
        sql = f"""
            with ordered as (
                select r.*, lag(verdict) over (partition by core, test order by ts, id) prev,
                       row_number() over (partition by core, test order by ts desc, id desc) rn,
                       count(*) over (partition by core, test) runs,
                       sum(verdict='PASS') over (partition by core, test) passes
                from runs r
            ), latest as (select * from ordered where rn=1)
            select l.id, l.core, l.test, l.verdict, l.prev, l.ts, l.host, l.msg, l.family, l.quest_id, l.variant,
                   l.level, l.subject, l.runs, l.passes, t.bucket, t.note
            from latest l left join triage t on t.core=l.core and t.test=l.test
            {"where " + " and ".join(where) if where else ""}
            order by l.ts desc limit ? offset ?
        """
        return self.query(sql, args + [limit, offset])

    def history(self, core, test):
        return self.query(
            "select id, ts, host, verdict, msg, elapsed_s, source from runs where core=? and test=? order by ts desc, id desc",
            (core, test),
        )

    def run(self, run_id):
        return self.one("select * from runs where id=?", (run_id,))

    def transitions(self, core=None, kind="flip", limit=50):
        cond = "verdict='PASS' and prev in ('FAIL','ABORT','IMPOSSIBLE')" if kind == "flip" else "verdict='FAIL' and prev='PASS'"
        args = []
        core_cond = ""
        if core:
            core_cond = "where core=?"; args.append(core)
        return self.query(
            f"""select * from (
                    select id, core, test, ts, host, verdict, msg,
                           lag(verdict) over (partition by core, test order by ts, id) prev
                    from runs {core_cond})
                where {cond} order by ts desc limit ?""",
            args + [limit],
        )

    def clusters(self, core=None, limit=40):
        args = []
        core_cond = ""
        if core:
            core_cond = "and core=?"; args.append(core)
        return self.query(
            f"""select core, family, cluster, count(*) n, count(distinct test) tests, max(ts) last, max(id) example
                from runs where verdict in ('FAIL','ABORT','IMPOSSIBLE') {core_cond}
                group by core, family, cluster order by n desc limit ?""",
            args + [limit],
        )

    def families(self):
        return self.query("select core, family, count(distinct test) tests, count(*) runs from runs group by core, family order by runs desc")

    def set_triage(self, core, test, bucket, note):
        if bucket and bucket not in BUCKETS:
            raise ValueError(f"bucket must be one of {BUCKETS}")
        with self.lock:
            self.db.execute(
                "insert into triage (core, test, bucket, note, updated) values (?, ?, ?, ?, ?)"
                " on conflict(core, test) do update set bucket=excluded.bucket, note=excluded.note, updated=excluded.updated",
                (core, test, bucket or None, note or None, time.strftime("%Y-%m-%d %H:%M:%S")),
            )
            self.db.commit()

    def triage(self, core, test):
        return self.one("select bucket, note, updated from triage where core=? and test=?", (core, test))

    def favorites(self):
        return self.query("select core, bot from favorites order by core, bot")

    def set_favorite(self, core, bot, on):
        with self.lock:
            if on:
                self.db.execute("insert or ignore into favorites (core, bot) values (?, ?)", (core, bot))
            else:
                self.db.execute("delete from favorites where core=? and bot=?", (core, bot))
            self.db.commit()
