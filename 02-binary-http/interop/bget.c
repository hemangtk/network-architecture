/*
 * bget.c -- a second, independent BHP/1 client, written only from SPEC.md.
 *
 * Author : Hemang (Roll No. 24bcs10209)
 *
 * It shares no code with bcurl/bproto.py. If it can talk to bserve, the spec
 * is what they share -- a protocol, not just an implementation.
 *
 *   cc -O2 -Wall -o bget bget.c
 *   ./bget localhost 9000 /index.html /docs/hello.txt     (one connection)
 *
 * Exit: 0 all 2xx/3xx, 4 any 4xx, 5 any 5xx, 3 connection/protocol error.
 */
#include <netdb.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

static int fd;

static void die(const char *msg) {
    fprintf(stderr, "bget: %s\n", msg);
    exit(3);
}

static void read_full(uint8_t *buf, size_t n) {
    while (n) {
        ssize_t r = recv(fd, buf, n, 0);
        if (r <= 0) die("connection closed mid-frame");
        buf += r;
        n -= (size_t)r;
    }
}

static void write_full(const uint8_t *buf, size_t n) {
    while (n) {
        ssize_t w = send(fd, buf, n, 0);
        if (w <= 0) die("send failed");
        buf += w;
        n -= (size_t)w;
    }
}

/* SPEC section 2: Length:24 Type:8 Flags:8 StreamID:24, big-endian */
static void put_header(uint8_t *h, uint32_t len, uint8_t type, uint8_t flags,
                       uint32_t stream) {
    h[0] = len >> 16; h[1] = len >> 8; h[2] = len;
    h[3] = type;
    h[4] = flags;
    h[5] = stream >> 16; h[6] = stream >> 8; h[7] = stream;
}

/* SPEC section 5: indexed field = idx:8 value_len:16 value */
static size_t put_indexed(uint8_t *p, uint8_t idx, const char *v) {
    size_t n = strlen(v);
    p[0] = idx; p[1] = n >> 8; p[2] = n;
    memcpy(p + 3, v, n);
    return 3 + n;
}

/* literal field = 0x00 name_len:8 name value_len:16 value */
static size_t put_literal(uint8_t *p, const char *name, const char *v) {
    size_t nl = strlen(name), vl = strlen(v);
    p[0] = 0; p[1] = (uint8_t)nl;
    memcpy(p + 2, name, nl);
    p[2 + nl] = vl >> 8; p[3 + nl] = vl;
    memcpy(p + 4 + nl, v, vl);
    return 4 + nl + vl;
}

static void send_get(uint32_t stream, const char *path, const char *host) {
    uint8_t frame[8 + 1024];
    size_t n = 0;
    if (strlen(path) > 900) die("path too long for this toy");
    n += put_indexed(frame + 8 + n, 0x01, "GET");
    n += put_indexed(frame + 8 + n, 0x02, path);
    n += put_indexed(frame + 8 + n, 0x04, host);
    n += put_literal(frame + 8 + n, "x-client", "bget.c");
    put_header(frame, (uint32_t)n, 0x01 /* HEADERS */, 0x01 /* END_STREAM */,
               stream);
    write_full(frame, 8 + n);
}

/* Find :status (index 3) and content-length (index 8) in a header block;
 * skip everything else, including indices we do not know (SPEC 5: MUST skip
 * the field). *clen is set to -1 when content-length is absent. */
static int parse_status(const uint8_t *b, size_t len, long long *clen) {
    size_t i = 0;
    int status = -1;
    *clen = -1;
    while (i < len) {
        uint8_t idx = b[i++];
        if (idx == 0) {
            if (i >= len) die("truncated literal");
            i += 1 + b[i];
        }
        if (i + 2 > len) die("truncated value length");
        size_t vl = ((size_t)b[i] << 8) | b[i + 1];
        i += 2;
        if (i + vl > len) die("value past end of frame");
        if (idx == 0x03) {
            if (status != -1 || vl != 3) die("need exactly one 3-digit :status");
            for (size_t k = 0; k < 3; k++)
                if (b[i + k] < '0' || b[i + k] > '9') die(":status is not digits");
            status = (b[i] - '0') * 100 + (b[i + 1] - '0') * 10 + (b[i + 2] - '0');
        }
        if (idx == 0x08) {
            if (*clen != -1 || vl == 0 || vl > 15) die("bad or repeated content-length");
            long long v = 0;
            for (size_t k = 0; k < vl; k++) {
                if (b[i + k] < '0' || b[i + k] > '9') die("content-length is not digits");
                v = v * 10 + (b[i + k] - '0');
            }
            *clen = v;
        }
        i += vl;
    }
    return status;
}

int main(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr, "usage: bget HOST PORT PATH [PATH ...]\n");
        return 2;
    }
    struct addrinfo hints = {0}, *ai;
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo(argv[1], argv[2], &hints, &ai) != 0) die("resolve failed");
    fd = -1;
    for (struct addrinfo *p = ai; p && fd < 0; p = p->ai_next) {  /* ::1, then 127.0.0.1 */
        fd = socket(p->ai_family, p->ai_socktype, p->ai_protocol);
        if (fd >= 0 && connect(fd, p->ai_addr, p->ai_addrlen) != 0) {
            close(fd);
            fd = -1;
        }
    }
    freeaddrinfo(ai);
    if (fd < 0) die("connect failed");

    write_full((const uint8_t *)"BHP\x01", 4);        /* SPEC section 1 */

    int worst = 0;
    for (int a = 3; a < argc; a++) {
        uint32_t want = (uint32_t)(a - 2);
        send_get(want, argv[a], argv[1]);
        int status = -1, done = 0;
        long long clen = -1, got = 0;
        while (!done) {
            uint8_t h[8];
            read_full(h, 8);
            uint32_t len = ((uint32_t)h[0] << 16) | (h[1] << 8) | h[2];
            uint8_t type = h[3], flags = h[4];
            uint32_t stream = ((uint32_t)h[5] << 16) | (h[6] << 8) | h[7];
            uint8_t *p = malloc(len ? len : 1);
            if (!p) die("out of memory");
            read_full(p, len);          /* unknown types: read and drop */
            if (type == 0x02) die("server sent GOAWAY");
            if (type == 0x01 && stream == want) {
                status = parse_status(p, len, &clen);
            } else if (type == 0x00 && stream == want) {
                if (status == -1) die("DATA before HEADERS");
                got += len;
                if (clen >= 0 && got > clen) die("more DATA than content-length");
                fwrite(p, 1, len, stdout);
            } else if (type <= 0x02) {
                die("frame for an unexpected stream");
            } else {
                fprintf(stderr, "bget: skipped unknown frame type 0x%02x (%u octets)\n",
                        type, len);
                free(p);
                continue;
            }
            free(p);
            if ((type == 0x00 || type == 0x01) && (flags & 0x01)) done = 1;
        }
        if (clen >= 0 && got != clen) die("DATA does not match content-length");
        fprintf(stderr, "bget: stream %u %s -> %d\n", want, argv[a], status);
        if (status < 200 || status > 599) die("response without a valid :status (200-599)");
        if (status / 100 > worst) worst = status / 100;
    }
    uint8_t bye[8];
    put_header(bye, 0, 0x02, 0, 0);                   /* GOAWAY, no reason */
    write_full(bye, 8);
    close(fd);
    return worst >= 5 ? 5 : worst == 4 ? 4 : 0;
}
