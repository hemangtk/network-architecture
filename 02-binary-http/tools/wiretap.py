#!/usr/bin/env python3
"""
wiretap.py -- a transparent TCP relay that records the exact bytes in each
direction, so the hexdump in HEXDUMP.md is what actually crossed the wire
(not what either program *thinks* it sent).

Author : Hemang (Roll No. 24bcs10209)

    ./bserve ./www 9000 &
    python3 tools/wiretap.py 9300 localhost:9000 /tmp/cap &   # one connection
    ./bcurl -v localhost:9300/hi.txt
    -> /tmp/cap.c2s  (client -> server bytes)   /tmp/cap.s2c  (server -> client)

Handles exactly one connection, then exits.
"""

import socket
import sys
import threading


def pump(src, dst, out):
    with open(out, "wb") as f:
        while True:
            data = src.recv(65536)
            if not data:
                break
            f.write(data)
            f.flush()
            dst.sendall(data)
    try:
        dst.shutdown(socket.SHUT_WR)
    except OSError:
        pass


def main():
    if len(sys.argv) != 4:
        sys.exit("usage: wiretap.py LISTEN_PORT HOST:PORT OUT_PREFIX")
    listen_port = int(sys.argv[1])
    host, port = sys.argv[2].rsplit(":", 1)
    prefix = sys.argv[3]
    lsock = socket.socket()
    lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lsock.bind(("127.0.0.1", listen_port))
    lsock.listen(1)
    client, _ = lsock.accept()
    server = socket.create_connection((host, int(port)))
    t1 = threading.Thread(target=pump, args=(client, server, prefix + ".c2s"))
    t2 = threading.Thread(target=pump, args=(server, client, prefix + ".s2c"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    client.close()
    server.close()


if __name__ == "__main__":
    main()
