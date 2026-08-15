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
$9FA7   JSR $80DA               ; -> $C039  draw 2x2 + set attribute
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
this a same-length edit:

| CPU | PRG off | File off | Before | After | Effect |
|---|---|---|---|---|---|
| `$9FA4` | `$11FA4` | `$11FB4` | `20 CA 80` | `20 C6 80` | `$C027` → `$C024`, 8×8 glyph |
| `$9FA7` | `$11FA7` | `$11FB7` | `20 DA 80` | `20 D6 80` | `$C039` → `$C03C`, single tile |
| `$9FAC` | `$11FAC` | `$11FBC` | `E6 0C E6 0C E6 0C` | `EA` × 6 | slot base += 1, not 4 |
| `$9FB4` | `$11FB4` | `$11FC4` | `E6 00` | `EA EA` | column += 1, not 2 |
| `$9F91` | `$11F91` | `$11FA1` | `0A` | narrower | starting column |
| `$9FBB` | `$11FBB` | `$11FCB` | `E6 0B E6 0B` | `E6 0B EA EA` | line pitch 1 row, not 2 |

(File offsets include the 16-byte iNES header. Bank 04 begins at PRG `$10000`.)

Every change is same-length or NOP-padded, so **no address moves** and nothing
downstream needs fixing up. The cost is a handful of wasted cycles per
character, which is irrelevant on a static score screen.

### One thing this breaks, deliberately

`$C03C` writes a nametable tile but **not** an attribute byte — the attribute
write lives in `$C039`, which we are dropping. English yaku text will inherit
whatever palette the score window's attribute bytes already hold. That is
probably fine, but verify it on screen; if the text comes out the wrong colour,
set the window's attributes once when the window is drawn rather than
reintroducing a per-character attribute write.

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

## 4. Corrections to earlier documents

Both amended in place:

- `PHASE1-TEXT-ENGINE.md` — `$EB19` was called a probable name-length table.
  It is a palette table.
- `KANJI-TABLE.md` — same correction; the walker item is now resolved.

The Phase 1 claim that "there is no script and no string printer" also needs
its final shape: it is true of **menu and UI text**, which really is unrolled
per-character code, and false of the **yaku names**, which have both a table
and a printer. The two subsystems need completely different treatment.

## 5. Next

1. **Runtime CDL pass** in Mesen — still outstanding, still the thing that
   validates all of this against a running game and separates real UI strings
   from false positives in `string-inventory.csv`.
2. **Find the ドラ pointer computation** so the 18 dora entries survive
   repointing.
3. **Measure the score window** and draft English yaku names to fit.
4. **Apply the `$9F7A` patch** and confirm one yaku renders in Latin. That is
   the proof-of-concept that de-risks the whole project.

## Files

- `yaku-callsites.csv` — 62 call sites with exact patch offsets
- `yaku-names.csv` — 90 enumerated strings
- `jangou.tbl`, `jangou-kanji.tbl` — character tables
- `string-inventory.csv` — UI text triage (contains false positives)
