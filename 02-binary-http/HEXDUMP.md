# Annotated hexdump: one complete request and response

*Hemang (Roll No. 24bcs10209)*

## How the bytes were captured

These bytes are the ones that **actually crossed the wire**. They were recorded
by a transparent TCP relay ([`tools/wiretap.py`](tools/wiretap.py)) sitting
between the client and the server, which saves each direction to a file:

```
$ ./bserve ./www 9000 &
$ python3 tools/wiretap.py 9300 localhost:9000 cap &       # relay, records both directions
$ ./bcurl -vv localhost:9300/hi.txt
hi
```

The raw captures are in [`captures/`](captures/):
- `request.c2s.bin`: 81 octets, client to server.
- `response.s2c.bin`: 113 octets, server to client.
- Each has a matching `*.xxd.txt` dump.

The annotation below was written by hand against those files. Offsets are
positions in each direction's byte stream. As a cross-check,
`bcurl -vv` decodes the same bytes independently, and its output is in
[`hexdump-capture.txt`](hexdump-capture.txt).

Totals:

| Direction | Contents | Octets |
|---|---|---|
| Client to server | preface 4 + HEADERS 8+57 + GOAWAY 8+4 | **81** |
| Server to client | HEADERS 8+94 + DATA 8+3 | **113** |

---

## 1. Client to server: `captures/request.c2s.bin`

```
00000000: 4248 5001 0000 3901 0100 0001 0100 0347  BHP...9........G
00000010: 4554 0200 072f 6869 2e74 7874 0400 096c  ET.../hi.txt...l
00000020: 6f63 616c 686f 7374 0500 1462 6375 726c  ocalhost...bcurl
00000030: 2f31 2028 3234 6263 7331 3032 3039 2906  /1 (24bcs10209).
00000040: 0003 2a2f 2a00 0004 0200 0000 0064 6f6e  ..*/*........don
00000050: 65                                       e
```

### 1a. Preface, sent once per connection (offsets 0x00 to 0x03)

| Offset | Octets | Meaning |
|---|---|---|
| `00` | `42 48 50` | Magic `"BHP"`. A server reading anything else answers `400` on stream 0 and closes. |
| `03` | `01` | Version 1. Any other value gets `505` (SPEC §1). |

### 1b. HEADERS frame: the request (offsets 0x04 to 0x44, 8 + 57 octets)

**Frame header, octet by octet, then bit by bit:**

| Offset | Octets | Field | Value |
|---|---|---|---|
| `04` | `00 00 39` | Length (24 bits) | 0x39 = **57** payload octets. The frame therefore ends at 0x0c + 57 = **0x45**. |
| `07` | `01` | Type (8 bits) | **HEADERS** |
| `08` | `01` | Flags (8 bits) | **END_STREAM**: there is no body, so this one frame is the whole request. |
| `09` | `00 00 01` | Stream ID (24 bits) | **1** |

```
octets  00       00       39       01       | 01       00       00       01
bits    00000000 00000000 00111001 00000001 | 00000001 00000000 00000000 00000001
        |------ Length = 57 -----| Type=1   | Flags=1  |------ Stream = 1 ------|
```

**Payload: the header block (offsets 0x0c to 0x44).** Each field is
`index`, then a 2-octet length, then the value.

| Offset | Octets | Meaning |
|---|---|---|
| `0c` | `01` | Index 0x01 = `:method` (static table) |
| `0d` | `00 03` | Value length 3 |
| `0f` | `47 45 54` | `"GET"` |
| `12` | `02` | Index 0x02 = `:path` |
| `13` | `00 07` | Value length 7 |
| `15` | `2f 68 69 2e 74 78 74` | `"/hi.txt"` |
| `1c` | `04` | Index 0x04 = `host` |
| `1d` | `00 09` | Value length 9 |
| `1f` | `6c 6f 63 61 6c 68 6f 73 74` | `"localhost"` |
| `28` | `05` | Index 0x05 = `user-agent` |
| `29` | `00 14` | Value length 20 |
| `2b` | `62 63 75 72 6c 2f 31 20 28 32 34 62 63 73 31 30 32 30 39 29` | `"bcurl/1 (24bcs10209)"` |
| `3f` | `06` | Index 0x06 = `accept` |
| `40` | `00 03` | Value length 3 |
| `42` | `2a 2f 2a` | `"*/*"`. The last octet is 0x44, exactly where Length said the frame would end. |

All five names are in the static table, so **no header name is spelled out**
on the wire: each one costs a single octet. The same request as HTTP/1.1 text
(`GET /hi.txt HTTP/1.1` plus Host, User-Agent and Accept) is 88 octets. Here it
is 65 (8 + 57), and finding where it ends requires no searching for `\r\n`.

### 1c. GOAWAY: the client is done (offsets 0x45 to 0x50, 8 + 4 octets)

| Offset | Octets | Meaning |
|---|---|---|
| `45` | `00 00 04` | Length 4 |
| `48` | `02` | Type **GOAWAY** |
| `49` | `00` | No flags |
| `4a` | `00 00 00` | Stream 0: this frame is about the connection, not a request. |
| `4d` | `64 6f 6e 65` | Reason `"done"`. The server logs it and closes. |

---

## 2. Server to client: `captures/response.s2c.bin`

```
00000000: 0000 5e01 0000 0001 0300 0332 3030 0700  ..^........200..
00000010: 1974 6578 742f 706c 6169 6e3b 2063 6861  .text/plain; cha
00000020: 7273 6574 3d75 7466 2d38 0800 0133 0900  rset=utf-8...3..
00000030: 1562 7365 7276 652f 3120 2832 3462 6373  .bserve/1 (24bcs
00000040: 3130 3230 3929 0a00 1d46 7269 2c20 3235  10209)...Fri, 25
00000050: 2053 6570 2032 3032 3620 3133 3a33 363a   Sep 2026 13:36:
00000060: 3334 2047 4d54 0000 0300 0100 0001 6869  34 GMT........hi
00000070: 0a                                       .
```

### 2a. HEADERS frame: status and metadata (offsets 0x00 to 0x65, 8 + 94 octets)

| Offset | Octets | Meaning |
|---|---|---|
| `00` | `00 00 5e` | Length 0x5e = **94**. The frame ends at 0x08 + 94 = **0x66**. |
| `03` | `01` | Type **HEADERS** |
| `04` | `00` | Flags: **not** END_STREAM, so DATA follows on this stream. |
| `05` | `00 00 01` | Stream **1**: this answers the request in §1b. |
| `08` | `03` | Index 0x03 = `:status` |
| `09` | `00 03` | Value length 3 |
| `0b` | `32 30 30` | `"200"` (three ASCII digits, as SPEC §5 requires) |
| `0e` | `07` | Index 0x07 = `content-type` |
| `0f` | `00 19` | Value length 25 |
| `11` | `74 65 78 74 2f 70 6c 61 69 6e 3b 20 63 68 61 72 73 65 74 3d 75 74 66 2d 38` | `"text/plain; charset=utf-8"` |
| `2a` | `08` | Index 0x08 = `content-length` |
| `2b` | `00 01` | Value length 1 |
| `2d` | `33` | `"3"`: the DATA frames must carry exactly 3 octets in total. |
| `2e` | `09` | Index 0x09 = `server` |
| `2f` | `00 15` | Value length 21 |
| `31` | `62 73 65 72 76 65 2f 31 20 28 32 34 62 63 73 31 30 32 30 39 29` | `"bserve/1 (24bcs10209)"` |
| `46` | `0a` | Index 0x0a = `last-modified` |
| `47` | `00 1d` | Value length 29 |
| `49` | `46 72 69 2c 20 32 35 20 53 65 70 20 32 30 32 36 20 31 33 3a 33 36 3a 33 34 20 47 4d 54` | `"Fri, 25 Sep 2026 13:36:34 GMT"`. The last octet is 0x65. |

### 2b. DATA frame: the body (offsets 0x66 to 0x70, 8 + 3 octets)

| Offset | Octets | Meaning |
|---|---|---|
| `66` | `00 00 03` | Length 3 |
| `69` | `00` | Type **DATA** |
| `6a` | `01` | Flags **END_STREAM**: the response is complete. |
| `6b` | `00 00 01` | Stream 1 |
| `6e` | `68 69 0a` | Body `"hi\n"`. That is 3 octets, matching `content-length`, and it is what `bcurl` writes to stdout. |

The connection is still open after this frame, and stream 2 could follow on
the same socket. Here the client ends it with the GOAWAY in §1c.

---

## 3. An unknown frame type being skipped

`captures/grease-response.s2c.bin` is the same request answered by
`./bserve --grease`. The server deliberately sends a frame of an unknown type
first, to exercise the rule from the brief that receivers MUST skip it:

```
00000000: 0000 15fa 0000 0001 736b 6970 206d 652c  ........skip me,
00000010: 2049 2061 6d20 6672 6f6d 2076 3200 005e   I am from v2..^
00000020: 0100 0000 0103 0003 3230 3007 0019 7465  ........200...te
...
```

| Offset | Octets | Meaning |
|---|---|---|
| `00` | `00 00 15` | Length 0x15 = **21** |
| `03` | `fa` | Type **0xFA**, which v1 does not define |
| `04` | `00` | No flags |
| `05` | `00 00 01` | Stream 1 |
| `08` | `73 6b 69 70 20 6d 65 2c 20 49 20 61 6d 20 66 72 6f 6d 20 76 32` | `"skip me, I am from v2"`. The receiver discards exactly these 21 octets without interpreting them. |
| `1d` | `00 00 5e 01 …` | The next frame header, the normal HEADERS from §2a, which is processed as usual. |

`bcurl -v` shows the skip, and it still hexdumps the skipped octets:

```
< UNKNOWN(0xfa) stream=1 len=21 flags=-  (unknown type: skipped)
<   00000000  00 00 15 fa 00 00 00 01                           |........|
<   00000008  73 6b 69 70 20 6d 65 2c  20 49 20 61 6d 20 66 72  |skip me, I am fr|
<   00000018  6f 6d 20 76 32                                    |om v2|
* unknown frame type 0xfa (21 octets) skipped
```

The independent C client `interop/bget` skips the same frame:
`bget: skipped unknown frame type 0xfa (21 octets)`. Neither client knew in
advance what 0xFA means. Each read Length, stepped over the payload, and
carried on, which is exactly the version-2 escape hatch the spec promises.
