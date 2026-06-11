# GX20 溫度監視 — 功能改善建議

> 文件版本：v1（2026-06-11 整理）
> 對象：GX20 溫度監視前端 + 後端 poller
> 範圍：主畫面（index.html / main.js）、設定頁、CSS、poller、SQLite、API

---

## 0. 現況速覽

| 區塊 | 現況 |
|---|---|
| 主畫面 | 1 張大圖表（Chart.js，最多 20 條線）+ 右側「最新讀值」表格 |
| 即時資料 | Socket.IO 每 10 秒推播 `new_sample`（含 temps/rate/avg） |
| X 軸視窗 | 主畫面 select 切換（0=全部 / 15min~1day），即時寫 settings |
| 速率 / 平均 | 從 ring buffer 計算，可由 select 切換 window |
| 設定頁 | 接點顯示/隱藏、別名、顏色、Y 軸上下限、保留天數、CSV 匯出 |
| 主題 | light / dark（存 sessionStorage） |
| 匯出 | CSV（用 showSaveFilePicker 或 <a download> fallback） |
| 已知問題 | **X 軸切換 / 主題切換 / 站點切換時圖表渲染異常**（見 §4） |

---

## 一、加速理解溫度變化的指標 / 統計

### 1.1 圖表上疊加「智慧線」

| 項目 | 說明 | 技術 | 優先 |
|---|---|---|---|
| 極值標註 | 每條線在「目前 X 軸視窗內」的最高 / 最低處加小三角 + 數值 | Chart.js annotation plugin 或自訂 plugin | P1 |
| 參考線 | Y 軸上使用者自訂的水平虛線（上限 / 下限） | chartjs-plugin-annotation | P1 |
| 趨勢著色 | 通道溫度「持續上升 N 分鐘」自動加粗 | 後端算 slope 後推 flag | P2 |
| 異常旗標 | 速率 < -2°C/min 持續 30s → 表格顯示「降溫中」徽章 | 後端在 payload 內推 flag | P2 |

### 1.2 全站統計摘要卡

```
[ 工位5 ─ 統計摘要（近 60 分鐘） ]
┌────────────┬────────────┬────────────┬────────────┐
│ 最高溫     │ 最低溫     │ 平均溫     │ 最大溫差   │
│ 78.3°C@Ch07│ 22.1°C@Ch03│ 51.4°C     │ 56.2°C     │
│ 發生 14:32 │ 發生 13:05 │            │ (max-min)  │
└────────────┴────────────┴────────────┴────────────┘
```

- 後端加 `/api/stats/<station>?since_minutes=60`，一次回傳 20 通道的 min/max/avg/Δ + 發生時間
- 前端一個可摺疊卡片，預設收合
- 區間可選：15min / 1h / 6h / 24h

### 1.3 「最新讀值」表格加 4 個欄位（高速掃描用）

| 新欄位 | 意義 | 計算方式 |
|---|---|---|
| 趨勢箭頭 | 依 rate 分級 ↑↑ / ↑ / → / ↓ / ↓↓ | 門檻 ±0.5、±2.0 |
| 本視窗 Δ | 視窗起點 - 終點差值 | 從 ring buffer 取首末值 |
| 本視窗 min/max | 視窗內範圍 | 從 ring buffer 掃 |
| 距上次變化 | 該通道值連續相同的秒數 | 記住上次的 ts + value |

### 1.4 異常事件時間軸

- 圖表下方水平「事件軸」，任一通道 rate 越界 / 跨門檻 → 點出小圖示
- 點擊事件 → 圖表自動滑到該時間 + 跳出提示卡

---

## 二、介面觀看便利性

### 2.1 圖表可讀性

| 項目 | 效果 | 優先 |
|---|---|---|
| Y 軸自動縮放 | 依 X 軸視窗內資料動態微調 min/max（保留使用者設定為基準） | P1 |
| 雙 Y 軸（選配） | 設定頁勾選「啟用第二 Y 軸」→ 選定的通道畫在右軸 | P2 |
| 十字游標 + 同步 tooltip | 一條垂直線跨所有線，表頭顯示「該時刻各通道值」 | P1 |
| 時間刻度可調密度 | < 30min → 每分鐘；< 3h → 每 10min；> 1d → 每小時 | P1 |
| 快捷縮放 | 滾輪 = 縮放 X 軸；Shift+滾輪 = 縮放 Y 軸；雙擊 = 復原 | P2 |
| 框選放大 | 拖曳選取區域放大 | P2 |

### 2.2 表格可讀性

| 項目 | 效果 | 優先 |
|---|---|---|
| 數值異常高亮 | 當前讀值 > 上限或 < 下限 → cell 紅 / 藍背景閃爍 | P1 |
| 點表格行 → 圖表高亮 | 點表格某行 → 對應的線加粗，其他線變淡 | P1 |
| 欄位排序 | 點表頭可依「讀值 / 速率 / 平均」升降冪排序 | P1 |
| 凍結按鈕 | 按下後暫停即時更新（資料仍寫入 DB），再按一次解除 | P1 |
| 快速過濾 | 表格上方 input，輸入關鍵字即時過濾通道名 | P1 |

### 2.3 多工位比較模式（進階）

- 頂部加 [單工位] [比較] 切換
- 比較模式可勾選 2~3 個工位，把同一通道號疊在一張圖上（例：「爐心溫度」跨 6 工位比較）
- 後端 `/api/multi_history?stations=工位1,工位3&channel=7` → 回傳多 series

### 2.4 響應式 / 觸控

- 表格在手機寬度下變成可左右滑動的卡片
- 圖表加雙指縮放
- 主按鈕加大觸控區（至少 44px）

### 2.5 視覺化輔助

| 項目 | 用途 | 優先 |
|---|---|---|
| 小縮圖 sparkline | 每工位旁顯示近 1 小時迷你折線圖，掃 6 工位概況 | P2 |
| 狀態色塊 | 工位下拉旁顯示色塊 + 連線狀態 + 最後更新秒數（> 60s 變紅） | P1 |
| 圖例小窗 | 把隱藏的圖例改為「左側可摺疊清單」+ 搜尋框 | P2 |

---

## 三、更詳細的設定參數

### 3.1 主畫面三個 select 可延伸

| 參數 | 現況 | 建議擴充 |
|---|---|---|
| X 軸 | 預設 9 個選項 | 加「自訂…」（跳輸入框） |
| 速率區間 | 1~60 分鐘 | 加「自訂」+ 1 秒級（瞬間測試用） |
| 平均區間 | 1~360 分鐘 | 同上 |

### 3.2 警報 / 門檻（建議獨立分頁）

```json
{
  "alerts": {
    "enabled": true,
    "channels": {
      "Ch07": { "high": 75, "low": 20, "rate_up": 5, "rate_down": -3 },
      "Ch03": { "high": 60, "low": 15 }
    },
    "actions": {
      "sound": true,
      "flash_row": true,
      "toast": true
    }
  }
}
```

- 觸發時：表格 cell 變色 + 右上角 toast + 圖表內紅色驚嘆號
- 全部存 `data/`，不依賴外部服務

### 3.3 統計區間可調

- 統計摘要卡的「近 N 分鐘」用同一個 select 控制
- 加「對齊到整點 / 整 5 分鐘」checkbox（避免每分鐘都漂移）

### 3.4 通道群組

- 設定頁可自訂群組名（「爐心」「外殼」「散熱片」）
- 群組內通道可一次切換顯示 / 隱藏
- 主畫面表格上方加群組下拉

### 3.5 顯示細節選項

| 參數 | 預設 | 用途 | 優先 |
|---|---|---|---|
| `show_point_value` | false | 圖表上每個資料點顯示數字（會很亂，慎用） | P2 |
| `line_width` | 1.5 | 線條粗細（1~3） | P1 |
| `point_radius` | 1.5 | 資料點大小（0~4） | P1 |
| `smooth` | 0.15 | 曲線平滑度（0=折線） | P1 |
| `decimal_places` | 1 | 表格讀值小數位（0~3） | P1 |
| `rate_unit` | per_window | 速率單位：每分鐘 / 每視窗 | P2 |
| `color_blind_palette` | false | 色盲友善色盤切換 | P2 |

### 3.6 性能 / 細節

- `lttb_threshold`：現為 2000，可調（500~5000）
- `refresh_interval`：現為 socket 推播（10s），可改「手動刷新」按鈕模式
- `chart_buffer_seconds`：X 軸右側保留幾秒空白

### 3.7 CSV 匯出擴充

- 匯出範圍除了「主畫面 X 軸」，可選「全資料 / 近 1h / 今日 / 自訂起訖」
- 加 JSON 格式匯出
- 選項：是否含 rate / avg / 別名

---

## 四、已知 Bug 與修法

### Bug-1：X 軸切換後圖表變空白、X 軸縮成毫秒級

**症狀（圖 2）**：把 X 軸從「全部」切到「3 小時」後，圖表變空白，X 軸刻度縮到 `1:03:15.622 p.m. ~ 1:03:15.624 p.m.`（0.002 秒跨度）。

**根因**：

1. `slideXWindow()` 在每次 `onNewSample` 觸發時，把 `chart.options.scales.x.min` 設成 `Date.now() - xMin*60000`
2. 但 `loadHistory()` 只在初始化或站點切換時跑一次 → 切換 X 軸時**不會重新拉歷史**
3. `rebuildChart()` 內部設定了 `xScale.min`，但 `pruneOldData()` 在新 sample 進來時**把視窗外的舊資料全砍了** → 圖表變空
4. Chart.js time scale 在 dataset 全空時，min 變成 `undefined` 對齊到某個內部時間軸（顯示成毫秒級）

**修法**：

- `patchSettingAndApply` 切換 X 軸時，**同步觸發 `loadHistory()`**（用新視窗的 `since_minutes` 拉資料）
- `slideXWindow()` 加保護：若 `chart.data.datasets[*].data` 全部為空，跳過更新 min
- `pruneOldData()` 不要在 `xMin > 0` 時砍到全空，**至少保留最近 1 個點**

### Bug-2：主題切換後圖表渲染異常

**症狀**：切到 dark 或切回 light 後，dataset 結構被破壞、Y 軸刻度比例亂、表格空白。

**根因**：`MutationObserver` 觀察 `body[data-theme]`，每次變化就 `rebuildChart()` → 在 chart 動畫/重繪中途又觸發 destroy + new Chart，導致中途狀態被讀到。

**修法**：

- 主題切換只更新 chart 顏色（destroy 後重建），**用 requestAnimationFrame 包起來**
- chart 不存在時 observer 不做事
- `rebuildChart()` 內統一走 `chart.destroy()` → `new Chart()` 路徑，避免殘留狀態

### Bug-3：站點切換後資料停在舊時間、表格空白

**症狀（圖 3）**：切換站點後，圖表停在 15:55，右側「最新讀值」表格空白，必須再切一次才正常。

**根因**：

- 站點切換 listener 只呼叫 `rebuildChart()` + `loadHistory()` + `updateReadoutTable(null)`
- `updateReadoutTable(null)` 故意清空表格（合理）
- 但 `loadHistory()` 是 `async`，在 `chart.data.datasets` 已經被 `rebuildChart()` 重建過的**新 dataset** 上面塞資料，而 socket 推播到的還是「當下選的工位」→ 兩者錯亂

**修法**：

- `loadHistory()` 開頭先 `await` 確認 `chart` 與 `currentStation` 沒有被中途切換（用一個 `loadGen` 計數器）
- 站點切換時鎖住直到 loadHistory 完成，再解鎖接收 socket
- 或者更簡單：把 `currentStation` 改用變數並在每次 fetch 前後檢查

---

## 五、優先順序（Sprint 規劃）

### Sprint 1（1~2 天，CP 高、風險低）— **強烈建議先做**

- [ ] 修 Bug-1 / Bug-2 / Bug-3（見 §4）
- [ ] 統計摘要卡（最高 / 最低 / 平均 / 最大溫差）
- [ ] 表格加「趨勢箭頭」+「本視窗 Δ / min / max」
- [ ] 十字游標同步 tooltip
- [ ] 通道門檻（高 / 低溫）+ cell 閃爍
- [ ] 凍結 / 排序 / 快速過濾

### Sprint 2（2~3 天，進階可視化）

- [ ] 極值標註 + 異常旗標
- [ ] 圖表縮放 / 框選
- [ ] 工位狀態色塊 + 連線秒數
- [ ] 警報 toast + 驚嘆號
- [ ] sparkline 縮圖
- [ ] 通道群組

### Sprint 3（3~5 天，大改版）

- [ ] 雙 Y 軸 / 多工位比較模式
- [ ] 自訂警報複雜邏輯
- [ ] 色盲模式 / 響應式優化
- [ ] 匯出 JSON / 匯出選項

---

## 六、改動風險評估

| 項目 | 改動範圍 | 風險 |
|---|---|---|
| Bug 修法 | main.js（前端） | 低 |
| 統計摘要卡 | 新增 /api/stats、後端查 ring 或 DB、新增區塊 | 低 |
| 表格欄位 | main.js + 後端 payload 結構 | 中（要動 poller 推播） |
| 主題切換 bug | main.js | 低 |
| 通道門檻 | 新增 settings、poller 計算、新增 cell style | 中 |
| 多工位比較 | 新頁面 + 新 API | 高（資料流複雜） |
