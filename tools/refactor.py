#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Collapse the unrolled yaku call-site blocks in bank 04 into a table-driven loop.

Bank 04 contains 56 byte-identical 57-byte blocks, one per yaku, differing only
in six immediate values. Blocks 2..54 (53 of them, $8F51-$9B1D, exactly 3021
bytes with no gaps) become 82 bytes of loop plus six 53-byte parallel arrays,
freeing 2621 bytes -- which is what pays for full English yaku names.

Left alone deliberately:
  block 0/1 and the 33-byte interlock at $8EF7-$8F17, which encodes
    "double riichi and riichi are mutually exclusive"
  block 55 ($9B1E), whose not-taken branch lands 5 bytes past its own end
    instead of on the next block boundary
  the 6 irregular sites at $9B61 and $9D8F-$9DD3 (limit labels)

Safety, and what it is worth
----------------------------
This used to be a paragraph asserting that "every branch inside the replaced
span lands on a block boundary, and the only references into it from bank 04
or the fixed bank are three jumps to $8F51 itself". No code did any of it, and
the description was wrong: one of the three is a JMP and the other two are
conditional branches. check_safety() below now runs the analysis on every
build, and apply() refuses to write if it does not come out as recorded.

What it proves:

  * every branch inside the span lands on a block boundary. This is exact.
    extract() matches all 57 bytes of every block against TEMPLATE, so the
    instruction boundaries are known rather than guessed, exactly one opcode
    in that template is a branch, and its displacement is checked to equal
    BLOCK - the next block's first byte.
  * nothing in bank 04 or the fixed bank 0F reaches into the span except the
    three paths to $8F51, which survives as the loop's entry point. The scan
    reads every byte offset rather than decoding, so it over-reports rather
    than under-reports; each hit is accounted for by name in ENTRIES.

What it does NOT prove, and cannot: that no COMPUTED jump enters the span.
count_word_refs() measures the noise floor for that - the raw little-endian
word pairs in those two banks that would address the span. It is in the
hundreds and no static argument brings it to zero. That residual is the same
kind of uncertainty flagged for --use-dora-block, and it is flagged here now:
only a runtime CDL pass can close it, and none has been run.
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rom import (HDR, BANK_SIZE, PRINTER_BANK, SWITCHED as ORG, FIXED_BANK,
                 window, cpu2file, prg2cpu, bank_bytes)

FIRST    = 0x8F51        # entry point; 3 external jumps target this
LAST_END = 0x9B1E        # block 55 starts here and is left in place
NBLOCKS  = 53

# canonical block template; None marks a field that varies
TEMPLATE = [0xA4,0x0A, 0xA2,None, 0x8A, 0x0A, 0x0A, 0x18, 0x69,None, 0x85,0x08,
            0xA9,0x00, 0x69,0x6C, 0x85,0x09, 0xB1,0x08, 0x29,None, 0xF0,None,
            0xA9,None, 0x85,0x0D, 0xA9,None, 0x85,0x0E,
            0x20,0x7A,0x9F, 0x20,0xD2,0x9F,
            0xA9,None, 0x85,0x00, 0xA9,None, 0x85,0x01,
            0xA9,None, 0x85,0x02, 0xA9,None, 0x85,0x03, 0x20,0x58,0xA0]
BLOCK = len(TEMPLATE)                      # 57
F = {"X": 3, "base": 9, "mask": 21, "beq": 23, "ptrlo": 25, "ptrhi": 29,
     "p00": 39, "p01": 43, "p02": 47, "p03": 51}


CODE_LEN = 0x52          # assembled size of the loop, asserted in build_loop
BEQ_REL  = 0x21          # the template BEQ's displacement; checked, not assumed

BRANCHES = {0x10: "BPL", 0x30: "BMI", 0x50: "BVC", 0x70: "BVS",
            0x90: "BCC", 0xB0: "BCS", 0xD0: "BNE", 0xF0: "BEQ"}
JUMPS    = {0x4C: "JMP", 0x20: "JSR", 0x6C: "JMP()"}

# Banks that can be executing while bank 04 is mapped at $8000: bank 04 itself,
# and bank 0F, which is fixed at $C000. A $9xxx target in bank 02 addresses
# bank 02's own $9xxx, not this span, so the other banks are not in scope.
LIVE_BANKS = (0x04, 0x0F)

# Every hit scan_entries() produces, with the reason it is safe. The build
# fails on a hit that is not here, and on an entry here that the scan no
# longer produces -- so this table cannot quietly go stale.
ENTRIES = {
    (0x04, 0x8EF7, "JMP", 0x8F51):
        "the entry point. Three paths converge on $8F51 and the loop keeps it "
        "as its first instruction, so all three still work.",
    (0x04, 0x8F16, "BEQ", 0x8F51):
        "the double-riichi interlock at $8EF7-$8F17 skipping past block 1.",
    (0x04, 0x8F2E, "BEQ", 0x8F51):
        "block 1's own not-taken branch. Block 1 is $8F18-$8F50, so this is "
        "the template BEQ landing on the next block boundary, which is $8F51.",
    (0x0F, 0xDCD0, "JMP()", 0x906C):
        "NOT an instruction, and not a reference. The real code is "
        "$DCCB LDA $0B / $DCCD LDX $0A / $DCCF CMP $6C6C,X (DD 6C 6C) / "
        "$DCD2 BCC +3 / $DCD4 JMP $DD63. The scan is reading the CMP's "
        "operand and the BCC's opcode as an indirect jump. $6C6C is WRAM - "
        "the same yaku flag area every block indexes - so the decode is sound.",
}


def scan_entries(rom):
    """Every byte offset in LIVE_BANKS whose operand would reach into the
    replaced span from outside it. Returns sorted (bank, addr, kind, target).

    Deliberately not a disassembler: it tries every offset, so data that
    happens to look like a jump is reported too. Over-reporting is the safe
    direction here - a real entry cannot hide from it - and the false
    positives are cheap to account for once, in ENTRIES."""
    hits = []
    for bank in LIVE_BANKS:
        b = bank_bytes(rom, bank)
        org = window(bank)
        for i in range(len(b) - 2):
            a = org + i
            if FIRST <= a < LAST_END:
                continue                      # inside: covered by the template
            op = b[i]
            if op in JUMPS:
                t = b[i + 1] | (b[i + 2] << 8)
                if FIRST <= t < LAST_END:
                    hits.append((bank, a, JUMPS[op], t))
            elif op in BRANCHES:
                d = b[i + 1]
                t = a + 2 + (d - 256 if d > 127 else d)
                if FIRST <= t < LAST_END:
                    hits.append((bank, a, BRANCHES[op], t))
    return sorted(hits)


def count_word_refs(rom):
    """Raw little-endian word pairs in LIVE_BANKS that address the span.

    The noise floor for a computed entry - a jump through a pointer no static
    scan can follow. This number does not go to zero, and reporting it is the
    honest alternative to implying the span is provably unreachable."""
    n = 0
    for bank in LIVE_BANKS:
        b = bank_bytes(rom, bank)
        for i in range(len(b) - 1):
            if FIRST <= (b[i] | (b[i + 1] << 8)) < LAST_END:
                n += 1
    return n


def check_block_branches():
    """The 'every branch lands on a block boundary' half, proved for the whole
    span at once rather than scanned.

    extract() pins all 57 bytes of every block against TEMPLATE, so the
    instruction boundaries inside the span are known exactly. Given that, it
    is enough to show the template holds exactly one branch, that no varying
    byte can be an instruction, and that the branch reaches the next block."""
    at = [i for i, t in enumerate(TEMPLATE) if t in BRANCHES]
    if at != [22]:
        raise SystemExit("the block template holds %d branch opcodes, expected "
                         "exactly one at +22: %s" % (len(at), at))
    for i, t in enumerate(TEMPLATE):
        if t is None and (i == 0 or TEMPLATE[i - 1] is None):
            raise SystemExit("template byte +%d varies and is not the operand of "
                             "a fixed opcode, so its value could decode as an "
                             "instruction" % i)
    target = 22 + 2 + BEQ_REL
    if target != BLOCK:
        raise SystemExit("the block BEQ reaches +%d, not the next block "
                         "boundary at +%d" % (target, BLOCK))


def check_safety(rom, verbose=True):
    """Run the analysis the module docstring used to merely assert.
    Returns the computed-entry noise floor."""
    check_block_branches()
    found = set(scan_entries(rom))
    known = set(ENTRIES)
    for hit in sorted(found - known):
        raise SystemExit(
            "unaccounted path into $%04X-$%04X: $%04X in bank %02X is %s $%04X.\n"
            "  Decode it. If it is a real entry the refactor is unsafe; if it is "
            "data, add it to ENTRIES in tools/refactor.py with the decode that "
            "shows so." % (FIRST, LAST_END - 1, hit[1], hit[0], hit[2], hit[3]))
    for hit in sorted(known - found):
        raise SystemExit(
            "ENTRIES lists $%04X in bank %02X as %s $%04X, but the scan no longer "
            "finds it - the table is stale." % (hit[1], hit[0], hit[2], hit[3]))
    words = count_word_refs(rom)
    if verbose:
        real = sorted(h for h in found if "NOT an instruction" not in ENTRIES[h])
        print("  safety: every block branch lands on a block boundary; %d paths "
              "into the span, all to $%04X" % (len(real), FIRST))
        for bank, a, kind, t in real:
            print("            bank %02X  $%04X  %-5s $%04X" % (bank, a, kind, t))
        print("          %d raw word pairs in banks 04/0F address the span - the "
              "noise floor" % words)
        print("          for a computed entry, which no static scan can rule out.")
    return words


def table_addrs(n=NBLOCKS):
    """CPU addresses of the six parallel arrays, without building anything."""
    t_off = FIRST + CODE_LEN
    return {"off": t_off, "msk": t_off + n, "lo": t_off + 2 * n,
            "hi": t_off + 3 * n, "col": t_off + 4 * n, "han": t_off + 5 * n}


def is_refactored(rom):
    """True if this image carries the loop rather than the unrolled blocks."""
    o = cpu2file(FIRST)
    return rom[o:o + 2] == b"\xA2\x00" and rom[o + 2] == 0xBD


# ------------------------------------------------ call site -> table slot ----
# Which of the six arrays' 53 slots holds a given call site's string pointer.
#
# build.py and test_printer.py both used to answer this with "csv index ==
# block index, so slot = i - 2", written as a comment in one and repeated as
# code in the other. Nothing checked it. If it ever stopped holding, every
# yaku name would move to the wrong hand -- and because the test reproduced
# the same assumption to read the pointers back, it would have agreed with the
# broken build and reported success.
#
# It is not an assumption any more. slot_for_callsite() computes the slot from
# the call site's own recorded offset, and check_callsite_map() ties each row
# to its block through a value neither side computes -- the Japanese string
# pointer the block actually contains.

def slot_for_callsite(prg_lo_off, prg_hi_off):
    """Loop-table slot for the call site whose pointer immediates sit at these
    PRG offsets, or None if it is outside the replaced span and therefore keeps
    its pointer as an immediate.

    Pure geometry: it depends on the block layout, which extract() separately
    verifies against the ROM byte for byte, and not on row order."""
    a_lo = prg2cpu(prg_lo_off)
    a_hi = prg2cpu(prg_hi_off)
    if not FIRST <= a_lo < LAST_END:
        return None
    slot, within = divmod(a_lo - FIRST, BLOCK)
    if within != F["ptrlo"]:
        raise SystemExit(
            "call site pointer-low at PRG $%05X lands %d bytes into block %d, "
            "but the immediate lives at +%d. yaku-callsites.csv and the block "
            "layout disagree -- regenerate the csv with tools/extract.py."
            % (prg_lo_off, within, slot, F["ptrlo"]))
    if a_hi - a_lo != F["ptrhi"] - F["ptrlo"]:
        raise SystemExit(
            "call site at PRG $%05X has its pointer bytes %d apart; the block "
            "template puts them %d apart."
            % (prg_lo_off, a_hi - a_lo, F["ptrhi"] - F["ptrlo"]))
    return slot


def check_callsite_map(rom, sites):
    """Verify and return {csv index: slot} for the sites the loop owns.

    `rom` must be the unrefactored image and `sites` the parsed call-site
    table, each entry carrying "lo", "hi" and "ptr". Three things are checked:

      * every site inside the span sits exactly on a block's pointer field
      * those sites cover slots 0..52 once each -- no gaps, no collisions
      * the pointer the csv recorded for a site equals the pointer the block
        actually holds

    The last one is the one that matters. Geometry can be self-consistently
    wrong; this cannot, because all 53 pointers are distinct, so any mix-up of
    rows against blocks changes at least two of them."""
    blocks = extract(rom)
    slots = {}
    for i in sorted(sites):
        s = slot_for_callsite(sites[i]["lo"], sites[i]["hi"])
        if s is not None:
            slots[i] = s

    if sorted(slots.values()) != list(range(NBLOCKS)):
        missing = sorted(set(range(NBLOCKS)) - set(slots.values()))
        dupes = sorted({s for s in slots.values()
                        if list(slots.values()).count(s) > 1})
        raise SystemExit(
            "the %d call sites inside $%04X-$%04X do not cover slots 0..%d "
            "exactly (missing %s, duplicated %s)"
            % (len(slots), FIRST, LAST_END - 1, NBLOCKS - 1, missing, dupes))

    for i, s in sorted(slots.items()):
        want = blocks[s]["ptrlo"] | (blocks[s]["ptrhi"] << 8)
        if sites[i]["ptr"] != want:
            raise SystemExit(
                "call site row %d maps to slot %d, but that block points at "
                "$%04X while the csv says $%04X. The row-to-block mapping is "
                "wrong; repointing would put this name on another yaku."
                % (i, s, want, sites[i]["ptr"]))
    return slots


def extract(rom):
    """Pull the 53 blocks out of the ROM, checking every assumption."""
    b4 = bank_bytes(rom, PRINTER_BANK)
    out = []
    for n in range(NBLOCKS):
        a = FIRST + n * BLOCK
        o = a - ORG
        for k, t in enumerate(TEMPLATE):
            if t is not None and b4[o + k] != t:
                raise SystemExit("block %d at $%04X does not match the template "
                                 "at +%d (got $%02X, want $%02X)"
                                 % (n, a, k, b4[o + k], t))
        g = {k: b4[o + v] for k, v in F.items()}
        if g["base"] != 0x74 or g["p01"] != 0x01 or g["beq"] != BEQ_REL:
            raise SystemExit("block %d at $%04X has an unexpected constant "
                             "(base=$%02X p01=$%02X beq=$%02X)"
                             % (n, a, g["base"], g["p01"], g["beq"]))
        if g["p03"] not in (0x00, 0x04):
            raise SystemExit("block %d: p03=$%02X, expected $00 or $04" % (n, g["p03"]))
        if g["p02"] & 0x80:
            raise SystemExit("block %d: p02=$%02X has bit 7 set, cannot pack" % (n, g["p02"]))
        out.append(g)
    if FIRST + NBLOCKS * BLOCK != LAST_END:
        raise SystemExit("block span does not end exactly at $%04X" % LAST_END)
    return out


def build_loop(blocks):
    """Assemble the replacement. Returns (bytes, first_free_addr)."""
    n = len(blocks)
    code_len = 0x52                                    # 82, fixed
    t_off = FIRST + code_len
    t_msk = t_off + n
    t_lo  = t_msk + n
    t_hi  = t_lo + n
    t_col = t_hi + n
    t_han = t_col + n
    free  = t_han + n

    def abx(op, addr):                                 # LDA/AND absolute,X
        return bytes([op, addr & 0xFF, addr >> 8])

    code = bytearray()
    code += b"\xA2\x00"                     # LDX #$00
    loop = FIRST + len(code)
    code += abx(0xBD, t_off)                # LDA tbl_off,X
    code += b"\x85\x08"                     # STA $08
    code += b"\xA9\x6C"                     # LDA #$6C     (all offsets stay < $100)
    code += b"\x85\x09"                     # STA $09
    code += b"\xA4\x0A"                     # LDY $0A
    code += b"\xB1\x08"                     # LDA ($08),Y
    code += abx(0x3D, t_msk)                # AND tbl_msk,X
    beq_at = FIRST + len(code)
    code += b"\xF0\x00"                     # BEQ skip     (patched below)
    code += abx(0xBD, t_lo)                 # LDA tbl_lo,X
    code += b"\x85\x0D"                     # STA $0D
    code += abx(0xBD, t_hi)                 # LDA tbl_hi,X
    code += b"\x85\x0E"                     # STA $0E
    code += b"\x8A\x48"                     # TXA / PHA    (callees clobber X)
    code += b"\x20\x7A\x9F"                 # JSR $9F7A
    code += b"\x20\xD2\x9F"                 # JSR $9FD2
    code += b"\x68\xAA"                     # PLA / TAX
    code += abx(0xBD, t_col)                # LDA tbl_col,X
    code += b"\x85\x00"                     # STA $00
    code += b"\xA9\x01"                     # LDA #$01     (p01 is always 1)
    code += b"\x85\x01"                     # STA $01
    code += abx(0xBD, t_han)                # LDA tbl_han,X
    code += b"\x29\x7F"                     # AND #$7F
    code += b"\x85\x02"                     # STA $02
    code += abx(0xBD, t_han)                # LDA tbl_han,X
    code += b"\x29\x80"                     # AND #$80     (p03 packed in bit 7)
    code += b"\xF0\x02"                     # BEQ +2
    code += b"\xA9\x04"                     # LDA #$04
    code += b"\x85\x03"                     # STA $03
    code += b"\x8A\x48"                     # TXA / PHA
    code += b"\x20\x58\xA0"                 # JSR $A058
    code += b"\x68\xAA"                     # PLA / TAX
    skip = FIRST + len(code)
    code += b"\xE8"                         # INX
    code += bytes([0xE0, n])                # CPX #n
    back = (loop - (FIRST + len(code) + 2)) & 0xFF
    code += bytes([0xD0, back])             # BNE loop
    code += bytes([0x4C, LAST_END & 0xFF, LAST_END >> 8])   # JMP block55

    if len(code) != code_len:
        raise SystemExit("loop assembled to %d bytes, expected %d" % (len(code), code_len))
    code[beq_at - FIRST + 1] = (skip - (beq_at + 2)) & 0xFF

    tables = bytearray()
    tables += bytes(0x74 + g["X"] * 4 for g in blocks)                 # tbl_off
    tables += bytes(g["mask"] for g in blocks)                         # tbl_msk
    tables += bytes(g["ptrlo"] for g in blocks)                        # tbl_lo
    tables += bytes(g["ptrhi"] for g in blocks)                        # tbl_hi
    tables += bytes(g["p00"] for g in blocks)                          # tbl_col
    tables += bytes(g["p02"] | (0x80 if g["p03"] else 0) for g in blocks)
    assert len(tables) == 6 * n
    return bytes(code) + bytes(tables), free, (t_lo, t_hi)


def apply(dst, verbose=True):
    """Patch a mutable ROM image in place. Returns (free_lo, free_hi, ptr_tables)."""
    check_safety(bytes(dst), verbose)
    blocks = extract(bytes(dst))
    blob, free, ptr_tables = build_loop(blocks)
    o = cpu2file(FIRST)
    span = LAST_END - FIRST
    dst[o:o + span] = blob + b"\x00" * (span - len(blob))
    if verbose:
        print("  replaced $%04X-$%04X (%d bytes, %d blocks)"
              % (FIRST, LAST_END - 1, span, len(blocks)))
        print("  loop+tables occupy %d bytes; freed $%04X-$%04X = %d bytes"
              % (len(blob), free, LAST_END - 1, LAST_END - free))
    return free, LAST_END - 1, ptr_tables
