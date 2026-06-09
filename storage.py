# -*- coding: utf-8 -*-
"""
storage.py
==========
SQLite 儲存層。

策略:
  - 資料持久：關閉程式不會自動刪 DB；超過「保留天數」才清除
  - 啟動時只補缺的 table / index；不刪舊資料
  - 由設定頁「立即清除 SQLite」按鈕或後端定期清理過期資料
  - schema 見模組 docstring
"""

import os
import sqlite3
import logging
from contextlib import contextmanager
from typing import Dict, List, Optional, Any, Iterator
from datetime import datetime, timedelta

log = logging.getLogger("storage")

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "gx20.db")

# === schema ===
SAMPLE_T_COLS = ", ".join(f"t{i:02d} REAL" for i in range(1, 21))
SCHEMA_SAMPLES = (
    "CREATE TABLE IF NOT EXISTS samples ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " ts TEXT NOT NULL,"
    " station TEXT NOT NULL,"
    f" {SAMPLE_T_COLS}"
    ")"
)


def _ensure_data_dir() -> None:
    os.makedirs(DB_DIR, exist_ok=True)


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    _ensure_data_dir()
    c = sqlite3.connect(DB_PATH, timeout=5, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    try:
        yield c
    finally:
        c.close()


def init_db(reset: bool = False) -> None:
    """建立資料表（若檔案已存在則保留資料）。
    reset=True 時會刪除舊 DB。
    過期資料清除由 purge_old_samples() 負責。"""
    _ensure_data_dir()
    if reset and os.path.exists(DB_PATH):
        log.info("init_db: reset=True，刪除舊的 %s", DB_PATH)
        os.remove(DB_PATH)
    with _conn() as c:
        c.execute(SCHEMA_SAMPLES)
        c.execute("CREATE INDEX IF NOT EXISTS idx_samples_station_ts ON samples(station, ts)")
        c.execute(
            "CREATE TABLE IF NOT EXISTS settings ("
            " key TEXT PRIMARY KEY,"
            " value TEXT"
            ")"
        )
    log.info("init_db: schema 就緒 (%s)", DB_PATH)


def purge_old_samples(retention_days: int) -> int:
    """刪除超過保留天數的資料。回傳刪除筆數。"""
    if retention_days <= 0:
        return 0
    cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat(timespec="seconds")
    with _conn() as c:
        cur = c.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
        deleted = cur.rowcount or 0
    if deleted:
        log.info("purge_old_samples: 刪除 %d 筆（保留 %d 天）", deleted, retention_days)
    return deleted


def clear_db() -> None:
    """刪除整個 SQLite 檔（對應「關閉網頁清除資料」）。"""
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
            log.info("clear_db: 已刪除 %s", DB_PATH)
        except OSError as e:
            log.error("clear_db 失敗: %s", e)


def insert_sample(ts: str, station: str, temps: List[Optional[float]]) -> None:
    """寫入一筆取樣（temps 必須長度 20；None 視為 NULL）。"""
    assert len(temps) == 20, f"temps 長度必須為 20，收到 {len(temps)}"
    cols = "ts, station, " + ", ".join(f"t{i:02d}" for i in range(1, 21))
    placeholders = "?, ?, " + ", ".join("?" for _ in range(20))
    sql = f"INSERT INTO samples ({cols}) VALUES ({placeholders})"
    vals: List[Any] = [ts, station] + [t if t is not None else None for t in temps]
    with _conn() as c:
        c.execute(sql, vals)


def query_recent(station: str, since_minutes: int = 60) -> List[Dict[str, Any]]:
    """拉取指定工位最近 N 分鐘的 samples。"""
    cutoff = (datetime.now() - timedelta(minutes=since_minutes)).isoformat(timespec="seconds")
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM samples WHERE station = ? AND ts >= ? ORDER BY ts ASC",
            (station, cutoff),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def query_latest(station: str) -> Optional[Dict[str, Any]]:
    """取得指定工位最新一筆。"""
    with _conn() as c:
        r = c.execute(
            "SELECT * FROM samples WHERE station = ? ORDER BY ts DESC LIMIT 1",
            (station,),
        ).fetchone()
    return _row_to_dict(r) if r else None


def _row_to_dict(r: sqlite3.Row) -> Dict[str, Any]:
    d = {"ts": r["ts"], "station": r["station"]}
    for i in range(1, 21):
        d[f"t{i:02d}"] = r[f"t{i:02d}"]
    return d


# === settings ===

def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with _conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(key: str, value: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_all_settings() -> Dict[str, str]:
    with _conn() as c:
        rows = c.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def count_samples() -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) AS n FROM samples").fetchone()["n"]


def count_samples_by_station() -> Dict[str, int]:
    with _conn() as c:
        rows = c.execute("SELECT station, COUNT(*) AS n FROM samples GROUP BY station").fetchall()
    return {r["station"]: r["n"] for r in rows}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db(reset=True)
    insert_sample("2026-06-09T13:50:00", "工位1", [25.0 + i * 0.1 for i in range(20)])
    print("最新:", query_latest("工位1"))
    print("近 60 分鐘筆數:", len(query_recent("工位1", 60)))
    print("總筆數:", count_samples())
    clear_db()
    print("清除後存在?", os.path.exists(DB_PATH))
