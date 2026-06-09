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
"""

import atexit
import logging
import signal
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Deque

from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_socketio import SocketIO

import config
import storage
from gx20_reader import GX20, STATIONS, POINTS_PER_STATION, CHANNEL_NUMBER
from lttb import downsample_rows

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("app")

# === Flask / SocketIO 初始化 ===
app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["SECRET_KEY"] = "gx20-web-monitor-secret"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

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
              "rate_window_min", "avg_window_min", "history_minutes",
              "retention_days", "max_points"):
        if k in raw:
            try:
                out[k] = int(raw[k]) if k in (
                    "gx20_port", "rate_window_min", "avg_window_min",
                    "history_minutes", "retention_days", "max_points"
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


# ---------- 背景 poller ----------

def poller() -> None:
    log.info("poller 啟動")
    with state["lock"]:
        state["poller_running"] = True

    s = load_settings()
    state["gx20"] = GX20(host=s["gx20_host"], port=int(s["gx20_port"]))

    while True:
        try:
            s = load_settings()
            gx = state["gx20"]
            if gx.gsRemoteHost != s["gx20_host"] or gx.gnRemotePort != int(s["gx20_port"]):
                log.info("GX20 連線設定變更 → 重建實例")
                gx = GX20(host=s["gx20_host"], port=int(s["gx20_port"]))
                with state["lock"]:
                    state["gx20"] = gx

            data = gx.get_all_temperatures()
            if data is None:
                with state["lock"]:
                    state["last_error"] = "GX20 讀取失敗"
                    state["connected"]  = False
                log.warning("GX20 讀取失敗，下次輪詢重試")
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

            # 計算 rate / avg（用 ring buffer，不再查 DB）
            rate_window = int(s["rate_window_min"])
            avg_window  = int(s["avg_window_min"])

            for station, temps in data.items():
                payload = {
                    "ts":      ts,
                    "station": station,
                    "temps":   temps,
                    "rate":    [compute_rate_from_ring(station, rate_window, i) for i in range(20)],
                    "avg":     [compute_avg_from_ring(station, avg_window, i)  for i in range(20)],
                }
                socketio.emit("new_sample", payload)

            # 定期 purge 過期資料（約每 5 分鐘一次）
            now_ts = time.time()
            if now_ts - state["last_purge_at"] > 300:
                retention = int(s.get("retention_days", config.DEFAULT_RETENTION_DAYS))
                try:
                    storage.purge_old_samples(retention)
                except Exception as e:
                    log.warning("purge_old_samples 失敗: %s", e)
                state["last_purge_at"] = now_ts

        except Exception as e:
            log.exception("poller 錯誤: %s", e)
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
    拉取該站近 history_minutes 分鐘歷史。
    支援 query string:
      ?max_points=N    → 若原始筆數 > N，自動 LTTB 降取樣到 N
                         （預設為 settings.max_points，預設 2000）
    """
    if station not in STATIONS:
        return jsonify({"ok": False, "error": "unknown station"}), 404
    s = load_settings()
    rows = storage.query_recent(station, since_minutes=int(s["history_minutes"]))
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
    if station not in STATIONS:
        return jsonify({"ok": False, "error": "unknown station"}), 404
    r = storage.query_latest(station)
    return jsonify({"ok": True, "row": r})


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
    """資料庫統計（每工位筆數與時間範圍）。"""
    s = load_settings()
    by_station = storage.count_samples_by_station()
    total = sum(by_station.values())
    return jsonify({
        "ok": True,
        "by_station": by_station,
        "total": total,
        "retention_days": int(s.get("retention_days", config.DEFAULT_RETENTION_DAYS)),
    })


@app.route("/api/clear", methods=["POST"])
def api_clear():
    """手動一鍵清除 SQLite。"""
    storage.clear_db()
    storage.init_db(reset=False)
    with state["lock"]:
        for s in STATIONS:
            state["ring"][s].clear()
    return jsonify({"ok": True})


@app.route("/api/channels")
def api_channels():
    return jsonify({"ok": True, "channels": CHANNEL_NUMBER})


# ---------- 啟動 / 關閉 ----------

def _on_shutdown():
    log.info("程式關閉，DB 保留（下次啟動可繼續累積）")
    # 不再 clear_db() - 資料持久化
    # 仍關閉 ring buffer 與 GX20 連線
    with state["lock"]:
        for s in STATIONS:
            state["ring"][s].clear()
    if state["gx20"] is not None:
        # GX20 物件無顯式 close（with statement 內已處理）
        pass


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

    # 2) 寫入預設設定（若表為空）
    if not storage.get_all_settings():
        d = config.default_settings()
        for k, v in d.items():
            storage.set_setting(k, config.to_json(v) if isinstance(v, (dict, list)) else str(v))

    # 3) 啟動時先清一次過期資料
    try:
        s = load_settings()
        storage.purge_old_samples(int(s.get("retention_days", config.DEFAULT_RETENTION_DAYS)))
    except Exception as e:
        log.warning("啟動時 purge 失敗: %s", e)

    # 4) 註冊關閉 hooks
    _register_shutdown_hooks()

    # 5) 啟動 poller
    t = threading.Thread(target=poller, name="gx20-poller", daemon=True)
    t.start()

    log.info("啟動 Flask-SocketIO 0.0.0.0:5000")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
