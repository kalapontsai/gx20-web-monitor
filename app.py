# -*- coding: utf-8 -*-
"""
app.py
======
Flask + Flask-SocketIO 主程式。

啟動流程:
  1. storage.init_db(reset=False) - 保留既有資料，只補 schema
  2. 寫入預設設定（若 settings 表為空）
  3. 啟動背景 poller thread
  4. 啟動 Flask-SocketIO

資料生命週期（v2）：
  - DB 改為持久：atexit 與 signal 不再 clear_db
  - 啟動時呼叫 purge_old_samples(retention_days) 清掉超過保留天數的舊資料
  - poller 每輪 (約 5 分鐘) 也跑一次 purge，避免無限膨脹
  - 「立即清除 SQLite」按鈕（POST /api/clear）仍可手動一鍵清空

poller 設計：
  - 維護每工位 ring buffer（最近 RING_BUFFER_SIZE 筆，預設 720=2hr）
  - rate / avg 改用 ring buffer 計算，不再 query_recent 全表掃
  - emit `new_sample`：只推 1 筆最新 + 算好的 rate/avg
  - 偶爾 emit `history_full`（首次連線時由前端主動觸發 GET /api/history）

v3 變更:
  - 加上 debug logger：log 寫到 logs/app.log（RotatingFileHandler，2MB×5）
  - debug 模式可從設定頁 / API 動態切換，會即時生效
  - 新增 chart_x_minutes 設定：0=全部資料，>0 只看最近 N 分鐘
  - poller 推播仍含 rate/avg 供右側表格；圖表本身只畫溫度線

路由:
  GET  /                       監看主頁
  GET  /settings               設定頁
  GET  /api/settings           讀取全部設定
  POST /api/settings           寫入設定
  GET  /api/history/<station>  拉取該站歷史，支援 ?max_points=N 自動 LTTB 降取樣
  GET  /api/latest/<station>   該站最新一筆
  GET  /api/connection         連線狀態
  POST /api/clear              手動清除 SQLite
  GET  /api/db_stats           資料庫統計（用於監看）
  GET  /api/debug              讀取 debug 狀態
  POST /api/debug              切換 debug 狀態
  GET  /api/debug/log_tail     查 app.log 末段
"""

import atexit
import csv
import io
import json
import logging
import logging.handlers
import os
import signal
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Deque

from flask import Flask, jsonify, render_template, request, send_from_directory, Response
from flask_socketio import SocketIO

import config
import storage
from gx20_reader import GX20, STATIONS, POINTS_PER_STATION, CHANNEL_NUMBER
from lttb import downsample_rows

# === Debug logger（v3 新增）===
# 將所有重要事件寫到 logs/app.log：
#   - 啟動 / 關閉
#   - poller 每一輪輪詢結果（成功/失敗、樣本數）
#   - GX20 連線狀態變化
#   - HTTP 請求（method, path, status, ms）
#   - SocketIO 事件（join/leave/new_sample 數量）
#   - storage 動作（insert / purge / clear）
#   - 例外（自動 traceback）
#
# 等級透過 settings 表的 `debug_log_enabled` 動態切換：
#   enabled=True  →  DEBUG（含每輪輪詢細節、HTTP body 等）
#   enabled=False →  INFO（預設，只記重要事件）
# 等級改變後會立即生效（FileHandler 與 Logger 的 level 都會跟著改）

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "app.log")

# === 設定同步檔（v4 新增）===
# 使用者按「保存」時，除了寫 SQLite 外，順手把整包設定 dump 進
# config/settings.json。下次重啟時若檔案存在，優先採用檔案內容並寫回 SQLite
# （以免 DB 預設值被誤重設）。
SETTINGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")
SETTINGS_JSON_PATH = os.path.join(SETTINGS_DIR, "settings.json")

# 共用格式：時間 等級 logger名 訊息
_LOG_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _build_file_handler() -> logging.Handler:
    """輪詢式 file handler，單檔 2MB、保留 5 個備份。"""
    fh = logging.handlers.RotatingFileHandler(
        LOG_PATH,
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(logging.Formatter(_LOG_FMT, datefmt=_LOG_DATEFMT))
    return fh


def _build_stream_handler() -> logging.Handler:
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter(_LOG_FMT, datefmt=_LOG_DATEFMT))
    return sh


def _setup_logging(debug_enabled: bool) -> None:
    """套用 log 設定。會先把舊的 handler 清掉再裝新的，
    避免重複（測試 / reload 場景）。"""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(_build_file_handler())
    root.addHandler(_build_stream_handler())
    root.setLevel(logging.DEBUG if debug_enabled else logging.INFO)

    # Werkzeug 預設 INFO；debug 模式時降到 DEBUG
    logging.getLogger("werkzeug").setLevel(
        logging.DEBUG if debug_enabled else logging.INFO
    )
    # SocketIO/EngineIO 太吵，壓到 WARNING
    for noisy in ("socketio", "engineio", "geventwebsocket", "gevent"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _is_debug_enabled() -> bool:
    """從 settings 表讀 debug_log_enabled。"""
    try:
        v = storage.get_setting("debug_log_enabled", "0")
        return str(v) in ("1", "true", "True", "yes")
    except Exception:
        return False


def apply_log_level_from_settings() -> None:
    """從 settings 重新讀 debug flag 並重設 log 等級。"""
    _setup_logging(_is_debug_enabled())


# 啟動時先用預設（INFO）；main() 內會再依 settings 切換
_setup_logging(debug_enabled=False)

log = logging.getLogger("app")
log.debug("logger 模組載入完成（尚未決定等級）")

# === Flask / SocketIO 初始化 ===
app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["SECRET_KEY"] = "gx20-web-monitor-secret"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")


# ---------- HTTP 請求 log ----------
# 紀錄每一個 HTTP 請求的方法、路徑、狀態、耗時
# 用 g._t0 傳遞開始時間

@app.before_request
def _http_before():
    request.environ["_gx20_t0"] = time.time()


@app.after_request
def _http_after(resp):
    try:
        t0 = request.environ.get("_gx20_t0")
        if t0 is not None:
            ms = (time.time() - t0) * 1000
        else:
            ms = -1
        # 靜態資源 / favicon 隨手只記 INFO 層級
        if request.path.startswith("/static/") or request.path == "/favicon.ico":
            log.debug("HTTP %s %s → %d (%.1fms)", request.method, request.path, resp.status_code, ms)
        else:
            log.info("HTTP %s %s → %d (%.1fms)", request.method, request.path, resp.status_code, ms)
    except Exception:
        pass
    return resp


# ---------- SocketIO 事件 log ----------

@socketio.on("connect")
def _on_connect():
    log.info("SocketIO connect sid=%s from %s", request.sid, request.remote_addr)


@socketio.on("disconnect")
def _on_disconnect():
    log.info("SocketIO disconnect sid=%s", request.sid)

# === 狀態 ===
state = {
    "gx20":           None,
    "poller_running": False,
    "last_error":     None,
    "last_ts":        None,
    "connected":      False,
    "last_purge_at":  0.0,        # 最後一次 purge 時間戳
    "lock":           threading.Lock(),
    # ring buffer: 每工位 → deque of (ts, temps[20])
    "ring":           {s: deque(maxlen=config.RING_BUFFER_SIZE) for s in STATIONS},
}


# ---------- 設定讀寫輔助 ----------

def load_settings() -> dict:
    """合併 SQLite 設定與預設值（缺項用預設補）。"""
    raw = storage.get_all_settings()
    defaults = config.default_settings()
    out = dict(defaults)

    for k in ("gx20_host", "gx20_port", "y_axis_min", "y_axis_max",
              "rate_window_min", "avg_window_min",
              "retention_days", "max_points", "chart_x_minutes"):
        if k in raw:
            try:
                out[k] = int(raw[k]) if k in (
                    "gx20_port", "rate_window_min", "avg_window_min",
                    "history_minutes", "retention_days", "max_points",
                    "chart_x_minutes"
                ) else (
                    float(raw[k]) if k in ("y_axis_min", "y_axis_max") else raw[k]
                )
            except (TypeError, ValueError):
                pass

    if "theme" in raw and raw["theme"] in ("light", "dark"):
        out["theme"] = raw["theme"]

    for k in ("ch_visibility", "ch_alias", "ch_color"):
        v = config.from_json(raw.get(k), default=defaults[k])
        if isinstance(v, dict):
            merged = dict(defaults[k])
            for st in STATIONS:
                if st in v:
                    merged[st] = v[st]
            out[k] = merged

    return out


def save_settings(patch: dict) -> None:
    for k, v in patch.items():
        if k in ("ch_visibility", "ch_alias", "ch_color") and isinstance(v, dict):
            existing_raw = storage.get_setting(k)
            existing = config.from_json(existing_raw, default=config.default_settings()[k])
            if not isinstance(existing, dict):
                existing = config.default_settings()[k]
            for st, val in v.items():
                if st in STATIONS:
                    existing[st] = val
            storage.set_setting(k, config.to_json(existing))
        else:
            storage.set_setting(k, str(v))

    # v4：同步 dump 一份 JSON 到 config/settings.json
    # 寫檔採 atomic 策略：先寫 .tmp，再 os.replace
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        merged = load_settings()
        tmp_path = SETTINGS_JSON_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp_path, SETTINGS_JSON_PATH)
        log.info("save_settings: 已同步寫入 %s", SETTINGS_JSON_PATH)
    except Exception as e:
        log.warning("save_settings: 寫入 %s 失敗: %s", SETTINGS_JSON_PATH, e)


def load_settings_from_json() -> Optional[dict]:
    """
    讀取 config/settings.json。
    讀到且格式正確 → 回傳 dict；不存在或讀檔失敗 → 回傳 None。
    """
    if not os.path.exists(SETTINGS_JSON_PATH):
        return None
    try:
        with open(SETTINGS_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
        if not isinstance(data, dict):
            log.warning("%s 格式不正確（預期 dict）", SETTINGS_JSON_PATH)
            return None
        return data
    except Exception as e:
        log.warning("讀取 %s 失敗: %s", SETTINGS_JSON_PATH, e)
        return None


def apply_json_to_sqlite(d: dict) -> None:
    """
    把 JSON 內的設定寫進 SQLite（寫進後 load_settings() 仍以 SQLite 為主）
    - 以 JSON 內容為準
    - 動底欄位 (ch_visibility/alias/color) 以 JSON 內的 dict 完整覆寫
    - 其他欄位以 str() 儲存
    """
    defaults = config.default_settings()
    for k, v in d.items():
        if k in ("ch_visibility", "ch_alias", "ch_color"):
            # 只接受 dict；錯誤值走預設
            if isinstance(v, dict):
                storage.set_setting(k, config.to_json(v))
            else:
                storage.set_setting(k, config.to_json(defaults.get(k, {})))
        else:
            storage.set_setting(k, str(v))


# ---------- ring buffer 計算（不再 query_recent） ----------

def _ring_window_since(rb: Deque, since_minutes: int):
    """從 ring buffer 取 since_minutes 分鐘內的資料。"""
    if not rb:
        return []
    cutoff = datetime.now() - timedelta(minutes=since_minutes)
    return [item for item in rb if datetime.fromisoformat(item[0]) >= cutoff]


def compute_rate_from_ring(station: str, since_minutes: int, point_index: int) -> Optional[float]:
    rb = state["ring"].get(station)
    if not rb:
        return None
    win = _ring_window_since(rb, since_minutes)
    vals = []
    for ts_str, temps in win:
        v = temps[point_index]
        if v is not None:
            vals.append((ts_str, v))
    if len(vals) < 2:
        return None
    t0, v0 = vals[0]
    t1, v1 = vals[-1]
    try:
        dt0 = datetime.fromisoformat(t0)
        dt1 = datetime.fromisoformat(t1)
        minutes = (dt1 - dt0).total_seconds() / 60.0
        if minutes <= 0:
            return None
        return round((v1 - v0) / minutes, 3)
    except Exception:
        return None


def compute_avg_from_ring(station: str, since_minutes: int, point_index: int) -> Optional[float]:
    rb = state["ring"].get(station)
    if not rb:
        return None
    win = _ring_window_since(rb, since_minutes)
    vals = [temps[point_index] for _, temps in win if temps[point_index] is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 2)


# ---------- Debug 輔助：把樣本簡化成可讀字串 ----------

def _fmt_temps(temps: List[Optional[float]]) -> str:
    """20 個溫度用 compact 格式呈現，例如 [25.1, 26.0, ...]"""
    return "[" + ", ".join("—" if v is None else f"{v:.1f}" for v in temps) + "]"


def _fmt_rates(rates: List[Optional[float]]) -> str:
    return "[" + ", ".join("—" if v is None else f"{v:+.2f}" for v in rates) + "]"


# ---------- 背景 poller ----------

def poller() -> None:
    log.info("poller 啟動")
    with state["lock"]:
        state["poller_running"] = True

    s = load_settings()
    state["gx20"] = GX20(host=s["gx20_host"], port=int(s["gx20_port"]))
    log.info("GX20 連線實例建立: %s:%s", s["gx20_host"], s["gx20_port"])

    round_no = 0
    while True:
        round_no += 1
        t_start = time.time()
        try:
            s = load_settings()
            gx = state["gx20"]
            if gx.gsRemoteHost != s["gx20_host"] or gx.gnRemotePort != int(s["gx20_port"]):
                log.info("GX20 連線設定變更 %s:%s → %s:%s",
                         gx.gsRemoteHost, gx.gnRemotePort,
                         s["gx20_host"], s["gx20_port"])
                gx = GX20(host=s["gx20_host"], port=int(s["gx20_port"]))
                with state["lock"]:
                    state["gx20"] = gx

            log.debug("[round #%d] 開始讀取 GX20 (%s:%s)",
                      round_no, gx.gsRemoteHost, gx.gnRemotePort)
            data = gx.get_all_temperatures()
            if data is None:
                with state["lock"]:
                    state["last_error"] = "GX20 讀取失敗"
                    state["connected"]  = False
                log.warning("[round #%d] GX20 讀取失敗（連線中斷？）", round_no)
                time.sleep(config.POLL_INTERVAL_SEC)
                continue

            ts = datetime.now().replace(microsecond=0).isoformat()

            with state["lock"]:
                state["last_ts"]    = ts
                state["last_error"] = None
                state["connected"]  = True

            # 寫 SQLite + 更新 ring buffer
            for station, temps in data.items():
                storage.insert_sample(ts, station, temps)
                state["ring"][station].append((ts, list(temps)))
                log.debug("[round #%d] %s 寫入 SQLite 成功，ring size=%d, temps=%s",
                          round_no, station, len(state["ring"][station]), _fmt_temps(temps))

            # 計算 rate / avg（用 ring buffer，不再查 DB）
            rate_window = int(s["rate_window_min"])
            avg_window  = int(s["avg_window_min"])

            for station, temps in data.items():
                rates = [compute_rate_from_ring(station, rate_window, i) for i in range(20)]
                avgs  = [compute_avg_from_ring(station, avg_window, i)  for i in range(20)]
                payload = {
                    "ts":      ts,
                    "station": station,
                    "temps":   temps,
                    "rate":    rates,
                    "avg":     avgs,
                }
                socketio.emit("new_sample", payload)
                log.debug("[round #%d] %s emit new_sample rate=%s avg=%s",
                          round_no, station, _fmt_rates(rates), _fmt_rates(avgs))

            elapsed_ms = (time.time() - t_start) * 1000
            log.info("[round #%d] OK ts=%s stations=%d elapsed=%.1fms",
                     round_no, ts, len(data), elapsed_ms)

            # 定期 purge 過期資料（約每 5 分鐘一次）
            now_ts = time.time()
            if now_ts - state["last_purge_at"] > 300:
                retention = int(s.get("retention_days", config.DEFAULT_RETENTION_DAYS))
                try:
                    deleted = storage.purge_old_samples(retention)
                    log.info("purge 過期資料（保留 %d 天）→ 刪除 %d 筆", retention, deleted)
                except Exception as e:
                    log.warning("purge_old_samples 失敗: %s", e)
                state["last_purge_at"] = now_ts

        except Exception as e:
            log.exception("[round #%d] poller 錯誤: %s", round_no, e)
            with state["lock"]:
                state["last_error"] = str(e)
                state["connected"]  = False

        time.sleep(config.POLL_INTERVAL_SEC)


# ---------- 路由 ----------

@app.route("/favicon.ico")
def favicon():
    return send_from_directory(app.static_folder, "favicon.ico", mimetype="image/x-icon")


@app.route("/")
def index():
    return render_template(
        "index.html",
        stations=STATIONS,
        points_per_station=POINTS_PER_STATION,
    )


@app.route("/settings")
def settings_page():
    return render_template(
        "settings.html",
        stations=STATIONS,
        points_per_station=POINTS_PER_STATION,
    )


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    return jsonify(load_settings())


@app.route("/api/settings", methods=["POST"])
def api_set_settings():
    patch = request.get_json(silent=True) or {}
    if not isinstance(patch, dict):
        return jsonify({"ok": False, "error": "body 須為 JSON 物件"}), 400
    save_settings(patch)
    return jsonify({"ok": True})


@app.route("/api/history/<station>")
def api_history(station: str):
    """
    拉取該站歷史。
    支援 query string:
      ?max_points=N          → 若原始筆數 > N，自動 LTTB 降取樣到 N
                                （預設為 settings.max_points，預設 2000）
      ?since_minutes=N        → 只拉最近 N 分鐘
                                （預設為 settings.chart_x_minutes，0 = 全部資料）

    v3：原本用的 history_minutes 設定已從 UI 移除，
       改用 chart_x_minutes 為主，0 拉全部。
    """
    if station not in STATIONS:
        return jsonify({"ok": False, "error": "unknown station"}), 404
    s = load_settings()

    # 預設 since_minutes：先看 query string，沒給就以 chart_x_minutes 為準
    # 0 = 拉全部 → 以 10 年 (5_256_000 分) 上限呼叫 query_recent
    try:
        q_since = int(request.args.get("since_minutes", "-1"))
    except (TypeError, ValueError):
        q_since = -1
    if q_since < 0:
        chart_x = int(s.get("chart_x_minutes", 0) or 0)
        q_since = chart_x if chart_x > 0 else 5256000

    rows = storage.query_recent(station, since_minutes=q_since)
    original_count = len(rows)

    # 決定 max_points
    try:
        max_points = int(request.args.get("max_points", s.get("max_points", config.DEFAULT_MAX_POINTS)))
    except (TypeError, ValueError):
        max_points = config.DEFAULT_MAX_POINTS

    downsampled = False
    if max_points > 0 and original_count > max_points:
        rows = downsample_rows(rows, ts_key="ts", point_keys=[f"t{i:02d}" for i in range(1, 21)], threshold=max_points)
        downsampled = True

    return jsonify({
        "ok": True,
        "rows": rows,
        "count": len(rows),
        "original_count": original_count,
        "downsampled": downsampled,
        "max_points": max_points,
    })


@app.route("/api/latest/<station>")
def api_latest(station: str):
    """取得指定工位最新一筆（完整 new_sample 格式：含 temps / rate / avg）。"""
    if station not in STATIONS:
        return jsonify({"ok": False, "error": "unknown station"}), 404
    r = storage.query_latest(station)
    if r is None:
        return jsonify({"ok": True, "payload": None})
    # 組合成 onNewSample 用的 payload（讓前端可直接 updateReadoutTable）
    s = load_settings()
    rate_window = int(s.get("rate_window_min", config.DEFAULT_RATE_WINDOW_MIN))
    avg_window = int(s.get("avg_window_min", config.DEFAULT_AVG_WINDOW_MIN))
    temps = [r.get(f"t{i+1:02d}") for i in range(20)]
    rates = [compute_rate_from_ring(station, rate_window, i) for i in range(20)]
    avgs  = [compute_avg_from_ring(station, avg_window,  i) for i in range(20)]
    payload = {
        "ts":      r["ts"],
        "station": station,
        "temps":   temps,
        "rate":    rates,
        "avg":     avgs,
    }
    return jsonify({"ok": True, "payload": payload, "row": r})


@app.route("/api/connection")
def api_connection():
    with state["lock"]:
        return jsonify({
            "connected":   state["connected"],
            "last_error":  state["last_error"],
            "last_ts":     state["last_ts"],
            "host":        state["gx20"].gsRemoteHost if state["gx20"] else None,
            "port":        state["gx20"].gnRemotePort if state["gx20"] else None,
        })


@app.route("/api/db_stats")
def api_db_stats():
    """資料庫統計（v5：跨 6 工位 DB）。"""
    s = load_settings()
    by_station = storage.count_samples_by_station()
    time_range = {st: storage.sample_time_range(st) for st in STATIONS}
    total = sum(by_station.values())
    return jsonify({
        "ok": True,
        "by_station": by_station,
        "time_range": time_range,
        "total": total,
        "retention_days": int(s.get("retention_days", config.DEFAULT_RETENTION_DAYS)),
        "archive_keep_per_station": storage.ARCHIVE_KEEP_PER_STATION,
        "db_layout": "v5 (per-station DB + shared settings DB)",
    })


@app.route("/api/clear", methods=["POST"])
def api_clear():
    """
    清除指定工位的 samples（v5）。

    接收：
      - body JSON 或 query string 都可以
        - station  = '工位5'         必填
        - archive  = true / false     預設 true
      - 未帶 station → 拒絕
        （原本的「全清」按鈕已移除；如需全清可明確帶 station='ALL'）
    流程：
      1) 若 archive=true → 先 archive_station(station) 拷到 archive/
      2) clear_station_db(station) 刪除該工位 DB
      3) 清空該工位 ring buffer
    """
    payload = request.get_json(silent=True) or {}
    station = (
        payload.get("station")
        or request.args.get("station")
        or request.form.get("station")
    )
    archive_flag = (
        payload.get("archive")
        if "archive" in payload
        else (request.args.get("archive", "true").lower() in ("1", "true", "yes"))
    )

    if station == "ALL":
        # 全清（不再歸檔，避免歸檔檔爆量；如需可擴充為逐一歸檔）
        deleted = 0
        with state["lock"]:
            for s in STATIONS:
                if storage.clear_station_db(s):
                    state["ring"][s].clear()
                    deleted += 1
        log.warning("api_clear: 全清 %d 個工位（未歸檔）", deleted)
        return jsonify({"ok": True, "cleared": deleted, "archived": 0, "mode": "all"})

    if not station or station not in STATIONS:
        return jsonify({"ok": False, "error": "必須帶 station 參數（工位名）"}), 400

    archived_path = None
    if archive_flag:
        archived_path = storage.archive_station(station)
    else:
        log.info("api_clear: 使用者選擇不歸檔，直接刪除 %s", station)

    ok = storage.clear_station_db(station)
    if not ok:
        return jsonify({"ok": False, "error": "刪除 DB 失敗，請看 log"}), 500

    # 清空該工位 ring buffer
    with state["lock"]:
        state["ring"][station].clear()

    log.info("api_clear: 已清除 %s（歸檔=%s，路徑=%s）", station, archive_flag, archived_path)
    return jsonify({
        "ok": True,
        "station": station,
        "archived": bool(archived_path),
        "archive_path": archived_path,
        "mode": "single",
    })


@app.route("/api/archives", methods=["GET"])
def api_archives():
    """
    列出歸檔清單。
    Query:
      ?station=工位5   只列該工位；不帶 → 列全部
    """
    s = request.args.get("station")
    if s and s not in STATIONS:
        return jsonify({"ok": False, "error": "unknown station"}), 404
    archives = storage.list_archives(station=s)
    return jsonify({
        "ok": True,
        "archives": archives,
        "count": len(archives),
        "keep_per_station": storage.ARCHIVE_KEEP_PER_STATION,
    })


@app.route("/api/channels")
def api_channels():
    return jsonify({"ok": True, "channels": CHANNEL_NUMBER})


# ---------- Debug 控制 ----------

@app.route("/api/debug", methods=["GET", "POST"])
def api_debug():
    """
    GET  → 回傳目前 debug 狀態
    POST → 切換 debug 狀態
        body: {"enabled": true/false}
        也可省略 body（toggle）
    變更會即時生效，並寫入 settings 表。
    """
    cur = _is_debug_enabled()
    if request.method == "GET":
        return jsonify({
            "ok": True,
            "enabled": cur,
            "log_path": LOG_PATH,
        })

    patch = request.get_json(silent=True) or {}
    if "enabled" in patch:
        new_val = bool(patch["enabled"])
    else:
        new_val = not cur
    storage.set_setting("debug_log_enabled", "1" if new_val else "0")
    apply_log_level_from_settings()
    log.info("debug log 模式切換: %s → %s", cur, new_val)
    return jsonify({
        "ok": True,
        "enabled": new_val,
        "log_path": LOG_PATH,
    })


@app.route("/api/debug/log_tail")
def api_debug_log_tail():
    """
    查詢 app.log 末段（debug 模式診斷用）。
    Query:
      ?lines=N  預設 100
    """
    try:
        n = max(1, min(2000, int(request.args.get("lines", "100"))))
    except (TypeError, ValueError):
        n = 100
    if not os.path.exists(LOG_PATH):
        return jsonify({"ok": True, "lines": [], "log_path": LOG_PATH})
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        tail = lines[-n:] if len(lines) > n else lines
        return jsonify({
            "ok": True,
            "lines": [l.rstrip("\n") for l in tail],
            "total": len(lines),
            "returned": len(tail),
            "log_path": LOG_PATH,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------- OTA 管理 ----------

import ota as _ota

@app.route("/api/admin/status")
def api_admin_status():
    """
    檢查 OTA 狀態。
    公開可讀（不洩漏 token），只回 fingerprint 供使用者確認 token 已設定。
    """
    s = _ota.status()
    return jsonify(s)


@app.route("/api/admin/ota", methods=["POST"])
def api_admin_ota():
    """
    上傳單檔 OTA 更新。
    Header: X-OTA-Token: <token>
    Form:
      - file:  檔案
      - target: 相對於 APP_ROOT 的路徑（例: static/js/main.js）
    """
    if not _ota.check_token(request.headers.get("X-OTA-Token")):
        return jsonify({"ok": False, "error": "invalid or missing token"}), 401
    target = (request.form.get("target") or "").strip()
    if not target:
        return jsonify({"ok": False, "error": "missing 'target' field"}), 400
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "missing 'file'"}), 400
    content = f.read()
    result = _ota.save_file(target, content)
    log.info("OTA 上傳 target=%s size=%d → %s", target, len(content), result.get("ok"))
    status_code = 200 if result.get("ok") else 400
    return jsonify(result), status_code


@app.route("/api/admin/ota_bundle", methods=["POST"])
def api_admin_ota_bundle():
    """
    一次推多檔 OTA 更新。
    Header: X-OTA-Token: <token>
    Body (JSON):
      {
        "files": [
          {"target": "static/js/main.js", "content_b64": "..."},
          {"target": "templates/index.html", "content_b64": "..."}
        ],
        "restart": true   // 可選，呼叫後觸發自我重啟
      }
    """
    if not _ota.check_token(request.headers.get("X-OTA-Token")):
        return jsonify({"ok": False, "error": "invalid or missing token"}), 401
    body = request.get_json(silent=True) or {}
    files = body.get("files") or []
    if not isinstance(files, list) or not files:
        return jsonify({"ok": False, "error": "missing 'files' list"}), 400
    results = []
    all_ok = True
    import base64 as _b64
    for entry in files:
        target = (entry.get("target") or "").strip()
        b64 = entry.get("content_b64") or ""
        try:
            content = _b64.b64decode(b64)
        except Exception as e:
            results.append({"target": target, "ok": False, "error": f"base64 decode: {e}"})
            all_ok = False
            continue
        r = _ota.save_file(target, content)
        results.append(r)
        if not r.get("ok"):
            all_ok = False
    log.info("OTA bundle 上傳 %d 個檔案，結果: %s", len(files),
             "ALL OK" if all_ok else "PARTIAL/FAIL")
    response = {"ok": all_ok, "results": results, "saved_count": sum(1 for r in results if r.get("ok"))}
    if body.get("restart") and all_ok:
        response["restart"] = _ota.schedule_restart(delay_sec=2)
    return jsonify(response), (200 if all_ok else 400)


@app.route("/api/admin/restart", methods=["POST"])
def api_admin_restart():
    """
    觸發自我重啟。
    Header: X-OTA-Token: <token>
    Body: {"delay": 2}  // 可選，預設 2 秒
    """
    if not _ota.check_token(request.headers.get("X-OTA-Token")):
        return jsonify({"ok": False, "error": "invalid or missing token"}), 401
    body = request.get_json(silent=True) or {}
    delay = int(body.get("delay", 2))
    delay = max(0, min(delay, 10))
    log.warning("管理員觸發自我重啟（%d 秒後）", delay)
    return jsonify(_ota.schedule_restart(delay_sec=delay))


# ---------- CSV 匯出 ----------

def _sanitize_csv_cell(s: str) -> str:
    """避免在 CSV 頭出現裡號 / 跳脫 / 開頭運算式。"""
    if s is None:
        return ""
    s = str(s)
    # 防止 Excel 公式注入
    if s and s[0] in ("=", "+", "-", "@"):
        s = "'" + s
    # 移除可能造成解析錯誤的字元
    for ch in ['\r', '\n', '\t', '"']:
        s = s.replace(ch, " ")
    return s


@app.route("/api/export_csv/<station>")
def api_export_csv(station: str):
    """
    匯出指定工位溫度記錄為 CSV（v3 改版：取樣整合為每分鐘一筆）。

    區間：
      - query string ?since_minutes=N
          > 0   → 只匯出近 N 分鐘
          = 0   → 拉全部 DB
          未帶 → 以 settings.chart_x_minutes 為準（0=拉全部，>0=近 N 分鐘）
      - chart_x_minutes 是主畫面 X 軸的長度，與圖表看到的一致
    取樣：
      - DB 原始為 10 秒一筆
      - 以「分鐘」為 bucket，同一分鐘內的所有原始樣本以算術平均
        整合成 1 筆（每個 channel 各別平均；全 None → 空字串）
    格式：
      - 編碼: UTF-8-sig (BOM)
      - 標頭: datetime, 別名1, 別名2, ... 別名20
      - 時間格式: %m/%d/%y %H:%M:%S
      - 不包含 rate / avg
      - 不考慮隱藏狀態 → 20 個接點都出
    """
    if station not in STATIONS:
        return jsonify({"ok": False, "error": "unknown station"}), 404

    # 決定時間範圍
    try:
        q_since = int(request.args.get("since_minutes", "-1"))
    except (TypeError, ValueError):
        q_since = -1

    s = load_settings()
    if q_since < 0:
        # 未帶 → 以 X 軸長度為主
        chart_x = int(s.get("chart_x_minutes", 0) or 0)
        q_since = chart_x if chart_x > 0 else 0

    if q_since > 0:
        raw_rows = storage.query_recent(station, since_minutes=q_since)
    else:
        # 拉全部：給 10 年上限
        raw_rows = storage.query_recent(station, since_minutes=5256000)

    if not raw_rows:
        return jsonify({"ok": False, "error": "no data"}), 404

    # 取得別名
    aliases = s.get("ch_alias", {}).get(station, [])

    # 造標頭
    headers = ["datetime"]
    for i in range(1, 21):
        alias = (aliases[i - 1] or "").strip() if i - 1 < len(aliases) else ""
        col_name = alias if alias else str(i)
        headers.append(_sanitize_csv_cell(col_name))

    # 整合 10 秒→1 分鐘：以每分鐘 bucket 算術平均
    # bucket_key 取該分鐘的整點 ISO 字串（避免浮點誤差）
    buckets: Dict[str, Dict[str, Any]] = {}   # key=YYYY-MM-DDTHH:MM:00, value={sums, counts, has_any}
    for r in raw_rows:
        try:
            dt = datetime.fromisoformat(r["ts"])
        except Exception:
            continue
        # 對齊到分鐘起點
        bucket_dt = dt.replace(second=0, microsecond=0)
        bkey = bucket_dt.isoformat()
        b = buckets.get(bkey)
        if b is None:
            b = {
                "_dt": bucket_dt,
                "sums":  [0.0] * 20,
                "cnts":  [0]   * 20,
                "any":   [False] * 20,
            }
            buckets[bkey] = b
        for i in range(1, 21):
            v = r.get(f"t{i:02d}")
            if v is None:
                continue
            try:
                b["sums"][i - 1] += float(v)
                b["cnts"][i - 1] += 1
                b["any"][i - 1]  = True
            except (TypeError, ValueError):
                # 非數字視為 None，不計入平均
                continue

    if not buckets:
        return jsonify({"ok": False, "error": "no data"}), 404

    # 依時間排序後輸出
    sorted_buckets = sorted(buckets.values(), key=lambda b: b["_dt"])

    # 造 CSV (UTF-8-sig BOM)
    buf = io.StringIO()
    buf.write("\ufeff")  # BOM
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(headers)
    for b in sorted_buckets:
        ts_str = b["_dt"].strftime("%m/%d/%y %H:%M:%S")
        row = [ts_str]
        for i in range(20):
            if b["any"][i]:
                avg = b["sums"][i] / b["cnts"][i]
                # 與原樣本位數一致：取小數 4 位（避免科學記號）
                row.append(f"{avg:.4f}")
            else:
                row.append("")
        writer.writerow(row)

    csv_text = buf.getvalue()
    buf.close()

    # 檔名 v3 修正：原本 filename="工位5_xxx.csv" 在 werkzeug 會走 latin-1 編 header
    # → UnicodeEncodeError。改用 ASCII 檔名（拼音化 station）作為 filename=，
    #   UTF-8 版放 filename*= (RFC 5987) 供支援的 client 沿用中文檔名。
    ts_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    ascii_fname = f"{_ascii_station(station)}_{ts_str}.csv"
    utf8_fname  = f"{station}_{ts_str}.csv"
    from urllib.parse import quote
    quoted_utf8 = quote(utf8_fname, safe="")

    return Response(
        csv_text,
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{ascii_fname}\"; "
                f"filename*=UTF-8''{quoted_utf8}"
            ),
            "Content-Length": str(len(csv_text.encode("utf-8-sig"))),
        },
    )


def _ascii_station(station: str) -> str:
    """
    把 "工位5" / "工位A" 這類中文站名轉成 ASCII，給 Content-Disposition 的 filename= 用。
    規則：掳到所有數字 → 拼成 "Station5"；掳不到 → "Station"。
    這可確保 header 走 latin-1 編碼不會爆。
    """
    import re
    m = re.search(r'\d+', station or "")
    if m:
        return f"Station{m.group(0)}"
    return "Station"


# ---------- 啟動 / 關閉 ----------

def _on_shutdown():
    log.info("=" * 60)
    log.info("GX20 Web Monitor 關閉中...")
    log.info("程式關閉，DB 保留（下次啟動可繼續累積）")
    # 不再 clear_db() - 資料持久化
    # 仍關閉 ring buffer 與 GX20 連線
    with state["lock"]:
        for s in STATIONS:
            state["ring"][s].clear()
    if state["gx20"] is not None:
        # GX20 物件無顯式 close（with statement 內已處理）
        pass
    log.info("關閉完成")
    log.info("=" * 60)


def _signal_handler(signum, frame):
    log.info("signal %s received", signum)
    _on_shutdown()
    raise SystemExit(0)


def _register_shutdown_hooks():
    atexit.register(_on_shutdown)
    signal.signal(signal.SIGINT,  _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)


def main():
    # 1) 初始化 DB（保留舊資料）
    storage.init_db(reset=False)

    # 2) v4：若 config/settings.json 存在 → 優先採用並寫入 SQLite
    json_cfg = load_settings_from_json()
    if json_cfg is not None:
        log.info("讀取設定檔 %s，直接套用", SETTINGS_JSON_PATH)
        apply_json_to_sqlite(json_cfg)
    elif not storage.get_all_settings():
        # 3) SQLite 為空 → 寫入預設
        d = config.default_settings()
        for k, v in d.items():
            storage.set_setting(k, config.to_json(v) if isinstance(v, (dict, list)) else str(v))
        # 預設關閉 debug log
        storage.set_setting("debug_log_enabled", "0")
        # 順手 dump 一次，避免下次只能依賴 SQLite
        try:
            merged = load_settings()
            os.makedirs(SETTINGS_DIR, exist_ok=True)
            with open(SETTINGS_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2, sort_keys=True)
            log.info("首次啟動，已建立 %s", SETTINGS_JSON_PATH)
        except Exception as e:
            log.warning("首次 dump 設定檔失敗: %s", e)

    # 3) 套用 debug 設定
    apply_log_level_from_settings()
    log.info("=" * 60)
    log.info("GX20 Web Monitor 啟動中...")
    log.info("log file: %s", LOG_PATH)
    log.info("debug 模式: %s", "開" if _is_debug_enabled() else "關")

    # 4) 啟動時先清一次過期資料
    try:
        s = load_settings()
        storage.purge_old_samples(int(s.get("retention_days", config.DEFAULT_RETENTION_DAYS)))
    except Exception as e:
        log.warning("啟動時 purge 失敗: %s", e)

    # 5) 註冊關閉 hooks
    _register_shutdown_hooks()

    # 6) 啟動 poller
    t = threading.Thread(target=poller, name="gx20-poller", daemon=True)
    t.start()

    log.info("啟動 Flask-SocketIO 0.0.0.0:5000")
    log.info("=" * 60)
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
