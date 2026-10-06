#!/usr/bin/env node
/*
 * bserve.js -- a second, independent BHP/1 server, written only from SPEC.md.
 *
 * Author : Hemang (Roll No. 24bcs10209)
 *
 * Different language (JavaScript / Node.js), different author's-eye view, no
 * shared code with bserve/bproto.py. If ./bcurl works against this, the
 * client speaks the protocol and not just "whatever my own server does".
 *
 *   node interop/bserve.js ./www 9001 [IDLE_SECONDS]
 */
'use strict';
const net = require('net');
const fs = require('fs');
const path = require('path');

// SPEC section 5: the static table
const TABLE = [null, ':method', ':path', ':status', 'host', 'user-agent',
  'accept', 'content-type', 'content-length', 'server', 'last-modified'];
const INDEX = Object.fromEntries(TABLE.map((n, i) => [n, i]).filter(([n]) => n));
const PREFACE = Buffer.from([0x42, 0x48, 0x50, 0x01]);
const DATA = 0x00, HEADERS = 0x01, GOAWAY = 0x02, END_STREAM = 0x01;
const MAX_DATA = 16384;
const TYPES = { '.html': 'text/html; charset=utf-8', '.txt': 'text/plain; charset=utf-8',
  '.css': 'text/css', '.js': 'text/javascript', '.json': 'application/json',
  '.png': 'image/png', '.jpg': 'image/jpeg' };

// SPEC section 2: Length:24 Type:8 Flags:8 Stream:24, big-endian
function frame(type, flags, stream, payload = Buffer.alloc(0)) {
  const h = Buffer.alloc(8);
  h.writeUIntBE(payload.length, 0, 3);
  h[3] = type;
  h[4] = flags;
  h.writeUIntBE(stream, 5, 3);
  return Buffer.concat([h, payload]);
}

function encodeFields(fields) {
  const parts = [];
  for (const [name, value] of fields) {
    const v = Buffer.from(String(value), 'utf8');
    const idx = INDEX[name];
    if (idx) {
      parts.push(Buffer.from([idx]));
    } else {
      const n = Buffer.from(name, 'ascii');
      parts.push(Buffer.from([0, n.length]), n);
    }
    const len = Buffer.alloc(2);
    len.writeUInt16BE(v.length);
    parts.push(len, v);
  }
  return Buffer.concat(parts);
}

// returns array of [name, value] or throws on malformed block
function decodeFields(b) {
  const out = [];
  let i = 0;
  while (i < b.length) {
    const idx = b[i++];
    let name = null;
    if (idx === 0) {
      if (i >= b.length) throw new Error('truncated name length');
      const nl = b[i++];
      if (nl === 0 || i + nl > b.length) throw new Error('bad name length');
      name = b.subarray(i, i + nl).toString('ascii').toLowerCase();
      i += nl;
    } else if (idx < TABLE.length) {
      name = TABLE[idx];
    }
    if (i + 2 > b.length) throw new Error('truncated value length');
    const vl = b.readUInt16BE(i);
    i += 2;
    if (i + vl > b.length) throw new Error('value past end of frame');
    const value = b.subarray(i, i + vl);
    i += vl;
    if (name === null) continue;                 // unknown index: skip field
    const text = new TextDecoder('utf-8', { fatal: true });
    try { out.push([name, text.decode(value)]); } catch { throw new Error('value not UTF-8'); }
  }
  return out;
}

function serve(root, sock) {
  let buf = Buffer.alloc(0);
  let gotPreface = false;
  let skipping = 0;           // payload bytes of an unknown frame still to discard
  let pending = null;         // {stream, payload}: HEADERS waiting for its body to end
  const peer = `${sock.remoteAddress}:${sock.remotePort}`;

  const respond = (stream, status, body, ctype, headOnly) => {
    const head = encodeFields([[':status', status], ['content-type', ctype],
      ['content-length', body.length], ['server', 'bserve.js/1 (24bcs10209)']]);
    if (headOnly || body.length === 0) {
      sock.write(frame(HEADERS, END_STREAM, stream, head));
      return;
    }
    sock.write(frame(HEADERS, 0, stream, head));
    for (let off = 0; off < body.length; off += MAX_DATA) {
      const last = off + MAX_DATA >= body.length;
      sock.write(frame(DATA, last ? END_STREAM : 0, stream, body.subarray(off, off + MAX_DATA)));
    }
  };
  // SPEC 4: a response to HEAD has no DATA (content-length still given)
  const error = (stream, status, msg, headOnly = false) =>
    respond(stream, status, Buffer.from(msg + '\n'), 'text/plain; charset=utf-8', headOnly);

  let lastStream = 0;
  // Decode a HEADERS payload just enough to know: fields (or an error), whether
  // it is HEAD (so error replies carry no DATA), and its content-length.
  const inspect = (payload) => {
    if (payload === null) return { problem: 'header block too large', head: false, declared: null };
    let fields;
    try { fields = decodeFields(payload); } catch (e) { return { problem: 'malformed header block: ' + e.message, head: false, declared: null }; }
    const pick = (n) => fields.filter(([k]) => k === n).map(([, v]) => v);
    const cl = pick('content-length');
    const declared = cl.length === 1 && /^[0-9]+$/.test(cl[0]) ? Number(cl[0]) : null;
    return { fields, methods: pick(':method'), paths: pick(':path'), cl, declared,
      head: pick(':method').length === 1 && pick(':method')[0] === 'HEAD' };
  };
  // SPEC 4: an ID is used up even when its request is bad or aborted
  const claim = (stream) => {
    const reused = stream !== 0 && stream <= lastStream;
    if (stream > lastStream) lastStream = stream;
    return reused;
  };

  const handle = (stream, info, reused) => {
    const head = info.head;
    // the 400 row of SPEC 4, in order
    if (info.problem) return error(stream, 400, info.problem, head);
    if (stream === 0) return error(0, 400, 'HEADERS must not use stream 0', head);
    if (reused) return error(stream, 400, 'stream id not increasing', head);
    if (info.methods.length !== 1 || info.paths.length !== 1) return error(stream, 400, 'need one :method and one :path', head);
    if (info.cl.length > 1 || (info.cl.length === 1 && info.declared === null)) return error(stream, 400, 'bad content-length', head);
    const [method, p] = [info.methods[0], info.paths[0]];
    if (!p.startsWith('/') || p.includes('#')) return error(stream, 400, ':path must start with / and have no #', head);
    let rel;
    try { rel = decodeURIComponent(p.split('?')[0]); } catch { return error(stream, 400, 'bad percent-encoding', head); }
    // then 405, then 404 / 403 / 500
    if (method !== 'GET' && !head) return error(stream, 405, 'method not allowed');
    if (rel.includes('\0')) return error(stream, 404, 'not found', head);
    let full;
    try {
      full = fs.realpathSync(path.join(root, rel));
      const rp = path.relative(root, full);               // escape check that also works for root "/"
      if (rp.startsWith('..') || path.isAbsolute(rp)) return error(stream, 404, 'not found', head);
      if (fs.statSync(full).isDirectory()) full = path.join(full, 'index.html');
      if (!fs.statSync(full).isFile()) return error(stream, 404, 'not found', head);
    } catch (e) {
      return error(stream, e.code === 'EACCES' ? 403 : ['ENOENT', 'ENOTDIR'].includes(e.code) ? 404 : 500,
        'cannot open', head);
    }
    let body;
    try { body = fs.readFileSync(full); } catch (e) {
      return error(stream, e.code === 'EACCES' ? 403 : 500, 'cannot read file', head);
    }
    respond(stream, 200, body, TYPES[path.extname(full)] || 'application/octet-stream', head);
    console.error(`[${peer}] stream ${stream} ${method} ${p} -> 200 (${body.length} bytes)`);
  };

  sock.on('data', (chunk) => {
    buf = Buffer.concat([buf, chunk]);
    for (;;) {
      if (skipping) {                                   // discard unknown payload
        const n = Math.min(skipping, buf.length);
        buf = buf.subarray(n);
        skipping -= n;
        if (skipping) return;
      }
      if (!gotPreface) {
        if (buf.length < 4) return;
        if (!buf.subarray(0, 4).equals(PREFACE)) {
          if (buf.subarray(0, 3).equals(PREFACE.subarray(0, 3))) error(0, 505, 'version not supported');
          else error(0, 400, 'bad preface');
          sock.end(frame(GOAWAY, 0, 0, Buffer.from('bad preface')));
          return;
        }
        buf = buf.subarray(4);
        gotPreface = true;
      }
      if (buf.length < 8) return;
      const len = buf.readUIntBE(0, 3), type = buf[3], flags = buf[4], stream = buf.readUIntBE(5, 3);
      if (type > GOAWAY) {                              // SPEC: MUST skip unknown types
        buf = buf.subarray(8);
        skipping = len;
        continue;
      }
      if (buf.length < 8 + len) return;
      const payload = buf.subarray(8, 8 + len);
      buf = buf.subarray(8 + len);
      if (type === GOAWAY) { sock.end(); return; }
      if (type === DATA) {                              // body we do not need
        if (pending && stream !== pending.stream) {     // SPEC 4: DATA for another stream
          error(pending.stream, 400, 'DATA for another stream', pending.info.head);
          pending = null;
        } else if (pending) {
          pending.got += len;
          if (flags & END_STREAM) {
            const q = pending;
            pending = null;
            if (q.info.declared !== null && q.got !== q.info.declared) error(q.stream, 400, 'body does not match content-length', q.info.head);
            else handle(q.stream, q.info, q.reused);    // answer once the body ended
          }
        }
        continue;                                       // stray DATA: discarded
      }
      if (pending) {                                    // SPEC 4: unfinished request
        error(pending.stream, 400, 'request ended without END_STREAM', pending.info.head);
        pending = null;
      }
      const info = inspect(len > 65536 ? null : Buffer.from(payload));
      const reused = claim(stream);
      if (!(flags & END_STREAM)) { pending = { stream, info, reused, got: 0 }; continue; }
      if (info.declared) { error(stream, 400, 'content-length but no DATA', info.head); continue; }
      handle(stream, info, reused);
    }
  });
  sock.on('error', () => {});
  sock.setTimeout(IDLE_MS, () => sock.end(frame(GOAWAY, 0, 0, Buffer.from('idle timeout'))));
}

const [rootArg, portArg, idleArg] = process.argv.slice(2);
const IDLE_MS = 1000 * Number(idleArg || 30);
if (!rootArg || !portArg) {
  console.error('usage: node bserve.js ROOT PORT [IDLE_SECONDS]');
  process.exit(2);
}
const root = fs.realpathSync(rootArg);
net.createServer((s) => serve(root, s)).listen(Number(portArg), '0.0.0.0', () =>
  console.error(`bserve.js (24bcs10209): serving ${root} on :${portArg} (BHP/1)`));
