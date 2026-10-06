# BHP/1: HTTP semantics in binary frames

*Hemang (Roll No. 24bcs10209). Normative. Rationale: DESIGN.md. Annotated bytes: HEXDUMP.md.*

Key words as in RFC 2119. All integers are unsigned, big-endian.

## 1. Connection

1. The client opens **one** TCP connection and sends the preface `42 48 50 01` ("BHP", version 1) once, before any frame. The server sends no preface.
2. `42 48 50` with another version byte gets `505` on stream 0; any other preface gets `400` on stream 0. Either way the server then sends GOAWAY and closes.
3. After the preface, both sides send only frames. The connection **stays open** after every response, including errors. A server MAY close an idle connection (or an incomplete preface) after sending GOAWAY.
4. A client MUST NOT open a second connection while the first is usable, i.e. until a GOAWAY or until stream ID 16 777 215 is used.

## 2. Frame header (8 octets, fixed)

```
| Length (24) | Type (8) | Flags (8) | Stream ID (24) | Payload (Length octets) ...
```

**Length** counts payload octets (0 to 16 777 215, excluding the 8 header octets). **Type** is the frame type (§3). **Flags** are per-type; receivers MUST ignore flag bits they do not know. **Stream ID** pairs a response with its request; stream 0 is the connection itself.

A receiver MUST accept DATA payloads of up to 16 384 octets and HEADERS payloads of up to 65 536 octets. It MAY refuse larger ones, but it still reads past them using Length. A server refusing one answers that request with `400`.

*Why, in short:* two aligned 32-bit words; 24 bits is the first byte width above a 64 KiB cap; 253 reserved types and 7 spare flag bits leave room for v2; a per-request ID lets pipelined answers be checked.

## 3. Frame types

| Type | Name | Payload |
|---|---|---|
| `0x00` | DATA | Body octets. Flag `0x01` END_STREAM marks the last frame of a message. |
| `0x01` | HEADERS | One complete header block (§5). END_STREAM as for DATA. |
| `0x02` | GOAWAY | Sent on stream 0, but means "connection" on any stream. Optional UTF-8 reason. The sender sends nothing after it, then closes. |
| `0x03`–`0xFF` | Reserved | For later versions. |

> **A receiver meeting a frame type it does not know MUST skip it cleanly.** It discards exactly Length payload octets and reads the next frame header. It does not report an error or close the connection. That is how version 2 can add frame types.

## 4. Requests and responses

A **request** is one HEADERS frame, then zero or more DATA frames on the same stream. Its last frame carries END_STREAM.

- The client numbers requests 1, 2, 3, … and never reuses a number. An ID is used up even if its request fails. The server answers `400` to a stream ID that is not greater than the previous one.
- A request's `content-length`, if sent, must equal its DATA total, else `400`.
- A client MAY **pipeline** (send requests before earlier responses arrive). A request's frames are contiguous; only unknown types may appear between them.
- While a request is unfinished, two things end it with `400`: a new HEADERS frame (which is then processed normally), or DATA for a different stream.
- A GOAWAY drops an unfinished request without a response. DATA arriving when no request is open is discarded.

A **response** is one HEADERS frame on the request's stream, then DATA frames of at most 16 384 octets each. Its last frame carries END_STREAM.

- Responses are sent **in request order**.
- When `content-length` is present, DATA octets MUST total exactly that, except that a HEAD response has no DATA (its `content-length` is the GET body's size).
- HEADERS on **stream 0** is allowed only from server to client. It reports an error that belongs to no request: a bad preface, a bad version, or HEADERS received on stream 0. It is the final answer on that connection: the client treats it as the response to every request still outstanding.
- Errors to HEAD carry no DATA, except when the single `:method` itself cannot be determined (malformed block, missing or repeated `:method`).

**`:path` → file.** `:path` starts with `/` and may carry a `?query`. A client strips any `#fragment` before sending. The server drops the query, percent-decodes the rest once as UTF-8, and resolves the result under its root. A directory serves its `index.html`.

| Checked in this order | Status |
|---|---|
| Malformed block (§5); `:path` not starting with `/`, containing `#`, or decoding to invalid UTF-8; HEADERS on stream 0; non-increasing stream ID | `400` |
| `:method` other than `GET` or `HEAD` | `405` |
| No such file (including a directory without `index.html`), or the path resolves outside the root (through `..` or a symlink) | `404` |
| The file (or a directory on its path) cannot be read | `403` |
| Any other I/O failure / otherwise (body = the file's octets) | `500` / `200` |

Errors carry a short `text/plain` body and leave the connection open. The server closes only after a bad preface or version, when the peer closes mid-frame or sends GOAWAY, after an idle timeout, or when it cannot send octets it has promised (it MUST close rather than send fewer).

## 5. Header block

A header block is a sequence of fields that runs to the end of the frame payload. There is no count and no terminator.

```
field = index:8 [ name_len:8 name ] value_len:16 value     ; [..] only if index = 0
```

- **`0x00`:** a literal name follows, 1 to 255 octets of ASCII. Senders use lowercase; receivers lowercase what they get and accept a literal spelling of a table name.
- **`0x01`–`0x0A`:** a name from the static table below. **`0x0B`–`0xFF`:** reserved; a receiver MUST skip the field (its value is still length-prefixed).
- **Values** are 0 to 65 535 octets of UTF-8; the whole block must fit the HEADERS limit (§2).

**Static table** (the ten names v1 sends): `01 :method`, `02 :path`, `03 :status`, `04 host`, `05 user-agent`, `06 accept`, `07 content-type`, `08 content-length`, `09 server`, `0A last-modified`.

A block is **malformed** if a length runs past the payload, `name_len` is 0, a name is not ASCII or a value is not UTF-8, a request lacks exactly one `:method` and one `:path`, a response lacks exactly one `:status` of three ASCII digits from `200` to `599` (v1 has no 1xx), or `content-length` repeats or is not all digits. Other fields MAY repeat.

**Client outcome.** A client exits with code 5 if any response was 5xx, otherwise with 4 if any was 4xx, otherwise with 0. It exits with 3 on a connection failure or a protocol error: a malformed response, DATA on a HEAD response, DATA octets that do not match `content-length`, or a frame (other than GOAWAY or a stream-0 error) for a stream it is not waiting on.

## 6. Worked example: `GET /hi.txt` on stream 1

```
42 48 50 01                       preface "BHP" v1
00 00 10 01 01 00 00 01           Length 16, HEADERS, END_STREAM, stream 1
01 00 03 47 45 54                 :method (index 01), length 3, "GET"
02 00 07 2f 68 69 2e 74 78 74     :path (index 02), length 7, "/hi.txt"
00 00 0a 01 00 00 00 01           response: Length 10, HEADERS, stream 1
03 00 03 32 30 30 08 00 01 33     :status "200", content-length "3"
00 00 03 00 01 00 00 01 68 69 0a  DATA, END_STREAM, stream 1, "hi\n"
```
