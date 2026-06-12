// main.js — 監看主頁邏輯
//
// 圖表：Chart.js（y 軸=溫度；右側 legend 隱藏，由設定頁管理接點顯示/隱藏）
// 即時：Socket.IO
//
// v3 變更：
//   - 主畫面不再有「保存」按鈕（設定頁才有）
//   - 主畫面不再顯示圖表標題、左下角說明框
//   - 圖表只顯示 20 條溫度線（移除 rate/avg 兩種 dataset）
//   - rate / avg 由後端推播，前端只用於「最新讀值」表格
//   - X 軸範圍 / 速率區間 / 平均區間 → 主畫面「最新讀值」區塊三個 select
//     變更後立即 POST /api/settings（單一 key patch），下一輪推播即生效
//   - 「最新讀值」表頭速率/平均的單位會跟著 select 改變
//
// v4 修正（2026-06-11）：
//   - 修「切 X 軸後圖表空白 / X 軸縮成毫秒」：切 X 軸時清空 dataset 並重新拉歷史
//   - 修「切主題後渲染錯亂」：MutationObserver 用 rAF 排隊，銷毀前先 chart.stop()
//   - 修「切站點後表格空白 / 資料停在舊時間」：loadHistory 與 switchStation
//     用 loadGen 世代號保護，非同步結果在站點/視窗被切走時自動丟棄
//   - buildChart 補上 xScale.max，slideXWindow 同步更新 max，
//     避免 Chart.js time scale 在空 dataset 時退化到毫秒級
//   - pruneOldData 改成「每條線至少保留 1 點」，避免 dataset 全空
//   - Chart 加上 normalized: true，明確告訴它用 {x,y} 物件資料
//
// v4.1 修正（2026-06-11，第二次）：
//   - patchSettingAndApply 切 X 軸：改用 rebuildChart() 取代「清空 dataset + update」，
//     因為 Chart.js time scale 的 min/max 是在 chart 物件初始化時計算的，後續改
//     options.scales.x.min 在空 dataset 狀態下會讓 scale 退化到毫秒級。
//   - buildChart 建好後立即把 min/max 寫進 chart.options.scales.x，確保生效。
//   - patchSettingAndApply 切完 X 軸、loadHistory 拉完後，再強制設一次 min/max
//     並 chart.update("none")，避免 Chart.js 用舊錨點計算軸。
//   - pruneOldData 從「至少 1 點」改為「至少 2 點」（起點 + 終點才有線）。
//
// v4.2 修正（2026-06-11，第三次）：
//   - 修「切主題後圖表線條消失」：之前切主題觸發 rebuildChart()，會清空
//     dataset 但沒重拉資料，導致線條不見。改成只改 chart 顏色（不重建）。
//   - 新增 applyThemeToChart()，只更新 chart.options.scales.*.ticks.color
//     與 grid.color，保留 dataset 與 chart 物件本身。
//   - MutationObserver 改呼叫 applyThemeToChart() 而非 rebuildChart()。
//
// v4.3 修正（2026-06-11，第四次）：
//   - 修「切工位後表格空白 0~10 秒」：原來 switchStation 跑完只靠 socket 推播
//     更新表格，10 秒一輪可能讓表格空白。
//   - 新增 /api/latest/<station> 回傳完整 new_sample payload（後端 app.py 改）。
//   - switchStation 跑完後立即呼叫 /api/latest，拿最新一筆填表格。
//   - 視覺表現：切工位後 < 1 秒就看到完整 20 列讀值。

const POINTS = 20;
let currentStation = null;
let chart = null;
let themeRebuildPending = false;   // 主題切換時排隊重建，避免動畫中重入
let loadGen = 0;                    // loadHistory / onNewSample 世代號，避免中途被站點切換打斷
const channelNums = {};

// ---------- 初始化 ----------

async function init() {
  await GX20State.init();
  const settings = GX20State.settings;

  // 預設站點（session 沒指定就用第一站）
  const cache = loadSessionExtra();
  const sel = document.getElementById("stationSelect");
  if (cache.currentStation && settings.ch_alias[cache.currentStation]) {
    sel.value = cache.currentStation;
  }
  currentStation = sel.value;
  document.title = `[${currentStation}]`;

  await loadChannelNums();

  // 主畫面三個 select 初始化
  initReadoutControls(settings);

  sel.addEventListener("change", () => {
    switchStation(sel.value);
  });

  document.getElementById("clearBtn").addEventListener("click", async () => {
    // v5：兩段式 confirm
    // 1) 詢問是否歸檔
    const archive = confirm(
      `要清除工位「${currentStation}」的歷史資料嗎？\n\n` +
      `【確定】= 先歸檔到 data/archive/ 再清除（推薦）\n` +
      `【取消】= 繼續下一個問題（問要不要直接刪除）`
    );
    // 2) 詢問是否真的清除
    if (!confirm(
      `最後確認：清除工位「${currentStation}」的資料？\n\n` +
      `歸檔：${archive ? "是（保留到 data/archive/）" : "否（不保留）"}\n` +
      `按下「確定」就立刻刪除，無法復原${archive ? "（但有歸檔可恢復）" : ""}。`
    )) return;

    const btn = document.getElementById("clearBtn");
    btn.disabled = true;
    const oldText = btn.textContent;
    btn.textContent = "清除中…";
    try {
      const r = await fetch("/api/clear", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ station: currentStation, archive }),
      });
      const j = await r.json();
      if (!j.ok) { alert("清除失敗：" + (j.error || "")); return; }
      const archMsg = j.archived ? `\n歸檔：${j.archive_path}` : "\n歸檔：未保留";
      alert(`已清除「${j.station}」${archMsg}`);
      rebuildChart();
      updateReadoutTable(null);
    } finally {
      btn.disabled = false;
      btn.textContent = "清除此工位";
    }
  });

  // 主題切換
  document.getElementById("themeBtn").addEventListener("click", () => {
    const next = GX20State.theme === "dark" ? "light" : "dark";
    GX20State.setTheme(next);
  });

  // 儲存 CSV（以視窗對話框選路徑）
  document.getElementById("exportCsvBtn").addEventListener("click", exportCurrentStationAsCsv);

  // SocketIO
  const socket = io();
  socket.on("connect",    () => updateConn(true,  null));
  socket.on("disconnect", () => updateConn(false, "SocketIO 斷線"));
  socket.on("new_sample", onNewSample);

  setInterval(refreshConnStatus, 5000);
  refreshConnStatus();

  buildChart();
  loadHistory();

  // v6 游標模式
  initCursorMode();
}

// 站點選擇另外存（不歸 GX20State 核心設定管）
const TAB_KEY = "gx20.tab_extra.v1";
function loadSessionExtra() {
  try { return JSON.parse(sessionStorage.getItem(TAB_KEY) || "{}"); } catch { return {}; }
}
function saveSessionExtra(patch) {
  const cur = loadSessionExtra();
  const next = Object.assign({}, cur, patch);
  sessionStorage.setItem(TAB_KEY, JSON.stringify(next));
  GX20State.markDirty();
}

async function loadChannelNums() {
  const r = await fetch("/api/channels");
  const j = await r.json();
  if (j.ok) {
    Object.assign(channelNums, j.channels);
  } else {
    for (const s of Object.keys(GX20State.settings.ch_alias)) {
      channelNums[s] = Array.from({length: POINTS}, (_, i) => `Ch${String(i+1).padStart(2, "0")}`);
    }
  }
}

// ---------- 連線狀態 ----------

function updateConn(ok, errMsg) {
  const dot  = document.getElementById("connDot");
  const text = document.getElementById("connText");
  dot.classList.toggle("on",  ok);
  dot.classList.toggle("off", !ok);
  text.textContent = ok ? "已連線" : (errMsg || "未連線");
}

async function refreshConnStatus() {
  try {
    const r = await fetch("/api/connection");
    const j = await r.json();
    updateConn(j.connected, j.last_error);
    document.getElementById("lastTs").textContent = j.last_ts ? `最後更新: ${j.last_ts}` : "";
  } catch (e) {
    updateConn(false, e.message);
  }
}

// ---------- 主畫面右側 select 控制項 ----------

/**
 * 把分鐘數轉成 "X分鐘" / "X小時" / "X天" 顯示字串（用於 select option label）。
 */
function _fmtMinLabel(min) {
  const n = Number(min);
  if (n === 0) return "全部";
  if (n < 60)  return `${n} 分鐘`;
  if (n % 60 === 0) {
    const h = n / 60;
    return h === 1 ? "1 小時" : `${h} 小時`;
  }
  // 非整數小時，回退顯示分鐘
  return `${n} 分鐘`;
}

/**
 * 動態設定表頭文字（速率/平均欄位）。
 */
function refreshRateAvgHeaders() {
  const rate = Number(GX20State.settings.rate_window_min) || 5;
  const avg  = Number(GX20State.settings.avg_window_min)  || 10;
  const rateTxt = `速率 (°C/${_fmtMinLabel(rate)})`;
  const avgTxt  = `平均 (°C/${_fmtMinLabel(avg)})`;
  document.getElementById("rateTh").textContent = rateTxt;
  document.getElementById("avgTh").textContent  = avgTxt;
}

/**
 * 把 select option 的 value 套用「目前」的值。
 * 並比對 option 是否存在；若不在預設清單內，動態加上一個 "N 分鐘"。
 */
function _setSelectValue(sel, value, allowed) {
  const v = String(value);
  if ([...sel.options].some(o => o.value === v)) {
    sel.value = v;
    return;
  }
  // 動態新增 option（使用者從設定頁/外部改了非預設值）
  const opt = document.createElement("option");
  opt.value = v;
  opt.textContent = _fmtMinLabel(Number(v));
  sel.appendChild(opt);
  sel.value = v;
}

function initReadoutControls(settings) {
  const xSel   = document.getElementById("chartXSel");
  const rateSel = document.getElementById("rateSel");
  const avgSel  = document.getElementById("avgSel");

  _setSelectValue(xSel,   settings.chart_x_minutes ?? 0);
  _setSelectValue(rateSel, settings.rate_window_min ?? 5);
  _setSelectValue(avgSel,  settings.avg_window_min  ?? 10);

  refreshRateAvgHeaders();

  xSel.addEventListener("change",   () => patchSettingAndApply("chart_x_minutes", parseInt(xSel.value, 10) || 0, "x"));
  rateSel.addEventListener("change", () => patchSettingAndApply("rate_window_min", parseInt(rateSel.value, 10) || 5, "rate"));
  avgSel.addEventListener("change",  () => patchSettingAndApply("avg_window_min",  parseInt(avgSel.value, 10) || 10, "avg"));
}

/**
 * 切換站點：清空資料 → 重建 chart → 拉歷史 → 期間拒收 socket 推播
 * 用 loadGen 確保非同步結果不會錯放到別的站位。
 */
async function switchStation(newStation) {
  currentStation = newStation;
  loadGen += 1;            // 中斷所有進行中的 loadHistory / onNewSample
  const myGen = loadGen;
  saveSessionExtra({ currentStation });
  document.title = `[${currentStation}]`;
  // 先清空右側表格並重畫 chart（避免殘留上一站資料的視覺）
  updateReadoutTable(null);
  rebuildChart();
  // v6：rebuildChart() 內已經會用新站位的 y_axis 套 Y 軸
  await loadHistory(myGen);
  // loadHistory 內部會自己檢查世代號
  // 關鍵修正：loadHistory 拉完歷史後，右側「最新讀值」表格依賴 socket
  // 推播更新。但 socket 推播是每 10 秒一次，剛切完可能還沒推，
  // table 會空白 0~10 秒。改用 /api/latest 拿最新一筆立即填入。
  if (myGen === loadGen) {
    try {
      const r = await fetch(`/api/latest/${encodeURIComponent(currentStation)}`);
      if (myGen !== loadGen) return;  // 又被切走了
      const j = await r.json();
      if (j.ok && j.payload) {
        updateReadoutTable(j.payload);
        document.getElementById("lastTs").textContent = `最後更新: ${j.payload.ts}`;
      }
    } catch (e) {
      // 拿不到最新也沒關係，等 socket 下一輪推播
    }
  }
}

/**
 * 變更設定 → 立即 POST /api/settings（單 key patch）→ 更新 GX20State → 重畫
 * 「下一個 tick」生效：後端 poller 下一次 emit new_sample 會帶新值
 * 但 X 軸要立即套用（影響 chart 範圍），所以 client 端也同步更新。
 */
async function patchSettingAndApply(key, value, which) {
  // 1) 寫 GX20State（同步 UI）
  GX20State.update(key, value);

  // 2) 立即套用到 client（X 軸 / 表頭）
  if (which === "x") {
    // X 軸視窗變更：整個重建 chart + 重拉歷史。
    // 重要：必須 rebuildChart() 不能只清 dataset，因為 Chart.js time scale
    // 的 min/max 是建立時計算的，之後改 options.scales.x.min 在空 dataset
    // 狀態下會讓 scale 退化成毫秒級（出現 0.002 秒跨度的刻度）。
    loadGen += 1;
    rebuildChart();      // 以新視窗重建 chart（含新 min/max）
    await loadHistory(loadGen);
    // 拉完歷史後，鎖住 X 軸 min/max 避免下次 chart.update 又退化
    if (chart) {
      const xMin = Number(GX20State.settings.chart_x_minutes) || 0;
      if (xMin > 0) {
        chart.options.scales.x.min = Date.now() - xMin * 60 * 1000;
        chart.options.scales.x.max = Date.now();
      } else {
        chart.options.scales.x.min = undefined;
        chart.options.scales.x.max = undefined;
      }
      chart.update("none");
    }
  } else if (which === "rate" || which === "avg") {
    refreshRateAvgHeaders();
  }

  // 3) 背景 POST 到 server（不需要等回應，poller 下輪會重讀）
  try {
    await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [key]: value }),
    });
  } catch (e) {
    console.warn("patchSetting 失敗", key, value, e);
  }
}

// ---------- Chart ----------

/**
 * v6：取得「目前站位」的 Y 軸設定。
 * 回傳 { min, max, auto }。
 * 讀不到就退到全域預設。
 */
function getYAxisForCurrent() {
  const s = GX20State.settings;
  const yAxis = (s && s.y_axis) || {};
  const entry = yAxis[currentStation];
  if (entry && typeof entry === "object") {
    return {
      min:  Number(entry.min),
      max:  Number(entry.max),
      auto: !!entry.auto,
    };
  }
  return { min: -20, max: 100, auto: false };
}

/**
 * 將目前站位的 Y 軸設定套進 chart.options.scales.y。
 * auto=true → 不設 min/max（讓 Chart.js 自動決定）
 * auto=false → 設 min/max 鎖住範圍
 */
function applyYAxisToChart() {
  if (!chart) return;
  const { min, max, auto } = getYAxisForCurrent();
  if (auto) {
    delete chart.options.scales.y.min;
    delete chart.options.scales.y.max;
  } else {
    if (Number.isFinite(min)) chart.options.scales.y.min = min;
    if (Number.isFinite(max)) chart.options.scales.y.max = max;
  }
}

// 圖表軸 / 格線 / 文字色，根據當前主題切換
function chartColors() {
  const cs = getComputedStyle(document.body);
  return {
    text:    cs.getPropertyValue("--text-dim").trim() || "#888",
    textStrong: cs.getPropertyValue("--text").trim() || "#fff",
    grid:    cs.getPropertyValue("--grid").trim() || "rgba(0,0,0,0.1)",
    bg:      cs.getPropertyValue("--surface").trim() || "#fff",
  };
}

function buildChart() {
  const ctx = document.getElementById("chart").getContext("2d");
  const settings = GX20State.settings;
  const datasets = [];
  for (let i = 0; i < POINTS; i++) {
    const vis = settings.ch_visibility[currentStation][i];
    if (!vis) continue;
    const color = settings.ch_color[currentStation][i];
    const alias = settings.ch_alias[currentStation][i] || `Ch${i+1}`;

    datasets.push({
      label: `${alias}`,
      data: [],
      borderColor: color,
      backgroundColor: color,
      borderWidth: 1.5,
      pointRadius: 1.5,
      tension: 0.15,
      yAxisID: "y",
      hidden: false,
      pointIndex: i,
    });
  }

  // 銷毀舊 chart（保險起見，先 stop 再 destroy，避免殘留動畫）
  if (chart) {
    try { chart.stop(); } catch (_) { /* noop */ }
    chart.destroy();
    chart = null;
  }
  const c = chartColors();
  // v6：Y 軸範圍改為 per-station 結構，這裡不再讀 y_axis_min/max
  // （applyYAxisToChart() 會在 chart 建好後處理）
  const xMin = Number(settings.chart_x_minutes) || 0;

  const xScale = {
    type: "time",
    time: {
      tooltipFormat: "yyyy-MM-dd HH:mm:ss",
      displayFormats: { minute: "HH:mm", hour: "MM-dd HH:mm", day: "MM-dd" },
    },
    ticks: { color: c.text, maxRotation: 0, autoSkipPadding: 20, source: "auto" },
    grid:  { color: c.grid },
    // 給 Chart.js 一個明確的 min/max bounds，避免空 dataset 時 scale 退化到毫秒。
    // 注意：Chart.js time scale 的 min/max 是「在 chart 物件上」的，不是 xScale 上的。
    // 我們下面在 scales.x 再設一次，確保生效。
  };
  if (xMin > 0) {
    const now = Date.now();
    xScale.min = now - xMin * 60 * 1000;
    xScale.max = now;
  }

  chart = new Chart(ctx, {
    type: "line",
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      parsing: false,
      normalized: true,             // 開啟 normalized，data 用 {x,y} 才不會被當成 category
      interaction: { mode: "nearest", intersect: false },
      plugins: {
        // v3：隱藏接點 legend（顯示/隱藏統一在設定頁管理）
        legend: { display: false },
        tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y}` } },
      },
      scales: {
        x: xScale,
        y: {
          type: "linear",
          position: "left",
          ticks: { color: c.text },
          grid:  { color: c.grid },
          title: { display: true, text: "溫度 (°C)", color: c.text },
          // v6：Y 軸 per-station。這裡不再硬編 yMin/yMax，改在 build 後 applyYAxisToChart() 決定。
        },
      },
    },
  });

  // v6：依「目前站位」y_axis 設定套 min/max
  applyYAxisToChart();

  // 重要：建好後立即把 min/max 寫進 chart.options（Chart.js time scale 只看這個）
  if (xMin > 0) {
    const now = Date.now();
    chart.options.scales.x.min = now - xMin * 60 * 1000;
    chart.options.scales.x.max = now;
  }
}

function rebuildChart() { buildChart(); }

/**
 * 主題切換時只改 chart 顏色（不重建 chart、不清資料）。
 * 重建 chart 會把 dataset 清空、資料要重拉，主題切換不該有這種副作用。
 */
function applyThemeToChart() {
  if (!chart) return;
  const c = chartColors();
  // X 軸
  if (chart.options.scales.x.ticks) chart.options.scales.x.ticks.color = c.text;
  if (chart.options.scales.x.grid)  chart.options.scales.x.grid.color  = c.grid;
  // Y 軸
  if (chart.options.scales.y.ticks) chart.options.scales.y.ticks.color = c.text;
  if (chart.options.scales.y.grid)  chart.options.scales.y.grid.color  = c.grid;
  if (chart.options.scales.y.title) chart.options.scales.y.title.color = c.text;
  chart.update("none");
}

// 監聽 body 的 data-theme 屬性變更（自訂事件）→ 套用新顏色
// 用 rAF 避免在 chart 內部重繪過程中重入
const _obs = new MutationObserver(() => {
  if (!chart || themeRebuildPending) return;
  themeRebuildPending = true;
  requestAnimationFrame(() => {
    themeRebuildPending = false;
    if (!chart) return;
    applyThemeToChart();
  });
});
_obs.observe(document.body, { attributes: true, attributeFilter: ["data-theme"] });

// ---------- 拉歷史 ----------

/**
 * 拉指定站位的歷史。呼叫時可帶 gen（預設用 loadGen）。
 * 結束時若 loadGen 已變（被其他切換/操作打斷）→ 丟棄結果，避免塞到錯的 chart。
 */
async function loadHistory(gen) {
  const myGen = (typeof gen === "number") ? gen : loadGen;
  try {
    const maxPoints = (GX20State.settings && GX20State.settings.max_points) || DATASET_MAX_POINTS;
    const xMin = Number(GX20State.settings.chart_x_minutes) || 0;
    const params = new URLSearchParams();
    params.set("max_points", String(maxPoints));
    if (xMin > 0) params.set("since_minutes", String(xMin));
    // xMin=0 不帶 since_minutes → server 拉全部
    const url = `/api/history/${encodeURIComponent(currentStation)}?${params.toString()}`;
    const r = await fetch(url);
    // fetch 期間若 loadGen 已變（站點/視窗被切走），直接放棄
    if (myGen !== loadGen) return;
    const j = await r.json();
    if (myGen !== loadGen) return;
    if (!j.ok) return;
    for (const row of j.rows) {
      const ts = new Date(row.ts).getTime();
      for (const ds of chart.data.datasets) {
        const idx = ds.pointIndex;
        const v = row[`t${String(idx+1).padStart(2, "0")}`];
        if (v === null || v === undefined) continue;
        ds.data.push({ x: ts, y: v });
      }
    }
    // 補上 LTTB 降取樣（避免 dataset 太肥）
    for (const ds of chart.data.datasets) {
      if (ds.data.length > DATASET_MAX_POINTS) {
        ds.data = window.lttb(ds.data, DATASET_MAX_POINTS);
      }
    }
    chart.update("none");
  } catch (e) {
    if (myGen === loadGen) console.error("loadHistory:", e);
  }
}

// ---------- SocketIO：即時資料 ----------

function onNewSample(payload) {
  if (!chart) return;
  if (payload.station !== currentStation) return;
  // v6：保存最近一次的 payload，供 setCursorMode 切回 live 時重畫表格用
  chart._lastPayload = payload;
  const ts = new Date(payload.ts).getTime();
  for (const ds of chart.data.datasets) {
    const idx = ds.pointIndex;
    const v = payload.temps[idx];
    if (v !== null && v !== undefined) ds.data.push({ x: ts, y: v });
  }
  pruneOldData();
  // X 軸動態：select 是分鐘錨點，隨時間流逝錨點也要跟著滑動
  slideXWindow();
  chart.update("none");
  // v6 游標模式：X 軸滑動後，游標線的 pixel 位置需要重算才能對齊
  if (typeof cursorState !== "undefined" && cursorState.mode === "cursor" && typeof layoutCursorBars === "function") {
    layoutCursorBars();
  }
  document.getElementById("lastTs").textContent = `最後更新: ${payload.ts}`;
  // v6 游標模式：量測狀態下不更新右側表格（避免平均/最大/最小被即時溫度覆蓋）
  if (typeof cursorState !== "undefined" && cursorState.mode === "cursor") return;
  updateReadoutTable(payload);
}

// 從 main.html 引入 lttb.js 後，全域 window.lttb 可用
const DATASET_MAX_POINTS = 2000;

function pruneOldData() {
  // X 軸是動態錨點：每次 new_sample 都以「現在」往前 N 分鐘剪掉舊點
  const xMin = Number(GX20State.settings.chart_x_minutes) || 0;
  if (xMin > 0) {
    const cutoff = Date.now() - xMin * 60 * 1000;
    for (const ds of chart.data.datasets) {
      // 每條線至少保留 2 個點：
      //   1) 避免 dataset 全空時 Chart.js time scale 退化成毫秒級
      //   2) 至少要有起點 + 終點，畫面才會顯示成線
      while (ds.data.length > 2 && ds.data[0].x < cutoff) ds.data.shift();
      if (ds.data.length > DATASET_MAX_POINTS) {
        ds.data = window.lttb(ds.data, DATASET_MAX_POINTS);
      }
    }
  } else {
    // 全部資料模式：只做點數上限保護（不裁剪）
    for (const ds of chart.data.datasets) {
      if (ds.data.length > DATASET_MAX_POINTS) {
        ds.data = window.lttb(ds.data, DATASET_MAX_POINTS);
      }
    }
  }
}

/**
 * X 軸動態錨點：select 是分鐘數，錨點是「now - N*60s」。
 * 隨著時間流逝，必須把錨點跟著往右滑，否則使用者看到的有效區間會一直縮小。
 * Chart.js time scale 預設不會自動 slide min，這裡手動更新。
 */
function slideXWindow() {
  // 隨著時間流逝，X 軸「現在」的錨點要跟著往右滑，
  // 否則使用者看到的有效區間會一直縮小。
  // 規則：只要 chart 有資料，就更新 min/max。
  // 若 chart 還沒資料（空 dataset）→ 不動，避免退化。
  if (!chart) return;
  const xMin = Number(GX20State.settings.chart_x_minutes) || 0;
  if (xMin <= 0) {
    // 「全部」模式：讓 Chart.js 自動決定（不鎖 min/max）
    if (chart.options.scales.x.min !== undefined) chart.options.scales.x.min = undefined;
    if (chart.options.scales.x.max !== undefined) chart.options.scales.x.max = undefined;
    return;
  }
  // 有 xMin 時：鎖定「最新資料點」到「最新 - xMin 分鐘」這個滑動視窗
  const newMin = Date.now() - xMin * 60 * 1000;
  const newMax = Date.now();
  // 只在真的改變時寫，避免觸發不必要的重算
  if (chart.options.scales.x.min !== newMin) chart.options.scales.x.min = newMin;
  if (chart.options.scales.x.max !== newMax) chart.options.scales.x.max = newMax;
}

// ---------- 右側表格（名稱、讀值、速率、平均） ----------
// 名稱規則：別名優先，別名為空時顯示頻道號

function updateReadoutTable(payload) {
  // v6 游標模式：量測狀態下不更新右側表格（避免被即時溫度覆蓋）
  if (typeof cursorState !== "undefined" && cursorState.mode === "cursor") return;
  const tbody = document.querySelector("#readoutTable tbody");
  tbody.innerHTML = "";
  if (!payload) return;
  const settings = GX20State.settings;
  for (let i = 0; i < POINTS; i++) {
    if (!settings.ch_visibility[currentStation][i]) continue;
    const alias = (settings.ch_alias[currentStation][i] || "").trim();
    const fallback = channelNums[currentStation]?.[i] || `Ch${i+1}`;
    const name = alias || fallback;
    const color = settings.ch_color[currentStation][i];
    const v = payload.temps[i];
    const r = payload.rate ? payload.rate[i] : null;
    const a = payload.avg  ? payload.avg[i]  : null;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${i+1}</td>
      <td><span class="swatch" style="background:${color}"></span>${escapeHtml(name)}</td>
      <td>${fmt(v, 1)}</td>
      <td>${fmtRate(r)}</td>
      <td>${fmt(a, 2)}</td>
    `;
    tbody.appendChild(tr);
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// 統一格式化：null/undefined/NaN → "—"，否則小數位
function fmt(v, digits) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return Number(v).toFixed(digits);
}

// 速率：含正負號
function fmtRate(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const s = Number(v).toFixed(3);
  return Number(v) > 0 ? "+" + s : s;
}

// ---------- 儲存 CSV ----------
// 優先使用 showSaveFilePicker（Chrome/Edge，可選資料夾與檔名）
// 不支援則 fallback 到傳統 <a download>（也會跳儲存對話框）

function pad2(n) { return String(n).padStart(2, "0"); }

function defaultFilename(xMin) {
  const d = new Date();
  const ts = `${d.getFullYear()}${pad2(d.getMonth() + 1)}${pad2(d.getDate())}_` +
             `${pad2(d.getHours())}${pad2(d.getMinutes())}${pad2(d.getSeconds())}`;
  const range = xMin > 0 ? `_${xMin}min` : "_all";
  return `${currentStation}${range}_${ts}.csv`;
}

async function exportCurrentStationAsCsv() {
  const btn = document.getElementById("exportCsvBtn");
  btn.disabled = true;
  const oldText = btn.textContent;
  btn.textContent = "儲存中…";
  try {
    // 以主畫面 X 軸長度作為匯出區間
    // 0 = 全部；>0 = 近 N 分鐘
    const xMin = Number(GX20State.settings.chart_x_minutes) || 0;
    const params = new URLSearchParams();
    if (xMin > 0) params.set("since_minutes", String(xMin));
    const qs = params.toString();
    const url = `/api/export_csv/${encodeURIComponent(currentStation)}${qs ? "?" + qs : ""}`;
    const r = await fetch(url);
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      alert("儲存失敗：" + (j.error || r.statusText));
      return;
    }
    const csvText = await r.text();
    const suggestedName = defaultFilename(xMin);

    if (window.showSaveFilePicker) {
      // Chrome/Edge 的 File System Access API
      try {
        const handle = await window.showSaveFilePicker({
          suggestedName,
          types: [{
            description: "CSV 檔案",
            accept: { "text/csv": [".csv"] },
          }],
        });
        const writable = await handle.createWritable();
        await writable.write(csvText);
        await writable.close();
        return;
      } catch (e) {
        if (e && e.name === "AbortError") return;  // 使用者取消
        console.warn("showSaveFilePicker 失敗，改用傳統下載:", e);
      }
    }

    // Fallback：建立隱形 <a download>
    const blob = new Blob(["\ufeff" + csvText.replace(/^\ufeff/, "")], { type: "text/csv;charset=utf-8" });
    const dlUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = dlUrl;
    a.download = suggestedName;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(dlUrl), 1000);
  } finally {
    btn.disabled = false;
    btn.textContent = oldText;
  }
}

window.addEventListener("DOMContentLoaded", init);

// =====================================================================
// v6 進階計算：游標模式（量測狀態）
// =====================================================================
//
// 模式狀態：'live'（即時）或 'cursor'（量測）。
// 切換邏輯：
//   - 即時 → 隱藏游標線、表格顯示即時溫度
//   - 量測 → 顯示兩條可拖曳游標線、表格顯示區間平均/最大/最小
//
// 互動規則：
//   - 拖曳游標：即時更新平均/最大/最小（純前端，從 chart dataset 取值）
//   - 拖曳停止 300ms 後：打 /api/cursor/coverage 查實際筆數（debounce）
//   - 切換工位：強制回 'live'
//   - 切換 X 軸：游標位置重置為新範圍的 25% / 75%

const CURSOR_DEBOUNCE_MS = 300;

const cursorState = {
  mode: 'live',        // 'live' | 'cursor'
  tsLeft:  null,       // Date
  tsRight: null,       // Date
  coverageTimer: null, // debounce timer id
};

function initCursorMode() {
  // toggle 按鈕
  document.getElementById("modeLiveBtn").addEventListener("click", () => setCursorMode("live"));
  document.getElementById("modeCursorBtn").addEventListener("click", () => setCursorMode("cursor"));

  // 切換工位時 → 強制回 live，並重置游標時間戳記（下次進量測模式時重算位置）
  document.getElementById("stationSelect").addEventListener("change", () => {
    cursorState.tsLeft = null;
    cursorState.tsRight = null;
    setCursorMode("live");
  });

  // 切換 X 軸時 → 游標線重置（但模式保留）
  document.getElementById("chartXSel").addEventListener("change", () => {
    // 不論當前是哪個模式，都把舊的時間戳記清掉
    // 避免下次進量測模式時拿舊工位/舊 X 軸的 ts 推算位置（會跑到螢幕外）
    cursorState.tsLeft = null;
    cursorState.tsRight = null;
    if (cursorState.mode === "cursor") {
      resetCursorPositions();
    }
  });

  // 拖曳：分別給左右游標線綁 mousedown
  bindCursorDrag("cursorLeft");
  bindCursorDrag("cursorRight");
}

function setCursorMode(mode) {
  cursorState.mode = mode;
  const liveBtn   = document.getElementById("modeLiveBtn");
  const cursorBtn = document.getElementById("modeCursorBtn");
  const overlay   = document.getElementById("cursorOverlay");
  const info      = document.getElementById("cursorInfo");
  const table     = document.getElementById("readoutTable");

  // 切換按鈕 active 樣式
  if (mode === "live") {
    liveBtn.classList.add("active");
    cursorBtn.classList.remove("active");
    overlay.classList.remove("active");
    info.hidden = true;
    table.classList.remove("cursor-mode");
    // 隱藏游標線
    document.getElementById("cursorLeft").hidden  = true;
    document.getElementById("cursorRight").hidden = true;
    document.getElementById("cursorRange").hidden = true;
    // 取消 pending 的 coverage API
    if (cursorState.coverageTimer) {
      clearTimeout(cursorState.coverageTimer);
      cursorState.coverageTimer = null;
    }
    // 表格立即恢復即時模式（用當前 payload）
    if (chart && chart._lastPayload) {
      rerenderLiveReadout();
    } else {
      updateReadoutTable(null);
    }
  } else {
    cursorBtn.classList.add("active");
    liveBtn.classList.remove("active");
    overlay.classList.add("active");
    info.hidden = false;
    table.classList.add("cursor-mode");
    // 顯示游標線（如尚未初始化則 reset）
    document.getElementById("cursorLeft").hidden  = false;
    document.getElementById("cursorRight").hidden = false;
    document.getElementById("cursorRange").hidden = false;
    if (!cursorState.tsLeft || !cursorState.tsRight) {
      resetCursorPositions();
    } else {
      layoutCursorBars();
      updateCursorInfo();
    }
  }
}

/**
 * 重置游標位置到當前圖表 X 軸範圍的 25% / 75%。
 * 必須在 chart 已 build 且 dataset 至少 1 點後呼叫。
 */
function resetCursorPositions() {
  if (!chart) return;
  const xScale = chart.scales.x;
  if (!xScale) return;
  const min = xScale.min;
  const max = xScale.max;
  if (min == null || max == null || max <= min) return;
  const span = max - min;
  // v6.1 fix: 游標線預設放在 1/3 / 2/3 位置，確保在 X 軸可見範圍內
  cursorState.tsLeft  = new Date(min + span * (1/3));
  cursorState.tsRight = new Date(min + span * (2/3));
  layoutCursorBars();
  updateCursorInfo();
}

/**
 * 把游標線的 CSS left / width 對齊到 chart 的 pixel 座標。
 * 使用 chart 內建 helpers 處理 scale → pixel 換算。
 */
function layoutCursorBars() {
  if (!chart) return;
  const xScale = chart.scales.x;
  if (!xScale) return;
  const chartArea = chart.chartArea;
  if (!chartArea) return;
  const canvasRect = chart.canvas.getBoundingClientRect();
  const leftPx  = xScale.getPixelForValue(cursorState.tsLeft.getTime());
  const rightPx = xScale.getPixelForValue(cursorState.tsRight.getTime());

  // chartArea 在 canvas 內部的偏移；overlay 套在 .chart-area 上，
  // 與 canvas 同位置 (padding 8px)，所以 leftPx 需扣掉 chartArea.left 再加 padding(8)
  const padding = 8;
  const leftCss  = (chartArea.left - padding) + (leftPx  - chartArea.left) + "px";
  const rightCss = (chartArea.left - padding) + (rightPx - chartArea.left) + "px";

  const barL = document.getElementById("cursorLeft");
  const barR = document.getElementById("cursorRight");
  const range = document.getElementById("cursorRange");

  barL.style.left  = leftCss;
  barR.style.left  = rightCss;
  range.style.left  = leftCss;
  range.style.width = (rightPx - leftPx) + "px";

  // 拖曳 line 頂端的時間標籤
  document.getElementById("cursorLeftTime").textContent  = _fmtTs(cursorState.tsLeft);
  document.getElementById("cursorRightTime").textContent = _fmtTs(cursorState.tsRight);
}

function _fmtTs(d) {
  if (!d) return "—";
  const p = n => String(n).padStart(2, "0");
  return `${p(d.getMonth()+1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/**
 * 給兩條游標線綁定拖曳行為。
 * side = 'left' | 'right'
 */
function bindCursorDrag(id) {
  const el = document.getElementById(id);
  const side = el.dataset.side;
  let dragging = false;

  const onDown = (e) => {
    if (cursorState.mode !== "cursor") return;
    dragging = true;
    el.setPointerCapture && el.setPointerCapture(e.pointerId ?? 0);
    e.preventDefault();
  };
  const onMove = (e) => {
    if (!dragging) return;
    if (!chart) return;
    const xScale = chart.scales.x;
    const chartArea = chart.chartArea;
    if (!xScale || !chartArea) return;
    // clientX → 相對 chartArea 的 pixel
    const rect = chart.canvas.getBoundingClientRect();
    const xInChart = e.clientX - rect.left;
    // clamp 到 chartArea
    if (xInChart < chartArea.left) return;  // 交給 layoutCursorBars 邊界
    if (xInChart > chartArea.right) return;
    const ts = xScale.getValueForPixel(xInChart);
    const newTs = new Date(ts);
    if (side === "left") {
      if (newTs >= cursorState.tsRight) return;   // 不超過右線
      cursorState.tsLeft = newTs;
    } else {
      if (newTs <= cursorState.tsLeft) return;    // 不低於左線
      cursorState.tsRight = newTs;
    }
    layoutCursorBars();
    updateCursorInfo();           // v6.1.2: 拖曳時同步更新「區間」資訊
    updateReadoutFromCursor();
    scheduleCoverageRequest();
  };
  const onUp = (e) => {
    dragging = false;
  };

  el.addEventListener("mousedown", onDown);
  el.addEventListener("touchstart", onDown, { passive: false });
  window.addEventListener("mousemove", onMove);
  window.addEventListener("touchmove", onMove, { passive: false });
  window.addEventListener("mouseup", onUp);
  window.addEventListener("touchend", onUp);
}

/**
 * 從 chart dataset 過濾出 [tsLeft, tsRight] 區間內的資料，
 * 依每個 channel 計算 平均 / 最大 / 最小，並更新表格。
 *
 * 分母 = 區間內實際筆數（沿用 v6 avg 原則：分母 = 實際筆數，不補 0）
 *
 * 即使資料筆數 < 理論完整筆數，也照算（這是「量測模式」的語意：
 * 使用者拖曳出來的範圍就是他要看的）。
 */
function updateReadoutFromCursor() {
  if (!chart) return;
  const settings = GX20State.settings;
  const tL = cursorState.tsLeft.getTime();
  const tR = cursorState.tsRight.getTime();

  const tbody = document.querySelector("#readoutTable tbody");
  tbody.innerHTML = "";
  for (let i = 0; i < POINTS; i++) {
    if (!settings.ch_visibility[currentStation][i]) continue;
    const alias = (settings.ch_alias[currentStation][i] || "").trim();
    const fallback = channelNums[currentStation]?.[i] || `Ch${i+1}`;
    const name = alias || fallback;
    const color = settings.ch_color[currentStation][i];

    // 找對應 dataset
    const ds = chart.data.datasets.find(d => d.pointIndex === i);
    let avg = null, max = null, min = null;
    if (ds) {
      const vals = [];
      for (const p of ds.data) {
        if (p.x >= tL && p.x <= tR && p.y !== null && p.y !== undefined && !Number.isNaN(p.y)) {
          vals.push(p.y);
        }
      }
      if (vals.length > 0) {
        avg = vals.reduce((a, b) => a + b, 0) / vals.length;
        max = Math.max(...vals);
        min = Math.min(...vals);
      }
    }
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${i+1}</td>
      <td><span class="swatch" style="background:${color}"></span>${escapeHtml(name)}</td>
      <td>—</td>
      <td>—</td>
      <td class="cell-avg">${fmt(avg, 2)}</td>
    `;
    // 量測模式加 max / min 兩欄需要更動表頭；為了不破壞既有表頭結構，
    // 把 max / min 放進同一個 cell 內以「(max~min)」格式呈現。
    // 但會擠；先維持簡單：另存到 data-* 屬性，後續可擴充。
    tr.dataset.max = max == null ? "" : max.toFixed(2);
    tr.dataset.min = min == null ? "" : min.toFixed(2);
    tr.dataset.avg = avg  == null ? "" : avg.toFixed(2);
    tbody.appendChild(tr);
  }
  // 表頭改為量測模式：把 "讀值/速率/平均" 換成 "平均/最大/最小"
  // 但既有的 th 結構是固定的，這裡直接改 textContent 比較單純。
  document.getElementById("rateTh").textContent = "最大";
  document.getElementById("avgTh").textContent  = "最小";
  // 讀值欄位在量測模式不適用（顯示 —），把第一個資料 td 改為平均，最大/最小填到後兩欄
  // 為簡化，這裡重新組一次 row 結構：
  // 由於前面已建好 tr，重新走一次把 cell 內容對調
  const trs = tbody.querySelectorAll("tr");
  trs.forEach(tr => {
    const tds = tr.querySelectorAll("td");
    if (tds.length < 5) return;
    const avg = tr.dataset.avg;
    const max = tr.dataset.max;
    const min = tr.dataset.min;
    tds[2].textContent = avg ? avg : "—";
    tds[2].className   = "cell-avg";
    tds[3].textContent = max ? max : "—";
    tds[3].className   = "cell-max";
    tds[4].textContent = min ? min : "—";
    tds[4].className   = "cell-min";
  });
}

/**
 * 切回即時模式時，恢復原本的表頭文字。
 */
function restoreReadoutHeaders() {
  const rate = Number(GX20State.settings.rate_window_min) || 5;
  const avg  = Number(GX20State.settings.avg_window_min)  || 10;
  document.getElementById("rateTh").textContent = `速率 (°C/${_fmtMinLabel(rate)})`;
  document.getElementById("avgTh").textContent  = `平均 (°C/${_fmtMinLabel(avg)})`;
}

/**
 * 更新「區間：xx ~ yy (duration)」「資料覆蓋：xx 筆 / 預期 yy 筆 (zz%)」這兩列。
 */
function updateCursorInfo() {
  if (!cursorState.tsLeft || !cursorState.tsRight) return;
  const tL = cursorState.tsLeft;
  const tR = cursorState.tsRight;
  const durSec = Math.max(0, (tR - tL) / 1000);
  const durTxt = _fmtDuration(durSec);
  document.getElementById("cursorInfoRange").textContent =
    `${_fmtTs(tL)} ~ ${_fmtTs(tR)}  (${durTxt})`;

  const cov = document.getElementById("cursorInfoCoverage");
  cov.textContent = "計算中…";
  cov.className = "calculating";
  cov.classList.remove("warn");
}

function _fmtDuration(sec) {
  if (sec < 60) return `${Math.round(sec)} 秒`;
  if (sec < 3600) return `${Math.floor(sec/60)} 分 ${Math.round(sec%60)} 秒`;
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return `${h} 時 ${m} 分`;
}

/**
 * Debounce 300ms 後打 /api/cursor/coverage 更新覆蓋率。
 * 拖曳中不重複打 API，停下 0.3 秒後才送。
 */
function scheduleCoverageRequest() {
  if (!currentStation) return;
  if (cursorState.coverageTimer) {
    clearTimeout(cursorState.coverageTimer);
  }
  cursorState.coverageTimer = setTimeout(async () => {
    cursorState.coverageTimer = null;
    const tL = cursorState.tsLeft.toISOString();
    const tR = cursorState.tsRight.toISOString();
    const cov = document.getElementById("cursorInfoCoverage");
    try {
      const resp = await fetch(`/api/cursor/coverage?station=${encodeURIComponent(currentStation)}&t1=${encodeURIComponent(tL)}&t2=${encodeURIComponent(tR)}`);
      const j = await resp.json();
      if (!j.ok) {
        cov.textContent = "查詢失敗";
        cov.className = "";
        cov.classList.add("warn");
        return;
      }
      cov.textContent = `${j.actual} 筆 / 預期 ${j.expected} 筆 (${j.pct}%)`;
      cov.className = "";
      if (j.pct < 50) cov.classList.add("warn");
    } catch (e) {
      cov.textContent = "查詢失敗";
      cov.className = "";
      cov.classList.add("warn");
    }
  }, CURSOR_DEBOUNCE_MS);
}

// 游標模式與原 onNewSample / updateReadoutTable 的協作：
// 1. onNewSample 內若 cursorState.mode === 'cursor'，就只更新 chart dataset，
//    不更新右側表格（避免平均值/最大值/最小值被即時溫度覆蓋）
// 2. updateReadoutTable 同理：cursor 模式下直接 return
// 3. 切回 live 模式時，setCursorMode() 會用 chart._lastPayload 重畫一次表格
//
// 上面的 guard 邏輯是「補在」原本函式內的，這個檔案原本的 onNewSample /
// updateReadoutTable 已 patch 過（用條件判斷）。本檔末尾不再做 monkey-patch。

// 供 setCursorMode 切回 live 時重畫表格用
function rerenderLiveReadout() {
  if (chart && chart._lastPayload) {
    restoreReadoutHeaders();
    _updateReadoutTableOriginal(chart._lastPayload);
  }
}

// 保留原本 updateReadoutTable 的引用（給 rerenderLiveReadout 用）
// 因為 updateReadoutTable 本身在前面已加上 guard，這裡給它一個「無 guard」版本
function _updateReadoutTableOriginal(payload) {
  if (!payload) return;
  const tbody = document.querySelector("#readoutTable tbody");
  tbody.innerHTML = "";
  const settings = GX20State.settings;
  for (let i = 0; i < POINTS; i++) {
    if (!settings.ch_visibility[currentStation][i]) continue;
    const alias = (settings.ch_alias[currentStation][i] || "").trim();
    const fallback = channelNums[currentStation]?.[i] || `Ch${i+1}`;
    const name = alias || fallback;
    const color = settings.ch_color[currentStation][i];
    const v = payload.temps[i];
    const r = payload.rate ? payload.rate[i] : null;
    const a = payload.avg  ? payload.avg[i]  : null;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${i+1}</td>
      <td><span class="swatch" style="background:${color}"></span>${escapeHtml(name)}</td>
      <td>${fmt(v, 1)}</td>
      <td>${fmtRate(r)}</td>
      <td>${fmt(a, 2)}</td>
    `;
    tbody.appendChild(tr);
  }
}
