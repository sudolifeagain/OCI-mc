# OCI-mc

Oracle Cloud Infrastructure (OCI) 上の Minecraft サーバーを Discord から管理する Bot。
バックアップの Notion 連携、プラグイン自動更新、Claude Code セッション管理など、リモート運用に必要な機能を一通り備えている。

## 機能一覧

### サーバー管理

複数サーバー（Paper / Forge 等）を個別に制御できる。

| コマンド | 説明 | 権限 |
| :--- | :--- | :--- |
| `/start [server]` | サーバーを起動（メモリ残量チェック付き） | `start` |
| `/stop [server]` | サーバーを停止（SIGTERM → SIGKILL フォールバック） | `stop` |
| `/restart [server]` | サーバーを再起動 | `restart` |
| `/cmd <command> [server]` | RCON でコンソールコマンドを実行 | `rcon` |
| `/status [server]` | CPU / メモリ / TPS / MSPT を表示 | `status` |
| `/whitelist_add <player> [server]` | ホワイトリストにプレイヤーを追加 | `whitelist_add` |
| `/shell <command>` | ホスト上でシェルコマンドを実行 | Owner のみ |

- ステータス埋め込みを専用チャンネルに 3 分間隔で自動更新
- サーバーログを Discord チャンネルにリアルタイム転送
- OOM Kill 検知時に Discord へ通知

### バックアップ / ロールバック

| コマンド | 説明 | 権限 |
| :--- | :--- | :--- |
| `/backup [server]` | 手動バックアップを作成し Notion にアップロード | `backup` |
| `/backups` | Notion から最新 10 件のバックアップを一覧表示 | `backup` |
| `/rollback <index>` | 指定バックアップからワールドを復元 | `backup` |

- ファイルサイズの SHA256 フィンガープリントで変更検知（差分がなければスキップ）
- 20 MB 超のファイルはマルチパートアップロード
- `config.json` の `schedule_time` で定時自動バックアップ

### プラグイン管理

| コマンド | 説明 | 権限 |
| :--- | :--- | :--- |
| `/plugins [server]` | インストール済みプラグインとバージョンを一覧表示 | `status` |
| `/check_updates [server]` | 更新の有無を確認（サーバー停止不要） | `status` |
| `/update_plugins [server]` | 最新版をダウンロードしてインストール | `backup` |

対応ソース:
- **GeyserMC API** — Geyser, Floodgate
- **GitHub Releases** — BlueMap 等

SHA256 ハッシュ比較で更新を検出し、アトミックにファイルを置換する。

### 権限管理

| コマンド | 説明 | 権限 |
| :--- | :--- | :--- |
| `/permission list` | 現在のロール / ユーザー権限を表示 | Owner のみ |
| `/permission user <user> <action> <mode>` | ユーザー単位で権限を付与 / 剥奪 | Owner のみ |
| `/permission role <role> <action> <mode>` | ロール単位で権限を付与 / 剥奪 | Owner のみ |

- `config.json` でロールベースのデフォルト権限を定義
- ユーザー単位のオーバーライドは `user_permissions.json` に永続化

### リアクションロール

`config.json` の `reaction_roles` セクションで設定したメッセージにリアクションを付けると、対応する Discord ロールが自動付与される。ロールを外すとリアクションも除去される。

| コマンド | 説明 | 権限 |
| :--- | :--- | :--- |
| `/reaction-role-setup` | リアクションロールメッセージを投稿 | `reaction_role_setup` |

### Claude Code セッション管理

OCI サーバー上で Claude Code を tmux セッションとして管理し、Discord プラグイン経由でチャンネルから直接 Claude と会話できる。

| コマンド | 説明 | 権限 |
| :--- | :--- | :--- |
| `/claude-start` | セッションを開始（プラグインパッチ自動適用） | `claude` |
| `/claude-stop` | セッションを停止 | `claude` |
| `/claude-restart` | セッションを再起動（コンテキストリセット + 更新適用） | `claude` |
| `/claude-status` | セッションの状態とバージョンを表示 | `claude` |

- Discord プラグインに SuppressNotifications と allowRoles のパッチを自動適用
- `access.json` でチャンネル・ロール単位のアクセス制御

## アーキテクチャ

```
OCI-mc/
├── bot.py                     # エントリーポイント
├── settings.py                # 設定ローダー (.env + config.json)
├── config.json                # サーバー・権限・バックアップ設定
├── requirements.txt           # Python 依存パッケージ
├── .env.example               # 環境変数テンプレート
├── cogs/
│   ├── basic_control.py       # サーバー制御コマンド
│   ├── backup_system.py       # バックアップ / ロールバック
│   ├── plugin_system.py       # プラグイン管理
│   ├── status_display.py      # ステータス自動更新
│   ├── permission_system.py   # 権限管理
│   ├── system_monitor.py      # OOM Kill 監視
│   ├── claude_manager.py      # Claude Code セッション管理
│   └── reaction_roles.py      # リアクションロール
├── utils/
│   ├── server_manager.py      # サーバーインスタンス管理
│   ├── permissions.py         # 権限チェックユーティリティ
│   ├── rcon.py                # RCON プロトコル実装 (asyncio)
│   ├── plugin_manager.py      # プラグイン操作
│   └── notion_api.py          # Notion API クライアント
├── scripts/
│   └── patch_discord_plugin.sh  # Discord プラグイン自動パッチ
├── systemd/
│   └── discord-bot.service    # systemd ユニット
└── .github/workflows/
    ├── ci.yml                 # Lint (ruff) + import チェック
    ├── deploy.yml             # main push → OCI 自動デプロイ
    └── pr-merged.yml          # PR マージ通知
```

## セットアップ

### 前提条件

- Python 3.12+
- OCI インスタンス（ARM ベースの Always Free 推奨）
- Discord Bot Token
- Notion Integration Token + Database ID

### 手順

1. リポジトリをクローン:
   ```bash
   git clone https://github.com/sudolifeagain/OCI-mc.git
   cd OCI-mc
   ```

2. 仮想環境を作成して依存パッケージをインストール:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install --require-hashes -r requirements.txt
   ```

3. `.env.example` を `.env` にコピーして環境変数を設定:
   ```bash
   cp .env.example .env
   ```

   | 変数名 | 説明 |
   | :--- | :--- |
   | `DISCORD_TOKEN` | Discord Bot トークン |
   | `NOTION_TOKEN` | Notion Integration トークン |
   | `NOTION_DB_ID` | Notion データベース ID |
   | `DISCORD_CHANNEL_ID` | 通知先チャンネル ID |
   | `DISCORD_ADMIN_ID` | admin ロール ID |
   | `DISCORD_MOD_ID` | mod ロール ID |
   | `DISCORD_OWNER_ID` | Bot オーナーのユーザー ID |
   | `DISCORD_USER_ID` | user ロール ID（カンマ区切りで複数指定可） |
   | `DISCORD_CLAUDE_ROLE_ID` | Claude コマンド用ロール ID |
   | `DISCORD_STATUS_CHANNEL_ID` | ステータス埋め込み表示チャンネル |
   | `DISCORD_*_LOG_CHANNEL_ID` | サーバー別ログ転送チャンネル |

4. `config.json` でサーバー定義・権限・バックアップ対象を編集

5. Bot を起動:
   ```bash
   python bot.py
   ```

### デプロイ（本番環境）

`main` ブランチへの push で GitHub Actions が自動デプロイを実行する:

1. rsync でコードを OCI サーバーに同期（`.env`, `venv`, `logs` 等は除外）
2. systemd ユニットを配置・有効化
3. `pip install --require-hashes -r requirements.txt`
4. `discord-bot` サービスを再起動
5. Discord Webhook で成功 / 失敗を通知

開発は `develop` ブランチで行い、PR 経由で `main` にマージする。

## プラグイン設定例

```json
{
  "plugins": {
    "paper": {
      "geyser": {
        "source": "geysermc",
        "project": "geyser",
        "platform": "spigot",
        "filename": "Geyser-Spigot.jar"
      },
      "bluemap": {
        "source": "github",
        "repo": "BlueMap-Minecraft/BlueMap",
        "asset_pattern": "bluemap-*-paper.jar",
        "filename_pattern": "bluemap-*-paper.jar"
      }
    }
  }
}
```

## ライセンス

MIT
