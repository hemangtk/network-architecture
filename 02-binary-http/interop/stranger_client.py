#!/usr/bin/env python3
"""
stranger_client.py -- the "stranger test" for SPEC.md.

Written from SPEC.md alone, without looking at bserve/bcurl/bproto, to check
that the spec really is "enough for a stranger". It is kept deliberately
terse and unpolished, exactly as written blind; it worked first time against
both bserve (Python) and interop/bserve.js (Node).

    python3 interop/stranger_client.py 9000 /hi.txt HEAD:/docs/big.txt /missing

Prints: stream, decoded header fields, body length, first 60 body bytes.
"""
import socket, struct, sys
def frame(t, fl, sid, p): return struct.pack(">I", len(p))[1:] + bytes([t, fl]) + sid.to_bytes(3,"big") + p
def field(idx, val, name=None):
    v = val.encode()
    if idx == 0: n = name.encode(); return b"\0" + bytes([len(n)]) + n + struct.pack(">H", len(v)) + v
    return bytes([idx]) + struct.pack(">H", len(v)) + v
def recvn(s, n):
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c: raise EOFError(f"closed after {len(b)}/{n}")
        b += c
    return b
def readframe(s):
    h = recvn(s, 8); L = int.from_bytes(h[:3], "big"); return h[3], h[4], int.from_bytes(h[5:], "big"), recvn(s, L)
NAMES = {1:":method",2:":path",3:":status",4:"host",5:"user-agent",6:"accept",7:"content-type",8:"content-length",9:"server",10:"last-modified"}
def parse_hdrs(p):
    i, out = 0, []
    while i < len(p):
        idx = p[i]; i += 1
        if idx == 0: nl = p[i]; i += 1; name = p[i:i+nl].decode(); i += nl
        else: name = NAMES.get(idx, f"?{idx}")
        vl = int.from_bytes(p[i:i+2], "big"); i += 2; out.append((name, p[i:i+vl])); i += vl
    return out
def response(s):
    hdrs, body = None, b""
    while True:
        t, fl, sid, p = readframe(s)
        if t == 1: hdrs = parse_hdrs(p)
        elif t == 0: body += p
        elif t == 2: return ("GOAWAY", p)
        else: continue
        if fl & 1: return sid, hdrs, body
if __name__ == "__main__":
    port = int(sys.argv[1]); s = socket.create_connection(("127.0.0.1", port)); s.sendall(b"BHP\x01")
    for i, path in enumerate(sys.argv[2:], 1):
        method = "GET"
        if path.startswith("HEAD:"): method, path = "HEAD", path[5:]
        s.sendall(frame(1, 1, i, field(1, method) + field(2, path) + field(4, "localhost") + field(0, "x", "x-grader")))
        sid, h, b = response(s); print(sid, h, len(b), b[:60])
