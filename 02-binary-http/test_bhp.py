#!/usr/bin/env python3
"""
End-to-end tests for BHP/1: bserve, bcurl and the independent C client bget.

Author : Hemang (Roll No. 24bcs10209)

Server tests build their wire bytes BY HAND from SPEC.md (struct.pack, no
bproto encoder), so they check the server against the spec rather than
against its own code. Client tests run the real ./bcurl binary against fake
servers that send exactly the bytes the spec allows (unknown frames, unknown
header indices, 5xx, ...).

Run:  python3 -m unittest -v test_bhp.py
"""

import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BSERVE = os.path.join(HERE, "bserve")
BCURL = os.path.join(HERE, "bcurl")
BGET_SRC = os.path.join(HERE, "interop", "bget.c")

PREFACE = b"BHP\x01"


# ------------------------------------------------ hand-rolled wire helpers --

def hdr(length, ftype, flags, stream):
    return bytes([length >> 16 & 255, length >> 8 & 255, length & 255, ftype,
                  flags, stream >> 16 & 255, stream >> 8 & 255, stream & 255])


def frame(ftype, flags, stream, payload=b""):
    return hdr(len(payload), ftype, flags, stream) + payload


def field(idx, value, name=None):
    value = value.encode() if isinstance(value, str) else value
    lit = b"" if name is None else bytes([len(name)]) + name.encode()
    return bytes([idx]) + lit + struct.pack(">H", len(value)) + value


def get_req(path, stream, method="GET"):
    return frame(0x01, 0x01, stream, field(1, method) + field(2, path))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Peer:
    """A raw socket speaking to bserve, with a hand-written frame reader."""

    def __init__(self, port, preface=True):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.buf = b""
        if preface:
            self.s.sendall(PREFACE)

    def send(self, data):
        self.s.sendall(data)

    def _need(self, n):
        while len(self.buf) < n:
            chunk = self.s.recv(65536)
            if not chunk:
                raise EOFError
            self.buf += chunk

    def frame(self):
        self._need(8)
        h = self.buf[:8]
        length = h[0] << 16 | h[1] << 8 | h[2]
        self._need(8 + length)
        payload = self.buf[8:8 + length]
        self.buf = self.buf[8 + length:]
        return length, h[3], h[4], h[5] << 16 | h[6] << 8 | h[7], payload

    def response(self):
        """-> (stream, {name: value}, body, [data frame lengths])"""
        _, ftype, flags, stream, payload = self.frame()
        assert ftype == 0x01, "expected HEADERS, got type %d" % ftype
        fields, i = {}, 0
        names = {1: ":method", 2: ":path", 3: ":status", 4: "host",
                 5: "user-agent", 6: "accept", 7: "content-type",
                 8: "content-length", 9: "server", 10: "last-modified"}
        while i < len(payload):
            idx = payload[i]
            i += 1
            if idx == 0:
                n = payload[i]
                name = payload[i + 1:i + 1 + n].decode()
                i += 1 + n
            else:
                name = names[idx]
            vl = struct.unpack_from(">H", payload, i)[0]
            fields[name] = payload[i + 2:i + 2 + vl].decode()
            i += 2 + vl
        body, sizes = b"", []
        while not flags & 0x01:
            length, ftype, flags, fstream, payload = self.frame()
            assert (ftype, fstream) == (0x00, stream)
            body += payload
            sizes.append(length)
        return stream, fields, body, sizes

    def closed(self):
        try:
            return self.s.recv(1) == b""
        except (ConnectionResetError, socket.timeout):
            return True

    def close(self):
        self.s.close()


# ---------------------------------------------------------- server tests --

class ServerTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp()
        with open(os.path.join(cls.root, "hi.txt"), "wb") as f:
            f.write(b"hi\n")
        with open(os.path.join(cls.root, "index.html"), "wb") as f:
            f.write(b"<h1>index</h1>\n")
        os.mkdir(os.path.join(cls.root, "sub"))
        with open(os.path.join(cls.root, "sub", "index.html"), "wb") as f:
            f.write(b"sub index\n")
        cls.big = os.urandom(100_000)
        with open(os.path.join(cls.root, "big.bin"), "wb") as f:
            f.write(cls.big)
        with open(os.path.join(os.path.dirname(cls.root), "secret.txt"), "w") as f:
            f.write("outside root")
        cls.port = free_port()
        cls.proc = subprocess.Popen([BSERVE, "--idle", "1.5", cls.root, str(cls.port)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", cls.port)).close()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("bserve did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait()
        shutil.rmtree(cls.root)
        os.remove(os.path.join(os.path.dirname(cls.root), "secret.txt"))

    def peer(self, preface=True):
        p = Peer(self.port, preface)
        self.addCleanup(p.close)
        return p

    def test_spec_worked_example(self):
        """SPEC section 6, byte for byte."""
        p = self.peer(preface=False)
        p.send(bytes.fromhex("42485001"
                             "0000100101000001"
                             "010003474554"
                             "0200072f68692e747874"))
        stream, fields, body, _ = p.response()
        self.assertEqual((stream, fields[":status"], fields["content-length"], body),
                         (1, "200", "3", b"hi\n"))

    def test_keeps_connection_open_across_statuses(self):
        p = self.peer()
        cases = [("/hi.txt", "GET", "200"), ("/nope", "GET", "404"),
                 ("/hi.txt", "POST", "405"), ("/", "GET", "200"),
                 ("/sub/", "GET", "200"), ("/hi.txt", "HEAD", "200")]
        for n, (path, method, status) in enumerate(cases, start=1):
            p.send(get_req(path, n, method))
            stream, fields, body, _ = p.response()
            self.assertEqual((stream, fields[":status"]), (n, status), path)
        self.assertEqual(body, b"", "HEAD must not carry a body")
        self.assertEqual(fields["content-length"], "3")
        p.send(get_req("/hi.txt", 99))
        self.assertEqual(p.response()[2], b"hi\n")

    def test_body_split_into_16k_data_frames(self):
        p = self.peer()
        p.send(get_req("/big.bin", 1))
        _, fields, body, sizes = p.response()
        self.assertEqual(body, self.big)
        self.assertEqual(int(fields["content-length"]), len(self.big))
        self.assertTrue(all(s <= 16384 for s in sizes), sizes)
        self.assertEqual(len(sizes), 7)                   # ceil(100000 / 16384)

    def test_unknown_frame_types_are_skipped(self):
        p = self.peer()
        # a tiny one, an empty one, and a 200 KB one -- before and between requests
        p.send(frame(0x7F, 0xFF, 0, b"future") + frame(0x03, 0, 5)
               + frame(0xEE, 0, 1, b"\xAB" * 200_000)
               + get_req("/hi.txt", 1) + frame(0x42, 0, 1, b"x") + get_req("/hi.txt", 2))
        self.assertEqual(p.response()[:3:2], (1, b"hi\n"))
        self.assertEqual(p.response()[:3:2], (2, b"hi\n"))

    def test_unknown_header_index_and_literal_names_are_accepted(self):
        p = self.peer()
        block = (field(0x42, "a v2 field") + field(1, "GET")
                 + field(0, "whatever", name="x-custom") + field(2, "/hi.txt")
                 + field(0xFF, ""))
        p.send(frame(0x01, 0x01, 1, block))
        _, fields, body, _ = p.response()
        self.assertEqual((fields[":status"], body), ("200", b"hi\n"))

    def test_unknown_flag_bits_are_ignored(self):
        p = self.peer()
        p.send(frame(0x01, 0x01 | 0x80 | 0x10, 1, field(1, "GET") + field(2, "/hi.txt")))
        self.assertEqual(p.response()[1][":status"], "200")

    def test_malformed_header_block_is_400_and_connection_survives(self):
        p = self.peer()
        bad = [
            field(1, "GET") + b"\x02\x00\xff/hi",            # value past frame end
            field(1, "GET") + b"\x02\x00",                   # truncated length
            b"\x00\x00\x00\x01x",                            # zero-length name
            field(1, "GET"),                                 # no :path
            field(2, "/hi.txt"),                             # no :method
            field(1, "GET") + field(2, "hi.txt"),            # :path without /
            field(1, "GET") + field(1, "GET") + field(2, "/hi.txt"),
            field(1, b"\xff\xfe") + field(2, "/hi.txt"),     # not UTF-8
        ]
        for n, block in enumerate(bad, start=1):
            with self.subTest(block=block.hex()):
                p.send(frame(0x01, 0x01, n, block))
                stream, fields, _, _ = p.response()
                self.assertEqual((stream, fields[":status"]), (n, "400"))
        p.send(get_req("/hi.txt", 50))
        self.assertEqual(p.response()[2], b"hi\n")

    def test_headers_on_stream_zero_is_400(self):
        p = self.peer()
        p.send(get_req("/hi.txt", 0))
        stream, fields, _, _ = p.response()
        self.assertEqual((stream, fields[":status"]), (0, "400"))
        p.send(get_req("/hi.txt", 1))
        self.assertEqual(p.response()[1][":status"], "200")

    def test_request_body_is_consumed_not_parsed(self):
        p = self.peer()
        smuggled = get_req("/nope", 7)      # DATA that *looks* like a request
        p.send(frame(0x01, 0x00, 1, field(1, "GET") + field(2, "/hi.txt"))
               + frame(0x00, 0x00, 1, smuggled) + frame(0x00, 0x01, 1, b"end")
               + get_req("/index.html", 2))
        self.assertEqual(p.response()[:3:2], (1, b"hi\n"))
        self.assertEqual(p.response()[:3:2], (2, b"<h1>index</h1>\n"))

    def test_pipelined_requests_answered_in_order(self):
        p = self.peer()
        paths = ["/hi.txt", "/nope", "/index.html", "/hi.txt", "/sub/"]
        p.send(b"".join(get_req(x, n) for n, x in enumerate(paths, start=1)))
        got = [(s, f[":status"]) for s, f, _, _ in (p.response() for _ in paths)]
        self.assertEqual(got, [(1, "200"), (2, "404"), (3, "200"), (4, "200"), (5, "200")])

    def test_path_traversal_is_404(self):
        p = self.peer()
        for n, path in enumerate(["/../secret.txt", "/%2e%2e/secret.txt",
                                  "/sub/../../secret.txt", "/hi.txt%00"], start=1):
            p.send(get_req(path, n))
            self.assertEqual(p.response()[1][":status"], "404", path)

    def test_bad_preface_gets_400_goaway_and_close(self):
        p = self.peer(preface=False)
        p.send(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        stream, fields, body, _ = p.response()
        self.assertEqual((stream, fields[":status"]), (0, "400"))
        self.assertIn(b"preface", body)
        self.assertEqual(p.frame()[1], 0x02)            # GOAWAY
        self.assertTrue(p.closed())

    def test_idle_timeout_sends_goaway(self):
        p = self.peer()
        p.send(get_req("/hi.txt", 1))
        p.response()
        _, ftype, _, stream, payload = p.frame()       # arrives after ~1.5 s
        self.assertEqual((ftype, stream, payload), (0x02, 0, b"idle timeout"))
        self.assertTrue(p.closed())

    def test_frame_split_across_many_tcp_writes(self):
        p = self.peer()
        p.s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        for b in get_req("/hi.txt", 1):
            p.send(bytes([b]))
            time.sleep(0.002)
        self.assertEqual(p.response()[2], b"hi\n")


# ---------------------------------------------------------- client tests --

class FakeServer:
    """Accepts connections, checks the preface, answers each HEADERS request
    with whatever `script(stream, fields_raw)` returns. Counts connections."""

    def __init__(self, script):
        self.script = script
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.connections = 0
        self.prefaces = []
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self.handle, args=(c,), daemon=True).start()

    def handle(self, c):
        p = Peer.__new__(Peer)
        p.s, p.buf = c, b""
        try:
            p._need(4)
            self.prefaces.append(p.buf[:4])
            p.buf = p.buf[4:]
            while True:
                _, ftype, _, stream, payload = p.frame()
                if ftype == 0x01:
                    c.sendall(self.script(stream, payload))
                elif ftype == 0x02:
                    break
        except (EOFError, OSError):
            pass
        finally:
            c.close()

    def close(self):
        self.sock.close()


def ok_response(stream, body=b"hello\n", status="200", extra=b""):
    block = field(3, status) + field(8, str(len(body))) + extra
    return frame(0x01, 0, stream, block) + frame(0x00, 0x01, stream, body)


def req_path(block):
    """:path from a bcurl request block (:method is sent first, then :path)."""
    i = 3 + struct.unpack_from(">H", block, 1)[0]      # step over :method
    assert block[i] == 2
    n = struct.unpack_from(">H", block, i + 1)[0]
    return block[i + 3:i + 3 + n].decode()


def run_bcurl(*args):
    return subprocess.run([BCURL, *args], capture_output=True, timeout=15)


class ClientTest(unittest.TestCase):

    def fake(self, script):
        srv = FakeServer(script)
        self.addCleanup(srv.close)
        return srv

    def test_body_to_stdout_exit_zero_and_preface(self):
        srv = self.fake(lambda s, _: ok_response(s))
        r = run_bcurl("localhost:%d/x" % srv.port)
        self.assertEqual((r.returncode, r.stdout), (0, b"hello\n"))
        self.assertEqual(srv.prefaces, [PREFACE])

    def test_request_bytes_follow_the_spec(self):
        seen = []
        srv = self.fake(lambda s, block: seen.append((s, block)) or ok_response(s))
        run_bcurl("127.0.0.1:%d/index.html" % srv.port)
        stream, block = seen[0]
        self.assertEqual(stream, 1)
        self.assertTrue(block.startswith(field(1, "GET") + field(2, "/index.html")))

    def test_skips_unknown_frames_and_header_indices(self):
        def script(s, _):
            return (frame(0x99, 0xFF, s, b"\x00" * 70_000)
                    + frame(0x01, 0, s, field(3, "200") + field(0x77, "v2 thing")
                            + field(0, "yes", name="x-extra"))
                    + frame(0x55, 0, 0, b"between")
                    + frame(0x00, 0x01, s, b"still fine\n"))
        srv = self.fake(script)
        r = run_bcurl("localhost:%d/x" % srv.port)
        self.assertEqual((r.returncode, r.stdout), (0, b"still fine\n"))

    def test_exit_non_zero_on_4xx_and_5xx(self):
        codes = {"/404": "404", "/503": "503", "/ok": "200"}
        srv = self.fake(lambda s, block: ok_response(s, b"x\n", codes[req_path(block)]))
        self.assertEqual(run_bcurl("localhost:%d/404" % srv.port).returncode, 4)
        self.assertEqual(run_bcurl("localhost:%d/503" % srv.port).returncode, 5)
        self.assertEqual(run_bcurl("localhost:%d/ok" % srv.port).returncode, 0)

    def test_never_opens_a_second_connection(self):
        srv = self.fake(lambda s, _: ok_response(s, b"%d\n" % s))
        urls = ["localhost:%d/%d" % (srv.port, i) for i in range(5)]
        r = run_bcurl(*urls)
        self.assertEqual((r.returncode, r.stdout), (0, b"1\n2\n3\n4\n5\n"))
        self.assertEqual(srv.connections, 1)
        r = run_bcurl("--pipeline", *urls)
        self.assertEqual(r.stdout, b"1\n2\n3\n4\n5\n")
        self.assertEqual(srv.connections, 2)            # one more run, one more

    def test_refuses_urls_that_would_need_two_connections(self):
        r = run_bcurl("localhost:1/a", "localhost:2/b")
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"second connection", r.stderr)

    def test_short_body_is_a_protocol_error(self):
        srv = self.fake(lambda s, _: frame(0x01, 0, s, field(3, "200") + field(8, "10"))
                        + frame(0x00, 0x01, s, b"abc"))
        self.assertEqual(run_bcurl("localhost:%d/x" % srv.port).returncode, 3)

    def test_verbose_hexdumps_every_frame(self):
        srv = self.fake(lambda s, _: frame(0xAB, 0, s, b"?") + ok_response(s))
        err = run_bcurl("-v", "localhost:%d/x" % srv.port).stderr.decode()
        for needle in ["> PREFACE", "42 48 50 01", "> HEADERS stream=1",
                       "< UNKNOWN(0xab)", "< HEADERS stream=1", "< DATA stream=1",
                       "flags=END_STREAM", "> GOAWAY"]:
            self.assertIn(needle, err)

    def test_connection_refused_exits_3(self):
        self.assertEqual(run_bcurl("localhost:%d/x" % free_port()).returncode, 3)


# ----------------------------------------- real client x real server ------

class EndToEndTest(unittest.TestCase):
    """bcurl and the C client bget against a real bserve on ./www."""

    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.www = os.path.join(HERE, "www")
        cls.proc = subprocess.Popen([BSERVE, "--grease", cls.www, str(cls.port)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait()

    def url(self, path):
        return "localhost:%d%s" % (self.port, path)

    def test_bcurl_fetches_files_byte_exact(self):
        for path in ["/index.html", "/docs/big.txt", "/hi.txt"]:
            with open(os.path.join(self.www, path.lstrip("/")), "rb") as f:
                want = f.read()
            r = run_bcurl(self.url(path))
            self.assertEqual((r.returncode, r.stdout), (0, want), path)

    def test_bcurl_404_405(self):
        self.assertEqual(run_bcurl(self.url("/missing")).returncode, 4)
        self.assertEqual(run_bcurl("-X", "DELETE", self.url("/hi.txt")).returncode, 4)

    def test_bcurl_head(self):
        r = run_bcurl("-I", self.url("/docs/big.txt"))
        self.assertIn(b"content-length: 48300", r.stdout)

    @unittest.skipUnless(shutil.which("cc"), "no C compiler")
    def test_independent_c_client_interoperates(self):
        out = os.path.join(tempfile.mkdtemp(), "bget")
        subprocess.run(["cc", "-O2", "-o", out, BGET_SRC], check=True)
        r = subprocess.run([out, "localhost", str(self.port), "/hi.txt", "/index.html"],
                           capture_output=True, timeout=10)
        with open(os.path.join(self.www, "index.html"), "rb") as f:
            self.assertEqual(r.stdout, b"hi\n" + f.read())
        self.assertEqual(r.returncode, 0)
        self.assertIn(b"skipped unknown frame type 0xfa", r.stderr)
        r = subprocess.run([out, "localhost", str(self.port), "/nope"],
                           capture_output=True, timeout=10)
        self.assertEqual(r.returncode, 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
