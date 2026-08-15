# Building the Jangou English patch

```bash
python tools/build.py --use-dora-block --refactor
python tools/verify_patch.py
python tools/test_refactor.py
python tools/test_printer.py
```

**All 62 yaku names are in English.**

Output lands in `build/`: a patched `.nes` plus `.bps` and `.ips` patches.
Your ROM is never modified — the build reads it, verifies its SHA-1, and
writes elsewhere.

## What this patch does

Converts the yaku name display from 16×16 kanji to the 8×8 Latin charset the
game already contains, and replaces the Japanese yaku names with English.

It changes **450 bytes**. Nothing moves; every edit is same-length.

## Requirements

Python 3.8+. No third-party packages. The source ROM must be:

```
Jangou (Japan).nes
sha1  e1de1fa7a7bbac0315f604beac74a6e296b89078
```

The build refuses to run against anything else. This patch is byte-offset
specific and would silently corrupt a different dump.

## Files

| | |
|---|---|
| `tools/build.py` | the build |
| `tools/verify_patch.py` | applies both patches back and checks they reproduce the ROM |
| `script/yaku-en.txt` | **the translation. This is the file you edit.** |
| `yaku-callsites.csv` | 62 call sites and their patch offsets (generated, do not hand-edit) |
| `build/` | output, safe to delete |

## Editing the translation

`script/yaku-en.txt` is `[*]index | japanese | english`, one per line.

- **Uppercase only.** The font has A–Z, 0–9, space, and `* - ! ? ( ) < > / .`
  There is no lowercase, comma, colon or apostrophe. The build rejects
  anything else by name and line number.
- **14 characters maximum.** That is the width the original already draws
  (国士無双十三面 = 7 kanji × 2 columns), so it is known safe. Raise `MAX_LEN`
  in `build.py` only after measuring the score window on screen.
- **`*` marks high priority.** Space runs out; starred lines are packed first.

Re-run the build after editing. Same inputs always produce the same output —
verified across Python hash seeds.

## The call-site refactor (`--refactor`)

This is what pays for the full translation. `tools/refactor.py`.

Bank 04 held **56 byte-identical 57-byte blocks**, one per yaku, differing only
in six immediate values:

```asm
LDY $0A            ; player
LDX #$02           ; VARIES: flag group
TXA / ASL / ASL / CLC / ADC #$74 / STA $08    ; $6C74 + group*4
LDA #$00 / ADC #$6C / STA $09
LDA ($08),Y
AND #$04           ; VARIES: bit mask
BEQ next
LDA #$21 / STA $0D ; VARIES: string pointer
LDA #$9E / STA $0E
JSR $9F7A          ; print the name
JSR $9FD2
LDA #$18 / STA $00 ; VARIES: han display column
LDA #$01 / STA $01 ; constant
LDA #$01 / STA $02 ; VARIES: han value
LDA #$00 / STA $03 ; VARIES: 0 or 4
JSR $A058
```

Blocks 2–54 (53 of them, `$8F51–$9B1D`, exactly 3021 bytes with no gaps)
collapse into 82 bytes of loop plus six 53-byte parallel arrays — structure of
arrays, not array of structures, so a single 8-bit `X` indexes all six.
`base` and the `$01` are constants and vanish; `p03` is only ever 0 or 4 so it
packs into bit 7 of the han byte.

**400 bytes replace 3021. That frees 2621.**

Three blocks are deliberately left alone:

- **blocks 0 and 1** plus the 33-byte interlock at `$8EF7–$8F17`, which encodes
  "double riichi and riichi are mutually exclusive"
- **block 55** (`$9B1E`), whose not-taken branch lands 5 bytes past its own end
  instead of on the next block boundary
- the **6 irregular sites** at `$9B61` and `$9D8F–$9DD3` (limit labels)

Checked before writing a byte: every branch inside the replaced span lands on
a block boundary, and the only references into it from bank 04 or the fixed
bank are three jumps to `$8F51`, which stays the entry point. The extractor
re-verifies all of that at build time and refuses to run if any assumption
fails.

### Proving it is equivalent

```bash
python tools/test_refactor.py
```

Runs the whole yaku-list routine on the original ROM and on a refactor-only
build (no string changes, so output must match exactly), driving it with
synthetic flag bitfields, and compares nametable, CHR-RAM, and the complete
PPU write sequence:

```
refactor-only build: 2867 bytes differ
  single: block 2  OK   1 flags   4041/3895 instrs   16 tiles  nt= chr= writes=
  five             OK   5 flags  17139/17038 instrs  80 tiles  nt= chr= writes=
  random 2         OK   6 flags  21235/21145 instrs 100 tiles  nt= chr= writes=
ALL EQUIVALENT
```

2867 bytes of code really changed; every observable output is identical.

## The space problem (solved by `--refactor`)

Without the refactor this is the binding constraint, and it is worth
understanding.

The printer lives in bank 04 and reads its pointer through `($0D),Y`, so
strings must sit in bank 04's `$8000–$BFFF` window. Recursive-descent
reachability plus a full operand cross-reference over all 16 banks found
**no free space in bank 04 or bank 0F**. Every apparently-dead region is a
referenced data table. The only reusable bytes are the Japanese table we are
replacing:

| | |
|---|---|
| `$9E18–$9F13` | 252 bytes — Japanese yaku names |
| `$9F14–$9F64` | 81 bytes — ドラ１..ドラ１８, opt-in, see below |
| `$9F65–$9F79` | 21 bytes — the limit labels |
| **total** | **354 bytes** |

All 62 English names want ~484 bytes. **The shortfall is about 130 bytes.**

The build packs what fits and points everything else at a shared empty string.
Currently **47 of 62 names are English; 15 print blank.**

### Why blank, and not left in Japanese

The patched printer sends *every* character code to the 8×8 glyph loader. A
kanji code fed to it fetches font tile `$200 + code`, which is bitmap garbage.
The patch is all-or-nothing per string — there is no room in the printer's
88 bytes to dispatch on code value and keep both paths. So untranslated
entries are pointed at a `$00` terminator: they draw nothing and advance the
line. Blank is honest; garbage is not.

### `--use-dora-block`

`$9F14–$9F64` holds ドラ１ through ドラ１８. No call site points at it, and no
computed pointer into it could be found in any bank. It is very probably dead
data — but *could not find* is not *does not exist*. The flag is opt-in.
Before relying on it, confirm in an emulator that dora names never appear on
screen. Without the flag you get 273 bytes and far fewer names.

### Getting the remaining 130 bytes

The 62 call sites are ~90 bytes each of near-identical unrolled code —
about 5.5 KB in bank 04:

```asm
LDA ($08),Y        ; yaku flag bitfield
AND #$02           ; this yaku's bit
BEQ skip
LDA #$18 / STA $0D ; pointer
LDA #$9E / STA $0E
JSR $9F7A
JSR $9FD2
LDA #$17 / STA $00 ; han value display
...
```

Collapsing that into a loop over a table of (bitmask, pointer, han) would free
several kilobytes — enough for full English names, a proper dispatching
printer that handles both glyph sizes, and room to spare. It needs the flag
bitfield layout confirmed at runtime first, which is why it is not in this
patch.

## Testing without an emulator

```bash
python tools/test_printer.py
```

`tools/cpu6502.py` is a complete NMOS 6502 interpreter; `tools/test_printer.py`
maps the ROM the way MMC1 does, sets up the zero page the way a call site does,
calls `$9F7A` directly, captures every `$2006`/`$2007` write, and reconstructs
CHR-RAM and the nametable into `build/printer-test.png`.

It is self-validating: it renders the **unpatched** ROM first, which must
produce the original 16×16 kanji. That control passing is what makes the
patched render trustworthy.

It then sweeps all 62 call sites and checks each name draws on exactly one
row, in contiguous columns, inside the screen, without running the CHR-RAM
slot counter past `$FF`. Current result:

```
62 names drawn, 0 blank, 0 failures
rightmost column used : 20  (nametable is 32 wide)
highest CHR-RAM slot  : $89 (base $80, must stay under $100)
palette              : 3 for every drawn tile, matching the kanji
```

### The palette question, settled

An earlier note here said the patch drops the attribute write because it stops
calling `$C039`. **That was wrong.** `$C03C` → `$E616`, which the patched
printer calls instead, reaches the same attribute writer `$D742`. The harness
simulates the real sequence — `$D4F3` clears `$2000-$23FF`, which includes the
attribute table, so every 2×2 block starts at palette 0 — and then prints:

```
original  立直   2 attribute writes  -> palette 3
patched   立直   6 attribute writes  -> palette 3
```

Six writes instead of two, because 8×8 characters are half the width of a
kanji and two of them share an attribute quadrant. `$D742` handles that with a
read-modify-write:

```asm
$D7D4  LDA $2007      ; buffered read: discard
$D7D7  LDA $2007      ; the real byte
$D7DA  AND $0C        ; clear this quadrant
$D7DC  ORA $0D        ; OR in the palette bits
```

That idiom is the reason the harness needs a correct PPUDATA read buffer.
Without one, `$2007` reads return zero, the `AND`/`ORA` wipes the neighbouring
quadrant, and the text renders half in palette 0 and half in palette 3. The
first version of the harness had exactly that bug and reported a mixed palette
that does not actually occur.

The RGB values still come from elsewhere — `$C9A2` uploads them from zero page
`$10-$1F`, which this call chain never touches. But the palette *index* is what
the patch could have broken, and it does not.

## What to check when you run it

1. **Score a hand and read the yaku list.** Latin names, left-aligned at
   column 10. This is the whole proof — it means the 8×8 conversion works.
2. **Colour.** The patch drops the per-character attribute write (that lived
   in `$C039`, which we no longer call), so the text inherits the score
   window's existing palette. If it comes out wrong, the fix is to set the
   window's attributes once when it is drawn, not to reinstate a per-character
   write.
3. **Spacing.** Characters advance one column, lines two rows. `ROUND WIND`
   and `SANSHOKU-C` are the longest at 10; if anything overflows the window,
   lower `MAX_LEN`.
4. **Blank rows.** 15 yaku print nothing. Expected — see above.
5. **Dora.** If you used `--use-dora-block`, watch for a hand with dora and
   confirm nothing garbles.

## The engine patch

One routine, `$9F7A` in bank 04. 29 bytes, same length as the original:

```asm
$9F9E  LDA ($0D),Y      fetch character
$9FA0  BEQ $9FBB        $00 terminates
$9FA2  STA $02
$9FA4  CMP #$01         space: advance the cursor, draw nothing
$9FA6  BEQ $9FAE
$9FA8  JSR $80C6        -> $C024  upload one 8x8 glyph      (was $C027, 16x16)
$9FAB  JSR $80D6        -> $C03C  write one nametable tile  (was $C039, 2x2)
$9FAE  INC $0C          CHR-RAM slot += 1                   (was += 4)
$9FB0  INC $00          column      += 1                    (was += 2)
$9FB2  INC $05
$9FB4  JMP $9F98
$9FB7  NOP x4           pad so $9FBB stays put
```

Space is code `$01` — deliberately not a printable glyph. The font contains
no blank tile anywhere in the 8×8 page, so a space has to be a skip rather
than a character.

Background: `PHASE1-TEXT-ENGINE.md`, `KANJI-TABLE.md`, `YAKU-PRINTER.md`.

## Distribution

Ship `build/jangou-en.bps`. Never distribute the ROM or the patched `.nes` —
the BPS carries a CRC of the source, so it applies only to the correct dump
and refuses everything else.
