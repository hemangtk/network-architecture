# Course project: HTTP in binary (BHP/1)

*Hemang, Roll No. 24bcs10209*

BHP/1 carries HTTP semantics in fixed-size binary frames. It comes with a
two-page spec, a server (`bserve`), a client (`bcurl`), and an annotated
hexdump of real wire traffic. It also includes independent implementations in
C and JavaScript (Node.js), written from the spec alone, to show that the spec
is the protocol and not just one program's behaviour.

## Quick check (one command)

```
./demo.sh                       # every line of the brief, PASS/FAIL, against a live server
python3 -m unittest test_bhp    # 109 end-to-end tests
```

Output of `./demo.sh` (abridged; the full run is under
[Captured output](#captured-output)):

```
Track 1 -- the server:  ./bserve ./www 9000
  PASS  accept a TCP connection, read one binary request frame, reply 200   [1 200 open]
  PASS  map the path to a file under a root; reply status, headers, bytes (48 KB byte-exact)
  PASS  404 if it is not there -- and the connection stays open   [1 404 2 200 open]
  PASS  400 if the frame is malformed -- and the connection stays open   [1 400 2 200 open]
  ...
17 passed, 0 failed
```

## What is handed in

| # | The brief asks for | File |
|---|---|---|
| 1 | **The spec.** Two pages, enough for a stranger. | [`SPEC.md`](SPEC.md), with [`SPEC.pdf`](SPEC.pdf) rendered at A4, 10.5 pt: **exactly 2 pages**. |
| | The defence of the widths, and the answer to "HTTP/2 chose 24/8/8/31. Why?" | [`DESIGN.md`](DESIGN.md) |
| 2 | **Your program.** Track 1 is the server, track 2 is the client. | [`bserve`](bserve), [`bcurl`](bcurl), and the shared [`bproto.py`](bproto.py) |
| 3 | **An annotated hexdump** of one complete request and response. | [`HEXDUMP.md`](HEXDUMP.md): every octet annotated by hand. The bytes were captured on the wire by [`tools/wiretap.py`](tools/wiretap.py) and are in [`captures/`](captures/). |
| + | **Pairs:** "a client that only works against your own server is an implementation, not a protocol". | Independent [`interop/bget.c`](interop/bget.c) (a C client) and [`interop/bserve.js`](interop/bserve.js) (a Node server), each written from the spec. [`interop/matrix.sh`](interop/matrix.sh) runs every client against every server. [`interop/stranger_client.py`](interop/stranger_client.py) was written blind from `SPEC.md`. |

## Requirement checklist

Each row of the brief, where it is implemented, and how it is proven.

| Brief | Implemented in | Proven by |
|---|---|---|
| `./bserve ./www 9000` | `bserve` `main()` takes ROOT and PORT | `demo.sh` |
| Accept a TCP connection | Thread per connection; the preface is checked first | `test_spec_worked_example` |
| Read one binary request frame | One HEADERS frame per request (8-octet header + header block) | `test_spec_worked_example` sends SPEC §6 byte for byte |
| Map the path to a file under a root | `Conn.resolve`: drop the query, percent-decode, `realpath`, root check, directory → `index.html` | `test_path_traversal_is_404`, `test_symlink_out_of_root_is_404`, `test_query_is_ignored_and_percent_decoding_applies` |
| Reply with status, headers, the bytes | HEADERS (`:status`, `content-type`, `content-length`, `server`, `last-modified`), then DATA frames of at most 16 KiB, streamed from disk | `test_body_split_into_16k_data_frames` (100 KB arrives as 7 frames), `test_bcurl_fetches_files_byte_exact` |
| 404 if it is not there | Yes. It also sends 403 for an unreadable file, 405 for other methods, and 500 for I/O errors. | `test_keeps_connection_open_across_statuses`, `test_unreadable_file_is_403_and_connection_survives` |
| 400 if the frame is malformed | Checked in exactly the order of SPEC §4's table, so 400 comes before 405. Covers: a malformed header block, a bad or repeated `content-length` or one that does not match the body, missing or repeated `:method`/`:path`, bad `:path`, HEADERS on stream 0, a stream ID that does not increase, an oversized header block, or a request cut short by new HEADERS or by DATA for another stream. Error responses to HEAD carry no DATA. | `test_malformed_header_block_is_400_and_connection_survives` (8 cases), `test_oversized_header_block_is_400_and_connection_survives`, `test_new_headers_before_end_stream_gets_400_then_is_served`, `test_data_for_another_stream_mid_request_is_400`, `test_stream_ids_must_increase`, `test_head_error_responses_carry_no_data`, `test_request_content_length_rules`, `test_400_row_is_checked_before_405`, `test_aborted_request_still_uses_up_its_stream_id` |
| And keep the connection open | Errors never close the connection, because Length keeps the framing in sync. It closes only for a bad preface, a peer closing mid-frame, GOAWAY, an idle timeout, or promised octets it cannot send. The server also survives running out of file descriptors. | `test_thousand_requests_one_connection`, `test_pipelined_requests_answered_in_order`, `test_concurrent_clients` (20 × 10), `test_survives_running_out_of_file_descriptors` |
| `./bcurl -v localhost:9000/index.html` | `bcurl` takes `HOST:PORT/PATH` (or `bhp://…`) | `demo.sh` |
| Build the binary request frame | `Client.send_request` | `test_request_bytes_follow_the_spec` |
| Read the response, body to stdout | The body is streamed to stdout as DATA arrives (never fully buffered), and the client aborts the moment it exceeds `content-length`. The log goes to stderr. | `test_body_to_stdout_exit_zero_and_preface`, `test_body_beyond_content_length_aborts_immediately`, `test_closed_stdout_exits_quietly` |
| `-v` hexdumps every frame | Preface, every sent and received frame, and unknown frames including their skipped payload. `-vv` also annotates each field. | `test_verbose_hexdumps_every_frame`, `test_verbose_hexdumps_skipped_unknown_payload_too`, `test_vv_annotates_preface_and_fields` |
| Exit non-zero on 4xx / 5xx | `4` for 4xx, `5` for 5xx, `3` for a protocol or connection error, `2` for usage errors | `test_exit_non_zero_on_4xx_and_5xx`, `test_invalid_status_is_protocol_error`, `test_non_numeric_content_length_is_protocol_error` |
| And never open a second connection | Several URLs share one socket. `--pipeline` keeps up to 32 requests in flight, so 20,000 pipelined requests cannot deadlock. URLs for different hosts are refused. | `test_never_opens_a_second_connection` (it counts accepts), `test_refuses_urls_that_would_need_two_connections`, `test_pipeline_twenty_thousand_requests_no_deadlock` |
| Fixed-size frame header; pick the widths and defend them | `Length:24 Type:8 Flags:8 Stream:24`, 8 octets | SPEC §2, DESIGN §2 |
| HTTP/2 chose 24/8/8/31. Why? | | DESIGN §1, including the draft-13 history: an 8-octet header with a 14-bit length, changed to 24 bits plus `SETTINGS_MAX_FRAME_SIZE` |
| Number the ten names you send; length-prefix the rest | Static table `0x01`–`0x0A`; literal names are `0x00 name_len:8 name`; values are `value_len:16` | SPEC §5, DESIGN §3, `test_unknown_header_index_and_literal_names_are_accepted` |
| An unknown frame type MUST be skipped cleanly | All four implementations skip exactly Length octets. `bserve --grease` sends unknown frames to prove clients do this. | `test_unknown_frame_types_are_skipped` (including a 200 KB unknown frame), `test_skips_unknown_frames_and_header_indices`, HEXDUMP §3 |
| Room for version 2 | Reserved types `0x03`–`0xFF`, reserved header indices `0x0B`–`0xFF`, 7 spare flag bits that must be ignored, and a version byte in the preface (a mismatch gets `505`) | `test_unknown_flag_bits_are_ignored`, `test_wrong_version_is_505_goaway_close` |

## Pairs: the spec is the only thing that crosses

The brief asks for the protocol to work between programs that share nothing
but the spec. These implementations, in three languages, are all my own work.
Each was written against `SPEC.md` and they share no code, but they are not a
substitute for a partner's implementation. If a partner's client or server is
available, it can be dropped into `interop/matrix.sh` as another row or
column.

| | `bserve` (Python) | `interop/bserve.js` (Node) |
|---|---|---|
| `bcurl` (Python) | PASS | PASS |
| `interop/bget.c` (C) | PASS | PASS |
| `interop/stranger_client.py` (written blind from SPEC.md) | PASS | PASS |

- **The server test suite runs against both servers.** The 37 server tests
  in `test_bhp.py` build their bytes by hand from the spec with
  `struct.pack`, not with this project's encoder. `NodeServerTest` runs the
  same suite against the Node server, and both pass.
- **The client tests use fake servers.** They point `bcurl` at fake servers
  that send only what the spec allows: unknown frames, unknown header
  indices, `505` on stream 0, and malformed `:status` or `content-length`.

## Run it

```
./bserve ./www 9000                       # -v logs frames, --grease sends unknown frames
./bcurl -v localhost:9000/index.html      # -vv annotates fields; -I = HEAD; --pipeline
./bcurl localhost:9000/hi.txt localhost:9000/docs/big.txt   # both on ONE connection
./interop/matrix.sh                       # every client x every server
python3 -m unittest -v test_bhp.py        # 109 tests
python3 tools/render_spec.py SPEC.md SPEC.pdf   # prints "pages: 2"
```

It needs Python 3.8+ only. Node and a C compiler are optional, for the
interop programs.

## Captured output

All output below comes from real runs (macOS, Python 3.14, Node 25, Apple
clang).

<details><summary><code>./demo.sh</code>: 17 passed, 0 failed</summary>

```
Track 1 -- the server:  ./bserve ./www 9000
  PASS  accept a TCP connection, read one binary request frame, reply 200   [1 200 open]
  PASS  map the path to a file under a root; reply status, headers, bytes (48 KB byte-exact)
  PASS  404 if it is not there -- and the connection stays open   [1 404 2 200 open]
  PASS  400 if the frame is malformed -- and the connection stays open   [1 400 2 200 open]
  PASS  paths cannot escape the root (../, %2e%2e)   [1 404 2 404 open]
  PASS  and keep the connection open: 4 requests, 1 connection   [1 200 2 200 3 200 4 200 open]

Track 2 -- the client:  ./bcurl -v localhost:9000/index.html
  PASS  build the binary request frame; read the response; body to stdout
  PASS  -v hexdumps every frame: preface, request, HEADERS, 3 DATA, GOAWAY = 7 dumps
  PASS  exit non-zero on 4xx  (exit 4)
  PASS  exit non-zero on 4xx: 405 for DELETE  (exit 4)
  PASS  and never open a second connection (3 URLs -> 1 connection)
  PASS  refuses URLs that would need a second connection (exit 2)

The middle -- the protocol
  PASS  a receiver meeting an unknown frame type MUST skip it cleanly (server)   [1 200 open]
  PASS  ... and the client skips the server's unknown 0xFA frames too
  PASS  version byte in the preface: v2 preface gets 505, GOAWAY, close   [0 505 GOAWAY closed]
  PASS  an HTTP/1.1 client fails fast: 400 on stream 0, GOAWAY, close   [0 400 GOAWAY closed]

Pairs -- the only thing that crosses is the spec
  PASS  interop matrix: bcurl, bget (C), stranger client  x  bserve, bserve.js (Node) -- 6/6 cells pass

17 passed, 0 failed
```
</details>

<details><summary><code>./bcurl -v localhost:9000/index.html</code></summary>

```
* connected to localhost:9000 (one TCP connection for everything)
> PREFACE
>   00000000  42 48 50 01                                       |BHP.|
> HEADERS stream=1 len=61 flags=END_STREAM
>   00000000  00 00 3d 01 01 00 00 01  01 00 03 47 45 54 02 00  |..=........GET..|
>   00000010  0b 2f 69 6e 64 65 78 2e  68 74 6d 6c 04 00 09 6c  |./index.html...l|
>   00000020  6f 63 61 6c 68 6f 73 74  05 00 14 62 63 75 72 6c  |ocalhost...bcurl|
>   00000030  2f 31 20 28 32 34 62 63  73 31 30 32 30 39 29 06  |/1 (24bcs10209).|
>   00000040  00 03 2a 2f 2a                                    |..*/*|
< HEADERS stream=1 len=95 flags=-
<   00000000  00 00 5f 01 00 00 00 01  03 00 03 32 30 30 07 00  |.._........200..|
<   00000010  18 74 65 78 74 2f 68 74  6d 6c 3b 20 63 68 61 72  |.text/html; char|
<   00000020  73 65 74 3d 75 74 66 2d  38 08 00 03 31 37 30 09  |set=utf-8...170.|
<   00000030  00 15 62 73 65 72 76 65  2f 31 20 28 32 34 62 63  |..bserve/1 (24bc|
<   00000040  73 31 30 32 30 39 29 0a  00 1d 46 72 69 2c 20 32  |s10209)...Fri, 2|
<   00000050  35 20 53 65 70 20 32 30  32 36 20 31 33 3a 33 35  |5 Sep 2026 13:35|
<   00000060  3a 31 34 20 47 4d 54                              |:14 GMT|
< DATA stream=1 len=170 flags=END_STREAM
<   00000000  00 00 aa 00 01 00 00 01  3c 21 64 6f 63 74 79 70  |........<!doctyp|
<   00000010  65 20 68 74 6d 6c 3e 0a  3c 68 74 6d 6c 3e 0a 3c  |e html>.<html>.<|
    ... (170 body octets, all dumped) ...
* stream 1: GET /index.html -> 200
<!doctype html>
<html>
<head><title>BHP/1</title></head>
<body>
<h1>Hello over BHP/1</h1>
<p>Served by bserve, fetched by bcurl. Roll No. 24bcs10209.</p>
</body>
</html>
> GOAWAY stream=0 len=4 flags=-
>   00000000  00 00 04 02 00 00 00 00  64 6f 6e 65              |........done|
```
</details>

<details><summary>Interop matrix: <code>./interop/matrix.sh</code></summary>

```
Interop matrix (each cell: /hi.txt, /docs/big.txt, /missing on one connection)
  bcurl            -> bserve (Python, --grease) PASS
  bget (C)         -> bserve (Python, --grease) PASS
  stranger_client  -> bserve (Python, --grease) PASS
  bcurl            -> bserve.js (Node)       PASS
  bget (C)         -> bserve.js (Node)       PASS
  stranger_client  -> bserve.js (Node)       PASS
6/6 cells pass
```
</details>

## Files

| Path | What |
|---|---|
| `bserve`, `bcurl`, `bproto.py` | The server, the client, and shared framing and encoding (Python 3 standard library only). |
| `SPEC.md` / `SPEC.pdf` | The normative spec, two pages. |
| `DESIGN.md` | The defence of every width, and the HTTP/2 comparison. |
| `HEXDUMP.md`, `captures/`, `hexdump-capture.txt` | The annotated wire capture, the raw bytes, and `bcurl -vv`'s independent decoding of them. |
| `interop/` | The C client, the Node server, the blind stranger client, and the matrix script. |
| `tools/wiretap.py`, `tools/render_spec.py` | The capture relay, and the spec → PDF renderer with page count. |
| `test_bhp.py`, `demo.sh` | 109 end-to-end tests, and the brief-by-brief demo. |
| `www/` | The document root used in the examples. |
