# インフラストラクチャ管理

OCI側のリソース情報はTerraform、Ubuntuゲスト内部はAnsibleで管理する構成である。Terraformの`remote-exec`や`local-exec`は使用せず、OS設定をTerraform stateへ混在させない。

## Terraform

`infra/terraform`は既存Computeインスタンスを読み取り、対象コンパートメント、稼働状態、シェイプを検証する。stateとOCIDは公開リポジトリへ保存しない。OCI Resource Managerで次のスタック変数を機密扱いで設定する。

Terraform CLIはOCI Resource Managerが対応する1.5.7へ固定する。OCI Providerは8.24.0へ固定し、DependabotのPRで更新可否を検証する。

- `region`
- `compartment_ocid`
- `instance_ocid`

OCIリソースを今後コード管理へ移す場合はResource ManagerのResource Discoveryで生成した構成を別PRで精査する。既存リソースを手作業で再定義して即時適用することは禁止する。

## Ansible

`infra/ansible/playbooks/host.yml`は本番デプロイ中にOCIホスト自身で実行する。次を冪等に管理する。

- Ubuntu 24.04の管理パッケージ
- タイムゾーン、swap、unattended-upgrades
- Minecraft実行ユーザーとファイル権限
- Discord Botと保守再起動のsystemd unit

保守再起動は毎日05:30（Asia/Tokyo）に`/run/reboot-required`を確認する。オンラインプレイヤーがいる場合、またはバックアップ条件を満たさない場合は延期する。再起動前に稼働中サーバーをRCONで正常停止し、起動後にBotと対象ポートの復旧を確認する。

## 公開リポジトリ

秘密鍵、ホスト名、OCID、Terraform state、plan、`terraform.tfvars`はコミットしない。デプロイ用SSH情報はGitHubの`production` Environment secrets、Terraform変数とstateはOCI Resource Managerで管理する。
