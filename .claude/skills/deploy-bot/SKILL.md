---
name: deploy-bot
description: Deploy workflow for OCI-mc Discord bot. Use when deploying code, pushing to master, checking GitHub Actions, or troubleshooting deployment failures.
---

# Deploy Bot

## Quick Start

```bash
# 1. lint確認（必須）
ruff check . --select=E,F,W --ignore=E501 --exclude=venv

# 2. developにpush
git push origin develop

# 3. PRを作成してmasterにマージ
gh pr create --base master --head develop
```

## Workflow

### ブランチ戦略

| ブランチ | 用途 | CI | デプロイ |
|---------|------|-----|---------|
| `develop` | 開発用 | lint/構文チェック | なし |
| `master` | 本番用 | lint/構文チェック | OCI自動デプロイ |

### デプロイフロー

```
developで開発
    |
ruff check . でlint確認
    |
git push origin develop
    |
CI成功を確認
    |
gh pr create --base master
    |
PRマージ
    |
GitHub Actionsが自動デプロイ
    |
rsync → /opt/minecraft/bot/
    |
systemctl restart discord-bot
    |
Discordに通知
```

## Important Notes

1. **masterへの直接pushは禁止** - 必ずdevelop経由でPRを作成する
2. **lint必須** - CIで同じチェックが走るため、ローカルで通らないコードはpushしない
3. **masterへのpushはサーバー再起動を伴う** - Minecraftサーバーも一時停止する（auto-start機能で自動復旧）

## Related Files

- `.github/workflows/deploy.yml` - デプロイワークフロー
- `.github/workflows/ci.yml` - CI設定
- `CLAUDE.md` - コード規約・チェックリスト
- `.agent/development.md` - ブランチ戦略詳細

## Troubleshooting

### CI失敗時

```bash
# エラー内容を確認
gh run view --log-failed

# ローカルでlint実行
ruff check . --select=E,F,W --ignore=E501 --exclude=venv

# 自動修正
ruff check . --fix
```

### デプロイ失敗時

```bash
# GitHub Actions ログ確認
gh run list --limit 5
gh run view <run_id> --log

# リモートでサービス状態確認
ssh ubuntu@<OCI_IP> "sudo systemctl status discord-bot"
ssh ubuntu@<OCI_IP> "sudo journalctl -u discord-bot -n 50"
```

### デプロイ後にボットが起動しない

1. `.env`ファイルの存在確認
2. `DISCORD_TOKEN`の有効性確認
3. Python依存関係の確認: `pip install -r requirements.txt`
