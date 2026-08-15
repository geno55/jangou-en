#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Equivalence test for the call-site refactor.

Runs the whole yaku-list routine on the ORIGINAL ROM and on a refactor-only
build (no string changes, so output must be identical), driving it with
synthetic yaku flag bitfields, and compares what a caller could observe.

Coverage is exhaustive where exhaustive is possible. It used to be twelve
cases from random.sample() on a fixed seed - three of the fifty-three
singletons and never more than six flags at once. Now:

    all 53 singletons        every block, one at a time
    all 53 adjacent pairs    every block against its neighbour, wrapping
    6 sliding windows of 10  every block at maximum simultaneity
    the empty set            nothing scored

The ceiling of 10 simultaneous yaku is the game's, not the test's. Each name
takes two rows, so the eleventh fills the score window and the routine hands
off to an NMI-synchronised update - $E166 spins on bits 0-1 of $3F waiting for
an NMI this harness does not run. That code is in bank 0F and the refactor does
not touch it. Cases above the ceiling are reported as skipped, never as passed.

What is compared, at $9B1E and again eight instructions later:

    A X Y P S, zero page, the live stack, RAM, WRAM, CHR-RAM, the nametable,
    and the complete ordered PPU write sequence.

Three registers legitimately differ at $9B1E - A, X and P - and each is listed
in ALLOWED_AT_END with its reason. The reasons are not taken on trust: both
machines are stepped past $9B1E and required to agree on everything, which is
what actually shows the differences are dead. A negative control removes block
55's LDX #$07 and requires the test to fail, because that instruction is the
entire reason the X divergence is harmless.

The A divergence was found by this version. The old test happened to include
block 54 as a case but compared only the nametable, CHR-RAM and PPU writes, so
a register difference could not show up.

    python tools/test_refactor.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import utf8io          # noqa: F401  - not via test_printer's import, on purpose
from cpu6502 import CPU, IllegalOpcode
from test_printer import Bus, SENTINEL, CHAIN_START, FLAG_BASE, run_chain as _run_chain
import refactor

from rom import ROOT, SRC_ROM, cpu2file, fixed_off

CHAIN_END   = 0x9B1E     # block 55; both versions must arrive here

# $E166  LDA $3F / AND #$03 / BNE -6 - waits for the NMI to acknowledge a
# screen update. No NMI here, so this is a hang, and it is what caps the test
# at 10 simultaneous yaku. Bank 0F, untouched by the refactor.
NMI_SPIN    = range(0xE166, 0xE16C)
MAX_YAKU    = 10         # names that fit the score window, two rows each
CONVERGE_BY = 8          # instructions past CHAIN_END by which all state must agree

# Registers that may differ at CHAIN_END, and why. Everything else must match
# there; everything including these must match CONVERGE_BY instructions later.
ALLOWED_AT_END = {
    "X": "the loop leaves X = 53, its iteration count; the last unrolled block "
         "left X = its own flag-group immediate ($07). Block 55's second "
         "instruction is LDX #$07. Dead at +2.",
    "P": "carry. The loop's CPX #53 sets C where the unrolled path left it "
         "clear. Block 55 does CLC before its ADC, and its LDY/LDX overwrite "
         "N and Z. Dead at +4.",
    "A": "only when block 54 - the last one - scores. The loop restores X "
         "after JSR $A058 with PLA/TAX, and the PLA leaves the pushed "
         "iteration index ($34) in A; the unrolled path left whatever $A058 "
         "returned. Block 55's third instruction is TXA. Dead at +3. "
         "Exhaustive coverage found this; twelve samples comparing only PPU "
         "state could not.",
}


def snapshot(cpu, bus):
    """Everything a caller could observe.

    The stack BELOW S is excluded: those bytes are dead by definition, and the
    two versions really do leave different litter there - the loop pushes X
    around the JSRs and the unrolled blocks never did. Excluding them is a
    statement about the 6502, not a convenience."""
    return {
        "A": cpu.a, "X": cpu.x, "Y": cpu.y, "P": cpu._p(), "S": cpu.s,
        "zero page": bytes(bus.ram[0x00:0x100]),
        "live stack": bytes(bus.ram[0x100 + ((cpu.s + 1) & 0xFF):0x200]),
        "RAM": bytes(bus.ram[0x200:0x800]),
        "WRAM": bytes(bus.wram),
        "CHR-RAM": bytes(bus.vram[0x0000:0x2000]),
        "nametable": bytes(bus.vram[0x2000:0x2400]),
        "PPU writes": tuple(bus.writes),
    }


def differences(a, b):
    return [k for k in a if a[k] != b[k]]


def run_chain(rom, flags, **kw):
    """The shared driver from test_printer, stopped at block 55 and taught that
    $E166 means "waiting for an NMI this harness does not run"."""
    return _run_chain(rom, flags, CHAIN_END, spin=NMI_SPIN, **kw)


def compare(src, ref, flags):
    """Run one flag pattern through both ROMs. Returns (instrs, problems, note)."""
    ca, ba, na, ea = run_chain(src, flags)
    cb, bb, nb, eb = run_chain(ref, flags)
    if ea or eb:
        if ea and eb and ea.startswith("waits") and eb.startswith("waits"):
            return (na, nb), [], "SKIP both %s" % ea
        return (na, nb), ["original: %s / refactored: %s" % (ea, eb)], None

    bad = []
    at_end = differences(snapshot(ca, ba), snapshot(cb, bb))
    for k in at_end:
        if k not in ALLOWED_AT_END:
            bad.append("%s differs at $%04X" % (k, CHAIN_END))
    for _ in range(CONVERGE_BY):
        ca.step(); cb.step()
        if ca.pc != cb.pc:
            bad.append("execution diverged past $%04X" % CHAIN_END)
            break
    else:
        late = differences(snapshot(ca, ba), snapshot(cb, bb))
        for k in late:
            bad.append("%s still differs %d instructions past $%04X - the "
                       "divergence is not dead" % (k, CONVERGE_BY, CHAIN_END))
    return (na, nb), bad, None


def make_refactor_only(src):
    """Refactor with no string changes: output must be functionally identical."""
    dst = bytearray(src)
    refactor.apply(dst, verbose=False)
    return bytes(dst)


def safety_negative_controls(src):
    """The safety analysis used to be a docstring. Now that it is code, show it
    can fail - otherwise it is a docstring that runs."""
    out = []

    def one(label, mutate=None, beq=None):
        rom = bytearray(src)
        if mutate:
            mutate(rom)
        keep = refactor.BEQ_REL
        if beq is not None:
            refactor.BEQ_REL = beq
        try:
            refactor.check_safety(bytes(rom), verbose=False)
            out.append((label, False, "(nothing)"))
        except SystemExit as e:
            out.append((label, True, e.args[0].splitlines()[0]))
        finally:
            refactor.BEQ_REL = keep

    def plant(rom):                    # a new way in that nobody accounted for
        o = fixed_off(0xE000)
        rom[o:o + 3] = b"\x4c\x00\x95"
    def unbranch(rom):                 # a path in ENTRIES that no longer exists
        o = cpu2file(0x8F16)
        rom[o:o + 2] = b"\xea\xea"

    one("an unaccounted path into the span", mutate=plant)
    one("ENTRIES gone stale", mutate=unbranch)
    one("block BEQ misses the boundary", beq=refactor.BEQ_REL - 1)
    return out


def equivalence_negative_controls(src, ref, pool):
    """Break the refactored build, and break the assumption that makes the
    allowed divergences harmless. Both must be caught."""
    out = []

    def one(label, flags, mutate_src=None, mutate_ref=None):
        a = bytearray(src); b = bytearray(ref)
        if mutate_src:
            mutate_src(a); mutate_src(b)      # a ROM-wide change, both sides
        if mutate_ref:
            mutate_ref(b)                     # a refactor bug, one side
        _, problems, note = compare(bytes(a), bytes(b), flags)
        out.append((label, bool(problems), (problems or [note or "(nothing)"])[0]))

    t = refactor.table_addrs()

    def no_reload_x(rom):
        # Block 55's LDX #$07 at $9B20 is the whole reason X may differ at
        # CHAIN_END. Take it away and the divergence stops being dead.
        rom[cpu2file(0x9B20):cpu2file(0x9B20) + 2] = b"\xea\xea"
    def bend_mask(rom):
        # rotate, don't flip a bit: flipping bit 0 of a mask that tests bit 7
        # leaves the AND result unchanged for a hand that scores only that
        # yaku, and the control passes vacuously. Rotating moves the block onto
        # a different flag bit, so it stops firing.
        o = cpu2file(t["msk"] + 7)
        rom[o] = ((rom[o] << 1) | (rom[o] >> 7)) & 0xFF
    def bend_han(rom):
        o = cpu2file(t["han"] + 3); rom[o] ^= 0x02
    def bend_col(rom):
        o = cpu2file(t["col"] + 3); rom[o] ^= 0x04

    one("block 55 stops reloading X", [pool[52]], mutate_src=no_reload_x)
    one("one tbl_msk entry rotated", [pool[7]], mutate_ref=bend_mask)
    one("one tbl_han bit flipped", [pool[3]], mutate_ref=bend_han)
    one("one tbl_col bit flipped", [pool[3]], mutate_ref=bend_col)
    return out


def main():
    src = open(SRC_ROM, "rb").read()

    print("safety analysis (was a docstring, now runs every build)")
    words = refactor.check_safety(src)
    bad = 0
    for label, caught, first in safety_negative_controls(src):
        print("    %-34s %s" % (label, "caught" if caught else "MISSED"))
        if not caught:
            print("      %s" % first); bad += 1
    if bad:
        print("\n%d safety check(s) cannot fail - they prove nothing" % bad)
        return 1
    print()

    ref = make_refactor_only(src)
    print("refactor-only build: %d bytes differ"
          % sum(1 for a, b in zip(src, ref) if a != b))

    # blocks 2..54 as (group, mask), read straight out of the ROM
    blocks = refactor.extract(src)
    pool = [(g["X"], g["mask"]) for g in blocks]
    n = len(pool)

    groups = [
        ("all off", [("none", [])]),
        ("every block alone",
         [("block %d" % (i + 2), [pool[i]]) for i in range(n)]),
        ("every adjacent pair",
         [("blocks %d+%d" % (i + 2, (i + 1) % n + 2), [pool[i], pool[(i + 1) % n]])
          for i in range(n)]),
        ("windows of %d" % MAX_YAKU,
         [("blocks %d-%d" % (i + 2, i + len(pool[i:i + MAX_YAKU]) + 1),
           pool[i:i + MAX_YAKU]) for i in range(0, n, MAX_YAKU)]),
    ]

    print("\nequivalence, exhaustive where the harness allows "
          "(ceiling %d simultaneous yaku - see the module docstring)" % MAX_YAKU)
    bad = skipped = total = 0
    instrs = 0
    for title, cases in groups:
        gbad = gskip = 0
        for label, flags in cases:
            (na, nb), problems, note = compare(src, ref, flags)
            total += 1
            instrs += na + nb
            if note:
                gskip += 1; skipped += 1
                print("  SKIP  %-16s %s" % (label, note))
                continue
            if problems:
                gbad += 1; bad += 1
                print("  FAIL  %-16s %s" % (label, problems[0]))
                for p in problems[1:]:
                    print("        %-16s %s" % ("", p))
        print("  %-22s %3d cases  %s"
              % (title, len(cases),
                 "all equivalent" if not gbad and not gskip else
                 "%d failed, %d skipped" % (gbad, gskip)))

    print("\n  %d cases, %d instructions executed" % (total, instrs))
    print("  compared: A X Y P S, zero page, live stack, RAM, WRAM, CHR-RAM,")
    print("            nametable, and the full ordered PPU write sequence")
    print("  at $%04X these may differ, and must be dead by +%d instructions:"
          % (CHAIN_END, CONVERGE_BY))
    for k, why in sorted(ALLOWED_AT_END.items()):
        print("    %s - %s" % (k, why.split(";")[0]))

    print("\n  negative controls - each of these must be caught:")
    for label, caught, first in equivalence_negative_controls(src, ref, pool):
        print("    %-28s %s  %s" % (label, "caught" if caught else "MISSED",
                                    first if not caught else ""))
        if not caught:
            bad += 1

    if skipped and not bad:
        print("\n%d case(s) skipped - not passed" % skipped)
    print("\n%s" % ("ALL EQUIVALENT" if bad == 0 else "%d CASE(S) DIVERGED" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
