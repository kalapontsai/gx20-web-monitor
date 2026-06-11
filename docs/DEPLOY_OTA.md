# 部署 + OTA 使用手冊

> 對象：<DEPLOY_PATH_OLD>\（<DEPLOY_HOST> 部署端）
> 維護者：二寶（agent）
> 設計：本地無 OneDrive，部署端與開發端用 OTA 通道同步

---

## 0. 環境概要

| 角色 | 主機 | 工作目錄 | 備註 |
|---|---|---|---|
| 開發 | WSL (D:\OneDrive\...) | <DEV_PATH>\ | 我改檔的起點 |
| 部署 | Windows <DEPLOY_HOST> | <DEPLOY_PATH_OLD>\ | 跑 python app.py |
| 同步通道 | **OTA** | HTTP POST /api/admin/* | 帶 token 認證 |

---

## 1. 一次性部署（明天上班）

部署 v4 + OTA 模組到 <DEPLOY_HOST>。**只需做一次**，之後我用 OTA 推送所有更新。

### 1.1 確認當前 v3 服務已停

在 <DEPLOY_HOST> 的 cmd 視窗 Ctrl+C 關掉 Flask。

### 1.2 複製檔案

把開發端這 6 個檔案覆蓋到部署端 `<DEPLOY_PATH_OLD>\`：

```
app.py
config.py
ota.py              ← 新增（OTA 模組）
ota_push.py         ← 新增（CLI 推送工具）
ota_watchdog.bat    ← 新增（重啟 watch dog）
storage.py          ← 沒改，但一併同步確保版本一致
```

最快的做法 — 在 <DEPLOY_HOST> 的 cmd 跑：

```cmd
:: 假設開發端路徑可達（透過網路芳鄰 / share），否則由大大用隨身碟 / OneDrive 手動搬
copy /Y "\\<WSL_HOST>\<DEV_PATH>\app.py"       "<DEPLOY_PATH_OLD>\app.py"
copy /Y "\\<WSL_HOST>\<DEV_PATH>\config.py"    "<DEPLOY_PATH_OLD>\config.py"
copy /Y "\\<WSL_HOST>\<DEV_PATH>\ota.py"      "<DEPLOY_PATH_OLD>\ota.py"
copy /Y "\\<WSL_HOST>\<DEV_PATH>\ota_push.py" "<DEPLOY_PATH_OLD>\ota_push.py"
copy /Y "\\<WSL_HOST>\<DEV_PATH>\ota_watchdog.bat" "<DEPLOY_PATH_OLD>\ota_watchdog.bat"
copy /Y "\\<WSL_HOST>\<DEV_PATH>\storage.py"  "<DEPLOY_PATH_OLD>\storage.py"
```

> 若 WSL 路徑無法直接存取，就用隨身碟 / OneDrive 同步後手動 copy。
> 反正這只是**一次性**。

### 1.3 確認前端檔案也更新

v4 前端修正在 `static/js/main.js`，需要確認部署端有最新版。如果沒有，把開發端的 `static/js/main.js` 也覆蓋過去：

```cmd
copy /Y "X:\path\static\js\main.js"  "<DEPLOY_PATH_OLD>\static\js\main.js"
```

### 1.4 啟動 v4（用 watch dog 包住）

```cmd
cd <DEPLOY_PATH_OLD>
ota_watchdog.bat
```

這會打開一個視窗跑 `python app.py`，Flask 崩潰或被 OTA 重啟時自動再起。

### 1.5 拿回 OTA token

Flask 第一次啟動時，OTA 模組會自動產生 token 寫到 `<DEPLOY_PATH_OLD>\config\ota_token`。

```cmd
type <DEPLOY_PATH_OLD>\config\ota_token
```

把這串 token **私訊給二寶**（我）。我會寫到開發端的 `config/ota_token`，之後 `ota_push.py` 就能用它推檔。

> ⚠ **Token 不可用 Telegram 明文傳**（雖說是授權頻道，但怕被截圖/匯出）。建議用以下任一管道：
> - 直接登入 <DEPLOY_HOST> 跑 `type` 命令時螢幕截圖（token 自己看）
> - 或把 token 寫到 `<DEPLOY_PATH_OLD>\ota_token_sync.txt`，**只允許我透過 OTA 通道讀取**
>
> **務實方案**：我直接做「OTA 推檔時自動從部署端抓 token」流程（見 §3.3 進階）

### 1.6 驗證 OTA

```bash
# 在 WSL 端跑（要有 ota_push.py + token）
python3 ota_push.py status http://<DEPLOY_HOST>:5000
```

預期看到：
```json
{
  "ok": true,
  "ota_version": 1,
  "token_source": "file",
  "token_fingerprint": "xxxxxxxx",
  "app_root": "<DEPLOY_PATH_OLD>",
  ...
}
```

---

## 2. OTA 推送流程（日常）

### 2.1 改完檔後 — 推單檔

```bash
python3 ota_push.py push http://<DEPLOY_HOST>:5000 \
    ./static/js/main.js \
    static/js/main.js \
    --restart
```

行為：
1. POST `/api/admin/ota` 把 `main.js` 上傳到部署端（自動備份原檔到 `config/ota_backup/<ts>/`）
2. 呼叫 `/api/admin/restart` → 部署端 Flask 排程 2 秒後自我重啟
3. 新版上線，watch dog 視窗會看到 Flask 自己重啟

### 2.2 改多檔 — 用 manifest

寫一個 `ota_manifest.json`：
```json
{
  "files": [
    {"local": "./static/js/main.js",        "target": "static/js/main.js"},
    {"local": "./static/css/style.css",     "target": "static/css/style.css"},
    {"local": "./templates/index.html",     "target": "templates/index.html"}
  ],
  "restart": true
}
```

```bash
python3 ota_push.py bundle http://<DEPLOY_HOST>:5000 ota_manifest.json
```

行為：
1. 把所有檔案打包 base64 → 一次 POST `/api/admin/ota_bundle`
2. 全部成功才觸發重啟

### 2.3 只重啟不推檔

```bash
python3 ota_push.py restart http://<DEPLOY_HOST>:5000
```

### 2.4 Token 怎麼給 ota_push.py

依序找：
1. `--token XXX` 參數
2. 環境變數 `GX20_OTA_TOKEN`
3. 開發端 `config/ota_token` 檔（建議固定放這）

```bash
# 永久設定（Linux/WSL）
echo "TOKEN_HERE" > config/ota_token
chmod 600 config/ota_token
```

---

## 3. 已知限制與處理

### 3.1 部署端沒有 OneDrive

✅ 這就是 OTA 通道存在的目的 — 我用 HTTP 推檔，不依賴雲端同步。

### 3.2 Token 同步問題

開發端跟部署端 token **理論上應該一致**，否則推送會 401。

**簡單做法**：把 token 寫到 `config/ota_token`（只放開發端）+ 部署端（部署時手動 copy）。兩端都用同一個 token，**並且**部署端設環境變數 `GX20_OTA_TOKEN` 覆寫（萬一檔案 token 失效還有備援）。

**進階做法**：Token 握手流程（見下）

### 3.3 進階：Token 自動握手（規劃中）

`ota_push.py` 在第一次推送時，若開發端沒 token，會：
1. 呼叫 `/api/admin/status?force_token_init=1` → 部署端自動產生 token 並回傳
2. 開發端把 token 存到 `config/ota_token`
3. 之後推送都帶這個 token

**風險**：第一次握手時若部署端有設 token 環境變數，新產的 token 會跟環境變數不一致 → 推送失敗

**緩解**：用一次性 challenge-response（部署端回傳 challenge，開發端加密回應），但這會增加複雜度。

**務實建議**：直接手動 copy token，反正一學期才做一次。

### 3.4 重啟時連線短暫中斷

OTA 重啟流程：Flask 排程 2 秒後退出 → 部署端 `python app.py` 重新啟動 → 對外服務中斷約 5~8 秒

前端會看到 socket 斷線 → 重連。**所有用 `onNewSample` 寫入 ring buffer 的邏輯會暫停**，但 SQLite 寫入不影響（poller 跟 Flask 一起重啟，重啟後會從 GX20 重新讀）。

如要無感重啟，進階做法是加 hot reload（Flask debug mode），但會犧牲效能。

### 3.5 寫入失敗處理

`atomic_write` 先寫 `.tmp` 再 rename — **檔案要嘛是舊版，要嘛是新版，不會半套**。

寫入失敗（磁碟滿、權限錯、白名單擋下）會回 400，**不會觸發重啟**（避免一壞全壞）。

寫入前會自動備份到 `config/ota_backup/<timestamp>/<rel>`，可隨時手動還原。

---

## 4. 安全注意事項

| 項目 | 說明 |
|---|---|
| Token 強度 | 32 byte 隨機 base64url（43 字符），相當於 256 bit entropy |
| Token 存放 | 檔案設 0600（Linux/WSL 有效，Windows 忽略但 NTFS ACL 仍擋其他使用者） |
| Token 傳輸 | HTTPS（若部署端有反向代理） / 內網 HTTP（<DEPLOY_HOST> 屬內網） |
| 白名單 | 嚴格限縮路徑前綴 + 副檔名（拒絕 .pyc / .dll / .bat / .sh） |
| 路徑穿越 | 雙重檢查（is_allowed_target + resolve_target） |
| Rate limit | **無** — 內網用，暫不考慮；若日後暴露外網建議加 IP 白名單 + 每分鐘 60 次上限 |

---

## 5. Rollback 流程

如果 OTA 推完新版出問題：

```bash
# 1. 看最近備份
ls config/ota_backup/ | tail -5

# 2. 用 OTA 推舊版回去
python3 ota_push.py push http://<DEPLOY_HOST>:5000 \
    config/ota_backup/20260611_180000/static/js/main.js \
    static/js/main.js \
    --restart
```

或最快：把整個 `config/ota_backup/<ts>/` 內容 zip，下載到 <DEPLOY_HOST> 解壓覆蓋，重啟 Flask。

---

## 6. 自我監控建議（可選）

部署後建議設個 daily cron：

```python
# 每天早上 8:00 跑一次健康檢查
0 8 * * * python3 ota_push.py status http://<DEPLOY_HOST>:5000 | grep '"ok": true' || echo "OTA 服務異常！"
```

若 token 過期或服務掛了，可以提早發現。
