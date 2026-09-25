# Assignment: a calculator that stays on the line (HTTP/1.1)

*Hemang, Roll No. 24bcs10209*

An HTTP/1.1 calculator written against a raw `socket`, with no framework and
no `http.server`. The arithmetic is trivial. The point is **keep-alive**: one
TCP connection carries every request, so the server has to work out where each
request ends by itself.

| File | What it is |
|---|---|
| [`calc_server.py`](calc_server.py) | The server (Python 3, standard library only). |
| [`mark.py`](mark.py) | Reproduces the marking script from the assignment: one socket, every request. |
| [`test_calc.py`](test_calc.py) | 13 end-to-end tests over real sockets. The suite runs twice (normal and 1 s idle timeout), for 25 results. |

## Feature set

| Request | Response |
|---|---|
| `GET /add?a=2&b=3` | `200 5` |
| `GET /sub?a=10&b=4` | `200 6` |
| `GET /mul?a=6&b=7` | `200 42` |
| `GET /div?a=9&b=3` | `200 3` |
| `GET /div?a=1&b=0` | `400` (division by zero) |
| `GET /add?a=x&b=3` | `400` (not a number) |
| `GET /pow?a=2&b=8` | `404` (unknown operation) |
| `POST /add` | `405`, with `Allow: GET, HEAD` |
| `GET /add` with no `Host` | `400` (HTTP/1.1 requires `Host`) |

It also handles decimals and negatives (`7/2 = 3.5`, `3-10 = -7`, `0.1*3 = 0.3`
exactly, because it uses `Decimal`). NaN, Infinity and values beyond 1e100 get
`400`.

## Where one request ends and the next begins

Each connection keeps a byte buffer, and the parser only ever removes whole
requests from its front:

1. **Head:** read until `\r\n\r\n`. Everything after that marker stays in the
   buffer.
2. **Body:**
   - With `Content-Length: n`, `read_exact(n)` takes exactly *n* bytes, never
     *n+1*. Byte *n+1* is the start of the next request.
   - With `Transfer-Encoding: chunked`, it decodes chunks down to the
     zero-length chunk and its trailers.
   - With neither, the body is empty.
3. Whatever is left in the buffer is the start of the next request. That is
   what makes pipelining work without extra code.

A request that breaks framing (a bad `Content-Length`, both `Content-Length`
and `Transfer-Encoding`, duplicate `Host`, a malformed request line) gets
`400` and `Connection: close`. After that the server cannot know where the
next request starts. The smuggling cases are rejected rather than guessed at.
A request that is well framed but wrong (division by zero, a bad number,
missing `Host`) gets `400` and the connection **stays open**.

## Stretch goals, all implemented

| Goal | How |
|---|---|
| Honour `Connection: close` | The server sends the response with `Connection: close`, then closes. HTTP/1.0 closes by default unless the client sends `Connection: keep-alive`. |
| An idle timeout you can defend | 15 s (set with `CALC_IDLE_TIMEOUT`). That is long enough for someone typing into `nc`, or a harness pausing between requests. It is short enough that abandoned clients cannot pin threads (one thread per connection). It is also well under nginx's 75 s, which is right for a toy server. The server advertises it in `Keep-Alive: timeout=15`. |
| Chunked encoding | Request bodies are decoded, including chunk extensions and trailers, and consumed exactly. |
| Pipelining | All six requests sent in one `write` are answered in order. |

## Run it

```
python3 calc_server.py 8080          # terminal 1
python3 mark.py                      # terminal 2
python3 mark.py --pipeline
python3 -m unittest -v test_calc.py
```

## Captured output

The outputs below are from a real run on macOS with Python 3.14.

`python3 mark.py`:

```
s = socket.create_connection(('localhost', 8080))   # local port 52288
mode: sequential (request, wait, request, ...)

  GET  /add?a=2&b=3     -> 200  5    PASS
  GET  /sub?a=10&b=4    -> 200  6    PASS
  GET  /mul?a=6&b=7     -> 200  42   PASS
  GET  /div?a=1&b=0     -> 400       PASS
  GET  /pow?a=2&b=8     -> 404       PASS
  POST /add             -> 405       PASS

socket still open: True
1 TCP handshake, 6 responses
score: 7/7
```

`python3 mark.py --pipeline` (all six requests in a single `sendall`) prints
the same six PASS lines, then `socket still open: True` and
`1 TCP handshake, 6 responses`.

Server log for those two runs. It shows one `accepted` per run, which means
one TCP handshake, followed by six requests:

```
calc server (24bcs10209) listening on :8080, idle timeout 15s
[127.0.0.1:52288] accepted (TCP handshake)
[127.0.0.1:52288] #1 GET /add?a=2&b=3 -> 200 5
[127.0.0.1:52288] #2 GET /sub?a=10&b=4 -> 200 6
[127.0.0.1:52288] #3 GET /mul?a=6&b=7 -> 200 42
[127.0.0.1:52288] #4 GET /div?a=1&b=0 -> 400 division by zero
[127.0.0.1:52288] #5 GET /pow?a=2&b=8 -> 404 no such operation: /pow
[127.0.0.1:52288] #6 POST /add -> 405 POST not allowed, use GET
[127.0.0.1:52288] client closed after 6 requests
[127.0.0.1:52290] accepted (TCP handshake)
...same six lines...
[127.0.0.1:52290] client closed after 6 requests
```

`curl` confirms it reuses the connection:

```
$ curl -sv 'http://localhost:8080/add?a=2&b=3' 'http://localhost:8080/sub?a=10&b=4' 'http://localhost:8080/div?a=1&b=0'
* Connected to localhost (127.0.0.1) port 8080
< HTTP/1.1 200 OK
5
* Re-using existing connection with host localhost
< HTTP/1.1 200 OK
6
* Re-using existing connection with host localhost
< HTTP/1.1 400 Bad Request
division by zero
```

Three pipelined requests through `nc`. The request with no `Host` gets `400`
and the connection survives. The last request asks for `Connection: close`:

```
$ printf 'GET /mul?a=6&b=7 HTTP/1.1\r\nHost: localhost\r\n\r\nGET /add HTTP/1.1\r\n\r\nGET /div?a=9&b=3 HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n' | nc localhost 8080
HTTP/1.1 200 OK
Content-Type: text/plain; charset=utf-8
Content-Length: 3
Connection: keep-alive
Keep-Alive: timeout=15

42
HTTP/1.1 400 Bad Request
Content-Type: text/plain; charset=utf-8
Content-Length: 20
Connection: keep-alive
Keep-Alive: timeout=15

missing Host header
HTTP/1.1 200 OK
Content-Type: text/plain; charset=utf-8
Content-Length: 2
Connection: close

3
```

`python3 -m unittest -v test_calc.py`. The 12 base tests run against a server with the normal 15 s idle timeout, then again against one with a 1 s timeout, plus the idle-close test:

```
test_405_advertises_allow [CalcServerTest] ... ok
test_body_bytes_are_not_mistaken_for_a_request [CalcServerTest] ... ok
test_chunked_request_body_is_consumed_exactly [CalcServerTest] ... ok
test_connection_close_is_honoured [CalcServerTest] ... ok
test_decimals_and_negatives [CalcServerTest] ... ok
test_feature_table_on_one_socket [CalcServerTest] ... ok
test_http10_closes_by_default [CalcServerTest] ... ok
test_malformed_framing_gets_400_and_close [CalcServerTest] ... ok
test_many_requests_one_connection [CalcServerTest] ... ok
test_pipelining_six_at_once_answered_in_order [CalcServerTest] ... ok
test_request_dribbled_one_byte_at_a_time [CalcServerTest] ... ok
test_smuggling_shape_rejected [CalcServerTest] ... ok
test_405_advertises_allow [IdleTimeoutTest] ... ok
test_body_bytes_are_not_mistaken_for_a_request [IdleTimeoutTest] ... ok
test_chunked_request_body_is_consumed_exactly [IdleTimeoutTest] ... ok
test_connection_close_is_honoured [IdleTimeoutTest] ... ok
test_decimals_and_negatives [IdleTimeoutTest] ... ok
test_feature_table_on_one_socket [IdleTimeoutTest] ... ok
test_http10_closes_by_default [IdleTimeoutTest] ... ok
test_idle_connection_is_closed [IdleTimeoutTest] ... ok
test_malformed_framing_gets_400_and_close [IdleTimeoutTest] ... ok
test_many_requests_one_connection [IdleTimeoutTest] ... ok
test_pipelining_six_at_once_answered_in_order [IdleTimeoutTest] ... ok
test_request_dribbled_one_byte_at_a_time [IdleTimeoutTest] ... ok
test_smuggling_shape_rejected [IdleTimeoutTest] ... ok

----------------------------------------------------------------------
Ran 25 tests in 2.6s

OK
```

`test_body_bytes_are_not_mistaken_for_a_request` is the key test. It sends a
`POST` whose body is itself a complete `GET /mul...` request. A server that
reads past `Content-Length` would answer that body as a request, and return
two responses where there should be one.
