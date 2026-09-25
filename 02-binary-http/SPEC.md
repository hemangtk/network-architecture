# BHP/1: HTTP semantics in binary frames

*Author: Hemang (Roll No. 24bcs10209). Status: v1, complete. Two pages.*

The key words MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119.
All multi-octet integers are **unsigned and big-endian** (network order).

## 1. Connection

BHP/1 runs over one TCP connection (no default port; the examples use 9000).

1. After connecting, the client sends the 4-octet **preface** `42 48 50 01`
   (`"BHP"` then version `0x01`), exactly once, before its first frame.
   If the server reads anything else, it SHOULD answer with a `400` response on
   stream 0 and a GOAWAY frame, then MUST close. (An HTTP/1.1 client that
   connects by mistake sends `GET `, so it fails straight away instead of being
   read as a 4.6 MB frame.)
2. After the preface, both sides send only frames (§2). The server sends no
   preface.
3. The connection **stays open** after each response. Either side ends it by
   sending GOAWAY and then closing, or by simply closing. A server MAY close a
   connection that has been idle for a while, and SHOULD send GOAWAY first.
4. A client MUST NOT open a second connection to send more requests. It reuses
   the one it has.

## 2. Frame layout

Every frame is an 8-octet header followed by `Length` octets of payload.

```
  0                   1                   2                   3
  0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
 +-----------------------------------------------+---------------+
 |                  Length (24)                  |   Type (8)    |
 +---------------+-----------------------------------------------+
 |   Flags (8)   |                 Stream ID (24)                |
 +---------------+-----------------------------------------------+
 |                    Payload (Length octets) ...                |
```

| Field | Bits | Meaning |
|---|---|---|
| Length | 24 | Number of payload octets, 0 to 16 777 215. It does not count the 8 header octets. |
| Type | 8 | Frame type (§3). |
| Flags | 8 | Per-type flags. A receiver MUST ignore flag bits it does not know. |
| Stream ID | 24 | Which request/response this frame belongs to. 0 means the connection itself. |

**Why these widths.** The header is **8 octets**, so one 64-bit read loads all
of it, and the receiver knows the full frame size before it reads any payload.
**Length is 24 bits** because 32 bits would let a peer declare a 4 GiB frame
and force the receiver to buffer it or give up. 24 bits caps a frame at 16 MiB.
Bodies are split into DATA frames of at most 16 384 octets (§4), so no single
frame holds the line for long. **Type is 8 bits**: v1 uses 3 of the 256 values,
which leaves plenty of room for later versions. **Flags is 8 bits** because v1
needs only one flag. **Stream ID is 24 bits**, which allows 16.7 million
requests per connection, and a client can simply open a new connection after
that. HTTP/2 needed 32 bits here: 31 bits for the ID plus a reserved bit,
because it lets both sides open streams and multiplex them. BHP/1 does not
multiplex, so it saves the octet. That octet is what makes the header 8 octets
instead of 9.

**HTTP/2 chose 24/8/8/31. Why?**
- **Length 24:** the default frame limit is 16 KiB, and SETTINGS can raise it
  up to 2^24−1. That is big enough for bulk transfer but small enough that one
  stream cannot block the other streams sharing the connection for long.
- **Type 8 and Flags 8:** space for new frame types and flags, which is how
  extensions are added.
- **Stream ID 31:** the top bit is reserved (it must be sent as 0 and ignored
  when read), so the ID is always a non-negative number even in languages
  without unsigned 32-bit integers, like Java. Streams are split by parity:
  odd IDs are opened by the client, even IDs by the server for push. The total
  header is 9 octets.

## 3. Frame types

| Type | Name | Stream | Payload |
|---|---|---|---|
| `0x00` | DATA | ≠ 0 | Body octets. |
| `0x01` | HEADERS | ≠ 0 | One header block (§5). The whole block MUST fit in one frame. |
| `0x02` | GOAWAY | 0 | Optional UTF-8 reason. The sender closes after sending it. |
| `0x03`–`0xFF` | *(reserved)* | any | Meaning unknown to v1. |

Flag `0x01` = **END_STREAM** on DATA and HEADERS: this is the last frame of the
message.

> **A receiver meeting a frame type it does not know MUST skip it cleanly.** It
> reads and discards exactly `Length` payload octets, then carries on with the
> next frame header. It does not close the connection or send an error. That
> is how a v2 peer can add frame types without breaking v1 peers.

## 4. Messages

A **request** is one HEADERS frame, then zero or more DATA frames, all on the
same stream. The last frame carries END_STREAM. A request with no body is a
single HEADERS frame with END_STREAM set.

- The client picks each stream ID. IDs MUST be non-zero and SHOULD go up
  1, 2, 3, …
- In v1, a message's frames are **contiguous**: a new HEADERS frame MUST NOT
  appear until the previous message has ended. Unknown frame types MAY appear
  anywhere.
- A client MAY **pipeline**: send several requests without waiting. The server
  answers them **in the order received**, and each response carries the stream
  ID of its request.

A **response** is a HEADERS frame on the request's stream, then DATA frames.
The sender SHOULD keep each DATA frame at 16 384 octets or less. If the body is
empty, or the request was HEAD, the HEADERS frame alone carries END_STREAM. If
`content-length` is present, the DATA payloads MUST add up to exactly that many
octets.

**Server rules.**

| Case | Response |
|---|---|
| `:path` maps to a regular file under the root. A path that is a directory maps to its `index.html`. | `200` with the file bytes |
| No such file, or the path escapes the root (`..`) | `404` |
| `:method` is not `GET` or `HEAD` | `405` |
| Malformed header block, missing `:method` or `:path`, `:path` not starting with `/`, or HEADERS on stream 0 | `400` on that stream |

The server keeps the connection open after a `400`, because the 8-octet frame
header still says exactly where the next frame starts. The only times it must
close are a bad preface, or when the connection ends in the middle of a frame.

## 5. Header block

A header block is a sequence of **fields**, which runs until the end of the
frame payload. There is no count and no terminator: the frame's Length already
marks the end.

```
field   = index:8  [ name_len:8  name ]  value_len:16  value
          `------- only when index = 0 -------'
```

- `index = 0x00`: a **literal** name follows, `name_len` octets long (1 to
  255), in lowercase ASCII.
- `index = 0x01`–`0x0A`: the name comes from the **static table** below.
- `index = 0x0B`–`0xFF`: reserved. A receiver MUST skip the field. Its value
  is still length-prefixed, so it can be stepped over.
- `value_len` is 0 to 65 535 octets. The value is UTF-8.

**The static table** lists the ten names v1 actually sends:

| idx | name | idx | name |
|---|---|---|---|
| `01` | `:method` | `06` | `accept` |
| `02` | `:path` | `07` | `content-type` |
| `03` | `:status` | `08` | `content-length` |
| `04` | `host` | `09` | `server` |
| `05` | `user-agent` | `0A` | `last-modified` |

- A request MUST contain exactly one `:method` and one `:path`.
- A response MUST contain `:status` as three ASCII digits, for example `"200"`.
- Names starting with `:` are called pseudo-fields. They MAY appear in any
  order.

These are HPACK's first two mechanisms: a static table of indexed names, and
length-prefixed literals. BHP/1 does not use HPACK's dynamic table or Huffman
coding.

## 6. Worked example

The request `GET /hi.txt` on stream 1, as bytes. `HEXDUMP.md` annotates a
complete request and response captured from a real run.

```
42 48 50 01                     preface "BHP" v1
00 00 10 01 01 00 00 01         Length=16 Type=HEADERS Flags=END_STREAM Stream=1
01 00 03 47 45 54               :method (idx 1), len 3, "GET"
02 00 07 2f 68 69 2e 74 78 74   :path   (idx 2), len 7, "/hi.txt"
```

The response to it: `00 00 0a 01 00 00 00 01 03 00 03 32 30 30 08 00 01 33`
(HEADERS, stream 1, `:status "200"`, `content-length "3"`), then
`00 00 03 00 01 00 00 01 68 69 0a` (DATA, END_STREAM, `"hi\n"`).

## 7. Errors, briefly

| Situation | Action |
|---|---|
| Unknown frame type | Skip `Length` octets. |
| Unknown header index | Skip the field. |
| Unknown flag bit | Ignore it. |
| Bad field lengths inside a HEADERS frame | Respond `400` and keep the connection. |
| Bad preface, or the peer closes mid-frame | Close the connection. |

A client exits non-zero on any `4xx` or `5xx` response.
