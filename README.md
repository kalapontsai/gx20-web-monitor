# GX20 Web Monitor — 溫度監視網頁版

> YOKOGAWA GX20 紙記錄器的網頁版溫度監視系統
>
> 6 工位 × 20 接點 = 120 點，每 10 秒取樣一次，**可持續記錄 7 天以上**
>
> **v5.0** 改版重點：6 工位獨立 DB + 清除前歸檔
>
> **v4.0** 改版重點：最新讀值下拉化、CSV 平均整合、設定檔同步
>
> **v3.0** 改版重點：debug logger、圖表精簡、X 軸範圍動態、CSV 整合匯出、設定檔同步
>
> **v2.0** 改版重點：資料持久化、LTTB 降取樣、明暗主題、ring buffer 計算

---

## 目錄

1. [專案概述](#1-專案概述)
2. [與桌面版差異](#2-與桌面版差異)
3. [v3 / v4 / v5 改版總覽](#3-v3--v4--v5-改版總覽)
4. [系統架構](#4-系統架構)
5. [技術選型](#5-技術選型)
6. [資料模型（SQLite Schema）](#6-資料模型sqlite-schema)
7. [模組設計](#7-模組設計)
8. [設定同步檔 config/settings.json](#8-設定同步檔-configsettingsjson)
9. [資料生命週期與 DB 佈局](#9-資料生命週期與-db-佈局)
10. [效能與降取樣策略](#10-效能與降取樣策略)
11. [前端 UI 與互動](#11-前端-ui-與互動)
12. [路由與 API](#12-路由與-api)
13. [設定頁欄位](#13-設定頁欄位)
14. [主題系統（light / dark）](#14-主題系統light--dark)
15. [執行方式](#15-執行方式)
16. [故障排除](#16-故障排除)
17. [已知限制](#17-已知限制)

---

## 1. 專案概述

### 功能

- **6 個工位 × 20 個溫度接點**（共 120 點）即時溫度監看
- 每 10 秒取樣一次，**資料以 SQLite 持久保存**（預設保留 7 天）
- 即時趨勢圖：**20 條溫度線**（圖例隱藏，顯示/隱藏統一在設定頁管理）
- 右側「最新讀值」表格：名稱、讀值、**速率 (°C/N 分鐘)**、**平均 (°C/N 分鐘)**
- 主畫面內可直接下拉調整：X 軸範圍、速率/平均計算區間
- 網頁式設定：站點選擇、接點顯示/隱藏、別名、256 色盤選色、Y 軸範圍
- 明亮 / 暗黑主題切換
- CSV 匯出：依目前 X 軸範圍，原始 10 秒/筆自動平均整合為 1 分鐘/筆
- 多瀏覽器分頁透過 SocketIO 自動同步
- Debug logger：寫到 `logs/app.log`，可從設定頁或 API 動態切換等級
- 設定值同步存成 `config/settings.json`，重新啟動自動套用
- 「每分頁 session 暫存」+「全域 SQLite / JSON 持久保存」三層架構

### 不在範圍

- **PW3335 電力計**（題目只要 GX20，桌面版的電力功能不移植）
- 多台 GX20 同時連線（本版只支援一台，Host/Port 已在 settings 留欄位可調）

### 適用場景

- 工廠/實驗室 GX20 溫度即時監看
- 7 天以上趨勢分析（接 GX20 後即可累積資料）
- 多人多裝置同時監看（透過瀏覽器）

---

## 2. 與桌面版差異

| 功能 | 桌面版 `GX20_PW3335.py` | 本網頁版 v3 |
|---|---|---|
| GUI 框架 | Tkinter | HTML / CSS / JS |
| 圖表 | matplotlib（後端算圖） | Chart.js（前端算圖） |
| 圖表曲線 | 溫度 + 速率 + 平均（三層）| **只畫溫度 20 條**；速率/平均改在右側表格 |
| 圖表圖例 | 預設顯示 | **隱藏**（顯示/隱藏統一在設定頁）|
| 資料儲存 | CSV 檔 | SQLite（持久）|
| 資料生命週期 | 永久累積在 CSV | SQLite 預設保留 7 天 |
| 關閉清除 | 不會 | **不會**（改用手動按鈕）|
| 取樣頻率 | 可調 10/60/180/300 秒 | 固定 10 秒 |
| 工位切換 | Notebook 6 個 tab | 下拉式選單（單頁）|
| 接點顏色 | 預設 | 256 色盤自選 |
| PW3335 電力 | 支援 | **不支援**（題目只要 GX20）|
| 多瀏覽器同步 | 無 | 透過 SocketIO 自動同步 |
| 主題 | 兩套（Ocean Deep / Serene Greens）| light / dark（CSS 變數）|
| 計算效能 | query_recent 全表 | ring buffer（720 筆記憶體）|
| 大資料繪圖 | matplotlib 自動處理 | LTTB 自動降取樣到 2000 點 |
| X 軸範圍 | 不可調 | 主畫面下拉：全部 / 15分 ~ 1天 |
| CSV 匯出 | 全部 + 10 秒/筆 | **依 X 軸範圍 + 平均整合為 1 分鐘/筆** |
| 跨 session 設定 | 不適用 | SQLite + JSON 檔 + sessionStorage 三層 |
| Debug 機制 | print | 結構化 logger + 檔案輪詢 + 動態等級 |
| DB 佈局 | 1 個 CSV | **v5：6 工位獨立 DB + 共用 settings DB + 歸檔保留 5 份** |
| 清除手只動 | N/A | **v5：只清當前工位 + 選擇性歸檔** |

---

## 3. v3 / v4 / v5 改版總覽

### v5.0 — 6 工位獨立 DB + 清除前歸檔

**背景**：6 工位非同步上下線，原有單一 `data/gx20.db` 設計會造成：
- 清除某工位只能全刪（其他工位一起陪葬）
- 6 工位輪流上下線，時間軸混雜難以分辨
- 清除無歸檔，按錯救不回

**佈局變更**：

```
data/
├── gx20_<station>.db        # 每工位一份 samples 表
├── gx20_settings.db         # 6 工位共用的 settings 表
└── archive/
    ├── gx20_<station>_<YYYYMMDD_HHMMSS>.db    # 清除前歸檔
    └── gx20_pre_migration_<時間>.db            # 舊佈局 migrate 記錄
```

**新行為**：

- 主畫面 / 設定頁 [清除資料] 改為 [清除此工位]
- 點擊 → 兩段式 confirm：是否歸檔 → 確認清除
- 歸檔自動保留最近 **5 份**（每工位各自），超過自動刪最舊
- 設定與資料分離：清資料不會洗掉 GX20 連線、別名、顏色
- 6 個小 DB 各自 WAL，輪流寫入比 1 個大 DB 友善

**API 變更**：

- `POST /api/clear` 必填 `station`（不再支援全清；如需全清帶 `station=ALL`）
- 新增 `GET /api/archives?station=工位5` 查歸檔清單
- `GET /api/db_stats` 加 `time_range`（每工位首/末筆時間）與 `archive_keep_per_station`

**向後相容**：

- 啟動時偵測舊 `data/gx20.db` → 自動 migrate
  - 1) 整份先歸檔為 `gx20_pre_migration_<時間>.db`
  - 2) samples 按 station 切到 6 個新 DB
  - 3) settings 複製到新 settings DB
  - 4) 刪除舊檔（WAL/SHM/JOURNAL 一起清）

### v4.0 — 最新讀值下拉化 + CSV 平均整合 + 設定檔同步

### v3.0 — Debug logger + 圖表精簡 + 動態 X 軸

- **Debug logger**：log 寫到 `logs/app.log`（RotatingFileHandler，2MB × 5 個備份）
  - 啟動 / 關閉、poller 每輪結果、HTTP 請求、SocketIO 連線 / 斷線都會入 log
  - 等級由 `settings` 表的 `debug_log_enabled` 控制（INFO ↔ DEBUG）
  - 新增 `GET /api/debug`、`POST /api/debug`、`GET /api/debug/log_tail` API
  - 設定頁有「Debug log」開關（3.7 偵錯區）
- **圖表精簡**：原本每接點 3 條線（temp/rate/avg）→ 只保留 20 條溫度線
  - rate / avg 仍由後端推播，前端只用於「最新讀值」表格
  - 圖例 `display: false`（接點顯示/隱藏全交由設定頁管理）
  - 移除主畫面頂部 [保存]、圖表標題、左下角說明框
- **X 軸範圍動態**：`settings.chart_x_minutes`（0 = 全部資料，>0 = 近 N 分鐘）
  - 主畫面新增 X 軸下拉選單（全部 / 15 / 30 / 1時 / 3時 / 6時 / 12時 / 1天）
  - Chart.js time scale 動態錨點，每 tick 滑動避免有效區間縮小
- **CSV 中文檔名 latin-1 修正**：`Content-Disposition` 改 ASCII + `filename*=UTF-8''` 雙路徑

### v4.0 — 最新讀值下拉化 + CSV 平均整合 + 設定檔同步

- **主畫面「最新讀值」下拉化**：
  - 三個 select 放同一列：X 軸 / 速率 (1~60 分鐘) / 平均 (1~60 分鐘, 3/6 小時)
  - 表頭「速率 (°C/N 分鐘)」「平均 (°C/N 分鐘)」動態更新
  - select change → 立即 client 端重畫 + 背景 POST `/api/settings`，下個 tick 套用
- **CSV 平均整合**：`/api/export_csv/<station>` 拉 raw rows 後依分鐘 bucket 算術平均
  - 每個 channel 各別平均；全 None → 空字串
  - 區間預設讀 `chart_x_minutes`（與 X 軸一致），可由 `?since_minutes=N` 覆寫
  - 檔名加區間標記：`工位5_60min_20260610_154959.csv`
- **設定檔同步**：`save_settings()` 同步 dump 整包設定到 `config/settings.json`
  - 啟動時若檔案存在 → 優先採用並寫回 SQLite（避免 DB 預設值誤蓋）
  - atomic write：`.tmp` + `os.replace`

### 設定頁欄位精簡

- 移除「歷史視窗 (分鐘)」input → 由主畫面 X 軸下拉取代
- 移除「計算時間長度」整段（升降速率 / 平均 input）→ 由主畫面下拉取代
- 保留 [保存] 按鈕（主畫面已無此按鈕）

---

## 4. 系統架構

```
gx20-web-monitor/
├── README.md                       本文件
├── requirements.txt                flask + flask-socketio
├── run.py                          啟動入口（python run.py）
├── gx20_reader.py                  GX20 TCP 通訊（移植自桌面版）
├── storage.py                      SQLite 層（v5：6 工位獨立 DB + settings DB + 歸檔）
├── config.py                       預設值集中管理
├── lttb.py                         LTTB 降取樣（後端版）
├── app.py                          Flask + Flask-SocketIO 主程式
├── data/                           SQLite 檔（v5 佈局）
│   ├── gx20_<station>.db           每工位一份 samples
│   ├── gx20_settings.db            6 工位共用的 settings
│   └── archive/                    清除前歸檔（每工位保留 5 份）
│       └── gx20_<station>_<時間>.db
├── config/                         設定同步檔（v4 新增）
│   ├── settings.json               啟動時若存在 → 自動套用
│   └── settings.example.json       範例
├── logs/                           Debug logger 輸出（v3 新增）
│   └── app.log                     RotatingFileHandler（2MB × 5）
├── templates/
│   ├── index.html                  監看主頁
│   └── settings.html               設定頁
└── static/
    ├── css/
    │   └── style.css               CSS 變數主題系統
    ├── js/
    │   ├── storage.js              跨分頁 sessionStorage 設定層
    │   ├── main.js                 主頁邏輯
    │   ├── settings.js             設定頁邏輯
    │   ├── colorpicker.js          256 色盤
    │   └── lttb.js                 LTTB 降取樣（前端版）
    └── vendor/
        ├── chart.umd.min.js
        ├── chartjs-adapter-date-fns.bundle.min.js
        └── socket.io.min.js
```

---

## 5. 技術選型

| 項目 | 選擇 | 理由 |
|---|---|---|
| 後端框架 | **Flask 3 + Flask-SocketIO** | 輕量；WebSocket 即時推送；Python 生態 |
| 圖表 | **Chart.js** + `chartjs-adapter-date-fns` | 前端算圖、不吃伺服器資源；time scale、auto bounds 內建 |
| 即時通訊 | **Socket.IO** | 廣播給所有 client；多瀏覽器同步 |
| 資料庫 | **SQLite** + WAL 模式 | 零安裝；單檔；本機使用效能足夠 |
| 排程 | `threading.Thread` + `time.sleep(10)` | poller daemon thread |
| 計算降取樣 | **LTTB**（Largest-Triangle-Three-Buckets） | 保留視覺上重要的峰谷，比等距取樣好 |
| 設定持久層 | SQLite + `config/settings.json` + `sessionStorage` | 三層：全域 ↔ 跨重啟 ↔ 分頁 UI |
| Debug log | `logging.handlers.RotatingFileHandler` | 單檔 2MB × 5 個備份，自動輪替 |
| 前端設定暫存 | `sessionStorage`（每分頁獨立）| 同一瀏覽器不同分頁可有不同 UI 狀態 |
| 前端 UI | 原生 HTML / CSS / 少量 vanilla JS | 單頁/雙頁，無需框架 |
| 顏色選擇 | 256 色盤（216 web-safe + 40 灰階） | 題目指定 |
| 主題 | CSS 變數 + `data-theme` 屬性 | 動態切換不需重整 |

---

## 6. 資料模型（SQLite Schema）

### `samples` 表（持久化取樣資料）

```sql
CREATE TABLE samples (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT    NOT NULL,              -- ISO 8601 (秒精度)
    station TEXT    NOT NULL,              -- '工位1' ~ '工位6'
    t01 REAL, t02 REAL, ..., t20 REAL      -- 20 個溫度欄位；無效值 NULL
);
CREATE INDEX idx_samples_station_ts ON samples(station, ts);
```

**容量估算**：

| 週期 | 每工位筆數 | 6 工位總計 | 磁碟空間 |
|---|---|---|---|
| 1 小時 | 360 | 2,160 | ~200 KB |
| 1 天 | 8,640 | 51,840 | ~5 MB |
| 7 天 | 60,480 | 362,880 | ~35 MB |

### `settings` 表（key-value 設定）

```sql
CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT                          -- 字串，dict/list 用 JSON
);
```

**v3 設定 key 表**（v2 的 `history_minutes` 已移除）：

| key | 類型 | 預設 | 說明 |
|---|---|---|---|
| `gx20_host` | str | `<GX20_DEFAULT_HOST>` | GX20 IP |
| `gx20_port` | int | `34434` | GX20 port |
| `y_axis_min` | float | `-20` | Y 軸最小值 |
| `y_axis_max` | float | `100` | Y 軸最大值 |
| `rate_window_min` | int | `5` | 升降速率計算區間（主畫面下拉）|
| `avg_window_min` | int | `10` | 平均值計算區間（主畫面下拉）|
| `chart_x_minutes` | int | `0` | X 軸範圍（0=全部，>0=近 N 分鐘）|
| `retention_days` | int | `7` | DB 保留天數（超過自動刪除）|
| `max_points` | int | `2000` | 圖表最大顯示點數（超過 LTTB 降取樣）|
| `theme` | str | `light` | `light` 或 `dark` |
| `debug_log_enabled` | int | `0` | Debug 模式（1=開，0=關）|
| `ch_visibility` | JSON | 全 true | `{"工位1":[true,...], ...}` 6×20 |
| `ch_alias` | JSON | `["Ch01",...]` | `{"工位1":["","",...], ...}` 6×20 |
| `ch_color` | JSON | 20 色預設 | `{"工位1":["#1f77b4",...], ...}` 6×20 |

> 詳細設定流程請見 §8（`config/settings.json` 同步機制）。

---

## 7. 模組設計

### 7.1 `gx20_reader.py`

完全移植自桌面版 `GX20_PW3335.py` 的通訊部分，**協定一字不改**：

- TCP `socket.create_connection(host, port, timeout=3)`
- 指令 `FData,0,0001,1210\r\n`
- 31-char 固定格式解析（data_type / channel / unit / sign / scientific value）
- 999.9 視為無效，回傳 `None`
- 6 工位 × 20 接點的 `CHANNEL_NUMBER` 對應表

**對外主要介面**：

```python
gx = GX20(host="<GX20_DEFAULT_HOST>", port=34434)
data = gx.get_all_temperatures()
# → {"工位1": [t1, t2, ..., t20], ..., "工位6": [...]} 或 None（連線失敗）
```

### 7.2 `storage.py`

```python
storage.init_db(reset=False)         # 啟動時呼叫；reset=False 保留既有資料
storage.insert_sample(ts, station, temps[20])
storage.query_recent(station, since_minutes=60)  →  List[dict]
storage.query_latest(station)         →  dict | None
storage.purge_old_samples(retention_days)  →  int（刪除筆數）
storage.count_samples() / count_samples_by_station()
storage.clear_db()                    # 手動一鍵清除
storage.get_all_settings() / get_setting() / set_setting()
```

**WAL 模式**：`PRAGMA journal_mode=WAL` + `synchronous=NORMAL` → 並行讀取不阻塞 poller 寫入。

### 7.3 `lttb.py`（後端 LTTB 降取樣）

```python
from lttb import lttb_xy, downsample_rows

# 對 (x, y) 序列降取樣
xs, ys = lttb_xy(xs_in, ys_in, threshold=2000)

# 對 list of dict 降取樣（用 ts 為主軸切桶）
rows = downsample_rows(rows, ts_key="ts", point_keys=["t01",...], threshold=2000)
```

演算法概念：把 N 筆切成 `threshold` 桶，每桶挑「與上一選中點 + 下桶平均點構成最大三角形」的那一點。**視覺上能保留峰谷**。

### 7.4 `app.py`（Flask + SocketIO）

詳見 §12 路由表。

關鍵設計：

- **poller thread**：每 10 秒讀 GX20 → 寫 SQLite → 更新 ring buffer → emit `new_sample`
- **ring buffer**：每工位保留最近 720 筆（2hr），rate/avg 直接從記憶體算，不再 query_recent 全表
- **定期 purge**：poller 每 5 分鐘跑一次 `purge_old_samples(retention_days)`
- **LTTB on-the-fly**：`/api/history` 回應前若筆數 > `max_points` 自動降取樣
- **CSV 平均整合**：`/api/export_csv/<station>` 拉 raw rows，依分鐘 bucket 平均輸出
- **Debug logger**：RotatingFileHandler 寫到 `logs/app.log`；HTTP 請求、SocketIO 事件、poller 每輪結果都會入 log
- **設定同步**：`save_settings()` 同步 dump 到 `config/settings.json`；`main()` 啟動時若檔案存在則直接採用

### 7.5 `static/js/storage.js`（跨分頁設定層）

```
GX20State.init()     → 拉 server 設定 → 套 session 覆蓋 → 套主題
GX20State.update(k, v) → 寫 sessionStorage + 標 dirty
GX20State.save()      → POST 到 server + 清 dirty + 寫回 session 頂層 key
GX20State.setTheme(t) → 立即切換主題
```

**關鍵**：每個分頁有自己獨立的 `sessionStorage["gx20.tab_state.v1"]`，
與 SQLite / JSON 持久層分離。**切換分頁不會互相覆蓋未保存的變更**。

### 7.6 `static/js/lttb.js`（前端 LTTB）

`window.lttb(data, threshold)` 對 `{x, y}` 陣列降取樣。當前端 dataset 超過上限時保險用。

---

## 8. 設定同步檔 config/settings.json

### 三層架構

```
┌────────────────────────────────────────────┐
│ sessionStorage  (per tab, dirty until save) │
│     ↑ user changes                          │
│     │ "保存" 按鈕 → POST /api/settings      │
│     ↓                                        │
│ SQLite (gx20.db, 全部設定的事實來源)          │
│     ↑ save_settings()                       │
│     │ 同步 dump                              │
│     ↓                                        │
│ config/settings.json  (重啟時優先採用)        │
└────────────────────────────────────────────┘
```

### 啟動流程

```
main():
    1. storage.init_db(reset=False)
    2. 若 config/settings.json 存在:
         log.info("讀取設定檔 ... 直接套用")
         apply_json_to_sqlite(json)
       elif SQLite 為空:
         寫入預設 + dump 一次 settings.json
    3. 套用 debug 設定
    4. 啟動時 purge 過期資料
    5. 註冊關閉 hooks
    6. 啟動 poller thread
    7. socketio.run(...)
```

### 同步策略

- **寫入側**：`save_settings()` 寫完 SQLite 後，atomic write（`.tmp` + `os.replace`）dump 整包 merge 設定到 `config/settings.json`
- **讀取側**：`main()` 啟動時若檔案存在 → 直接讀取並寫回 SQLite（後續 `load_settings()` 仍以 SQLite 為主，但內容已被 JSON 覆蓋）
- **動底欄位**（`ch_visibility` / `ch_alias` / `ch_color`）：以 dict 完整覆寫

### 範例 `config/settings.json`

```json
{
  "avg_window_min": 10,
  "ch_alias": { "工位1": ["Ch01", "Ch02", ...], ... },
  "ch_color": { "工位1": ["#1f77b4", "#ff7f0e", ...], ... },
  "ch_visibility": { "工位1": [true, true, ...], ... },
  "chart_x_minutes": 0,
  "debug_log_enabled": 0,
  "gx20_host": "<GX20_DEFAULT_HOST>",
  "gx20_port": 34434,
  "max_points": 2000,
  "rate_window_min": 5,
  "retention_days": 7,
  "theme": "light",
  "y_axis_max": 100,
  "y_axis_min": -20
}
```

> `.gitignore` 已排除 `config/settings.json`（避免 commit 使用者實際設定）。
> 範例可參考 `config/settings.example.json`。

---

## 9. 資料生命週期與 DB 佈局

### DB 佈局（v5）

```
data/
├── gx20_<station>.db       # 6 工位各自一份 samples 表
├── gx20_settings.db        # 共用 settings 表
└── archive/
    └── gx20_<station>_<YYYYMMDD_HHMMSS>.db   # 清除前歸檔（每工位保留 5 份）
```

- **每工位獨立 samples DB** → 6 工位可非同步上下線，不互相污染
- **共用 settings DB** → GX20 連線 / 別名 / 顏色與資料分離
- **歸檔保留 5 份** → 超過自動刪最舊（可由 `storage.ARCHIVE_KEEP_PER_STATION` 調整）

### 啟動

```
1. _migrate_legacy_if_needed()        ← v5 自動：若偵測到舊 data/gx20.db，samples 按 station 切到 6 DB + settings 複製
2. config/settings.json 優先套用（v4）
3. 若 SQLite 為空 → 寫入預設值 + dump JSON
4. storage.purge_old_samples(7)       ← 逐工位刪超過 7 天的舊資料
5. 註冊 atexit / SIGINT handler（不再 clear_db）
6. 啟動 poller thread
7. socketio.run(...)
```

### 執行中

- 每 10 秒：poller 讀 GX20 → 寫「該工位」的 SQLite → 更新 ring buffer → emit `new_sample`
- 每 5 分鐘：逐工位 purge 過期資料
- DB 持續累積，**關閉程式也不會被清空**
- 使用者按「保存」→ 同步寫 SQLite + dump JSON

### 關閉

- atexit / SIGINT / SIGTERM 觸發時：只清空 ring buffer 與 GX20 連線
- **不再刪 DB**（v1 行為已拔除）
- 下次啟動可繼續累積

### 手動清除（v5）

主畫面或設定頁頂部 [清除此工位] 按鈕：

1. **第一段 confirm**：是否先歸檔到 `data/archive/`？
2. **第二段 confirm**：確認刪除？顯示此次會做的動作
3. 按下確認後：
   - `archive_station(station)` → 拷貝到 `gx20_<station>_<時間>.db` → 輪替保留 5 份
   - `clear_station_db(station)` → 刪除該工位 DB（含 WAL/SHM/JOURNAL）
   - 清空該工位 ring buffer
4. 其他工位資料完全不動
5. 設定（GX20 連線、別名、顏色）完全不動

> 設定頁可以切工位 tab，所以清除時作用於「當前選定的工位」；
> 主畫面清除時作用於「下拉選單選定的工位」。

---

## 10. 效能與降取樣策略

### 瓶頸與對策

| 瓶頸 | v1 問題 | v3 對策 |
|---|---|---|
| 7 天後 DB 巨大（35 MB）| 沒問題，但每次關閉都被清 | 改為持久 + 7 天自動 purge |
| 7 天後 `/api/history` 慢 | query_recent 全表 60k 筆 → 卡 | LTTB 降取樣到 2000 點（45ms）|
| poller 算 rate/avg 慢 | 每次 query_recent 全表 | ring buffer（720 筆記憶體運算）|
| 前端 chart 塞 60k 點 | 不可能（v1 沒這麼多資料）| 1) 後端先 LTTB 2) 前端再保險 LTTB |
| 瀏覽器記憶體爆 | 沒考慮過 | 20 接點 × 1 曲線 × 2000 點 ≈ 40k 物件 / 幾 MB |
| CSV 輸出太大 | 全部 10 秒/筆（7 天 60k 筆）| 依 X 軸範圍 + 平均整合為 1 分鐘/筆（最多 1k 筆）|
| X 軸範圍硬編碼 | 60 分鐘固定 | 下拉 15分 ~ 1天，0=全部 |
| 故障難排查 | print 在 terminal | 結構化 log 寫檔 + 動態等級 |

### 測試數據（7 天 60480 筆，單工位）

| 操作 | 耗時 |
|---|---|
| INSERT 35,210 筆/秒（batch executemany）| 1.7s / 60k 筆 |
| LTTB 60480 → 2000 | **45ms** |
| CSV 平均整合 60480 → 1440 桶 | **~30ms** |
| `query_recent` 7 天 | 1.07s（poller 不再跑這個）|
| `purge_old_samples` 7 天 | 0.35s |

### 降取樣演算法：LTTB

比「每 N 點取 1 點」更視覺友善，能保留峰谷：

- 第一點與最後一點**永遠保留**
- 中間切成 `threshold` 桶，每桶挑「與上一選中點 + 下桶平均點構成最大三角形」的那一點
- 1000 點 sin 波 + 3 個 spike 降到 100 點：spike **3 個全保留**

### 參數調整

- `max_points = 2000`：圖表總點數上限
  - 螢幕寬度 1080px → 1 點 ≈ 0.5px → 2000 點足夠塞滿且滑順
  - 調高（如 5000）會更精細但可能卡
  - 調低（如 500）會更流暢但丟失細節
- `retention_days = 7`：DB 保留天數
  - 7 天 / 6 工位 ≈ 35 MB
  - 空間夠可調 30，空間緊可調 1~3

---

## 11. 前端 UI 與互動

### 主頁 `index.html`

```
┌──────────────────────────────────────────────────────────────────┐
│ 站點: [工位1 ▼] [設定] [儲存 CSV] [...]  ●已連線  最後更新: 14:32 │
├────────────────────────────────────────┬─────────────────────────┤
│                                          │ 最新讀值                 │
│  Chart.js 圖表區                          │ X 軸:[全部 ▼] 速率:[5] 平均:[10]│
│  - 20 條溫度線（顏色自選）                │  #  名稱   讀值  速率  平均│
│  - 圖例隱藏（顯示/隱藏在設定頁）           │  1  Ch01   25.0 +0.10  24.9│
│  - Y 軸: 溫度 (auto scale, clamp)        │  2  Ch02   25.1 +0.05  25.0│
│  - X 軸: 動態錨點（分鐘滑動）            │  ...                     │
└────────────────────────────────────────┴─────────────────────────┘
```

- **站點下拉選單**：切換時清空 chart、重新拉 history
- **三個 select 同列**：X 軸 / 速率 / 平均，變更後下個 tick 生效
  - 速率：1/2/5/10/15/30/60 分鐘
  - 平均：1/2/5/10/15/30/60 分鐘 / 3 小時 / 6 小時
  - X 軸：全部 / 15分 / 30分 / 1時 / 3時 / 6時 / 12時 / 1天
- **表頭動態單位**：「速率 (°C/N 分鐘)」「平均 (°C/N 分鐘)」跟著設定變
- **右側表格**：4 欄（#、名稱/別名、讀值、速率、平均），依隱藏設定過濾
  - 名稱規則：別名優先，別名為空時顯示頻道號（如 `0005`）
  - 速率含正負號（`+0.123` / `-0.050`）

### 設定頁 `settings.html`

```
┌─────────────────────────────────────────┐
│ GX20 監視 — 設定        [回監看] [保存] [立即清除 SQLite] [...] │
├─────────────────────────────────────────┤
│ 1. GX20 連線                             │
│    Host: [<GX20_DEFAULT_HOST>]   Port: [34434]   │
│                                         │
│ 2. Y 軸範圍                              │
│    最小值: [-20]  最大值: [100]           │
│    ※ 歷史視窗/時間範圍請至主畫面右側...  │
│                                         │
│ 3.5 資料保留                             │
│    DB 保留天數: [7]                      │
│    圖表最大顯示點數: [2000]              │
│                                         │
│ 3.7 偵錯                                │
│    ☑ 啟用詳細 log（記錄至 logs/app.log）  │
│                                         │
│ 4. 接點設定                               │
│    [工位1] [工位2] ... [工位6]            │
│    ☑ 全部開啟 / 隱藏                    │
│    ┌──────┬──────┬──────┬──────┐         │
│    │ #1   │ #2   │ #3   │ #4   │         │
│    │ ☑顯示│ ☑顯示│ ☑顯示│ ☑顯示│         │
│    │ 別名:[____] 顏色:[🟦]                 │
│    └──────┴──────┴──────┴──────┘         │
└─────────────────────────────────────────┘
```

### 「保存」按鈕行為

- 設定頁頂部有 [保存] 按鈕（主畫面已無此按鈕）
- 任何欄位改動 → 按鈕變橘色，文字變 `保存 ●`（提示 dirty）
- 按下保存 → 寫 server + 清 dirty + 同步 sessionStorage 頂層 + dump JSON
- **未保存就離開頁面** → 變更只存在本分頁的 sessionStorage，下次進同分頁仍在；進新分頁則消失

---

## 12. 路由與 API

### 頁面

| 路徑 | 方法 | 用途 |
|---|---|---|
| `/` | GET | 監看主頁 |
| `/settings` | GET | 設定頁 |
| `/favicon.ico` | GET | 內建 ICO |

### API

| 路徑 | 方法 | 用途 |
|---|---|---|
| `/api/settings` | GET | 讀取全部設定 |
| `/api/settings` | POST | 寫入設定（merge-write + dump JSON）|
| `/api/channels` | GET | 6 工位 × 20 接點的 4 碼頻道號 |
| `/api/history/<station>` | GET | 拉歷史；支援 `?max_points=N` LTTB 降取樣、`?since_minutes=N` |
| `/api/latest/<station>` | GET | 該站最新一筆 |
| `/api/connection` | GET | 連線狀態、host/port |
| `/api/db_stats` | GET | DB 統計（每工位筆數、時間範圍、retention、歸檔保留份數）|
| `/api/clear` | POST | **v5**：清除指定工位；body `{"station":"工位5", "archive":true}` |
| `/api/archives` | GET | 查歸檔清單（`?station=工位5` 過濾）|
| `/api/export_csv/<station>` | GET | 匯出 CSV；依 X 軸範圍 + 平均整合為 1 分鐘/筆 |
| `/api/export_csv/<station>` | GET | 匯出 CSV；依 X 軸範圍 + 平均整合為 1 分鐘/筆 |
| `/api/debug` | GET | 讀取 debug 狀態 |
| `/api/debug` | POST | 切換 debug 狀態（`{"enabled": true/false}`）|
| `/api/debug/log_tail` | GET | 查 `logs/app.log` 末段（`?lines=N`）|

### SocketIO 事件

| 事件 | 方向 | payload |
|---|---|---|
| `new_sample` | S → C | `{ts, station, temps:[20], rate:[20], avg:[20]}` |

每 10 秒廣播一次給所有連線的瀏覽器分頁。

---

## 13. 設定頁欄位

| 區塊 | 欄位 | 預設 | 範圍 | 說明 |
|---|---|---|---|---|
| 1. GX20 連線 | Host | `<GX20_DEFAULT_HOST>` | — | GX20 IP |
| | Port | `34434` | — | TCP port |
| 2. Y 軸範圍 | 最小值 (°C) | `-20` | 任意 | Y 軸下限（auto scale 不會低於此）|
| | 最大值 (°C) | `100` | 任意 | Y 軸上限（auto scale 不會高於此）|
| 3.5 資料保留 | DB 保留天數 | `7` | 1~30 | 超過天數自動刪除（啟動時 + 每 5 分鐘 purge）|
| | 圖表最大顯示點數 | `2000` | 200~10000 | dataset 超過此值會 LTTB 降取樣 |
| 3.7 偵錯 | Debug log | `false` | bool | 啟用後 log 寫到 `logs/app.log`（DEBUG 等級）|
| 4. 接點設定 | 顯示 / 隱藏 | 全顯示 | bool | 隱藏的接點不畫線、不入表格（資料仍存）|
| | 別名 | `Ch01`~`Ch20` | str | 右側表格與圖例優先顯示別名 |
| | 顏色 | 20 色預設 | hex | 圖表曲線顏色 + 表格 swatch |

> **v3 移除的欄位**（已由主畫面下拉取代）：
> - 「2. Y 軸範圍」→「歷史視窗 (分鐘)」
> - 「3. 計算時間長度」整段（升降速率 / 平均 input）
>
> **主畫面下拉的對應設定**（會即時寫回 SQLite + dump JSON）：
> - `chart_x_minutes` (X 軸)
> - `rate_window_min` (速率區間)
> - `avg_window_min` (平均區間)

---

## 14. 主題系統（light / dark）

### 設計

- CSS 變數系統：所有顏色集中在 `:root` 與 `body[data-theme="..."]` 區塊
- `body[data-theme="light"]` / `body[data-theme="dark"]` 兩套配色
- 切換主題**不需重整**，即時生效
- 圖表軸、格線、圖例文字都從 CSS 變數讀取；`MutationObserver` 監聽主題變化自動 rebuild chart

### 顏色

| 變數 | light | dark |
|---|---|---|
| `--bg`（頁面底色）| `#f5f5dc`（題目指定預設）| `#1d2e17` |
| `--surface`（卡片/表格）| `#ffffff` | `#2c4521` |
| `--surface-2`（輸入框/次要）| `#ecead0` | `#395a2b` |
| `--text` | `#2a2a1a` | `#eaf2e0` |
| `--text-dim` | `#5a5a4a` | `#b8cfce` |
| `--text-mute` | `#8a8a72` | `#8a9a7a` |
| `--primary` | `#2e6b3e` | `#4c6d3b` |
| `--danger` | `#b33a3a` | `#843c39` |
| `--accent` | `#d4a017`（未保存指示）| `#ffcc44` |

### 主題切換行為

- 設定在 `sessionStorage["gx20.tab_state.v1"].theme`（每分頁獨立）
- 按下保存才寫入 server；下次重整會記得
- 預設偵測系統 `prefers-color-scheme`

---

## 15. 執行方式

### 安裝

```bash
cd "<your-project-dir>"
pip install -r requirements.txt
```

> 執行設備（題目指定非原儲存位置）可以放在本機磁碟任意位置，**不必放 OneDrive**。
> SQLite WAL 模式對單機存取效能最佳。

### 啟動

```bash
python run.py
```

- 預設綁 `0.0.0.0:5000`
- 開瀏覽器：`http://localhost:5000/`（監看） / `http://localhost:5000/settings`（設定）
- 首次啟動會建立 `config/settings.json` 與 `data/gx20.db`
- 之後重啟若 `config/settings.json` 存在，**自動套用**（不必按保存）

### 關閉

- `Ctrl+C` 終止
- 關閉後 DB **保留**（下次啟動可繼續累積）
- 想清空：到設定頁按「立即清除 SQLite」

### 重啟後行為

- 之前保存的設定（接點、別名、顏色、主題、Host/Port、X 軸、速率/平均…）都還在
- DB 資料保留（最多 7 天，過期自動清）
- poller 重新開始累積新資料
- Debug log 模式沿用上次保存的狀態

### 部署在另一台電腦

1. 整個資料夾複製過去（**不要複製 `config/settings.json`**，讓新機器自己產生）
2. 安裝 Python 3.10+ 與相依
3. 啟動
4. 瀏覽器開 `http://<該機IP>:5000/`

---

## 16. 故障排除

| 症狀 | 可能原因 | 解法 |
|---|---|---|
| 連線狀態一直紅 | GX20 沒開、IP/Port 錯、網路不通 | 設定頁改 Host/Port，按保存 |
| 圖表一直空白 | 還沒累積到一筆資料 | 等 10 秒（首次取樣）|
| 圖表曲線斷斷續續 | 該接點資料常是無效值（999.9）| 檢查 GX20 該通道接線 |
| 切換主題後文字看不到 | 罕見；CSS 變數未生效 | `Ctrl+Shift+R` 強制重整 |
| console 出現 SyntaxError | 瀏覽器 cache 舊 JS | `Ctrl+Shift+R` 或開 DevTools → Network → Disable cache |
| 設定保存後進設定頁又還原 | 極罕見；sessionStorage 被清 | 確認瀏覽器未開「關閉時清除資料」|
| DB 異常大 | `retention_days` 設太大 | 調小，或手動按「清除此工位」（v5）|
| 找不到問題原因 | log 看不到細節 | 設定頁 → 3.7 偵錯 → 開啟 Debug log，查 `logs/app.log` |
| CSV 匯出空白 | 該工位 / 該 X 軸範圍內無資料 | 確認 DB 內有此工位資料 |
| 設定值重啟後不見 | `config/settings.json` 被誤刪 | 從備份還原或到設定頁重新保存 |
| 按「清除此工位」資料不見了怎麼辦 | v5 會先歸檔 | 到 `data/archive/` 找 `gx20_<station>_<時間>.db` 手動複製回去重命名 |
| 清資料後清錯工位 | 歸檔不見得夠 | 從 `data/archive/gx20_pre_migration_<時間>.db` 查舊資料 |

### log 位置

- 預設 INFO 等級 → 終端 + `logs/app.log`
- 開啟 Debug log → 設定頁或 `POST /api/debug {"enabled": true}`
- 查 log 末段：`GET /api/debug/log_tail?lines=100`

---

## 17. 已知限制

- **多進程不安全**：SQLite 不支援多 app 實例同時寫。只能部署 1 份
- **大量歷史查詢**：雖然有 LTTB，但若 6 工位 × 7 天 × 2000 點同時繪製仍需幾秒
- **速率計算**：採「首末兩點差 / 時間差」，資料稀疏時不準
- **平均計算**：採算術平均，異常值會拉偏
- **256 色盤**：web-safe 近似，未做色弱優化
- **GX20 單一連線**：本版只支援 1 台 GX20（多台需改 protocol）
- **即時通訊**：SocketIO broadcast 給所有 client，多瀏覽器開會重複接收（但前端只繪當前站，其他忽略）
- **CSV 平均**：採算術平均；若該分鐘內有突波，會被稀釋掉（建議搭配原始 DB 查詢做比對）

---

## 附錄 A：GX20 通訊協定摘要

| 項目 | 值 |
|---|---|
| 連線 | TCP |
| 預設 IP / Port | `<GX20_DEFAULT_HOST>:34434` |
| 指令 | `FData,0,0001,1210\r\n` |
| 頻道範圍 | 0001 ~ 1210（6 工位 × 20 接點）|
| 回應格式 | 每行 31 char |
| 解析欄位 | `[0]` 狀態 / `[2:6]` 頻道 / `[10:18]` 單位 / `[18]` 正負號 / `[19:31]` 科學符號值 |
| 無效值 | `999.9` 視為無效 |

詳見 `gx20_reader.py`。

## 附錄 B：LTTB 參考

論文：Sveinn Steinarsson, "Downsampling Time Series for Visual Representation" (2013)
https://skemman.is/handle/1946/15343

實作：見 `lttb.py`（後端）與 `static/js/lttb.js`（前端），兩者演算法一致。
