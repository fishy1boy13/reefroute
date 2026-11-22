"""Integer microcredit accounting; reserve before dispatch, never after."""
import datetime
import json
import sqlite3
import uuid


def day():
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS ledger (
                id TEXT PRIMARY KEY, day TEXT NOT NULL, caller TEXT NOT NULL,
                provider TEXT NOT NULL, model TEXT NOT NULL, cost INTEGER NOT NULL,
                status TEXT NOT NULL, input_tokens INTEGER, output_tokens INTEGER);
            CREATE TABLE IF NOT EXISTS alerts (
                condition TEXT, day TEXT, PRIMARY KEY(condition, day));
            CREATE TABLE IF NOT EXISTS catalogs (
                provider TEXT PRIMARY KEY, payload TEXT NOT NULL);
        """)

    def reserve(self, caller, cap, cost, provider, model):
        if type(cap) is not int or type(cost) is not int or min(cap, cost) < 0:
            raise ValueError("cap and cost must be nonnegative integer microcredits")
        today = day()
        try:
            self.db.execute("BEGIN IMMEDIATE")
            used = self.db.execute(
                "SELECT COALESCE(SUM(cost),0) FROM ledger WHERE caller=? AND day=?",
                (caller, today)).fetchone()[0]
            if used + cost > cap:
                self.db.rollback()
                return None
            rid = uuid.uuid4().hex
            self.db.execute("INSERT INTO ledger VALUES (?,?,?,?,?,?,?,NULL,NULL)",
                            (rid, today, caller, provider, model, cost, "reserved"))
            self.db.commit()
            return rid
        except BaseException:
            self.db.rollback()
            raise

    def finish(self, rid, status, usage=None):
        usage = usage or {}
        def count(key):
            value = usage.get(key)
            return value if type(value) is int and value >= 0 else None
        with self.db:
            self.db.execute("UPDATE ledger SET status=?, input_tokens=?, output_tokens=? WHERE id=?",
                            (status, count("prompt_tokens"), count("completion_tokens"), rid))

    def report(self):
        return [dict(row) for row in self.db.execute(
            "SELECT day, caller, provider, model, COUNT(*) AS requests, SUM(cost) AS microcredits "
            "FROM ledger GROUP BY day, caller, provider, model ORDER BY day, caller, provider, model")]

    def ledger(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM ledger ORDER BY rowid")]

    def claim_alert(self, condition):
        with self.db:
            return bool(self.db.execute("INSERT OR IGNORE INTO alerts VALUES (?,?)", (condition, day())).rowcount)

    def catalog(self, provider, models):
        previous = self.db.execute("SELECT payload FROM catalogs WHERE provider=?", (provider,)).fetchone()
        old = set(json.loads(previous[0])) if previous else set()
        current = set(models)
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO catalogs VALUES (?,?)", (provider, json.dumps(sorted(current))))
        return {"added": sorted(current - old), "removed": sorted(old - current)}

    def close(self):
        self.db.close()
