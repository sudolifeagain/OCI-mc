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
シェルスクリプト経由でJavaを起動すると、ログがDiscordに転送されない。

### 原因
シェルスクリプト経由の起動では、シェルのバッファリングによりstdoutがブロックバッファになる。
- `stdbuf -oL`、`unbuffer`、`script` コマンドは **Javaには効かない**（Javaは独自のI/Oシステムを使用）

### 解決策
**Paperは直接Java起動、Forgeはスクリプト起動**

```python
# server_manager.py
if self.use_script:
    cmd = [self.use_script, 'nogui']  # Forge用
else:
    cmd = ['java', f'-Xmx{self.memory}', f'-Xms{self.memory}', '-jar', self.jar, 'nogui']  # Paper用
```

```json
// config.json
"paper": { "jar": "paper.jar", "memory": "4G", ... }  // 直接起動
"forge": { "use_script": "./run.sh", ... }            // Forgeデフォルトスクリプト
```

### 重要な教訓

1. **直接起動 vs スクリプト起動**
   - 直接起動: Javaのstdoutが直接パイプに接続 → リアルタイム転送
   - スクリプト起動: シェルを経由 → バッファリング問題

2. **試みたが効果がなかった方法**
   - `stdbuf -oL`: Javaは libc を使わないため無効
   - `unbuffer`: 同様に無効
   - `script -qfc`: 制御文字が混入する問題
   - log4j2設定変更: Paper側で上書きされる

3. **Forgeがスクリプト必須な理由**
   - `run.sh` がForge起動に必要なクラスパス等を設定している

---

## Forgeのログ転送無効化 (2026-01)

### 問題
Forgeサーバーはシェルスクリプト経由でしか起動できず、stdoutバッファリング問題が解決できない。
また、大量のMOD（約590個）により起動時にログが大量出力され、Discord送信が追いつかない。

### 決定
Forgeのログ転送を無効化する。

### 理由
1. シェルスクリプト経由ではリアルタイムログ転送が不可能
2. コマンド実行結果も見えないため、ログ転送の価値が低い
3. 大量のログがキューに溜まり、メモリ消費・送信遅延の原因になる

### 実装

```json
// config.json
"forge": {
    "log_forwarding": false,  // ログ転送を無効化
    ...
}
```

```python
# server_manager.py
self.log_forwarding = config.get("log_forwarding", True)

# basic_control.py - discord_log_sender
if not server_instance.log_forwarding:
    # キューを空にしてスキップ（メモリリーク防止）
    while not server_instance.log_queue.empty():
        server_instance.log_queue.get_nowait()
    continue
```

### 注意点
- デフォルトは `true`（後方互換性維持）
- 無効化時もキューは空にする（stdoutは読み続けるため）
- Forgeのログ確認はSSHで `tail -f /opt/minecraft/forge/logs/latest.log`
