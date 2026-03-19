---
name: backup-notion
description: Backup and restore Minecraft worlds using Notion as storage. Use when performing backups, restoring from backups, troubleshooting Notion API issues, or managing backup schedules.
---

# Backup & Notion Integration

## Quick Start

```
/backup              - 全サーバーをバックアップ
/backup server:paper - 特定サーバーをバックアップ
/backups             - 最新10件のバックアップ一覧
/rollback <index>    - 指定番号のバックアップに復元
```

## Workflow

### バックアップフロー

```
/backup コマンド実行
    |
サーバー停止（データ整合性のため）
    |
world ディレクトリを ZIP 圧縮
    |
一時ディレクトリに保存: /tmp/backup_{server}_{timestamp}.zip
    |
Notion APIにアップロード（マルチパート対応）
    |
Notion DBに登録（ファイル名、サイズ、日時）
    |
一時ファイル削除
    |
サーバー再起動
```

### ロールバックフロー

```
/rollback <index> 実行
    |
Notion DBからバックアップ一覧取得
    |
ファイル名からサーバーID推測（backup_paper_*, backup_forge_*）
    |
サーバー停止
    |
Notionからダウンロード
    |
既存world削除
    |
ZIP/TAR展開
    |
サーバー起動
```

## Important Notes

### Notion API制限

- **ファイルサイズ**: 20MB以上はマルチパートアップロードが必要
- **レート制限**: 連続リクエストに注意
- **ファイル形式**: `.zip` 形式で直接アップロード

### バックアップ対象ディレクトリ

`config.json` で設定:

```json
"backup": {
    "target_dirs": {
        "paper": ["world", "world_nether", "world_the_end"],
        "forge": ["world"]
    },
    "schedule_time": "04:00"
}
```

### スケジュールバックアップ

- 毎日 `schedule_time` に自動実行
- 全サーバーが対象
- `tasks.loop(minutes=1)` で時刻監視

## Related Files

- `cogs/backup_system.py` - バックアップCog
- `utils/notion_api.py` - Notion API連携
- `config.json` - バックアップ設定

### 環境変数

| 変数 | 用途 |
|------|------|
| `NOTION_TOKEN` | Notion API認証トークン |
| `NOTION_DB_ID` | バックアップ登録先データベースID |
| `NOTION_DS_ID` | data_source_id のオーバーライド（オプション） |

## Troubleshooting

### バックアップが失敗する

1. 環境変数の確認:
   ```bash
   ssh ubuntu@<OCI_IP> "cat /opt/minecraft/bot/.env | grep NOTION"
   ```

2. Notion API接続テスト:
   ```python
   import requests
   headers = {"Authorization": "Bearer <NOTION_TOKEN>"}
   r = requests.get("https://api.notion.com/v1/users/me", headers=headers)
   print(r.status_code)  # 200ならOK
   ```

3. ディスク容量確認:
   ```bash
   df -h /tmp
   ```

### ダウンロードが失敗する

1. NotionファイルURLの有効期限切れ（1時間で期限切れ）
2. ネットワーク接続の確認
3. `/rollback`実行時にURLを再取得する設計になっているか確認

### ロールバック後にワールドが壊れている

1. バックアップ作成時にサーバーが正常停止していたか確認
2. ZIP/TARファイルの整合性確認
3. 展開先パスが正しいか確認（mc_dir変数）

### ファイルサイズが大きすぎる

Forgeサーバーのワールドは巨大になりやすい:
- modsフォルダは除外されている（バックアップ対象はworldのみ）
- 圧縮率が低い場合はZIP_DEFLATEDレベルを調整
