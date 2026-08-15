# Jangou — English translation patch

An English translation patch for **Jangou** (雀豪), a 1990 Famicom mahjong game
by Orpheus Industries, published by Victor Musical Industries.

All 62 yaku names render in English.

**The rest of the score screen does not.** The `N符 M飜` line draws directly
above the yaku list and is still 16×16 kanji, so a scored hand reads `RIICHI` /
`MENZEN-TSUMO` in Latin with `30符 4飜` in Japanese underneath. The round
indicator and the 二飜縛り rule display are also untouched. Those sites are
outside everything this patch rewrites — but the screen is mixed-script, and
saying "the yaku names are translated" without saying that would be an
invitation to assume otherwise. See [Known gaps](#known-gaps).

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
python tools/build.py --refactor
python tools/verify_patch.py     # both patches decode to the built ROM
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

3143 bytes differ from the source ROM.

## Repository layout

| | |
|---|---|
| `script/yaku-en.txt` | **the translation — this is the file to edit** |
| `tools/rom.py` | **the cartridge map — constants and address arithmetic, one copy** |
| `tools/build.py` | the build |
| `tools/refactor.py` | the call-site refactor |
| `tools/cpu6502.py` | 6502 interpreter — all 151 **documented** opcodes, no undocumented ones |
| `tools/test_printer.py` | headless render test + full sweep |
| `tools/test_refactor.py` | equivalence test for the refactor |
| `tools/verify_patch.py` | spec-complete BPS/IPS reader; applies both patches back |
| `tools/extract.py` | regenerates the derived CSVs from the ROM |
| `jangou.tbl` | 8×8 character table — **source**, hand-verified |
| `jangou-kanji.tbl` | 16×16 kanji table, 135 codes — **source**, hand-verified |
| `yaku-callsites.csv` | 62 call sites and their patch offsets — *generated* |
| `yaku-names.csv` | 90 enumerated Japanese strings — *generated* |
| `string-inventory.csv` | UI text triage, contains false positives — *generated* |

The three CSVs are checked in because the build needs them, but they are
reproducible from the ROM plus the two `.tbl` files:

```bash
python tools/extract.py --check    # verify they still match
python tools/extract.py            # regenerate
```

The `.tbl` files are *not* generated. They were derived by rendering every
glyph and checking the result against the game's own yaku spellings — a step
that needs a human eye. See [`KANJI-TABLE.md`](KANJI-TABLE.md).

## Documentation

Read in order:

1. [`PHASE1-TEXT-ENGINE.md`](PHASE1-TEXT-ENGINE.md) — cartridge layout, the
   CHR-RAM glyph pipeline, character encoding
2. [`KANJI-TABLE.md`](KANJI-TABLE.md) — all 135 kanji codes, how they resolve
3. [`YAKU-PRINTER.md`](YAKU-PRINTER.md) — the printer, the call sites, `$EB19`
4. [`BUILD.md`](BUILD.md) — building, editing the script, the refactor, testing

When something here turns out to be wrong, the wrong version is **removed** and
what replaced it is listed in [`YAKU-PRINTER.md`](YAKU-PRINTER.md) §4. Leaving
a retracted claim standing next to its retraction is not honesty, it is a trap
for whoever reads only the first paragraph. Two so far: `$EB19` is a palette
table, not a name-length table, and the patch does *not* drop the per-character
attribute write — it reaches `$D742` by a different route.

## Testing without an emulator

`tools/cpu6502.py` plus `tools/test_printer.py` map the ROM the way MMC1 does,
call the printer directly, capture every `$2006`/`$2007` write, and rebuild
CHR-RAM and the nametable.

It does not check geometry. One row, contiguous columns, on screen — a name
rendered as eight `Z`s satisfies all of that. Instead the test reads the glyphs
back out of CHR-RAM and compares them byte for byte against the font in the
ROM, so "it drew something" and "it drew the right letters" are different
results. The **unpatched** ROM gets the same treatment against the 16×16 kanji
sheet, which is what makes the harness itself trustworthy — no human squints at
a PNG.

```
62 of 62 control renders match the ROM's own kanji data
62 of 62 names render exactly the text in script/yaku-en.txt
rightmost column used : 20  (nametable is 32 wide)
highest CHR-RAM slot  : $89 (base $80, must stay under $100)
```

Then it breaks the ROM six ways — a changed letter, a scrambled name, a
truncated name, a moved pointer, and two engine edits that leave the geometry
perfect — and fails unless every one is caught. Failures print and the script
exits 1. See [`BUILD.md`](BUILD.md#testing-without-an-emulator).

## Known gaps

- **The score screen is mixed-script.** 12 sites in bank 04 still upload 16×16
  kanji, in three groups: the `N符 M飜` line (5), the round indicator (3), and
  the 二飜縛り rule display (4). The first of those is the one that shows: it
  sits immediately above the yaku list. `test_printer.py` prints the full
  inventory with addresses on every run and fails if it drifts, so this is now
  a checked fact rather than something nobody had looked for. Finishing the
  line is the obvious next commit — and it is more than "two more strings",
  because the **digits are 16×16 kanji too** (`$9D00`, `$9D22`, `$9D5E` compute
  a kanji code from `$6039`/`$6038`), so converting `符`/`飜` alone would put
  8×8 letters next to 16×16 numerals. The whole line has to move together.
- **Nothing has run on hardware or in an emulator yet.** Every claim here comes
  from static analysis and the 6502 harness, and the harness has three
  structural blind spots, not vague ones:
  - **No NMI or IRQ path at all.** Interrupts are never delivered, so anything
    the game does per-frame is invisible to it. This is also why the
    multi-yaku test tops out at 10 names: the eleventh fills the window and
    `$E166` spins waiting for an NMI acknowledgement that never comes.
  - **`$2002` returns `$80` unconditionally**
    ([test_printer.py](tools/test_printer.py)), so every vblank spin-wait
    exits on its first read. Real timing is not modelled and cannot be.
  - **No PPU rendering.** It reconstructs CHR-RAM and the nametable from the
    write stream; it does not scan out pixels, so scroll, sprite-0 and
    mid-frame effects are out of reach.
- **The English yaku list is double-spaced, and that was never a decision.**
  The patch makes glyphs one row tall but leaves the line pitch at two rows
  (`$9FBB` is untouched), so five yaku land on rows 0, 2, 4, 6, 8 where the
  original filled 0–9 solidly. `test_printer.py` now drives the whole routine
  and prints both, so it is at least looked at. It is legible and occupies
  exactly the rows the original did — but whether it *should* be single-spaced
  is a design call nobody has made, and the change is one byte pair. See
  [`BUILD.md`](BUILD.md#the-line-pitch).
- **The refactor's safety is checked, but not provable statically.** Nothing in
  bank 04 or the fixed bank 0F reaches into the replaced span except three
  paths to `$8F51`, which survives — `refactor.check_safety()` verifies that on
  every build rather than asserting it in a comment. What no static scan can
  rule out is a *computed* jump into the span: 230 raw word pairs in those two
  banks address it, and that is the noise floor. Only a runtime CDL pass closes
  it. See [`BUILD.md`](BUILD.md).
- **`--use-dora-block` is off by default**, and the headline command no longer
  turns it on. It reuses 81 bytes that could be shown unreferenced but not
  proven dead, and alongside `--refactor` it buys nothing — 62 of 62 either
  way. Enable it only after confirming in an emulator that dora names never
  appear on screen.
- **No real patcher has ever applied the `.bps`.** `verify_patch.py` implements
  the whole BPS format from the spec — all four actions, all three CRCs — and
  is exercised on features `build.py` cannot emit, but it is still our code
  reading our output. Applying it with `beat` or `Flips` and comparing the
  SHA-1 is one command; see [`BUILD.md`](BUILD.md#verifying-the-patch-files).
- **Menu and UI text is untranslated.** Unlike the yaku names, it is emitted by
  unrolled per-character code with no script and no printer. This used to say
  "roughly 880 hardcoded sites"; the real figure is **at most ~480, likely
  fewer**. 210 of the 882 are in banks that cannot execute, 57 carry the han
  value rather than a character, and 137 load a byte that is not a glyph code
  at all. `python tools/extract.py --counts` prints the derivation. See
  [`PHASE1-TEXT-ENGINE.md`](PHASE1-TEXT-ENGINE.md).

## Legal

The patch contains no copyrighted game data. It is a byte diff against a ROM
you must supply yourself. Do not distribute the ROM or the patched `.nes`.
