# GX20 Web Monitor — 溫度監視網頁版

> YOKOGAWA GX20 紙記錄器的網頁版溫度監視系統
>
> 6 工位 × 20 接點 = 120 點，每 10 秒取樣一次，**可持續記錄 7 天以上**
>
> v2.0 改版重點：資料持久化、LTTB 降取樣、明暗主題、ring buffer 計算

---

## 目錄

1. [專案概述](#1-專案概述)
2. [與桌面版差異](#2-與桌面版差異)
3. [系統架構](#3-系統架構)
4. [技術選型](#4-技術選型)
5. [資料模型](#5-資料模型-sqlite-schema)
6. [模組設計](#6-模組設計)
7. [資料生命週期（v2 改版重點）](#7-資料生命週期v2-改版重點)
8. [效能與降取樣策略](#8-效能與降取樣策略)
9. [前端 UI 與互動](#9-前端-ui-與互動)
10. [路由與 API](#10-路由與-api)
11. [設定頁欄位](#11-設定頁欄位)
12. [主題系統（light / dark）](#12-主題系統light--dark)
13. [執行方式](#13-執行方式)
14. [故障排除](#14-故障排除)
15. [已知限制](#15-已知限制)

---

## 1. 專案概述

### 功能

- **6 個工位 × 20 個溫度接點**（共 120 點）即時溫度監看
- 每 10 秒取樣一次，**資料以 SQLite 持久保存**（預設保留 7 天）
- 即時趨勢圖：溫度分布（實線）、升降速率（虛線，右軸 °C/min）、平均值（點線）
- 網頁式設定：站點選擇、接點顯示/隱藏、別名、256 色盤選色、Y 軸範圍、計算時間長度
- 明亮 / 暗黑主題切換
- 多瀏覽器分頁透過 SocketIO 自動同步
- 「每分頁 session 暫存」+「全域 SQLite 持久保存」雙層架構

### 不在範圍

- **PW3335 電力計**（題目只要 GX20，桌面版的電力功能不移植）
- 多台 GX20 同時連線（本版只支援一台，Host/Port 已在 settings 留欄位可調）

### 適用場景

- 工廠/實驗室 GX20 溫度即時監看
- 7 天以上趨勢分析（接 GX20 後即可累積資料）
- 多人多裝置同時監看（透過瀏覽器）

---

## 2. 與桌面版差異

| 功能 | 桌面版 `GX20_PW3335.py` | 本網頁版 v2 |
|---|---|---|
| GUI 框架 | Tkinter | HTML / CSS / JS |
| 圖表 | matplotlib（後端算圖） | Chart.js（前端算圖） |
| 資料儲存 | CSV 檔 | SQLite（持久） |
| 資料生命週期 | 永久累積在 CSV | SQLite 預設保留 7 天 |
| 關閉清除 | 不會 | **不會**（改用手動按鈕） |
| 取樣頻率 | 可調 10/60/180/300 秒 | 固定 10 秒 |
| 工位切換 | Notebook 6 個 tab | 下拉式選單（單頁） |
| 接點顏色 | 預設 | 256 色盤自選 |
| PW3335 電力 | 支援 | **不支援**（題目只要 GX20） |
| 多瀏覽器同步 | 無 | 透過 SocketIO 自動同步 |
| 主題 | 兩套（Ocean Deep / Serene Greens） | light / dark（CSS 變數）|
| 計算效能 | query_recent 全表 | ring buffer（720 筆記憶體）|
| 大資料繪圖 | matplotlib 自動處理 | LTTB 自動降取樣到 2000 點 |
| 跨 session 設定 | 不適用 | SQLite 持久 + sessionStorage 分頁暫存 |

---

## 3. 系統架構

```
gx20-web-monitor/
├── README.md                  本文件
├── requirements.txt           flask + flask-socketio
├── run.py                     啟動入口（python run.py）
├── gx20_reader.py             GX20 TCP 通訊（移植自桌面版）
├── storage.py                 SQLite 層（schema / insert / query / purge）
├── config.py                  預設值集中管理
├── lttb.py                    LTTB 降取樣（後端版）
├── app.py                     Flask + Flask-SocketIO 主程式
├── data/
│   └── gx20.db                SQLite 檔（執行時建立，持久保存）
├── templates/
│   ├── index.html             監看主頁
│   └── settings.html          設定頁
└── static/
    ├── css/
    │   └── style.css          CSS 變數主題系統
    ├── js/
    │   ├── storage.js         跨分頁 sessionStorage 設定層
    │   ├── main.js            主頁邏輯
    │   ├── settings.js        設定頁邏輯
    │   ├── colorpicker.js     256 色盤
    │   └── lttb.js            LTTB 降取樣（前端版）
    └── vendor/
        ├── chart.umd.min.js
        ├── chartjs-adapter-date-fns.bundle.min.js
        └── socket.io.min.js
```

---

## 4. 技術選型

| 項目 | 選擇 | 理由 |
|---|---|---|
| 後端框架 | **Flask 3 + Flask-SocketIO** | 輕量；WebSocket 即時推送；Python 生態 |
| 圖表 | **Chart.js** + `chartjs-adapter-date-fns` | 前端算圖、不吃伺服器資源；time scale、auto bounds 內建 |
| 即時通訊 | **Socket.IO** | 廣播給所有 client；多瀏覽器同步 |
| 資料庫 | **SQLite** + WAL 模式 | 零安裝；單檔；本機使用效能足夠 |
| 排程 | `threading.Thread` + `time.sleep(10)` | poller daemon thread |
| 計算降取樣 | **LTTB**（Largest-Triangle-Three-Buckets） | 保留視覺上重要的峰谷，比等距取樣好 |
| 前端設定暫存 | `sessionStorage`（每分頁獨立） | 同一瀏覽器不同分頁可有不同 UI 狀態 |
| 前端 UI | 原生 HTML / CSS / 少量 vanilla JS | 單頁/雙頁，無需框架 |
| 顏色選擇 | 256 色盤（216 web-safe + 40 灰階） | 題目指定 |
| 主題 | CSS 變數 + `data-theme` 屬性 | 動態切換不需重整 |

---

## 5. 資料模型（SQLite Schema）

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

預設 / 可能的 key：

| key | 類型 | 預設 | 說明 |
|---|---|---|---|
| `gx20_host` | str | `192.168.1.1` | GX20 IP |
| `gx20_port` | int | `34434` | GX20 port |
| `y_axis_min` | float | `-20` | Y 軸最小值 |
| `y_axis_max` | float | `100` | Y 軸最大值 |
| `history_minutes` | int | `60` | 前端首次載入拉多少分鐘 |
| `rate_window_min` | int | `5` | 升降速率計算區間 |
| `avg_window_min` | int | `10` | 平均值計算區間 |
| `retention_days` | int | `7` | DB 保留天數（超過自動刪除）|
| `max_points` | int | `2000` | 圖表最大顯示點數（超過 LTTB 降取樣）|
| `theme` | str | `light` | `light` 或 `dark` |
| `ch_visibility` | JSON | 全 true | `{"工位1":[true,...], ...}` 6×20 |
| `ch_alias` | JSON | `["Ch01",...]` | `{"工位1":["","",...], ...}` 6×20 |
| `ch_color` | JSON | 20 色預設 | `{"工位1":["#1f77b4",...], ...}` 6×20 |

---

## 6. 模組設計

### 6.1 `gx20_reader.py`

完全移植自桌面版 `GX20_PW3335.py` 的通訊部分，**協定一字不改**：

- TCP `socket.create_connection(host, port, timeout=3)`
- 指令 `FData,0,0001,1210\r\n`
- 31-char 固定格式解析（data_type / channel / unit / sign / scientific value）
- 999.9 視為無效，回傳 `None`
- 6 工位 × 20 接點的 `CHANNEL_NUMBER` 對應表

**對外主要介面**：

```python
gx = GX20(host="192.168.1.1", port=34434)
data = gx.get_all_temperatures()
# → {"工位1": [t1, t2, ..., t20], ..., "工位6": [...]} 或 None（連線失敗）
```

### 6.2 `storage.py`

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

### 6.3 `lttb.py`（後端 LTTB 降取樣）

```python
from lttb import lttb_xy, downsample_rows

# 對 (x, y) 序列降取樣
xs, ys = lttb_xy(xs_in, ys_in, threshold=2000)

# 對 list of dict 降取樣（用 ts 為主軸切桶）
rows = downsample_rows(rows, ts_key="ts", point_keys=["t01",...], threshold=2000)
```

演算法概念：把 N 筆切成 `threshold` 桶，每桶挑「與上一選中點 + 下桶平均點構成最大三角形」的那一點。**視覺上能保留峰谷**。

### 6.4 `app.py`（Flask + SocketIO）

詳見 §10 路由表。

關鍵設計：

- **poller thread**：每 10 秒讀 GX20 → 寫 SQLite → 更新 ring buffer → emit `new_sample`
- **ring buffer**：每工位保留最近 720 筆（2hr），rate/avg 直接從記憶體算，不再 query_recent 全表
- **定期 purge**：poller 每 5 分鐘跑一次 `purge_old_samples(retention_days)`
- **LTTB on-the-fly**：`/api/history` 回應前若筆數 > `max_points` 自動降取樣

### 6.5 `static/js/storage.js`（跨分頁設定層）

```
GX20State.init()     → 拉 server 設定 → 套 session 覆蓋 → 套主題
GX20State.update(k, v) → 寫 sessionStorage + 標 dirty
GX20State.save()      → POST 到 server + 清 dirty + 寫回 session 頂層 key
GX20State.setTheme(t) → 立即切換主題
```

**關鍵**：每個分頁有自己獨立的 `sessionStorage["gx20.tab_state.v1"]`，
與 SQLite 持久層分離。**切換分頁不會互相覆蓋未保存的變更**。

### 6.6 `static/js/lttb.js`（前端 LTTB）

`window.lttb(data, threshold)` 對 `{x, y}` 陣列降取樣。當前端 dataset 超過上限時保險用。

---

## 7. 資料生命週期（v2 改版重點）

### 啟動

```
1. storage.init_db(reset=False)         ← 補 schema，不刪資料
2. 若 settings 表為空 → 寫入預設值
3. storage.purge_old_samples(7)         ← 一次性清掉超過 7 天的舊資料
4. 註冊 atexit / SIGINT handler（不再 clear_db）
5. 啟動 poller thread
6. socketio.run(...)
```

### 執行中

- 每 10 秒：poller 讀 GX20 → 寫 SQLite → 更新 ring buffer → emit
- 每 5 分鐘：purge 過期資料
- DB 持續累積，**關閉程式也不會被清空**

### 關閉

- atexit / SIGINT / SIGTERM 觸發時：只清空 ring buffer 與 GX20 連線
- **不再刪 DB**（v1 行為已拔除）
- 下次啟動可繼續累積

### 手動清除

設定頁「立即清除 SQLite」按鈕（POST /api/clear）→ `clear_db()` 刪檔 + `init_db(reset=False)` 重建空 schema + 清空 ring buffer。

---

## 8. 效能與降取樣策略

### 瓶頸與對策

| 瓶頸 | v1 問題 | v2 對策 |
|---|---|---|
| 7 天後 DB 巨大（35 MB） | 沒問題，但每次關閉都被清 | 改為持久 + 7 天自動 purge |
| 7 天後 `/api/history` 慢 | query_recent 全表 60k 筆 → 卡 | LTTB 降取樣到 2000 點（45ms）|
| poller 算 rate/avg 慢 | 每次 query_recent 全表 | ring buffer（720 筆記憶體運算）|
| 前端 chart 塞 60k 點 | 不可能（v1 沒這麼多資料）| 1) 後端先 LTTB 2) 前端再保險 LTTB |
| 瀏覽器記憶體爆 | 沒考慮過 | 20 接點 × 3 曲線 × 2000 點 ≈ 120k 物件 / 幾 MB |

### 測試數據（7 天 60480 筆，單工位）

| 操作 | 耗時 |
|---|---|
| INSERT 35,210 筆/秒（batch executemany）| 1.7s / 60k 筆 |
| LTTB 60480 → 2000 | **45ms** |
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

## 9. 前端 UI 與互動

### 主頁 `index.html`

```
┌────────────────────────────────────────────────────────────┐
│ 站點: [工位1 ▼] [設定] [保存] [清除資料] [☀/🌙]  ●已連線  最後更新: 14:32:10 │
├────────────────────────────────────────────────┬───────────┤
│  [工位1]                                          │ 最新讀值  │
│  Chart.js 圖表區                                  │  # 名稱   │
│  - 溫度分布 (實線, 左軸 °C)                        │  讀值     │
│  - 升降速率 (虛線, 右軸 °C/min)                     │  速率     │
│  - 平均值  (點線, 左軸)                             │  平均     │
│                                                  │           │
│  Y 軸: 溫度 (auto scale, clamp 在設定範圍)         │           │
└────────────────────────────────────────────────┴───────────┘
```

- **站點下拉選單**：切換時清空 chart、重新拉 history
- **圖例**：點擊切換曲線顯示（Chart.js 內建）
- **速率 / 平均曲線**：預設隱藏，點圖例開啟
- **右側表格**：4 欄（#、名稱/別名、讀值、速率、平均），依隱藏設定過濾
  - 名稱規則：別名優先，別名為空時顯示頻道號（如 `0005`）
  - 速率含正負號（`+0.123` / `-0.050`）

### 設定頁 `settings.html`

```
┌─────────────────────────────────────────┐
│ GX20 監視 — 設定                  [回監看] [保存] [...]  │
├─────────────────────────────────────────┤
│ 1. GX20 連線                             │
│    Host: [192.168.1.1]   Port: [34434]   │
│                                         │
│ 2. Y 軸範圍                              │
│    最小值: [-20]  最大值: [100]           │
│    歷史視窗 (分鐘): [60]                 │
│                                         │
│ 3. 計算時間長度                          │
│    升降速率 (分鐘): [5]                  │
│    平均值 (分鐘): [10]                   │
│                                         │
│ 3.5 資料保留                             │
│    DB 保留天數: [7]                      │
│    圖表最大顯示點數: [2000]              │
│                                         │
│ 4. 接點設定                               │
│    [工位1] [工位2] [工位3] [工位4] [工位5] [工位6] │
│    ☑ 全部開啟 / 隱藏                    │
│    ┌──────┬──────┬──────┬──────┬──────┐│
│    │ #1   │ #2   │ #3   │ #4   │ #5   ││
│    │ 0001 │ 0002 │ 0003 │ 0004 │ 0005 ││
│    │ ☑顯示│ ☑顯示│ ☑顯示│ ☑顯示│ ☑顯示││
│    │ 別名:[____] 別名:[____] ...      ││
│    │ 顏色:[🟦]  顏色:[🟦]  ...        ││
│    ├──────┼──────┼──────┼──────┼──────┤│
│    │ ...20 個接點 ...                   ││
│    └──────┴──────┴──────┴──────┴──────┘│
└─────────────────────────────────────────┘
```

### 「保存」按鈕行為

- 兩頁頂部都有「保存」按鈕
- 任何欄位改動 → 按鈕變橘色，文字變 `保存 ●`（提示 dirty）
- 按下保存 → 寫 server + 清 dirty + 同步 sessionStorage 頂層
- **未保存就離開頁面** → 變更只存在本分頁的 sessionStorage，下次進同分頁仍在；進新分頁則消失

---

## 10. 路由與 API

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
| `/api/settings` | POST | 寫入設定（merge-write）|
| `/api/channels` | GET | 6 工位 × 20 接點的 4 碼頻道號 |
| `/api/history/<station>` | GET | 拉歷史；支援 `?max_points=N` LTTB 降取樣 |
| `/api/latest/<station>` | GET | 該站最新一筆 |
| `/api/connection` | GET | 連線狀態、host/port |
| `/api/db_stats` | GET | DB 統計（每工位筆數、總筆數、retention）|
| `/api/clear` | POST | 手動一鍵清除 SQLite |

### SocketIO 事件

| 事件 | 方向 | payload |
|---|---|---|
| `new_sample` | S → C | `{ts, station, temps:[20], rate:[20], avg:[20]}` |

每 10 秒廣播一次給所有連線的瀏覽器分頁。

---

## 11. 設定頁欄位

| 區塊 | 欄位 | 預設 | 範圍 | 說明 |
|---|---|---|---|---|
| 1. GX20 連線 | Host | `192.168.1.1` | — | GX20 IP |
| | Port | `34434` | — | TCP port |
| 2. Y 軸範圍 | 最小值 (°C) | `-20` | 任意 | Y 軸下限（auto scale 不會低於此）|
| | 最大值 (°C) | `100` | 任意 | Y 軸上限（auto scale 不會高於此）|
| | 歷史視窗 (分鐘) | `60` | 1~10080 | 前端首次載入拉多少分鐘 |
| 3. 計算時間長度 | 升降速率 (分鐘) | `5` | 1~60 | 計算升降速率的時間區間 |
| | 平均值 (分鐘) | `10` | 1~60 | 計算平均值的時間區間 |
| 3.5 資料保留 | DB 保留天數 | `7` | 1~30 | 超過天數自動刪除（啟動時 + 每 5 分鐘 purge）|
| | 圖表最大顯示點數 | `2000` | 200~10000 | dataset 超過此值會 LTTB 降取樣 |
| 4. 接點設定 | 顯示 / 隱藏 | 全顯示 | bool | 隱藏的接點不畫線、不入表格（資料仍存）|
| | 別名 | `Ch01`~`Ch20` | str | 右側表格與圖例優先顯示別名 |
| | 顏色 | 20 色預設 | hex | 圖表曲線顏色 + 表格 swatch |

---

## 12. 主題系統（light / dark）

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

## 13. 執行方式

### 安裝

```bash
cd "D:\gx20-web-monitor"      # 或你的部署路徑
pip install -r requirements.txt
```

> **執行設備**（題目指定非原儲存位置）可以放在本機磁碟任意位置，**不必放 OneDrive**。
> SQLite WAL 模式對單機存取效能最佳。

### 啟動

```bash
python run.py
```

- 預設綁 `0.0.0.0:5000`
- 開瀏覽器：`http://localhost:5000/`（監看） / `http://localhost:5000/settings`（設定）

### 關閉

- `Ctrl+C` 終止
- 關閉後 DB **保留**（下次啟動可繼續累積）
- 想清空：到設定頁按「立即清除 SQLite」

### 重啟後行為

- 之前保存的設定（接點、別名、顏色、主題、Host/Port…）都還在
- DB 資料保留（最多 7 天，過期自動清）
- poller 重新開始累積新資料

### 部署在另一台電腦

1. 整個資料夾複製過去
2. 安裝 Python 3.10+ 與相依
3. 啟動
4. 瀏覽器開 `http://<該機IP>:5000/`

---

## 14. 故障排除

| 症狀 | 可能原因 | 解法 |
|---|---|---|
| 連線狀態一直紅 | GX20 沒開、IP/Port 錯、網路不通 | 設定頁改 Host/Port，按保存 |
| 圖表一直空白 | 還沒累積到一筆資料 | 等 10 秒（首次取樣）|
| 圖表曲線斷斷續續 | 該接點資料常是無效值（999.9）| 檢查 GX20 該通道接線 |
| 切換主題後文字看不到 | 罕見；CSS 變數未生效 | `Ctrl+Shift+R` 強制重整 |
| console 出現 SyntaxError | 瀏覽器 cache 舊 JS | `Ctrl+Shift+R` 或開 DevTools → Network → Disable cache |
| 設定保存後進設定頁又還原 | 極罕見；sessionStorage 被清 | 確認瀏覽器未開「關閉時清除資料」|
| DB 異常大 | `retention_days` 設太大 | 調小，或手動按「立即清除 SQLite」|

### log 位置

啟動時所有 log 印在 terminal（INFO 級別）。要看 poller 細節：

```python
# app.py 開頭
logging.basicConfig(level=logging.DEBUG)   # 改 DEBUG 看更詳細
```

---

## 15. 已知限制

- **多進程不安全**：SQLite 不支援多 app 實例同時寫。只能部署 1 份
- **大量歷史查詢**：雖然有 LTTB，但若 6 工位 × 7 天 × 2000 點同時繪製仍需幾秒
- **速率計算**：採「首末兩點差 / 時間差」，資料稀疏時不準
- **平均計算**：採算術平均，異常值會拉偏
- **256 色盤**：web-safe 近似，未做色弱優化
- **GX20 單一連線**：本版只支援 1 台 GX20（多台需改 protocol）
- **即時通訊**：SocketIO broadcast 給所有 client，多瀏覽器開會重複接收（但前端只繪當前站，其他忽略）

---

## 附錄 A：GX20 通訊協定摘要

| 項目 | 值 |
|---|---|
| 連線 | TCP |
| 預設 IP / Port | `192.168.1.1:34434` |
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
