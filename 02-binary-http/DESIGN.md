# BHP/1 design notes: defending the widths

*Hemang (Roll No. 24bcs10209). This is the companion to SPEC.md. The spec
says what to send; this file says why. The history below was checked
against the SPDY/3 draft and the IETF HTTP/2 drafts 04, 13 and 14.*

## 1. Why HTTP/2 chose 24 / 8 / 8 / 31

HTTP/2's frame header (RFC 9113 §4.1) is 9 octets:
`Length:24 Type:8 Flags:8 R:1 StreamID:31`. Each width has a history.

| Step | Header | Length field | Notes |
|---|---|---|---|
| SPDY/3 (HTTP/2's starting point) | 8 octets | 24 bits | The first bit was a **control bit**. Data frames used `C=0, StreamID:31, Flags:8, Length:24`; control frames used `C=1, Version:15, Type:16`. |
| HTTP/2 draft 04 | 8 octets | **16 bits** | `Length:16 Type:8 Flags:8 R:1 StreamID:31`, so frames were capped at 64 KiB−1. |
| HTTP/2 draft 13 | 8 octets | **14 bits** | 2 reserved bits plus a 14-bit length, so frames were capped at 16 383 octets. |
| HTTP/2 draft 14 → RFC | **9 octets** | **24 bits** | Added `SETTINGS_MAX_FRAME_SIZE`, with a default of 2^14 and a maximum of 2^24−1. |

- **Why 31 and not 32.** The stream ID inherits SPDY's layout, where the top
  bit of that word was the control/data flag. When HTTP/2 moved
  "control or data" into the Type octet, the bit was left over. RFC 9113 only
  says that bit is reserved: its semantics are undefined, it MUST be sent as
  0, and it MUST be ignored. A commonly cited side benefit is that a 31-bit ID
  always fits in a *signed* 32-bit integer, as in Java. That is folklore, not
  the RFC's stated reason.
  - Streams are opened by both peers: clients use odd IDs and servers use even
    IDs (for push). IDs are never reused, so when they run out the connection
    is replaced via GOAWAY.
- **Why Length 24, and why accept 9 octets.**
  - Frames are HTTP/2's unit of interleaving. If a stream sends huge frames,
    every other stream on the connection waits behind them. So the *default*
    stays small (16 KiB, close to the draft-13 cap), and a receiver can opt in
    to larger frames for bulk transfer.
  - 14 or 16 bits made that opt-in impossible, and 24 bits allows 16 MiB
    frames. The working group judged that worth losing 8-octet alignment.
  - The width is not the protection. The negotiated limit is.
- **Why Type 8.** HTTP/2 defines 10 frame types, and RFC 9113 §5.5 lets
  extensions add more. Receivers MUST ignore unknown types, which is the same
  rule BHP/1 makes non-negotiable.
- **Why Flags 8.** These are per-type booleans: END_STREAM, END_HEADERS,
  PADDED and PRIORITY, plus **ACK** on SETTINGS and PING. One octet keeps the
  fields byte-aligned and leaves spare bits for each type.

## 2. BHP/1's header: `Length:24 Type:8 Flags:8 StreamID:24` (8 octets)

**8 octets in total.** The header is exactly two aligned 32-bit words, the
same size as SPDY's header and early HTTP/2's. BHP/1 can afford 24 bits of
length *and* stay at 8 octets for two reasons:
- It does not need SPDY's control bit or HTTP/2's reserved bit.
- It does not need 31 bits of stream ID, because there is no server push and
  no multiplexing.

**Length: 24 bits.**
- 16 bits would cap every frame at 65 535 octets forever. HTTP/2's own path
  (16 bits, then 14, then 24) shows that a fixed small cap gets regretted.
- 32 bits would cost an octet on every frame, for sizes no receiver wants in
  one frame.
- 24 is the next byte boundary after 16.
- As in HTTP/2, **protection comes from receiver limits, not from the width**.
  SPEC §2 says receivers must accept 16 KiB DATA and 64 KiB HEADERS payloads,
  may refuse anything larger, and still skip past it using Length, so the
  connection stays in sync.
- Senders split bodies into 16 KiB DATA frames, which matches HTTP/2's
  default and leaves v2 room to interleave streams.

**Type: 8 bits.**
- v1 uses 3 values (DATA, HEADERS, GOAWAY) and reserves 253.
- 4 bits would be enough today, but would save only half an octet at the cost
  of alignment.
- The width matters only because receivers MUST skip unknown types: a v2
  sender can add a type, and a v1 receiver steps over it using Length alone.

**Flags: 8 bits.**
- v1 needs only one flag (END_STREAM), so on its own this would argue for
  1 bit.
- Any width below 8 would need shift-and-mask on both ends, and alignment
  would pad the field back to a byte anyway. So the other 7 bits cost nothing.
- Receivers MUST ignore flag bits they do not know, which makes those 7 bits
  a safe place for v2 to add things like PADDED, PRIORITY or compression.

**Stream ID: 24 bits.** BHP/1 answers requests in order, so why carry an ID
at all?
1. **Self-checking pipelines.** A client can verify that each response
   belongs to the request it expects, instead of trusting order. `bcurl` and
   `bget` treat a frame for the wrong stream as a protocol error. The server
   rejects a stream ID that does not increase. A desynchronised connection
   therefore fails loudly instead of returning the wrong file.
2. **Connection versus request.** Stream 0 carries GOAWAY and errors that
   belong to no request.
3. **Multiplexing in v2 without a new header.** A version 2 only has to relax
   SPEC §4's rule that "a request's frames are contiguous".

Why 24 bits and not 16 or 31:
- **Against 16.** 65 535 requests per connection is reachable. A pipelining
  client doing 1 000 small requests per second exhausts it in about
  65 seconds, then must reconnect (TCP handshake plus slow start).
- **For 24.** 16 777 215 requests per connection is about 4.6 hours at that
  rate. It costs one octet more than 16 bits, and that octet is exactly what
  completes the 8-octet header.
- **Against 31.** 31 bits would need a ninth octet, as in HTTP/2, which BHP/1
  cannot justify without multiplexing.
- **No reserved bit, no odd/even split.** Nothing in v1 needs a reserved bit.
  Version 2 room is already provided by the reserved types, the reserved
  header indices and the spare flag bits. Servers never open streams, so
  there is no need to split IDs by parity.

## 3. Header block: HPACK's first two mechanisms

**Static table: names only, as the brief asks ("number the ten names you
actually send").**
- `bcurl` sends `:method`, `:path`, `host`, `user-agent` and `accept`.
- `bserve` sends `:status`, `content-type`, `content-length`, `server` and
  `last-modified`.
- Each name costs 1 octet instead of its full spelling.

HPACK's real static table (61 entries) also has name+value pairs such as
`:method GET` and `:status 200`, so the most common fields cost 1 octet in
total. BHP/1 deliberately does not do that:
- **Cost.** It would save about 5 octets per request and per response, but
  would double the table. A stranger implementing it in an evening would then
  face two kinds of entry.
- **Uniformity.** Every field stays "index plus length-prefixed value".
- **Room.** A v2 can add value-carrying entries in the reserved index range
  `0x0B`–`0xFF` without breaking v1, because v1 receivers skip unknown
  indices.

**Length-prefixed literals, HPACK's second mechanism, for everything else.**
- **`name_len:8`.** Header names are short tokens, and 255 octets is far
  beyond any real one.
- **`value_len:16`.** This allows values up to 65 535 octets.
  - 8 bits (255) would be too small, since URLs of 2 000+ characters are
    common.
  - 32 bits would cost 2 more octets per field, to describe values that the
    64 KiB block limit refuses anyway.
  - SPEC §5 says a field must also fit inside the block limit.
- **Fixed-width lengths.** These replace HPACK's prefix-coded integers so
  parsing is a straight sequence of reads.

**Deliberately left out: HPACK's dynamic table and Huffman coding.**
- Without a dynamic table, every HEADERS frame can be decoded on its own,
  with no connection-wide state that the two peers could disagree on.
- It also leaves no compression state for CRIME-style attacks to probe.

**`:status` is three ASCII digits**, as in HTTP/2, not a binary u16. That
keeps one value encoding for every field, at the cost of one extra octet.

## 4. The preface `42 48 50 01`

- **Fail-fast detection.** An HTTP/1.1 client that connects by mistake sends
  `GET `. Without a magic, that would parse as a 4.6 MB frame of type 0x20,
  and the server would sit waiting for it. HTTP/2's 24-octet
  `PRI * HTTP/2.0` preface exists for the same reason.
- **Version negotiation.** The version byte lets a v1 server answer a v2
  client with `505` instead of misparsing it.

## 5. Size, measured

These numbers come from HEXDUMP.md.

| Message | HTTP/1.1 text | BHP/1 |
|---|---|---|
| `GET /hi.txt` with Host, User-Agent and Accept | 88 octets | 65 octets |

BHP/1 also needs **zero** scanning for `\r\n`: every boundary is given by a
length.
