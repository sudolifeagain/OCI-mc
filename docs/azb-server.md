# azb サーバー

azb は既存 Paper とは別のワールドであり、Discord のサーバー選択肢は `azb`、内部 ID は `paper-new` である。Java 版の認証とホワイトリストを有効にし、統合版は Geyser と Floodgate を経由する。初期ホワイトリストは既存 Paper からコピー済みである。

- Paper 26.3 build 244、Java 25
- Geyser 2.12.1 build 1292、Floodgate 2.2.5 build 141
- サーバーディレクトリ: `/opt/minecraft/paper-new`
- ワールドのバックアップ対象: `world`
- Discord ログ: 環境変数 `DISCORD_AZB_LOG_CHANNEL_ID` で指定する専用チャンネル。未設定時は他のチャンネルに転送しない

Java のゲームポートと RCON ポートは既存 Paper と共用するため、両サーバーは同時に起動できない。起動前にもう一方を停止する。Bot は管理対象サーバー間でゲームポートまたは RCON ポートが重複する場合、起動を拒否する。

統合版は NetherNet を使用する。ホストの TCP と UDP の両方を許可し、OCI 側のセキュリティリストまたはネットワーク・セキュリティ・グループでも接続を許可する必要がある。初回接続時に信頼確認が表示される場合がある。

既存 Paper 用の `server-artifacts.json` と自動更新ワークフローは azb の jar を更新しない。azb の Paper を更新する際は対応版と配布元のハッシュを確認し、別途更新する。Geyser と Floodgate は Bot のプラグイン管理対象である。
