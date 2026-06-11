# -*- coding: utf-8 -*-
"""
ota_push.py
===========
OTA 推送 CLI 工具（給開發者 / agent 用）。

用法：
  1) 推單檔
     python ota_push.py push <host> <local_file> <target> [--restart]
       例：python ota_push.py push <DEPLOY_HOST>:5000 ./static/js/main.js static/js/main.js

  2) 推多檔（manifest 模式）
     python ota_push.py bundle <host> <manifest.json> [--restart]
       manifest.json 範例：
         {
           "files": [
             {"local": "./static/js/main.js",       "target": "static/js/main.js"},
             {"local": "./templates/index.html",     "target": "templates/index.html"}
           ],
           "restart": true
         }

  3) 觸發重啟
     python ota_push.py restart <host>

  4) 查狀態
     python ota_push.py status <host>

Token 來源（依序）：
  1) --token 命令列參數
  2) 環境變數 GX20_OTA_TOKEN
  3) ./ota_token 本地檔（與部署目錄同名 token 拷貝過來的）
"""

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    import urllib.request
    import urllib.error


# ---------- Token 取得 ----------

def resolve_token(args) -> str:
    tok = args.token or os.environ.get("GX20_OTA_TOKEN")
    if tok:
        return tok
    local = Path(__file__).parent / "config" / "ota_token"
    if local.exists():
        return local.read_text(encoding="utf-8").strip()
    print("ERROR: 找不到 OTA token，請用 --token 或設 GX20_OTA_TOKEN", file=sys.stderr)
    sys.exit(2)


# ---------- HTTP 抽象層（相容 urllib） ----------

class HttpClient:
    def __init__(self, base_url: str, token: str):
        self.base = base_url.rstrip("/")
        self.token = token
        self.headers = {"X-OTA-Token": token}

    def get(self, path: str) -> dict:
        url = f"{self.base}{path}"
        if HAS_REQUESTS:
            r = requests.get(url, timeout=10)
            return r.json()
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def post_json(self, path: str, body: dict) -> tuple:
        url = f"{self.base}{path}"
        if HAS_REQUESTS:
            r = requests.post(url, json=body, headers=self.headers, timeout=15)
            return r.status_code, r.json()
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={**self.headers, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                return e.code, json.loads(body)
            except Exception:
                return e.code, {"raw": body}

    def post_multipart(self, path: str, file_path: str, target: str) -> tuple:
        url = f"{self.base}{path}"
        if HAS_REQUESTS:
            with open(file_path, "rb") as f:
                files = {"file": (os.path.basename(file_path), f, "application/octet-stream")}
                data = {"target": target}
                r = requests.post(url, files=files, data=data, headers=self.headers, timeout=30)
                return r.status_code, r.json()
        # urllib fallback（簡化版）
        import uuid
        boundary = f"----ota{uuid.uuid4().hex}"
        with open(file_path, "rb") as f:
            file_content = f.read()
        body = []
        body.append(f"--{boundary}\r\n".encode())
        body.append(f'Content-Disposition: form-data; name="target"\r\n\r\n'.encode())
        body.append(target.encode() + b"\r\n")
        body.append(f"--{boundary}\r\n".encode())
        body.append(
            f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(file_path)}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n".encode()
        )
        body.append(file_content)
        body.append(b"\r\n")
        body.append(f"--{boundary}--\r\n".encode())
        payload = b"".join(body)
        req = urllib.request.Request(
            url, data=payload,
            headers={
                **self.headers,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                return e.code, json.loads(body)
            except Exception:
                return e.code, {"raw": body}


# ---------- 子命令 ----------

def cmd_status(args):
    cli = HttpClient(args.host, resolve_token(args))
    print(f"GET {args.host}/api/admin/status …")
    r = cli.get("/api/admin/status")
    print(json.dumps(r, ensure_ascii=False, indent=2))


def cmd_push(args):
    cli = HttpClient(args.host, resolve_token(args))
    print(f"POST {args.host}/api/admin/ota  target={args.target}  file={args.local}")
    status_code, r = cli.post_multipart("/api/admin/ota", args.local, args.target)
    print(f"  HTTP {status_code}")
    print(json.dumps(r, ensure_ascii=False, indent=2))
    if args.restart and r.get("ok"):
        print("\n觸發重啟…")
        time.sleep(0.5)
        cmd_restart_inner(cli)


def cmd_bundle(args):
    cli = HttpClient(args.host, resolve_token(args))
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    files = []
    for entry in manifest.get("files", []):
        local = entry["local"]
        target = entry["target"]
        if not Path(local).exists():
            print(f"ERROR: 找不到本地檔 {local}", file=sys.stderr)
            sys.exit(3)
        content = Path(local).read_bytes()
        files.append({"target": target, "content_b64": base64.b64encode(content).decode("ascii")})
        print(f"  + {local} → {target}  ({len(content)} bytes)")
    body = {"files": files, "restart": bool(manifest.get("restart") or args.restart)}
    print(f"\nPOST {args.host}/api/admin/ota_bundle  ({len(files)} 檔)")
    status_code, r = cli.post_json("/api/admin/ota_bundle", body)
    print(f"  HTTP {status_code}")
    print(json.dumps(r, ensure_ascii=False, indent=2))
    saved = r.get("saved_count", 0)
    if saved and (manifest.get("restart") or args.restart):
        print("\n觸發重啟…")
        time.sleep(0.5)
        cmd_restart_inner(cli)


def cmd_restart_inner(cli: HttpClient):
    status_code, r = cli.post_json("/api/admin/restart", {"delay": 2})
    print(f"  HTTP {status_code}")
    print(json.dumps(r, ensure_ascii=False, indent=2))


def cmd_restart(args):
    cli = HttpClient(args.host, resolve_token(args))
    print(f"POST {args.host}/api/admin/restart")
    cmd_restart_inner(cli)


# ---------- main ----------

def main():
    p = argparse.ArgumentParser(description="GX20 OTA 推送工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("host", help="目標主機，例如 http://<DEPLOY_HOST>:5000")
    common.add_argument("--token", help="OTA token（亦可用 GX20_OTA_TOKEN 環境變數）")

    sp = sub.add_parser("status", parents=[common], help="查 OTA 狀態")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("push", parents=[common], help="推單檔")
    sp.add_argument("local", help="本地檔案路徑")
    sp.add_argument("target", help="目標路徑（相對於部署目錄）")
    sp.add_argument("--restart", action="store_true", help="推完自動重啟")
    sp.set_defaults(func=cmd_push)

    sp = sub.add_parser("bundle", parents=[common], help="推多檔（manifest）")
    sp.add_argument("manifest", help="manifest.json 路徑")
    sp.add_argument("--restart", action="store_true", help="推完自動重啟")
    sp.set_defaults(func=cmd_bundle)

    sp = sub.add_parser("restart", parents=[common], help="觸發遠端重啟")
    sp.set_defaults(func=cmd_restart)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
