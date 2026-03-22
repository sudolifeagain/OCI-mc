#!/usr/bin/env bash
# Discord プラグイン (claude-plugins-official) へのカスタムパッチ
# 冪等: 適用済みなら何もしない。claude-restart / claude-start 時に毎回実行される。
set -euo pipefail

PLUGIN_DIR="$HOME/.claude/plugins/cache/claude-plugins-official/discord"

for f in "$PLUGIN_DIR"/*/server.ts; do
  [ -f "$f" ] || continue

  # 1. import に MessageFlags を追加
  if ! grep -q "MessageFlags" "$f"; then
    sed -i 's/  type Attachment,/  type Attachment,\n  MessageFlags,/' "$f"
  fi

  # 2. reply の ch.send に SuppressNotifications フラグを追加
  if ! grep -q "SuppressNotifications" "$f"; then
    sed -i 's/const sent = await ch\.send({/const sent = await ch.send({\n              flags: MessageFlags.SuppressNotifications,/' "$f"
  fi

  # 3. allowRoles によるロールベースアクセス制御を追加
  if ! grep -q "allowRoles" "$f"; then
    perl -i -0pe '
      s/(  if \(requireMention \&\& !\(await isMentioned)/  const allowRoles = (policy as any).allowRoles ?? []\n  if (allowRoles.length > 0 \&\& !allowRoles.some((r: string) => msg.member?.roles.cache.has(r))) {\n    return { action: '\''drop'\'' }\n  }\n$1/
    ' "$f"
  fi

  echo "patched: $f"
done
