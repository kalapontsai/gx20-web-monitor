# -*- coding: utf-8 -*-
"""
config.py
=========
集中管理所有預設值、預設顏色、預設站點對應等。
"""

import json
from gx20_reader import STATIONS, POINTS_PER_STATION, DEFAULT_COLORS, CHANNEL_NUMBER

# ---------- GX20 連線 ----------
DEFAULT_GX20_HOST = "192.168.1.1"
DEFAULT_GX20_PORT = 34434
POLL_INTERVAL_SEC = 10

# ---------- 圖表 ----------
DEFAULT_HISTORY_MINUTES = 60            # 首次載入拉多少分鐘的歷史
DEFAULT_RATE_WINDOW_MIN = 5             # 升降速率計算區間（分鐘）
DEFAULT_AVG_WINDOW_MIN = 10             # 平均值計算區間（分鐘）
DEFAULT_Y_MIN = -20                     # Y 軸最小值
DEFAULT_Y_MAX = 100                     # Y 軸最大值
DEFAULT_THEME = "light"                 # 預設主題（light/dark）
DEFAULT_RETENTION_DAYS = 7              # DB 保留天數
DEFAULT_MAX_POINTS = 2000               # /api/history 降取樣門檻
RING_BUFFER_SIZE = 720                  # poller ring buffer 最多保留筆數（10 秒一筆，720=2hr）

# ---------- 預設接點設定 ----------
def default_visibility() -> dict:
    """預設全部顯示。"""
    return {s: [True] * POINTS_PER_STATION for s in STATIONS}


def default_alias() -> dict:
    """預設別名 = 接點編號 1~20。"""
    return {s: [f"Ch{i+1:02d}" for i in range(POINTS_PER_STATION)] for s in STATIONS}


def default_color() -> dict:
    """預設顏色（每工位皆用同一組 20 色）。"""
    return {s: list(DEFAULT_COLORS) for s in STATIONS}


# ---------- 預設完整設定 dict（給 settings 頁初始化用） ----------
def default_settings() -> dict:
    return {
        "gx20_host":      DEFAULT_GX20_HOST,
        "gx20_port":      DEFAULT_GX20_PORT,
        "y_axis_min":     DEFAULT_Y_MIN,
        "y_axis_max":     DEFAULT_Y_MAX,
        "rate_window_min": DEFAULT_RATE_WINDOW_MIN,
        "avg_window_min":  DEFAULT_AVG_WINDOW_MIN,
        "history_minutes": DEFAULT_HISTORY_MINUTES,
        "ch_visibility":  default_visibility(),
        "ch_alias":       default_alias(),
        "ch_color":       default_color(),
        "theme":          DEFAULT_THEME,
        "retention_days": DEFAULT_RETENTION_DAYS,
        "max_points":     DEFAULT_MAX_POINTS,
    }


def to_json(d) -> str:
    return json.dumps(d, ensure_ascii=False)


def from_json(s, default=None):
    if s is None:
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


# 對外 re-export，方便 app.py / templates 使用
__all__ = [
    "STATIONS", "POINTS_PER_STATION", "CHANNEL_NUMBER",
    "DEFAULT_GX20_HOST", "DEFAULT_GX20_PORT", "POLL_INTERVAL_SEC",
    "DEFAULT_HISTORY_MINUTES", "DEFAULT_RATE_WINDOW_MIN", "DEFAULT_AVG_WINDOW_MIN",
    "DEFAULT_Y_MIN", "DEFAULT_Y_MAX",
    "DEFAULT_THEME", "DEFAULT_RETENTION_DAYS", "DEFAULT_MAX_POINTS", "RING_BUFFER_SIZE",
    "default_visibility", "default_alias", "default_color", "default_settings",
    "to_json", "from_json",
]
