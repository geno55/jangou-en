#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regenerate the derived data files from the ROM.

    python tools/extract.py            # rewrite the CSVs
    python tools/extract.py --check    # verify the committed CSVs still match

Source of truth, hand-verified, NOT generated:
    jangou.tbl         8x8 character table
    jangou-kanji.tbl   16x16 kanji table

Generated from the ROM plus those tables:
    yaku-callsites.csv    62 call sites and their patch offsets  (build input)
    yaku-names.csv        90 enumerated Japanese strings
    string-inventory.csv  UI text triage; contains false positives

The two .tbl files were derived by rendering every glyph and checking the
result against the game's own yaku spellings - see KANJI-TABLE.md. That step
needs a human eye, so the tables are checked in as source. Everything below is
mechanical and reproducible.
"""
import sys, os, csv, io, hashlib, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import utf8io          # noqa: F401  - --check echoes CSV lines full of kanji

from rom import ROOT, SRC_ROM, SRC_SHA1, HDR, BANK_SIZE, window

BANK = lambda prg, n: prg[n * BANK_SIZE:(n + 1) * BANK_SIZE]
# Where a bank is seen by the 6502. This used to be `0x8000 if n < 8 else
# 0xC000`, which put banks $08-$0E at $C000; the mapper puts every bank except
# $0F at $8000. No inventory row happened to land in $08-$0E, so the two
# copies of the cartridge map disagreed without ever producing a wrong byte.
ORG  = window

# Bank 04 / 05 string tables. The bank 05 start is found by scanning, below.
YAKU_TABLE   = (4, 0x9E18, "scoring")
STATS_SEARCH = (5, 0xAB80, 0xAC00, "records screen")
CODE_BANKS   = (2, 3, 4, 5, 0x0F)     # 00/01 are font, 06/07 are title art
MAX_KANJI    = 0x86                   # codes above this are not kanji


def load_tbl(path):
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[int(k, 16)] = v
    return out


def load_rom():
    rom = open(SRC_ROM, "rb").read()
    sha = hashlib.sha1(rom).hexdigest()
    if sha != SRC_SHA1:
        raise SystemExit("source ROM SHA-1 mismatch\n  expected %s\n  got      %s"
                         % (SRC_SHA1, sha))
    return rom[HDR:]


def find_stats_table(prg, dec_ok):
    """Locate the records-screen table by taking the start that yields the most
    consecutive valid $00-terminated entries."""
    bank, lo, hi, _ = STATS_SEARCH
    best = (0, lo)
    for start in range(lo, hi):
        n = len(enum_table(prg, bank, start, dec_ok))
        if n > best[0]:
            best = (n, start)
    return best[1]


def enum_table(prg, bank, start_cpu, dec_ok, maxn=140):
    """Walk $00-terminated strings until one contains a non-kanji code."""
    blk = BANK(prg, bank)
    off = start_cpu - ORG(bank)
    out = []
    while len(out) < maxn and off < len(blk):
        end = off
        while end < len(blk) and blk[end] != 0:
            end += 1
        s = blk[off:end]
        if not s or any(b > MAX_KANJI for b in s):
            break
        out.append((ORG(bank) + off, bank * BANK_SIZE + off, bytes(s)))
        off = end + 1
    return out


def gen_callsites(prg, kdec):
    """Find every 'LDA #lo / STA $0D / LDA #hi / STA $0E / JSR $9F7A'."""
    rows = []
    for b in range(8):
        blk = BANK(prg, b)
        for i in range(len(blk) - 11):
            if (blk[i] == 0xA9 and blk[i+2] == 0x85 and blk[i+3] == 0x0D and
                    blk[i+4] == 0xA9 and blk[i+6] == 0x85 and blk[i+7] == 0x0E and
                    blk[i+8] == 0x20 and blk[i+9] == 0x7A and blk[i+10] == 0x9F):
                ptr = blk[i+1] | (blk[i+5] << 8)
                so = ptr - ORG(b)
                end = so
                while end < len(blk) and blk[end] != 0:
                    end += 1
                s = blk[so:end]
                rows.append(["%02X" % b, "$%04X" % (ORG(b) + i),
                             "$%05X" % (b * BANK_SIZE + i + 1),
                             "$%05X" % (b * BANK_SIZE + i + 5),
                             "$%04X" % ptr, len(s), kdec(s), s.hex(" ")])
    return ["bank", "callsite_cpu", "patch_lo_prg_off", "patch_hi_prg_off",
            "pointer_cpu", "length", "japanese", "bytes"], rows


def gen_names(prg, kdec):
    stats_start = find_stats_table(prg, kdec)
    rows = []
    for bank, start, label in (YAKU_TABLE,
                               (STATS_SEARCH[0], stats_start, STATS_SEARCH[3])):
        for i, (cpu, off, s) in enumerate(enum_table(prg, bank, start, kdec)):
            rows.append(["%02X" % bank, label, "$%04X" % cpu, "$%05X" % off,
                         i, len(s), kdec(s), s.hex(" ")])
    return ["bank", "table", "cpu_addr", "prg_offset", "index", "length",
            "japanese", "bytes"], rows


def gen_inventory(prg, tbl, ldec):
    """Runs of >=3 bytes that decode as 8x8 charset. Heuristic: opcode bytes
    fall in the charset range constantly, so this contains false positives."""
    rows = []
    for b in CODE_BANKS:
        blk = BANK(prg, b)
        run, start = [], 0
        for i, x in enumerate(blk):
            if x in tbl:
                if not run:
                    start = i
                run.append(x)
            else:
                if len(run) >= 3:
                    rows.append(["%02X" % b, "$%04X" % (ORG(b) + start),
                                 "$%05X" % (b * BANK_SIZE + start), len(run),
                                 ldec(bytes(run)), bytes(run).hex(" ")])
                run = []
    return ["bank", "cpu_addr", "prg_offset", "length", "decoded", "bytes"], rows


# --------------------------------------------------- the UI text estimate --
# PHASE1 quoted 882 `LDA #imm / STA $02` sites and README rounded it to
# "roughly 880". The count is right and the question is wrong, three times over:
#
#   * 210 of them are in banks $08-$0E, which PHASE1's own analysis proves
#     cannot execute - they are seven byte-identical copies of the fixed bank,
#     carrying seven copies of its 30 sites.
#   * zero page $02 is not a character register. It carries the han value into
#     $A058 as well, so the pattern counts scoring code as text. The refactor
#     proved it: bank 04 has 201 of these before and 148 after, and the 53
#     that vanished are exactly the 53 collapsed blocks.
#   * $02 is general scratch besides. Bank 03 is full of `LDA #$FF / STA $02`,
#     and $FF is not a code in either character table.
#
# What is left is an upper bound, not a count. Nothing here proves a site
# draws anything; it only removes the ones that provably do not.
DEAD_BANKS = tuple(range(0x08, 0x0F))    # duplicate builds of the fixed bank
LIVE_BANKS = (2, 3, 4, 5, 6, 7, 0x0F)    # 00/01 are font data and hold none
HAN_CALL   = b"\x20\x58\xa0"             # JSR $A058, the han-value display


def count_ui_sites(rom, valid_codes):
    """Breakdown of the `LDA #imm / STA $02` population. Returns a dict."""
    prg_ = rom if len(rom) < 0x40000 + HDR else rom[HDR:]
    def sites(bank):
        b = BANK(prg_, bank)
        return [(i, b[i + 1], b) for i in range(len(b) - 3)
                if b[i] == 0xA9 and b[i + 2] == 0x85 and b[i + 3] == 0x02]

    total = sum(len(sites(k)) for k in range(16))
    dead  = sum(len(sites(k)) for k in DEAD_BANKS)
    han = not_a_code = maybe = 0
    for k in LIVE_BANKS:
        for i, imm, b in sites(k):
            if HAN_CALL in bytes(b[i + 4:i + 28]):
                han += 1
            elif imm not in valid_codes:
                not_a_code += 1
            else:
                maybe += 1
    return {"total": total, "dead": dead, "live": total - dead,
            "han": han, "not_a_code": not_a_code, "maybe": maybe}


def report_counts(rom, tbl, kan):
    c = count_ui_sites(rom, set(tbl) | set(kan))
    print("UI text: how many hardcoded character sites are really there")
    print("  %4d  `LDA #imm / STA $02` sites in the image" % c["total"])
    print("  %4d  in banks $08-$0E, which cannot execute (7 copies of bank $0F)"
          % c["dead"])
    print("  ----")
    print("  %4d  in banks that run" % c["live"])
    print("  %4d  feed JSR $A058 - the han value, not a character" % c["han"])
    print("  %4d  load a byte that is not a code in either charset" % c["not_a_code"])
    print("  ----")
    print("  %4d  could still be a character - an UPPER BOUND, not a count"
          % c["maybe"])
    print()
    print("  Nothing here proves a site draws anything. It removes only the")
    print("  ones that provably do not, and 'roughly 880' was the population")
    print("  before any of that was subtracted.")
    return c


def render(header, rows):
    """CSV text. LF endings to match .gitattributes; BOM so spreadsheets read
    the Japanese columns as UTF-8."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return "﻿" + buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--counts", action="store_true",
                    help="report the UI text site breakdown and exit")
    ap.add_argument("--check", action="store_true",
                    help="verify the committed files match; exit 1 if not")
    args = ap.parse_args()

    prg  = load_rom()
    tbl  = load_tbl(os.path.join(ROOT, "jangou.tbl"))
    kan  = load_tbl(os.path.join(ROOT, "jangou-kanji.tbl"))
    kdec = lambda bs: "".join(kan.get(b, "‹%02X›" % b) for b in bs)
    ldec = lambda bs: "".join(tbl.get(b, "・") for b in bs)
    print("ROM ok, %d 8x8 codes, %d kanji codes" % (len(tbl), len(kan)))

    if args.counts:
        print()
        report_counts(open(SRC_ROM, "rb").read(), tbl, kan)
        return 0

    outputs = {
        "yaku-callsites.csv":   gen_callsites(prg, kdec),
        "yaku-names.csv":       gen_names(prg, kdec),
        "string-inventory.csv": gen_inventory(prg, tbl, ldec),
    }

    bad = 0
    for name, (header, rows) in outputs.items():
        path = os.path.join(ROOT, name)
        text = render(header, rows)
        if args.check:
            # newline="" so line endings survive the read. With the default,
            # Python converts CRLF to LF and a CRLF/LF difference is invisible.
            have = (open(path, encoding="utf-8-sig", newline="").read()
                    if os.path.exists(path) else None)
            want = text.lstrip("﻿")
            if have is None:
                print("  %-22s MISSING" % name); bad += 1
            elif have == want:
                print("  %-22s OK    %d rows" % (name, len(rows)))
            elif have.replace("\r\n", "\n") == want:
                print("  %-22s OK    %d rows (committed copy uses CRLF)" % (name, len(rows)))
            else:
                print("  %-22s DIFFERS  %d rows generated" % (name, len(rows)))
                h = have.replace("\r\n", "\n").split("\n")
                g = want.split("\n")
                for i in range(min(len(h), len(g))):
                    if h[i] != g[i]:
                        print("      line %d\n        committed: %s\n        generated: %s"
                              % (i + 1, h[i][:90], g[i][:90]))
                        break
                if len(h) != len(g):
                    print("      line counts: committed %d, generated %d" % (len(h), len(g)))
                bad += 1
        else:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            print("  wrote %-22s %d rows" % (name, len(rows)))

    if args.check:
        print("\n%s" % ("all derived files match the ROM" if not bad
                        else "%d file(s) out of date - run without --check" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
