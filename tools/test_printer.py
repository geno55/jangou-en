#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Headless render test for the yaku printer $9F7A.

Boots nothing. Maps the ROM the way MMC1 does, sets up the zero page the way a
call site does, calls $9F7A directly, captures every $2006/$2007 write, then
reconstructs CHR-RAM and the nametable.

It then reads the glyphs back out of CHR-RAM and asserts, tile by tile, that
they are the exact bytes the ROM's own font holds for the characters the script
asked for. Geometry alone (one row, no gaps, on screen) is NOT enough: a name
rendered as eight wrong letters satisfies every geometric property a correct
one does. The `negative controls` section at the end proves this test notices,
by breaking the ROM on purpose and requiring the checks to fail.

The control render of the UNPATCHED ROM is checked the same way, against the
16x16 kanji sheet. Nothing here depends on a human looking at the PNG; the PNG
is output, not evidence.

Exits non-zero if anything fails.

    python tools/test_printer.py
"""
import sys, os, csv
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import utf8io          # noqa: F401  - stdout must take Japanese before we print
from cpu6502 import CPU, IllegalOpcode
import build
from build import ENC, SPACE_CODE
from PIL import Image, ImageDraw

import rom as cart
from rom import (ROOT, SRC_ROM, PATCHED_ROM as PATCHED, BUILD_DIR, FONT_BANK,
                 PRINTER_BANK as BANK04, BANK_SIZE, FIXED_BANK, SWITCHED, FIXED,
                 bank_off, fixed_off, cpu2file, prg)
OUT_PNG  = os.path.join(BUILD_DIR, "printer-test.png")
SENTINEL = 0x1234        # RTS lands here and we stop
ROW, SLOT = 0x04, 0x80   # the sweep's fixed row and CHR-RAM slot base


class Bus:
    """NES address space with MMC1. Bank 0F fixed at $C000, one switchable
    16K bank at $8000. Only what $9F7A's call chain actually touches."""

    def __init__(self, rom, start_bank=4):
        self.prg = prg(rom)
        self.ram = bytearray(0x800)
        self.wram = bytearray(0x2000)
        self.bank = start_bank
        self.shift = 0x10          # MMC1 serial shift register
        self.vram = bytearray(0x4000)
        self.vaddr = 0
        self.vlatch = False
        self.vinc = 1
        self.rbuf = 0              # PPUDATA read buffer
        self.writes = []           # (vram_addr, value)
        self.attr_writes = []      # writes landing in the attribute table
        self.pal_writes = []       # writes landing in palette RAM
        self.open_bus = 0

    # ---------------- CPU bus ----------------
    def read(self, a):
        a &= 0xFFFF
        if a < 0x2000:
            return self.ram[a & 0x7FF]
        if a < 0x4000:
            r = 0x2000 | (a & 7)
            if r == 0x2002:
                self.vlatch = False
                return 0x80        # vblank set: spin-waits exit immediately
            if r == 0x2007:
                # Buffered read. Reads below $3F00 return the PREVIOUS byte and
                # then refill, which is why real code does two LDA $2007 in a
                # row. $D742 relies on this to read-modify-write attribute
                # bytes; without it, every write wipes the neighbouring
                # quadrant and text comes out mixed-palette.
                va = self.vaddr & 0x3FFF
                if va >= 0x3F00:
                    val = self.vram[va]
                    self.rbuf = self.vram[va & 0x2FFF]
                else:
                    val = self.rbuf
                    self.rbuf = self.vram[va]
                self.vaddr = (self.vaddr + self.vinc) & 0x3FFF
                return val
            return self.open_bus
        if a < 0x4020:
            return 0
        if a < 0x6000:
            return self.open_bus
        if a < 0x8000:
            return self.wram[a - 0x6000]
        if a < 0xC000:
            return self.prg[self.bank * BANK_SIZE + (a - SWITCHED)]
        return self.prg[FIXED_BANK * BANK_SIZE + (a - FIXED)]

    def write(self, a, v):
        a &= 0xFFFF; v &= 0xFF
        if a < 0x2000:
            self.ram[a & 0x7FF] = v; return
        if a < 0x4000:
            r = 0x2000 | (a & 7)
            if r == 0x2000:
                self.vinc = 32 if (v & 0x04) else 1
            elif r == 0x2006:
                if not self.vlatch:
                    self.vaddr = ((v & 0x3F) << 8) | (self.vaddr & 0xFF)
                    self.vlatch = True
                else:
                    self.vaddr = (self.vaddr & 0xFF00) | v
                    self.vlatch = False
            elif r == 0x2007:
                va = self.vaddr & 0x3FFF
                self.vram[va] = v
                self.writes.append((va, v))
                if 0x23C0 <= va <= 0x23FF:
                    self.attr_writes.append((va, v))
                elif 0x3F00 <= va <= 0x3F1F:
                    self.pal_writes.append((va, v))
                self.vaddr = (self.vaddr + self.vinc) & 0x3FFF
            return
        if a < 0x4020:
            return
        if 0x6000 <= a < 0x8000:
            self.wram[a - 0x6000] = v; return
        if a >= 0x8000:                      # MMC1 serial port
            if v & 0x80:
                self.shift = 0x10
                return
            full = self.shift & 1
            self.shift = ((self.shift >> 1) | ((v & 1) << 4)) & 0x1F
            if full:
                val = self.shift
                self.shift = 0x10
                if a >= 0xE000:              # PRG bank select
                    self.bank = val & 0x0F
            return


def call(cpu, bus, addr, limit=4_000_000):
    """Call a subroutine and run until it returns to SENTINEL."""
    r = SENTINEL - 1
    cpu.push(r >> 8); cpu.push(r & 0xFF)
    cpu.pc = addr
    n = 0
    while cpu.pc != SENTINEL:
        cpu.step()
        n += 1
        if n > limit:
            raise SystemExit("$%04X did not return after %d instrs (pc=$%04X bank=%d)"
                             % (addr, n, cpu.pc, bus.bank))
    return n


def run_printer(rom, ptr, row=ROW, slot=SLOT, bank=4, limit=2_000_000,
                clear_first=False):
    bus = Bus(rom, bank)
    cpu = CPU(bus)
    # zero page as a call site leaves it
    bus.ram[0x4B] = bank          # current PRG bank, restored by $D55E
    bus.ram[0x0B] = row           # text row
    bus.ram[0x0C] = slot          # CHR-RAM slot base
    bus.ram[0x0D] = ptr & 0xFF    # string pointer
    bus.ram[0x0E] = ptr >> 8
    bus.ram[0x34] = 0x00          # shadow PPUCTRL
    # $0543 == 0 selects the direct-write path in $D55E / $D9DE
    bus.ram[0x543 & 0x7FF] = 0

    if clear_first:
        # $D4F3 blanks $2000-$23FF with $00. That range INCLUDES the attribute
        # table, so after a clear every 2x2 block is palette 0. This is the
        # real starting state the printer draws onto.
        call(cpu, bus, 0xD4F3)
        bus.writes.clear(); bus.attr_writes.clear()

    n = call(cpu, bus, 0x9F7A, limit)
    return bus, n


CHAIN_START = 0x8EBE     # block 0 of the whole yaku-list routine
FLAG_BASE   = 0x6C74     # yaku flag bitfield, $6C74 + group*4, indexed by $0A

def run_chain(rom, flags, stop, player=0, row=0x00, slot=0x40,
              limit=1_000_000, spin=()):
    """Drive the entire yaku-list routine, not one name.

    This lives here rather than in test_refactor.py because it is harness, not
    test: test_printer needs it to render a multi-yaku score screen and
    test_refactor needs it to compare two builds. `stop` is the pc to halt on
    and `spin` is a range of addresses that mean "waiting for an NMI we do not
    provide". Returns (cpu, bus, instrs, error) stopped exactly at `stop`, so
    the caller can keep stepping."""
    bus = Bus(rom, 4)
    cpu = CPU(bus)
    bus.ram[0x4B] = 4
    bus.ram[0x0A] = player
    bus.ram[0x0B] = row
    bus.ram[0x0C] = slot
    bus.ram[0x543 & 0x7FF] = 0
    bus.ram[0x34] = 0
    for grp, mask in flags:
        bus.wram[FLAG_BASE + grp * 4 + player - 0x6000] |= mask
    bus.wram[0x6001 - 0x6000] = 0xFF      # the riichi interlock reads these
    bus.wram[0x6002 - 0x6000] = 0xFF

    r = SENTINEL - 1
    cpu.push(r >> 8); cpu.push(r & 0xFF)
    cpu.pc = CHAIN_START
    # Stop only when execution ARRIVES at `stop`. A range test ("pc outside the
    # chain") is wrong: the chain JSRs to the printer at $9F7A, above the end,
    # and would stop on the first name printed.
    n = 0
    while cpu.pc != stop:
        cpu.step()
        n += 1
        if cpu.pc in spin:
            return None, None, n, "waits for an NMI at $%04X" % cpu.pc
        if n > limit:
            return None, None, n, "TIMEOUT at $%04X" % cpu.pc
    return cpu, bus, n, None


def attr_palette(bus, row, col):
    """Palette index the PPU will use for the tile at (row, col)."""
    a = bus.vram[0x23C0 + (row // 4) * 8 + (col // 4)]
    shift = ((row & 2) << 1) | (col & 2)     # 0,2,4,6
    return (a >> shift) & 3


# ------------------------------------------------- ground truth from the ROM --
# The glyph bytes below are read straight out of the ROM image, not out of the
# emulator. That is the whole point: the render is compared against an
# independently derived expectation, so the two can disagree.
#
#   $E62E (8x8)    ptr = $A000 + code*16, one 16-byte tile
#   $E6E7 (16x16)  base = $8000/$9000/$A000 by code range,
#                  ptr  = base + $E952[code]*16, then four tiles at
#                  +$000 +$010 +$100 +$110  (TL, TR, BL, BR)
#   $D55E copies 16 bytes to $2007. It writes five bits to $F000 first, so it
#   always reads through PRG bank 00 whatever bank the caller was in.
FONT_8X8  = 0xA000
KANJI_MAP = 0xE952        # bank 0F: LDA $E952,X -> sheet index for kanji X

def glyph8(rom, code):
    o = bank_off(FONT_BANK, FONT_8X8 + code * 16)
    return bytes(rom[o:o + 16])

def kanji_quads(rom, code):
    """The four 8x8 tiles $E6E7 uploads for one 16x16 kanji, in upload order."""
    base = 0xA000 if code >= 0x78 else 0x9000 if code >= 0x3E else 0x8000
    src  = base + rom[fixed_off(KANJI_MAP) + code] * 16
    return [bytes(rom[bank_off(FONT_BANK, src + d):bank_off(FONT_BANK, src + d) + 16])
            for d in (0x000, 0x010, 0x100, 0x110)]

def start_column(rom):
    """$9F90  LDA #$0A / STA $00 - the printer's first column. Read out of the
    ROM rather than hardcoded, so changing it fails the check that it changed
    rather than the 62 checks that did not."""
    o = bank_off(BANK04, 0x9F90)
    assert rom[o] == 0xA9, "expected LDA #imm at $9F90, found $%02X" % rom[o]
    return rom[o + 1]

DEC = {v: k for k, v in ENC.items()}
assert len(DEC) == len(ENC), "the encoder is not injective"

def stored_text(rom, ptr):
    """Decode the $00-terminated string the ROM actually holds at `ptr`.
    Returns (text, error)."""
    if not 0x8000 <= ptr <= 0xBFFF:
        return None, "pointer $%04X is outside bank 04's $8000-$BFFF window" % ptr
    out = []
    a = ptr
    while a <= 0xBFFF:
        b = rom[bank_off(BANK04, a)]
        if b == 0:
            return "".join(out), None
        if b not in DEC:
            return None, ("byte $%02X at $%04X is not a character the patched "
                          "printer can draw" % (b, a))
        out.append(DEC[b]); a += 1
    return None, "unterminated string at $%04X" % ptr

# ------------------------------------------- what is still Japanese on screen --
# The patch converts the yaku printer. It does NOT convert the rest of the
# score screen, and until someone actually looked, no document said so - which
# left "all 62 yaku names render in English" inviting the reader to think the
# screen was finished. It is not: the N符 M飜 line sits directly above the yaku
# list, so the shipped result is mixed-script.
#
# These sites are outside the replaced span and were never in scope. They are
# recorded here, and checked on every run, so the state of the screen is a fact
# the tests assert rather than something a reader has to discover.
KANJI_UPLOAD = b"\x20\xca\x80"        # JSR $80CA -> $C027, the 16x16 glyph loader

MIXED_SCRIPT = {
    # the score line, directly above the now-English yaku list
    0x9D00: ("N符 M飜 line", "fu value, tens digit"),
    0x9D22: ("N符 M飜 line", "fu value, ones digit"),
    0x9D3C: ("N符 M飜 line", "符  (kanji $72)"),
    0x9D5E: ("N符 M飜 line", "han value digit"),
    0x9D78: ("N符 M飜 line", "飜  (kanji $5E)"),
    # the round indicator
    0x82A3: ("round indicator", "round wind, $6013 + $56"),
    0x82C6: ("round indicator", "hand number digit"),
    0x82DC: ("round indicator", "局  (kanji $5B)"),
    # han-minimum rule, drawn only when $6025 >= 5
    0x82FD: ("二飜縛り rule", "二  (kanji $02)"),
    0x8313: ("二飜縛り rule", "飜  (kanji $5E)"),
    0x8329: ("二飜縛り rule", "縛  (kanji $84)"),
    0x833F: ("二飜縛り rule", "り  (kanji $85)"),
    # not text
    0xA01E: ("not text", "kanji code $00, the blank tile"),
}

def surviving_kanji(rom):
    """Every 16x16 kanji upload left in bank 04, with the code it draws when
    that is an immediate rather than computed."""
    b = cart.bank_bytes(rom, BANK04)
    out = []
    for i in range(len(b) - 2):
        if b[i:i + 3] == KANJI_UPLOAD:
            code = None
            for j in range(max(0, i - 14), max(0, i - 3)):
                if b[j] == 0xA9 and b[j + 2] == 0x85 and b[j + 3] == 0x02:
                    code = b[j + 1]
            out.append((cart.SWITCHED + i, code))
    return out


def check_mixed_script(rom):
    """The recorded inventory must match the ROM. If a later patch converts one
    of these, this fails and the documentation gets updated with it."""
    found = {a for a, _ in surviving_kanji(rom)}
    bad = []
    for a in sorted(found - set(MIXED_SCRIPT)):
        bad.append("$%04X still uploads a 16x16 kanji and is not in "
                   "MIXED_SCRIPT - the screen changed, update it and the docs" % a)
    for a in sorted(set(MIXED_SCRIPT) - found):
        bad.append("MIXED_SCRIPT lists $%04X but it no longer uploads a kanji - "
                   "if that is deliberate, say so in README and BUILD.md" % a)
    return bad


FONT_LAST = 0xEF          # jangou.tbl's own header: the 8x8 page is $50-$EF

def check_charset(rom):
    """jangou.tbl is the single source for build.py's encoder, so it is worth
    asking the ROM whether it is telling the truth. Returns a list of problems.

    What a machine can check: every code lands inside the 8x8 page, and every
    character the table claims has an actual glyph behind it. What it cannot
    check is whether that glyph is the character named - identifying 'A' as A
    needs an eye, which is why the table is checked in as source rather than
    generated. Two codes labelled with the same character would be caught here
    too, but build.py refuses to load such a table at all."""
    bad = []
    for ch, code in sorted(ENC.items()):
        if code == SPACE_CODE:
            continue          # engine convention, deliberately not a font code
        if not 0x50 <= code <= FONT_LAST:
            bad.append("%r is $%02X, outside the 8x8 page $50-$%02X"
                       % (ch, code, FONT_LAST))
        elif not any(glyph8(rom, code)[8:]):
            bad.append("%r ($%02X) has a blank glyph, and the 8x8 page has no "
                       "blank tile - so this is probably not a character" % (ch, code))
    return bad

def codebook(rom):
    """bitmap -> character, for reading a render back as text.

    This ROM's font draws 'O' and '0' with byte-identical tiles, so the
    read-back cannot tell those two apart and prefers the letter. That is a
    property of the font, not a fault: the per-character check below compares
    against ENC[ch] directly and is exact either way."""
    book, same = {}, []
    for ch, code in sorted(ENC.items(), key=lambda kv: (not kv[0].isalpha(), kv[0])):
        if code == SPACE_CODE:
            continue
        g = glyph8(rom, code)
        if g in book:
            same.append("%r and %r are the same 8x8 tile in this ROM" % (book[g], ch))
        else:
            book[g] = ch
    return book, same

def decode_row(rom, bus, row, col0, book):
    """Read the drawn row back out of CHR-RAM as text. '?' is a tile whose
    bitmap is not any character in the font."""
    cols = [c for c in range(32) if bus.vram[0x2000 + row * 32 + c]]
    if not cols:
        return ""
    out = []
    for c in range(min(col0, min(cols)), max(cols) + 1):
        t = bus.vram[0x2000 + row * 32 + c]
        if not t:
            out.append(" "); continue
        o = (t * 16) & 0x1FFF
        out.append(book.get(bytes(bus.vram[o:o + 16]), "?"))
    return "".join(out)


# ------------------------------------------------------------------ checks ---
def drawn_cells(bus):
    return {(r, c): bus.vram[0x2000 + r * 32 + c]
            for r in range(30) for c in range(32)
            if bus.vram[0x2000 + r * 32 + c]}

def cmp_cells(got, want):
    bad = []
    for cell in sorted(set(got) | set(want)):
        g, w = got.get(cell), want.get(cell)
        if g != w:
            bad.append("row %d col %d holds %s, expected %s"
                       % (cell[0], cell[1],
                          "tile $%02X" % g if g else "nothing",
                          "tile $%02X" % w if w else "nothing"))
    return bad[:6]

def check_patched_render(rom, bus, text, row, col0, slot0):
    """Every assertion the patched render has to satisfy: the right tiles in
    the right cells, each holding the right glyph, in palette 3."""
    want = {(row, col0 + i): slot0 + i
            for i, ch in enumerate(text) if ch != " "}
    bad = cmp_cells(drawn_cells(bus), want)
    for i, ch in enumerate(text):
        if ch == " ":                 # SPACE_CODE advances, draws nothing
            continue
        slot = slot0 + i
        o = (slot * 16) & 0x1FFF
        if bytes(bus.vram[o:o + 16]) != glyph8(rom, ENC[ch]):
            bad.append("CHR slot $%02X does not hold the glyph for %r" % (slot, ch))
        p = attr_palette(bus, row, col0 + i)
        if p != 3:
            bad.append("col %d renders in palette %d, expected 3" % (col0 + i, p))
    return bad

def check_kanji_render(rom, bus, codes, row, col0, slot0):
    """Same, for the unpatched ROM: each kanji is a 2x2 block of four
    consecutive slots holding the four quadrants of its sheet entry."""
    want = {}
    for i, code in enumerate(codes):
        c, s = col0 + 2 * i, slot0 + 4 * i
        want[(row, c)]         = s          # TL
        want[(row, c + 1)]     = s + 1      # TR
        want[(row + 1, c)]     = s + 2      # BL
        want[(row + 1, c + 1)] = s + 3      # BR
    bad = cmp_cells(drawn_cells(bus), want)
    for i, code in enumerate(codes):
        for k, q in enumerate(kanji_quads(rom, code)):
            o = ((slot0 + 4 * i + k) * 16) & 0x1FFF
            if bytes(bus.vram[o:o + 16]) != q:
                bad.append("kanji $%02X quadrant %d is not the ROM's tile" % (code, k))
    return bad


def sweep_patched(rom, script, col0, book, indices=range(62)):
    """Render every call site and check it end to end: the pointer resolves to
    the script's text, and the text reaches CHR-RAM as the right glyphs."""
    results = []
    for idx in indices:
        ptr, jp = pointer_for(rom, idx)
        want = script.get(idx, "")
        stored, err = stored_text(rom, ptr)
        if err:
            results.append((idx, jp, "", "", [err])); continue
        blank = (stored == "")
        bad = []
        if not blank and stored != want:
            bad.append("ROM holds %r, script says %r" % (stored, want))
        try:
            bus, _ = run_printer(rom, ptr, row=ROW, slot=SLOT, clear_first=True)
        except (IllegalOpcode, SystemExit) as e:
            results.append((idx, jp, stored, "", bad + ["printer did not return: %s" % e]))
            continue
        bad += check_patched_render(rom, bus, stored, ROW, col0, SLOT)
        got = decode_row(rom, bus, ROW, col0, book)
        results.append((idx, jp, stored, got, bad))
    return results

def sweep_original(rom, sites, col0, indices=range(62)):
    results = []
    for idx in indices:
        ptr, jp = pointer_for(rom, idx)
        codes = sites[idx]["jp_bytes"][:-1]         # drop the $00 terminator
        bad = []
        o = bank_off(BANK04, ptr)
        if bytes(rom[o:o + len(codes) + 1]) != bytes(codes) + b"\x00":
            bad.append("$%04X does not hold this call site's Japanese string" % ptr)
        try:
            bus, _ = run_printer(rom, ptr, row=ROW, slot=SLOT, clear_first=True)
        except (IllegalOpcode, SystemExit) as e:
            results.append((idx, jp, codes, None, bad + ["printer did not return: %s" % e]))
            continue
        bad += check_kanji_render(rom, bus, codes, ROW, col0, SLOT)
        results.append((idx, jp, codes, bus, bad))
    return results


# ------------------------------------------------------------------ render --
PAL = [(248, 248, 248), (176, 176, 176), (96, 96, 96), (0, 0, 0)]

def tile_img(vram, idx):
    img = Image.new("RGB", (8, 8))
    px = img.load()
    base = (idx * 16) & 0x1FFF
    for y in range(8):
        lo, hi = vram[base + y], vram[base + 8 + y]
        for x in range(8):
            b = 7 - x
            px[x, y] = PAL[((lo >> b) & 1) | (((hi >> b) & 1) << 1)]
    return img

def render(bus, cols=(8, 30), rows=(2, 10), zoom=4):
    """Draw the nametable window as the PPU would."""
    c0, c1 = cols; r0, r1 = rows
    w, h = (c1 - c0) * 8, (r1 - r0) * 8
    img = Image.new("RGB", (w, h), (248, 248, 248))
    for r in range(r0, r1):
        for c in range(c0, c1):
            idx = bus.vram[0x2000 + r * 32 + c]
            if idx:
                img.paste(tile_img(bus.vram, idx), ((c - c0) * 8, (r - r0) * 8))
    return img.resize((w * zoom, h * zoom), Image.NEAREST)

def nametable_text(bus):
    out = []
    for r in range(0, 16):
        row = [bus.vram[0x2000 + r * 32 + c] for c in range(32)]
        if any(row):
            out.append("    row %2d: %s" % (r, " ".join("%02X" % v if v else ".." for v in row)))
    return out


# -------------------------------------------------------------------- main --
_SITES = None

def sites():
    global _SITES
    if _SITES is None:
        _SITES = build.load_callsites()
    return _SITES


def pointer_for(rom, index):
    """Read a call site's live pointer straight out of the ROM image.

    After the call-site refactor the collapsed blocks no longer hold their
    pointer as an immediate - it lives in the tbl_lo/tbl_hi arrays, and the old
    immediate bytes are gone. Reading the stale location would report those
    yaku as blank when they are in fact fine.

    The slot comes from refactor.slot_for_callsite(), the same derivation
    build.py writes through. That shared function is deliberately not the only
    thing standing behind the mapping: main() also runs
    refactor.check_callsite_map() against the unpatched ROM, which pins each
    row to its block by the pointer the block actually holds. Without that, a
    wrong mapping would be written and read back through the same code and the
    sweep below would confirm a build that renames every yaku."""
    import refactor
    s = sites()[index]
    slot = refactor.slot_for_callsite(s["lo"], s["hi"]) if refactor.is_refactored(rom) else None
    if slot is not None:
        t = refactor.table_addrs()
        lo = rom[refactor.cpu2file(t["lo"] + slot)]
        hi = rom[refactor.cpu2file(t["hi"] + slot)]
    else:
        lo = rom[16 + s["lo"]]
        hi = rom[16 + s["hi"]]
    return lo | (hi << 8), s["jp"]


def load_script_map():
    return {r["idx"]: r["en"] for r in build.load_script()}


def negative_controls(rom, script, col0, book):
    """Break the ROM on purpose and require the checks above to notice.

    A test that cannot fail proves nothing, and geometry-only checks really do
    pass on a ROM whose every letter is wrong - that is why these exist."""
    out = []

    def one(label, mutate, idx=0):
        bad = bytearray(rom)
        mutate(bad)
        r = sweep_patched(bytes(bad), script, col0, book, [idx])[0]
        out.append((label, bool(r[4]), (r[4] or ["(nothing)"])[0]))

    ptr0, _ = pointer_for(rom, 0)
    n0 = len(script.get(0, ""))
    off0 = bank_off(BANK04, ptr0)
    prn = bank_off(BANK04, build.PRINTER_ADDR)

    def one_letter(b):                     # a single byte, same length, same shape
        b[off0 + n0 // 2] = ENC["Q"] if b[off0 + n0 // 2] != ENC["Q"] else ENC["X"]
    def scramble(b):                       # every letter replaced by Z
        b[off0:off0 + n0] = bytes([ENC["Z"]]) * n0
    def truncate(b):                       # name one character short
        b[off0 + n0 - 1] = 0x00
    def move_pointer(b):                   # site 0 aimed at site 1's string
        p1, _ = pointer_for(rom, 1)
        r = list(csv.DictReader(open(os.path.join(ROOT, "yaku-callsites.csv"),
                                     encoding="utf-8-sig")))[0]
        b[16 + int(r["patch_lo_prg_off"].lstrip("$"), 16)] = p1 & 0xFF
        b[16 + int(r["patch_hi_prg_off"].lstrip("$"), 16)] = p1 >> 8
    def no_upload(b):                      # drop JSR $80C6, the glyph upload
        o = prn + build.PRINTER_NEW.index(b"\x20\xc6\x80")
        b[o:o + 3] = b"\xea\xea\xea"
    def no_slot_advance(b):                # drop INC $0C, so every letter reuses $80
        o = prn + build.PRINTER_NEW.index(b"\xe6\x0c")
        b[o:o + 2] = b"\xea\xea"

    # The first four break the data, the last two break the engine while
    # leaving the geometry perfect - one row, contiguous columns, on screen,
    # palette 3. Those are the cases a geometry-only sweep waves through.
    one("one letter changed",                one_letter)
    one("every letter overwritten with Z",   scramble)
    one("name truncated by one character",   truncate)
    one("pointer aimed at the next name",    move_pointer)
    one("glyph upload NOPed out of $9F7A",   no_upload)
    one("slot advance NOPed out of $9F7A",   no_slot_advance)
    return out


def map_negative_controls(src):
    """Break the row-to-slot mapping five ways; check_callsite_map must catch
    all five. The first is the one that used to be undetectable: offsets and
    row order stay perfectly consistent, only the association is wrong."""
    import refactor
    out = []

    def one(label, mutate):
        s = {i: dict(v) for i, v in sites().items()}
        mutate(s)
        try:
            refactor.check_callsite_map(src, s)
            out.append((label, False, "(nothing)"))
        except SystemExit as e:
            out.append((label, True, str(e).splitlines()[0]))

    B = refactor.BLOCK
    one("two rows pointer-swapped",
        lambda s: s[2].update(ptr=s[3]["ptr"]) or s[3].update(ptr=sites()[2]["ptr"]))
    one("one row shifted a block along",
        lambda s: s[10].update(lo=s[10]["lo"] + B, hi=s[10]["hi"] + B))
    one("offsets one byte off the field",
        lambda s: s[20].update(lo=s[20]["lo"] + 1, hi=s[20]["hi"] + 1))
    one("a row moved outside the span",
        lambda s: s[30].update(lo=0x10000, hi=0x10004))
    one("lo/hi pair spacing wrong",
        lambda s: s[40].update(hi=s[40]["hi"] + 1))
    return out


def main():
    failures = []
    def fail(where, msgs):
        for m in msgs:
            failures.append("%s: %s" % (where, m))

    src = open(SRC_ROM, "rb").read()
    if not os.path.exists(PATCHED):
        raise SystemExit("missing %s - run tools/build.py first" % PATCHED)
    dst = open(PATCHED, "rb").read()

    col0 = start_column(dst)
    if start_column(src) != col0:
        fail("harness", ["the two ROMs start at different columns"])
    script = load_script_map()
    book, same = codebook(dst)
    fail("charset", check_charset(dst))
    cases = []            # (label, rendered image) - see the contact sheet below

    # ---- control: the unpatched ROM must draw the original kanji ----
    print("=== control: UNPATCHED ROM must draw the original 16x16 kanji ===")
    print("  Every kanji is compared against the sheet in PRG bank 00 that")
    print("  $E6E7 reads - four tiles per character, checked byte for byte.")
    ctl = sweep_original(src, build.load_callsites(), col0)
    for idx, jp, codes, bus, bad in ctl:
        if bad:
            fail("control idx %d %s" % (idx, jp), bad)
        if idx in (1, 4, 36) and bus is not None:
            print("  idx %2d  %-10s %d kanji, %2d cols, slots $%02X-$%02X  %s"
                  % (idx, jp, len(codes), 2 * len(codes), SLOT,
                     SLOT + 4 * len(codes) - 1, "OK" if not bad else "FAIL"))
            cases.append(("original: " + jp, render(bus)))
    ok = sum(1 for r in ctl if not r[4])
    print("  %d of %d control renders match the ROM's own kanji data" % (ok, len(ctl)))

    # ---- the patched ROM, every call site, glyph by glyph ----
    print("\n=== call site -> loop slot map ===")
    try:
        import refactor
        slots = refactor.check_callsite_map(src, sites())
        print("  %d rows map onto slots 0-%d, one each, and every row's csv"
              % (len(slots), len(slots) - 1))
        print("  pointer matches the pointer its block actually holds.")
        print("  Checked against the UNPATCHED ROM, so it does not share an")
        print("  assumption with the repointing it is verifying.")
        for label, caught, first in map_negative_controls(src):
            print("    %-32s %s" % (label, "caught" if caught else "MISSED"))
            if not caught:
                fail("callsite map", ["%s went undetected" % label])
    except SystemExit as e:
        fail("callsite map", [str(e)])

    print("\n=== still Japanese on screen: the patch does not finish the screen ===")
    fail("mixed script", check_mixed_script(dst))
    groups = {}
    for a, _ in sorted(surviving_kanji(dst)):
        where, what = MIXED_SCRIPT[a]
        groups.setdefault(where, []).append((a, what))
    for where in ("N符 M飜 line", "round indicator", "二飜縛り rule", "not text"):
        items = groups.get(where, [])
        print("  %-16s %d site%s" % (where, len(items), "" if len(items) == 1 else "s"))
        for a, what in items:
            print("      $%04X  %s" % (a, what))
    print("  The N符 M飜 line renders directly above the yaku list, so a scored")
    print("  hand shows English names over a Japanese fu/han line. Out of scope")
    print("  for this patch, in scope for the next one - see BUILD.md.")

    print("\n=== charset: jangou.tbl against the ROM's font ===")
    print("  %d characters, every code inside the 8x8 page $50-$%02X, every one"
          % (len(ENC) - 1, FONT_LAST))
    print("  with a real glyph behind it. build.py builds its encoder from this")
    print("  file - there is no second copy to drift.")

    print("\n=== PATCHED ROM: all 62 call sites, checked glyph by glyph ===")
    res = sweep_patched(dst, script, col0, book)
    blank, no_line = [], []
    for idx, jp, stored, got, bad in res:
        if bad:
            fail("idx %d %s" % (idx, jp), bad)
        if stored == "":
            # Two different problems. "Had a line, did not fit" is a space
            # issue; "has no line at all" means somebody edited the script and
            # lost one, which used to pass every check in this repository.
            (blank if idx in script else no_line).append((idx, jp))
        if idx in (1, 4, 36):
            b, _ = run_printer(dst, pointer_for(dst, idx)[0], clear_first=False)
            cases.append(("patched: " + jp, render(b)))
    good = [r for r in res if not r[4] and r[2]]
    print("  %d of %d names render exactly the text in script/yaku-en.txt"
          % (len(good), len(res)))
    print("  read back out of CHR-RAM through the inverse of the encoder:")
    for idx, jp, stored, got, bad in res[:6]:
        print("    idx %2d  %-10s -> %-14r %s" % (idx, jp, got, "OK" if not bad else "FAIL"))
    print("    ... and %d more" % (len(res) - 6))
    for s in same:
        print("  note: %s, so the read-back above cannot distinguish them" % s)
    if blank:
        fail("build", ["%d names are blank in this ROM (%s) - they have script "
                       "entries but did not fit. Build with --refactor."
                       % (len(blank), ", ".join(j for _, j in blank))])
    if no_line:
        n = len(no_line)
        fail("script", ["%s no line in script/yaku-en.txt (index %s) and print%s "
                        "nothing at all. A deleted line looks exactly like this."
                        % ("1 call site has" if n == 1 else "%d call sites have" % n,
                           ", ".join(str(i) for i, _ in no_line),
                           "s" if n == 1 else "")])

    drawn = [r for r in res if r[2]]
    worst_col = max((col0 + len(r[2]) - 1) for r in drawn) + 1 if drawn else 0
    worst_slot = max((SLOT + len(r[2]) - 1) for r in drawn) if drawn else 0
    print("  rightmost column used : %d  (nametable is 32 wide)" % worst_col)
    print("  highest CHR-RAM slot  : $%02X (base $%02X, must stay under $100)"
          % (worst_slot, SLOT))
    if worst_col > 32:
        fail("layout", ["a name runs off the right edge of the nametable"])
    if worst_slot > 0xFF:
        fail("layout", ["CHR-RAM slots overflow past $FF"])

    # ---- prove the checks above can fail ----
    print("\n=== negative controls: deliberately broken ROMs must FAIL ===")
    for label, caught, first in negative_controls(dst, script, col0, book):
        print("  %-34s %s  %s" % (label, "caught" if caught else "MISSED", first))
        if not caught:
            fail("negative control", ["%s went undetected" % label])

    # ---- palette / attribute comparison ----
    print("\n=== palette: what the PPU will use for the yaku text ===")
    print("  $D4F3 clears $2000-$23FF (attribute table included, so everything")
    print("  starts at palette 0), then the printer draws.\n")
    for label, rom, idx in (("original", src, 1), ("patched ", dst, 1)):
        ptr, jp = pointer_for(rom, idx)
        bus, _ = run_printer(rom, ptr, row=ROW, clear_first=True)
        aw = bus.attr_writes
        cells = [c for c in range(32) if bus.vram[0x2000 + ROW * 32 + c]]
        pals = sorted({attr_palette(bus, ROW, c) for c in cells}) if cells else []
        print("  %s  %-6s  %3d attribute writes  -> text renders in palette %s"
              % (label, jp, len(aw), pals if pals else "n/a"))
        seen = {}
        for a, v in aw:
            seen[a] = v
        for a in sorted(seen):
            print("        $%04X = $%02X   (quadrants: %s)"
                  % (a, seen[a], [(seen[a] >> s) & 3 for s in (0, 2, 4, 6)]))
    print("\n  Palette RAM ($3F00-$3F1F) is uploaded by $C9A2 from zero page")
    print("  $10-$1F, which this call chain never touches - so the RGB values")
    print("  come from screen-setup code elsewhere and are not decided here.")
    print("  What IS decided here is the palette INDEX, and that is checked.")

    # ---- the multi-yaku list, which the single-name sweep cannot show ----
    print("\n=== a scored hand: five yaku at once ===")
    print("  The sweep above prints one name at a time, so it cannot show what")
    print("  the list looks like. This drives the whole routine from $8EBE.")
    import refactor as _r
    pool = [(g["X"], g["mask"]) for g in _r.extract(src)][:5]
    for label, rom in (("original", src), ("patched ", dst)):
        cpu, bus, n, err = run_chain(rom, pool, stop=0x9B1E)
        if err:
            fail("score screen", ["%s: %s" % (label, err)]); continue
        rows = [r for r in range(24)
                if any(bus.vram[0x2000 + r * 32 + c] for c in range(32))]
        print("  %s  rows drawn: %s" % (label, ", ".join(str(r) for r in rows)))
        cases.append(("5 yaku, " + label.strip(), render(bus, rows=(0, 12))))

    print("\n  The original draws 16x16 names two rows tall on a two-row pitch,")
    print("  so rows 0-9 are solid. The patch makes the glyphs one row tall and")
    print("  leaves the pitch alone ($9FBB is untouched), so the English list is")
    print("  DOUBLE-SPACED: names on 0,2,4,6,8 with a blank row between each.")
    print("  Nobody had looked at this. It is legible and it occupies exactly")
    print("  the rows the original did - but it was never a decision, and the")
    print("  one-line change is in BUILD.md.")

    print("\n=== nametable, patched idx 1 ===")
    b, _ = run_printer(dst, pointer_for(dst, 1)[0], clear_first=True)
    for line in nametable_text(b):
        print(line)

    # contact sheet - output, not evidence
    tiles = cases
    W = max(i.width for _, i in tiles)
    H = sum(i.height + 22 for _, i in tiles) + 10
    sheet = Image.new("RGB", (W + 16, H), (28, 28, 40))
    dr = ImageDraw.Draw(sheet)
    y = 6
    for lbl, im in tiles:
        dr.text((8, y), lbl, fill=(255, 225, 120))
        sheet.paste(im, (8, y + 14))
        y += im.height + 22
    os.makedirs(os.path.dirname(OUT_PNG), exist_ok=True)
    sheet.save(OUT_PNG)
    print("\nwrote %s  (a picture of the result, not the proof)" % OUT_PNG)

    if failures:
        print("\nFAILED (%d)" % len(failures))
        for f in failures[:40]:
            print("  " + f)
        if len(failures) > 40:
            print("  ... and %d more" % (len(failures) - 40))
        return 1
    print("\nPASS: 62 control kanji renders and 62 English renders match the ROM,"
          "\n      and every deliberately broken ROM was caught.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
