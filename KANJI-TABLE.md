# Jangou — 16×16 Kanji Code Table

Phase 2 of the text-engine work. All 135 kanji codes resolved, rendered, and
verified against the game's own yaku name table.

Companion files: `jangou-kanji.tbl`, `yaku-names.csv`.
Prerequisite reading: `PHASE1-TEXT-ENGINE.md`.

---

## 1. How a kanji code becomes pixels

Kanji use a **separate code space** from the 8×8 charset in `jangou.tbl`.
The resolution path, from `$E6E7` in fixed bank 0F:

```
code $00-$3D  ->  font page $8000   (PRG $0000)
code $3E-$77  ->  font page $9000   (PRG $1000)
code $78-$86  ->  font page $A000   (PRG $2000)

tile_index = [$E952 + code]                 ; translation table
glyph_addr = page_base + tile_index * 16

4 tiles uploaded, in this order, into CHR-RAM slots $04..$04+3:
    +$000  top-left        +$010  top-right
    +$100  bottom-left     +$110  bottom-right
```

The `+$100` step is 16 tiles, so the font is a **16-tile-wide bitmap sheet**
and each kanji is a 2×2 block within it. The `$E952` values step
`00 02 04 … 0E`, then jump to `20`, then `40` — eight kanji per sheet row,
`$20` tiles per row.

**Valid code range is `$00–$86`.** From `$87` upward the table values become
consecutive pairs (`5A 5B`, `4A 4B`, …) that index the 8×8 charset region;
rendered as kanji they are garbage. `$87+` is not kanji.

## 2. The table

`jangou-kanji.tbl` has all 135. Grouped by purpose:

| Codes | Contents |
|---|---|
| `$00` | blank — **also the string terminator** |
| `$01–$0A` | 一 二 三 四 伍 六 七 八 九 十 |
| `$0B–$14` | full-width １ ２ ３ ４ ５ ６ ７ ８ ９ ０ |
| `$15–$3D` | yaku vocabulary — 国 士 無 双 緑 子 了 上 口 正 対 々 暗 刻 老 頭 元 立 平 和 搶 槓 開 花 海 撈 河 月 清 混 純 門 前 么 同 大 小 荘 満 摸 流 |
| `$3E–$4E` | 終 人 振 切 不 天 気 通 貫 直 断 盃 嶺 底 魚 帯 色 |
| `$50–$65` | 栄 聴 親 牌 面 家 東 南 西 北 風 局 役 倍 飜 自 発 散 塔 蓮 宝 燈 |
| `$68–$75` | 地 喜 字 跳 算 全 順 白 發 中 符 連 打 場 |
| `$76–$7A` | large katakana ド ラ ダ ブ ル (for ドラ / ダブル立直) |
| `$7B–$86` | 種 倒 荒 放 銃 副 落 数 半 縛 り 総 |
| `$4F`, `$66`, `$67` | **blank slots — glyphs never drawn** |

Note `$00` serves double duty as blank glyph and terminator, so a kanji string
can never contain an embedded space. Note also `$85` is a large hiragana り,
the only kana in the kanji space besides ドラ/ダブル.

### Two readings worth flagging

- **`$6A` = 字, not 宇.** The glyphs differ only in the element under 宀, and
  at 16×16 they are nearly identical. Context settles it: the string at
  `$9ECC` is `6A 01 4E`, and 字一色 (*tsuuiisou*) is a real yaku while
  宇一色 is not.
- **`$5E` = 飜** (*han*), used by the score display. Confirmed by shape, not
  by a string — it does not appear in the yaku table.

## 3. Verification

The table was not read off a font sheet and trusted. It was checked by
searching the ROM for the byte sequences that known yaku *must* spell, then
confirming those sequences land inside a coherent table. Every probe hit:

| Yaku | Expected bytes | Found |
|---|---|---|
| 国士無双 | `15 16 17 18` | bank 04 `$9EEC` |
| 立直 | `26 47` | bank 04 `$9E1E` |
| 清一色 | `31 01 4E` | bank 04 `$9EBA` |
| 大三元 | `38 03 25` | bank 04 `$9F03` |
| 断么九 | `48 36 09` | bank 04 `$9E2C` |
| 対々和 | `1F 20 28` | bank 04 `$9E98` |

The hits are contiguous and terminator-separated, which is what proves this
is a real table rather than a coincidence of opcode bytes.

## 4. The yaku name table — bank 04 `$9E18`, 80 entries

`$00`-terminated, sequential, no pointer table. Full listing in
`yaku-names.csv`. It covers the entire scoring vocabulary: from ダブル立直
and 門前清摸和 through the limit hands (国士無双十三面, 九蓮宝燈, 大四喜,
四槓子) and on to ドラ１–ドラ１８ and the limit labels 満貫 / 跳満 / 倍満 /
三倍満 / 役満.

Several yaku appear twice — once bare, once with 門前 appended (三色同順 /
三色同順門前, 清一色 / 清一色門前). The engine picks by concealment, so each
needs its own English string; they are not duplicates to be deduplicated.

A second table at **bank 05 `$ABB8`** holds 10 record-screen stat labels:
摸和, 栄和, 放銃, 聴牌, 不聴, 立直, 副落, ドラ数, 白發中. Entry 0 (`71`, 中)
is a single byte and may be a fragment of the preceding data rather than a
real entry — check it at runtime before touching it.

### An original-game bug worth inheriting deliberately

Two entries begin with the blank codes `$66 $67`:

```
index 46   $9EE5   66 67 09 63 64 65    □□九蓮宝燈
index 42   $9ED4   04 21 22             四暗刻   (identical to index 41)
```

Index 46 is plainly meant to be **純正九蓮宝燈** and index 42 **四暗刻単騎** —
the "pure" and "single-wait" variants. The distinguishing kanji were never
drawn, so the original ships showing two blank tiles and a duplicate. In
English this evaporates for free: "PURE NINE GATES" and "SUUANKOU TANKI" are
just different strings. Worth listing in the patch notes.

## 5. What this changes about the patch plan

`PHASE1-TEXT-ENGINE.md` concluded there was no script to dump. That stands for
menu and UI text, but **not** for these 90 strings, which are the most
player-visible Japanese in the game and are a completely conventional
table job:

- They are terminated, so lengths are not fixed.
- They sit in banks 04/05 with the rest of the game code, but nothing stops
  you relocating the whole table into free space and repointing — there is no
  pointer table to fix up, only the code that walks the table.
- English yaku names are much longer than 2–6 kanji ("ALL SIMPLES",
  "NINE GATES", "THIRTEEN ORPHANS"), and they must render in the **8×8**
  charset, not the 16×16 one. That is a different draw call
  (`$C024` instead of `$C027`, one tile per character instead of four).

That last point is the real work item: the scoring display currently advances
its cursor two columns per kanji and burns four CHR-RAM slots per character.
Feeding it 8×8 English means changing the step and the slot allocation at the
call sites that render yaku. Budget for that before translating a single name.

## 6. Next

1. **Runtime CDL pass** in Mesen — still outstanding from Phase 1, still the
   thing that separates real strings from false positives in
   `string-inventory.csv`.
2. ~~**Locate the yaku table walker**~~ **Done** — it is `$9F7A` in bank 04, a
   proper loop-based `$00`-terminated printer taking a pointer in zp `$0D/$0E`.
   `$EB19` turned out to be unrelated: it is a palette table for mahjong tile
   graphics, not a name-length table. See `YAKU-PRINTER.md`.
3. **Draft English yaku names** to a column width the score window can hold —
   measure the window first.

## Files

- `jangou.tbl` — 8×8 character table
- `jangou-kanji.tbl` — 16×16 kanji table (this document)
- `yaku-names.csv` — 90 enumerated strings, both tables
- `string-inventory.csv` — UI text triage list (contains false positives)
- `PHASE1-TEXT-ENGINE.md` — engine trace
