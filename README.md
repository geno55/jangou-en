# Jangou — English translation patch

An English translation patch for **Jangou** (雀豪), a 1990 Famicom mahjong game
by Orpheus Industries, published by Victor Musical Industries.

All 62 yaku names render in English. The scoring vocabulary — the text a player
actually reads — is fully translated.

## You need to supply the ROM

The ROM is not in this repository and never will be. The patch is the
deliverable. Put your own dump in the repo root as `Jangou (Japan).nes`:

```
sha1    e1de1fa7a7bbac0315f604beac74a6e296b89078
crc32   0973F714   (headerless)
size    262,160 bytes
```

The build verifies that hash and refuses to run against anything else. This
patch is byte-offset specific and would silently corrupt a different dump.

## Build

Python 3.8+, plus Pillow for the render test. No other dependencies.

```bash
python tools/build.py --use-dora-block --refactor
python tools/verify_patch.py     # both patch formats round-trip
python tools/test_refactor.py    # the code refactor is behaviour-preserving
python tools/test_printer.py     # every name renders correctly
```

Output in `build/`: a patched `.nes` plus `.bps` and `.ips` patches. Ship the
BPS. Same inputs always produce the same bytes — verified across Python hash
seeds.

## What the patch does

The game has **CHR-RAM**, so no text exists as pre-rendered tiles: every
character is copied from PRG into video RAM at draw time. Yaku names were drawn
as 16×16 kanji. The patch:

1. **Converts the yaku printer to 8×8** — the font already contains a full
   Latin A–Z, so no new glyphs were needed. 29 bytes, same length as the
   original, no addresses move.
2. **Collapses 53 unrolled call-site blocks into a loop** — 400 bytes replace
   3021, freeing 2621 bytes. This is what pays for English names, which are
   3–5× longer than the kanji they replace.
3. **Rewrites the yaku name table** in the freed space and repoints all 62
   call sites.

3206 bytes differ from the source ROM.

## Repository layout

| | |
|---|---|
| `script/yaku-en.txt` | **the translation — this is the file to edit** |
| `tools/build.py` | the build |
| `tools/refactor.py` | the call-site refactor |
| `tools/cpu6502.py` | a complete NMOS 6502 interpreter |
| `tools/test_printer.py` | headless render test + full sweep |
| `tools/test_refactor.py` | equivalence test for the refactor |
| `tools/verify_patch.py` | applies both patches back and compares |
| `jangou.tbl` | 8×8 character table |
| `jangou-kanji.tbl` | 16×16 kanji table (135 codes) |
| `yaku-callsites.csv` | 62 call sites and their patch offsets |
| `yaku-names.csv` | 90 enumerated Japanese strings |
| `string-inventory.csv` | UI text triage list — contains false positives |

## Documentation

Read in order:

1. [`PHASE1-TEXT-ENGINE.md`](PHASE1-TEXT-ENGINE.md) — cartridge layout, the
   CHR-RAM glyph pipeline, character encoding
2. [`KANJI-TABLE.md`](KANJI-TABLE.md) — all 135 kanji codes, how they resolve
3. [`YAKU-PRINTER.md`](YAKU-PRINTER.md) — the printer, the call sites, `$EB19`
4. [`BUILD.md`](BUILD.md) — building, editing the script, the refactor, testing

Two corrections are recorded in place rather than quietly fixed: `$EB19` is a
palette table, not a name-length table, and the claim that the patch drops the
attribute write was wrong.

## Testing without an emulator

`tools/cpu6502.py` plus `tools/test_printer.py` map the ROM the way MMC1 does,
call the printer directly, capture every `$2006`/`$2007` write, and rebuild
CHR-RAM and the nametable into a PNG. It renders the **unpatched** ROM first as
a control — that must produce the original kanji, which is what makes the
patched render trustworthy.

```
62 names drawn, 0 blank, 0 failures
rightmost column used : 20  (nametable is 32 wide)
highest CHR-RAM slot  : $89 (base $80, must stay under $100)
palette              : 3 for every drawn tile, matching the kanji
```

## Known gaps

- **`yaku-callsites.csv`, `yaku-names.csv` and `string-inventory.csv` are
  generated, but their generators are not in `tools/`.** They were produced
  during analysis and are checked in because the build needs them. That is a
  real reproducibility hole: the build cannot be regenerated from source alone.
  Porting the extractors into `tools/` is the first thing to fix.
- **Nothing has run on hardware or in an emulator yet.** Every claim here comes
  from static analysis and the 6502 harness. Two things the harness cannot
  reach: a score screen with several yaku at once (it prints one at a time, so
  line-pitch and scroll interactions are untested), and dora — `--use-dora-block`
  reuses 81 bytes that could be shown unreferenced but not proven dead.
- **Menu and UI text is untranslated.** Unlike the yaku names, it is emitted by
  unrolled per-character code with no script and no printer — roughly 880
  hardcoded sites. See `PHASE1-TEXT-ENGINE.md`.

## Legal

The patch contains no copyrighted game data. It is a byte diff against a ROM
you must supply yourself. Do not distribute the ROM or the patched `.nes`.
