# Jangou — The Yaku Printer and `$EB19`

Phase 3. Two questions from the last pass: where is the code that walks the
yaku name table, and what is `$EB19`? Both answered, and one of my earlier
guesses was wrong.

Companion files: `yaku-callsites.csv`, `yaku-names.csv`.
Prerequisites: `PHASE1-TEXT-ENGINE.md`, `KANJI-TABLE.md`.

---

## 1. `$EB19` is a palette table, not a length table

I previously guessed it held the character count of each yaku name because
the values are 3, 2 and 0. That was wrong, and it isn't a near miss — the
table is on a completely different code path and never touches yaku text.

The trace:

```
$E79D   JSR $E889              ; map a mahjong TILE id -> glyph index in $02
$E7AF   LDX $02
$E7B1   LDA $EB19,X            ; <- the value
$E7B4   STA $02
        $03..$06 = four consecutive CHR-RAM slots
$E7C9   JSR $D9DE              ; draw the 2x2 block
          $DA1D  writes $03,$04 to $2007      ; top row
          $DA34  writes $05,$06 to $2007      ; bottom row
          $DA3E  JSR $D742
```

And `$D742` is unmistakably the attribute-table writer:

```
$D759   LDA $01 / LSR / LSR / ASL / ASL / ASL     ; (row / 4) * 8
$D762   LDA $00 / LSR / LSR / ADC $0C             ; + (col / 4)
$D76B   base = $23C0 + that                       ; ** attribute table **
$D780   pick quadrant from bits of row/col:
          mask $FC  and  $02 << 0
          mask $F3  and  $02 << 2
          mask $CF  and  $02 << 4
          mask $3F  and  $02 << 6
```

`$02` is shifted into a 2-bit quadrant of an `$23C0`-range byte. So `$EB19`
yields **which of the four background palettes** a mahjong tile graphic uses.
Values 0–3 are palette indices. Most tiles are 3; the 2s are the tiles that
need a second palette (the red 中, red fives, and so on).

Valid range is index `$00–$45` (70 entries). From `$EB69` the bytes are code
(`A5 02 48 …`), not table data.

`$E889`, which produces the index, maps mahjong tile IDs — honours `$00–$0D`,
then suit ranges split at `$10`, `$20`, `$30` — onto glyph slots `$3D`, `$3E`,
`$48–$4D`. Tile graphics, not text.

## 2. The yaku printer: `$9F7A`, bank 04

This is the good news. It is not a hand-unrolled mess like the UI text — it is
a **real loop-based, `$00`-terminated string printer taking a pointer**.

```asm
; in:  $0D/$0E = pointer to a $00-terminated kanji string
;      $0B     = row      $0C = CHR-RAM slot base
$9F7A   save $00-$05
$9F8C   LDA #$00 / STA $05      ; character index
$9F90   LDA #$0A / STA $00      ; starting column = 10
$9F94   LDA $0B  / STA $01      ; row (set once)
loop:
$9F98   LDA $0C  / STA $04      ; slot base for this character
$9F9C   LDY $05
$9F9E   LDA ($0D),Y             ; fetch character
$9FA0   BEQ done                ; $00 terminates
$9FA2   STA $02
$9FA4   JSR $80CA               ; -> $C027  upload 16x16 kanji (4 tiles)
$9FA7   JSR $80DA               ; -> $C03F  draw 2x2 + set attribute
$9FAA   INC $0C  x4             ; slot base += 4
$9FB2   INC $00  x2             ; column += 2
$9FB6   INC $05
$9FB8   JMP $9F98
done:
$9FBB   INC $0B  x2             ; row += 2, ready for the next line
        restore, RTS
```

### The call sites

**62 static call sites**, all in bank 04, all identical in shape:

```asm
$8ED0   LDA ($08),Y             ; yaku flag bitfield
$8ED2   AND #$02                ; this yaku's bit
$8ED4   BEQ skip
$8ED6   LDA #$18 / STA $0D      ; pointer low
$8EDA   LDA #$9E / STA $0E      ; pointer high
$8EDE   JSR $9F7A
```

The `AND` masks cycle `$01 $02 $04 $08 $10 $20 $40 $80`, so scored yaku are a
bitfield read through `($08),Y`, eight per byte.

`yaku-callsites.csv` lists all 62 with the **exact PRG offsets of the two
pointer bytes**, so repointing is a mechanical edit.

The remaining 18 table entries — ドラ１ through ドラ１８ at `$9F14–$9F60` —
have no static call site. They are formulaic, so the pointer is almost
certainly computed from the dora count. Find that before repointing the table,
or those 18 will break.

## 3. What this makes possible

Everything needed to put English yaku on screen is **one routine and 62 pointer
pairs**. No new printer, no engine to write.

### The core patch

Switch `$9F7A` from 16×16 kanji to 8×8 characters. Bank 04's local stubs make
this a same-length edit.

**What shipped** is one 29-byte replacement at `$9F9E` (PRG `$11F9E`, file
`$11FAE` — file offsets include the 16-byte iNES header, and bank 04 begins at
PRG `$10000`). `build.py` asserts old and new are both exactly 29 bytes and
refuses to run if the bytes it finds are not the ones below:

```
before  b1 0d f0 19 85 02  20 ca 80  20 da 80  e6 0c e6 0c e6 0c e6 0c
        e6 00 e6 00  e6 05  4c 98 9f
after   b1 0d f0 19 85 02  c9 01 f0 06  20 c6 80  20 d6 80  e6 0c
        e6 00  e6 05  4c 98 9f  ea ea ea ea
```

| effect | how |
|---|---|
| upload one 8×8 glyph, not four | `JSR $80CA` → `JSR $80C6`, i.e. `$C027` → `$C024` |
| write one tile, not a 2×2 block | `JSR $80DA` → `JSR $80D6`, i.e. `$C03F` → `$C03C` |
| CHR-RAM slot += 1, not 4 | three of the four `INC $0C` removed |
| column += 1, not 2 | one of the two `INC $00` removed |
| space draws nothing | **new:** `CMP #$01 / BEQ` skips both `JSR`s |
| keep `$9FBB` where it was | four trailing `NOP`s |

The freed bytes paid for the space check, which the first draft of this section
did not have. The net edit changes bytes only in `$9FA4–$9FAD`, `$9FB1` and
`$9FB3–$9FBA`; `$9FBB` onward is untouched, so **no address moves** and nothing
downstream needs fixing up. The cost is a handful of wasted cycles per
character, irrelevant on a static score screen.

**Two edits proposed here were dropped and are not in the patch.** They are
recorded because the reasoning is still useful, not because they happened:

- `$9F91`, the starting column, was going to be narrowed. It stayed at `$0A`.
  The original draws 7 kanji × 2 = 14 columns from column 10, so 14 8×8
  characters start at the same place and end no further right. There was
  nothing to buy.
- `$9FBB`, the line pitch, was going to drop from 2 rows to 1. It stayed at 2.
  Keeping it means the yaku list occupies exactly the rows it always did, so
  nothing positioned relative to it has to be re-checked. The cost is a blank
  row between names.

### One thing this does *not* break — an earlier claim, retracted

This section used to state that the patch drops the per-character attribute
write, because `$C03C` writes a nametable tile and the attribute write lives in
`$C039`, which the patch stops calling. **That was wrong, and the error was
in the call number.** The original printer calls `$80DA` → `$C03F`, not
`$C039`; the patched one calls `$80D6` → `$C03C` → `$E616`, which sets `$02`
to 3 and falls into `$D939`, which writes the tile to `$2007` and then hits
`JSR $D742` at `$D96A` — the same attribute writer the original reaches by the
longer route `$C03F` → `$E6AE` → `$D9DE` → `$DA3E`.

So the attribute write survives, and the palette is not inherited from whatever
the score window happened to hold. `tools/test_printer.py` checks it on every
drawn tile: palette 3, on both the original and the patched render. Six
attribute writes instead of two, because two 8×8 characters share an attribute
quadrant and `$D742` read-modify-writes each one.

The advice that followed from the wrong claim — set the window's attributes
once when it is drawn — is not needed and is not in the patch.

### Then the strings

With the printer converted, each of the 62 pointers can aim anywhere. Options,
in increasing order of effort:

1. **In place** — English names in 8×8 are 3–5× longer than the kanji they
   replace, so almost nothing fits in its original slot. Not viable except for
   the shortest.
2. **Relocate the table** into the tail of bank 04, repointing all 62. Simple,
   but bank 04 has limited slack.
3. **Relocate into free space** (banks 08–0E). The printer reads through
   `($0D),Y` from whatever bank is mapped at `$8000`, and bank 04 must stay
   mapped while `$9F7A` runs — so this needs a bank-switch dance or a copy into
   RAM first. More work, effectively unlimited room.

Start with option 2. Measure the score window's width before writing a single
English name — it sets the character budget, and it is cheaper to discover the
limit now than after 80 translations.

**Outcome:** option 2, and the budget turned out to be the binding constraint.
The Japanese table is 354 bytes and the English script wants 484, so option 2
on its own reaches 47 of 62 names. Collapsing the 53 unrolled call-site blocks
into a loop (`tools/refactor.py`) frees 2621 more and finishes all 62. The
character budget is 14, the width the original already draws. See `BUILD.md`.

## 4. Corrections

### To earlier documents

Both amended in place:

- `PHASE1-TEXT-ENGINE.md` — `$EB19` was called a probable name-length table.
  It is a palette table.
- `KANJI-TABLE.md` — same correction; the walker item is now resolved.

### To this document

- §3 said the patch drops the per-character attribute write. It does not; the
  claim named `$C039`, a stub the printer never called. Corrected above, and
  the wrong version is gone rather than left standing next to its retraction —
  a correction printed beside the error still leaves the error there to be
  copied.
- §3's table of six byte edits was a proposal, not the patch. Two of its rows
  (`$9F91` starting column, `$9FBB` line pitch) were never applied, and the
  shipped code gained a space check the table did not have. Replaced with the
  bytes `build.py` actually writes, with the dropped rows marked as dropped.

The Phase 1 claim that "there is no script and no string printer" also needs
its final shape: it is true of **menu and UI text**, which really is unrolled
per-character code, and false of the **yaku names**, which have both a table
and a printer. The two subsystems need completely different treatment.

## 5. Next

Done since this document was written:

- ~~Measure the score window and draft English names to fit.~~ 14 characters,
  all 62 written — `script/yaku-en.txt`.
- ~~Apply the `$9F7A` patch and confirm one yaku renders in Latin.~~ Applied;
  all 62 verified glyph by glyph in `tools/test_printer.py`, against the ROM's
  own font rather than by eye.

Still outstanding:

1. **Runtime CDL pass** in Mesen — still the thing that validates all of this
   against a running game and separates real UI strings from false positives in
   `string-inventory.csv`. **Nothing here has run on hardware or in an
   emulator.**
2. **Find the ドラ pointer computation.** Unresolved. `--use-dora-block` reuses
   those 81 bytes on the strength of *no reference found*, which is weaker than
   *no reference exists* — and with `--refactor` the flag buys nothing, so
   there is no longer a reason to take the risk.

## Files

- `yaku-callsites.csv` — 62 call sites with exact patch offsets
- `yaku-names.csv` — 90 enumerated strings
- `jangou.tbl`, `jangou-kanji.tbl` — character tables
- `string-inventory.csv` — UI text triage (contains false positives)
