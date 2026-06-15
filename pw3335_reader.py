# -*- coding: utf-8 -*-
"""
pw3335_reader.py
================
GW Instek PW3335 Programmable DC Power Meter 通訊客戶端。

移植自 desktop 版：
  - kalapontsai/GX20-PW3335-Data-Collection/GX20_PW3335.py  (class PW3335)
  - kalapontsai/PW3335/PW3335_0_1_0.py                    (class PW3335, connect/query_data)

通訊協定（與桌面版完全一致）：
  - TCP 連線到 host:port（預設 192.168.1.X:3300）
  - 送出指令:  b':MEAS? U,I,P,WH\\n'
  - 接收回應:  'U +110.14E+0;I +0.0000E+0;P +000.00E+0;WP +00.0000E+0'
                ↑ 電壓 (V)  ↑ 電流 (A)  ↑ 功率 (W)  ↑ 累積 (Wh) ← 不取
  - 用 ';' 切 4 段，每段以 token 開頭 ('U' / 'I' / 'P' / 'WP')
  - 數值格式：[sign]float + 'E[+-]N'  (例如 '+110.14E+0')

與桌面版差異：
  - 網頁版不像桌面版會「持續持有 socket」；這裡的設計是：
      connect() 建立 socket
      query_vip() 發一次命令、取一次回應
      close()      關 socket
    由 app.py 的 poller 決定要 keep-alive 或每次重連（見 pw3335_reader 模組層的 helper）。
    桌面版那種「singleton 物件 + 長時間 keep socket」的設計，在多工位 + 設定可熱改
    的場景下不好管理（連線失敗要 reconnect、port 改了要砍 instance…），
    簡化為「每輪/每次 query 一條 socket」反而更乾淨。
  - 解析失敗時回傳 (0.0, 0.0, 0.0, False)；不丟例外。
    poller 端依此決定要不要記 warning log、要不要標 disconnected。
  - 連線失敗時回傳 (0.0, 0.0, 0.0, False)；呼叫端寫入 0 值。
"""

import logging
import re
import socket
import time
from typing import Optional, Tuple

log = logging.getLogger("pw3335_reader")


# === 預設 / 通訊常數 ===

DEFAULT_PW3335_PORT = 3300
DEFAULT_TIMEOUT_SEC = 2.0
DEFAULT_MAX_RETRIES = 1

# 命令字串（結尾必須有 \\n，儀器端以 LF 為命令分隔）
MEAS_COMMAND = b":MEAS? U,I,P,WH\n"

# 回應解析：
#   範例: 'U +110.14E+0;I +0.0000E+0;P +000.00E+0;WP +00.0000E+0'
#   每段: <TOKEN><space><VALUE>
#   VALUE: [+-]float + 'E' + [+-]int  (例如 +110.14E+0)
# 允許負值 (e.g. 迴灌)、0 值、單位前綴可能有多個空白
_RESP_RE = re.compile(
    r"(?P<key>[A-Z]+)\s+(?P<val>[+-]?\d+(?:\.\d+)?[Ee][+-]?\d+)"
)

# 我們只取前三個 token；WP（累積 Wh）不取
# 順序固定為 U → I → P，跟桌面版解析順序一致
_WANTED_KEYS = ("U", "I", "P")


class PW3335:
    """
    單一 PW3335 的通訊客戶端。

    用法：
        pw = PW3335("192.168.1.2")
        if pw.connect():
            v, i, w, ok = pw.query_vip()
            pw.close()
        else:
            v, i, w, ok = 0.0, 0.0, 0.0, False
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PW3335_PORT,
        timeout: float = DEFAULT_TIMEOUT_SEC,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self._sock: Optional[socket.socket] = None

    # ---------- 連線 ----------

    def connect(self) -> bool:
        """
        建立 TCP 連線（最多重試 max_retries 次）。
        成功回 True；失敗回 False（不丟例外）。
        """
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(self.timeout)
                sock.connect((self.host, self.port))
                self._sock = sock
                if attempt > 0:
                    log.info("PW3335 %s:%d 第 %d 次重試成功", self.host, self.port, attempt + 1)
                return True
            except socket.timeout as e:
                last_err = e
                log.warning("PW3335 %s:%d 連線超時 (attempt %d/%d)",
                            self.host, self.port, attempt + 1, self.max_retries)
            except ConnectionRefusedError as e:
                last_err = e
                log.warning("PW3335 %s:%d 連線被拒 (attempt %d/%d)",
                            self.host, self.port, attempt + 1, self.max_retries)
            except OSError as e:
                # 涵蓋 gaierror、unreachable、其他網路錯誤
                last_err = e
                log.warning("PW3335 %s:%d 網路錯誤 %s (attempt %d/%d)",
                            self.host, self.port, e, attempt + 1, self.max_retries)
            except Exception as e:
                last_err = e
                log.warning("PW3335 %s:%d 未預期錯誤 %s (attempt %d/%d)",
                            self.host, self.port, e, attempt + 1, self.max_retries)
            # 失敗 → 關掉可能半開的 socket，等一下再重試
            if self._sock is not None:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None
            if attempt + 1 < self.max_retries:
                time.sleep(0.5)
        log.error("PW3335 %s:%d 連線失敗（已重試 %d 次）: %s",
                  self.host, self.port, self.max_retries, last_err)
        return False

    def close(self) -> None:
        """關閉 socket。"""
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception as e:
                log.debug("PW3335 %s close() 錯誤: %s", self.host, e)
            finally:
                self._sock = None

    @property
    def connected(self) -> bool:
        return self._sock is not None

    # ---------- 查詢 ----------

    def query_vip(self) -> Tuple[float, float, float, bool]:
        """
        送出 ':MEAS? U,I,P,WH'，解析回應。

        回傳 (V, I, W, ok)：
          - ok=True  → 解析成功，V/I/W 為浮點
          - ok=False → 連線中斷、解析失敗、值無效；V/I/W = 0.0
        """
        if self._sock is None:
            return (0.0, 0.0, 0.0, False)

        try:
            self._sock.sendall(MEAS_COMMAND)
            # 桌面版 recv(1024)，這裡沿用
            data = self._sock.recv(1024).decode("ascii", errors="ignore").strip()
        except (socket.timeout, OSError, ConnectionError) as e:
            log.warning("PW3335 %s query 通訊錯誤: %s", self.host, e)
            # 通訊錯誤 → 視同連線失效，主動關掉讓下次重連
            self.close()
            return (0.0, 0.0, 0.0, False)
        except Exception as e:
            log.warning("PW3335 %s query 未預期錯誤: %s", self.host, e)
            self.close()
            return (0.0, 0.0, 0.0, False)

        v, i, w = self._parse_response(data)
        return (v, i, w, True)

    @staticmethod
    def _parse_response(text: str) -> Tuple[float, float, float]:
        """
        解析儀器回應。

        規則：
          - 用 regex 抓出所有 '<KEY> <VALUE>' 段
          - 取 U / I / P 對應的浮點值
          - 缺段 / 解析失敗 → 該欄視為 0
        """
        vals = {"U": 0.0, "I": 0.0, "P": 0.0}
        for m in _RESP_RE.finditer(text):
            key = m.group("key")
            if key in vals:
                try:
                    vals[key] = float(m.group("val"))
                except (ValueError, TypeError):
                    # 個別欄位解析失敗就保留 0，不擋整筆
                    pass
        return (vals["U"], vals["I"], vals["P"])


# === 模組層 helper：poller 用 ===

def fetch_one_station(
    host: str,
    port: int = DEFAULT_PW3335_PORT,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> Tuple[float, float, float, bool]:
    """
    一次性 read：connect → query → close，全部包起來。
    給 app.py poller 用（每輪/每工位呼叫一次，連線成本可接受）。
    對網頁 poller 10s 週期 + 6 工位 × 1 query 的量而言，
    每次 connect/close 的 latency 是可忽略的（< 50ms 區間）。

    回傳 (V, I, W, ok) — 與 PW3335.query_vip() 同。
    """
    pw = PW3335(host=host, port=port, timeout=timeout)
    if not pw.connect():
        return (0.0, 0.0, 0.0, False)
    try:
        return pw.query_vip()
    finally:
        pw.close()


# === 模組自我測試 ===

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if len(sys.argv) < 2:
        print("用法: python pw3335_reader.py <host> [port]")
        print("範例: python pw3335_reader.py 192.168.1.2 3300")
        sys.exit(1)
    host = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PW3335_PORT
    print(f"讀取 PW3335 {host}:{port} ...")
    v, i, w, ok = fetch_one_station(host, port)
    if ok:
        print(f"  V = {v:.2f} V")
        print(f"  I = {i:.4f} A")
        print(f"  W = {w:.2f} W")
    else:
        print("  讀取失敗")
        sys.exit(2)
