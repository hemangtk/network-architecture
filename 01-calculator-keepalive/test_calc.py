#!/usr/bin/env python3
"""
End-to-end tests for calc_server.py. Each test talks to a real server process
over a real TCP socket.

Author : Hemang (Roll No. 24bcs10209)

Run:  python3 -m unittest -v test_calc.py
"""

import os
import socket
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mark import Reader, still_open  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(target, host=True, extra=""):
    h = "Host: localhost\r\n" if host else ""
    return ("GET %s HTTP/1.1\r\n%s%s\r\n" % (target, h, extra)).encode()


class CalcServerTest(unittest.TestCase):
    IDLE_TIMEOUT = "15"

    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.proc = subprocess.Popen(
            [sys.executable, os.path.join(HERE, "calc_server.py"), str(cls.port)],
            env=dict(os.environ, CALC_IDLE_TIMEOUT=cls.IDLE_TIMEOUT),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", cls.port)).close()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait()

    def connect(self):
        s = socket.create_connection(("127.0.0.1", self.port))
        s.settimeout(5)
        self.addCleanup(s.close)
        return s, Reader(s)

    def ask(self, s, r, raw):
        s.sendall(raw)
        return r.response()

    # -- the feature set from the assignment -----------------------------

    def test_feature_table_on_one_socket(self):
        s, r = self.connect()
        table = [
            (get("/add?a=2&b=3"), 200, "5"),
            (get("/sub?a=10&b=4"), 200, "6"),
            (get("/mul?a=6&b=7"), 200, "42"),
            (get("/div?a=9&b=3"), 200, "3"),
            (get("/div?a=1&b=0"), 400, None),
            (get("/add?a=x&b=3"), 400, None),
            (get("/pow?a=2&b=8"), 404, None),
            (b"POST /add HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n", 405, None),
            (get("/add?a=2&b=3", host=False), 400, None),
        ]
        for raw, status, body in table:
            with self.subTest(raw=raw):
                got_status, headers, got_body = self.ask(s, r, raw)
                self.assertEqual(got_status, status)
                if body is not None:
                    self.assertEqual(got_body, body)
                self.assertEqual(headers["connection"], "keep-alive")
        self.assertTrue(still_open(s), "server hung up on a keep-alive client")

    def test_405_advertises_allow(self):
        s, r = self.connect()
        _, headers, _ = self.ask(s, r, b"DELETE /add HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertEqual(headers["allow"], "GET, HEAD")

    def test_decimals_and_negatives(self):
        s, r = self.connect()
        self.assertEqual(self.ask(s, r, get("/div?a=7&b=2"))[2], "3.5")
        self.assertEqual(self.ask(s, r, get("/sub?a=3&b=10"))[2], "-7")
        self.assertEqual(self.ask(s, r, get("/mul?a=0.1&b=3"))[2], "0.3")
        self.assertEqual(self.ask(s, r, get("/add?a=1e3&b=1"))[2], "1001")
        self.assertEqual(self.ask(s, r, get("/add?a=nan&b=1"))[0], 400)
        self.assertEqual(self.ask(s, r, get("/add?a=1"))[0], 400)
        self.assertEqual(self.ask(s, r, get("/mul?a=1e999999&b=1e999999"))[0], 400)

    # -- framing: where does one request end and the next begin? ----------

    def test_body_bytes_are_not_mistaken_for_a_request(self):
        # The POST body *looks* like a request. If the server reads it as one,
        # we would get two responses; we must get exactly one 405 and then the
        # real next request's answer.
        s, r = self.connect()
        evil = b"GET /mul?a=6&b=7 HTTP/1.1\r\nHost: x\r\n\r\n"
        post = (b"POST /add HTTP/1.1\r\nHost: x\r\nContent-Length: %d\r\n\r\n"
                % len(evil)) + evil
        s.sendall(post + get("/add?a=2&b=3"))
        self.assertEqual(r.response()[0], 405)
        status, _, body = r.response()
        self.assertEqual((status, body), (200, "5"))

    def test_chunked_request_body_is_consumed_exactly(self):
        s, r = self.connect()
        post = (b"POST /add HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n"
                b"5\r\nhello\r\n"
                b"6;ext=1\r\n world\r\n"
                b"0\r\nX-Trailer: yes\r\n\r\n")
        s.sendall(post + get("/sub?a=10&b=4"))
        self.assertEqual(r.response()[0], 405)
        self.assertEqual(r.response()[2], "6")
        self.assertTrue(still_open(s))

    def test_request_dribbled_one_byte_at_a_time(self):
        s, r = self.connect()
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        for byte in get("/mul?a=6&b=7"):
            s.send(bytes([byte]))
            time.sleep(0.002)
        self.assertEqual(r.response()[2], "42")

    def test_pipelining_six_at_once_answered_in_order(self):
        s, r = self.connect()
        targets = ["/add?a=2&b=3", "/sub?a=10&b=4", "/mul?a=6&b=7",
                   "/div?a=1&b=0", "/pow?a=2&b=8", "/div?a=9&b=3"]
        s.sendall(b"".join(get(t) for t in targets))
        got = [r.response()[0:3:2] for _ in targets]
        self.assertEqual(got, [(200, "5"), (200, "6"), (200, "42"),
                               (400, "division by zero"),
                               (404, "no such operation: /pow"), (200, "3")])
        self.assertTrue(still_open(s))

    # -- connection management --------------------------------------------

    def test_connection_close_is_honoured(self):
        s, r = self.connect()
        status, headers, body = self.ask(
            s, r, get("/add?a=1&b=1", extra="Connection: close\r\n"))
        self.assertEqual((status, body, headers["connection"]), (200, "2", "close"))
        self.assertEqual(s.recv(1), b"", "server should have closed")

    def test_http10_closes_by_default(self):
        s, r = self.connect()
        status, headers, _ = self.ask(s, r, b"GET /add?a=1&b=1 HTTP/1.0\r\n\r\n")
        self.assertEqual((status, headers["connection"]), (200, "close"))
        self.assertEqual(s.recv(1), b"")

    def test_malformed_framing_gets_400_and_close(self):
        s, r = self.connect()
        status, headers, _ = self.ask(
            s, r, b"GET /add?a=1&b=1 HTTP/1.1\r\nHost: x\r\nContent-Length: abc\r\n\r\n")
        self.assertEqual((status, headers["connection"]), (400, "close"))
        self.assertEqual(s.recv(1), b"")

    def test_smuggling_shape_rejected(self):
        s, r = self.connect()
        status, _, _ = self.ask(
            s, r, b"POST /add HTTP/1.1\r\nHost: x\r\nContent-Length: 4\r\n"
                  b"Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n")
        self.assertEqual(status, 400)

    def test_many_requests_one_connection(self):
        s, r = self.connect()
        for i in range(500):
            self.assertEqual(self.ask(s, r, get("/add?a=%d&b=1" % i))[2], str(i + 1))
        self.assertTrue(still_open(s))


class IdleTimeoutTest(CalcServerTest):
    """Same server, 1 s idle timeout, so the test does not take 15 s."""
    IDLE_TIMEOUT = "1"

    def test_idle_connection_is_closed(self):
        s, r = self.connect()
        self.assertEqual(self.ask(s, r, get("/add?a=2&b=3"))[2], "5")
        time.sleep(0.5)                       # activity resets the clock
        self.assertEqual(self.ask(s, r, get("/add?a=2&b=3"))[2], "5")
        time.sleep(1.5)
        self.assertEqual(s.recv(1), b"", "idle socket should be closed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
