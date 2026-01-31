# Writing style (必須)
ドキュメント、コミットメッセージ、PR/Issue/レビューコメントは以下を遵守:
- **である調を使用**: ですます調は使わない
- **絵文字禁止**: 不要な絵文字は使わない
- **簡潔に**: 冗長な表現を避け、要点のみ記述
- **プロフェッショナルな口調**: 曖昧な表現や感情的な表現を避ける

例:
- ✗ 「〜を追加しました！」 → ✓ 「〜を追加」
- ✗ 「〜かもしれません」 → ✓ 「〜の可能性がある」

# Pre-commit checklist (必須)
コード変更後、push前に必ず実行:
```bash
ruff check . --select=E,F,W --ignore=E501 --exclude=venv
```
- CIで同じチェックが走るため、ローカルで通らないコードはpushしない
- `--fix` オプションで自動修正可能

# Bash commands
- `pip install -r requirements.txt`: Install dependencies
- `python bot.py`: Run bot locally (requires .env)
- `ssh -i ~/.ssh/id_ed25519 ubuntu@<OCI_IP>`: SSH into server
- `ps aux | grep java`: Check running server processes (remote)

# Code style
- **Slash Commands**: Use `@app_commands.command` (no prefix commands)
- **Type Hints**: Required for all arguments and returns
- **Async**: Use `async/await` for all I/O functions

# Workflow
- **Branch Strategy**: `develop`で開発 → PRで`main`にマージ → 自動デプロイ
  - **featureブランチは作成しない**: すべての開発は`develop`ブランチで直接行う
  - `main`への直接pushは禁止
- **Deploy**: Push to `main` to trigger OCI deployment via GitHub Actions
- **Secrets**: Do NOT commit real IPs or keys. Use `.env` or GitHub Secrets
- **Documentation**: See `.agent/infrastructure.md` for server paths and `.agent/development.md` for architecture details

# Skills
`.claude/skills/`にドメイン固有の知識がパッケージ化されている。関連タスクで自動的にトリガーされる。
- `/deploy`: デプロイワークフロー、CI、トラブルシューティング
- `/process-management`: サーバープロセス管理、ログ転送の仕組み
- `/backup-notion`: バックアップ・復元、Notion連携
