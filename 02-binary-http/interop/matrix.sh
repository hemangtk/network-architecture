#!/bin/sh
# Interop matrix: every client x every server. They share only SPEC.md.
# Author: Hemang (Roll No. 24bcs10209)
#
#   clients: bcurl (Python)   bget (C)   stranger_client.py (written blind from SPEC.md)
#   servers: bserve (Python, --grease: also sends unknown frames)   bserve.js (Node)
#
# Each cell fetches /hi.txt, /docs/big.txt (48 KB -> 3 DATA frames) and /missing
# on ONE connection: the bodies must be byte-exact and the 404 must be reported.
# Builds into a temp dir and picks free ports, so it leaves the repo untouched.
set -u
cd "$(dirname "$0")/.."
TMP=$(mktemp -d)
free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'; }
cc -O2 -Wall -o "$TMP/bget" interop/bget.c || exit 1

P1=$(free_port); ./bserve --grease ./www "$P1" 2>/dev/null & S1=$!
S2=; P2=
if command -v node >/dev/null; then P2=$(free_port); node interop/bserve.js ./www "$P2" 2>/dev/null & S2=$!; fi
trap 'kill $S1 $S2 2>/dev/null; wait 2>/dev/null; rm -rf "$TMP"' EXIT
python3 -c "import time; time.sleep(1)"

want=$(cat www/hi.txt www/docs/big.txt | shasum)
pass=0; total=0
row() {  # label, verdict
    total=$((total+1)); [ "$2" = PASS ] && pass=$((pass+1))
    printf '  %-42s %s\n' "$1" "$2"
}
echo "Interop matrix (each cell: /hi.txt, /docs/big.txt, /missing on one connection)"
for port in $P1 $P2; do
    if [ "$port" = "$P1" ]; then srv="bserve (Python, --grease)"; else srv="bserve.js (Node)"; fi

    # bcurl: bodies on stdout (404 body last), exit 4 because of the 404
    ./bcurl localhost:$port/hi.txt localhost:$port/docs/big.txt localhost:$port/missing \
        > "$TMP/out" 2>/dev/null; rc=$?
    got=$(head -c 48303 "$TMP/out" | shasum)
    if [ "$got" = "$want" ] && [ $rc = 4 ]; then v=PASS; else v="FAIL (exit $rc)"; fi
    row "bcurl            -> $srv" "$v"

    "$TMP/bget" localhost $port /hi.txt /docs/big.txt /missing > "$TMP/out" 2>/dev/null; rc=$?
    got=$(head -c 48303 "$TMP/out" | shasum)
    if [ "$got" = "$want" ] && [ $rc = 4 ]; then v=PASS; else v="FAIL (exit $rc)"; fi
    row "bget (C)         -> $srv" "$v"

    # stranger client prints "stream fields body_len body[:60]" per request
    out=$(python3 interop/stranger_client.py $port /hi.txt /docs/big.txt /missing 2>&1)
    if printf "%s\n" "$out" | sed -n 1p | grep -q "b'200'.* 3 b'hi\\\\n'" \
       && printf "%s\n" "$out" | sed -n 2p | grep -q "b'200'.* 48300 " \
       && printf "%s\n" "$out" | sed -n 3p | grep -q "b'404'"; then v=PASS; else v=FAIL; fi
    row "stranger_client  -> $srv" "$v"
done
echo "$pass/$total cells pass"
[ "$pass" = "$total" ]
