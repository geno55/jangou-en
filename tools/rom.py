#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The cartridge map. One copy of it.

Every tool here needs the same handful of facts: where the iNES header ends,
how big a PRG bank is, which bank is fixed and where each window lands, what
the source ROM must hash to, and where the repository's files live. Those were
re-typed in every file, and the copies had already drifted:

  * `cpu2file` was defined twice - in build.py WITH a bounds assert, in
    refactor.py WITHOUT. Two functions, same name, same job, one weaker.
  * `BANK04` meant `$10000`, a file OFFSET, in build.py and refactor.py, and
    `4`, a bank NUMBER, in test_printer.py. Same name, incompatible meanings,
    one import away from a silent corruption.
  * `SRC_SHA1` was in build.py and extract.py; `HDR` in three files.
  * extract.py placed banks `$08-$0E` at `$C000`. The mapper puts every bank
    except `$0F` at `$8000`. No output happened to fall in that range, so the
    disagreement was latent rather than visible - which is the failure mode
    that makes duplication worth removing before it costs anything.

Import from here rather than re-deriving. Nothing in this module reads a file
or has an opinion about what a tool should do; it is facts and address
arithmetic.
"""
import os

# ------------------------------------------------------------ cartridge ----
HDR       = 16                    # iNES header, stripped to get raw PRG
BANK_SIZE = 0x4000                # 16K PRG bank
NBANKS    = 16
ROM_SIZE  = HDR + NBANKS * BANK_SIZE          # 262,160 bytes

SRC_SHA1  = "e1de1fa7a7bbac0315f604beac74a6e296b89078"
SRC_CRC32 = 0x0973F714            # headerless

# MMC1 as this game configures it: one switchable 16K bank at $8000, and bank
# $0F permanently at $C000. Bank $0F can also be switched in at $8000, but
# nothing here does that, and the fixed mapping is what its code assumes.
SWITCHED     = 0x8000
SWITCHED_END = 0xBFFF
FIXED        = 0xC000
FIXED_END    = 0xFFFF
FIXED_BANK   = 0x0F
PRINTER_BANK = 0x04               # yaku printer $9F7A, its call sites, its strings
FONT_BANK    = 0x00               # $D55E switches here to fetch glyph data

# ---------------------------------------------------------------- paths ----
ROOT        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_ROM     = os.path.join(ROOT, "Jangou (Japan).nes")
BUILD_DIR   = os.path.join(ROOT, "build")
PATCHED_ROM = os.path.join(BUILD_DIR, "jangou-en.nes")
BPS_PATCH   = os.path.join(BUILD_DIR, "jangou-en.bps")
IPS_PATCH   = os.path.join(BUILD_DIR, "jangou-en.ips")
SCRIPT_TXT  = os.path.join(ROOT, "script", "yaku-en.txt")
CALLSITES   = os.path.join(ROOT, "yaku-callsites.csv")
CHARSET     = os.path.join(ROOT, "jangou.tbl")
KANJI_TBL   = os.path.join(ROOT, "jangou-kanji.tbl")


# ----------------------------------------------------- address arithmetic --
def window(bank):
    """Base CPU address of `bank` as this game maps it."""
    return FIXED if bank == FIXED_BANK else SWITCHED


def bank_off(bank, addr):
    """File offset of CPU address `addr` as seen with `bank` mapped.

    Raises rather than returning a plausible wrong number: an address outside
    the window is a bug in the caller, and it is exactly what the unchecked
    copy of this function would have silently computed."""
    if not 0 <= bank < NBANKS:
        raise ValueError("bank $%02X is outside 0-$%02X" % (bank, NBANKS - 1))
    base = window(bank)
    end = FIXED_END if base == FIXED else SWITCHED_END
    if not base <= addr <= end:
        raise ValueError("$%04X is outside bank $%02X's window $%04X-$%04X"
                         % (addr, bank, base, end))
    return HDR + bank * BANK_SIZE + (addr - base)


def fixed_off(addr):
    """File offset in the fixed bank $0F at $C000-$FFFF."""
    return bank_off(FIXED_BANK, addr)


def cpu2file(addr):
    """File offset in bank $04's $8000-$BFFF window.

    The printer, its 62 call sites and every string this patch writes live in
    bank 04, so this is the arithmetic almost every tool wants."""
    return bank_off(PRINTER_BANK, addr)


def prg2cpu(off, bank=PRINTER_BANK):
    """CPU address of a header-less PRG offset, as seen with `bank` mapped.

    The CSVs record offsets this way - `HDR + off` indexes the ROM file, while
    `prg2cpu(off)` says where the 6502 sees it."""
    lo = bank * BANK_SIZE
    if not lo <= off < lo + BANK_SIZE:
        raise ValueError("PRG offset $%05X is not inside bank $%02X" % (off, bank))
    return window(bank) + (off - lo)


def prg(rom):
    """The raw PRG, header stripped."""
    return rom[HDR:]


def bank_bytes(rom, bank):
    """One 16K bank as bytes."""
    if not 0 <= bank < NBANKS:
        raise ValueError("bank $%02X is outside 0-$%02X" % (bank, NBANKS - 1))
    o = HDR + bank * BANK_SIZE
    return rom[o:o + BANK_SIZE]


assert ROM_SIZE == 262160, "cartridge geometry does not match the documented dump"
