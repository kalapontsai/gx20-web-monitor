// settings.js — 設定頁邏輯
//
// 全部走 GX20State：
//   - 進頁面：GX20State.init()（server + session 合併，主題套用）
//   - 改值：GX20State.update(key, value) → 寫 sessionStorage + 標 dirty
//   - 「保存」：GX20State.save() → POST 到 server
// 站點選擇另外用 TAB_KEY session 暫存
//
// STATIONS 與 POINTS 由 settings.html 內嵌的 <script> 注入到 window.GX20Config

const STATIONS = (window.GX20Config && window.GX20Config.stations) || ["工位1","工位2","工位3","工位4","工位5","工位6"];
const POINTS   = (window.GX20Config && window.GX20Config.points) || 20;
let currentStation = STATIONS[0];
let channelNums = {};
const cp = new ColorPicker();

const TAB_KEY = "gx20.tab_extra.v1";
function loadTabExtra() { try { return JSON.parse(sessionStorage.getItem(TAB_KEY) || "{}"); } catch { return {}; } }
function saveTabExtra(patch) {
  const cur = loadTabExtra();
  sessionStorage.setItem(TAB_KEY, JSON.stringify(Object.assign({}, cur, patch)));
  GX20State.markDirty();
}

async function init() {
  await GX20State.init();
  const settings = GX20State.settings;
  channelNums = await loadChannels();

  // 還原站點
  const tab = loadTabExtra();
  if (tab.currentStation && STATIONS.includes(tab.currentStation)) {
    currentStation = tab.currentStation;
  }

  // 填入基本欄位
  document.getElementById("gx20_host").value        = settings.gx20_host;
  document.getElementById("gx20_port").value        = settings.gx20_port;
  document.getElementById("y_axis_min").value       = settings.y_axis_min;
  document.getElementById("y_axis_max").value       = settings.y_axis_max;
  document.getElementById("retention_days").value   = settings.retention_days;
  document.getElementById("max_points").value       = settings.max_points;

  // 從 server 額外拉 debug flag（GX20State 不會自動合併，獨立處理）
  try {
    const r = await fetch("/api/debug");
    const j = await r.json();
    if (j.ok) {
      document.getElementById("debugLogEnabled").checked = !!j.enabled;
    }
  } catch {}

  // 基本欄位 → GX20State.update
  bindField("gx20_host",       (v) => GX20State.update("gx20_host", v));
  bindField("gx20_port",       (v) => GX20State.update("gx20_port", parseInt(v, 10) || 0));
  bindField("y_axis_min",      (v) => GX20State.update("y_axis_min", parseFloat(v) || 0));
  bindField("y_axis_max",      (v) => GX20State.update("y_axis_max", parseFloat(v) || 0));
  bindField("retention_days",  (v) => GX20State.update("retention_days", Math.max(1, Math.min(30, parseInt(v, 10) || 7))));
  bindField("max_points",      (v) => GX20State.update("max_points", Math.max(200, Math.min(10000, parseInt(v, 10) || 2000))));

  // Debug log 切換 → 直接 POST /api/debug（不透過 GX20State）
  document.getElementById("debugLogEnabled").addEventListener("change", async (e) => {
    const want = e.target.checked;
    try {
      const r = await fetch("/api/debug", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: want }),
      });
      const j = await r.json();
      if (j.ok) {
        alert(want ? "Debug log 已開啟（記錄至 logs/app.log）" : "Debug log 已關閉");
      } else {
        alert("切換失敗: " + (j.error || ""));
        e.target.checked = !want;
      }
    } catch (err) {
      alert("切換失敗: " + err.message);
      e.target.checked = !want;
    }
  });

  // 站點 tab
  const tabs = document.getElementById("stationTabs");
  STATIONS.forEach(s => {
    const b = document.createElement("button");
    b.textContent = s;
    b.dataset.station = s;
    if (s === currentStation) b.classList.add("active");
    b.addEventListener("click", () => switchStation(s));
    tabs.appendChild(b);
  });

  // 全部 toggle
  document.getElementById("allToggle").addEventListener("change", (e) => {
    const on = e.target.checked;
    const vis = GX20State.settings.ch_visibility[currentStation];
    for (let i = 0; i < POINTS; i++) vis[i] = on;
    GX20State.update("ch_visibility", GX20State.settings.ch_visibility);
    document.querySelectorAll(".ch-cell .vis-chk").forEach(c => { c.checked = on; });
    syncAllToggleFromGrid();
  });

  renderChGrid();
  syncAllToggleFromGrid();

  // 頂部按鈕
  document.getElementById("saveBtn").addEventListener("click", async () => {
    try { await GX20State.save(); alert("已保存"); }
    catch (e) { alert("保存失敗: " + e.message); }
  });
  document.getElementById("clearDbBtn").addEventListener("click", async () => {
    if (!confirm("確定清除 SQLite 全部資料？")) return;
    const r = await fetch("/api/clear", { method: "POST" });
    const j = await r.json();
    alert(j.ok ? "已清除" : "失敗");
  });
  document.getElementById("themeBtn").addEventListener("click", () => {
    const next = GX20State.theme === "dark" ? "light" : "dark";
    GX20State.setTheme(next);
  });
}

function bindField(id, cb) {
  const el = document.getElementById(id);
  el.addEventListener("input", () => cb(el.value));
  el.addEventListener("change", () => cb(el.value));
}

async function loadChannels() {
  const r = await fetch("/api/channels");
  const j = await r.json();
  return (j.ok && j.channels) || {};
}

function switchStation(s) {
  currentStation = s;
  saveTabExtra({ currentStation: s });
  document.querySelectorAll("#stationTabs button").forEach(b => {
    b.classList.toggle("active", b.dataset.station === s);
  });
  renderChGrid();
  syncAllToggleFromGrid();
}

function renderChGrid() {
  const grid = document.getElementById("chGrid");
  grid.innerHTML = "";
  const settings = GX20State.settings;
  for (let i = 0; i < POINTS; i++) {
    const cell = document.createElement("div");
    cell.className = "ch-cell";

    // 標題列：#1  0001
    const num = document.createElement("div");
    num.className = "ch-cell-num";
    num.innerHTML = `<span>#${i+1}</span> <span class="ch-num-code">${channelNums[currentStation]?.[i] || ""}</span>`;
    cell.appendChild(num);

    // 顯示/隱藏
    const visLabel = document.createElement("label");
    visLabel.className = "ch-field";
    const visChk = document.createElement("input");
    visChk.type = "checkbox";
    visChk.className = "vis-chk";
    visChk.checked = !!settings.ch_visibility[currentStation][i];
    visChk.addEventListener("change", () => {
      settings.ch_visibility[currentStation][i] = visChk.checked;
      GX20State.update("ch_visibility", settings.ch_visibility);
      syncAllToggleFromGrid();
    });
    visLabel.appendChild(visChk);
    const visTxt = document.createElement("span");
    visTxt.className = "ch-field-label";
    visTxt.textContent = "顯示";
    visLabel.appendChild(visTxt);
    cell.appendChild(visLabel);

    // 別名（明確標籤 + 文字框）
    const aliasWrap = document.createElement("div");
    aliasWrap.className = "ch-field";
    const aliasTxt = document.createElement("span");
    aliasTxt.className = "ch-field-label";
    aliasTxt.textContent = "別名";
    const aliasInp = document.createElement("input");
    aliasInp.type = "text";
    aliasInp.className = "ch-alias-inp";
    aliasInp.value = settings.ch_alias[currentStation][i] || "";
    aliasInp.placeholder = `Ch${i+1}`;
    aliasInp.addEventListener("input", () => {
      settings.ch_alias[currentStation][i] = aliasInp.value;
      GX20State.update("ch_alias", settings.ch_alias);
    });
    aliasWrap.appendChild(aliasTxt);
    aliasWrap.appendChild(aliasInp);
    cell.appendChild(aliasWrap);

    // 顏色（明確標籤 + 按鈕）
    const colorWrap = document.createElement("div");
    colorWrap.className = "ch-field";
    const colorTxt = document.createElement("span");
    colorTxt.className = "ch-field-label";
    colorTxt.textContent = "顏色";
    const colorBtn = document.createElement("button");
    colorBtn.type = "button";
    colorBtn.className = "color-btn";
    colorBtn.title = "點擊選顏色";
    const initColor = settings.ch_color[currentStation][i] || "#888888";
    colorBtn.dataset.color = initColor;
    colorBtn.style.background = initColor;
    cp.attach(colorBtn, initColor, (newColor) => {
      settings.ch_color[currentStation][i] = newColor;
      GX20State.update("ch_color", settings.ch_color);
    });
    colorWrap.appendChild(colorTxt);
    colorWrap.appendChild(colorBtn);
    cell.appendChild(colorWrap);

    grid.appendChild(cell);
  }
}

function syncAllToggleFromGrid() {
  const vis = GX20State.settings.ch_visibility[currentStation];
  const allOn  = vis.every(v => v);
  const allOff = vis.every(v => !v);
  const tog = document.getElementById("allToggle");
  tog.checked = allOn;
  tog.indeterminate = !allOn && !allOff;
}

window.addEventListener("DOMContentLoaded", init);
