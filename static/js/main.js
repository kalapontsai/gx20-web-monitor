// main.js — 監看主頁邏輯
//
// 設定流程（見 storage.js）：
//   - GX20State.init() 從 server 拉 baseline，再用 sessionStorage 覆蓋
//   - 主頁完全以 sessionStorage 為主；不再跟 server 即時同步
//   - 任何設定改動 → 寫 sessionStorage，dirty 標記
//   - 頂部「保存」按鈕 → POST 到 server
//
// 標題格式：'[工位1]'
// 圖表：Chart.js（y 軸=溫度, y1 軸=速率）
// 即時：Socket.IO

const POINTS = 20;
let currentStation = null;
let chart = null;
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

  sel.addEventListener("change", () => {
    currentStation = sel.value;
    saveSessionExtra({ currentStation });
    document.title = `[${currentStation}]`;
    rebuildChart();
    loadHistory();
    updateReadoutTable(null);
  });

  document.getElementById("clearBtn").addEventListener("click", async () => {
    if (!confirm("確定要清除所有 SQLite 資料？此操作無法復原。")) return;
    await fetch("/api/clear", { method: "POST" });
    rebuildChart();
    updateReadoutTable(null);
  });

  document.getElementById("saveBtn").addEventListener("click", async () => {
    try {
      await GX20State.save();
      alert("已保存");
    } catch (e) {
      alert("保存失敗: " + e.message);
    }
  });

  // 主題切換
  document.getElementById("themeBtn").addEventListener("click", () => {
    const next = GX20State.theme === "dark" ? "light" : "dark";
    GX20State.setTheme(next);
  });

  // SocketIO
  const socket = io();
  socket.on("connect",    () => updateConn(true,  null));
  socket.on("disconnect", () => updateConn(false, "SocketIO 斷線"));
  socket.on("new_sample", onNewSample);

  setInterval(refreshConnStatus, 5000);
  refreshConnStatus();

  buildChart();
  loadHistory();
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

// ---------- Chart ----------

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
      kind: "temp",
    });
    datasets.push({
      label: `${alias} 速率`,
      data: [],
      borderColor: color,
      backgroundColor: color,
      borderWidth: 1,
      borderDash: [6, 4],
      pointRadius: 0,
      tension: 0.15,
      yAxisID: "y1",
      hidden: true,
      pointIndex: i,
      kind: "rate",
    });
    datasets.push({
      label: `${alias} 平均`,
      data: [],
      borderColor: color,
      backgroundColor: color,
      borderWidth: 1,
      borderDash: [2, 3],
      pointRadius: 0,
      tension: 0,
      yAxisID: "y",
      hidden: true,
      pointIndex: i,
      kind: "avg",
    });
  }

  if (chart) chart.destroy();
  const c = chartColors();
  chart = new Chart(ctx, {
    type: "line",
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      parsing: false,
      interaction: { mode: "nearest", intersect: false },
      plugins: {
        legend: { display: true, position: "right", labels: { color: c.text, boxWidth: 10, font: { size: 10 } } },
        tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y}` } },
      },
      scales: {
        x: {
          type: "time",
          time: { tooltipFormat: "yyyy-MM-dd HH:mm:ss", displayFormats: { minute: "HH:mm", hour: "MM-dd HH:mm" } },
          ticks: { color: c.text, maxRotation: 0, autoSkipPadding: 20 },
          grid:  { color: c.grid },
        },
        y: {
          type: "linear",
          position: "left",
          ticks: { color: c.text },
          grid:  { color: c.grid },
          title: { display: true, text: "溫度 (°C)", color: c.text },
        },
        y1: {
          type: "linear",
          position: "right",
          ticks: { color: c.text },
          grid: { drawOnChartArea: false },
          title: { display: true, text: "速率 (°C/min)", color: c.text },
        },
      },
    },
  });

  const titleEl = document.getElementById("chartTitle");
  if (titleEl) titleEl.textContent = `[${currentStation}]`;
}

function rebuildChart() { buildChart(); }

// 切換主題後重畫圖表（換顏色）
window.addEventListener("storage", (e) => {
  if (e.key === SESSION_KEY) {
    // 別的分頁改了，跨分頁暫存不在這用；略
  }
});

// 監聽 body 的 data-theme 屬性變更（自訂事件）→ 重畫
const _obs = new MutationObserver(() => { if (chart) { rebuildChart(); } });
_obs.observe(document.body, { attributes: true, attributeFilter: ["data-theme"] });

// ---------- 拉歷史 ----------

async function loadHistory() {
  try {
    const maxPoints = (GX20State.settings && GX20State.settings.max_points) || DATASET_MAX_POINTS;
    const url = `/api/history/${encodeURIComponent(currentStation)}?max_points=${maxPoints}`;
    const r = await fetch(url);
    const j = await r.json();
    if (!j.ok) return;
    for (const row of j.rows) {
      const ts = new Date(row.ts).getTime();
      for (const ds of chart.data.datasets) {
        const idx = ds.pointIndex;
        const v = row[`t${String(idx+1).padStart(2, "0")}`];
        if (v === null || v === undefined) continue;
        if (ds.kind === "temp") ds.data.push({ x: ts, y: v });
      }
    }
    // 伺服器已降取樣到 max_points；但多個 datasets 共用同一 rows，
    // 若使用者只顯示部分接點，個別 dataset 不會超限，無需前端再降取樣
    chart.update("none");
  } catch (e) {
    console.error("loadHistory:", e);
  }
}

// ---------- SocketIO：即時資料 ----------

function onNewSample(payload) {
  if (payload.station !== currentStation) return;
  const ts = new Date(payload.ts).getTime();
  for (const ds of chart.data.datasets) {
    const idx = ds.pointIndex;
    if (ds.kind === "temp") {
      const v = payload.temps[idx];
      if (v !== null && v !== undefined) ds.data.push({ x: ts, y: v });
    } else if (ds.kind === "rate") {
      const v = payload.rate[idx];
      if (v !== null && v !== undefined) ds.data.push({ x: ts, y: v });
    } else if (ds.kind === "avg") {
      const v = payload.avg[idx];
      if (v !== null && v !== undefined) ds.data.push({ x: ts, y: v });
    }
  }
  pruneOldData();
  chart.update("none");
  document.getElementById("lastTs").textContent = `最後更新: ${payload.ts}`;
  updateReadoutTable(payload);
}

// 從 main.html 引入 lttb.js 後，全域 window.lttb 可用
const DATASET_MAX_POINTS = 2000;

function pruneOldData() {
  // 雙重保護：
  // 1) 先以時間窗裁掉 X 軸外的舊點（避免越來越長）
  // 2) 若仍有 dataset 超過 DATASET_MAX_POINTS，做 LTTB 降取樣
  const winMs = (GX20State.settings.history_minutes || 60) * 60 * 1000;
  const cutoff = Date.now() - winMs;
  for (const ds of chart.data.datasets) {
    // 時間軸裁剪
    while (ds.data.length && ds.data[0].x < cutoff) ds.data.shift();
    // 點數上限保護
    if (ds.data.length > DATASET_MAX_POINTS) {
      ds.data = window.lttb(ds.data, DATASET_MAX_POINTS);
    }
  }
}

// ---------- 右側表格（四個欄位：名稱、讀值、速率、平均） ----------
// 名稱規則：別名優先，別名為空時顯示頻道號

function updateReadoutTable(payload) {
  const tbody = document.querySelector("#readoutTable tbody");
  tbody.innerHTML = "";
  if (!payload) return;
  const settings = GX20State.settings;
  for (let i = 0; i < POINTS; i++) {
    if (!settings.ch_visibility[currentStation][i]) continue;
    const alias = (settings.ch_alias[currentStation][i] || "").trim();
    const fallback = channelNums[currentStation]?.[i] || `Ch${i+1}`;
    const name = alias || fallback;   // 別名優先
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

window.addEventListener("DOMContentLoaded", init);
