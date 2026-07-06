# -*- coding: utf-8 -*-
"""피키픽시 트렌드 워치 — 로컬 웹서버.

실행:  python3 server.py
브라우저가 자동으로 http://localhost:8787 을 연다.
표준 라이브러리만 사용 (설치 불필요).
"""
import json
import os
import sys
import threading
import urllib.parse
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)  # 어느 위치에서 실행해도 fetch 모듈을 찾도록

import fetch as fetcher
import report as reporter

PORT = 8787
_refresh_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):

    # ---- 공통 응답 헬퍼 ----
    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _file(self, relpath, ctype):
        full = os.path.normpath(os.path.join(BASE_DIR, relpath.lstrip("/")))
        if not full.startswith(BASE_DIR) or not os.path.isfile(full):
            self._json({"error": "not found"}, 404)
            return
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- GET ----
    def do_GET(self):
        path = self.path.split("?")[0]
        path = urllib.parse.unquote(path)
        if path in ("/", "/index.html"):
            self._file("index.html", "text/html; charset=utf-8")
        elif path.startswith("/data/") or path.startswith("/reports/"):
            ctype = ("application/json; charset=utf-8" if path.endswith(".json")
                     else "text/markdown; charset=utf-8")
            self._file(path, ctype)
        elif path == "/api/videos":
            store = fetcher.load_store()
            self._json({
                "last_updated": store.get("last_updated"),
                "videos": list(store.get("videos", {}).values()),
                "instagram_manual": store.get("instagram_manual", []),
            })
        elif path == "/api/config":
            self._json(fetcher.load_config())
        else:
            self._json({"error": "not found"}, 404)

    # ---- POST ----
    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            if path == "/api/refresh":
                if not _refresh_lock.acquire(blocking=False):
                    self._json({"error": "이미 수집이 진행 중입니다"}, 409)
                    return
                try:
                    summary, log = fetcher.refresh()
                finally:
                    _refresh_lock.release()
                self._json({"summary": summary, "log": log})

            elif path == "/api/config":
                body = self._read_body()
                cfg = fetcher.load_config()
                for key in ("keywords", "days_back", "max_per_keyword",
                            "domestic_only", "instagram_auto"):
                    if key in body:
                        cfg[key] = body[key]
                fetcher.save_config(cfg)
                self._json({"ok": True})

            elif path == "/api/report":
                fname = reporter.generate()
                self._json({"ok": True, "file": fname})

            elif path == "/api/video/update":
                # 자동 발굴된 영상(주로 인스타그램)의 조회수/제목 수동 보정
                body = self._read_body()
                store = fetcher.load_store()
                v = store["videos"].get(body.get("id"))
                if not v:
                    self._json({"error": "해당 영상을 찾을 수 없습니다"}, 404)
                    return
                if "views" in body:
                    v["views"] = int(body["views"] or 0)
                if body.get("title"):
                    v["title"] = str(body["title"]).strip()
                if body.get("channel"):
                    v["channel"] = str(body["channel"]).strip()
                fetcher.save_store(store)
                self._json({"ok": True})

            elif path == "/api/video/delete":
                body = self._read_body()
                store = fetcher.load_store()
                store["videos"].pop(body.get("id"), None)
                fetcher.save_store(store)
                self._json({"ok": True})

            elif path == "/api/instagram":
                body = self._read_body()
                url = (body.get("url") or "").strip()
                if not url:
                    self._json({"error": "URL이 필요합니다"}, 400)
                    return
                store = fetcher.load_store()
                entry = {
                    "id": "ig_" + str(abs(hash(url)) % 10**10),
                    "platform": "instagram",
                    "url": url,
                    "title": (body.get("title") or "").strip(),
                    "channel": (body.get("channel") or "").strip(),
                    "views": int(body.get("views") or 0),
                    "memo": (body.get("memo") or "").strip(),
                    "first_seen": datetime.now().strftime("%Y-%m-%d"),
                }
                # 같은 URL이면 갱신
                manual = [e for e in store.get("instagram_manual", [])
                          if e.get("url") != url]
                manual.append(entry)
                store["instagram_manual"] = manual
                fetcher.save_store(store)
                self._json({"ok": True, "entry": entry})

            elif path == "/api/instagram/delete":
                body = self._read_body()
                store = fetcher.load_store()
                store["instagram_manual"] = [
                    e for e in store.get("instagram_manual", [])
                    if e.get("id") != body.get("id")]
                fetcher.save_store(store)
                self._json({"ok": True})

            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def log_message(self, fmt, *args):  # 콘솔 소음 줄이기
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"피키픽시 트렌드 워치 실행 중: {url}  (종료: Ctrl+C)", flush=True)
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")


if __name__ == "__main__":
    main()
