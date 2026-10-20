"""App behind a login, used to test provepr's setup, before_each, viewport, secrets and newer steps."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

PASSWORD = "s3cret-pw"
visits = {"n": 0}

LOGIN = b"""<!doctype html><title>Sign in</title><h1>Sign in</h1>
<form method="post" action="/login">
  <label for="u">Email</label><input id="u" name="email">
  <label for="p">Password</label><input id="p" name="password" type="password">
  <button>Sign in</button>
</form>"""

ACCOUNT = """<!doctype html><title>Account</title><meta name="viewport" content="width=device-width">
<style>.mobile-only {{ display: none }} @media (max-width: 600px) {{ .mobile-only {{ display: block }} }}</style>
<h1>Account</h1>
<p>Visits: {visits}</p>
<p class="mobile-only">Mobile layout</p>
<label><input type="checkbox" id="news"> Email me updates</label> <span id="sub"></span>
<button id="help">Help</button><span id="tip" hidden>We never share your email</span>
<label for="avatar">Avatar</label><input id="avatar" type="file"> <span id="picked"></span>
<ul><li class="item">One</li><li class="item">Two</li><li class="item">Three</li></ul>
<script>
  news.onchange = () => sub.textContent = news.checked ? "Subscribed" : "Unsubscribed";
  help.onmouseenter = () => tip.hidden = false;
  avatar.onchange = () => picked.textContent = "Selected: " + avatar.files[0].name;
</script>"""


class Handler(BaseHTTPRequestHandler):
    def send(self, code: int, body: bytes = b"", ctype: str = "text/html; charset=utf-8", headers=()) -> None:
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def signed_in(self) -> bool:
        return "session=ok" in (self.headers.get("Cookie") or "")

    def do_GET(self):
        if self.path == "/login":
            self.send(200, LOGIN)
        elif self.path == "/account":
            if not self.signed_in():
                return self.send(302, headers=[("Location", "/login")])
            visits["n"] += 1
            self.send(200, ACCOUNT.format(visits=visits["n"]).encode())
        else:
            self.send(200, b"ok", "text/plain")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        if self.path == "/login":
            if parse_qs(body).get("password") == [PASSWORD]:
                return self.send(303, headers=[("Location", "/account"), ("Set-Cookie", "session=ok; Path=/")])
            return self.send(200, LOGIN + b"<p>Wrong password</p>")
        if self.path == "/api/reset":
            visits["n"] = 0
            return self.send(200, json.dumps(visits).encode(), "application/json")
        self.send(404)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("PORT", "8766"))), Handler).serve_forever()
