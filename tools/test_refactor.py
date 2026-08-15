#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Equivalence test for the call-site refactor.

Runs the whole yaku-list routine on the ORIGINAL ROM and on a refactor-only
build (no string changes, so output must match byte for byte), driving it with
synthetic yaku flag bitfields, and compares every PPU write.

If the loop is not a faithful replacement for the 53 unrolled blocks, the
nametable will differ.

    python tools/test_refactor.py
"""
import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cpu6502 import CPU, IllegalOpcode
from test_printer import Bus, SENTINEL
import refactor

ROOT    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_ROM = os.path.join(ROOT, "Jangou (Japan).nes")

CHAIN_START = 0x8EBE     # block 0
CHAIN_END   = 0x9B1E     # block 55; both versions must arrive here
FLAG_BASE   = 0x6C74


def run_chain(rom, flags, player=0, row=0x00, slot=0x40, limit=4_000_000):
    """flags: {(group, mask)} bits to set. Returns the Bus after the chain."""
    bus = Bus(rom, 4)
    cpu = CPU(bus)
    bus.ram[0x4B] = 4
    bus.ram[0x0A] = player
    bus.ram[0x0B] = row
    bus.ram[0x0C] = slot
    bus.ram[0x543 & 0x7FF] = 0
    bus.ram[0x34] = 0
    # yaku flag bitfield lives at $6C74 + group*4, indexed by the player in $0A
    for grp, mask in flags:
        a = FLAG_BASE + grp * 4 + player - 0x6000
        bus.wram[a] |= mask
    # the riichi interlock reads these
    bus.wram[0x6001 - 0x6000] = 0xFF
    bus.wram[0x6002 - 0x6000] = 0xFF

    r = SENTINEL - 1
    cpu.push(r >> 8); cpu.push(r & 0xFF)
    cpu.pc = CHAIN_START
    # Stop only when execution actually ARRIVES at block 55. A range test
    # ("pc outside the chain") is wrong: the chain JSRs to the printer at
    # $9F7A, which is above CHAIN_END, and would end the run on the first
    # yaku printed - comparing two blank screens and passing vacuously.
    n = 0
    while cpu.pc != CHAIN_END:
        cpu.step()
        n += 1
        if n > limit:
            return bus, n, "TIMEOUT at $%04X" % cpu.pc
    return bus, n, None


def make_refactor_only(src):
    """Refactor with no string changes: output must be functionally identical."""
    dst = bytearray(src)
    refactor.apply(dst, verbose=False)
    return bytes(dst)


def main():
    src = open(SRC_ROM, "rb").read()
    ref = make_refactor_only(src)
    print("refactor-only build: %d bytes differ"
          % sum(1 for a, b in zip(src, ref) if a != b))

    # blocks 2..54 as (group, mask), read straight out of the ROM
    blocks = refactor.extract(src)
    pool = [(g["X"], g["mask"]) for g in blocks]

    random.seed(20250815)
    cases = [("single: block 2", [pool[0]]),
             ("single: block 30", [pool[28]]),
             ("single: block 54", [pool[52]]),
             ("three", [pool[3], pool[10], pool[20]]),
             ("five",  [pool[1], pool[5], pool[12], pool[33], pool[44]]),
             ("none",  [])]
    for i in range(6):
        cases.append(("random %d" % i, random.sample(pool, random.randint(2, 6))))

    bad = 0
    for name, flags in cases:
        a, na, ea = run_chain(src, flags)
        b, nb, eb = run_chain(ref, flags)
        if ea or eb:
            print("  %-14s ERROR  orig=%s  refac=%s" % (name, ea, eb)); bad += 1; continue
        same_nt = a.vram[0x2000:0x2400] == b.vram[0x2000:0x2400]
        same_chr = a.vram[0x0000:0x2000] == b.vram[0x0000:0x2000]
        same_w = a.writes == b.writes
        tiles = sum(1 for v in a.vram[0x2000:0x23C0] if v)
        ok = same_nt and same_chr and same_w
        bad += 0 if ok else 1
        print("  %-14s %-4s  %3d flags  %6d/%6d instrs  %4d tiles  nt=%s chr=%s writes=%s"
              % (name, "OK" if ok else "FAIL", len(flags), na, nb, tiles,
                 "=" if same_nt else "X", "=" if same_chr else "X",
                 "=" if same_w else "X"))
        if not ok:
            for k, (wa, wb) in enumerate(zip(a.writes, b.writes)):
                if wa != wb:
                    print("      first divergence at write %d: orig $%04X=$%02X  refac $%04X=$%02X"
                          % (k, wa[0], wa[1], wb[0], wb[1]))
                    break
            if len(a.writes) != len(b.writes):
                print("      write counts: orig %d, refac %d" % (len(a.writes), len(b.writes)))

    print("\n%s" % ("ALL EQUIVALENT" if bad == 0 else "%d CASE(S) DIVERGED" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
