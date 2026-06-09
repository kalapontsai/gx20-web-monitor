// colorpicker.js — 256 色盤
//
// 256 = 216 web-safe (6×6×6 RGB cube) + 40 灰階
// 用法：
//   const cp = new ColorPicker();
//   cp.attach(buttonEl, initialColor, (newColor) => { ... });
//
// 修正重點：
//   - 不依賴 document 級 click listener 判斷「點外面」；
//     改用 capture phase 監聽，避免跟 swatch 的 stopPropagation 打架。
//   - 開啟新 popover 前一定先解掉舊的 listener / 移除 DOM。
//   - 點 swatch 時 onPickCb 一定會被觸發，且能連續換色（不關閉再重開）。

(function (window) {
  "use strict";

  const RGB_STEPS = [0x00, 0x33, 0x66, 0x99, 0xCC, 0xFF];
  const WEB_SAFE = [];
  for (const r of RGB_STEPS) for (const g of RGB_STEPS) for (const b of RGB_STEPS) {
    const hex = "#" + [r, g, b].map(x => x.toString(16).padStart(2, "0")).join("");
    WEB_SAFE.push(hex);
  }
  const GRAYS = [];
  for (let i = 0; i < 40; i++) {
    const v = Math.round((i / 39) * 255);
    const hex = "#" + v.toString(16).padStart(2, "0").repeat(3);
    GRAYS.push(hex);
  }
  const ALL = Array.from(new Set([...WEB_SAFE, ...GRAYS])).slice(0, 256);
  while (ALL.length < 256) ALL.push("#000000");

  class ColorPicker {
    constructor() {
      this.popover = null;
      this.onPickCb = null;
      this.currentBtn = null;
      this._onDocClick = null;        // capture-phase 監聽器
      this._onDocKey = null;          // ESC 關閉
    }

    attach(btnEl, initialColor, onPick) {
      btnEl.style.background = initialColor || "#000000";
      btnEl.dataset.color = initialColor || "#000000";
      btnEl.addEventListener("click", (e) => {
        // 阻止冒泡到 document 級的 outside-click 監聽
        e.stopPropagation();
        e.preventDefault();
        if (this.popover && this.currentBtn === btnEl) {
          this.close();
        } else {
          this.open(btnEl, onPick);
        }
      });
    }

    open(btnEl, onPick) {
      this.close();
      this.currentBtn = btnEl;
      this.onPickCb = onPick;

      const pop = document.createElement("div");
      pop.className = "color-popover";

      // 216 web-safe
      for (const c of WEB_SAFE) {
        const s = this._makeSwatch(c);
        pop.appendChild(s);
      }
      // 灰階
      const gr = document.createElement("div");
      gr.className = "gray-row";
      for (const c of GRAYS) {
        const s = this._makeSwatch(c);
        gr.appendChild(s);
      }
      pop.appendChild(gr);

      document.body.appendChild(pop);

      // 定位：相對 btn
      const r = btnEl.getBoundingClientRect();
      // 防止超出右邊界
      const popW = pop.offsetWidth || 280;
      const winW = window.innerWidth;
      let left = window.scrollX + r.left;
      if (left + popW > window.scrollX + winW) {
        left = window.scrollX + winW - popW - 8;
      }
      pop.style.left = Math.max(8, left) + "px";
      pop.style.top  = (window.scrollY + r.bottom + 4) + "px";

      this.popover = pop;

      // capture-phase 監聽，統一處理「點外面」與「點 swatch」
      this._onDocClick = (e) => {
        // 點在 popover 內（任何 swatch / 灰階 / 容器）→ 由 swatch 自己處理
        if (pop.contains(e.target)) return;
        // 點在原本的按鈕上 → toggle 關閉（btn 自己的 click 已經 stopPropagation，
        // 所以這裡只在「按鈕以外的元素」觸發時才視為 outside）
        if (btnEl.contains(e.target)) return;
        this.close();
      };
      document.addEventListener("click", this._onDocClick, true);   // capture

      // ESC 關閉
      this._onDocKey = (e) => {
        if (e.key === "Escape") this.close();
      };
      document.addEventListener("keydown", this._onDocKey);
    }

    _makeSwatch(color) {
      const s = document.createElement("div");
      s.className = "swatch";
      s.style.background = color;
      s.dataset.color = color;
      s.title = color;
      s.addEventListener("click", (e) => {
        e.stopPropagation();   // 阻止冒泡到 document 的 outside 監聽
        e.preventDefault();
        this.pick(color);
      });
      return s;
    }

    pick(color) {
      if (this.currentBtn) {
        this.currentBtn.style.background = color;
        this.currentBtn.dataset.color = color;
      }
      if (this.onPickCb) this.onPickCb(color);
      // 不 close — 連續選色
    }

    close() {
      if (this.popover) {
        this.popover.remove();
        this.popover = null;
      }
      if (this._onDocClick) {
        document.removeEventListener("click", this._onDocClick, true);
        this._onDocClick = null;
      }
      if (this._onDocKey) {
        document.removeEventListener("keydown", this._onDocKey);
        this._onDocKey = null;
      }
      this.currentBtn = null;
      this.onPickCb = null;
    }
  }

  window.ColorPicker = ColorPicker;
  window.PALETTE_SIZE = WEB_SAFE.length + GRAYS.length;
})(window);
