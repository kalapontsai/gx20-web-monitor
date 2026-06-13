# GX20 Web Monitor — 架構與實作手冊

> 給**接手修改這個專案**的 agent / 工程師看的文件。
> 使用者操作說明見 [README.md](README.md)；歷史進度見 [CHANGELOG.md](CHANGELOG.md)。

---

## 目錄

1. [專案速覽](#1-專案速覽)
2. [目錄結構](#2-目錄結構)
3. [技術選型](#3-技術選型)
4. [資料模型](#4-資料模型)
5. [模組設計](#5-模組設計)
6. [資料生命週期與 DB 佈局](#6-資料生命週期與-db-佈局)
7. [效能與降取樣策略](#7-效能與降取樣策略)
8. [前端 UI 與互動](#8-前端-ui-與互動)
9. [路由與 API](#9-路由與-api)
10. [設定同步機制（config/settings.json）](#10-設定同步機制-configsettingsjson)
11. [主題系統（light / dark）](#11-主題系統light--dark)
12. [OTA 部署通道](#12-ota-部署通道)
13. [GX20 通訊協定摘要](#13-gx20-通訊協定摘要)
14. [LTTB 降取樣演算法](#14-lttb-降取樣演算法)

---

## 1. 專案速覽

- **目標**：YOKOGAWA GX20 紙記錄器的網頁版溫度監看
- **規模**：6 工位 × 20 接點 = 120 點，每 10 秒取樣
- **資料保存**：SQLite（v5 起：6 工位獨立 DB + 共用 settings DB）
- **即時通訊**：Flask-SocketIO broadcast `new_sample`
- **圖表**：Chart.js（前端算圖，LTTB 降取樣保護）
- **部署通道**：OTA（HTTP + Token，見 §12）

**改版脈絡**（高階）：

| 版 | 重點 |
|---|---|
| v2 | 資料持久化、LTTB、ring buffer、light/dark 主題 |
| v3 | debug logger、圖表精簡、X 軸動態、CSV 平均整合、settings.json 同步 |
| v4 | 最新讀值下拉化、CSV 平均整合、6 工位 hotfix、OTA 通道上線 |
| v5 | 6 工位獨立 DB + 清除前歸檔 + 共用 settings DB |
| v6.1 | 進階計算：游標模式（拖曳 x-bar 算區間統計） |

詳細每版 commit 與 bug fix 見 [CHANGELOG.md](CHANGELOG.md)。

---

## 2. 目錄結構

```
gx20-web-monitor/
├── README.md                   使用者導向說明（安裝、操作、故障排除）
├── ARCHITECTURE.md             本檔（給接手 agent 的架構文件）
├── CHANGELOG.md                版本演進與現況進度
├── requirements.txt            flask + flask-socketio
├── run.py                      啟動入口
├── gx20_reader.py              GX20 TCP 通訊（移植自桌面版）
├── storage.py                  SQLite 層（v5：6 工位獨立 DB + settings DB + 歸檔）
├── config.py                   預設值集中管理
├── lttb.py                     LTTB 降取樣（後端版）
├── app.py                      Flask + Flask-SocketIO 主程式
├── data/                       SQLite 檔（v5 佈局）
│   ├── gx20_<station>.db       每工位一份 samples 表
│   ├── gx20_settings.db        6 工位共用的 settings 表
│   └── archive/                清除前歸檔（每工位保留 5 份）
│       └── gx20_<station>_<時間>.db
├── config/                     設定同步檔
│   ├── settings.json           啟動時若存在 → 自動套用
│   ├── settings.example.json   範例
│   ├── ota_token               32 byte 隨機 token（不入版控）
│   └── ota_backup/<timestamp>/ OTA 寫入前備份
├── logs/
│   └── app.log                 RotatingFileHandler（2MB × 5 個備份）
├── docs/                       設計文件（IMPROVEMENTS / DEPLOY_OTA / CURSOR_MODE）
├── templates/
│   ├── index.html              監看主頁
│   └── settings.html           設定頁
└── static/
    ├── css/style.css           CSS 變數主題系統
    ├── js/
    │   ├── storage.js          跨分頁 sessionStorage 設定層
    │   ├── main.js             主頁邏輯
    │   ├── settings.js         設定頁邏輯
    │   ├── colorpicker.js      256 色盤
    │   └── lttb.js             LTTB 降取樣（前端版）
    └── vendor/                 chart.umd / socket.io / chartjs-adapter-date-fns
```

---

## 3. 技術選型

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

## 4. 資料模型

### 4.1 `samples` 表（每工位一份 DB）

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

### 4.2 `settings` 表（key-value，6 工位共用一份 DB）

```sql
CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT                          -- 字串，dict/list 用 JSON
);
```

**設定 key 完整表**：

| key | 類型 | 預設 | 說明 |
|---|---|---|---|
| `gx20_host` | str | `<GX20_DEFAULT_HOST>` | GX20 IP（見 `config.py`） |
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

> `ch_visibility` / `ch_alias` / `ch_color` 是「動底欄位」：以 dict 完整覆寫，不做欄位級 merge。

---

## 5. 模組設計

### 5.1 `gx20_reader.py`

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

### 5.2 `storage.py`

```python
storage.init_db(reset=False)                       # 啟動時呼叫；reset=False 保留既有資料
storage.insert_sample(ts, station, temps[20])
storage.query_recent(station, since_minutes=60)    # → List[dict]
storage.query_latest(station)                      # → dict | None
storage.purge_old_samples(retention_days)          # → int（刪除筆數）
storage.count_samples() / count_samples_by_station()
storage.clear_station_db(station)                  # v5：只清單一工位
storage.archive_station(station)                   # v5：清除前歸檔
storage.get_all_settings() / get_setting() / set_setting()
storage.save_settings(merged_dict)                 # 寫 SQLite + dump settings.json
```

**WAL 模式**：`PRAGMA journal_mode=WAL` + `synchronous=NORMAL` → 並行讀取不阻塞 poller 寫入。

**v5 關鍵行為**：
- 啟動時 `_migrate_legacy_if_needed()`：若偵測到舊 `data/gx20.db` → 整份先歸檔為 `gx20_pre_migration_<時間>.db` → samples 按 station 切到 6 個新 DB → settings 複製 → 刪舊檔（含 WAL/SHM/JOURNAL）
- 歸檔保留 5 份（每工位各自），超過自動刪最舊
- `clear_station_db` 只動單一工位的 DB（不含 settings）

### 5.3 `lttb.py`（後端 LTTB 降取樣）

```python
from lttb import lttb_xy, downsample_rows

# 對 (x, y) 序列降取樣
xs, ys = lttb_xy(xs_in, ys_in, threshold=2000)

# 對 list of dict 降取樣（用 ts 為主軸切桶）
rows = downsample_rows(rows, ts_key="ts", point_keys=["t01",...], threshold=2000)
```

演算法：見 §14。

### 5.4 `app.py`（Flask + SocketIO）

詳見 §9 路由表。

**關鍵設計**：

- **poller thread**：每 10 秒讀 GX20 → 寫 SQLite → 更新 ring buffer → emit `new_sample`
- **ring buffer**：每工位保留最近 720 筆（2hr），rate/avg 直接從記憶體算，不再 query_recent 全表
- **定期 purge**：poller 每 5 分鐘跑一次 `purge_old_samples(retention_days)`
- **LTTB on-the-fly**：`/api/history` 回應前若筆數 > `max_points` 自動降取樣
- **CSV 平均整合**：`/api/export_csv/<station>` 拉 raw rows，依分鐘 bucket 平均輸出
- **Debug logger**：RotatingFileHandler 寫到 `logs/app.log`；HTTP 請求、SocketIO 事件、poller 每輪結果都會入 log
- **設定同步**：`save_settings()` 同步 dump 到 `config/settings.json`；`main()` 啟動時若檔案存在則直接採用
- **OTA 模組**：`ota.py` 註冊 4 個 admin endpoint（見 §12）

### 5.5 `static/js/storage.js`（跨分頁設定層）

```
GX20State.init()     → 拉 server 設定 → 套 session 覆蓋 → 套主題
GX20State.update(k, v) → 寫 sessionStorage + 標 dirty
GX20State.save()      → POST 到 server + 清 dirty + 寫回 session 頂層 key
GX20State.setTheme(t) → 立即切換主題
```

**關鍵**：每個分頁有自己獨立的 `sessionStorage["gx20.tab_state.v1"]`，
與 SQLite / JSON 持久層分離。**切換分頁不會互相覆蓋未保存的變更**。

### 5.6 `static/js/lttb.js`（前端 LTTB）

`window.lttb(data, threshold)` 對 `{x, y}` 陣列降取樣。當前端 dataset 超過上限時保險用。

### 5.7 `static/js/main.js` 關鍵不變量（v4 系列修過的痛點）

接手時請特別留意以下幾個地方，動到容易回歸：

| 場景 | 必須做的處理 | 為什麼 |
|------|------------|--------|
| 切 X 軸 | `loadGen += 1` + `await loadHistory` | Chart.js time scale 在空 dataset 下會退化到毫秒級 |
| 切主題 | 只改 chart 顏色（`applyThemeToChart`），不要 `rebuildChart` | 重建會清掉 dataset，線條消失 |
| 切工位 | `loadGen` 守門 + 立即 fetch `/api/latest` | 否則切換後 0~10 秒表格空白 |
| `pruneOldData` | 每條線至少保留 1 點 | 空 dataset 會讓 time scale 退化 |

---

## 6. 資料生命週期與 DB 佈局

### 6.1 DB 佈局（v5）

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

### 6.2 啟動流程

```
1. _migrate_legacy_if_needed()        ← v5 自動：若偵測到舊 data/gx20.db
2. config/settings.json 優先套用       ← v4
3. 若 SQLite 為空 → 寫入預設值 + dump JSON
4. storage.purge_old_samples(7)       ← 逐工位刪超過 7 天的舊資料
5. 註冊 atexit / SIGINT handler（不再 clear_db）
6. 啟動 poller thread
7. socketio.run(...)
```

### 6.3 執行中

- 每 10 秒：poller 讀 GX20 → 寫「該工位」的 SQLite → 更新 ring buffer → emit `new_sample`
- 每 5 分鐘：逐工位 purge 過期資料
- DB 持續累積，**關閉程式也不會被清空**
- 使用者按「保存」→ 同步寫 SQLite + dump JSON

### 6.4 關閉

- atexit / SIGINT / SIGTERM 觸發時：只清空 ring buffer 與 GX20 連線
- **不再刪 DB**（v1 行為已拔除）
- 下次啟動可繼續累積

### 6.5 手動清除（v5）

主畫面或設定頁頂部 [清除此工位] 按鈕：

1. **第一段 confirm**：是否先歸檔到 `data/archive/`？
2. **第二段 confirm**：確認刪除？顯示此次會做的動作
3. 按下確認後：
   - `archive_station(station)` → 拷貝到 `gx20_<station>_<時間>.db` → 輪替保留 5 份
   - `clear_station_db(station)` → 刪除該工位 DB（含 WAL/SHM/JOURNAL）
   - 清空該工位 ring buffer
4. 其他工位資料完全不動
5. 設定（GX20 連線、別名、顏色）完全不動

---

## 7. 效能與降取樣策略

### 7.1 瓶頸與對策

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

### 7.2 測試數據（7 天 60480 筆，單工位）

| 操作 | 耗時 |
|---|---|
| INSERT 35,210 筆/秒（batch executemany）| 1.7s / 60k 筆 |
| LTTB 60480 → 2000 | **45ms** |
| CSV 平均整合 60480 → 1440 桶 | **~30ms** |
| `query_recent` 7 天 | 1.07s（poller 不再跑這個）|
| `purge_old_samples` 7 天 | 0.35s |

### 7.3 參數調整

- `max_points = 2000`：圖表總點數上限
  - 螢幕寬度 1080px → 1 點 ≈ 0.5px → 2000 點足夠塞滿且滑順
  - 調高（如 5000）會更精細但可能卡
  - 調低（如 500）會更流暢但丟失細節
- `retention_days = 7`：DB 保留天數
  - 7 天 / 6 工位 ≈ 35 MB
  - 空間夠可調 30，空間緊可調 1~3

---

## 8. 前端 UI 與互動

### 8.1 主頁 `index.html` 結構

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

### 8.2 v6.1 游標模式（量測狀態）

右側「最新讀值」標題旁多一組 toggle button：
- **即時狀態**（預設）：表格顯示即時溫度、速率、平均（同上表）
- **量測狀態**：表格改為顯示游標區間內的 **平均 / 最大 / 最小**（以實際筆數為分母）

游標線與區間 highlight：
- 量測狀態下圖表出現 **綠 / 紅兩條可拖曳垂直線**（x-bar）
- **淡黃色 highlight** 標示選取區間
- 拖曳即時更新表格（純前端從 LTTB 資料點算）
- 游標線預設放在當前 X 軸範圍的 **1/3 / 2/3** 位置
- 切換工位 → 強制回即時狀態，游標位置重置
- 切換 X 軸範圍 → 游標線重置（模式保留）
- 即時狀態下，游標線 / 區間 highlight / 區間資訊列全部隱藏

區間資訊列：顯示「區間：yy/mm/dd hh:mm:ss ~ yy/mm/dd hh:mm:ss (duration)」

### 8.3 設定頁 `settings.html` 結構

```
┌─────────────────────────────────────────┐
│ GX20 監視 — 設定        [回監看] [保存] [立即清除 SQLite] [...] │
├─────────────────────────────────────────┤
│ 1. GX20 連線                             │
│    Host: [<GX20_DEFAULT_HOST>]   Port: [34434]   │
│                                         │
│ 2. Y 軸範圍                              │
│    最小值: [-20]  最大值: [100]           │
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

### 8.4 「保存」按鈕行為

- 設定頁頂部有 [保存] 按鈕（主畫面已無此按鈕）
- 任何欄位改動 → 按鈕變橘色，文字變 `保存 ●`（提示 dirty）
- 按下保存 → 寫 server + 清 dirty + 同步 sessionStorage 頂層 + dump JSON
- **未保存就離開頁面** → 變更只存在本分頁的 sessionStorage，下次進同分頁仍在；進新分頁則消失

---

## 9. 路由與 API

### 9.1 頁面

| 路徑 | 方法 | 用途 |
|---|---|---|
| `/` | GET | 監看主頁 |
| `/settings` | GET | 設定頁 |
| `/favicon.ico` | GET | 內建 ICO |

### 9.2 API

| 路徑 | 方法 | 用途 |
|---|---|---|
| `/api/settings` | GET | 讀取全部設定 |
| `/api/settings` | POST | 寫入設定（merge-write + dump JSON）|
| `/api/channels` | GET | 6 工位 × 20 接點的 4 碼頻道號 |
| `/api/history/<station>` | GET | 拉歷史；支援 `?max_points=N` LTTB 降取樣、`?since_minutes=N` |
| `/api/latest/<station>` | GET | **v4.3 起**回傳完整 `new_sample` payload（temps / rate / avg） |
| `/api/connection` | GET | 連線狀態、host/port |
| `/api/db_stats` | GET | DB 統計（每工位筆數、時間範圍、retention、歸檔保留份數）|
| `/api/clear` | POST | **v5**：清除指定工位；body `{"station":"工位5", "archive":true}` |
| `/api/archives` | GET | 查歸檔清單（`?station=工位5` 過濾）|
| `/api/export_csv/<station>` | GET | 匯出 CSV；依 X 軸範圍 + 平均整合為 1 分鐘/筆 |
| `/api/debug` | GET | 讀取 debug 狀態 |
| `/api/debug` | POST | 切換 debug 狀態（`{"enabled": true/false}`）|
| `/api/debug/log_tail` | GET | 查 `logs/app.log` 末段（`?lines=N`）|

### 9.3 SocketIO 事件

| 事件 | 方向 | payload |
|---|---|---|
| `new_sample` | S → C | `{ts, station, temps:[20], rate:[20], avg:[20]}` |

每 10 秒廣播一次給所有連線的瀏覽器分頁。

### 9.4 OTA Admin 端點（見 §12）

| 方法 | 路徑 | 用途 |
|---|---|---|
| `GET`  | `/api/admin/status`     | 查 OTA 狀態、token 指紋、uptime、watch dog 版本 |
| `POST` | `/api/admin/ota`        | 推單檔（multipart） |
| `POST` | `/api/admin/ota_bundle` | 推多檔（JSON + base64）|
| `POST` | `/api/admin/restart`    | 觸發自我重啟 |

---

## 10. 設定同步機制（config/settings.json）

### 10.1 三層架構

```
┌────────────────────────────────────────────┐
│ sessionStorage  (per tab, dirty until save) │
│     ↑ user changes                          │
│     │ "保存" 按鈕 → POST /api/settings      │
│     ↓                                        │
│ SQLite (gx20_settings.db, 全部設定的事實來源)│
│     ↑ save_settings()                       │
│     │ 同步 dump                              │
│     ↓                                        │
│ config/settings.json  (重啟時優先採用)        │
└────────────────────────────────────────────┘
```

### 10.2 啟動流程

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

### 10.3 同步策略

- **寫入側**：`save_settings()` 寫完 SQLite 後，atomic write（`.tmp` + `os.replace`）dump 整包 merge 設定到 `config/settings.json`
- **讀取側**：`main()` 啟動時若檔案存在 → 直接讀取並寫回 SQLite（後續 `load_settings()` 仍以 SQLite 為主，但內容已被 JSON 覆蓋）
- **動底欄位**（`ch_visibility` / `ch_alias` / `ch_color`）：以 dict 完整覆寫

### 10.4 範例 `config/settings.json`

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

## 11. 主題系統（light / dark）

### 11.1 設計

- CSS 變數系統：所有顏色集中在 `:root` 與 `body[data-theme="..."]` 區塊
- `body[data-theme="light"]` / `body[data-theme="dark"]` 兩套配色
- 切換主題**不需重整**，即時生效
- 圖表軸、格線、圖例文字都從 CSS 變數讀取；`MutationObserver` 監聽主題變化自動 rebuild chart

### 11.2 顏色

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

### 11.3 主題切換行為

- 設定在 `sessionStorage["gx20.tab_state.v1"].theme`（每分頁獨立）
- 按下保存才寫入 server；下次重整會記得
- 預設偵測系統 `prefers-color-scheme`

---

## 12. OTA 部署通道

> 完整操作手冊：[docs/DEPLOY_OTA.md](docs/DEPLOY_OTA.md)
> 本節為**機制說明**（為什麼這樣設計、各元件怎麼互動、故障怎麼排查）

### 12.1 為什麼需要 OTA

| 角色 | 主機 | 工作目錄 | 同步方式 |
|------|------|----------|----------|
| 開發端 | WSL（<DEV_HOST>） | `<DEV_PROJECT_DIR>\` | 二寶改檔的起點 |
| 部署端 | Windows <DEPLOY_HOST> | `<DEPLOY_PATH>\` | 跑 python app.py |
| 同步通道 | **OTA**（HTTP） | `POST /api/admin/*` 帶 `X-OTA-Token` header | 兩端都是 Windows 跑 Python，但**沒有** OneDrive 同步 |

部署端是工廠機台，沒有開發環境，也不會裝 git、rsync、雲端同步。  
OTA 通道 = **HTTP 上傳檔 + 自我重啟**，讓二寶在 WSL 端改完 → 推檔 → 部署端自動套用。

### 12.2 系統組成（4 隻元件）

```
┌─────────────────────── 開發端 (WSL) ───────────────────────┐
│  1. ota_push.py        CLI 推送工具（單檔 / 批次 / 重啟）   │
└──────────────────────────┬──────────────────────────────────┘
                           │  HTTP POST + X-OTA-Token
                           ▼
┌─────────────────────── 部署端 (<DEPLOY_HOST>) ────────────────┐
│  2. app.py              Flask 主進程（掛載 4 個 admin 端點）│
│       └── ota.py        OTA 模組（白名單、Token、原子寫入）│
│  3. ota_watchdog.bat    Watch dog（v2 自動找 python + log）│
│       └── python app.py  ←── Flask 子進程                  │
│  4. start_forever.bat   背景啟動器（關視窗不殺 watch dog） │
│                                                               │
│  config/                                                       │
│  ├── ota_token             32 byte 隨機 token（不入版控）     │
│  └── ota_backup/<ts>/<f>  寫入前自動備份                      │
└───────────────────────────────────────────────────────────────┘
```

| 元件 | 角色 | 觸發時機 |
|------|------|----------|
| `ota_push.py` | 開發者用的 CLI（單檔 push / 批次 bundle / 純重啟 / 查狀態） | 我改完檔之後 |
| `ota.py` | Flask 內的 OTA 模組；註冊 4 個 admin endpoint | Flask 啟動時掛載 |
| `ota_watchdog.bat` | 包住 `python app.py` 的批次迴圈 | 崩潰或 OTA 重啟時接手 |
| `start_forever.bat` | 用 `start /B /MIN` 把 watch dog 開成背景 | 工廠開機 / 換人接手時 |

### 12.3 4 個 Admin 端點

由 `ota.py` 註冊到 Flask，全部走 `X-OTA-Token` header 認證：

| 方法 | 路徑 | 用途 | 請求格式 |
|------|------|------|----------|
| `GET`  | `/api/admin/status`     | 查 OTA 狀態、token 指紋、uptime、watch dog 版本 | — |
| `POST` | `/api/admin/ota`        | 推**單檔**（multipart） | `multipart/form-data` + `target=相對路徑` |
| `POST` | `/api/admin/ota_bundle` | 推**多檔**一次到位 | JSON + base64 內文 |
| `POST` | `/api/admin/restart`    | 觸發自我重啟 | JSON `{"delay_sec": 2}` |

`ota_push.py` 把這些封裝成子命令：

```bash
python3 ota_push.py status  http://<DEPLOY_HOST>:5000
python3 ota_push.py push    http://<DEPLOY_HOST>:5000 ./static/js/main.js static/js/main.js --restart
python3 ota_push.py bundle  http://<DEPLOY_HOST>:5000 ota_manifest.json
python3 ota_push.py restart http://<DEPLOY_HOST>:5000
```

### 12.4 安全性（為什麼不擔心被打）

| 風險 | 防護 | 實作位置 |
|------|------|----------|
| 未授權推檔 | Token 認證（恆定時間比對） | `ota.check_token()` |
| 路徑穿越 `../` | `os.path.normpath` + 必須落在 `APP_ROOT` 內 | `ota.resolve_target()` |
| 任意檔案覆蓋 | 寫入白名單 `ALLOWED_TARGETS` | `ota.is_allowed_target()` |
| 上傳執行檔 | 黑名單副檔名 `.pyc/.so/.dll/.exe/.sh/.ps1` | `BLOCKED_EXTS`（白名單具名 .bat 例外） |
| 寫到一半壞檔 | Atomic write：`.tmp` + `os.replace` | `ota.atomic_write()` |
| 改壞了救不回 | 寫入前自動備份到 `config/ota_backup/<ts>/` | `ota._backup_existing()` |
| Token 洩漏 | 不進版控（`.gitignore`）、只用指紋對照 | `.gitignore`、`token_fingerprint()` |

**Token 來源優先序**：

1. 環境變數 `GX20_OTA_TOKEN`（最高優先）
2. `config/ota_token` 檔（部署端 Flask 第一次啟動自動產生 32 byte 隨機 token）
3. 自動產生並寫入 `config/ota_token`

`/api/admin/status` 只回 `token_fingerprint`（sha256 前 8 碼），**絕不回傳完整 token**。  
二寶跟大大對照指紋即可確認兩端 token 一致，token 本身不透過 Telegram 明文傳。

### 12.5 一次完整推送的時序

以「修了一個 JS bug」為例：

```
開發端 (WSL)                          部署端 (<DEPLOY_HOST>)
─────────────                         ───────────────────
1. 改 static/js/main.js               
2. 跑 ota_push.py push ...            
   │
   ├─ 讀 token (config/ota_token)     
   ├─ POST /api/admin/ota  ────────►  3. ota.py 收到檔案
   │   (multipart)                       ├─ 檢查 X-OTA-Token ✅
   │                                    ├─ 檢查 target 在白名單 ✅
   │                                    ├─ 備份原檔到 ota_backup/<ts>/
   │                                    ├─ 寫 .tmp → os.replace ✅
   │   ◄── {ok: true, size: 12345}      
   │                                    
   ├─ POST /api/admin/restart ─────►  4. ota.schedule_restart(2)
   │   ({"delay_sec": 2})                ├─ 起一個 daemon thread
   │   ◄── {ok: true}                    ├─ sleep 2 秒
   │                                    └─ os._exit(0)  ← 主進程退出
   │                                    
   │                              ┌──► 5. ota_watchdog.bat 看到 exit code 0
   │                              │     ├─ 視為「正常 OTA 重啟」
   │                              │     ├─ 清掉 FAIL_COUNT
   │                              │     ├─ timeout /t 3
   │                              │     └─ goto LOOP
   │                              │         └─ python app.py   ← 新 Flask 起來
   │                              │             ├─ 載入新版 main.js
   │                              │             └─ 廣播 socket 'version_changed'
   │                              │
   │   ◄── HTTP 200 (新 Flask 接手)    
   │                                    
6. 看到連線回 200 = 推送成功          
7. Playwright 自動驗證圖表/主題/切站
```

關鍵設計點：
- **OTA 自己不 spawn 新 Flask**：只讓主進程退出，由 watch dog 接手。  
  否則 `python app.py` 會跟 `ota_watchdog.bat` 內的 `python app.py` 撞 port 5000，產生兩個 Flask 並存。
- **exit code 區分崩潰 vs 正常重啟**：`os._exit(0)` = 正常 OTA 重啟（清 FAIL_COUNT），  
  其他非零 = 崩潰（累計 FAIL_COUNT，5 次連敗才放手）。
- **重啟期間前端不白屏**：socket 自動重連（Flask-SocketIO client 內建），5~8 秒後新版上線。

### 12.6 寫入路徑白名單

只有這些路徑可以被 OTA 覆蓋（其他路徑直接 400）：

```
前端
  static/js/         static/css/        static/vendor/
  templates/

後端核心
  app.py   config.py   storage.py   gx20_reader.py
  lttb.py  run.py

OTA 自己（不能改 ota.py 繞過自己的白名單檢查）
  ota.py   ota_push.py
  ota_watchdog.bat   start_forever.bat
```

新增可寫入檔 = 改 `ota.py` 的 `ALLOWED_TARGETS` 常數 → **必須透過 OTA 推新版 ota.py 才能加**。  
（避免「OTA 模組自己允許自己寫入任意路徑」的循環漏洞。）

### 12.7 Watch Dog 狀態機

```
            ┌─────────────────────────────────────────────┐
            │ ota_watchdog.bat :LOOP                      │
            └─────────────────────────────────────────────┘
                          │
                          ▼
            ┌─────────────────────────────────────────────┐
            │  python app.py                              │
            │   ├─ 正常服務中（socket 推播、poller 跑）   │
            │   ├─ 收到 /api/admin/restart                │
            │   │    └─ 2 秒後 os._exit(0)                │
            │   ├─ 收到 /api/admin/ota                   │
            │   │    └─ atomic_write → 等下次重啟才生效   │
            │   └─ 崩潰（未捕例外）                       │
            │        └─ 立刻退出，exit code != 0          │
            └─────────────────────────────────────────────┘
                          │
            ┌─────────────┴─────────────┐
            │                           │
       exit 0 (OTA)               exit != 0 (崩潰)
            │                           │
            ▼                           ▼
   FAIL_COUNT ← 0           FAIL_COUNT += 1
   timeout 3s                timeout 3s
   goto LOOP                 若 ≥ 5 → pause 給人接手
                             否則 goto LOOP
```

### 12.8 背景啟動（工廠現場）

部署端不希望有人記得「手動跑 watch dog」 → 用 `start_forever.bat`：

```cmd
cd <DEPLOY_PATH>
start_forever.bat
```

這個 bat 跑一行 `start "" /B /MIN cmd /c ota_watchdog.bat`：
- `/B` = 在同 session 背景跑
- `/MIN` = 縮到工作列
- 父視窗關掉**不影響**子 cmd（這是 `start /B` 跟直接呼叫的差別）

驗證三件事都看到才算活：

```cmd
netstat -ano | findstr :5000      ← Flask 在 listen
tasklist | findstr python.exe     ← python 進程在跑
type logs\watchdog.log            ← watch dog 有紀錄
```

### 12.9 故障排查對照表

| 現象 | 看哪個 log | 可能原因 |
|------|-----------|----------|
| 推檔回 401 | — | Token 錯 / 兩端 token 指紋不一致 → `GET /api/admin/status` 對指紋 |
| 推檔回 400 "target 不在白名單" | — | 想寫的路徑不在 `ALLOWED_TARGETS` → 改 `ota.py` 重推 |
| 推檔回 500 "寫入失敗" | `logs/app.log` | 磁碟滿 / 權限不足 / 路徑含中文路徑問題 |
| 重啟後 Flask 沒起來 | `logs/watchdog.log` | python 路徑找不到 / `app.py` 語法錯誤（已上線版本被改壞） |
| Watch dog 連續 5 次退出 | `logs/watchdog.log` 看到 `[FATAL]` | `app.py` 進 startup 就崩潰；先手動跑 `python app.py` 看錯誤 |
| 前端一直轉圈 | `logs/app.log` 的 socket 連線 | 可能是新版前端 JS 語法錯 → 從 `ota_backup` 還原 |
| 上傳的檔內容跟本地不一樣 | — | `--target` 寫錯路徑，檔案落在白名單允許但位置不對的地方 |
| Watch dog 跑兩份（port 5000 佔用） | `tasklist` 看到兩個 python | 之前 OTA 自己 spawn Flask 的舊 bug → 已修，v4.4 後不會發生 |

---

## 13. GX20 通訊協定摘要

| 項目 | 值 |
|---|---|
| 連線 | TCP |
| 預設 IP / Port | `<GX20_DEFAULT_HOST>:34434`（見 `config.py`） |
| 指令 | `FData,0,0001,1210\r\n` |
| 頻道範圍 | 0001 ~ 1210（6 工位 × 20 接點）|
| 回應格式 | 每行 31 char |
| 解析欄位 | `[0]` 狀態 / `[2:6]` 頻道 / `[10:18]` 單位 / `[18]` 正負號 / `[19:31]` 科學符號值 |
| 無效值 | `999.9` 視為無效 |

詳見 `gx20_reader.py`。

---

## 14. LTTB 降取樣演算法

論文：Sveinn Steinarsson, "Downsampling Time Series for Visual Representation" (2013)
https://skemman.is/handle/1946/15343

演算法概念：把 N 筆切成 `threshold` 桶，每桶挑「與上一選中點 + 下桶平均點構成最大三角形」的那一點。**視覺上能保留峰谷**。

- 第一點與最後一點**永遠保留**
- 中間切成 `threshold` 桶
- 1000 點 sin 波 + 3 個 spike 降到 100 點：spike **3 個全保留**

實作：見 `lttb.py`（後端）與 `static/js/lttb.js`（前端），兩者演算法一致。
