#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply build/jangou-en.{bps,ips} to the clean ROM and confirm both
reproduce build/jangou-en.nes exactly. Run after build.py."""
import os, sys, zlib, hashlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src  = open(os.path.join(ROOT, "Jangou (Japan).nes"), "rb").read()
want = open(os.path.join(ROOT, "build", "jangou-en.nes"), "rb").read()

def rd_varint(b, i):
    n, sh = 0, 0
    while True:
        x = b[i]; i += 1
        n += (x & 0x7F) << sh
        if x & 0x80: break
        sh += 7; n += 1 << sh
    return n, i

def apply_bps(src, p):
    assert p[:4] == b"BPS1", "bad BPS magic"
    i = 4
    ssz, i = rd_varint(p, i)
    tsz, i = rd_varint(p, i)
    msz, i = rd_varint(p, i)
    i += msz
    assert ssz == len(src), "BPS source size %d != ROM %d" % (ssz, len(src))
    assert zlib.crc32(src) & 0xFFFFFFFF == int.from_bytes(p[-12:-8], "little"), \
        "BPS source CRC does not match this ROM"
    out = bytearray()
    end = len(p) - 12
    while i < end:
        v, i = rd_varint(p, i)
        action, length = v & 3, (v >> 2) + 1
        if action == 0:                       # SourceRead
            out += src[len(out):len(out) + length]
        elif action == 1:                     # TargetRead
            out += p[i:i + length]; i += length
        else:
            raise SystemExit("this verifier only handles the action types build.py emits")
    assert len(out) == tsz, "BPS target size mismatch"
    assert zlib.crc32(bytes(out)) & 0xFFFFFFFF == int.from_bytes(p[-8:-4], "little"), \
        "BPS target CRC mismatch"
    return bytes(out)

def apply_ips(src, p):
    assert p[:5] == b"PATCH", "bad IPS magic"
    out, i = bytearray(src), 5
    while True:
        if p[i:i + 3] == b"EOF":
            break
        off = int.from_bytes(p[i:i + 3], "big"); i += 3
        ln  = int.from_bytes(p[i:i + 2], "big"); i += 2
        if ln == 0:                            # RLE record
            rl = int.from_bytes(p[i:i + 2], "big"); i += 2
            out[off:off + rl] = bytes([p[i]]) * rl; i += 1
        else:
            out[off:off + ln] = p[i:i + ln]; i += ln
    return bytes(out)

ok = True
for name, fn in (("bps", apply_bps), ("ips", apply_ips)):
    path = os.path.join(ROOT, "build", "jangou-en." + name)
    got = fn(src, open(path, "rb").read())
    good = got == want
    ok &= good
    print("  %-4s -> %s  sha1 %s" % (name, "OK  " if good else "FAIL", hashlib.sha1(got).hexdigest()))
print("  target  sha1 %s" % hashlib.sha1(want).hexdigest())
sys.exit(0 if ok else 1)
