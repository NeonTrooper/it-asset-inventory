#!/usr/bin/env python3
"""
app.py — the web server of the IT asset inventory.

Run:   python3 app.py          then open http://127.0.0.1:8000

It does two jobs:
  1. serves the page (the files in the static/ folder)
  2. answers the page's requests on /api/... with JSON

    GET    /api/assets?q=&status=&type=   list (with filters)
    POST   /api/assets                    create
    PUT    /api/assets/<id>               update
    DELETE /api/assets/<id>               delete
    GET    /api/assets/<id>/history       audit trail of one asset
    GET    /api/summary                   dashboard numbers

Only the Python standard library is used: no packages to install.
"""
import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import db

STATIC = Path(__file__).with_name("static")
# The only files the server will ever send. A fixed list means a crafted URL
# (like /../../etc/passwd) cannot make it read anything else.
PAGES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
MAX_BODY = 10000          # bytes; an asset is tiny, so refuse anything bigger
ASSET_URL = re.compile(r"^/api/assets/(\d+)$")
HISTORY_URL = re.compile(r"^/api/assets/(\d+)/history$")


def make_handler(conn):
    lock = threading.Lock()   # one request at a time uses the database connection

    class Handler(BaseHTTPRequestHandler):
        server_version = "AssetInventory/1.0"

        # ---------- helpers ----------
        def send_json(self, status, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def read_json(self):
            """Read the request body as JSON. Returns None (after answering) if it is unusable."""
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                self.send_json(400, {"errors": ["Request body is missing or too large."]})
                return None
            try:
                data = json.loads(self.rfile.read(length))
            except ValueError:
                data = None
            if not isinstance(data, dict):
                self.send_json(400, {"errors": ["Request body must be a JSON object."]})
                return None
            return data

        def log_message(self, fmt, *args):   # keep the terminal quiet during tests
            if not getattr(self.server, "quiet", False):
                super().log_message(fmt, *args)

        # ---------- routes ----------
        def do_GET(self):
            url = urlparse(self.path)
            if url.path in PAGES:
                name, content_type = PAGES[url.path]
                body = (STATIC / name).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)
                return
            with lock:
                if url.path == "/api/assets":
                    q = parse_qs(url.query)
                    first = lambda key: q.get(key, [""])[0]
                    self.send_json(200, db.list_assets(conn, first("q"), first("status"), first("type")))
                elif url.path == "/api/summary":
                    self.send_json(200, db.summary(conn))
                elif HISTORY_URL.match(url.path):
                    asset_id = int(HISTORY_URL.match(url.path).group(1))
                    if db.get_asset(conn, asset_id) is None:
                        self.send_json(404, {"errors": ["Asset not found."]})
                    else:
                        self.send_json(200, db.asset_history(conn, asset_id))
                else:
                    self.send_json(404, {"errors": ["Not found."]})

        def do_POST(self):
            if urlparse(self.path).path != "/api/assets":
                return self.send_json(404, {"errors": ["Not found."]})
            data = self.read_json()
            if data is None:
                return
            with lock:
                try:
                    self.send_json(201, db.create_asset(conn, data))
                except db.ValidationError as error:
                    self.send_json(400, {"errors": error.errors})

        def do_PUT(self):
            match = ASSET_URL.match(urlparse(self.path).path)
            if not match:
                return self.send_json(404, {"errors": ["Not found."]})
            data = self.read_json()
            if data is None:
                return
            with lock:
                try:
                    asset = db.update_asset(conn, int(match.group(1)), data)
                except db.ValidationError as error:
                    return self.send_json(400, {"errors": error.errors})
            if asset is None:
                self.send_json(404, {"errors": ["Asset not found."]})
            else:
                self.send_json(200, asset)

        def do_DELETE(self):
            match = ASSET_URL.match(urlparse(self.path).path)
            if not match:
                return self.send_json(404, {"errors": ["Not found."]})
            with lock:
                deleted = db.delete_asset(conn, int(match.group(1)))
            if deleted:
                self.send_json(200, {"deleted": True})
            else:
                self.send_json(404, {"errors": ["Asset not found."]})

    return Handler


def make_server(conn, host="127.0.0.1", port=8000):
    return ThreadingHTTPServer((host, port), make_handler(conn))


def main():
    parser = argparse.ArgumentParser(description="IT asset inventory web app")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default="inventory.db", help="path of the SQLite database file")
    parser.add_argument("--no-sample-data", action="store_true", help="start with an empty inventory")
    args = parser.parse_args()

    conn = db.connect(args.db)
    if not args.no_sample_data:
        db.seed(conn)
    # 127.0.0.1 = reachable from this computer only. There is no login, so the
    # app is deliberately not exposed to the network.
    server = make_server(conn, port=args.port)
    print("IT asset inventory running at http://127.0.0.1:%d  (Ctrl+C to stop)" % args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
