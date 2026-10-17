"""Tiny profile app used to demo and test provepr. Contains a planted bug."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

INDEX = Path(__file__).with_name("index.html").read_bytes()
profile = {"name": ""}


class Handler(BaseHTTPRequestHandler):
    def send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self.send(200, INDEX, "text/html; charset=utf-8")
        elif self.path == "/api/profile":
            self.send(200, json.dumps(profile).encode())
        else:
            self.send(404, b'{"error": "not found"}')

    def do_POST(self):
        if self.path != "/api/profile":
            return self.send(404, b'{"error": "not found"}')
        try:
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            profile["name"] = data["nme"]
        except (KeyError, ValueError) as e:
            return self.send(500, json.dumps({"error": repr(e)}).encode())
        self.send(200, json.dumps(profile).encode())

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8765"))
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
