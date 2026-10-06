"""
BHP/1 -- HTTP semantics in binary frames. Shared code for bserve and bcurl.

Author : Hemang (Roll No. 24bcs10209)
Spec   : SPEC.md in this directory. This module is one implementation of it;
         the spec, not this file, is the protocol.

Wire summary
    connection preface (client -> server, once):  42 48 50 01   "BHP" v1
    frame header (8 octets, big-endian):
        Length:24  Type:8  Flags:8  StreamID:24
    frame types: 0x00 DATA, 0x01 HEADERS, 0x02 GOAWAY, anything else -> skip
    header field: idx:8 [name_len:8 name] value_len:16 value
"""

import struct

PREFACE = b"BHP\x01"

HEADER_LEN = 8
MAX_LENGTH = (1 << 24) - 1          # what 24 bits can express
MAX_STREAM = (1 << 24) - 1
DATA_CHUNK = 16384                  # senders split bodies into <= 16 KiB DATA
MAX_HEADER_BLOCK = 65536            # SPEC: receivers MAY refuse bigger HEADERS

# frame types
DATA = 0x00
HEADERS = 0x01
GOAWAY = 0x02
TYPE_NAMES = {DATA: "DATA", HEADERS: "HEADERS", GOAWAY: "GOAWAY"}

# flags
END_STREAM = 0x01

# The ten header names v1 actually sends. Index 0 means "literal name follows".
STATIC_TABLE = [
    None,               # 0x00 literal
    ":method",          # 0x01
    ":path",            # 0x02
    ":status",          # 0x03
    "host",             # 0x04
    "user-agent",       # 0x05
    "accept",           # 0x06
    "content-type",     # 0x07
    "content-length",   # 0x08
    "server",           # 0x09
    "last-modified",    # 0x0a
]
STATIC_INDEX = {name: i for i, name in enumerate(STATIC_TABLE) if name}


class ProtocolError(Exception):
    """The peer sent bytes that do not follow the spec."""


# ---------------------------------------------------------------- frames ---

def pack_frame_header(length, ftype, flags, stream):
    if not 0 <= length <= MAX_LENGTH:
        raise ValueError("frame payload too long for 24 bits")
    if not 0 <= stream <= MAX_STREAM:
        raise ValueError("stream id does not fit in 24 bits")
    return struct.pack(">II", (length << 8) | ftype, (flags << 24) | stream)


def unpack_frame_header(raw):
    word1, word2 = struct.unpack(">II", raw)
    return word1 >> 8, word1 & 0xFF, word2 >> 24, word2 & 0xFFFFFF


def frame(ftype, flags, stream, payload=b""):
    return pack_frame_header(len(payload), ftype, flags, stream) + payload


# --------------------------------------------------------- header blocks ---

def encode_headers(fields):
    """[(name, value), ...] -> header block bytes. Order is preserved."""
    out = bytearray()
    for name, value in fields:
        name = name.lower()
        if isinstance(value, str):
            value = value.encode("utf-8")
        if len(value) > 0xFFFF:
            raise ValueError("header value longer than 65535 octets")
        idx = STATIC_INDEX.get(name)
        if idx is not None:
            out.append(idx)
        else:
            raw = name.encode("ascii")
            if not 1 <= len(raw) <= 255:
                raise ValueError("header name must be 1..255 octets")
            out.append(0)
            out.append(len(raw))
            out += raw
        out += struct.pack(">H", len(value))
        out += value
    return bytes(out)


def decode_headers(block):
    """Header block bytes -> [(name, value_str), ...]. Raises ProtocolError.

    Fields with an index this version does not know are skipped: their value
    is still length-prefixed, so a v1 receiver can step over a v2 field.
    """
    fields = []
    i, n = 0, len(block)
    while i < n:
        idx = block[i]
        i += 1
        name = None
        if idx == 0:
            if i >= n:
                raise ProtocolError("truncated literal name length")
            name_len = block[i]
            i += 1
            if name_len == 0 or i + name_len > n:
                raise ProtocolError("bad literal name length")
            try:
                name = block[i:i + name_len].decode("ascii").lower()
            except UnicodeDecodeError:
                raise ProtocolError("header name is not ASCII")
            i += name_len
        elif idx < len(STATIC_TABLE):
            name = STATIC_TABLE[idx]
        if i + 2 > n:
            raise ProtocolError("truncated value length")
        (value_len,) = struct.unpack_from(">H", block, i)
        i += 2
        if i + value_len > n:
            raise ProtocolError("value runs past end of frame")
        value = block[i:i + value_len]
        i += value_len
        if name is None:
            continue                     # unknown index: skip cleanly
        try:
            fields.append((name, value.decode("utf-8")))
        except UnicodeDecodeError:
            raise ProtocolError("header value is not UTF-8")
    return fields


# ------------------------------------------------------------ transport ---

class FrameReader:
    """Pulls whole frames off a socket; can skip payloads without buffering."""

    def __init__(self, sock):
        self.sock = sock
        self.buf = bytearray()        # amortised O(1) appends, unlike bytes +=

    def read_exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self.buf)))
            if not chunk:
                if not self.buf:
                    raise EOFError("peer closed the connection")
                raise ProtocolError("peer closed mid-frame")
            self.buf += chunk
        data = bytes(self.buf[:n])
        del self.buf[:n]
        return data

    def skip(self, n, sink=None):
        """Discard n payload bytes in pieces -- a 16 MiB unknown frame costs
        16 MiB of reading but only 64 KiB of memory. `sink(offset, piece)` is
        called for each piece (used by bcurl -v to hexdump what it skips)."""
        off = 0
        while n:
            take = min(n, 65536)
            piece = self.read_exact(take)
            if sink:
                sink(off, piece)
            off += take
            n -= take

    def read_header(self):
        """-> (length, type, flags, stream, raw_header_bytes)"""
        raw = self.read_exact(HEADER_LEN)
        return unpack_frame_header(raw) + (raw,)


# ------------------------------------------------------------- display ---

def hexdump(data, prefix=""):
    lines = []
    for off in range(0, len(data), 16):
        row = data[off:off + 16]
        hexpart = " ".join("%02x" % b for b in row[:8])
        if len(row) > 8:
            hexpart += "  " + " ".join("%02x" % b for b in row[8:])
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        lines.append("%s%08x  %-49s |%s|" % (prefix, off, hexpart, text))
    return "\n".join(lines)


def describe(ftype, flags, stream, length):
    name = TYPE_NAMES.get(ftype, "UNKNOWN(0x%02x)" % ftype)
    fl = []
    if flags & END_STREAM and ftype in (DATA, HEADERS):
        fl.append("END_STREAM")
    if flags & ~END_STREAM or (flags and ftype not in (DATA, HEADERS)):
        fl.append("0x%02x" % flags)
    return "%s stream=%d len=%d flags=%s" % (name, stream, length,
                                             "|".join(fl) or "-")


def annotate_preface(raw=PREFACE):
    return [(0, "42 48 50", 'magic "BHP" (identifies the protocol)'),
            (3, "%02x" % raw[3], "version = %d" % raw[3])]


def annotate(raw):
    """Explain one complete frame, octet group by octet group.

    Returns a list of (offset, hex, meaning) rows; used by `bcurl -vv` and to
    produce HEXDUMP.md.
    """
    rows = []

    def row(off, octets, meaning):
        rows.append((off, " ".join("%02x" % b for b in octets), meaning))

    length, ftype, flags, stream = unpack_frame_header(raw[:HEADER_LEN])
    row(0, raw[0:3], "Length = %d (payload octets after this 8-octet header)"
        % length)
    row(3, raw[3:4], "Type = 0x%02x %s" % (ftype, TYPE_NAMES.get(
        ftype, "UNKNOWN -> receiver skips %d octets" % length)))
    row(4, raw[4:5], "Flags = 0x%02x%s" % (flags, " END_STREAM"
                                          if flags & END_STREAM else ""))
    row(5, raw[5:8], "Stream ID = %d" % stream)
    payload = raw[HEADER_LEN:HEADER_LEN + length]
    base = HEADER_LEN

    if ftype == HEADERS:
        i = 0
        while i < len(payload):
            start = i
            idx = payload[i]
            i += 1
            if idx == 0:
                nlen = payload[i]
                name = payload[i + 1:i + 1 + nlen].decode("ascii", "replace")
                row(base + start, payload[start:start + 2],
                    "field: idx 0x00 = literal name, name length %d" % nlen)
                row(base + start + 2, payload[start + 2:start + 2 + nlen],
                    'name "%s"' % name)
                i += 1 + nlen
            else:
                known = STATIC_TABLE[idx] if idx < len(STATIC_TABLE) else None
                row(base + start, payload[start:start + 1],
                    "field: idx 0x%02x = %s" % (idx, known or
                                               "unknown index -> skip field"))
            (vlen,) = struct.unpack_from(">H", payload, i)
            row(base + i, payload[i:i + 2], "value length %d" % vlen)
            val = payload[i + 2:i + 2 + vlen]
            if vlen:
                row(base + i + 2, val, 'value "%s"'
                    % val.decode("utf-8", "replace"))
            i += 2 + vlen
    elif payload:
        label = {DATA: "body", GOAWAY: "reason"}.get(ftype, "opaque payload")
        for off in range(0, len(payload), 16):
            piece = payload[off:off + 16]
            text = piece.decode("utf-8", "replace").replace("\n", "\\n")
            row(base + off, piece, '%s "%s"' % (label, text))
    return rows


def format_annotation(rows, prefix=""):
    out = []
    for off, hx, meaning in rows:
        # wrap long hex runs to 16 octets per line
        octets = hx.split(" ")
        for k in range(0, len(octets), 16):
            chunk = " ".join(octets[k:k + 16])
            label = meaning if k == 0 else "  (cont.)"
            out.append("%s%04x  %-47s  %s" % (prefix, off + k, chunk, label))
    return "\n".join(out)
