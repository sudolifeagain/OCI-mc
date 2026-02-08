# BlueMap トラブルシューティング

## 概要
BlueMapはMinecraftワールドの3Dマップをリアルタイムでレンダリングするプラグイン。Paperサーバーで使用中。

## アーキテクチャ

### レンダリングの仕組み
1. **ファイル監視**: `BlueMap-RegionF`スレッドがリージョンファイル（`.mca`）のタイムスタンプを監視
2. **変更検出**: タイムスタンプが更新されると、そのリージョンをレンダリングキューに追加
3. **タイル生成**: `BlueMap-RenderThread`がキューからリージョンを取り出し、タイルを生成
4. **保存**: 生成されたタイルは`bluemap/web/maps/<world>/tiles/`に保存

### 主要スレッド
| スレッド | 役割 |
|---------|------|
| BlueMap-RenderThread | メインレンダリング処理 |
| BlueMap-FJP-0 (複数) | Fork-Join Pool（並列処理補助） |
| BlueMap-RegionF (複数) | リージョンファイル監視 |
| BlueMap-WebbApp | Webサーバー処理 |
| BlueMap-Plugin- | プラグイン連携 |

## 高CPU使用率の原因と対策

### 問題: 無限レンダリングループ

#### 症状
- `BlueMap-RenderThread`がCPU 90%以上を占有
- サーバー全体のCPU使用率が150%以上
- レンダリングキューが減らない

#### 原因
Minecraftの`autosave`とBlueMapのファイル監視が競合:
1. autosaveがリージョンファイルを更新（デフォルト: 60秒間隔）
2. BlueMapがタイムスタンプ変更を検出
3. レンダリングキューに追加
4. レンダリング完了前に次のautosaveが発生
5. 永遠にキューが空にならない

#### 検証コマンド
```bash
# BlueMapスレッドのCPU使用時間を確認
ps -T -p <PID> -o tid,comm,cputime | grep -i bluemap

# リアルタイムCPU使用率
top -H -b -n 1 -p <PID> | head -30

# リージョンファイルの更新頻度
watch -n 1 'ls -la /opt/minecraft/paper/world/region/*.mca | head -10'

# タイル生成速度（1分間の生成数）
find /opt/minecraft/paper/bluemap/web/maps/world/tiles -type f -name '*.gz' -mmin -1 | wc -l
```

### 解決策

#### 方法1: プレイヤーオンライン中のレンダリング停止（推奨）
`plugins/BlueMap/plugin.conf`:
```conf
# プレイヤーが1人以上オンラインの場合、自動レンダリングを停止
player-render-limit: 1
```
- プレイヤーがログアウトすると自動的にレンダリング再開
- 手動コマンド `/bluemap update world` は引き続き使用可能

#### 方法2: autosave間隔の延長
`bukkit.yml`:
```yaml
# 1200 ticks = 60秒 → 6000 ticks = 5分
ticks-per:
  autosave: 6000
```
注意: これだけでは根本解決にならない場合がある

#### 方法3: レンダリングの完全無効化
`plugins/BlueMap/core.conf`:
```conf
render-thread-count: 0
```

### BlueMapコマンド一覧
| コマンド | 説明 |
|---------|------|
| `/bluemap` | ステータス表示 |
| `/bluemap pause` | レンダリング一時停止 |
| `/bluemap resume` | レンダリング再開 |
| `/bluemap reload` | 設定リロード |
| `/bluemap render <map>` | 指定マップを全レンダリング |
| `/bluemap update <map>` | 変更箇所のみ更新 |

## 設定ファイル一覧

| ファイル | 用途 |
|---------|------|
| `plugins/BlueMap/core.conf` | コア設定（スレッド数、デバッグログ） |
| `plugins/BlueMap/plugin.conf` | プラグイン設定（player-render-limit） |
| `plugins/BlueMap/webserver.conf` | Webサーバー設定（ポート8100） |
| `plugins/BlueMap/maps/*.conf` | マップ別設定 |
| `plugins/BlueMap/storages/*.conf` | ストレージ設定 |

## パフォーマンス指標

### 正常時の目安
- `BlueMap-RenderThread`: CPU 0%（キューが空の時）
- タイル生成速度: 約95タイル/分（1スレッド時）

### 問題発生時の指標
- `BlueMap-RenderThread`: CPU 90%以上が継続
- サーバー全体: CPU 150%以上
- レンダリングキュー: 常に増加

## 現在の設定（2025-02-04時点）
- `player-render-limit: 1` - プレイヤーオンライン中は自動レンダリング停止
- `autosave: 6000` - 5分間隔
- `render-thread-count: 1` - シングルスレッド

この設定により、プレイヤーがプレイ中はCPU使用率が大幅に低下（167%→18%）。マップ更新が必要な場合は `/bluemap update world` を手動実行する運用。
