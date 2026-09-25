#!/usr/bin/env python3
"""
Marks the calculator the way the assignment says it will be marked:
one socket, every request, then check the socket is still open.

Author : Hemang (Roll No. 24bcs10209)

Usage:  python3 mark.py [host] [port]          (default localhost 8080)
        python3 mark.py --pipeline             (send all six in one write)
"""

import socket
import sys

CASES = [
    ("GET",  "/add?a=2&b=3",  200, "5"),
    ("GET",  "/sub?a=10&b=4", 200, "6"),
    ("GET",  "/mul?a=6&b=7",  200, "42"),
    ("GET",  "/div?a=1&b=0",  400, None),
    ("GET",  "/pow?a=2&b=8",  404, None),
    ("POST", "/add",          405, None),
]


class Reader:
    """Reads HTTP/1.1 responses off one socket using Content-Length framing."""

    def __init__(self, sock):
        self.sock = sock
        self.buf = b""

    def _need(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("server closed the socket")
            self.buf += chunk

    def response(self):
        while b"\r\n\r\n" not in self.buf:
            self._need(len(self.buf) + 1)
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        status = int(lines[0].split(" ")[1])
        headers = {}
        for line in lines[1:]:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
        n = int(headers["content-length"])
        self._need(n)
        body, self.buf = self.buf[:n], self.buf[n:]
        return status, headers, body.decode().strip()


def still_open(sock):
    """True if the peer has not sent FIN/RST. Peeks without consuming."""
    sock.setblocking(False)
    try:
        return sock.recv(1, socket.MSG_PEEK) != b""
    except BlockingIOError:
        return True          # nothing to read, but not closed
    except OSError:
        return False
    finally:
        sock.setblocking(True)


def request(method, target, host):
    extra = "Content-Length: 0\r\n" if method == "POST" else ""
    return ("%s %s HTTP/1.1\r\nHost: %s\r\n%s\r\n"
            % (method, target, host, extra)).encode()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pipeline = "--pipeline" in sys.argv
    host = args[0] if args else "localhost"
    port = int(args[1]) if len(args) > 1 else 8080

    s = socket.create_connection((host, port))      # the one and only handshake
    handshakes = 1
    local = s.getsockname()
    print("s = socket.create_connection((%r, %d))   # local port %d"
          % (host, port, local[1]))
    print("mode:", "pipelined (all six in one write)" if pipeline
          else "sequential (request, wait, request, ...)")
    print()
    reader = Reader(s)

    if pipeline:
        s.sendall(b"".join(request(m, t, host) for m, t, _, _ in CASES))

    passed = 0
    responses = 0
    for method, target, want_status, want_body in CASES:
        if not pipeline:
            s.sendall(request(method, target, host))
        status, headers, body = reader.response()
        responses += 1
        ok = status == want_status and (want_body is None or body == want_body)
        passed += ok
        shown = body if status == 200 else ""
        print("  %-4s %-16s -> %d  %-4s %s"
              % (method, target, status, shown, "PASS" if ok else "FAIL"))

    open_now = still_open(s)
    print()
    print("socket still open: %s" % open_now)
    print("%d TCP handshake, %d responses" % (handshakes, responses))
    print("score: %d/%d" % (passed + open_now, len(CASES) + 1))
    s.close()
    sys.exit(0 if passed == len(CASES) and open_now else 1)


if __name__ == "__main__":
    main()
