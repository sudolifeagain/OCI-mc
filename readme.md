# OCI-mc

Oracle Cloud Infrastructure (OCI) 上の Minecraft サーバーを Discord から管理する Bot。
Notion バックアップ、プラグイン更新、Paper artifact の検証付きデプロイ、Claude Code セッション管理など、リモート運用に必要な機能を備えている。

本番で使用する Minecraft、Paper、Mod ローダー、プラグインのバージョンとハッシュは `server-artifacts.json` を正とする。現在の Paper は 26.2 build 62 BETA であり、26.2 の STABLE 公開後に自動更新する構成である。

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
| `/shell <command>` | ホスト上で任意シェルコマンドを実行 | `DISCORD_SHELL_USER_IDS` のユーザーのみ |

- ステータス埋め込みを専用チャンネルに 3 分間隔で自動更新
- サーバーログを Discord チャンネルにリアルタイム転送
- OOM Kill 検知時に Discord へ通知
- 全スラッシュコマンドは許可guild・チャンネル内に限定
- `/shell` は60秒・64KiB上限、直列実行、ephemeral応答で運用

### バックアップ / ロールバック

| コマンド | 説明 | 権限 |
| :--- | :--- | :--- |
| `/backup [server]` | 手動バックアップを作成し Notion にアップロード | `backup` |
| `/backups` | Notion から最新 10 件のバックアップを一覧表示 | `backup` |
| `/rollback <index>` | 指定バックアップからワールドを復元 | `backup` |

- ファイルパス・サイズ・更新時刻のSHA256フィンガープリントで変更検知（差分がなければスキップ）
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
- **Modrinth API** — WorldEdit 等

配布元の SHA256 または SHA1 ハッシュで更新を検出・検証し、アトミックにファイルを置換する。GitHub Releases は `release_tag`、Modrinth は `version_number` で互換版を固定できる。

### Paper STABLE 自動更新

GitHub Actions が Paper Downloads Service を毎日 12:17 JST に確認し、Minecraft 26.2 の `STABLE` チャンネルだけを対象に更新する。

1. 候補 jar のファイル名、配布元、サイズ、SHA-256 を検証
2. `server-artifacts.json` だけを `develop` で更新
3. `main` への PR を作成し、必須 CI 成功後にマージ
4. 本番デプロイ時に jar を再取得して SHA-256 を再検証
5. 起動確認に失敗した場合は旧 jar へロールバック

`develop` に未マージ変更がある場合は自動更新を停止する。Minecraft の新しいバージョン、Forge、Mod、プラグインには自動追従しない。

### 権限管理

| コマンド | 説明 | 権限 |
| :--- | :--- | :--- |
| `/permission list` | 現在のロール / ユーザー権限を表示 | Owner のみ |
| `/permission user <user> <action> <mode>` | ユーザー単位で権限を付与 / 剥奪 | Owner のみ |
| `/permission role <role> <action> <mode>` | ロール単位で権限を付与 / 剥奪 | Owner のみ |

- `config.json` でロールベースのデフォルト権限を定義
- ユーザー単位のオーバーライドは `user_permissions.json` に永続化

### リアクションによるチャンネルアクセス

`config.json` の `reaction_roles` セクションで設定したメッセージにリアクションを付けると、対応する Discord チャンネルの閲覧権限をユーザー単位で付与する。リアクションを外すと権限オーバーライドを削除する。

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
├── server-artifacts.json      # Paper・プラグイン・Modローダーの本番バージョンとハッシュ
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
│   ├── discord_security.py    # Discord実行コンテキスト検証
│   ├── shell_runner.py        # 制限付き任意シェル実行
│   ├── plugin_manager.py      # プラグイン操作
│   └── notion_api.py          # Notion API クライアント
├── scripts/
│   ├── manage_paper_artifact.py # Paper jar の配置・検証・ロールバック
│   ├── paper_stable_update.py   # Paper 26.2 STABLE の検出
│   ├── graceful_shutdown.py     # RCON による安全な停止
│   ├── verify_runtime.py        # デプロイ後の起動確認
│   └── patch_discord_plugin.sh  # Discord プラグイン自動パッチ
├── systemd/
│   └── discord-bot.service    # systemd ユニット
└── .github/workflows/
    ├── ci.yml                 # 依存監査・テスト・Lint
    ├── deploy.yml             # main push → OCI 自動デプロイ
    ├── paper-stable-update.yml # Paper 26.2 STABLE 自動更新
    └── pr-merged.yml          # PR マージ通知
```

## セットアップ

### 前提条件

- Python 3.12+
- Java 25（Paper 26.2）
- OCI インスタンス（ARM ベースの Always Free 推奨）
- Discord Bot Token
- Notion Integration Token + Database ID（バックアップ機能を使用する場合）

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
   | `NOTION_DS_ID` | Notion Data Source ID の明示指定（任意） |
   | `DISCORD_CHANNEL_ID` | 通知先チャンネル ID |
   | `DISCORD_ADMIN_ID` | admin ロール ID |
   | `DISCORD_MOD_ID` | mod ロール ID |
   | `DISCORD_OWNER_ID` | Bot オーナーのユーザー ID |
   | `DISCORD_GUILD_IDS` | コマンドを許可するguild ID（カンマ区切り） |
   | `DISCORD_COMMAND_CHANNEL_IDS` | 管理コマンドを許可するチャンネル ID（カンマ区切り） |
   | `DISCORD_SHELL_USER_IDS` | 任意シェルを許可するユーザー ID（カンマ区切り） |
   | `DISCORD_SHELL_CHANNEL_IDS` | 任意シェルを許可するチャンネル ID（カンマ区切り） |
   | `DISCORD_USER_IDS` | user ロール ID（カンマ区切り） |
   | `DISCORD_USER_ID` | user ロール ID（後方互換用） |
   | `DISCORD_CLAUDE_ROLE_ID` | Claude コマンド用ロール ID |
   | `DISCORD_STATUS_CHANNEL_ID` | ステータス埋め込み表示チャンネル |
   | `DISCORD_*_LOG_CHANNEL_ID` | サーバー別ログ転送チャンネル |
   | `PAPER_RCON_PASSWORD` | Paper の RCON パスワード |
   | `FORGE_RCON_PASSWORD` | Forge の RCON パスワード |
   | `FORGE_ALT_RCON_PASSWORD` | Forge Alt の RCON パスワード |
   | `SERVER_RUNTIME_DIR` | PID と desired state の保存先 |

4. `config.json` でサーバー定義・権限・バックアップ対象を編集

5. Bot を起動:
   ```bash
   python bot.py
   ```

### デプロイ（本番環境）

`main` ブランチへの push で GitHub Actions が自動デプロイを実行する:

1. 稼働中サーバーをdesired stateへ保存し、RCONで安全に停止
2. rsyncでコードをOCIサーバーへ同期（`.env`, `venv`, `logs`等は除外）
3. ゲームサーバーごとの専用Unixユーザーとファイル権限を設定
4. `server-artifacts.json` の URL と SHA-256 を検証して Paper jar を配置
5. systemdユニットと依存関係を更新
6. Botを起動し、desired stateのゲームサーバーがreadyになるまで確認
7. 失敗時は Paper jar とゲームサーバーの稼働状態を復旧
8. Discord Webhookで成功／失敗を通知

開発は `develop` ブランチで行い、PR 経由で `main` にマージする。

## 開発と検証

開発は `develop` ブランチで直接行い、feature ブランチは作成しない。変更後は以下を実行する。

```bash
python -m unittest discover -s tests -v
ruff check . --select=E,F,W --ignore=E501 --exclude=venv
```

CI は Python 3.12 で依存関係の脆弱性監査、構文・import確認、単体テスト、ruff を実行する。

## 公開リポジトリでの秘密情報管理

- `.env`、SSH 秘密鍵、RCON パスワード、Discord・Notion のトークンは Git に追加しない
- GitHub Actions の認証情報は Repository または Environment secrets で管理する
- `config.json` と `server-artifacts.json` は公開されるため、認証情報を記録しない
- Discord の guild・channel・user ID は認証秘密ではないが、公開不要な運用メタデータは `.env` または Git 管理外の設定に置く
- 漏えいした秘密情報は Git から削除するだけでは無効化されないため、先に失効・ローテーションしてから履歴を処理する

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
        "release_tag": "v5.22",
        "filename_pattern": "bluemap-*-paper.jar"
      },
      "worldedit": {
        "source": "modrinth",
        "project": "worldedit",
        "loader": "paper",
        "game_version": "26.2",
        "version_number": "7.4.4",
        "filename_pattern": "worldedit-bukkit-*.jar"
      }
    }
  }
}
```

## ライセンス

MIT
