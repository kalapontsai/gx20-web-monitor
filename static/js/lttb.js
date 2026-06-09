// lttb.js — Largest Triangle Three Buckets 降取樣
//
// 對 {x, y} 陣列做降取樣，保留視覺上重要的峰谷。
// 演算法細節見 lttb.py。

(function (window) {
  "use strict";

  function lttb(data, threshold) {
    if (!Array.isArray(data)) return data;
    const n = data.length;
    if (threshold >= n || threshold < 3) return data.slice();

    const sampled = new Array(threshold);
    let sampledIdx = 0;
    sampled[sampledIdx++] = data[0];

    const bucketSize = (n - 2) / (threshold - 2);

    let aIdx = 0;  // 上一個被選中點在原陣列的 index

    for (let i = 0; i < threshold - 2; i++) {
      // 計算「下個 bucket」的平均點
      let nextStart = Math.floor((i + 1) * bucketSize) + 1;
      let nextEnd   = Math.floor((i + 2) * bucketSize) + 1;
      if (nextEnd > n) nextEnd = n;
      if (nextStart >= nextEnd) nextStart = nextEnd - 1;
      if (nextStart < 0) nextStart = 0;

      const nextRange = nextEnd - nextStart;
      let avgX = 0, avgY = 0;
      for (let k = nextStart; k < nextEnd; k++) {
        avgX += data[k].x;
        avgY += data[k].y;
      }
      avgX /= nextRange;
      avgY /= nextRange;

      // 當前 bucket
      const bucketStart = Math.floor(i * bucketSize) + 1;
      const bucketEnd   = Math.floor((i + 1) * bucketSize) + 1;
      const aX = data[aIdx].x;
      const aY = data[aIdx].y;

      let maxArea = -1;
      let maxIdx  = bucketStart;
      for (let k = bucketStart; k < bucketEnd; k++) {
        const area = Math.abs(
          (aX - avgX) * (data[k].y - aY) -
          (aX - data[k].x) * (avgY - aY)
        );
        if (area > maxArea) {
          maxArea = area;
          maxIdx  = k;
        }
      }

      sampled[sampledIdx++] = data[maxIdx];
      aIdx = maxIdx;
    }

    sampled[sampledIdx] = data[n - 1];
    return sampled;
  }

  window.lttb = lttb;
})(window);
