---
name: process-management
description: Server process management for Minecraft servers. Use when troubleshooting server start/stop issues, log forwarding problems, or process lifecycle management.
---

# Process Management

## Quick Start

サーバープロセスはボットの子プロセスとして管理される。この設計はログ転送を可能にするが、ボット再起動時にサーバーも終了する。

## Critical Rules

### 禁止事項

以下の設定は**絶対に使用しない**:

1. **`start_new_session=True`** - stdoutパイプが壊れ、ログ転送が動作しなくなる
2. **`exec` in start.sh** - 同様にstdoutパイプが壊れる

```python
# NG: 使用禁止
await asyncio.create_subprocess_exec(
    *cmd,
    start_new_session=True  # これを使うとログ転送が壊れる
)
```

```bash
# NG: start.shでexecを使わない
exec java -jar paper.jar  # ログ転送が壊れる
```

### 正しい実装

```python
# OK: start_new_sessionを使わない
self.process = await asyncio.create_subprocess_exec(
    *cmd,
    cwd=self.cwd,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT
)
```

```bash
# OK: execなしで起動
java @user_jvm_args.txt -jar paper.jar "$@"
```

## Workflow

### サーバー起動フロー

```
bot.py: on_ready()
    |
_auto_start_done フラグ確認（再接続時の重複防止）
    |
config.json の auto_start: true を確認
    |
asyncio.gather で並列起動
    |
ServerInstance.start()
    |
asyncio.create_subprocess_exec()
    |
PIDファイル作成: .{server_id}.pid
    |
_read_stdout() タスク開始
    |
ログをDiscordに転送
```

### サーバー停止フロー

```
stop() 呼び出し
    |
stdin に "stop\n" 送信
    |
60秒待機
    |
タイムアウト時: SIGTERM送信
    |
10秒待機
    |
タイムアウト時: SIGKILL送信
    |
PIDファイル削除
    |
online_players クリア
```

## Important Notes

### プロセス独立とログ転送のトレードオフ

| 方式 | プロセス独立 | ログ転送 | 採用 |
|------|------------|---------|------|
| 親子プロセス | x | o | 採用 |
| start_new_session | o | x | 不採用 |
| exec | o | x | 不採用 |
| systemd | o | x | 不採用 |

**結論**: ログ転送が必要なため、親子プロセス関係を維持し、auto-start機能でボット再起動後のサーバー復旧に対応する。

### PIDファイル管理

- 場所: `/opt/minecraft/{server_id}/.{server_id}.pid`
- 用途: プロセス検出、重複起動防止
- 注意: 古いPIDファイルが残っている場合、`is_running()`で誤検出する可能性がある

## Related Files

- `bot.py` - auto-start処理 (`_auto_start_servers()`)
- `utils/server_manager.py` - `ServerInstance`, `MultiServerManager`
- `config.json` - `auto_start` 設定
- `.agent/decisions.md` - 技術的意思決定の詳細記録
- `/opt/minecraft/*/start.sh` - 起動スクリプト（リモート）

## Troubleshooting

### ログがDiscordに転送されない

1. start.shに`exec`が含まれていないか確認
2. `start_new_session=True`が使われていないか確認
3. サーバーがボット経由で起動されているか確認（SSH起動ではログ転送不可）

### サーバーが停止しない

1. `_stopping`フラグがTrueのままになっていないか確認
2. PIDファイルと実際のプロセスが一致しているか確認
3. 手動でプロセスを確認: `ps aux | grep java`

### ボット再起動後にサーバーが起動しない

1. `config.json`の`auto_start: true`を確認
2. `_auto_start_done`フラグが正しくリセットされているか確認
3. ボットログで"Auto-starting server"を検索
