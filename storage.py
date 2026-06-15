# -*- coding: utf-8 -*-
"""
storage.py
==========
SQLite 儲存層（v5 多工位獨立 DB 版）。

佈局：
  data/
  ├── gx20_<station>.db    # 各工位一份 samples 表
  ├── gx20_settings.db     # 6 工位共用的 settings 表
  └── archive/
      └── gx20_<station>_<YYYYMMDD_HHMMSS>.db   # 清除前歸檔

設計理由：
  - 6 工位非同步上下線 → 各自獨立 DB 互不污染
  - 清特定工位時先歸檔 → 救得回來
  - 設定與資料分離 → 清資料不會洗掉 GX20 連線、別名、顏色

向後相容：
  - 啟動時若偵測到舊的 data/gx20.db，自動 migrate：
      1) samples 按 station 切到 6 個新 DB
      2) settings 全部複製到 gx20_settings.db
      3) 舊檔刪除（先歸檔到 archive/gx20_pre_migration_<時間>.db）
"""

import glob
import os
import re
import shutil
import sqlite3
import logging
from contextlib import contextmanager
from typing import Dict, List, Optional, Any, Iterator
from datetime import datetime, timedelta

log = logging.getLogger("storage")

# 延遲載入：避免 storage.py 被 import 時 gx20_reader 還沒初始化
_STATIONS: Optional[List[str]] = None

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
ARCHIVE_DIR = os.path.join(DB_DIR, "archive")

# 每工位歸檔保留份數（超過自動刪最舊）
ARCHIVE_KEEP_PER_STATION = 5

# 舊版單一 DB 檔名（用於 migrate 偵測）
LEGACY_DB_NAME = "gx20.db"
LEGACY_SETTINGS_DB_NAME = "gx20_settings.db"  # migrate 完後 settings 用這個檔名

# === schema ===
SAMPLE_T_COLS = ", ".join(f"t{i:02d} REAL" for i in range(1, 21))
# v7：每工位樣本表新增 v/i/w 三欄（PW3335 電力）
#  對於舊 DB（v6.1 之前），init_db() 內會用 _migrate_add_power_columns() 補欄
SAMPLE_PW_COLS = "v REAL, i REAL, w REAL"
SCHEMA_SAMPLES = (
    "CREATE TABLE IF NOT EXISTS samples ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " ts TEXT NOT NULL,"
    " station TEXT NOT NULL,"
    f" {SAMPLE_T_COLS},"
    f" {SAMPLE_PW_COLS}"
    ")"
)

# ALTER TABLE 用的欄位定義（給舊 DB 補欄用）
# 與 SAMPLE_PW_COLS 同名、同型別，這裡列出來方便比對
SAMPLE_PW_COLUMN_NAMES = ("v", "i", "w")
SCHEMA_SETTINGS = (
    "CREATE TABLE IF NOT EXISTS settings ("
    " key TEXT PRIMARY KEY,"
    " value TEXT"
    ")"
)


def _stations() -> List[str]:
    """惰性載入 STATIONS 列表。"""
    global _STATIONS
    if _STATIONS is None:
        from gx20_reader import STATIONS  # 避免循環 import
        _STATIONS = list(STATIONS)
    return _STATIONS


# === 路徑 helper ===

def samples_db_path(station: str) -> str:
    return os.path.join(DB_DIR, f"gx20_{station}.db")


def settings_db_path() -> str:
    return os.path.join(DB_DIR, LEGACY_SETTINGS_DB_NAME)


def _ensure_dirs() -> None:
    os.makedirs(DB_DIR, exist_ok=True)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)


# === connection helper ===

@contextmanager
def _conn_samples(station: str) -> Iterator[sqlite3.Connection]:
    """特定工位的 samples DB 連線。"""
    path = samples_db_path(station)
    _ensure_dirs()
    c = sqlite3.connect(path, timeout=5, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    try:
        yield c
    finally:
        c.close()


@contextmanager
def _conn_settings() -> Iterator[sqlite3.Connection]:
    """共用 settings DB 連線。"""
    _ensure_dirs()
    c = sqlite3.connect(settings_db_path(), timeout=5, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    try:
        yield c
    finally:
        c.close()


# === init / migrate ===

def init_db(reset: bool = False) -> None:
    """
    啟動時呼叫。
    1) 若有舊 data/gx20.db → migrate
    2) 為每工位建立 samples DB（補 schema）
    3) 為 settings DB 補 schema
    """
    _ensure_dirs()
    _migrate_legacy_if_needed()

    if reset:
        log.warning("init_db: reset=True，刪除所有 data/gx20_*.db 與 settings db")
        for s in _stations():
            p = samples_db_path(s)
            if os.path.exists(p):
                os.remove(p)
        if os.path.exists(settings_db_path()):
            os.remove(settings_db_path())

    # 為每工位建 schema（CREATE IF NOT EXISTS，不刪資料）
    for s in _stations():
        with _conn_samples(s) as c:
            c.execute(SCHEMA_SAMPLES)
            c.execute("CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts)")
            # v7：補上 v/i/w 三欄（舊 DB 向後相容）
            _ensure_power_columns(c, s)
    # settings
    with _conn_settings() as c:
        c.execute(SCHEMA_SETTINGS)
    log.info("init_db: 6 工位 samples DB + settings DB 就緒 (dir=%s)", DB_DIR)


def _migrate_legacy_if_needed() -> None:
    """若偵測到舊 data/gx20.db，把 samples 按 station 切到新 DB，settings 移到新 settings DB。"""
    legacy = os.path.join(DB_DIR, LEGACY_DB_NAME)
    if not os.path.exists(legacy):
        return

    log.info("偵測到舊 DB %s，開始 migrate 到 6 獨立 DB 佈局", legacy)
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_path = os.path.join(ARCHIVE_DIR, f"gx20_pre_migration_{ts_str}.db")

    # 1) 先把舊檔整份歸檔（含 settings + samples）
    try:
        shutil.copy2(legacy, archive_path)
        log.info("migrate: 舊 DB 已歸檔到 %s", archive_path)
    except Exception as e:
        log.warning("migrate: 歸檔舊 DB 失敗: %s（繼續 migrate）", e)

    # 2) 連舊 DB 拉資料
    try:
        c = sqlite3.connect(legacy, timeout=5)
        c.row_factory = sqlite3.Row
        # 2a) samples 按 station 切到新 DB
        rows = c.execute("SELECT * FROM samples ORDER BY id ASC").fetchall()
        grouped: Dict[str, List[sqlite3.Row]] = {}
        for r in rows:
            grouped.setdefault(r["station"], []).append(r)
        for station, srows in grouped.items():
            # 若 station 不在 _stations() 內（理論不會），仍建檔
            with _conn_samples(station) as nc:
                nc.execute(SCHEMA_SAMPLES)
                nc.execute("CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts)")
                cols = "ts, station, " + ", ".join(f"t{i:02d}" for i in range(1, 21))
                placeholders = "?, ?, " + ", ".join("?" for _ in range(20))
                sql = f"INSERT INTO samples ({cols}) VALUES ({placeholders})"
                for r in srows:
                    vals = [r["ts"], r["station"]] + [r[f"t{i:02d}"] for i in range(1, 21)]
                    nc.execute(sql, vals)
            log.info("migrate: %s 寫入 %d 筆", station, len(srows))

        # 2b) settings 移到新 settings DB
        try:
            srows = c.execute("SELECT key, value FROM settings").fetchall()
            with _conn_settings() as nc:
                nc.execute(SCHEMA_SETTINGS)
                for r in srows:
                    nc.execute(
                        "INSERT INTO settings(key, value) VALUES(?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (r["key"], r["value"]),
                    )
            log.info("migrate: settings 寫入 %d 筆", len(srows))
        except sqlite3.OperationalError:
            # 舊 DB 沒有 settings 表（v1 之前），略過
            log.info("migrate: 舊 DB 無 settings 表，略過")

        c.close()
    except Exception as e:
        log.exception("migrate 過程失敗: %s", e)
        return

    # 3) 刪除舊檔（含 WAL/SHM）
    for ext in ("", "-wal", "-shm", "-journal"):
        p = legacy + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError as e:
                log.warning("migrate: 刪除 %s 失敗: %s", p, e)
    log.info("migrate: 完成")


# === samples CRUD ===

def insert_sample(
    ts: str,
    station: str,
    temps: List[Optional[float]],
    v: Optional[float] = None,
    i: Optional[float] = None,
    w: Optional[float] = None,
) -> None:
    """寫入一筆取樣（temps 必須長度 20；None 視為 NULL）。

    v7：新增 v / i / w 三個 optional 參數，給 PW3335 用。
    - 寫 0.0 時也存成 0（不是 NULL），方便後端 CSV 輸出有實值
    - 寫 None 時存 NULL
    - 舊呼叫端不傳 v/i/w → 預設 None → 存 NULL
    """
    assert len(temps) == 20, f"temps 長度必須為 20，收到 {len(temps)}"
    assert station in _stations(), f"未知工位: {station}"
    # 註：必須在 _conn_samples 之前補 schema。若 DB 檔剛被刪除（clear_station_db 後），
    # 不建表寫入會跳 "no such table: samples" → 整個 round 死掉。
    _ensure_samples_table(station)
    cols = (
        "ts, station, "
        + ", ".join(f"t{i:02d}" for i in range(1, 21))
        + ", v, i, w"
    )
    placeholders = "?, ?, " + ", ".join("?" for _ in range(20)) + ", ?, ?, ?"
    sql = f"INSERT INTO samples ({cols}) VALUES ({placeholders})"
    vals: List[Any] = [ts, station] + [t if t is not None else None for t in temps] + [v, i, w]
    with _conn_samples(station) as c:
        c.execute(sql, vals)


def query_recent(station: str, since_minutes: int = 60) -> List[Dict[str, Any]]:
    """拉取指定工位最近 N 分鐘的 samples。"""
    assert station in _stations(), f"未知工位: {station}"
    _ensure_samples_table(station)
    cutoff = (datetime.now() - timedelta(minutes=since_minutes)).isoformat(timespec="seconds")
    with _conn_samples(station) as c:
        rows = c.execute(
            "SELECT * FROM samples WHERE ts >= ? ORDER BY ts ASC",
            (cutoff,),
        ).fetchall()
    return [_row_to_dict(r, station) for r in rows]


def query_latest(station: str) -> Optional[Dict[str, Any]]:
    """取得指定工位最新一筆。"""
    assert station in _stations(), f"未知工位: {station}"
    _ensure_samples_table(station)
    with _conn_samples(station) as c:
        r = c.execute(
            "SELECT * FROM samples ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    return _row_to_dict(r, station) if r else None


# v6.1.3: 移除 query_count_in_range() 函式
# （「資料覆蓋」指標已從 UI 移除，對應的 SQLite COUNT 查詢不再需要）


def _row_to_dict(r: sqlite3.Row, station: str) -> Dict[str, Any]:
    d = {"ts": r["ts"], "station": station}
    for i in range(1, 21):
        d[f"t{i:02d}"] = r[f"t{i:02d}"]
    # v7：PW3335 欄位（舊 DB 補欄前會跳 KeyError；用 try 包起來向後相容）
    for col in SAMPLE_PW_COLUMN_NAMES:
        try:
            d[col] = r[col]
        except (IndexError, KeyError):
            d[col] = None
    return d


# === 清除 / 歸檔 ===

def _archive_path(station: str, ts_str: str) -> str:
    return os.path.join(ARCHIVE_DIR, f"gx20_{station}_{ts_str}.db")


def archive_station(station: str) -> Optional[str]:
    """
    把指定工位的 samples DB 歸檔到 archive/，並回傳歸檔檔路徑。
    若該工位 DB 不存在或無資料，回傳 None。
    """
    assert station in _stations(), f"未知工位: {station}"
    src = samples_db_path(station)
    if not os.path.exists(src):
        return None
    # 用檔案大小判斷：空 DB 也照歸檔（一致性）
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = _archive_path(station, ts_str)
    try:
        # 先關掉可能的連線（SQLite WAL 切乾淨）
        with _conn_samples(station) as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        shutil.copy2(src, dst)
        log.info("archive_station: %s 已歸檔到 %s", station, dst)
    except Exception as e:
        log.error("archive_station: %s 歸檔失敗: %s", station, e)
        return None

    # 輪替：超過 ARCHIVE_KEEP_PER_STATION 份，刪最舊
    _prune_old_archives(station)
    return dst


def _prune_old_archives(station: str) -> int:
    """保留最近 ARCHIVE_KEEP_PER_STATION 份，刪除其餘。回傳刪除數。"""
    pattern = os.path.join(ARCHIVE_DIR, f"gx20_{station}_*.db*")
    files = sorted(glob.glob(pattern))
    # 同檔可能被列出多次（含 -wal, -shm, -journal）
    # 依主檔名分組
    main_files = [f for f in files if not f.endswith(("-wal", "-shm", "-journal"))]
    if len(main_files) <= ARCHIVE_KEEP_PER_STATION:
        return 0
    to_delete = main_files[:-ARCHIVE_KEEP_PER_STATION]
    deleted = 0
    for f in to_delete:
        try:
            os.remove(f)
            # 連 WAL/SHM/JOURNAL 也一起刪
            for ext in ("-wal", "-shm", "-journal"):
                p = f + ext
                if os.path.exists(p):
                    os.remove(p)
            deleted += 1
        except OSError as e:
            log.warning("刪除歸檔 %s 失敗: %s", f, e)
    if deleted:
        log.info("歸檔輪替: 刪除 %s 的 %d 份舊歸檔", station, deleted)
    return deleted


def list_archives(station: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    列出歸檔。
      station=None → 列全部工位的歸檔
      station='工位5' → 只列該工位
    回傳：[{station, filename, path, size, mtime}, ...]（新到舊排序）
    """
    if not os.path.isdir(ARCHIVE_DIR):
        return []
    out: List[Dict[str, Any]] = []
    for s in ([station] if station else _stations()):
        pattern = os.path.join(ARCHIVE_DIR, f"gx20_{s}_*.db")
        for f in glob.glob(pattern):
            try:
                st = os.stat(f)
            except OSError:
                continue
            out.append({
                "station":   s,
                "filename":  os.path.basename(f),
                "path":      f,
                "size":      st.st_size,
                "mtime":     datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            })
    out.sort(key=lambda d: d["mtime"], reverse=True)
    return out


def clear_station_db(station: str) -> bool:
    """
    刪除指定工位的 samples DB。
    注意：歸檔由呼叫端（archive_station）決定，不在此函式處理。
    """
    assert station in _stations(), f"未知工位: {station}"
    path = samples_db_path(station)
    if not os.path.exists(path):
        return True
    try:
        # 先 WAL checkpoint
        with _conn_samples(station) as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        # 刪主檔 + WAL/SHM
        for ext in ("", "-wal", "-shm", "-journal"):
            p = path + ext
            if os.path.exists(p):
                os.remove(p)
        log.info("clear_station_db: 已刪除 %s 的 DB", station)
        return True
    except OSError as e:
        log.error("clear_station_db: 刪除 %s DB 失敗: %s", station, e)
        return False


def clear_all_samples() -> int:
    """
    刪除所有工位的 samples DB（不動 settings）。
    用於「確定要清空所有量測資料」場景。
    回傳成功刪除的工位數。
    """
    n = 0
    for s in _stations():
        if clear_station_db(s):
            n += 1
    return n


# === purge（保留天數） ===

def purge_old_samples(retention_days: int) -> int:
    """逐工位刪除超過保留天數的資料，回傳總刪除筆數。"""
    if retention_days <= 0:
        return 0
    cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat(timespec="seconds")
    total = 0
    for s in _stations():
        _ensure_samples_table(s)
        with _conn_samples(s) as c:
            cur = c.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
            deleted = cur.rowcount or 0
            total += deleted
            if deleted:
                log.info("purge_old_samples[%s]: 刪除 %d 筆（保留 %d 天）", s, deleted, retention_days)
    if total:
        log.info("purge_old_samples: 總計刪除 %d 筆", total)
    return total


# === settings ===

def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with _conn_settings() as c:
        r = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(key: str, value: str) -> None:
    with _conn_settings() as c:
        c.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_all_settings() -> Dict[str, str]:
    with _conn_settings() as c:
        rows = c.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


# === 統計 ===

def _ensure_samples_table(station: str) -> None:
    """確保該工位 DB 有 samples 表（DB 不存在時也順手建檔）。"""
    if station not in _stations():
        return
    with _conn_samples(station) as c:
        c.execute(SCHEMA_SAMPLES)
        c.execute("CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts)")
        # v7：補上 v/i/w 三欄（舊 DB 向後相容）
        _ensure_power_columns(c, station)


def _ensure_power_columns(c: sqlite3.Connection, station: str) -> None:
    """v7：確保 samples 表有 v / i / w 三欄。
    對 v7 之後新建的 DB：SCHEMA_SAMPLES 內已含這三欄，no-op。
    對舊 DB（v6.1 之前）：逐一 ALTER TABLE ADD COLUMN 補上。
    """
    try:
        rows = c.execute("PRAGMA table_info(samples)").fetchall()
    except sqlite3.OperationalError:
        # 表根本還沒建（理論上 SCHEMA_SAMPLES 會建，但保險起見）
        return
    existing = {row[1] for row in rows}  # row[1] = column name
    for col in SAMPLE_PW_COLUMN_NAMES:
        if col not in existing:
            try:
                c.execute(f"ALTER TABLE samples ADD COLUMN {col} REAL")
                log.info("storage: 為 %s samples 表補上 %s 欄", station, col)
            except sqlite3.OperationalError as e:
                # 萬一 race condition（兩個 process 同時 ALTER）只 warn
                log.debug("storage: %s ADD COLUMN %s 失敗（可能已被其他 process 加好）: %s",
                          station, col, e)


def count_samples() -> int:
    total = 0
    for s in _stations():
        _ensure_samples_table(s)
        with _conn_samples(s) as c:
            r = c.execute("SELECT COUNT(*) AS n FROM samples").fetchone()
            total += r["n"]
    return total


def count_samples_by_station() -> Dict[str, int]:
    out: Dict[str, int] = {}
    for s in _stations():
        _ensure_samples_table(s)
        with _conn_samples(s) as c:
            r = c.execute("SELECT COUNT(*) AS n FROM samples").fetchone()
            out[s] = r["n"]
    return out


def sample_time_range(station: str) -> Optional[Dict[str, str]]:
    """該工位最早/最晚一筆的 ts。"""
    if station not in _stations():
        return None
    _ensure_samples_table(station)
    with _conn_samples(station) as c:
        r = c.execute("SELECT MIN(ts) AS mn, MAX(ts) AS mx FROM samples").fetchone()
    if not r or r["mn"] is None:
        return None
    return {"first": r["mn"], "last": r["mx"]}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    insert_sample("2026-06-09T13:50:00", "工位1", [25.0 + i * 0.1 for i in range(20)])
    print("最新:", query_latest("工位1"))
    print("近 60 分鐘筆數:", len(query_recent("工位1", 60)))
    print("總筆數:", count_samples())
    print("by_station:", count_samples_by_station())
    print("歸檔清單:", list_archives())
    clear_station_db("工位1")
    print("清除後存在?", os.path.exists(samples_db_path("工位1")))
