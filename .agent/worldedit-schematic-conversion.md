# WorldEdit Schematic 変換手順

## 背景
WorldEdit 7.2.15 (Forge 1.20.1) は Sponge Schematic **v2まで**しか対応していない。
WorldEdit 7.3+ / 7.4+（Paper 等）で作成された `.schem` ファイルは Sponge v3 形式であり、そのままでは読み込めない。

## 変換ツール

専用リポジトリで管理: https://github.com/sudolifeagain/mc-schematic-converter

### ローカル実行
```bash
cd C:\Users\admin\Documents\Github\mc-schematic-converter
PYTHONPATH=src python -m mc_schematic_converter <input.schem> <output.schem>
```

### リモート実行
OCI-mc リポジトリの `convert_v3_v2_fix.py` を使用（単一ファイル版、依存なし）:
```bash
# ローカルからリモートに転送
scp -i ~/.ssh/id_ed25519 convert_v3_v2_fix.py ubuntu@<IP>:/tmp/

# リモートで変換
python3 /tmp/convert_v3_v2_fix.py <input.schem> <output.schem>
```

### 運用手順（Paper → Forge 転送）
```bash
# 1. Paper の schematic を Forge ディレクトリにコピー（v3 原本を保持）
cp /opt/minecraft/paper/plugins/WorldEdit/schematics/<name>.schem \
   /opt/minecraft/forge/config/worldedit/schematics/<name>_v3.schem

# 2. v3→v2 変換
python3 /tmp/convert_v3_v2_fix.py \
  /opt/minecraft/forge/config/worldedit/schematics/<name>_v3.schem \
  /opt/minecraft/forge/config/worldedit/schematics/<name>.schem

# 3. Forge ゲーム内で読み込み・貼り付け
//schem load <name>
//paste
```

## エラーパターン

### "missing a Version tag"
```
java.io.IOException: Schematic file is missing a "Version" tag of type com.sk89q.jnbt.IntTag
```
**原因**: Sponge v3 形式。v3 ではデータが `Schematic` コンパウンドの中にネストされており、WorldEdit 7.2.15 はルート直下に `Version` タグを探すため失敗する。

### "missing a Palette tag"
v3→v2 変換で構造のネストだけ修正し、`Blocks` コンパウンドの中身を展開していない場合に発生。

### "missing a Rotation tag"
```
java.io.IOException: Schematic file is missing a "Rotation" tag of type com.sk89q.jnbt.ListTag
  at com.sk89q.worldedit.extent.clipboard.io.SpongeSchematicReader.readEntities
```
**原因**: v3 のエンティティは `Data` コンパウンド内に `Rotation` を格納するが、v2 リーダーはエンティティのルート直下に `Rotation` を期待する。エンティティの `Data` コンパウンドを展開する処理が必要。

### 額縁がサイレントにスキップされる（エラーログなし）
`//paste -e` でエンティティ付きペーストしても額縁・絵画が配置されない。ログにエラーは出ない。
**原因**: 1.21+ のエンティティNBTは取り付け位置を `block_pos` (IntArray[3]) で格納するが、1.20.1 は `TileX`/`TileY`/`TileZ` (個別Int) を期待する。`block_pos` → `TileX`/`TileY`/`TileZ` の変換が必要。Paper固有タグ（`Paper.SpawnReason` 等）の除去も推奨。

### チェストの中身が空
v3→v2 変換で BlockEntity の `Data` コンパウンドを展開していない、またはアイテム NBT 形式の変換が不足している場合に発生。

## Sponge Schematic バージョン対応

### 仕様バージョン

| Sponge Schematic | 仕様策定日 | DataVersion | Entity | Biome | BlockEntity キー |
|:---:|:---:|:---:|:---:|:---:|:---:|
| v1 | 2016-08-23 | なし | 非対応 | 非対応 | `TileEntities` |
| v2 | 2019-05-08 | あり | 対応 | 2D | `TileEntities` |
| v3 | 2021-05-04 | あり | 対応 | 3D | `BlockEntities` |

### WorldEdit バージョンとの対応

| WorldEdit | デフォルト書き出し | 読み込み対応 |
|:---:|:---:|:---:|
| 7.0.x - 7.1.x | Sponge v1 | v1 |
| **7.2.x** | **Sponge v2** | **v1, v2** |
| 7.3.x | Sponge v3 | v1, v2, v3 |
| 7.4.x | Sponge v3 | v1, v2, v3 |

WorldEdit 7.3+ では `//schem save <name> sponge.2` で v2 書き出しを明示指定可能。

## Sponge Schematic フォーマット差分

### v2（WorldEdit 7.2.15 対応）
```
Root Compound "Schematic"
├── Version (Int) = 2
├── DataVersion (Int)
├── Width, Height, Length (Short)
├── Offset (IntArray)
├── Palette (Compound)          ← ブロックパレット
├── PaletteMax (Int)
├── BlockData (ByteArray)       ← ブロックデータ
├── BlockEntities (List)        ← 各エントリに Id, Pos, Items 等が直接格納
└── Entities (List)             ← 各エントリに Id, Pos, Rotation 等が直接格納
```

### v3（WorldEdit 7.3+ / 7.4+）
```
Root Compound ""
└── Compound "Schematic"
    ├── Version (Int) = 3
    ├── DataVersion (Int)
    ├── Width, Height, Length (Short)
    ├── Offset (IntArray)
    ├── Blocks (Compound)           ← v2 と異なりネスト
    │   ├── Palette (Compound)
    │   ├── Data (ByteArray)        ← v2 の BlockData に相当
    │   └── BlockEntities (List)    ← 各エントリに Data コンパウンドがネスト
    └── Entities (List)             ← 各エントリに Data コンパウンドがネスト
```

## v3→v2 変換で必要な処理

### 1. ルート構造の変換
- v3: `Root("") → Schematic → ...`
- v2: `Root("Schematic") → ...`
- `Schematic` コンパウンドの中身をルートに昇格させ、ルート名を `"Schematic"` に設定

### 2. Blocks コンパウンドの展開
- `Blocks.Palette` → ルート直下の `Palette`
- `Blocks.Data` → ルート直下の `BlockData`（**リネーム必須**）
- `Blocks.BlockEntities` → ルート直下の `BlockEntities`
- `PaletteMax` (Int) を追加（= Palette のエントリ数）

### 3. BlockEntity の Data 展開
v3 の各 BlockEntity:
```
{Id: "minecraft:chest", Pos: [...], Data: {id: "minecraft:chest", Items: [...], ...}}
```
v2 に変換:
```
{Id: "minecraft:chest", Pos: [...], Items: [...], ...}
```
- `Data` コンパウンドの中身を親に展開
- `Data` 内の `id`（小文字）は `Id` と重複するためスキップ

### 4. Entity の Data 展開
BlockEntity と同じ構造。v3 の各 Entity:
```
{Id: "minecraft:item_frame", Pos: [...], Data: {id: ..., Pos: [...], Rotation: [...], ...}}
```
v2 に変換:
```
{Id: "minecraft:item_frame", Pos: [...], Rotation: [...], ...}
```
- `Data` コンパウンドの中身を親に展開
- `Data` 内の `id`（小文字）と `Pos` は親と重複するためスキップ
- **この処理を省略すると "missing a Rotation tag" エラーが発生する**

### 5. エンティティNBTのバージョン変換（MC 1.21+ → 1.20.1）
MC 1.20.5+ でエンティティNBT形式も変更された。特に額縁・絵画などの吊り下げエンティティが影響を受ける。

| フィールド | 1.21+ | 1.20.1 |
|-----------|-------|--------|
| 取り付け位置 | `block_pos` (IntArray[3]) | `TileX`, `TileY`, `TileZ` (個別 Int) |
| 額縁のアイテム | `Item` (1.21+形式) | `Item` (1.20.1形式、count→Count変換必要) |

- **`block_pos` は絶対座標（原本の `//copy` 位置）を格納している**。v2 の `TileX`/`TileY`/`TileZ` はスケマティック原点基準の相対座標である必要があるため、`block_pos` をそのまま変換すると座標不整合で額縁がサイレントにスキップされる（エラーログなし）
- 正しい変換: エンティティの `Pos`（相対座標、List of Double）から `math.floor()` で `TileX`/`TileY`/`TileZ` を導出する
- Paper/Bukkit/Spigot 固有タグ（`Paper.SpawnReason`, `Bukkit.updateLevel`, `Spigot.ticksLived` 等）はForge非互換のため除去

### 6. アイテムNBT形式の変換（MC 1.21+ → 1.20.1）
MC 1.20.5 でアイテムNBT形式が大きく変更された。

| フィールド | 1.21+ (v3) | 1.20.1 (v2) |
|-----------|-----------|-------------|
| 個数 | `count` (Int, type 3) | `Count` (Byte, type 1) |
| コンポーネント | `components` (Compound) | 存在しない（削除する） |
| ID | `id` (String) | `id` (String) — 同じ |
| スロット | `Slot` (Byte) | `Slot` (Byte) — 同じ |

### 7. Version タグの変更
- `Version` を `3` → `2` に変更

## 変換スクリプトの処理フロー
1. gzip 圧縮 NBT を読み込み
2. `Root → Schematic` のネストを解除
3. `Blocks` コンパウンドを展開（Palette, Data→BlockData, BlockEntities）
4. 各 BlockEntity の `Data` コンパウンドを展開
5. 各 Entity の `Data` コンパウンドを展開（Rotation 等を親レベルに）
6. エンティティNBTのバージョン変換（block_pos→TileX/Y/Z、Paper タグ除去、Item 変換）
7. アイテムの `count`→`Count` 変換、`components` 削除
8. `Version` を 2 に変更
8. gzip 圧縮 NBT として書き出し

## 検証方法
```bash
python3 -c "
import gzip, struct

with gzip.open('<file>.schem', 'rb') as f:
    data = f.read(100)
tag_type = data[0]
name_len = struct.unpack('>H', data[1:3])[0]
root_name = data[3:3+name_len].decode('utf-8')
print(f'Root: type={tag_type}, name=\"{root_name}\"')
# 期待値: type=10, name=\"Schematic\"
"
```

## NBT構造の確認方法
ファイルの形式を判別するためのワンライナー:
```bash
python3 -c "
import gzip, struct
with gzip.open('<file>.schem', 'rb') as f:
    data = f.read(200)
tag_type = data[0]
name_len = struct.unpack('>H', data[1:3])[0]
root_name = data[3:3+name_len].decode('utf-8', errors='replace')
print(f'Root: type={tag_type}, name=\"{root_name}\"')
# name が空 → v3 (Schematic が子タグ)
# name が 'Schematic' → v1/v2
"
```

## 既知の制約

- **components 未変換**: アイテムの `components`（エンチャント、耐久値、カスタム名等）は削除される。1.20.1 の `tag` 形式への変換は未実装
- **看板テキスト未変換**: 1.21+ の `front_text`/`back_text` 形式は 1.20.1 の `Text1`-`Text4` 形式に変換されない
- **DataVersion 不一致**: 1.21+ で作成されたファイルに含まれる 1.20.1 に存在しないブロック（`minecraft:shelf`, `minecraft:tuff_bricks`, `minecraft:vault` 等）は空気に置換される
- **存在しないアイテム**: 1.20.1 に存在しないアイテムはチェスト内から消失する
- **ペースト負荷**: 大きな schematic のペーストはサーバーに高負荷がかかり、SSH 接続がタイムアウトする場合がある
- **WorldEdit のメモリ消費**: 全ブロックステートをキャッシュするため、mod 数が多い環境ではメモリ使用量が大きい（~166MB）
