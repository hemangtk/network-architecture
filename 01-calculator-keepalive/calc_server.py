#!/usr/bin/env python3
"""
A calculator that stays on the line -- HTTP/1.1 keep-alive over a raw socket.

Author : Hemang (Roll No. 24bcs10209)
Course : Network Architecture -- Early HTTP/1.1 assignment

No framework, no http.server: just `socket`. The interesting part is framing:
with the connection kept open, a request ends where its headers end (CRLFCRLF)
plus exactly Content-Length body bytes (or the terminating 0-length chunk for
chunked bodies). Anything after that belongs to the *next* request, so it stays
in the buffer.

    GET /add?a=2&b=3   -> 200  5
    GET /sub?a=10&b=4  -> 200  6
    GET /mul?a=6&b=7   -> 200  42
    GET /div?a=9&b=3   -> 200  3
    GET /div?a=1&b=0   -> 400
    GET /add?a=x&b=3   -> 400
    GET /pow?a=2&b=8   -> 404
    POST /add          -> 405
    GET /add (no Host) -> 400

Stretch goals implemented:
    * Connection: close is honoured (and HTTP/1.0 closes unless keep-alive)
    * idle timeout (IDLE_TIMEOUT seconds with no bytes -> close)
    * chunked request bodies are decoded (so the next request is found)
    * pipelining: several requests in one write are answered in order

Usage:  python3 calc_server.py [port]        (default 8080)
"""

import os
import socket
import sys
import threading
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, unquote

HOST = "0.0.0.0"
PORT = 8080

# Why 15 s: long enough for a human typing in `nc` or a test harness pausing
# between requests, short enough that an abandoned client does not pin a thread
# forever. Browsers keep idle connections ~60-300 s; nginx defaults to 75 s --
# for a toy server with one thread per connection, shorter is the safer side.
IDLE_TIMEOUT = int(os.environ.get("CALC_IDLE_TIMEOUT", "15"))

MAX_HEADER_BYTES = 8192        # request line + headers; more than this -> 431
MAX_BODY_BYTES = 1 << 20       # 1 MiB; we never need a body, but must consume it

OPS = {
    "/add": lambda a, b: a + b,
    "/sub": lambda a, b: a - b,
    "/mul": lambda a, b: a * b,
    "/div": lambda a, b: a / b,
}

REASONS = {
    200: "OK",
    400: "Bad Request",
    404: "Not Found",
    405: "Method Not Allowed",
    408: "Request Timeout",
    411: "Length Required",
    413: "Payload Too Large",
    431: "Request Header Fields Too Large",
    501: "Not Implemented",
    505: "HTTP Version Not Supported",
}


class FramingError(Exception):
    """The byte stream can no longer be split into requests; must close."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class Connection:
    """Owns one client socket and the bytes read from it but not yet used."""

    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        self.buf = b""
        self.sock.settimeout(IDLE_TIMEOUT)

    # -- reading -----------------------------------------------------------

    def _fill(self):
        """Read more bytes into the buffer. Returns False on EOF."""
        chunk = self.sock.recv(65536)
        if not chunk:
            return False
        self.buf += chunk
        return True

    def read_until(self, marker, limit, status):
        """Return bytes up to and including `marker`; leave the rest buffered."""
        while True:
            idx = self.buf.find(marker)
            if idx != -1:
                end = idx + len(marker)
                data, self.buf = self.buf[:end], self.buf[end:]
                return data
            if len(self.buf) > limit:
                raise FramingError(status, "header section too large")
            if not self._fill():
                if self.buf:
                    raise FramingError(400, "connection closed mid-request")
                return None

    def read_exact(self, n):
        """Return exactly n bytes -- never n+1, byte n+1 is the next request."""
        while len(self.buf) < n:
            if not self._fill():
                raise FramingError(400, "connection closed mid-body")
        data, self.buf = self.buf[:n], self.buf[n:]
        return data

    def read_chunked(self):
        """Decode a chunked body (RFC 9112 section 7.1); discard trailers."""
        body = b""
        while True:
            line = self.read_until(b"\r\n", 1024, 400)
            if line is None:
                raise FramingError(400, "connection closed mid-chunk")
            size_txt = line[:-2].split(b";", 1)[0].strip()   # drop chunk-ext
            try:
                size = int(size_txt, 16)
            except ValueError:
                raise FramingError(400, "bad chunk size")
            if size == 0:
                # trailer section: header lines until an empty line
                while True:
                    trailer = self.read_until(b"\r\n", MAX_HEADER_BYTES, 431)
                    if trailer is None or trailer == b"\r\n":
                        return body
            if len(body) + size > MAX_BODY_BYTES:
                raise FramingError(413, "chunked body too large")
            body += self.read_exact(size)
            if self.read_exact(2) != b"\r\n":
                raise FramingError(400, "chunk not followed by CRLF")

    # -- parsing -----------------------------------------------------------

    def read_request(self):
        """Parse one request off the stream. None means a clean EOF."""
        # Tolerate stray CRLFs between requests (RFC 9112 section 2.2).
        while self.buf.startswith(b"\r\n"):
            self.buf = self.buf[2:]

        head = self.read_until(b"\r\n\r\n", MAX_HEADER_BYTES, 431)
        if head is None:
            return None

        lines = head[:-4].decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3:
            raise FramingError(400, "malformed request line")
        method, target, version = parts
        if not version.startswith("HTTP/1."):
            raise FramingError(505, "only HTTP/1.x is spoken here")

        headers = {}
        for line in lines[1:]:
            name, sep, value = line.partition(":")
            if not sep or not name or name != name.strip():
                raise FramingError(400, "malformed header line")
            key = name.lower()
            value = value.strip()
            if key in headers:
                if key in ("host", "content-length"):
                    # Two Hosts or two lengths is how request smuggling starts.
                    raise FramingError(400, "duplicate " + key)
                headers[key] += ", " + value
            else:
                headers[key] = value

        # Body framing. Transfer-Encoding wins over Content-Length, and
        # having both is rejected outright (smuggling defence).
        te = headers.get("transfer-encoding", "").lower()
        if te and "content-length" in headers:
            raise FramingError(400, "both Transfer-Encoding and Content-Length")
        if te:
            if te != "chunked":
                raise FramingError(501, "unsupported transfer-encoding")
            body = self.read_chunked()
        elif "content-length" in headers:
            cl = headers["content-length"]
            if not cl or any(c not in "0123456789" for c in cl):
                raise FramingError(400, "bad Content-Length")
            if int(cl) > MAX_BODY_BYTES:
                raise FramingError(413, "body too large")
            body = self.read_exact(int(cl))
        else:
            body = b""

        return method, target, version, headers, body

    # -- writing -----------------------------------------------------------

    def send(self, status, body, keep_alive, extra=()):
        payload = (body + "\n").encode()
        head = [
            "HTTP/1.1 %d %s" % (status, REASONS[status]),
            "Content-Type: text/plain; charset=utf-8",
            "Content-Length: %d" % len(payload),
            "Connection: %s" % ("keep-alive" if keep_alive else "close"),
        ]
        if keep_alive:
            head.append("Keep-Alive: timeout=%d" % IDLE_TIMEOUT)
        head.extend(extra)
        self.sock.sendall(("\r\n".join(head) + "\r\n\r\n").encode() + payload)

    # -- main loop ---------------------------------------------------------

    def serve(self):
        served = 0
        try:
            while True:
                try:
                    req = self.read_request()
                except socket.timeout:
                    log(self.addr, "idle %ds -> closing after %d requests"
                        % (IDLE_TIMEOUT, served))
                    return
                except FramingError as err:
                    # We no longer know where the next request starts.
                    self.send(err.status, err.message, keep_alive=False)
                    log(self.addr, "%d %s -> closing" % (err.status, err.message))
                    return
                if req is None:
                    log(self.addr, "client closed after %d requests" % served)
                    return

                method, target, version, headers, _body = req
                keep_alive = wants_keep_alive(version, headers)
                status, body, extra = route(method, target, version, headers)
                self.send(status, body, keep_alive, extra)
                served += 1
                log(self.addr, "#%d %s %s -> %d %s"
                    % (served, method, target, status, body))
                if not keep_alive:
                    return
        except (ConnectionResetError, BrokenPipeError):
            log(self.addr, "connection reset by peer")
        finally:
            self.sock.close()


def wants_keep_alive(version, headers):
    tokens = [t.strip().lower() for t in headers.get("connection", "").split(",")]
    if "close" in tokens:
        return False
    if version == "HTTP/1.0":
        return "keep-alive" in tokens
    return True


def parse_number(raw):
    try:
        n = Decimal(raw)
    except InvalidOperation:
        return None
    return n if n.is_finite() else None


def fmt(n):
    """10 -> '10', 2.5 -> '2.5', 1/3 -> '0.3333333333333333333333333333'"""
    return format(n.normalize(), "f")


def route(method, target, version, headers):
    """Return (status, body, extra_headers). Never touches the socket."""
    if version == "HTTP/1.1" and "host" not in headers:
        return 400, "missing Host header", ()

    path, _, query = target.partition("?")
    path = unquote(path)
    if path not in OPS:
        return 404, "no such operation: " + path, ()
    if method not in ("GET", "HEAD"):
        return 405, method + " not allowed, use GET", ("Allow: GET, HEAD",)

    params = parse_qs(query, keep_blank_values=True)
    if "a" not in params or "b" not in params:
        return 400, "need both a and b", ()
    if len(params["a"]) > 1 or len(params["b"]) > 1:
        return 400, "a and b must appear once", ()
    a, b = parse_number(params["a"][0]), parse_number(params["b"][0])
    if a is None or b is None:
        return 400, "a and b must be numbers", ()
    if max(abs(a.adjusted()), abs(b.adjusted())) > 100:
        return 400, "numbers must be within 1e-100 .. 1e100", ()
    if path == "/div" and b == 0:
        return 400, "division by zero", ()
    return 200, fmt(OPS[path](a, b)), ()


_log_lock = threading.Lock()


def log(addr, msg):
    with _log_lock:
        print("[%s:%d] %s" % (addr[0], addr[1], msg), flush=True)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, port))
    srv.listen(64)
    print("calc server (24bcs10209) listening on :%d, idle timeout %ds"
          % (port, IDLE_TIMEOUT), flush=True)
    try:
        while True:
            sock, addr = srv.accept()
            log(addr, "accepted (TCP handshake)")
            threading.Thread(target=Connection(sock, addr).serve,
                             daemon=True).start()
    except KeyboardInterrupt:
        pass
    finally:
        srv.close()


if __name__ == "__main__":
    main()
