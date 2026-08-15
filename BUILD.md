# Building the Jangou English patch

```bash
python tools/build.py --refactor
python tools/verify_patch.py
python tools/test_refactor.py
python tools/test_printer.py
```

**All 62 yaku names are in English.**

Output lands in `build/`: a patched `.nes` plus `.bps` and `.ips` patches.
Your ROM is never modified — the build reads it, verifies its SHA-1, and
writes elsewhere.

Every script above exits 0 on success and non-zero on failure, in any shell.
That second half was not free: these tools print Japanese yaku names, Python
takes stdout's encoding from the locale, and on Windows that is UTF-8 under
PowerShell but cp1252 under Git Bash and cmd. `build.py` used to raise
`UnicodeEncodeError` on its closing summary — *after* writing all three
artifacts — so the same command produced a correct `.bps` and a traceback
depending on which terminal you ran it from. `tools/utf8io.py` fixes stdout and
stderr for the process; **every entry point in `tools/` imports it before its
first `print`.** If you add a script there, import it too.

## What this patch does

Converts the yaku name display from 16×16 kanji to the 8×8 Latin charset the
game already contains, and replaces the Japanese yaku names with English.

### What each flag costs and buys

Every number in this document comes from one of these four runs. `build.py`
prints all of them itself — if a figure here disagrees with the build, the
build is right.

| flags | string pool | in English | blank | bytes changed |
|---|---|---|---|---|
| *(none)* | 273 | 37 of 62 | 25 | 360 |
| `--use-dora-block` | 354 | 47 of 62 | 15 | 448 |
| `--refactor` ← documented | 2894 | **62 of 62** | 0 | 3143 |
| `--use-dora-block --refactor` | 2975 | **62 of 62** | 0 | 3206 |

The jump from 448 to 3143 bytes is `--refactor` rewriting the 53 unrolled
call-site blocks in place: 3021 of those bytes are that one span.

**Nothing moves** in the sense that matters: no address outside the rewritten
span `$8F51–$9B1D` shifts, `$8F51` stays the entry point, and the engine patch
is byte-for-byte the same length as the code it replaces, so no branch, jump or
pointer anywhere else needs fixing up.

### Why the documented command is `--refactor` alone

It used to be `--use-dora-block --refactor`, which was wrong on its own terms.
Both flags are opt-in because each carries a risk; `--use-dora-block`'s own
documentation says to confirm in an emulator first, and no emulator has run.
Turning both on in the headline command handed the reader the risk while the
warning sat forty lines below.

The two are not comparable, and the table shows why:

- `--refactor` is **verified**. `refactor.check_safety()` runs the entry-point
  and branch analysis on every build; `test_refactor.py` proves the loop is
  behaviour-identical to the 53 blocks across 113 flag patterns — exhaustive
  over every block — comparing full CPU and memory state, not just the screen.
  It is also what delivers 62 of 62.
- `--use-dora-block` is **unverified and buys nothing**. Compare rows three and
  four: 62 of 62 either way. Its 81 bytes only ever mattered when the pool was
  354. It is the one edit in this patch that touches bytes which could not be
  *proven* dead, in exchange for zero names.

So it is off by default. The flag still exists, and if you have confirmed in an
emulator that dora names never appear, it does no harm.

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
| `tools/rom.py` | **the cartridge map** — header size, bank geometry, windows, the source hash, paths |
| `tools/build.py` | the build |
| `tools/verify_patch.py` | spec-complete BPS/IPS reader; applies both patches back |
| `script/yaku-en.txt` | **the translation. This is the file you edit.** |
| `yaku-callsites.csv` | 62 call sites and their patch offsets (generated, do not hand-edit) |
| `build/` | output, safe to delete |

## Editing the translation

`script/yaku-en.txt` is `[*]index | japanese | english`, one per line.

- **Uppercase only.** The font has A–Z, 0–9, space, and `* - ! ? ( ) < > / ~`
  There is no lowercase, comma, colon, apostrophe **or period** — `$7F` looks
  like one but is `・`, a centred dot. The build rejects anything else by name
  and line number, and prints the usable set.
- **The character set is [`jangou.tbl`](jangou.tbl)**, not a list in the
  Python. `build.py` derives its encoder from that file, so the two cannot
  disagree — they used to, which is how `.` came to mean `・`. Editing the
  table changes what the build accepts. The 94 kana it also defines are
  accepted too; they are simply not useful for English names.
- **14 characters maximum.** That is the width the original already draws
  (国士無双十三面 = 7 kanji × 2 columns), so it is known safe. Raise `MAX_LEN`
  in `build.py` only after measuring the score window on screen.
- **`*` marks high priority.** Space runs out; starred lines are packed first.
- **One line per index, and every index needs a line.** Both are enforced now.
  A duplicate index used to be silent data loss — `english[idx] = …` with no
  membership test, so the second line replaced the first, the build exited 0
  and the wrong name went into the ROM. The file's own header says *Do not
  renumber* and nothing checked it, including `test_printer.py`, which derives
  its expected strings from the same parser and would have confirmed the
  broken build. A *deleted* line was equally quiet: the name simply vanished.

  ```
  yaku-en.txt:26: index 1 is already used on line 25 ('RIICHI').
  yaku-en.txt:25: '*x' is not an index - expected a number, optionally prefixed with * for priority
  yaku-en.txt:25: expected 'index | japanese | english', found 2 fields
  ```

  A missing line is not fatal to the build — a partial translation is a
  legitimate thing to have — but `build.py` now lists it under
  **`NO SCRIPT LINE`** and `test_printer.py` fails on it, so it cannot pass
  unnoticed.

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

### Is it safe to overwrite that span?

This section used to be one sentence: *every branch inside the replaced span
lands on a block boundary, and the only references into it from bank 04 or the
fixed bank are three jumps to `$8F51`.* **No code did any of that**, and the
description was wrong — one of the three is a `JMP` and the other two are
conditional branches. "I checked once, by hand, and wrote the number down" is
not a proof, and the number was not even right.

`refactor.check_safety()` now runs on every build and `apply()` refuses to
write if the result differs from what is recorded:

```
safety: every block branch lands on a block boundary; 3 paths into the span, all to $8F51
          bank 04  $8EF7  JMP   $8F51
          bank 04  $8F16  BEQ   $8F51
          bank 04  $8F2E  BEQ   $8F51
        230 raw word pairs in banks 04/0F address the span - the noise floor
        for a computed entry, which no static scan can rule out.
```

- **Branches inside the span** — exact, and proved for all 53 blocks at once
  rather than scanned. `extract()` matches all 57 bytes of every block against
  the template, so instruction boundaries are *known*, not guessed. The
  template holds exactly one branch opcode, every varying byte is the operand
  of a fixed opcode and so can never decode as an instruction, and the branch's
  displacement is checked to equal 57 — the next block's first byte.
- **Paths in from outside** — the scan reads every byte offset in bank 04 and
  bank 0F rather than decoding, so it over-reports rather than under-reports; a
  real entry cannot hide from it. Every hit is accounted for by name in
  `ENTRIES`, with the reason it is safe. A hit that is not listed fails the
  build; a listed entry the scan stops finding also fails it, so the table
  cannot go stale. Only bank 04 and bank 0F are in scope — a `$9xxx` target in
  bank 02 addresses bank 02's own `$9xxx`, not this span.

One hit is a false positive and is recorded as such: bank 0F `$DCD0` looks like
`JMP ($906C)`, but the real code is `$DCCF CMP $6C6C,X` (`DD 6C 6C`) followed by
`BCC +3` (`90 03`) — the scan is reading operand bytes. `$6C6C` is WRAM, the
same yaku flag area every block indexes, which is what makes the decode sound.

**What this does not prove.** It cannot rule out a *computed* jump into the
span — one that arrives through a pointer no static scan can follow.
`count_word_refs()` measures the noise floor for that: **230** raw
little-endian word pairs in banks 04 and 0F address `$8F51–$9B1D`. That number
does not go to zero and no static argument brings it there. It is the same
class of uncertainty flagged for `--use-dora-block`, it was not flagged here
before, and it is now: only the outstanding runtime CDL pass can close it.

`test_refactor.py` breaks the analysis three ways and requires each to fail:

```
an unaccounted path into the span  caught
ENTRIES gone stale                 caught
block BEQ misses the boundary      caught
```

### Which slot belongs to which yaku

Repointing has to put each English string in the array slot belonging to *that
yaku*. Get it wrong and every name still renders perfectly — on the wrong hand.

This used to rest on a comment: *csv index == block index for 0..55, so
slot = i - 2*. Nothing checked it, and the render test repeated the same
arithmetic to read the pointers back, so it would have agreed with a broken
build and reported success.

`refactor.slot_for_callsite()` now **derives** the slot from the call site's
own recorded offset, and `refactor.check_callsite_map()` verifies the whole
mapping against the **unpatched** ROM before anything is written:

- every site inside the span lands exactly on a block's pointer field
- those sites cover slots 0–52 once each — no gaps, no collisions
- the pointer the csv recorded equals the pointer that block actually holds

The third is the one that matters, because it ties row to block through a
value neither side computes. All 53 original pointers are distinct, so any
mix-up changes at least two of them. `test_printer.py` runs the same check and
then breaks the mapping five ways to prove it fails:

```
=== call site -> loop slot map ===
  53 rows map onto slots 0-52, one each, and every row's csv
  pointer matches the pointer its block actually holds.
    two rows pointer-swapped         caught
    one row shifted a block along    caught
    offsets one byte off the field   caught
    a row moved outside the span     caught
    lo/hi pair spacing wrong         caught
```

Rotating a built ROM's `tbl_lo`/`tbl_hi` by a single slot — the exact symptom,
every name one yaku out — now fails the render sweep by name:

```
idx 38 十三不塔: ROM holds 'RENHOU', script says 'JUUSAN'
idx 39 人和: ROM holds 'TSUIISOU', script says 'RENHOU'
```

### Proving it is equivalent

```bash
python tools/test_refactor.py
```

Runs the whole yaku-list routine on the original ROM and on a refactor-only
build — no string changes, so output must be identical — driving it with
synthetic flag bitfields.

**Coverage is exhaustive**, not sampled. It used to be twelve cases from
`random.sample()` on a fixed seed: three of the fifty-three singletons, never
more than six flags at once. Exhaustive costs 2.3 seconds, so there was no
reason for sampling.

```
  all off                  1 cases  all equivalent
  every block alone       53 cases  all equivalent
  every adjacent pair     53 cases  all equivalent
  windows of 10            6 cases  all equivalent

  113 cases, 1374519 instructions executed
```

The **ceiling of 10 simultaneous yaku is the game's, not the test's.** Each
name takes two rows, so the eleventh fills the score window and the routine
hands off to an NMI-synchronised update — `$E166` spins on bits 0–1 of `$3F`
waiting for an NMI the harness does not run. That code is in bank 0F and the
refactor never touches it. Cases above the ceiling are reported as **skipped**,
never as passed.

### What is compared

Not just pixels. At `$9B1E` and again eight instructions later: `A X Y P S`,
zero page, the live stack, RAM, WRAM, CHR-RAM, the nametable, and the complete
ordered PPU write sequence.

Three registers legitimately differ at `$9B1E`, each recorded in
`ALLOWED_AT_END` with its reason:

| | why it differs | why it is harmless |
|---|---|---|
| `X` | loop leaves its iteration count 53; the unrolled path left block 54's flag-group immediate `$07` | block 55's 2nd instruction is `LDX #$07` — dead at +2 |
| `P` | the loop's `CPX #53` sets carry; the unrolled path left it clear | block 55 does `CLC` before its `ADC`; `LDY`/`LDX` overwrite N and Z — dead at +4 |
| `A` | only when block 54 scores: the loop's `PLA`/`TAX` leaves the pushed iteration index `$34` in A | block 55's 3rd instruction is `TXA` — dead at +3 |

**Those reasons are not taken on trust.** Both machines are stepped past
`$9B1E` and required to agree on *everything* — that is what shows the
divergence is dead, rather than an argument that it ought to be. A negative
control removes block 55's `LDX #$07` and requires the test to fail, since that
one instruction is the entire reason the `X` difference is harmless.

The stack *below* `S` is excluded from the comparison. The two versions really
do leave different litter there — the loop pushes `X` around its `JSR`s and the
unrolled blocks never did — and bytes below the stack pointer are dead by
definition of the 6502.

```
  negative controls - each of these must be caught:
    block 55 stops reloading X   caught
    one tbl_msk entry rotated    caught
    one tbl_han bit flipped      caught
    one tbl_col bit flipped      caught
```

The `A` divergence is new. The old test *did* include block 54 as a case, but
compared only the nametable, CHR-RAM and PPU writes — a register difference had
no way to show up. Exhaustive coverage and full-state comparison were both
needed to find it.

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

All 62 English names want 484 bytes, so **without `--refactor` there is not
enough room**: 273 bytes is a 211-byte shortfall, and `--use-dora-block`'s 354
still leaves 130 short.

The build packs what fits and points everything else at a shared empty string,
which is where the 37-of-62 and 47-of-62 rows in the table above come from.
With `--refactor` the pool is 2894 bytes, the shortfall is zero, and nothing is
left blank.

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
screen.

Its 81 bytes decided the outcome only while the pool was 273 bytes: 37 names
without it, 47 with. **Alongside `--refactor` it changes nothing** — 62 of 62
either way. It is the one edit in this patch that touches bytes not proven
dead, in exchange for nothing, so **it is no longer in the documented command**
and the shipped patch does not use it.

## Testing without an emulator

```bash
python tools/test_printer.py
```

`tools/cpu6502.py` implements all 151 documented 6502 opcodes and none of the
undocumented ones — an undocumented opcode raises `IllegalOpcode` rather than
running as a NOP. It counts no cycles: this models behaviour, not timing.
`tools/test_printer.py`
maps the ROM the way MMC1 does, sets up the zero page the way a call site does,
calls `$9F7A` directly, captures every `$2006`/`$2007` write, and reconstructs
CHR-RAM and the nametable into `build/printer-test.png`.

The check is **not** geometry. Asserting that a name lands on one row, in
contiguous columns, inside the screen tells you nothing about *which* letters
were drawn — overwrite a name with eight `Z`s and every one of those properties
still holds. So the test reads the glyphs back out of CHR-RAM and compares them
byte for byte against the ROM's own font:

- **the string** — the pointer is followed into bank 04, the bytes there are
  decoded through the inverse of the encoder, and the result must equal the
  line in `script/yaku-en.txt`. Catches mispacking and mispointing.
- **the glyphs** — for each character, the 16 bytes uploaded to its CHR-RAM
  slot must equal `$A000 + code*16` in PRG bank 00, the exact tile `$E62E`
  is supposed to fetch. Catches anything wrong between the string and the
  screen.
- **the cells** — the drawn nametable cells must be exactly the expected
  columns holding the expected slot numbers, in palette 3, and nothing else on
  the screen.
- **the control** — the same treatment for the **unpatched** ROM, all 62
  names, against the 16×16 kanji sheet: four tiles per character, top-left,
  top-right, bottom-left, bottom-right, byte for byte. This is what makes the
  harness itself trustworthy, and no human has to look at a picture for it.

Then it proves the test can fail, by breaking the ROM six ways and requiring
each break to be caught — including two that leave the geometry perfect:

```
=== negative controls: deliberately broken ROMs must FAIL ===
  one letter changed                 caught  ROM holds 'W-RIQCHI', script says 'W-RIICHI'
  every letter overwritten with Z    caught  ROM holds 'ZZZZZZZZ', script says 'W-RIICHI'
  name truncated by one character    caught  ROM holds 'W-RIICH', script says 'W-RIICHI'
  pointer aimed at the next name     caught  ROM holds 'RIICHI', script says 'W-RIICHI'
  glyph upload NOPed out of $9F7A    caught  CHR slot $80 does not hold the glyph for 'W'
  slot advance NOPed out of $9F7A    caught  row 4 col 11 holds tile $80, expected tile $81
```

Current result, exit code 0:

```
62 of 62 control renders match the ROM's own kanji data
62 of 62 names render exactly the text in script/yaku-en.txt
rightmost column used : 20  (nametable is 32 wide)
highest CHR-RAM slot  : $89 (base $80, must stay under $100)
```

The PNG is still written. It is output, not evidence. Any failure — a name
that does not match the script, a glyph that does not match the font, a blank
name, a negative control that slips through — prints under `FAILED` and the
script exits 1.

One note the test prints rather than fails on: this ROM's font draws `O` and
`0` with byte-identical tiles, so reading a render back as text cannot tell
those two apart. The per-character comparison is against `ENC[ch]` directly
and is exact either way.

### The palette question, settled

An earlier note here said the patch drops the attribute write because it stops
calling `$C039`. **That was wrong, and the mistake was one stub off.** The
original printer calls `$80DA` → `$C03F` → `$E6AE`; `$C039` → `$E79D` is the
mahjong-*tile* drawer from `YAKU-PRINTER.md` §1, which the yaku printer never
called and therefore never stopped calling. `$C03C` → `$E616`, which the
patched printer calls instead, reaches the same attribute writer `$D742`
(`JSR $D742` at `$D96A`) that `$E6AE` reaches via `$D9DE`. The harness
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
2. **Colour.** The patch keeps the per-character attribute write — `$C03C` →
   `$E616` reaches the same `$D742` the original did, and the harness confirms
   palette 3 on every drawn tile. So this should just be right. If it is not,
   the harness is modelling something wrong and that is the bug to chase, not
   the palette. (See *The palette question, settled* above; an earlier version
   of this file claimed the write was dropped and was wrong.)
3. **Spacing.** Characters advance one column, lines two rows. The longest
   names are `CHINITSU-C`, `KOKUSHI-13` and `SANSHOKU-C` at 10 characters,
   ending at column 19 of 32; if anything overflows the window, lower
   `MAX_LEN`.
4. **Blank rows.** With the documented flags, none — all 62 names print. If you
   built without `--refactor`, 15 or 25 print blank depending on
   `--use-dora-block`; `build.py` lists exactly which.
5. **Dora.** Not affected by the documented build, which no longer passes
   `--use-dora-block`. If you added the flag, watch for a hand with dora and
   confirm nothing garbles — that is the one check here that can fail on data
   the analysis could not prove dead.
6. **The refactored yaku list itself.** Score several yaku at once and confirm
   the list is complete, in the right order, with each name on the right hand.
   The harness prints one name at a time, so this is the part of `--refactor`
   that static checking and the 6502 harness both leave open.

## The engine patch

One routine, `$9F7A` in bank 04. 29 bytes, same length as the original:

```asm
$9F9E  LDA ($0D),Y      fetch character
$9FA0  BEQ $9FBB        $00 terminates
$9FA2  STA $02
$9FA4  CMP #$01         space: advance the cursor, draw nothing
$9FA6  BEQ $9FAE
$9FA8  JSR $80C6        -> $C024  upload one 8x8 glyph      (was $C027, 16x16)
$9FAB  JSR $80D6        -> $C03C  write one nametable tile  (was $80DA -> $C03F)
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

## Verifying the patch files

```bash
python tools/verify_patch.py
```

Applying a patch with a reader written to match our own writer proves nothing —
it is one implementation agreeing with itself. This script used to be exactly
that, and said so in its own error message: *"this verifier only handles the
action types build.py emits."* It also skipped the patch's own CRC32 at
`p[-4:]`, which real patchers check.

It is now a **spec-complete reader**, deliberately larger than the writer:

- **All four BPS actions**, including `SourceCopy` and `TargetCopy` with their
  signed relative offsets, and a non-empty metadata block. `build.py` emits
  only `SourceRead` and `TargetRead` and no metadata — so the reader is
  exercised on hand-built patches using everything it cannot produce, including
  a `TargetCopy` that overlaps its own output.
- **All three CRC32s**, source, target and the patch's own.
- **IPS RLE records**, which `build.py` never emits either.
- **A varint cross-check**: the reader's encoder is written from the format
  description and compared against `build.bps_varint` over thousands of values,
  and every value is round-tripped. If the two ever disagree, the build is
  emitting numbers no patcher can read.

Then it corrupts the real patches and requires each to be rejected **for the
stated reason**:

```
bps: magic corrupted         rejected  bad BPS magic b'BXS1'
bps: source CRC wrong        rejected  source CRC mismatch - this patch is for a di
bps: target CRC wrong        rejected  target CRC mismatch - the result is not what
bps: patch CRC wrong         rejected  patch CRC mismatch - the patch file itself i
bps: truncated               rejected  action at patch offset 3581 overruns the dec
bps: a data byte flipped     rejected  target CRC mismatch - the result is not what
bps: declared target size    rejected  produced 262160 bytes, patch declares 262162
ips: magic corrupted         rejected  bad IPS magic b'PXTCH'
ips: EOF removed             rejected  patch ends without an EOF marker
ips: record truncated        rejected  record data truncated
```

The reason matters and nearly went wrong here. The patch CRC covers every other
byte, so on the first attempt *all* of these were caught by that one check and
the source-CRC and target-CRC paths never executed — the controls passed while
testing nothing. Each mutation now re-seals the patch CRC, so the check being
named is the one that fires.

**What this still is not: `beat`, `Flips` or `Lunar IPS`.** It is a second
implementation written from the specification rather than from `build.py`,
which is a real improvement on a mirror and still not an independent tool. To
close the gap:

```bash
flips --apply build/jangou-en.bps "Jangou (Japan).nes" out.nes
sha1sum out.nes    # must match the SHA-1 verify_patch.py prints
```

That is one command and **nobody has run it.**

## The line pitch

`$9FBB` is `INC $0B / INC $0B` — the yaku printer advances two rows per name.
The patch does not touch it, and until the harness was made to drive the whole
routine, nobody had seen what that looks like with one-row-tall glyphs:

```
original  rows drawn: 0, 1, 2, 3, 4, 5, 6, 7, 8, 9
patched   rows drawn: 0, 2, 4, 6, 8
```

16×16 kanji are two rows tall on a two-row pitch, so the original list is
solid. 8×8 Latin is one row tall on the same pitch, so **the English list is
double-spaced.** `test_printer.py` renders both into `build/printer-test.png`.

This was never chosen. `YAKU-PRINTER.md` §3 proposed dropping the pitch to one
row and the proposal was dropped along with the starting-column change; the
recorded reason — the list occupies exactly the rows it always did, so nothing
positioned relative to it needs re-checking — is true, and was written without
anyone looking at the result.

Single-spacing it is one byte pair:

```
$9FBB   E6 0B E6 0B   ->   E6 0B EA EA
```

Five names would then occupy rows 0–4 instead of 0–8, which frees half the
window and changes where everything below the list sits. That is a layout
decision, and it wants an emulator before it wants a patch.

## What the patch does not translate

The yaku printer is one routine. The score screen is not, and **the shipped
result is mixed-script**. Nothing in this repository said so until someone
scanned bank 04 for surviving 16×16 kanji uploads (`JSR $80CA` → `$C027`) and
found twelve.

`test_printer.py` prints them on every run, grouped by what they draw, and
fails if the inventory drifts in either direction:

```
N符 M飜 line       5 sites
    $9D00  fu value, tens digit
    $9D22  fu value, ones digit
    $9D3C  符  (kanji $72)
    $9D5E  han value digit
    $9D78  飜  (kanji $5E)
round indicator  3 sites
    $82A3  round wind, $6013 + $56
    $82C6  hand number digit
    $82DC  局  (kanji $5B)
二飜縛り rule        4 sites
    $82FD  二   $8313  飜   $8329  縛   $833F  り
```

(A thirteenth site, `$A01E`, uploads kanji code `$00` — the blank tile. Not
text, listed as such.)

**The first group is the one that shows.** `$9CF0–$9D83` draws the `N符 M飜`
line immediately above the yaku list, so a scored hand reads `RIICHI` /
`MENZEN-TSUMO` in Latin with `30符 4飜` in Japanese underneath it. The round
indicator draws elsewhere on screen; `二飜縛り` only appears when `$6025 >= 5`.

### Finishing it is not two strings

The tempting fix — repoint `符` and `飜` at Latin strings — makes it worse. The
**digits on that line are 16×16 kanji as well**:

```asm
$9CF4  LDA $6039      ; fu value
$9CF7  SEC / SBC #$5A ; -> kanji code for the tens digit
$9D00  JSR $80CA      ; upload it 16x16
...
$9D52  LDA $6038      ; han value
$9D55  CLC / ADC #$0A ; -> kanji code for the digit
```

Convert `符`/`飜` alone and you get 8×8 letters beside 16×16 numerals on the
same line. The whole line has to move together: five uploads switched to the
8×8 loader, the digits re-derived as `'0'..'9'` in the 8×8 charset rather than
kanji codes, and the column/slot advances (`INC $00` ×2, `INC $0C` ×4 per
glyph) halved — the same edit shape as the yaku printer, but on a routine that
interleaves computed values with literals. That is a real commit, and a
sensible next one; it is not a string change.

## Distribution

Ship `build/jangou-en.bps`. Never distribute the ROM or the patched `.nes` —
the BPS carries a CRC of the source, so it applies only to the correct dump
and refuses everything else.
