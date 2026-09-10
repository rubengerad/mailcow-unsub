"""Minimal HTTP server for RFC 8058 one-click unsubscribe.

Deliberately stdlib-only (http.server) rather than pulling in a web framework
-- this only ever needs to handle two tiny routes.
"""
import logging
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from db import add_unsubscribe, get_connection
from tokens import verify_token

log = logging.getLogger("mailcow-unsub")

CONFIRMATION_HTML = """<!doctype html>
<html><head><title>Unsubscribed</title></head>
<body><p>{email} has been unsubscribed and will not receive further mail.</p></body></html>
"""


def make_handler(secret: str):
    class UnsubscribeHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            log.info("webapp: %s - %s", self.client_address[0], fmt % args)

        def _token_from_query(self):
            query = parse_qs(urlparse(self.path).query)
            return (query.get("token") or [None])[0]

        def _suppress(self, email: str, source: str):
            conn = get_connection()
            try:
                inserted = add_unsubscribe(
                    conn, email=email, source=source, subject=None, message_id=None,
                    received_at=datetime.utcnow(),
                )
                log.info("%s %s via one-click (%s)", email, "suppressed" if inserted else "already suppressed", source)
            finally:
                conn.close()

        def do_GET(self):
            if not urlparse(self.path).path.startswith("/unsubscribe"):
                self.send_response(404)
                self.end_headers()
                return

            token = self._token_from_query()
            email = verify_token(token, secret) if token else None
            if not email:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Invalid or expired unsubscribe link.")
                return

            self._suppress(email, source="one-click-get")
            body = CONFIRMATION_HTML.format(email=email).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            # RFC 8058 one-click: mailbox providers POST here with no user
            # interaction when a recipient clicks their client's native
            # "Unsubscribe" button. No confirmation page -- just a 200.
            if not urlparse(self.path).path.startswith("/unsubscribe"):
                self.send_response(404)
                self.end_headers()
                return

            token = self._token_from_query()
            email = verify_token(token, secret) if token else None
            if not email:
                self.send_response(400)
                self.end_headers()
                return

            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode(errors="replace") if length else ""
            if "List-Unsubscribe=One-Click" not in body:
                self.send_response(400)
                self.end_headers()
                return

            self._suppress(email, source="one-click-post")
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    return UnsubscribeHandler


def run(port: int, secret: str) -> None:
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(secret))
    log.info("one-click unsubscribe HTTP server listening on :%d", port)
    server.serve_forever()
