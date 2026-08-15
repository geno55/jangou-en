#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify build/jangou-en.{bps,ips} against the patch format specifications.

Applying a patch with a reader written to match our own writer proves nothing -
it is one implementation agreeing with itself. This file used to do exactly
that, and said so out loud: "this verifier only handles the action types
build.py emits". Round-tripping through that reader could not tell you whether
beat, Flips or Lunar IPS would accept the output. Three things changed:

  * The BPS reader implements the WHOLE format - all four actions, including
    SourceCopy and TargetCopy, and a non-empty metadata block. build.py emits
    none of those. A reader that understands only its own encoder's subset
    cannot stand in for a third-party one.
  * All three CRC32s are checked, including the patch's own at p[-4:], which
    real patchers verify and this did not.
  * Both readers are exercised on hand-built patches that use the features
    build.py cannot produce, and on deliberately corrupted patches they must
    reject. A reader that accepts everything is not a check.

What this still is NOT: beat, Flips or Lunar IPS. It is a second
implementation written from the specification rather than from build.py, which
is a real improvement over a mirror and still not an independent tool. To close
the gap properly, apply build/jangou-en.bps to the clean ROM with a real
patcher and compare the SHA-1 against the one printed here. That is one
command and nobody has run it; see BUILD.md.

    python tools/verify_patch.py
"""
import os, sys, zlib, hashlib, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import utf8io          # noqa: F401  - ASCII today, but the rule has no holes

from rom import ROOT, SRC_ROM as SRC, BUILD_DIR as BUILD


class PatchError(Exception):
    """A patch a conforming patcher would reject."""


# ------------------------------------------------------------------ varint --
# beat's number encoding. Written from the format description, and cross-checked
# against build.py's encoder over a wide range below - if the two ever disagree
# the build is emitting something no patcher can read.
def enc_varint(n):
    out = bytearray()
    while True:
        x = n & 0x7F
        n >>= 7
        if n == 0:
            out.append(0x80 | x)
            return bytes(out)
        out.append(x)
        n -= 1


def dec_varint(b, i):
    n, shift = 0, 0
    while True:
        if i >= len(b):
            raise PatchError("varint runs off the end of the patch")
        x = b[i]; i += 1
        n += (x & 0x7F) << shift
        if x & 0x80:
            return n, i
        shift += 7
        n += 1 << shift


# --------------------------------------------------------------------- BPS --
SOURCE_READ, TARGET_READ, SOURCE_COPY, TARGET_COPY = range(4)

def apply_bps(src, p, check_source_crc=True):
    """Apply a BPS patch. Complete: all four actions, metadata, all three CRCs."""
    if len(p) < 4 + 3 + 12:
        raise PatchError("patch is too short to be BPS")
    if p[:4] != b"BPS1":
        raise PatchError("bad BPS magic %r" % p[:4])

    # The patch's own CRC covers everything before it. Real patchers check this
    # and refuse a corrupted download; the old verifier ignored it entirely.
    if zlib.crc32(p[:-4]) & 0xFFFFFFFF != int.from_bytes(p[-4:], "little"):
        raise PatchError("patch CRC mismatch - the patch file itself is corrupt")

    i = 4
    ssz, i = dec_varint(p, i)
    tsz, i = dec_varint(p, i)
    msz, i = dec_varint(p, i)
    if i + msz > len(p) - 12:
        raise PatchError("metadata block runs past the end of the patch")
    i += msz                                  # metadata is not interpreted

    if ssz != len(src):
        raise PatchError("patch expects a %d-byte source, got %d" % (ssz, len(src)))
    if check_source_crc and zlib.crc32(src) & 0xFFFFFFFF != int.from_bytes(p[-12:-8], "little"):
        raise PatchError("source CRC mismatch - this patch is for a different ROM")

    out = bytearray()
    src_rel = tgt_rel = 0
    end = len(p) - 12
    while i < end:
        v, i = dec_varint(p, i)
        action, length = v & 3, (v >> 2) + 1
        if len(out) + length > tsz:
            raise PatchError("action at patch offset %d overruns the declared "
                             "target size" % i)

        if action == SOURCE_READ:
            o = len(out)
            if o + length > len(src):
                raise PatchError("SourceRead reads past the end of the source")
            out += src[o:o + length]

        elif action == TARGET_READ:
            if i + length > end:
                raise PatchError("TargetRead reads past the end of the patch")
            out += p[i:i + length]
            i += length

        elif action == SOURCE_COPY:
            data, i = dec_varint(p, i)
            src_rel += (-1 if data & 1 else 1) * (data >> 1)
            if src_rel < 0 or src_rel + length > len(src):
                raise PatchError("SourceCopy reads outside the source")
            out += src[src_rel:src_rel + length]
            src_rel += length

        else:                                  # TARGET_COPY
            data, i = dec_varint(p, i)
            tgt_rel += (-1 if data & 1 else 1) * (data >> 1)
            if tgt_rel < 0:
                raise PatchError("TargetCopy reads before the start of the target")
            # Reads from the output as it is being written, so it may overlap
            # itself - byte at a time is the specified behaviour, not a slice.
            for _ in range(length):
                if tgt_rel >= len(out):
                    raise PatchError("TargetCopy reads past what has been written")
                out.append(out[tgt_rel])
                tgt_rel += 1

    if i != end:
        raise PatchError("action stream ended %d bytes past the footer" % (i - end))
    if len(out) != tsz:
        raise PatchError("produced %d bytes, patch declares %d" % (len(out), tsz))
    if zlib.crc32(bytes(out)) & 0xFFFFFFFF != int.from_bytes(p[-8:-4], "little"):
        raise PatchError("target CRC mismatch - the result is not what the patch describes")
    return bytes(out)


# --------------------------------------------------------------------- IPS --
def apply_ips(src, p):
    """Apply an IPS patch, including RLE records, which build.py never emits."""
    if p[:5] != b"PATCH":
        raise PatchError("bad IPS magic %r" % p[:5])
    out, i = bytearray(src), 5
    while True:
        if i + 3 > len(p):
            raise PatchError("patch ends without an EOF marker")
        if p[i:i + 3] == b"EOF":
            i += 3
            break
        off = int.from_bytes(p[i:i + 3], "big"); i += 3
        if i + 2 > len(p):
            raise PatchError("record header truncated")
        ln = int.from_bytes(p[i:i + 2], "big"); i += 2
        if ln == 0:                            # RLE
            if i + 3 > len(p):
                raise PatchError("RLE record truncated")
            rl = int.from_bytes(p[i:i + 2], "big"); i += 2
            val = p[i]; i += 1
            if rl == 0:
                raise PatchError("RLE record with zero run length")
            chunk = bytes([val]) * rl
        else:
            if i + ln > len(p):
                raise PatchError("record data truncated")
            chunk = p[i:i + ln]; i += ln
        if off > len(out):                     # IPS may extend the file
            out += b"\x00" * (off - len(out))
        out[off:off + len(chunk)] = chunk
    if i != len(p):
        raise PatchError("%d trailing bytes after EOF" % (len(p) - i))
    return bytes(out)


# ------------------------------------------------- conformance / negatives --
def bps_build(source, target, actions, metadata=b""):
    """Assemble a BPS patch from raw actions, for testing the reader."""
    body = bytearray(b"BPS1")
    body += enc_varint(len(source)) + enc_varint(len(target))
    body += enc_varint(len(metadata)) + metadata
    for a in actions:
        body += a
    body += (zlib.crc32(source) & 0xFFFFFFFF).to_bytes(4, "little")
    body += (zlib.crc32(target) & 0xFFFFFFFF).to_bytes(4, "little")
    body += (zlib.crc32(bytes(body)) & 0xFFFFFFFF).to_bytes(4, "little")
    return bytes(body)


def act(action, length, extra=b""):
    return enc_varint(((length - 1) << 2) | action) + extra


def conformance():
    """Feed the readers what build.py cannot produce. Returns list of problems."""
    bad = []

    # -- varint: round-trip, and agreement with the encoder the build ships
    import build
    vals = list(range(0, 5000)) + [2 ** k + d for k in range(7, 32) for d in (-1, 0, 1)]
    for n in vals:
        e = enc_varint(n)
        if e != build.bps_varint(n):
            bad.append("varint(%d): build.py emits %r, the spec encoder gives %r"
                       % (n, build.bps_varint(n), e)); break
        got, j = dec_varint(e, 0)
        if got != n or j != len(e):
            bad.append("varint(%d) does not round-trip (got %d)" % (n, got)); break

    # -- BPS with every action type, plus metadata. build.py emits only
    #    SourceRead and TargetRead, and never any metadata.
    # Both copy actions carry a SIGNED relative offset: bit 0 is the sign. A
    # patch that only ever seeks forward never exercises that bit, and a reader
    # that ignores it passes - so each copy action appears twice here, once
    # seeking forward and once back.
    source = bytes(range(32))
    target = (bytes(range(4)) + b"\xAA\xBB\xCC" + source[16:20] + source[8:12]
              + b"\xAA\xBB\xCC" + bytes(range(4)))
    patch = bps_build(source, target, [
        act(SOURCE_READ, 4),
        act(TARGET_READ, 3, b"\xAA\xBB\xCC"),
        act(SOURCE_COPY, 4, enc_varint(16 << 1)),        # +16, src_rel 0 -> 20
        act(SOURCE_COPY, 4, enc_varint((12 << 1) | 1)),  # -12, src_rel 20 -> 12
        act(TARGET_COPY, 3, enc_varint(4 << 1)),         # +4,  tgt_rel 0 -> 7
        act(TARGET_COPY, 4, enc_varint((7 << 1) | 1)),   # -7,  tgt_rel 7 -> 4
    ], metadata=b"<patch/>")
    try:
        got = apply_bps(source, patch)
        if got != target:
            bad.append("BPS all-actions patch produced %r, expected %r" % (got, target))
    except PatchError as e:
        bad.append("BPS all-actions patch rejected: %s" % e)

    # -- TargetCopy overlapping itself, the RLE-like case
    src2 = b""
    tgt2 = b"ab" * 8
    p2 = bps_build(src2, tgt2, [act(TARGET_READ, 2, b"ab"),
                                act(TARGET_COPY, 14, enc_varint(0 << 1))])
    try:
        got = apply_bps(src2, p2)
        if got != tgt2:
            bad.append("BPS overlapping TargetCopy gave %r, expected %r" % (got, tgt2))
    except PatchError as e:
        bad.append("BPS overlapping TargetCopy rejected: %s" % e)

    # -- IPS RLE, which build.py never emits
    isrc = bytes(20)
    ips = (b"PATCH"
           + (2).to_bytes(3, "big") + (0).to_bytes(2, "big")
           + (5).to_bytes(2, "big") + b"\xFF"
           + (10).to_bytes(3, "big") + (3).to_bytes(2, "big") + b"\x01\x02\x03"
           + b"EOF")
    want = bytearray(isrc)
    want[2:7] = b"\xFF" * 5
    want[10:13] = b"\x01\x02\x03"
    try:
        got = apply_ips(isrc, ips)
        if got != bytes(want):
            bad.append("IPS RLE record mishandled")
    except PatchError as e:
        bad.append("IPS RLE record rejected: %s" % e)

    return bad


def negatives(src, bps, ips):
    """Corrupt the real patches; every one must be rejected FOR THE STATED
    REASON.

    The reason matters. The patch CRC covers every other byte, so without care
    every one of these is caught by that one check and the source-CRC and
    target-CRC paths are never executed at all - the controls pass while
    testing nothing. So each mutation re-seals the patch CRC afterwards, which
    is also what a real corrupted-then-rewritten patch would look like, and the
    error message has to match what was supposed to fire."""
    out = []

    def reseal(p):
        p[-4:] = (zlib.crc32(bytes(p[:-4])) & 0xFFFFFFFF).to_bytes(4, "little")

    def one(label, kind, mutate, expect, seal=True):
        p = bytearray(bps if kind == "bps" else ips)
        mutate(p)
        if seal and kind == "bps":
            reseal(p)
        try:
            (apply_bps if kind == "bps" else apply_ips)(src, bytes(p))
            out.append((label, False, "accepted"))
        except (PatchError, IndexError) as e:
            why = str(e)
            out.append((label, expect in why, why))

    one("bps: magic corrupted", "bps",
        lambda p: p.__setitem__(1, 0x58), "magic")
    one("bps: source CRC wrong", "bps",
        lambda p: p.__setitem__(-12, p[-12] ^ 1), "source CRC")
    one("bps: target CRC wrong", "bps",
        lambda p: p.__setitem__(-8, p[-8] ^ 1), "target CRC")
    one("bps: patch CRC wrong", "bps",
        lambda p: p.__setitem__(-1, p[-1] ^ 1), "patch CRC", seal=False)
    one("bps: truncated", "bps",
        lambda p: p.__delitem__(slice(-13, -12)), "")
    one("bps: a data byte flipped", "bps",
        lambda p: p.__setitem__(len(p) // 2, p[len(p) // 2] ^ 0xFF), "")
    one("bps: declared target size", "bps",
        lambda p: p.__setitem__(7, p[7] ^ 0x02), "")
    one("ips: magic corrupted", "ips",
        lambda p: p.__setitem__(1, 0x58), "magic")
    one("ips: EOF removed", "ips",
        lambda p: p.__delitem__(slice(-3, None)), "EOF")
    one("ips: record truncated", "ips",
        lambda p: p.__delitem__(slice(-8, -3)), "")
    return out


# -------------------------------------------------------------------- main --
def main():
    src  = open(SRC, "rb").read()
    want_path = os.path.join(BUILD, "jangou-en.nes")
    if not os.path.exists(want_path):
        raise SystemExit("missing %s - run tools/build.py first" % want_path)
    want = open(want_path, "rb").read()
    bps = open(os.path.join(BUILD, "jangou-en.bps"), "rb").read()
    ips = open(os.path.join(BUILD, "jangou-en.ips"), "rb").read()

    failures = []

    print("reader conformance - features build.py never emits")
    problems = conformance()
    failures += problems
    for p in problems:
        print("  FAIL  %s" % p)
    if not problems:
        print("  varint round-trip and agreement with build.py    OK")
        print("  BPS SourceCopy / TargetCopy / metadata           OK")
        print("  BPS TargetCopy overlapping its own output        OK")
        print("  IPS RLE records                                  OK")

    print("\nnegative controls - rejected, and for the stated reason")
    for label, caught, why in negatives(src, bps, ips):
        print("  %-28s %-9s %s" % (label, "rejected" if caught else "WRONG", why[:44]))
        if not caught:
            failures.append("%s: %s" % (label, why))

    print("\nthe real patches")
    for name, blob, fn in (("bps", bps, apply_bps), ("ips", ips, apply_ips)):
        try:
            got = fn(src, blob)
        except PatchError as e:
            print("  %-4s -> FAIL  %s" % (name, e))
            failures.append("%s: %s" % (name, e)); continue
        good = got == want
        if not good:
            failures.append("%s does not reproduce build/jangou-en.nes" % name)
        print("  %-4s -> %s  sha1 %s"
              % (name, "OK  " if good else "FAIL", hashlib.sha1(got).hexdigest()))
    print("  target  sha1 %s" % hashlib.sha1(want).hexdigest())

    print("\n  Still not independent: this is a second implementation written")
    print("  from the spec, not beat or Flips. Apply the .bps with a real")
    print("  patcher and compare against the SHA-1 above to close that gap.")

    if failures:
        print("\nFAILED (%d)" % len(failures))
        for f in failures:
            print("  " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
