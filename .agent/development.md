# Development Guide

## Branch Strategy
- **`develop`**: 開発用ブランチ。CIでlint/構文チェックのみ実行。デプロイなし。
- **`master`**: 本番ブランチ。pushでOCIへ自動デプロイ（サーバー再起動を伴う）。
- **Workflow**: `develop`で開発 → PRで`master`にマージ → 自動デプロイ

### ブランチ運用ルール
- **featureブランチは作成しない**: すべての開発は`develop`ブランチで直接行う
- **masterへの直接pushは禁止**: 必ず`develop`経由でPRを作成する
- **コミット粒度**: 機能単位で適切にコミットを分割する

## Commands
- **Install**: `pip install -r requirements.txt`
- **Run Local**: `python bot.py`
  - Ensure `.env` exists with `DISCORD_TOKEN`.

## Code Quality (必須)
コードを変更したら、push前に必ず以下を実行:
```bash
ruff check . --select=E,F,W --ignore=E501 --exclude=venv
```
- CIで同じチェックが走るため、ローカルで通らないコードはpushしない
- `--fix` オプションで自動修正可能
- エラーが出たら修正してからcommit

## Architecture: Cogs System
The bot uses `discord.py` Cogs extension pattern in `cogs/`.

### 1. Basic Control (`cogs.basic_control`)
- **File**: `cogs/basic_control.py`
- **Role**: Server lifecycle (start, stop, restart, status).
- **Key Logic**: Calls `MultiServerManager` to interact with subprocesses.
- **Log Streaming**: Bot captures stdout from spawned processes and forwards to Discord.
  - Only works for servers started via Bot (not SSH/manual startup)
  - Uses `asyncio.Queue` in `ServerInstance.log_queue`

### 2. Backup System (`cogs.backup_system`)
- **File**: `cogs/backup_system.py`
- **Role**: Backups to Notion.
- **Key Logic**: Compresses world files -> Uploads to Notion API.
- **Triggers**: Scheduled (loop) or Manual (`/backup`).

### 3. Plugin System (`cogs.plugin_system`)
- **File**: `cogs/plugin_system.py`
- **Role**: Update management.
- **Key Logic**: Checks SHA256 hashes against APIs (Geyser, GitHub Releases).

### 4. Status Display (`cogs.status_display`)
- **File**: `cogs/status_display.py`
- **Role**: リアルタイムサーバー参加状況表示
- **Key Logic**:
  - 3分ごとに指定チャンネルのEmbedメッセージを更新
  - サーバー稼働状態（✅/❌）とオンラインプレイヤー一覧を表示
  - プレイヤーの参加時間も表示
- **Required Env**: `DISCORD_STATUS_CHANNEL_ID`

## Player Tracking
`ServerInstance` がプレイヤーの参加/退出をログから検知し `online_players` dict に保持。
- Join: `joined the game` パターンでタイムスタンプ記録
- Leave: `left the game` パターンで削除
- `status_display` や `/status` コマンドで利用

## Server-specific Log Channels
各サーバーのログを別々のDiscordチャンネルに転送可能。
- `DISCORD_PAPER_LOG_CHANNEL_ID`: Paperサーバー用
- `DISCORD_FORGE_LOG_CHANNEL_ID`: Forgeサーバー用
- 未設定時は `DISCORD_CHANNEL_ID` にフォールバック

## Auto-Start Feature
ボット起動時に `auto_start: true` のサーバーを自動起動。

- **設定**: `config.json` の各サーバーに `"auto_start": true` を追加
- **処理**: `bot.py` の `on_ready` で `_auto_start_servers()` を実行
- **フラグ**: `_auto_start_done` でDiscord再接続時の重複起動を防止
- **並列起動**: `asyncio.gather` で複数サーバーを同時起動

**重要**: デプロイ（GitHub push → master）時にボット再起動でサーバーも終了するが、この機能で自動復旧する。

## Code Style
- **Slash Commands Only**: No `command_prefix`. Use `@app_commands.command`.
- **Type Hinting**: Required for all function arguments and returns.
- **Async**: Strictly used for all blocking operations (IO, Net, Subprocess).

## Agent Skills
`.claude/skills/`にドメイン固有の知識をパッケージ化。タスクに応じて自動的にトリガーされる。

| Skill | 用途 | トリガー例 |
|-------|------|-----------|
| `/deploy` | デプロイワークフロー、CI | 「デプロイ手順」「CIが失敗」 |
| `/process-management` | プロセス管理、ログ転送 | 「サーバーが停止しない」「ログが転送されない」 |
| `/backup-notion` | バックアップ・復元 | 「バックアップが失敗」「ロールバック」 |

**新規Skill作成時**: `.claude/skills/<name>/SKILL.md`を作成。YAMLフロントマターに`name`と`description`を記述。
