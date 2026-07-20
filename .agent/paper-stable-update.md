# Paper 26.2 STABLE自動更新

## 対象範囲

- Minecraft versionは`26.2`に固定する
- Paper Downloads Serviceの`STABLE` channelのみを対象とする
- Forge、NeoForge、MOD、プラグインは変更しない
- 新しいMinecraft versionには自動追従しない

## 実行フロー

1. GitHub Actionsが毎日12:17 JSTにPaper公式APIを確認する
2. 26.2の最新STABLEが未適用の場合のみjarを取得してSHA-256を検証する
3. `server-artifacts.json`だけを更新して`develop`へpushする
4. `develop`から`main`へのPRを作成する
5. 必須CIの成功後、管理者権限でレビュー要件だけを明示的にbypassしてPRをmergeする
6. `main`へのpushを契機にOCIへデプロイする
7. OCIでもjarを再取得してSHA-256を検証する
8. 起動確認に失敗した場合は旧jarへrollbackする
9. 起動確認に成功した場合は旧jarとrollback stateを削除する

`develop`に未マージ変更がある場合は自動更新を停止する。自動更新が別の変更を巻き込むことを防ぐためである。

## GitHub認証

Repository secret `PAPER_UPDATE_TOKEN`を使用する。Fine-grained personal access tokenを単一repositoryに限定し、以下の最小権限を付与する。

- Contents: Read and write
- Pull requests: Read and write
- Actions: Read
- Commit statuses: Read

token所有者はrepository管理者である必要がある。`main`のbranch protectionでは管理者への強制が無効であるため、必須CI成功後に限りレビュー要件をbypassできる。通常のPRに対する1名の承認要件は維持する。

## 参照仕様

- Paper Downloads Service: https://docs.papermc.io/misc/downloads-service/
- GitHub Actions schedule: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
- GitHub Actions token再帰実行制限: https://docs.github.com/en/actions/concepts/security/github_token#when-github_token-triggers-workflow-runs
