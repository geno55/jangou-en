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

Verified before writing: every branch inside the replaced span lands on a block
boundary, and the only references into it from bank 04 or the fixed bank are
three jumps to $8F51 itself, which stays the entry point.
"""

ORG      = 0x8000
BANK04   = 0x10000
HDR      = 16
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


def cpu2file(a):
    return HDR + BANK04 + (a - ORG)


def table_addrs(n=NBLOCKS):
    """CPU addresses of the six parallel arrays, without building anything."""
    t_off = FIRST + CODE_LEN
    return {"off": t_off, "msk": t_off + n, "lo": t_off + 2 * n,
            "hi": t_off + 3 * n, "col": t_off + 4 * n, "han": t_off + 5 * n}


def is_refactored(rom):
    """True if this image carries the loop rather than the unrolled blocks."""
    o = cpu2file(FIRST)
    return rom[o:o + 2] == b"\xA2\x00" and rom[o + 2] == 0xBD


def extract(rom):
    """Pull the 53 blocks out of the ROM, checking every assumption."""
    b4 = rom[HDR + BANK04: HDR + BANK04 + 0x4000]
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
        if g["base"] != 0x74 or g["p01"] != 0x01 or g["beq"] != 0x21:
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
