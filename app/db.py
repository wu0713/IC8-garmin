"""SQLite 資料層。刻意保持簡單：一個檔案、標準函式庫 sqlite3。"""
import json
import sqlite3
import threading
from contextlib import contextmanager

from .config import DB_PATH

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    display_name TEXT,
    pw_hash TEXT NOT NULL,
    pw_salt TEXT NOT NULL,
    cal_token TEXT UNIQUE NOT NULL,
    provider TEXT NOT NULL DEFAULT 'none',      -- none | garmin | demo
    garmin_email TEXT,
    garmin_tokens BLOB,                         -- Fernet 加密後的 token JSON
    mfa_state BLOB,                             -- MFA 等待中的暫存狀態（加密）
    last_sync TEXT,
    last_sync_error TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prefs (
    user_id INTEGER PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activities (
    user_id INTEGER NOT NULL,
    activity_id TEXT NOT NULL,
    date TEXT NOT NULL,
    start_local TEXT,
    type TEXT,
    name TEXT,
    distance_m REAL,
    duration_s REAL,
    avg_hr REAL,
    max_hr REAL,
    training_load REAL,
    avg_speed REAL,
    PRIMARY KEY (user_id, activity_id)
);
CREATE INDEX IF NOT EXISTS idx_act_user_date ON activities(user_id, date);
CREATE TABLE IF NOT EXISTS daily (
    user_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    rhr REAL,
    hrv_avg REAL,
    hrv_weekly REAL,
    hrv_status TEXT,
    sleep_score REAL,
    sleep_hours REAL,
    body_battery REAL,
    stress_avg REAL,
    PRIMARY KEY (user_id, date)
);
CREATE TABLE IF NOT EXISTS checkins (
    user_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    feeling INTEGER,            -- 1(很糟) ~ 5(超好)
    skip INTEGER DEFAULT 0,     -- 今天不跑
    note TEXT,
    PRIMARY KEY (user_id, date)
);
CREATE TABLE IF NOT EXISTS records (
    user_id INTEGER NOT NULL,
    rtype TEXT NOT NULL,          -- 1k | 1mi | 5k | 10k | half | full | longest
    value REAL NOT NULL,          -- 秒（距離類）或公尺（longest）
    activity_id TEXT NOT NULL,
    date TEXT NOT NULL,
    PRIMARY KEY (user_id, rtype, activity_id)
);
CREATE TABLE IF NOT EXISTS character (
    user_id INTEGER PRIMARY KEY,
    species TEXT NOT NULL,
    nickname TEXT,
    chosen_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS device (
    user_id INTEGER PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activity_series (
    user_id INTEGER NOT NULL,
    activity_id TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (user_id, activity_id)
);
CREATE TABLE IF NOT EXISTS plan_overrides (
    user_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    kind TEXT NOT NULL,          -- 想換成的課；rest = 改休息
    minutes REAL,
    PRIMARY KEY (user_id, date)
);
CREATE TABLE IF NOT EXISTS imported_plan (
    user_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    data TEXT NOT NULL,           -- 匯入課表的那一天（已轉成課：分段、配速、心率上限）
    PRIMARY KEY (user_id, date)
);
CREATE TABLE IF NOT EXISTS plan_meta (
    user_id INTEGER PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS season_state (
    user_id INTEGER PRIMARY KEY,
    data TEXT NOT NULL            -- 開季快照：診斷、基線、可行性、插入的週、檢核點結果
);
CREATE TABLE IF NOT EXISTS plan (
    user_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    data TEXT NOT NULL,
    garmin_workout_id TEXT,
    garmin_pushed_hash TEXT,
    PRIMARY KEY (user_id, date)
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_conn = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = _connect()
        _conn.executescript(SCHEMA)
        _migrate(_conn)
        _conn.commit()
    return _conn


MIGRATIONS = {
    "daily": {"steps": "REAL", "steps_goal": "REAL", "active_kcal": "REAL", "intensity_min": "REAL"},
    "activities": {"calories": "REAL", "elevation_gain": "REAL", "avg_cadence": "REAL",
                   "hr_drift": "REAL", "temp_c": "REAL", "humidity": "REAL", "weather_checked": "INTEGER"},
    "checkins": {"pain": "TEXT"},
    "plan_overrides": {"km": "REAL"},
}


def _migrate(c: sqlite3.Connection):
    """舊資料庫自動補上新欄位，升級不用重建資料。"""
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        if not have:
            continue
        for col, typ in cols.items():
            if col not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def reset_for_tests(path):
    """測試用：切換到另一個 DB 檔。"""
    global _conn, DB_PATH
    from . import config
    config.DB_PATH = path
    DB_PATH = path
    _conn = None


@contextmanager
def tx():
    with _lock:
        c = conn()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise


def q(sql, args=()):
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def q1(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def get_prefs(user_id: int) -> dict:
    from .planner import default_prefs, migrate_prefs

    row = q1("SELECT data FROM prefs WHERE user_id=?", (user_id,))
    prefs = default_prefs()
    if row:
        stored = json.loads(row["data"])
        # 巢狀 dict 也合併，舊版設定升級時不會缺欄位
        for k, v in stored.items():
            if isinstance(v, dict) and isinstance(prefs.get(k), dict):
                prefs[k].update(v)
            else:
                prefs[k] = v
    return migrate_prefs(prefs)


def save_prefs(user_id: int, prefs: dict):
    with tx() as c:
        c.execute(
            "INSERT INTO prefs(user_id, data) VALUES(?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data",
            (user_id, json.dumps(prefs, ensure_ascii=False)),
        )
