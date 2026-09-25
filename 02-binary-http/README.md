# Course project: HTTP in binary (BHP/1)

*Hemang, Roll No. 24bcs10209*

A binary framing of HTTP semantics, delivered as a spec, a server, a client,
and an annotated hexdump.

## What is handed in

| # | Deliverable | File |
|---|---|---|
| 1 | **The spec.** About two pages, meant to be enough for a stranger to implement from. It covers the frame layout, why each field has the width it does, why HTTP/2 chose 24/8/8/31, the header block format, and the rule to skip unknown frame types. | [`SPEC.md`](SPEC.md) |
| 2 | **The program.** Track 1 is the server, track 2 is the client. | [`bserve`](bserve), [`bcurl`](bcurl), [`bproto.py`](bproto.py) |
| 3 | **An annotated hexdump** of one complete request and response, captured from a real run. | [`HEXDUMP.md`](HEXDUMP.md), raw output in [`hexdump-capture.txt`](hexdump-capture.txt) |
| + | **An independent client in C**, written only from `SPEC.md`, to show this is a protocol and not just one implementation. | [`interop/bget.c`](interop/bget.c) |
| + | **Tests:** 27 end-to-end tests. | [`test_bhp.py`](test_bhp.py) |

Everything is Python 3 standard library, plus one C file. No frameworks.

## The protocol in one screen

```
preface (client, once):   42 48 50 01                       "BHP" v1

frame header, 8 octets:   | Length:24 | Type:8 | Flags:8 | Stream:24 |
types:                    00 DATA   01 HEADERS   02 GOAWAY   other -> MUST skip
flags:                    01 END_STREAM

header field:             idx:8 [name_len:8 name] value_len:16 value
static table (idx 1..10): :method :path :status host user-agent accept
                          content-type content-length server last-modified
```

## Track 1: the server

```
$ ./bserve ./www 9000                  # add -v to log every frame, --grease to
                                       # send an unknown frame before each response
```

- It accepts a TCP connection, checks the preface, and reads one request
  frame at a time.
- It maps `:path` to a file under the root. A directory maps to its
  `index.html`, and any path that escapes the root with `..` (including
  `%2e%2e`) gets `404`.
- The reply is HEADERS (`:status`, `content-type`, `content-length`, `server`,
  `last-modified`) followed by DATA frames of at most 16 KiB each.
- Status codes:
  - `404` when the file is not there.
  - `405` for any method other than GET or HEAD.
  - `400` when the frame is malformed.
- The connection stays open, including after a `400`: the frame length still
  says where the next frame starts.
- It closes only on a bad preface, when the peer closes mid-frame, or after
  30 s idle (it sends GOAWAY first).

## Track 2: the client

```
$ ./bcurl -v localhost:9000/index.html
$ ./bcurl localhost:9000/a localhost:9000/b     # both on ONE connection
```

- It builds the binary request frame, reads the response, and writes the body
  to stdout.
- `-v` hexdumps every frame to stderr. `-vv` also annotates every field.
- It exits with `4` on a 4xx and `5` on a 5xx, and with `3` on a protocol or
  connection error.
- It **never opens a second connection**. Several URLs share one socket (add
  `--pipeline` to send every request before reading any response). It refuses
  URLs on different hosts rather than opening a second connection.
- It skips unknown frame types and unknown header indices.

## Pairing: the "only the spec crosses" test

The assignment wants a server and client that share nothing but the spec.
There was no partner for this submission, so `interop/bget.c` stands in: a
second client written in a different language from `SPEC.md` alone, sharing
no code with `bproto.py`. It talks to `bserve` and skips the server's grease
frames. The server tests also build their request bytes **by hand** from the
spec with `struct.pack`, not with the project's own encoder. The client tests
run `bcurl` against fake servers that send only what the spec allows.

## Run it

```
./bserve ./www 9000 &
./bcurl -v localhost:9000/index.html
cc -O2 -o interop/bget interop/bget.c && ./interop/bget localhost 9000 /hi.txt /index.html
python3 -m unittest -v test_bhp.py
```

## Captured output

All of this is from real runs on macOS with Python 3.14 and Apple clang.

`./bcurl -v localhost:9000/index.html`: 1 preface, 1 request frame, and 2
response frames:

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
    ... (170 body octets) ...
* stream 1: GET /index.html -> 200
<!doctype html>
...
> GOAWAY stream=0 len=4 flags=-
>   00000000  00 00 04 02 00 00 00 00  64 6f 6e 65              |........done|
```

Exit codes, a byte-exact 48 KB transfer, pipelining, path traversal, HEAD and
405:

```
$ ./bcurl localhost:9000/nope.html; echo "exit=$?"
not found: /nope.html
bcurl: /nope.html -> 404
exit=4

$ ./bcurl localhost:9000/docs/big.txt | shasum;  shasum < www/docs/big.txt
16e8ce8c7fc5967ba56d62da0572e82c4591665d  -
16e8ce8c7fc5967ba56d62da0572e82c4591665d  -

$ ./bcurl localhost:9000/docs/hello.txt localhost:9000/ localhost:9000/../../etc/passwd --pipeline --grease; echo "exit=$?"
hi
<!doctype html>
...
not found: /../../etc/passwd
bcurl: /../../etc/passwd -> 404
exit=4

$ ./bcurl -I localhost:9000/docs/big.txt
:status: 200
content-type: text/plain; charset=utf-8
content-length: 48300
server: bserve/1 (24bcs10209)
last-modified: Fri, 25 Sep 2026 13:35:14 GMT

$ ./bcurl -X POST localhost:9000/index.html; echo "exit=$?"
method POST not allowed
bcurl: /index.html -> 405
exit=4
```

Server log for those runs. The pipelined run is a single connection
(`52483`) carrying streams 1, 2 and 3:

```
bserve (24bcs10209): serving .../02-binary-http/www on :9000 (BHP/1, idle 30s)
[127.0.0.1:52477] accepted
[127.0.0.1:52477] stream 1 GET /index.html -> 200 (170 bytes)
[127.0.0.1:52477] client GOAWAY after 1 requests
[127.0.0.1:52479] accepted
[127.0.0.1:52479] stream 1 GET /nope.html -> 404 (22 bytes)
[127.0.0.1:52479] client GOAWAY after 1 requests
[127.0.0.1:52481] accepted
[127.0.0.1:52481] stream 1 GET /docs/big.txt -> 200 (48300 bytes)
[127.0.0.1:52481] client GOAWAY after 1 requests
[127.0.0.1:52483] accepted
[127.0.0.1:52483] stream 1 GET /docs/hello.txt -> 200 (3 bytes)
[127.0.0.1:52483] stream 2 GET / -> 200 (170 bytes)
[127.0.0.1:52483] stream 3 GET /../../etc/passwd -> 404 (29 bytes)
[127.0.0.1:52483] client GOAWAY after 3 requests
[127.0.0.1:52485] accepted
[127.0.0.1:52485] stream 1 HEAD /docs/big.txt -> 200 (48300 bytes)
[127.0.0.1:52485] client GOAWAY after 1 requests
[127.0.0.1:52487] accepted
[127.0.0.1:52487] stream 1 -> 405 method POST not allowed
[127.0.0.1:52487] client GOAWAY after 1 requests
```

The independent C client, first against a normal server and then against
`bserve --grease`:

```
$ ./interop/bget localhost 9000 /hi.txt /index.html /missing; echo "exit=$?"
bget: stream 1 /hi.txt -> 200
bget: stream 2 /index.html -> 200
bget: stream 3 /missing -> 404
hi
<!doctype html>
...
not found: /missing
exit=4

$ ./interop/bget localhost 9000 /hi.txt      # server started with --grease
bget: skipped unknown frame type 0xfa (21 octets)
bget: stream 1 /hi.txt -> 200
hi
```

`python3 -m unittest -v test_bhp.py`:

```
test_body_to_stdout_exit_zero_and_preface ... ok
test_connection_refused_exits_3 ... ok
test_exit_non_zero_on_4xx_and_5xx ... ok
test_never_opens_a_second_connection ... ok
test_refuses_urls_that_would_need_two_connections ... ok
test_request_bytes_follow_the_spec ... ok
test_short_body_is_a_protocol_error ... ok
test_skips_unknown_frames_and_header_indices ... ok
test_verbose_hexdumps_every_frame ... ok
test_bcurl_404_405 ... ok
test_bcurl_fetches_files_byte_exact ... ok
test_bcurl_head ... ok
test_independent_c_client_interoperates ... ok
test_bad_preface_gets_400_goaway_and_close ... ok
test_body_split_into_16k_data_frames ... ok
test_frame_split_across_many_tcp_writes ... ok
test_headers_on_stream_zero_is_400 ... ok
test_idle_timeout_sends_goaway ... ok
test_keeps_connection_open_across_statuses ... ok
test_malformed_header_block_is_400_and_connection_survives ... ok
test_path_traversal_is_404 ... ok
test_pipelined_requests_answered_in_order ... ok
test_request_body_is_consumed_not_parsed ... ok
test_spec_worked_example ... ok
test_unknown_flag_bits_are_ignored ... ok
test_unknown_frame_types_are_skipped ... ok
test_unknown_header_index_and_literal_names_are_accepted ... ok
----------------------------------------------------------------------
Ran 27 tests in 3.4s

OK
```

To check the tests can actually fail, I made two deliberate breaks:
1. `bserve` treats an unknown frame type as an error instead of skipping it.
2. `bcurl` always exits with `0`.

The first failed `test_unknown_frame_types_are_skipped`. The second failed
`test_exit_non_zero_on_4xx_and_5xx` and `test_bcurl_404_405`. Restoring the
code made all 27 pass again.
