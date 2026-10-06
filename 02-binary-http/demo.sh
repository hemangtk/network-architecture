#!/bin/sh
# demo.sh -- walks through every line of the brief against a live ./bserve,
# using the exact commands from the brief. Prints PASS/FAIL per requirement.
# Author: Hemang (Roll No. 24bcs10209)
#
#   ./demo.sh            (needs python3; node + cc optional for the interop row)
set -u
cd "$(dirname "$0")"
PORT=${PORT:-9000}
pass=0; fail=0
ok()  { pass=$((pass+1)); printf '  PASS  %s\n' "$1"; }
bad() { fail=$((fail+1)); printf '  FAIL  %s\n' "$1"; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# raw-frame helper: send hex bytes on ONE connection, print each response as
# "stream status" lines, then whether the socket is still open
raw() {
python3 - "$PORT" "$@" <<'EOF'
import socket, sys
port, chunks = int(sys.argv[1]), sys.argv[2:]
s = socket.create_connection(("127.0.0.1", port), timeout=3)
buf = b""
def need(n):
    global buf
    while len(buf) < n:
        c = s.recv(65536)
        if not c: raise EOFError
        buf += c
def frame():
    global buf
    need(8); L = int.from_bytes(buf[:3], "big"); need(8 + L)
    f = (buf[3], buf[4], int.from_bytes(buf[5:8], "big"), buf[8:8+L]); buf = buf[8+L:]; return f
out = []
for hexchunk in chunks:
    s.sendall(bytes.fromhex(hexchunk))
    if hexchunk.startswith("42485001") and len(hexchunk) == 8: continue
    try:
        t, fl, sid, p = frame()
        while t not in (1, 2): t, fl, sid, p = frame()
        if t == 2: out.append("GOAWAY"); break
        st = p[3:3 + int.from_bytes(p[1:3], "big")].decode()      # :status is sent first
        out.append("%d %s" % (sid, st))
        while not fl & 1: t, fl, sid, p = frame()
    except (EOFError, OSError): out.append("CLOSED"); break
# drain anything else the server sends (e.g. GOAWAY) for 0.5 s, then decide
s.settimeout(0.5)
still = True
try:
    while True:
        t, fl, sid, p = frame()
        if t == 2 and "GOAWAY" not in out: out.append("GOAWAY")
except socket.timeout: still = True
except (EOFError, OSError): still = False
print(" ".join(out), "open" if still else "closed")
EOF
}
hdr() {  # hdr STREAM FLAGS PAYLOADHEX -> HEADERS frame hex
python3 -c "import sys; p=bytes.fromhex(sys.argv[3]); print((len(p).to_bytes(3,'big')+bytes([1,int(sys.argv[2])])+int(sys.argv[1]).to_bytes(3,'big')+p).hex())" "$@"; }
get() {  # get STREAM PATH -> GET request frame hex
python3 -c "import sys; m=b'GET'; p=sys.argv[2].encode(); print(hex(0)[0:0]+(lambda b:(len(b).to_bytes(3,'big')+bytes([1,1])+int(sys.argv[1]).to_bytes(3,'big')+b).hex())(bytes([1])+len(m).to_bytes(2,'big')+m+bytes([2])+len(p).to_bytes(2,'big')+p))" "$@"; }

./bserve ./www "$PORT" 2>/dev/null &
SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT
python3 -c "import time; time.sleep(0.7)"

echo "Track 1 -- the server:  ./bserve ./www $PORT"
r=$(raw 42485001 "$(get 1 /index.html)")
check "accept a TCP connection, read one binary request frame, reply 200   [$r]" '[ "$r" = "1 200 open" ]'
want=$(shasum < www/docs/big.txt)
check "map the path to a file under a root; reply status, headers, bytes (48 KB byte-exact)" \
      '[ "$(./bcurl localhost:$PORT/docs/big.txt | shasum)" = "$want" ]'
r=$(raw 42485001 "$(get 1 /nope.html)" "$(get 2 /hi.txt)")
check "404 if it is not there -- and the connection stays open   [$r]" '[ "$r" = "1 404 2 200 open" ]'
r=$(raw 42485001 "$(hdr 1 1 0100034745540200ff2f6869)" "$(get 2 /hi.txt)")
check "400 if the frame is malformed -- and the connection stays open   [$r]" '[ "$r" = "1 400 2 200 open" ]'
r=$(raw 42485001 "$(get 1 /../../etc/passwd)" "$(get 2 /%2e%2e/secret)")
check "paths cannot escape the root (../, %2e%2e)   [$r]" '[ "$r" = "1 404 2 404 open" ]'
r=$(raw 42485001 "$(get 1 /hi.txt)" "$(get 2 /hi.txt)" "$(get 3 /index.html)" "$(get 4 /docs/hello.txt)")
check "and keep the connection open: 4 requests, 1 connection   [$r]" '[ "$r" = "1 200 2 200 3 200 4 200 open" ]'

echo
echo "Track 2 -- the client:  ./bcurl -v localhost:$PORT/index.html"
check "build the binary request frame; read the response; body to stdout" \
      '[ "$(./bcurl localhost:$PORT/index.html)" = "$(cat www/index.html)" ]'
n=$(./bcurl -v localhost:$PORT/docs/big.txt 2>&1 >/dev/null | grep -cE '^[<>] (PREFACE|HEADERS|DATA|GOAWAY)')
check "-v hexdumps every frame: preface, request, HEADERS, 3 DATA, GOAWAY = $n dumps" '[ "$n" = 7 ]'
./bcurl localhost:$PORT/nope >/dev/null 2>&1; e=$?
check "exit non-zero on 4xx  (exit $e)" '[ "$e" = 4 ]'
./bcurl -X DELETE localhost:$PORT/hi.txt >/dev/null 2>&1; e=$?
check "exit non-zero on 4xx: 405 for DELETE  (exit $e)" '[ "$e" = 4 ]'
out=$(./bcurl -v localhost:$PORT/hi.txt localhost:$PORT/index.html localhost:$PORT/docs/hello.txt 2>&1 >/dev/null | grep -c "^\* connected")
check "and never open a second connection (3 URLs -> $out connection)" '[ "$out" = 1 ]'
./bcurl localhost:$PORT/a localhost:1/b >/dev/null 2>&1; e=$?
check "refuses URLs that would need a second connection (exit $e)" '[ "$e" = 2 ]'

echo
echo "The middle -- the protocol"
r=$(raw 42485001 "0000097f0000000168656c6c6f2c207632$(get 1 /hi.txt)")   # unknown type 0x7f, then a GET, in one write
check "a receiver meeting an unknown frame type MUST skip it cleanly (server)   [$r]" '[ "$r" = "1 200 open" ]'
kill $SRV; wait $SRV 2>/dev/null
./bserve --grease ./www "$PORT" 2>/dev/null & SRV=$!
python3 -c "import time; time.sleep(0.7)"
g=$(./bcurl -v localhost:$PORT/hi.txt 2>&1 | grep -c "unknown frame type 0xfa")
check "... and the client skips the server's unknown 0xFA frames too" '[ "$g" = 1 ] && [ "$(./bcurl localhost:$PORT/hi.txt)" = hi ]'
r=$(raw "$(python3 -c 'print(b"BHP\x02".hex())')")
check "version byte in the preface: v2 preface gets 505, GOAWAY, close   [$r]" '[ "$r" = "0 505 GOAWAY closed" ]'
r=$(raw "$(python3 -c 'print(b"GET / HTTP/1.1".hex())')")
check "an HTTP/1.1 client fails fast: 400 on stream 0, GOAWAY, close   [$r]" '[ "$r" = "0 400 GOAWAY closed" ]'

if command -v node >/dev/null && command -v cc >/dev/null; then
  echo
  echo "Pairs -- the only thing that crosses is the spec"
  kill $SRV; wait $SRV 2>/dev/null
  if out=$(./interop/matrix.sh 2>&1); then ok "interop matrix: bcurl, bget (C), stranger client  x  bserve, bserve.js (Node) -- $(echo "$out" | tail -1)"
  else bad "interop matrix"; echo "$out"; fi
fi

echo
echo "$pass passed, $fail failed"
[ "$fail" = 0 ]
