# Jangou (Japan) — Phase 1: Text Engine Trace

Static analysis of `Jangou (Japan).nes`. Every address below was derived by
disassembly; the character table was verified by decoding known strings and
matching them against the code that prints them.

**ROM identity** — SHA-1 `e1de1fa7a7bbac0315f604beac74a6e296b89078`,
headerless CRC32 `0973F714`, 262,160 bytes.
Everything here is valid only for this exact image.

---

## 1. Cartridge and bank layout

| | |
|---|---|
| Mapper | MMC1 (iNES 1), SNROM-class |
| PRG | 256 KB (16 × 16 KB) |
| CHR | **none — 8 KB CHR-RAM** |
| WRAM | 8 KB battery-backed at `$6000` |
| Mirroring | Horizontal |

Bank roles, inferred from each bank's leading `4C xx xx` trampoline table and
confirmed by the MMC1 write sites:

| Banks | Role |
|---|---|
| **00, 01** | Font / tile graphics. Mapped at `$8000` on demand. |
| **02–05** | Game code + string tables. Switched at `$8000`. |
| **06, 07** | Title screen art (Orpheus Industries / 1990 Victor Musical Industries). |
| **08–0E** | **Dead.** Duplicate builds of the fixed bank. |
| **0F** | Fixed bank at `$C000`. Text engine, vectors, trampoline table. |

### Free space: ~112 KB

Banks 08–0D are byte-identical to one another; bank 0E differs from 0F by two
bytes. Decisively: **all of banks 08–0F carry trampoline tables targeting
`$Cxxx`**, so they are linked to run at `$C000` — and only one bank can occupy
`$C000`, which is bank 0F. Banks 08–0E therefore cannot be usefully executed
and are leftover padding.

This is very strong static evidence, not proof. Two MMC1 switch sites
(`$C268`, `$C287`) take a *computed* bank number, so confirm with a runtime
Code/Data Logger pass before relying on the space.

---

## 2. The glyph pipeline

There is no tilemap of pre-rendered text. Because the cart has **CHR-RAM**, every
character on screen is copied from PRG into video RAM at the moment it is drawn,
then referenced by nametable index. Three layers:

### Layer 1 — `$D55E`, the tile uploader

```
in:  zp $00/$01 = source address of a 16-byte glyph (in the font bank)
     zp $02     = destination CHR-RAM tile slot (0-255)

  - writes MMC1 PRG register via 5 serial stores to $F000, selecting bank 00
  - VRAM address = slot * 16, set through $2006
  - copies 16 bytes to $2007
```

### Layer 2 — glyph fetch

The font is three 4 KB pages, visible at `$8000 / $9000 / $A000` while bank 00
is mapped.

**8×8 characters** — `$E62E`, reached via trampoline **`$C024`**:

```
glyph address = $A000 + (code * 16)
```

Equivalently **font tile index = `$200` + code**. One tile, one call.

**16×16 kanji** — `$E6E7`, reached via trampoline **`$C027`**:

```
code $00-$3D  -> page $8000
code $3E-$77  -> page $9000
code $78-$93  -> page $A000
tile index     = [$E952 + code]        ; translation table
uploads 4 tiles at +$00, +$10, +$100, +$110  (2x2 in a 16-tile-wide sheet)
into CHR-RAM slots $04 .. $04+3
```

The `$E952` table steps `00 02 04 06 08 0A 0C 0E / 20 22 24 …` — eight glyphs
per sheet row, two tiles wide, `$20` tiles per row. Code `$00` maps to `$6E`,
a blank.

### Layer 3 — the drawing call

```
zp $00 = column       zp $02 = character code
zp $01 = row          zp $04 = CHR-RAM tile slot

JSR $C024   ; cache the glyph into slot $04
JSR $C03C   ; write slot $04 into the nametable at ($00,$01)
```

`$C03C` → `$E616`. Banks 02–05 reach all of these through local re-export
stubs (e.g. bank 05 `$803D` → `$C024`, `$8041` → `$C03C`).

---

## 3. Character encoding

`jangou.tbl` holds the full 8×8 map. Layout:

| Codes | Contents |
|---|---|
| `$50–$69` | **A–Z** |
| `$6A–$6F` | `*` `-` `!` `?` `(` `)` |
| `$70–$73` | `<` `>` `/` `~` |
| `$74–$7D` | `1`–`9`, `0` |
| `$7E–$7F` | `X`, `・` |
| `$80–$AF` | hiragana あ…ん, `、` `。` |
| `$B0–$DC` | katakana ア…ン |
| `$E0+` | dakuten, handakuten, small kana |

Dakuten are **not** part of a character's code — they are separate combining
glyphs, so が is written as か + ゛.

16×16 kanji use a **separate** code space, `$00–$93` (148 glyphs), resolved
through `$E952`. The kanji set is entirely mahjong vocabulary — 国士無双,
立直, 平和, 清一色, 河底撈魚 and so on.

### Verified decodes

| Location | Bytes | Decoded |
|---|---|---|
| bank 05 `$9ED2` | `70 a3 87 9e ad 71` | `<やくまん>` — 〈役満〉 *yakuman* |
| bank 05 `$ABB1` | `70 89 8b ad 86 aa 87 71` | `<こしんきろく>` — 〈個人記録〉 *personal record* |

The first was confirmed end-to-end: the routine at bank 05 `$9B39` reads
`$9ED2`, `$9ED3`, `$9ED4` … and feeds each byte to the glyph loader.

---

## 4. The problem: there is no script, and no string printer

This is the finding that shapes the whole patch.

Text is emitted **imperatively, one character per unrolled code block**. The
printer for `<やくまん>` looks like this, repeated verbatim for every glyph:

```asm
$9B41  LDA $9ED2      ; '<'
$9B44  STA $02
$9B46  LDA #$EE
$9B48  STA $04
$9B4A  JSR $803D      ; cache glyph
$9B4D  JSR $8041      ; draw it
$9B50  INC $00        ; advance column
$9B52  LDA $9ED3      ; 'や'
$9B55  STA $02
$9B57  INC $04
$9B59  JSR $803D
$9B5C  JSR $8041
$9B5F  INC $00
...
```

Roughly **9–11 bytes of 6502 per character on screen.** There is no loop, no
pointer table, no terminator handling — the string bytes sit in a table, but
each one is fetched by its own hardcoded absolute `LDA`.

Scale of it:

- **882** `LDA #imm / STA $02` sites — characters hardcoded as immediates.
- **~20** `LDA $xxxx,X / STA $02` sites — the only genuinely table-driven text.
- **3,557** byte-runs in code banks 02/03/04/05/0F that decode as ≥3 valid
  characters, totalling 12,449 characters — see `string-inventory.csv`.

**Treat that last number as an upper bound.** It comes from a byte-range
heuristic across code banks, and opcode bytes frequently fall inside the
charset range, so the inventory contains real false positives. It is a
triage list, not a script dump.

### Important qualification (added after the Phase 2 kanji pass)

The above holds for **menu and UI text**. It does **not** hold for the yaku
names, which are the densest and most player-visible Japanese in the game:
those live in a clean, `$00`-terminated string table at bank 04 `$9E18`
(80 entries), with a second stats table at bank 05 `$ABB8` (10 entries).
Those 90 strings are a conventional dump-and-reinsert job. See
`KANJI-TABLE.md`.

### What this means

For UI text you cannot dump, translate, and reinsert — the conventional
pointer-table workflow does not apply there, because there are almost no
pointers.

The clean fix — and it is genuinely the low-taste-to-high-taste move — is to
**write the string printer the original developers didn't**:

```asm
; JSR print_string
;   .word string_addr      ; inline operand, pulled off the stack
;   ; string: length-prefixed or $00-terminated, codes per jangou.tbl
```

Put it in free space, then convert each unrolled site into a `JSR` plus a
pointer. Every converted site *recovers* about 8 bytes per character, so the
conversion pays for itself immediately and English text gets to be longer than
the Japanese it replaces. Combined with banks 08–0E, space is a non-issue.

Convert incrementally, one screen at a time — each conversion is independently
testable and independently revertable.

---

## 5. What Phase 2 needs

1. **Runtime confirmation.** Mesen Code/Data Logger over a full session:
   proves which banks execute, and separates the real strings from the
   false positives in `string-inventory.csv`.
2. ~~**Kanji decode table.**~~ **Done** — 135 codes (`$00–$86`) mapped and
   verified. See `jangou-kanji.tbl`, `yaku-names.csv`, `KANJI-TABLE.md`.
3. ~~**Width/length table `$EB19`.**~~ **Resolved, and my guess was wrong.**
   `$EB19` is a **palette/attribute table for mahjong tile graphics** — nothing
   to do with yaku names. See `YAKU-PRINTER.md`.
4. **The `print_string` routine**, and the first converted screen as a
   proof of concept.

## Files

- `jangou.tbl` — 8×8 character table
- `string-inventory.csv` — candidate string runs (bank, CPU address, PRG offset, decode)
- `PHASE1-TEXT-ENGINE.md` — this document
