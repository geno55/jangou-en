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

ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_ROM  = os.path.join(ROOT, "Jangou (Japan).nes")
SRC_SHA1 = "e1de1fa7a7bbac0315f604beac74a6e296b89078"
HDR      = 16

BANK = lambda prg, n: prg[n * 0x4000:(n + 1) * 0x4000]
ORG  = lambda n: 0x8000 if n < 8 else 0xC000

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
        out.append((ORG(bank) + off, bank * 0x4000 + off, bytes(s)))
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
                             "$%05X" % (b * 0x4000 + i + 1),
                             "$%05X" % (b * 0x4000 + i + 5),
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
                                 "$%05X" % (b * 0x4000 + start), len(run),
                                 ldec(bytes(run)), bytes(run).hex(" ")])
                run = []
    return ["bank", "cpu_addr", "prg_offset", "length", "decoded", "bytes"], rows


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
    ap.add_argument("--check", action="store_true",
                    help="verify the committed files match; exit 1 if not")
    args = ap.parse_args()

    prg  = load_rom()
    tbl  = load_tbl(os.path.join(ROOT, "jangou.tbl"))
    kan  = load_tbl(os.path.join(ROOT, "jangou-kanji.tbl"))
    kdec = lambda bs: "".join(kan.get(b, "‹%02X›" % b) for b in bs)
    ldec = lambda bs: "".join(tbl.get(b, "・") for b in bs)
    print("ROM ok, %d 8x8 codes, %d kanji codes" % (len(tbl), len(kan)))

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
