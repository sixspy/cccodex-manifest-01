import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import Config


def connect(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or Config.database()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # timeout 让并发写者在 IMMEDIATE 锁上排队，而不是立刻报 database is locked
    conn = sqlite3.connect(path, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_db(db_path: str | None = None):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS landing_batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  zone_code TEXT NOT NULL,
  zone_name TEXT NOT NULL,
  biz_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('收集中','已闭合','已作废')),
  closed_object_count INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(zone_code, biz_date)
);

CREATE TABLE IF NOT EXISTS landing_objects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id INTEGER NOT NULL,
  object_key TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  byte_size INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('已登记','已闭合','已作废')),
  rule_track_no INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(batch_id, object_key),
  FOREIGN KEY(batch_id) REFERENCES landing_batches(id)
);

-- 规则轨：旁路快照表，每行是一次整段物化，旧行永不改写；is_current=1 恒唯一，即“现行轨”
CREATE TABLE IF NOT EXISTS rule_tracks (
  track_no INTEGER PRIMARY KEY,
  track_code TEXT NOT NULL UNIQUE,
  min_byte_size INTEGER NOT NULL,
  min_hash_length INTEGER NOT NULL,
  required_key_prefix TEXT NOT NULL DEFAULT '',
  remark TEXT NOT NULL DEFAULT '',
  is_current INTEGER NOT NULL CHECK (is_current IN (0,1)),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rule_tracks_current
  ON rule_tracks(is_current) WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS close_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id INTEGER NOT NULL UNIQUE,
  object_count INTEGER NOT NULL,
  ok INTEGER NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(batch_id) REFERENCES landing_batches(id)
);

CREATE TABLE IF NOT EXISTS visibility_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  as_of_date TEXT NOT NULL,
  closed_batch_count INTEGER NOT NULL,
  open_batch_count INTEGER NOT NULL,
  object_count INTEGER NOT NULL,
  orphan_object_count INTEGER NOT NULL,
  mismatch_count INTEGER NOT NULL,
  summary TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

SEED_BATCHES = [
    ("bronze_orders", "订单铜区", "2026-11-14", "收集中"),
    ("bronze_payments", "支付铜区", "2026-11-14", "收集中"),
    ("bronze_events", "事件铜区", "2026-11-13", "收集中"),
    ("silver_orders", "订单银区", "2026-11-12", "已闭合"),
]


# 现行轨 1：旧门槛（宽松），收集中批此前即按此放行
SEED_RULE_TRACK = (1, "RULE-0001", 0, 1, "", "初始轨：沿用灌库前旧门槛", 1)


def init_db(db_path: str | None = None) -> None:
    with get_db(db_path) as conn:
        conn.executescript(SCHEMA)
        # 旧库平滑迁移：landing_objects 补规则轨号列
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(landing_objects)")}
        if "rule_track_no" not in cols:
            conn.execute("ALTER TABLE landing_objects ADD COLUMN rule_track_no INTEGER")
        row = conn.execute("SELECT value FROM meta WHERE key='seeded'").fetchone()
        if row:
            # 旧库此前没有规则轨：补物化初始现行轨，保证轨号可读取、门槛成立
            track = conn.execute("SELECT COUNT(*) AS c FROM rule_tracks").fetchone()
            if track["c"] == 0:
                conn.execute(
                    "INSERT INTO rule_tracks(track_no, track_code, min_byte_size, "
                    "min_hash_length, required_key_prefix, remark, is_current) "
                    "VALUES (?,?,?,?,?,?,?)",
                    SEED_RULE_TRACK,
                )
            return
        conn.execute(
            "INSERT INTO rule_tracks(track_no, track_code, min_byte_size, "
            "min_hash_length, required_key_prefix, remark, is_current) "
            "VALUES (?,?,?,?,?,?,?)",
            SEED_RULE_TRACK,
        )
        for zone, name, biz, status in SEED_BATCHES:
            conn.execute(
                "INSERT INTO landing_batches(zone_code, zone_name, biz_date, status, closed_object_count) "
                "VALUES (?,?,?,?,?)",
                (zone, name, biz, status, 2 if status == "已闭合" else None),
            )
        closed = conn.execute(
            "SELECT id FROM landing_batches WHERE status='已闭合'"
        ).fetchone()
        if closed:
            bid = closed["id"]
            conn.execute(
                "INSERT INTO landing_objects(batch_id, object_key, content_hash, byte_size, status) "
                "VALUES (?,?,?,?,?)",
                (bid, "part-000.parquet", "hash-silver-a", 1024, "已闭合"),
            )
            conn.execute(
                "INSERT INTO landing_objects(batch_id, object_key, content_hash, byte_size, status) "
                "VALUES (?,?,?,?,?)",
                (bid, "part-001.parquet", "hash-silver-b", 2048, "已闭合"),
            )
            conn.execute(
                "INSERT INTO close_runs(batch_id, object_count, ok, detail) VALUES (?,?,1,?)",
                (bid, 2, "种子闭合"),
            )
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('seeded','1')"
        )
