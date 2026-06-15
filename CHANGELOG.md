# GX20 Web Monitor — 版本演進與現況

> 大版本快照 + 重要 bug 修復 + 現況進度（最後更新：2026-06-15）
>
> 程式架構見 [ARCHITECTURE.md](ARCHITECTURE.md)；使用者操作見 [README.md](README.md)。

---

## 目錄

1. [版本演進快照](#1-版本演進快照)
2. [現況進度（2026-06-11 session）](#2-現況進度2026-06-11-session)
3. [現況進度（2026-06-12 進階計算上線）](#3-現況進度2026-06-12-進階計算上線)
4. [現況進度（2026-06-15 PW3335 電力計上線）](#4-現況進度2026-06-15-pw3335-電力計上線)

---

## 1. 版本演進快照

| 版 | 日期 | 重點 |
|---|---|---|
| **v2.0** | — | 資料持久化、LTTB 降取樣、明暗主題、ring buffer 計算 |
| **v3.0** | — | debug logger、圖表精簡、X 軸範圍動態、CSV 整合匯出、設定檔同步 |
| **v4.0** | — | 最新讀值下拉化、CSV 平均整合、設定檔同步 |
| **v4.1** | — | 圖表切換 hotfix + OTA 部署通道 |
| **v5.0** | — | 6 工位獨立 DB + 清除前歸檔 |
| **v6.0** | — | per-station Y 軸範圍 + 動態縮放 + clear_log endpoint |
| **v6.1** | 2026-06-12 | 進階計算：游標模式（拖曳 x-bar 計算區間平均/最大/最小）|
| **v7** | 2026-06-15 | PW3335 電力計整合（6 工位 V/I/W、雙圖表、CSV 補欄）|

### 1.1 v5.0 — 6 工位獨立 DB + 清除前歸檔

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

### 1.2 v4.0 — 最新讀值下拉化 + CSV 平均整合 + 設定檔同步

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

### 1.3 v3.0 — Debug logger + 圖表精簡 + 動態 X 軸

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

### 1.4 v4.1 — 圖表切換 hotfix + OTA 部署通道

**Bug 修正（`static/js/main.js` v4 hotfix）**：
- 切 X 軸視窗後圖表空白 / X 軸縮成毫秒級：切換時清空 dataset 並重新拉歷史（`patchSettingAndApply` 內 `loadGen += 1` + `await loadHistory`）
- 切主題後渲染錯亂：MutationObserver 用 `requestAnimationFrame` 排隊，銷毀前先 `chart.stop()`
- 切站點後表格空白 / 資料停在舊時間：`loadHistory` 與 `switchStation` 用 `loadGen` 世代號保護
- `pruneOldData` 改為「每條線至少保留 1 點」，避免 Chart.js time scale 在空 dataset 時退化到毫秒
- Chart 加上 `normalized: true` 與 `ticks.source: "auto"`，明確指定資料格式

**OTA 部署通道（`ota.py` / `ota_push.py` / `ota_watchdog.bat`）**：
- `GET  /api/admin/status` 查狀態
- `POST /api/admin/ota` 推單檔（multipart）
- `POST /api/admin/ota_bundle` 一次推多檔（JSON + base64）
- `POST /api/admin/restart` 觸發自我重啟
- Token 認證：環境變數 / 檔案 / 自動產生
- 白名單：限縮 `static/js/`、`static/css/`、`templates/`、核心 `.py`
- 寫入前自動備份到 `config/ota_backup/<timestamp>/`
- `ota_watchdog.bat` 包住 `python app.py`，崩潰或被 OTA 重啟時自動再起

詳細流程見 [docs/DEPLOY_OTA.md](docs/DEPLOY_OTA.md)。

### 1.5 v6.1 — 進階計算：游標模式

> 設計過程詳見 [docs/CURSOR_MODE.md](docs/CURSOR_MODE.md)

**新增功能**：
- 右側「最新讀值」標題旁多一組 toggle button（即時狀態 / 量測狀態）
- 量測狀態下圖表出現 **綠 / 紅兩條可拖曳垂直線**（x-bar）
- 淡黃色 highlight 標示選取區間
- 拖曳即時更新表格：區間內的 **平均 / 最大 / 最小**
- 區間資訊列顯示起訖時間與 duration

**設計決策摘要**：
- 切換按鈕：Toggle（互斥）
- 計算來源：前端 LTTB 資料點（拖曳需 0 延遲）
- 分母：區間內實際筆數（沿用 v6 avg 原則，不補 0）
- 游標線預設位置：1/3 / 2/3
- 切換工位 → 強制回即時狀態
- 切換 X 軸 → 游標重置、模式保留
- 「計算」按鈕 → 取消（拖曳即更新）
- 即時狀態下游標線 → 全部隱藏

---

## 2. 現況進度（2026-06-11 session）

本節記錄 2026-06-11 當日二寶協作發現的 bug、修法、以及進行中的工作。

### 2.1 已修好的 Bug（v4 系列）

實機觀察到三個**切換導致圖表錯亂**的 bug，已在 2026-06-11 全部修好並 OTA 推送。

| # | Bug | 觸發情境 | 修法版本 | 驗證 |
|---|-----|---------|---------|------|
| 1 | X 軸切換後圖表空白 | 把 X 軸從「全部」切到「3 小時」 | v4.1 | ✅ Playwright 自動驗證 |
| 2 | 切主題後線條消失 | 按 ☀/🌙 切到 dark / 切回 light | v4.2 | ✅ Playwright 自動驗證 |
| 3 | 切工位後表格 0~10 秒空白 | 切換工位後右側「最新讀值」空白到下一輪 socket 推播 | v4.3 | ✅ Playwright 自動驗證 |

**v4.1 重點**（X 軸切換）：
- `patchSettingAndApply` 切 X 軸時改用 `rebuildChart()` 取代「清空 dataset + update」
- 因為 Chart.js time scale 的 min/max 是 chart 物件初始化時計算的，後續改 `options.scales.x.min` 在空 dataset 狀態下會讓 scale 退化到毫秒級
- `buildChart` 建好後立即把 min/max 寫進 `chart.options.scales.x`
- `loadHistory` 拉完後再強制設一次 min/max 避免 Chart.js 用舊錨點
- `pruneOldData` 從「至少 1 點」改為「至少 2 點」（起點 + 終點才有線）

**v4.2 重點**（主題切換）：
- 之前切主題觸發 `rebuildChart()`，會清空 dataset 但沒重拉資料，導致線條不見
- 改成只改 chart 顏色（不重建）
- 新增 `applyThemeToChart()`，只更新 `chart.options.scales.*.ticks.color` 與 `grid.color`
- MutationObserver 改呼叫 `applyThemeToChart()` 而非 `rebuildChart()`

**v4.3 重點**（切工位表格立即更新）：
- 之前 `switchStation` 跑完只靠 socket 推播更新表格，10 秒一輪可能讓表格空白
- 後端 `app.py` 的 `/api/latest/<station>` 擴充為回傳完整 `new_sample` payload（含 temps / rate / avg）
- 前端 `switchStation` 跑完後立即呼叫 `/api/latest`，拿最新一筆填表格
- 視覺表現：切工位後 < 1 秒就看到完整 20 列讀值

### 2.2 Watch Dog 穩定性（v4.4 已上線 ✅）

**問題**：v4.1 / v4.2 / v4.3 三次 OTA 重啟後，watch dog 都没撐住重啟 Flask，每次都要請大大手動重啟。

**根因**：舊 `ota.schedule_restart` 用 `subprocess.Popen` spawn 一個新 `python app.py` 自己跑，**撞 watch dog 內同一個 `python app.py` → port 5000 佔用衝突**，且舊 watch dog 沒有日誌，難以事後排查。

**修法（v4.4）**：
- `ota.py.schedule_restart` 改為「只讓主進程退出」，**不自己 spawn 新 Flask**（避免跟 watch dog 撞 port 5000）
- `ota_watchdog.bat` 升級到 v2：
  - 自動偵測 python 絕對路徑（不依賴 PATH）
  - 失敗時自動 fallback 常見安裝位置
  - 所有事件寫到 `logs\watchdog.log` 便於事後診斷
  - `python --version` 預先驗證 executable 可用
  - 連續失敗 5 次才放手（給人接手，不無限循環）
- 新增 `start_forever.bat`：用 `start /B /MIN` 把 watch dog 開在背景，**cmd 視窗關掉不影響 watch dog**
- `ota.py` 白名單加入 `start_forever.bat`
- `/api/admin/status` 加入 `uptime_seconds` 與 `ota_version: 2`

**驗證結果**（2026-06-11 20:13）：

| 驗證項 | 結果 |
| ------ | ---- |
| Flask 連線 | ✅ 5/5 HTTP 200 |
| Watch dog 接手（單次 OTA 重啟） | ✅ 6 秒內 |
| Watch dog 接手（連推 3 次 OTA） | ✅ 3/3 成功 |
| 連推後所有切換（X 軸 / 主題 / 工位） | ✅ 表格 20 列無錯 |

### 2.3 待辦：功能改善（Sprint 1）

v4 系列 bug 修完後，原本提的三大需求進入實作階段。

| 需求 | 內容 | 預估 | 狀態 |
|------|------|------|------|
| 加速理解的指標/統計 | 圖表極值標註、Y 軸參考線、統計摘要卡（最高/最低/平均/最大溫差）、表格新欄位（趨勢箭頭、視窗 Δ / min/max、距上次變化） | 中 | 待開工 |
| 介面觀看便利性 | 雙 Y 軸、十字游標同步 tooltip、快捷縮放 / 框選放大、表格排序 / 凍結 / 快速過濾、點表格行高亮圖表、Sparkline 縮圖 | 中 | 待開工 |
| 更詳細的設定參數 | 通道門檻（高/低溫 + cell 閃爍）、警報 toast、群組、顯示細節（線寬/點大小/平滑度/小數位）、Y 軸自動縮放 | 中 | 待開工 |

詳細子項見 [docs/IMPROVEMENTS.md](docs/IMPROVEMENTS.md)。

### 2.4 環境與工作流摘要

| 角色 | 主機 | 路徑 | 備註 |
|------|------|------|------|
| 開發端 | WSL (PCXSSDl) | `D:\OneDrive - Sampo Corporation\3.Data\5.Python\gx20-web-monitor` | 二寶改檔的起點 |
| 部署端 | Windows <DEPLOY_HOST> | `<DEPLOY_PATH>` | 跑 python app.py |
| 同步通道 | OTA（HTTP） | `POST /api/admin/*` 帶 `X-OTA-Token` header | 兩端都是 Windows 跑 Python，但沒 OneDrive 同步 |

**二寶工具鏈**（在 WSL 端）：
- 程式碼：直接編輯開發端 `D:\OneDrive\...` 下的檔案
- OTA 推送：`python3 ota_push.py push http://<DEPLOY_HOST>:5000 <local> <target> --restart`
- 多檔推送：`python3 ota_push.py bundle http://<DEPLOY_HOST>:5000 ota_manifest.json`
- 自動驗證：Playwright headless Chromium，跑三個切換情境 + 截圖 + 像素分析

**Token 管理**：
- 部署端 Flask 第一次啟動時自動產生 32 byte token → 寫入 `<DEPLOY_PATH>\config\ota_token`
- 二寶的開發端用對應 token 寫入 `D:\OneDrive\...\config\ota_token`（本機路徑，**不進版控**）
- 指紋（`sha256[:8]`）可在 `/api/admin/status` 看到，用於對照

### 2.5 版本號對照（2026-06-11 20:13 快照）

| 部署端檔案 | 版本 | 對應 commit / 階段 | 狀態 |
|-----------|------|-------------------|------|
| `static/js/main.js` | v4.3 | 切工位立即 fetch /api/latest | ✅ 已上線 |
| `app.py` | v4.3 | `/api/latest/<station>` 回 new_sample 格式 | ✅ 已上線 |
| `ota.py` | **v4.4** | schedule_restart 改不 spawn、加入 uptime / ota_version: 2 | ✅ 已上線 |
| `ota_watchdog.bat` | **v2** | 自動找 python + log + 連敗 5 次放手 | ✅ 已上線 |
| `start_forever.bat` | **v1** | 背景啟動 watch dog（`start /B /MIN`） | ✅ 已上線 |
| `ota_push.py` | **v4.4** | CLI push / bundle / restart / status | ✅ 已上線 |
| `templates/index.html` | `?v=5` | 瀏覽器 cache busting | ✅ 已上線 |
| `static/css/style.css` | `?v=5` | 瀏覽器 cache busting | ✅ 已上線 |

---

## 3. 現況進度（2026-06-12 進階計算上線）

本節記錄 2026-06-12 v6.1 進階計算（游標模式）的設計、實作、迭代。

### 3.1 需求背景

參考其他工業監控軟體（如 A&D AD-1687 / HOBOware）設計「即時 / 游標」切換：
- **即時狀態**（Live）：預設，表格顯示最新讀值
- **量測狀態**（Cursor）：表格顯示游標區間內的統計值（平均 / 最大 / 最小）

提供「拖曳即計算」的即時互動，不需按「計算」按鈕。

### 3.2 設計決策

| 決策項 | 選擇 | 理由 |
|--------|------|------|
| 切換按鈕 | Toggle（互斥） | 兩個狀態互不重疊 |
| 計算來源 | 前端 LTTB 資料點 | 拖曳需 0 延遲 |
| 分母 | 區間內實際筆數 | 沿用 v6 avg 原則（不補 0） |
| 游標線預設位置 | 1/3 / 2/3 | 確保在 X 軸可見範圍內 |
| 切換工位 | 強制回即時狀態 | 不跨工位保留狀態 |
| 切換 X 軸 | 游標重置、模式保留 | 換視窗但「量測中」的語意保留 |
| 「計算」按鈕 | **取消** | 拖曳即更新，不需手動觸發 |
| 即時狀態下游標線 | **全部隱藏** | 避免 UI 雜訊 |

### 3.3 迭代歷史（8 個 commit 4 個小版本）

| 版本 | commit | 修改 |
|------|--------|------|
| v6.1 | 4d2d540 | 初版：toggle、游標拖曳、表格平均/最大/最小、debounce API 查覆蓋率 |
| v6.1.1 | 7967bcd | 修正：切換工位 / X 軸時游標線位置停在舊時間點（清空 tsLeft/tsRight、預設 1/3 / 2/3 取代 25% / 75%） |
| v6.1.2 | da57fa1 | 修正：拖曳時「區間」資訊列沒更新（onMove 漏加 updateCursorInfo()） |
| v6.1.3 | f4eaa28 | 修正：移除「資料覆蓋」整列 UI（語意不清，拖曳時跳動造成誤判） |
| v6.1.4 | 92968ae | 清理：移除 v6.1 殘留的 /api/cursor/coverage endpoint 與 storage.query_count_in_range 函式（-101 +30 行） |

### 3.4 v6.1.3 移除「資料覆蓋」的原因

觸發事件：OTA 端實測發現，拖曳游標線在斷線區間內移動時，「資料覆蓋：xx 筆 / 預期 yy 筆 (zz%)」的數字會跳動。

語意問題：
- **預期筆數** = 區間秒數 / poll 週期（會隨區間長度成比例縮放）
- **實際筆數** 在斷線區間內變動幅度小
- 結果：pct 跳動明顯 → 使用者誤以為資料有問題

無論用方案 A（前端 LTTB 推算，誤差 ±10%）還是方案 B（後端 SQLite 查詢，準但需 debounce）都解決不了「斷線中拖曳 → 預期變動」的語意問題。使用者決策：若不影響計算的正確性，**移除不用顯示**。

### 3.5 設計文件與迭代記錄

完整設計過程（兩個方案的優缺點比較、最終決策、未實作原因）記錄於：
- [docs/CURSOR_MODE.md](docs/CURSOR_MODE.md)：設計文件（375 行）

MEMORY.md 也記下了「使用者決策偏好」，供未來類似需求參考。

### 3.6 版本號對照（2026-06-12 23:47 快照）

| 部署端檔案 | 版本 | 對應 commit | 狀態 |
|-----------|------|-------------|------|
| `app.py` | v6.1.4 | 92968ae | ✅ 已上線（待推送） |
| `storage.py` | v6.1.4 | 92968ae | ✅ 已上線（待推送） |
| `static/js/main.js` | v6.1.3 | f4eaa28 | ✅ 已上線 |
| `static/css/style.css` | v6.1.3 | f4eaa28 | ✅ 已上線 |
| `templates/index.html` | v6.1.3 | f4eaa28 | ✅ 已上線 |
| `templates/index.html` | `?v=6` | 瀏覽器 cache busting | ✅ 已上線 |
| `static/css/style.css` | `?v=6` | 瀏覽器 cache busting | ✅ 已上線 |
| `static/js/main.js` | `?v=6` | 瀏覽器 cache busting | ✅ 已上線 |
| `docs/CURSOR_MODE.md` | v6.1.4 | 92968ae | 📁 本機 + GitHub（OTA 白名單不含 docs/，推不上去） |

---

## 4. 現況進度（2026-06-15 PW3335 電力計上線）

### 4.1 背景

桌機版 `kalapontsai/GX20-PW3335-Data-Collection` 已能用 GW Instek PW3335 讀電壓/電流/功率。
網頁版只接了 GX20 溫度，缺電力資料。本次任務把 PW3335 整合進來。

### 4.2 通訊協定（與桌機版一致）

- TCP 連線到 `<ip>:<port>`（預設 3300）
- 送出指令 `b':MEAS? U,I,P,WH\\n'`
- 回應 `U +110.14E+0;I +0.0000E+0;P +000.00E+0;WP +00.0000E+0`（4 段 `;` 分隔）
- 只取前三段（U / I / P）→ 對應 V / I / W；WP（累積 Wh）丟棄
- 解析失敗或連線錯誤 → 回傳 `(0, 0, 0, False)`，不丟例外

### 4.3 設計決策

| 決策項 | 選擇 | 理由 |
|--------|------|------|
| 連線方式 | 每輪 / 每工位一條 socket | 設定可熱改（IP/port/remote）；故障復原簡單 |
| 預設 IP | `192.168.1.{2..7}` | 沿用桌機版 `GX20_PW3335.py` line 884 對應規則（工位1→.2 ... 工位6→.7） |
| 預設 `remote` | 全 False | 避免第一次啟動就連一堆失敗的 PW3335；使用者到設定頁打勾才啟用 |
| 預設 V/I,W Y軸 | V(0,230) / I,W(0,250) | 依使用者 2026-06-15 決定 |
| 電力 DB 欄位 | `v REAL, i REAL, w REAL` | 跟 `t01~t20` 同檔；nullable 兼容舊 DB |
| DB 升級 | `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` | 舊 DB 自動補欄，無痛升級 |
| CSV 補欄 | `V, I, W` 三欄 | 依使用者需求命名（不用桌機版 U/V/A/P/W/WP） |
| 電力 CSV 精度 | V 2 / I 3 / W 2 | 沿用桌機版（V 跟 W 同精細度、I 較精細） |
| 電力圖表 Y 軸 | 左 I/W 共用、右 V 獨立 | 依使用者需求 |
| 電力圖表高度 | 30% | 溫度 70%（依使用者需求） |
| 電力圖表量測模式 | 隱藏 | 沿用「輔助 UI 在即時模式才顯示」偏好 |
| 電力線顏色 | 預設 V=黃 / I=青 / W=紅 | 避開溫度 20 色；可設定頁改 |
| 量測模式電力表 | 改顯示 平均/最大/最小 | 跟溫度表同行為 |
| 桌面版 `Debug_mode` 模擬值 | **不做** | 使用者明確要求 `remote=False` 寫 0 即可，不要假資料 |

### 4.4 迭代歷史（5 個 commit）

| commit | 修改 |
|--------|------|
| `f8e0d48` | feat(pw3335): 新增 pw3335_reader.py + config.py 預設值擴充 |
| `7002104` | feat(pw3335): storage 加 v/i/w 欄位 + app.py poller 整合（+ /api/pw_connection） |
| `522b860` | feat(csv): 匯出 CSV 補 V/I/W 三欄 |
| `f939bdb` | feat(ui): 主畫面雙圖表 (70/30) + 電力表格 + 設定頁 PW3335 區塊 |
| (本檔) | docs: CHANGELOG / README / example 設定補 v7 |

### 4.5 新 API

| 端點 | 用途 |
|------|------|
| `GET /api/pw_connection` | 6 工位 PW3335 連線狀態 (remote/connected/host/last_error/last_vip) |

### 4.6 DB Schema 變更（向後相容）

`samples` 表新增三欄（v7 之後新建的 DB 自動包含；舊 DB 啟動時自動 ALTER）：

```sql
ALTER TABLE samples ADD COLUMN v REAL;
ALTER TABLE samples ADD COLUMN i REAL;
ALTER TABLE samples ADD COLUMN w REAL;
```

`storage._ensure_power_columns()` 用 `PRAGMA table_info(samples)` 偵測缺欄就 ALTER。
