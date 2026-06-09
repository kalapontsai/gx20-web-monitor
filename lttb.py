# -*- coding: utf-8 -*-
"""
lttb.py
=======
LTTB (Largest Triangle Three Buckets) 時間序列降取樣。

參考：
  - Sveinn Steinarsson, "Downsampling Time Series for Visual Representation"
    (MSc thesis, University of Iceland, 2013)
  - https://github.com/sveinn-steinarsson/flot-downsample

演算法概念：
  把 N 筆資料切成 `threshold` 個 bucket，每個 bucket 挑 1 點代表（面積最大）。
  第一點與最後一點永遠保留。視覺上能保留峰谷。
"""

from typing import List, Sequence, Tuple


def lttb_xy(
    xs: Sequence[float],
    ys: Sequence[float],
    threshold: int,
) -> Tuple[List[float], List[float]]:
    """
    對 (x, y) 序列做 LTTB 降取樣到 `threshold` 點。
    若 len <= threshold 或 threshold < 3 → 原樣回傳。
    """
    n = len(xs)
    if n != len(ys):
        raise ValueError(f"xs 與 ys 長度不一致: {len(xs)} vs {len(ys)}")
    if threshold >= n or threshold < 3:
        return list(xs), list(ys)

    # 強制轉 float，否則 numpy 算面積會出包
    xs = [float(x) for x in xs]
    ys = [float(y) for y in ys]

    sampled_x: List[float] = [xs[0]]
    sampled_y: List[float] = [ys[0]]

    # 桶大小 = (n - 2) / (threshold - 2)
    bucket_size = (n - 2) / (threshold - 2)
    a_idx = 0  # 上一個被選中的點 index（最初為第 0 點）

    for i in range(threshold - 2):
        # 計算「下一個 bucket」的平均點（用於面積三角形的第三點）
        next_bucket_start = int((i + 1) * bucket_size) + 1
        next_bucket_end   = int((i + 2) * bucket_size) + 1
        next_bucket_end   = min(next_bucket_end, n)
        next_bucket_start = min(next_bucket_start, next_bucket_end)

        avg_x = 0.0
        avg_y = 0.0
        rng = next_bucket_end - next_bucket_start
        if rng == 0:
            # 邊界情況：用最後一點
            avg_x = xs[-1]
            avg_y = ys[-1]
        else:
            for k in range(next_bucket_start, next_bucket_end):
                avg_x += xs[k]
                avg_y += ys[k]
            avg_x /= rng
            avg_y /= rng

        # 在當前 bucket 內找「與上一選中點 + 下個 bucket 平均點構成最大三角形」的那一點
        bucket_start = int(i * bucket_size) + 1
        bucket_end   = int((i + 1) * bucket_size) + 1
        bucket_end   = min(bucket_end, n)

        ax = xs[a_idx]
        ay = ys[a_idx]

        max_area = -1.0
        max_idx  = bucket_start
        for k in range(bucket_start, bucket_end):
            # 三角形面積公式（不取絕對值，僅需相對比較）
            area = abs(
                (ax - avg_x) * (ys[k] - ay) -
                (ax - xs[k]) * (avg_y - ay)
            )
            if area > max_area:
                max_area = area
                max_idx  = k

        sampled_x.append(xs[max_idx])
        sampled_y.append(ys[max_idx])
        a_idx = max_idx

    # 最後一點永遠保留
    sampled_x.append(xs[-1])
    sampled_y.append(ys[-1])

    return sampled_x, sampled_y


def downsample_rows(
    rows: List[dict],
    ts_key: str,
    point_keys: List[str],
    threshold: int,
) -> List[dict]:
    """
    對 list of dict（從 SQLite 拉出的 row）做 LTTB 降取樣。
    以 ts 為主軸切桶，從每個 bucket 選 1 個具代表性的 row。
    每個欄位（point_keys）以原 row 值帶回。
    """
    if not rows:
        return []
    if len(rows) <= threshold or threshold < 3:
        return rows

    from datetime import datetime
    xs_num: List[float] = []
    for r in rows:
        try:
            xs_num.append(datetime.fromisoformat(r[ts_key]).timestamp())
        except Exception:
            xs_num.append(0.0)
    ys_dummy: List[float] = [float(i) for i in range(len(rows))]

    # 跑 LTTB，但 y 是 dummy → 結果的 y 值其實就是「被選中的原始 index」
    _, kept_indices = lttb_xy(xs_num, ys_dummy, threshold)
    kept_set = sorted({int(i) for i in kept_indices})
    return [rows[i] for i in kept_set]
