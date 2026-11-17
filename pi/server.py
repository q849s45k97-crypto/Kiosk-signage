#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kiosk Signage – Raspberry Pi 用ローカルサーバー（Python 3.7 以降・標準ライブラリのみ）

  http://<PiのIP>:8080/         … サイネージ表示（Pi の Chromium が全画面で開く）
  http://<PiのIP>:8080/remote   … スマホ用の操作画面
  http://<PiのIP>:8080/?mode=editor … 詳細設定（レイアウト・スケジュール等）

データはすべて Pi の中（既定: ~/.local/share/kiosk-signage）に保存されます。
環境変数:
  SIGNAGE_PORT  待ち受けポート（既定 8080）
  SIGNAGE_DATA  データ保存先フォルダ
  SIGNAGE_PIN   操作用PIN（空ならPINなし。data/config.json の "pin" でも可）
"""
import json
import os
import queue
import re
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.abspath(os.path.join(BASE, ".."))
DATA = os.path.expanduser(os.environ.get("SIGNAGE_DATA", "~/.local/share/kiosk-signage"))
MEDIA = os.path.join(DATA, "media")
STATE_FILE = os.path.join(DATA, "state.json")
CONFIG_FILE = os.path.join(DATA, "config.json")
PORT = int(os.environ.get("SIGNAGE_PORT", "8080"))
os.makedirs(MEDIA, exist_ok=True)

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/remote": ("remote.html", "text/html; charset=utf-8"),
    "/remote.html": ("remote.html", "text/html; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/maker": ("maker.html", "text/html; charset=utf-8"),
    "/maker.html": ("maker.html", "text/html; charset=utf-8"),
    "/maker.webmanifest": ("maker.webmanifest", "application/manifest+json"),
}
MEDIA_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

lock = threading.Lock()
clients = []          # SSE 接続ごとの queue
status = {"at": 0}    # プレイヤーから報告された再生状態


# ---------------- storage ----------------
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


state = load_json(STATE_FILE, {"rev": 0, "settings": None, "items": None})


def get_pin():
    return os.environ.get("SIGNAGE_PIN") or load_json(CONFIG_FILE, {}).get("pin", "")


# ---------------- events (Server-Sent Events) ----------------
def broadcast(event, data):
    msg = "event: %s\ndata: %s\n\n" % (event, json.dumps(data, ensure_ascii=False))
    with lock:
        for q in list(clients):
            try:
                q.put_nowait(msg)
            except Exception:
                pass


def lan_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        out = subprocess.check_output(["hostname", "-I"], timeout=2).decode().split()
        ips.update(x for x in out if "." in x)
    except Exception:
        pass
    ips.discard("127.0.0.1")
    return sorted(ips)


# ---------------- HTTP ----------------
class Handler(BaseHTTPRequestHandler):
    server_version = "KioskSignage/1.0"

    def log_message(self, fmt, *args):  # 通常のアクセスログは出さない
        pass

    # --- helpers ---
    def is_local(self):
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def authorized(self):
        pin = get_pin()
        return (not pin) or self.is_local() or self.headers.get("X-Pin", "") == pin

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def deny(self):
        self.send_json({"error": "PINが違います"}, 401)

    def read_body(self, limit=20 * 1024 * 1024):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ValueError("too large")
        return self.rfile.read(n) if n else b""

    # --- GET ---
    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        if p in STATIC:
            return self.send_static(*STATIC[p])
        if p.startswith("/icons/") and re.match(r"^/icons/[A-Za-z0-9_.-]+\.png$", p):
            return self.send_static(p.lstrip("/"), "image/png")
        if p == "/api/info":
            return self.send_json({"ips": lan_ips(), "hostname": socket.gethostname(), "port": PORT,
                                   "pinRequired": bool(get_pin())})
        if p == "/api/state":
            with lock:
                return self.send_json(state)
        if p == "/api/status":
            with lock:
                s = dict(status)
            s["age"] = time.time() - s.get("at", 0)
            return self.send_json(s)
        if p == "/api/events":
            return self.sse()
        if p.startswith("/media/"):
            return self.send_media(p[len("/media/"):])
        self.send_error(404)

    def send_static(self, rel, ctype):
        path = os.path.join(WEB, rel)
        if not os.path.isfile(path):
            return self.send_error(404)
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def send_media(self, mid):
        if not MEDIA_ID.match(mid):
            return self.send_error(404)
        path = os.path.join(MEDIA, mid)
        if not os.path.isfile(path):
            return self.send_error(404)
        meta = load_json(path + ".json", {})
        size = os.path.getsize(path)
        start, end, code = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if m and size:
            s, e = m.groups()
            if s == "":
                start = max(0, size - int(e or 0))
            else:
                start = int(s)
                end = min(int(e), size - 1) if e else size - 1
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.end_headers()
                return
            code = 206
        length = end - start + 1 if size else 0
        self.send_response(code)
        self.send_header("Content-Type", meta.get("type") or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.send_header("X-Media-Name", quote(meta.get("name", "")))
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        try:
            with open(path, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(262144, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = queue.Queue(maxsize=200)
        with lock:
            clients.append(q)
            first = dict(status)
        try:
            first["age"] = time.time() - first.get("at", 0)
            self.wfile.write(("retry: 2000\nevent: status\ndata: %s\n\n" % json.dumps(first, ensure_ascii=False)).encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=15)
                except queue.Empty:
                    msg = ": ping\n\n"
                self.wfile.write(msg.encode("utf-8"))
                self.wfile.flush()
        except Exception:
            pass
        finally:
            with lock:
                if q in clients:
                    clients.remove(q)

    # --- PUT ---
    def do_PUT(self):
        u = urlparse(self.path)
        if u.path == "/api/state":
            if not self.authorized():
                return self.deny()
            try:
                d = json.loads(self.read_body().decode("utf-8"))
            except Exception:
                return self.send_json({"error": "bad json"}, 400)
            with lock:
                state["settings"] = d.get("settings")
                state["items"] = d.get("items")
                state["rev"] = int(state.get("rev", 0)) + 1
                save_json(STATE_FILE, state)
                rev = state["rev"]
            broadcast("state", {"rev": rev, "origin": d.get("origin", "")})
            return self.send_json({"ok": True, "rev": rev})
        if u.path == "/api/media":
            if not self.authorized():
                return self.deny()
            q = parse_qs(u.query)
            mid = (q.get("id") or [""])[0]
            if not MEDIA_ID.match(mid):
                return self.send_json({"error": "bad id"}, 400)
            n = int(self.headers.get("Content-Length") or 0)
            path = os.path.join(MEDIA, mid)
            tmp = path + ".part"
            try:
                with open(tmp, "wb") as f:
                    left = n
                    while left > 0:
                        chunk = self.rfile.read(min(262144, left))
                        if not chunk:
                            raise IOError("upload interrupted")
                        f.write(chunk)
                        left -= len(chunk)
                os.replace(tmp, path)
            except Exception as e:
                try:
                    os.remove(tmp)
                except Exception:
                    pass
                return self.send_json({"error": str(e)}, 500)
            save_json(path + ".json", {"name": (q.get("name") or [""])[0], "type": (q.get("type") or [""])[0], "size": n})
            return self.send_json({"ok": True, "id": mid, "size": n})
        self.send_error(404)

    # --- POST ---
    def do_POST(self):
        p = urlparse(self.path).path
        if p == "/api/status":  # プレイヤーからの状態報告（読み取り専用の情報なので PIN 不要）
            try:
                d = json.loads(self.read_body().decode("utf-8"))
            except Exception:
                d = {}
            d["at"] = time.time()
            with lock:
                status.clear()
                status.update(d)
            d = dict(d)
            d["age"] = 0
            broadcast("status", d)
            return self.send_json({"ok": True})
        if p == "/api/auth":
            return self.send_json({"ok": self.authorized()}, 200 if self.authorized() else 401)
        if p == "/api/cmd":
            if not self.authorized():
                return self.deny()
            try:
                d = json.loads(self.read_body().decode("utf-8"))
            except Exception:
                return self.send_json({"error": "bad json"}, 400)
            if d.get("cmd") not in ("next", "prev", "pause", "resume", "toggle", "reload", "goto", "black", "unblack"):
                return self.send_json({"error": "unknown cmd"}, 400)
            broadcast("cmd", d)
            return self.send_json({"ok": True})
        self.send_error(404)

    # --- DELETE ---
    def do_DELETE(self):
        p = urlparse(self.path).path
        if p.startswith("/api/media/"):
            if not self.authorized():
                return self.deny()
            mid = p[len("/api/media/"):]
            if not MEDIA_ID.match(mid):
                return self.send_json({"error": "bad id"}, 400)
            for f in (mid, mid + ".json"):
                try:
                    os.remove(os.path.join(MEDIA, f))
                except FileNotFoundError:
                    pass
            return self.send_json({"ok": True})
        self.send_error(404)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    srv = Server(("0.0.0.0", PORT), Handler)
    ips = lan_ips()
    print("Kiosk Signage server: http://localhost:%d/" % PORT, flush=True)
    for ip in ips:
        print("  スマホから操作: http://%s:%d/remote" % (ip, PORT), flush=True)
    print("  データ保存先: %s" % DATA, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
