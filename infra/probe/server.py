"""Disposable infrastructure probe. Replace it after challenge/stack selection."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

REVISION = os.environ.get("APP_REVISION", "local")
PAGE = Path(__file__).with_name("page.html").read_bytes()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/healthz":
            payload = json.dumps({"status": "ok", "revision": REVISION}).encode()
            self.respond(200, "application/json", payload)
        elif path == "/":
            self.respond(200, "text/html; charset=utf-8", PAGE)
        else:
            self.respond(404, "text/plain; charset=utf-8", b"Not found\n")

    def do_HEAD(self):
        self.do_GET()

    def respond(self, status, content_type, payload):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)


if __name__ == "__main__":
    server = ThreadingHTTPServer(
        (os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", "3000"))),
        Handler,
    )
    print(f"Infrastructure probe on {server.server_address}; revision={REVISION}", flush=True)
    server.serve_forever()
