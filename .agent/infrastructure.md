# Infrastructure & Deployment

## Server Environment (OCI)
- **OS**: Ubuntu (ARM based)
- **IP**: *Retrieved from context/user* (e.g. `161.xxx`)
- **SSH Access**: `ssh -i <local_key> ubuntu@<IP>`

## Directory Map (Remote)
- **/opt/minecraft/**: Root
  - **paper/**: Paper server (Vanilla compatible)
    - `paper.jar`, `plugins/`, `world/`
    - `start.sh` - 起動スクリプト (`stdbuf -oL java @user_jvm_args.txt -jar paper.jar`)
    - `user_jvm_args.txt` - JVMメモリ設定 (`-Xmx4G -Xms4G`)
    - `.paper.pid` - PIDファイル（ボット起動時に自動生成）
  - **forge/**: Forge server (Modded, Better MC 1.20.1)
    - `run.sh`, `mods/` (~590 mods, 1.1GB), `world/`
    - `start.sh` - 起動スクリプト (`stdbuf -oL ./run.sh`)
    - `.forge.pid` - PIDファイル（ボット起動時に自動生成）
    - Memory: 10G, Port: 25566
  - **bot/**: This repository deployment
    - `bot.py`, `.env`, `venv/`

## Server Operations (SSH)

### Process Management
```bash
# Check running Java processes
ps aux | grep java | grep -v grep

# Start Forge server (background)
cd /opt/minecraft/forge && nohup ./run.sh nogui > /tmp/forge.log 2>&1 &

# Stop server (graceful -> force)
kill <PID>        # SIGTERM first
kill -9 <PID>     # SIGKILL if needed

# View startup log
tail -f /opt/minecraft/forge/logs/latest.log
```

### Whitelist Management (Direct Edit)
When console access is unavailable (e.g., nohup startup):
```bash
# Get player UUID from Mojang API
curl -s https://api.mojang.com/users/profiles/minecraft/<PlayerName>

# Edit whitelist.json directly (UUID format: 8-4-4-4-12)
echo '[{"uuid":"xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx","name":"PlayerName"}]' > /opt/minecraft/forge/whitelist.json
```

### Important Notes
- **Discord Logs**: Only work when server is started via Bot (`/start`), not SSH
- **Bot monitors stdout** of processes it spawns; SSH-started servers are invisible to Bot
- **start.sh に `exec` を使わない**: stdoutパイプが壊れてログ転送が動作しなくなる（詳細: `.agent/decisions.md`）

### RCON設定
Discord botからMinecraftサーバーにコマンドを送信するためにRCONを使用。

#### サーバー側設定 (server.properties)
```properties
# Paper (port 25575)
enable-rcon=true
rcon.port=25575
rcon.password=<secure_password>

# Forge (port 25576)
enable-rcon=true
rcon.port=25576
rcon.password=<secure_password>
```

#### Bot側設定 (.env)
```bash
PAPER_RCON_PASSWORD=<password>
FORGE_RCON_PASSWORD=<password>
```

#### config.json
各サーバーに`rcon_port`と`rcon_password_env`を設定:
```json
{
  "servers": {
    "paper": {
      "rcon_port": 25575,
      "rcon_password_env": "PAPER_RCON_PASSWORD"
    }
  }
}
```

#### 使用方法
- `/cmd <command> [server]`: RCONでコマンド実行、結果を表示

#### セキュリティ
- RCONポート（25575/25576）は外部に公開しない
- OCI Security Listで許可されていないことを確認
- パスワードは16文字以上のランダム文字列を推奨

### 権限管理

#### ファイル構成
| ファイル | 内容 | Git管理 |
|---------|------|--------|
| `config.json` | 静的設定（サーバー設定、権限マッピング） | Yes |
| `user_permissions.json` | 動的権限（`/permission`で変更） | No（.gitignore） |
| `.env` | ロールID、トークン、パスワード | No |

#### 権限チェックの流れ
1. Owner（`DISCORD_OWNER_ID`）→ 常に許可
2. `user_permissions.json` にユーザーIDとアクションが存在 → 許可
3. `config.json`の`permissions`でロールにアクションが割り当て → 許可

#### コマンド
- `/permission list`: 現在の権限一覧
- `/permission user <user> <action> <allow/deny>`: ユーザー権限設定（永続化）
- `/permission role <role> <action> <allow/deny>`: ロール権限設定（**一時的、再起動で消失**）

#### 注意事項
- ユーザー権限は`user_permissions.json`に保存され、デプロイ後も維持される
- ロール権限の変更はメモリ上のみ。永続化するには`config.json`を直接編集する

### Memory Configuration

#### Swap Settings
スワップは無効化済み。Minecraftサーバーのパフォーマンス優先のため。

```bash
# 現在のスワップ状態を確認
swapon --show
free -h
sysctl vm.swappiness

# スワップが有効な場合の無効化手順
sudo swapoff -a                                           # 即時無効化
echo 'vm.swappiness=0' | sudo tee /etc/sysctl.d/99-disable-swap.conf
sudo sysctl -p /etc/sysctl.d/99-disable-swap.conf         # 永続化
```

#### 設定ファイル
- `/etc/sysctl.d/99-disable-swap.conf`: `vm.swappiness=0`
- `/etc/fstab`: スワップエントリなし

#### 注意点
- **OOM Killer**: メモリ枯渇時はスワップへの退避ではなくプロセス強制終了が発生
- **監視推奨**: `free -h`でメモリ使用量を定期確認
- 現在のメモリ構成: 17GB（Forge 10GB + Paper 4GB + Bot + OS）

## Deployment Flow
1. **GitHub Actions**: Triggered on push to `main` (not `develop`).
2. **Rsync**: Syncs files to `/opt/minecraft/bot/`.
   - Excludes: `.env`, `.git`, `venv`, `user_permissions.json`
3. **Systemd**:
   - Service: `discord-bot`
   - Path: `/etc/systemd/system/discord-bot.service`
   - Restarted automatically after deploy.
4. **Auto-Start**: ボット起動後、`auto_start: true` のサーバーを自動起動。

### Branch Strategy
- **`develop`**: 開発用。CIでlint/構文チェックのみ。デプロイなし。
- **`main`**: 本番用。pushでOCIへ自動デプロイ。
- **注意**: mainへのpushはサーバー再起動を伴う。

## Environment Variables (.env)

### Required
- `DISCORD_TOKEN`: Bot token

### Channels
- `DISCORD_CHANNEL_ID`: Default log channel (fallback)
- `DISCORD_PAPER_LOG_CHANNEL_ID`: Paper server log channel (optional)
- `DISCORD_FORGE_LOG_CHANNEL_ID`: Forge server log channel (optional)
- `DISCORD_STATUS_CHANNEL_ID`: Real-time status display channel (optional)

### Roles/Users
- `DISCORD_ADMIN_ID`: Admin role ID
- `DISCORD_MOD_ID`: Mod role ID
- `DISCORD_OWNER_ID`: Bot owner user ID (for `/shell`)
- `DISCORD_USER_IDS`: Allowed user IDs (comma-separated)

### RCON
- `PAPER_RCON_PASSWORD`: Paper server RCON password
- `FORGE_RCON_PASSWORD`: Forge server RCON password

### Notion (Backup)
- `NOTION_TOKEN`: Notion API token
- `NOTION_DB_ID`: Notion database ID for backups

### Notion API でバックアップファイルをダウンロード

NotionにアップロードされたバックアップファイルをAPI経由でダウンロードする手順:

#### 1. ページIDを取得
Notion URLからページIDを抽出（`?p=`パラメータの値）:
```
https://re4rity.notion.site/...?p=2ff8e4f849c181aead25e6194692d944
                                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                  これがページID
```

#### 2. ページ情報を取得
```bash
curl -s -X GET "https://api.notion.com/v1/pages/<PAGE_ID>" \
  -H "Authorization: Bearer $NOTION_TOKEN" \
  -H "Notion-Version: 2022-06-28"
```

#### 3. ファイルURLを抽出
レスポンスの `properties.File.files[0].file.url` に署名付きS3 URLが含まれる。

#### 4. ファイルをダウンロード
```bash
curl -L -o "backup.zip" "<S3_URL>"
```

**注意**: 署名付きURLは1時間で期限切れ。期限切れ後は再度ページ情報を取得する。

## BlueMap (Paper)
Paperサーバーで3DマップをWebブラウザで表示するプラグイン。

- **Webポート**: 8100
- **タイル保存先**: `/opt/minecraft/paper/bluemap/web/maps/`
- **設定**: `/opt/minecraft/paper/plugins/BlueMap/`

### 現在の設定
- `player-render-limit: 1` - プレイヤーオンライン中は自動レンダリング停止
- 手動更新: `/bluemap update world`

### トラブルシューティング
高CPU使用率などの問題は `.agent/bluemap-troubleshooting.md` を参照。

## Sensitive Data Handling
- **Public Repo**: This codebase is public.
- **Secrets**:
  - `DISCORD_TOKEN`: Managed in `.env` (remote) and GitHub Secrets for CI.
  - SSH Keys: Never stored in repo.
