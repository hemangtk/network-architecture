# Network Architecture

*Hemang, Roll No. 24bcs10209*

| Folder | Work |
|---|---|
| [`01-calculator-keepalive/`](01-calculator-keepalive/) | **Assignment (early HTTP/1.1):** a calculator that stays on the line. It is an HTTP/1.1 keep-alive server on a raw socket, with exact `Content-Length` and chunked framing, `Connection: close`, an idle timeout, and pipelining. |
| [`02-binary-http/`](02-binary-http/) | **Course project (HTTP in binary):** the BHP/1 spec (two pages, plus a PDF), design defence, the server `bserve`, the client `bcurl`, an annotated hexdump of a wire capture, and interop with an independent C client and Node server. Run `./demo.sh` there for a brief-by-brief PASS/FAIL check. |

Both parts use only the Python 3 standard library, with no frameworks. The interop programs in part 2 are in C and Node.js.

Each folder's README has the commands to run, the captured output, and test
results. To run every test:

```
cd 01-calculator-keepalive && python3 -m unittest -v test_calc.py
cd ../02-binary-http      && python3 -m unittest -v test_bhp.py
```
