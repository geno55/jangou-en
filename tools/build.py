#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jangou English translation patch - build script.

Pure function:  clean ROM (SHA-1 verified) + script/yaku-en.txt  ->  build/*

The source ROM is never modified. Output is a patched .nes plus .bps and .ips
patches. Re-running with the same inputs produces byte-identical output.

    python tools/build.py
    python tools/build.py --report        # space budget only, no build
    python tools/build.py --strict        # fail unless every name fits
    python tools/build.py --use-dora-block  # opt in to 81 more bytes (see below)

See BUILD.md.  Engine details: YAKU-PRINTER.md.
"""
import sys, os, csv, zlib, hashlib, argparse

ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_ROM    = os.path.join(ROOT, "Jangou (Japan).nes")
SCRIPT_TXT = os.path.join(ROOT, "script", "yaku-en.txt")
CALLSITES  = os.path.join(ROOT, "yaku-callsites.csv")
OUT_DIR    = os.path.join(ROOT, "build")

SRC_SHA1 = "e1de1fa7a7bbac0315f604beac74a6e296b89078"
HDR      = 16
BANK04   = 0x10000
def cpu2file(a):
    assert 0x8000 <= a <= 0xBFFF, hex(a)
    return HDR + BANK04 + (a - 0x8000)

# ---------------------------------------------------------------- config ----
SPACE_CODE = 0x01   # no glyph; the patched printer advances the cursor instead
MAX_LEN    = 14     # columns. The original draws 7 kanji x 2 = 14, so 14 is
                    # proven safe. Raise only after measuring the score window.

# Space the printer can reach. It runs in bank 04, so pointers must resolve
# inside bank 04's $8000-$BFFF window. Verified by recursive descent plus a
# full operand cross-reference over all 16 banks: banks 04 and 0F contain NO
# other free space. The only reusable bytes are the Japanese table itself.
POOL_BASE = [(0x9E18, 0x9F13), (0x9F65, 0x9F79)]        # 252 + 21 = 273

# $9F14-$9F64 holds ドラ１..ドラ１８. No call site points at it and no
# computed pointer into it could be found, so it is very likely dead data --
# but "could not find" is not "does not exist". Opt in with --use-dora-block
# only after confirming in an emulator that dora names never appear on screen.
POOL_DORA = (0x9F14, 0x9F64)                            # +81

# ------------------------------------------------------------- charset ------
def _mk_encoder():
    m = {c: 0x50 + i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")}
    m.update({c: 0x74 + i for i, c in enumerate("123456789")})
    m["0"] = 0x7D
    m.update({" ": SPACE_CODE, "*": 0x6A, "-": 0x6B, "!": 0x6C, "?": 0x6D,
              "(": 0x6E, ")": 0x6F, "<": 0x70, ">": 0x71, "/": 0x72, ".": 0x7F})
    return m
ENC = _mk_encoder()

def encode(text, where):
    out = bytearray()
    for ch in text:
        if ch not in ENC:
            raise SystemExit(
                "%s: character %r is not in the game's 8x8 font.\n"
                "  Usable: A-Z 0-9 space * - ! ? ( ) < > / .\n"
                "  The font has no lowercase, comma, colon or apostrophe."
                % (where, ch))
        out.append(ENC[ch])
    out.append(0x00)
    return bytes(out)

# --------------------------------------------------------- engine patch -----
# Rewrite the body of the yaku printer $9F7A to draw 8x8 characters instead of
# 16x16 kanji. Exactly 29 bytes, same length as the original, so no address
# moves and nothing downstream needs fixing up.
#
#   $9F9E  LDA ($0D),Y     fetch character
#   $9FA0  BEQ $9FBB       $00 terminates
#   $9FA2  STA $02
#   $9FA4  CMP #SPACE      space: advance the cursor, draw nothing
#   $9FA6  BEQ $9FAE
#   $9FA8  JSR $80C6       -> $C024  upload one 8x8 glyph
#   $9FAB  JSR $80D6       -> $C03C  write one nametable tile
#   $9FAE  INC $0C         CHR-RAM slot += 1  (was += 4)
#   $9FB0  INC $00         column      += 1   (was += 2)
#   $9FB2  INC $05
#   $9FB4  JMP $9F98
#   $9FB7  NOP x4          pad so $9FBB stays put
PRINTER_ADDR = 0x9F9E
PRINTER_OLD  = bytes.fromhex("b10d f019 8502 20ca80 20da80 e60c e60c e60c e60c"
                             "e600 e600 e605 4c989f".replace(" ", ""))
PRINTER_NEW  = bytes.fromhex(("b10d f019 8502 c9%02x f006 20c680 20d680 e60c"
                              "e600 e605 4c989f eaeaeaea" % SPACE_CODE).replace(" ", ""))
assert len(PRINTER_OLD) == len(PRINTER_NEW) == 29

# ------------------------------------------------------------- inputs -------
def load_script():
    rows = []
    with open(SCRIPT_TXT, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) != 3:
                raise SystemExit("%s:%d: expected 'index | japanese | english'"
                                 % (SCRIPT_TXT, lineno))
            idx, jp, en = parts
            rows.append({"line": lineno, "idx": int(idx.lstrip("*")),
                         "jp": jp, "en": en.upper(), "prio": idx.startswith("*")})
    return rows

def load_callsites():
    out = {}
    with open(CALLSITES, encoding="utf-8-sig") as f:
        for i, r in enumerate(csv.DictReader(f)):
            out[i] = {"lo": int(r["patch_lo_prg_off"].lstrip("$"), 16),
                      "hi": int(r["patch_hi_prg_off"].lstrip("$"), 16),
                      "jp": r["japanese"],
                      "jp_bytes": bytes.fromhex(r["bytes"].replace(" ", "")) + b"\x00"}
    return out

# ------------------------------------------------------------ packing -------
def coalesce(regions):
    """Merge touching/overlapping regions so packing sees one big span."""
    rs = sorted(regions)
    out = [list(rs[0])]
    for lo, hi in rs[1:]:
        if lo <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return [tuple(r) for r in out]

def dedupe(chosen):
    """Unique strings, longest first, dropping any that is a suffix of another.

    The sort key is (-len, bytes), not just len. A set of bytes objects
    iterates in hash order, which Python randomises per process, so sorting on
    length alone leaves equal-length strings in arbitrary order and the build
    stops being reproducible. Ties must break on content."""
    uniq = sorted(set(chosen.values()), key=lambda s: (-len(s), s))
    kept = []
    for s in uniq:
        if not any(k.endswith(s) for k in kept):
            kept.append(s)
    return kept

def pack_size(chosen):
    kept = dedupe(chosen)
    return sum(len(k) for k in kept), kept

def try_layout(chosen, pool):
    """Real first-fit placement. Returns {bytes: cpu_addr} or None if it will
    not fit. Used by the selection loop so the budget can never disagree with
    the actual layout - a size total ignores per-region fragmentation."""
    kept = dedupe(chosen)
    cursors = [lo for lo, _ in pool]
    addr = {}
    for s in kept:
        for ri, (lo, hi) in enumerate(pool):
            if cursors[ri] + len(s) - 1 <= hi:
                addr[s] = cursors[ri]
                cursors[ri] += len(s)
                break
        else:
            return None
    return addr

def resolve(s, addr):
    """Address of s, allowing it to be the suffix of an already-placed string.
    Iterates in sorted order so the result never depends on dict ordering."""
    if s in addr:
        return addr[s]
    for k in sorted(addr):
        if k.endswith(s):
            return addr[k] + len(k) - len(s)
    raise KeyError(s)

# ------------------------------------------------------------ patch fmts ----
def bps_varint(n):
    out = bytearray()
    while True:
        x = n & 0x7F
        n >>= 7
        if n == 0:
            out.append(0x80 | x); break
        out.append(x); n -= 1
    return bytes(out)

def make_bps(src, dst):
    p = bytearray(b"BPS1")
    p += bps_varint(len(src)) + bps_varint(len(dst)) + bps_varint(0)
    i = 0
    while i < len(dst):
        same = src[i] == dst[i]
        j = i
        while j < len(dst) and (src[j] == dst[j]) == same:
            j += 1
        n = j - i
        p += bps_varint(((n - 1) << 2) | (0 if same else 1))
        if not same:
            p += dst[i:j]
        i = j
    p += (zlib.crc32(src) & 0xFFFFFFFF).to_bytes(4, "little")
    p += (zlib.crc32(dst) & 0xFFFFFFFF).to_bytes(4, "little")
    p += (zlib.crc32(bytes(p)) & 0xFFFFFFFF).to_bytes(4, "little")
    return bytes(p)

def make_ips(src, dst):
    p = bytearray(b"PATCH")
    i = 0
    while i < len(dst):
        if src[i] == dst[i]:
            i += 1; continue
        j = i
        while j < len(dst) and src[j] != dst[j] and (j - i) < 0xFFFF:
            j += 1
        off = i - 1 if i == 0x454F46 else i
        chunk = dst[off:j]
        p += off.to_bytes(3, "big") + len(chunk).to_bytes(2, "big") + chunk
        i = j
    return bytes(p + b"EOF")

# ---------------------------------------------------------------- main ------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--use-dora-block", action="store_true")
    ap.add_argument("--refactor", action="store_true",
                    help="collapse the 53 unrolled yaku blocks into a loop, "
                         "freeing ~2.6KB in bank 04 (see tools/refactor.py)")
    args = ap.parse_args()

    if not os.path.exists(SRC_ROM):
        raise SystemExit("missing source ROM: %s" % SRC_ROM)
    src = open(SRC_ROM, "rb").read()
    sha = hashlib.sha1(src).hexdigest()
    if sha != SRC_SHA1:
        raise SystemExit("source ROM SHA-1 mismatch\n  expected %s\n  got      %s\n"
                         "This patch targets one exact dump." % (SRC_SHA1, sha))
    print("source ROM  OK  sha1 %s  (%d bytes)" % (sha, len(src)))

    # The refactor rewrites $8F51-$9B1D, so it must happen before anything is
    # placed. It returns the freed span, which joins the string pool.
    dst = bytearray(src)
    regions = list(POOL_BASE) + ([POOL_DORA] if args.use_dora_block else [])
    ptr_tables = None
    if args.refactor:
        import refactor
        print("\ncall-site refactor")
        free_lo, free_hi, ptr_tables = refactor.apply(dst)
        regions.append((free_lo, free_hi))
    pool = coalesce(regions)
    pool_total = sum(hi - lo + 1 for lo, hi in pool)

    sites = load_callsites()
    rows  = load_script()
    english = {}
    for r in rows:
        if r["idx"] not in sites:
            raise SystemExit("line %d: index %d has no call site" % (r["line"], r["idx"]))
        if len(r["en"]) > MAX_LEN:
            raise SystemExit("line %d: %r is %d chars, limit %d"
                             % (r["line"], r["en"], len(r["en"]), MAX_LEN))
        english[r["idx"]] = {"bytes": encode(r["en"], "line %d" % r["line"]),
                             "text": r["en"], "prio": r["prio"], "order": r["line"]}

    # EVERY call site must be placed, and it can NOT be left pointing at its
    # Japanese string: the patched printer sends every code to the 8x8 glyph
    # loader, so a kanji code would fetch tile $200+code -- bitmap garbage.
    # The patch is all-or-nothing per string. Anything we cannot translate is
    # therefore pointed at a single shared empty string: it draws nothing and
    # advances the line. Blank is honest; garbage is not.
    EMPTY = b"\x00"
    chosen = {i: EMPTY for i in sites}
    order = sorted(english, key=lambda i: (not english[i]["prio"], english[i]["order"]))
    upgraded = []
    for i in order:
        trial = dict(chosen)
        trial[i] = english[i]["bytes"]
        if try_layout(trial, pool) is not None:
            chosen = trial
            upgraded.append(i)

    final_size, kept = pack_size(chosen)
    want_all, _ = pack_size({i: english[i]["bytes"] for i in english})
    jp_size, _  = pack_size({i: sites[i]["jp_bytes"] for i in sites})

    print("\nspace budget")
    print("  pool                %4d bytes  %s" % (pool_total,
          " + ".join("$%04X-$%04X" % x for x in pool)))
    print("  all-Japanese        %4d bytes  (the table as shipped)" % jp_size)
    print("  all-English         %4d bytes  (what the full script wants)" % want_all)
    print("  shortfall           %4d bytes" % max(0, want_all - pool_total))
    print("  this build          %4d bytes  -> %d of %d names in English"
          % (final_size, len(upgraded), len(english)))
    if args.report:
        return
    if len(upgraded) < len(english) and args.strict:
        raise SystemExit("\n--strict: %d names do not fit (need %d more bytes). "
                         "See BUILD.md." % (len(english) - len(upgraded), want_all - pool_total))

    # write strings, repoint every call site
    for lo, hi in pool:                       # clear the pool first
        dst[cpu2file(lo):cpu2file(hi) + 1] = b"\x00" * (hi - lo + 1)
    addr = try_layout(chosen, pool)
    if addr is None:
        raise SystemExit("internal: final layout failed")
    for s in sorted(addr):
        o = cpu2file(addr[s])
        dst[o:o + len(s)] = s
    for i, s in chosen.items():
        a = resolve(s, addr)
        # After the refactor, blocks 2..54 no longer carry their pointer as an
        # immediate - it lives in the tbl_lo/tbl_hi arrays. csv index == block
        # index for 0..55, so slot = i - 2.
        if ptr_tables is not None and 2 <= i <= 54:
            t_lo, t_hi = ptr_tables
            dst[cpu2file(t_lo + i - 2)] = a & 0xFF
            dst[cpu2file(t_hi + i - 2)] = (a >> 8) & 0xFF
        else:
            dst[HDR + sites[i]["lo"]] = a & 0xFF
            dst[HDR + sites[i]["hi"]] = (a >> 8) & 0xFF

    # engine patch
    o = cpu2file(PRINTER_ADDR)
    if bytes(dst[o:o + 29]) != PRINTER_OLD:
        raise SystemExit("printer bytes at $%04X unexpected - wrong ROM or already patched"
                         % PRINTER_ADDR)
    dst[o:o + 29] = PRINTER_NEW

    assert len(dst) == len(src)
    dst = bytes(dst)
    os.makedirs(OUT_DIR, exist_ok=True)
    open(os.path.join(OUT_DIR, "jangou-en.nes"), "wb").write(dst)
    open(os.path.join(OUT_DIR, "jangou-en.bps"), "wb").write(make_bps(src, dst))
    open(os.path.join(OUT_DIR, "jangou-en.ips"), "wb").write(make_ips(src, dst))

    print("\nin English (%d):" % len(upgraded))
    for i in sorted(upgraded):
        print("    %-16s %s" % (sites[i]["jp"], english[i]["text"]))
    left = [i for i in english if i not in upgraded]
    if left:
        print("\nBLANK - no room for English (%d): %s"
              % (len(left), ", ".join(sites[i]["jp"] for i in sorted(left))))
        print("  These now print nothing at all. They can NOT be left in Japanese:")
        print("  the patched printer sends every code to the 8x8 glyph loader, so a")
        print("  kanji code would fetch tile $200+code and draw garbage. Blank is the")
        print("  honest fallback. Reclaim space to finish them -- see BUILD.md.")

    print("\n  build/jangou-en.nes   sha1 %s" % hashlib.sha1(dst).hexdigest())
    print("  build/jangou-en.bps")
    print("  build/jangou-en.ips")
    print("  %d bytes differ from the source ROM"
          % sum(1 for a, b in zip(src, dst) if a != b))

if __name__ == "__main__":
    main()
