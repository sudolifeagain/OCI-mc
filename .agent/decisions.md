# 技術的な意思決定の記録

このドキュメントは、設計上の意思決定とその背景を記録し、将来のAIエージェントが同じ試行錯誤を繰り返さないようにするためのものです。

---

## プロセス管理とログ転送 (2026-01)

### 問題
ボット再起動（GitHub push によるデプロイ）時に、Minecraftサーバーも一緒に終了してしまう。

### 原因
ボットがサーバーを子プロセスとして起動しているため、親プロセス（ボット）が終了すると子プロセス（サーバー）も終了する。

### 試みた解決策と結果

| 方式 | 内容 | 結果 |
|------|------|------|
| `start_new_session=True` | 新しいセッションでプロセスを起動し、親子関係を切る | ❌ stdoutパイプが壊れ、ログ転送が動作しなくなった |
| `exec` コマンド | start.sh でシェルをjavaに置き換え、PID追跡を簡略化 | ❌ 同様にstdoutパイプが壊れた |
| systemdサービス化 | サーバーをsystemdで管理 | 検討のみ。stdinへのコマンド送信ができなくなる問題 |
| screen/tmux | セッション内でサーバーを起動 | 検討のみ。複雑になるため見送り |

### 採用した解決策

**自動起動機能** - ボット起動時にサーバーを自動起動する

```python
# bot.py
async def on_ready(self):
    if not self._auto_start_done:
        self._auto_start_done = True
        await self._auto_start_servers()
```

- `config.json` で `auto_start: true` を設定したサーバーを起動
- `asyncio.gather` で複数サーバーを並列起動
- `_auto_start_done` フラグで再接続時の重複起動を防止

### 現在の動作

```
GitHub push
    ↓
GitHub Actions でデプロイ
    ↓
ボット再起動（サーバーも終了）
    ↓
on_ready で自動起動
    ↓
サーバー復旧、ログ転送開始
```

### 重要な教訓

1. **`exec` と `start_new_session=True` は stdout パイプを壊す**
   - Pythonの `asyncio.create_subprocess_exec` で `start_new_session=True` を使うと、stdoutの読み取りができなくなる
   - シェルスクリプト内の `exec` も同様の問題を引き起こす

2. **プロセス独立とログ転送はトレードオフ**
   - 親子関係を維持しないとstdoutパイプが使えない
   - ログ転送が必要なら、自動起動で対応するのが現実的

3. **start.sh の内容**
   - `exec` は使わない
   - `stdbuf -oL` でline bufferingを強制（後述）
   - Paper: `stdbuf -oL java @user_jvm_args.txt -jar paper.jar "$@"`
   - Forge: `stdbuf -oL ./run.sh "$@"`

### 関連ファイル
- `bot.py` - 自動起動処理
- `config.json` - `auto_start` 設定
- `utils/server_manager.py` - プロセス管理
- `/opt/minecraft/*/start.sh` - 起動スクリプト（リモート）
- `/opt/minecraft/*/user_jvm_args.txt` - JVMメモリ設定（リモート）

---

## stdoutバッファリング問題 (2026-01)

### 問題
起動ログはDiscordに転送されるが、コマンド実行結果やゲーム内コマンドのログが転送されない。

### 原因
パイプ接続時、javaはブロックバッファリング（デフォルト4KB〜64KB）を使用する。
- 起動時: 大量のログ出力でバッファがフラッシュされる → 転送される
- 運用時: 個別のログ行はバッファに溜まったまま → 転送されない

### 解決策
`stdbuf -oL` でstdoutをline bufferedに強制する。

```sh
# /opt/minecraft/paper/start.sh
#!/usr/bin/env sh
stdbuf -oL java @user_jvm_args.txt -jar paper.jar "$@"

# /opt/minecraft/forge/start.sh
#!/usr/bin/env sh
stdbuf -oL ./run.sh "$@"
```

### 重要な教訓

1. **パイプ接続時のバッファリング動作**
   - ターミナル接続: line buffered（行単位でフラッシュ）
   - パイプ接続: block buffered（バッファが満杯でフラッシュ）

2. **`stdbuf -oL` の意味**
   - `-o`: stdout に対して
   - `L`: line buffered モードを強制

3. **注意点**
   - `stdbuf` はLinux coreutilsに含まれる（macOSでは `gstdbuf`）
   - リモートサーバー上のstart.shを直接編集する必要がある（GitHubリポジトリ外）
