#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Headless render test for the yaku printer $9F7A.

Boots nothing. Maps the ROM the way MMC1 does, sets up the zero page the way a
call site does, calls $9F7A directly, captures every $2006/$2007 write, then
reconstructs CHR-RAM and the nametable and draws the result to a PNG.

Self-validating: it first renders the UNPATCHED ROM, which must produce the
original 16x16 kanji. If that comes out right the harness is trustworthy, and
the patched render means something.

    python tools/test_printer.py
"""
import sys, os, csv
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cpu6502 import CPU, IllegalOpcode
from PIL import Image, ImageDraw

ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_ROM  = os.path.join(ROOT, "Jangou (Japan).nes")
PATCHED  = os.path.join(ROOT, "build", "jangou-en.nes")
OUT_PNG  = os.path.join(ROOT, "build", "printer-test.png")
SENTINEL = 0x1234        # RTS lands here and we stop


class Bus:
    """NES address space with MMC1. Bank 0F fixed at $C000, one switchable
    16K bank at $8000. Only what $9F7A's call chain actually touches."""

    def __init__(self, rom, start_bank=4):
        self.prg = rom[16:]
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
            return self.prg[self.bank * 0x4000 + (a - 0x8000)]
        return self.prg[0x0F * 0x4000 + (a - 0xC000)]

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


def run_printer(rom, ptr, row=0x04, slot=0x80, bank=4, limit=2_000_000,
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


def attr_palette(bus, row, col):
    """Palette index the PPU will use for the tile at (row, col)."""
    a = bus.vram[0x23C0 + (row // 4) * 8 + (col // 4)]
    shift = ((row & 2) << 1) | (col & 2)     # 0,2,4,6
    return (a >> shift) & 3


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
def pointer_for(rom, index):
    """Read a call site's live pointer straight out of the ROM image.

    After the call-site refactor, blocks 2..54 no longer hold their pointer as
    an immediate - it lives in the tbl_lo/tbl_hi arrays, and the old immediate
    bytes are gone. Reading the stale location would report those yaku as
    blank when they are in fact fine."""
    import refactor
    with open(os.path.join(ROOT, "yaku-callsites.csv"), encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    r = rows[index]
    if refactor.is_refactored(rom) and 2 <= index <= 54:
        t = refactor.table_addrs()
        lo = rom[refactor.cpu2file(t["lo"] + index - 2)]
        hi = rom[refactor.cpu2file(t["hi"] + index - 2)]
    else:
        lo = rom[16 + int(r["patch_lo_prg_off"].lstrip("$"), 16)]
        hi = rom[16 + int(r["patch_hi_prg_off"].lstrip("$"), 16)]
    return lo | (hi << 8), r["japanese"]


def main():
    src = open(SRC_ROM, "rb").read()
    cases = []

    print("=== control: UNPATCHED ROM (must draw 16x16 kanji) ===")
    for idx in (1, 4, 36):
        ptr, jp = pointer_for(src, idx)
        bus, n = run_printer(src, ptr)
        wr = len(bus.writes)
        print("  idx %2d  %-10s ptr $%04X  %6d instrs  %4d PPU writes" % (idx, jp, ptr, n, wr))
        cases.append(("original: " + jp, bus))
        if idx == 1:
            for line in nametable_text(bus): print(line)

    if os.path.exists(PATCHED):
        dst = open(PATCHED, "rb").read()
        print("\n=== PATCHED ROM (must draw 8x8 Latin) ===")
        for idx in (1, 4, 36):
            ptr, jp = pointer_for(dst, idx)
            bus, n = run_printer(dst, ptr)
            print("  idx %2d  %-10s ptr $%04X  %6d instrs  %4d PPU writes"
                  % (idx, jp, ptr, n, len(bus.writes)))
            cases.append(("patched: " + jp, bus))
            if idx == 1:
                for line in nametable_text(bus): print(line)
    else:
        print("\n(no build/jangou-en.nes - run tools/build.py first)")

    # ---- palette / attribute comparison ----
    if os.path.exists(PATCHED):
        dst = open(PATCHED, "rb").read()
        print("\n=== palette: what the PPU will use for the yaku text ===")
        print("  Simulating the real sequence: $D4F3 clears $2000-$23FF (which")
        print("  includes the attribute table, so everything starts at palette 0),")
        print("  then the printer draws.\n")
        ROW = 0x04
        for label, rom, idx in (("original", src, 1), ("patched ", dst, 1)):
            ptr, jp = pointer_for(rom, idx)
            bus, _ = run_printer(rom, ptr, row=ROW, clear_first=True)
            aw = bus.attr_writes
            cells = [c for c in range(32) if bus.vram[0x2000 + ROW * 32 + c]]
            pals = sorted({attr_palette(bus, ROW, c) for c in cells}) if cells else []
            print("  %s  %-6s  %3d attribute writes  -> text renders in palette %s"
                  % (label, jp, len(aw), pals if pals else "n/a"))
            if aw:
                seen = {}
                for a, v in aw:
                    seen[a] = v
                for a in sorted(seen):
                    print("        $%04X = $%02X   (quadrants: %s)"
                          % (a, seen[a], [ (seen[a] >> s) & 3 for s in (0, 2, 4, 6) ]))
        print("\n  Palette RAM ($3F00-$3F1F) is uploaded by $C9A2 from zero page")
        print("  $10-$1F, which this call chain never touches - so the RGB values")
        print("  come from screen-setup code elsewhere and are not decided here.")
        print("  What IS decided here is the palette INDEX, and that is the thing")
        print("  that changed.")

    # ---- regression sweep over every call site ----
    if os.path.exists(PATCHED):
        dst = open(PATCHED, "rb").read()
        print("\n=== sweep: all 62 call sites on the patched ROM ===")
        SLOT, ROW = 0x80, 0x04
        worst_col, worst_slot, blank, fails = 0, 0, 0, []
        bad_pal = []
        for idx in range(62):
            ptr, jp = pointer_for(dst, idx)
            try:
                bus, _ = run_printer(dst, ptr, row=ROW, slot=SLOT, clear_first=True)
            except (IllegalOpcode, SystemExit) as e:
                fails.append((idx, jp, str(e))); continue
            drawn = [c for c in range(32) if bus.vram[0x2000 + ROW * 32 + c]]
            pals = {attr_palette(bus, ROW, c) for c in drawn}
            if drawn and pals != {3}:
                bad_pal.append((idx, jp, sorted(pals)))
            cells = [c for c in range(32) if bus.vram[0x2000 + ROW * 32 + c]]
            rows_used = {r for r in range(30)
                         if any(bus.vram[0x2000 + r * 32 + c] for c in range(32))}
            if not cells:
                blank += 1; continue
            worst_col = max(worst_col, max(cells) + 1)
            worst_slot = max(worst_slot, max(bus.vram[0x2000 + ROW * 32 + c] for c in cells))
            if len(rows_used) != 1:
                fails.append((idx, jp, "wrote %d rows, expected 1" % len(rows_used)))
            if max(cells) - min(cells) + 1 != len(cells):
                fails.append((idx, jp, "gap in the drawn columns"))
        print("  %d names drawn, %d blank, %d failures" % (62 - blank - len(fails), blank, len(fails)))
        print("  rightmost column used : %d  (nametable is 32 wide)" % worst_col)
        print("  highest CHR-RAM slot  : $%02X (base $%02X, must stay under $100)"
              % (worst_slot, SLOT))
        for idx, jp, why in fails:
            print("    FAIL idx %2d %-12s %s" % (idx, jp, why))
        if bad_pal:
            print("  PALETTE MISMATCH (%d):" % len(bad_pal))
            for idx, jp, p in bad_pal:
                print("    idx %2d %-12s renders in palette %s, expected [3]" % (idx, jp, p))
        else:
            print("  palette              : 3 for every drawn tile, matching the kanji")
        if not fails and not bad_pal:
            print("  no failures: one row, contiguous, on screen, correct palette")

    # contact sheet
    tiles = [(lbl, render(b)) for lbl, b in cases]
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
    print("\nwrote %s" % OUT_PNG)


if __name__ == "__main__":
    main()
