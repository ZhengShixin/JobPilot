"""The job pool database (data/jobpilot.db, SQLite): every posting seen, its filter and review results and its status.

Each row is a JSON document; the columns next to it are copies that exist only for querying. Writers use short
transactions (WAL mode), so the dashboard can read while a daily run writes.

Posting stages: new (waiting for review) -> dropped (coarse filter) | reviewed | failed (review kept failing).
Statuses: pending, dropped, excluded, below_threshold, ready, applied, skipped, hold, stale.
"""
import contextlib
import json
import sqlite3
import threading

from . import util

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings(
  id TEXT PRIMARY KEY,
  stage TEXT NOT NULL DEFAULT 'new',
  status TEXT NOT NULL DEFAULT 'pending',
  track TEXT,
  score REAL,
  company_key TEXT,
  posted_at TEXT,
  first_seen TEXT,
  updated_at TEXT,
  data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS postings_status ON postings(status, track, score);
CREATE INDEX IF NOT EXISTS postings_stage ON postings(stage);
CREATE INDEX IF NOT EXISTS postings_company ON postings(company_key);
CREATE INDEX IF NOT EXISTS postings_seen ON postings(first_seen);
CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""
COLUMNS = ("stage", "status", "track", "score", "company_key", "posted_at", "first_seen", "updated_at")
STATUSES = ("pending", "dropped", "excluded", "below_threshold", "ready", "applied", "skipped", "hold", "stale")
OPEN = ("ready", "hold")


class DB:
    def __init__(self, path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.executescript(SCHEMA)

    def close(self):
        with self._lock:
            self.conn.close()

    def __del__(self):
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001 - interpreter shutdown
            pass

    @contextlib.contextmanager
    def tx(self):
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")

    def _exec(self, sql, params=()):
        with self._lock:
            return self.conn.execute(sql, params)

    # ------------------------------------------------------------ postings
    @staticmethod
    def _row(r):
        return json.loads(r["data"]) if r else None

    def get(self, pid):
        return self._row(self._exec("SELECT data FROM postings WHERE id=?", (str(pid),)).fetchone())

    def put(self, row):
        row = dict(row)
        row["id"] = str(row["id"])
        row["updated_at"] = util.stamp()
        row.setdefault("stage", "new")
        row.setdefault("status", "pending")
        if "company_key" not in row:
            row["company_key"] = util.company_key(row.get("company"))
        values = [row.get(c) for c in COLUMNS]
        self._exec("INSERT OR REPLACE INTO postings(id,%s,data) VALUES(?,%s,?)"
                   % (",".join(COLUMNS), ",".join("?" * len(COLUMNS))),
                   [row["id"], *values, json.dumps(row, ensure_ascii=False, sort_keys=True)])
        return row

    def put_many(self, rows):
        with self.tx():
            return [self.put(r) for r in rows]

    def find(self, where="1=1", params=(), order="", limit=None):
        sql = "SELECT data FROM postings WHERE %s" % where
        if order:
            sql += " ORDER BY " + order
        if limit:
            sql += " LIMIT %d" % int(limit)
        return [self._row(r) for r in self._exec(sql, params).fetchall()]

    def count(self, where="1=1", params=()):
        return self._exec("SELECT COUNT(*) FROM postings WHERE %s" % where, params).fetchone()[0]

    def counts(self, column, where="1=1", params=()):
        if column not in COLUMNS:
            raise ValueError(column)
        rows = self._exec("SELECT %s, COUNT(*) FROM postings WHERE %s GROUP BY %s" % (column, where, column), params)
        return {r[0]: r[1] for r in rows.fetchall()}

    def exists(self, pid):
        return self._exec("SELECT 1 FROM postings WHERE id=?", (str(pid),)).fetchone() is not None

    # ------------------------------------------------------------ reviews (full AI answers, for audit)
    def review(self, pid):
        r = self._exec("SELECT data FROM reviews WHERE id=?", (str(pid),)).fetchone()
        return json.loads(r["data"]) if r else None

    def set_review(self, pid, data):
        self._exec("INSERT OR REPLACE INTO reviews(id,data,created_at) VALUES(?,?,?)",
                   (str(pid), json.dumps(data, ensure_ascii=False, sort_keys=True), util.stamp()))

    # ------------------------------------------------------------ meta
    def meta(self, key, default=None):
        r = self._exec("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def set_meta(self, key, value):
        self._exec("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, json.dumps(value, ensure_ascii=False)))
