# Forge起動時の互換修復

Minecraft 1.20.1 / Forge 47.4.28を維持し、配布版MODで置換できる箇所を優先する。クライアント535 jar、専用サーバー533 jarとなる。既存のデータ修復942件にBetterNetherのPatchouli形式修復1件を追加し、計943リソース・58 jarとなる。これは独自クラス修正の本数とは別である。

## 配布版の更新

| MOD | 更新前 | 採用版 | 両側への影響 |
| --- | --- | --- | --- |
| Clockwork | 0.5.4.5 | 0.5.6 | 共通MODとして両側で更新する。VS2 2.4.10とKelvinを維持し、切断時にnullableな船舶ワールド参照を使用する |
| AllTheLeaks | 1.1.1と旧0.1.2.1の重複 | 1.1.4 | 旧ファイル2本を置換する。Citadel・Iron's Spells連携でのリフレクション失敗を解消する |
| Amendments | 2.2.5 | 2.2.6 | 両側で更新する。カスタムケーキには別途リソース修復が必要である |
| Moonlight | 2.16.27 | 2.16.37 | 両側で更新する。クライアントのみ診断レベルの修正を加える |

配布URLとSHA-256は[更新計画](../infra/forge-startup-updates.json)に固定する。[Clockwork公式リリース](https://github.com/ValkyrienSkies/Clockwork/releases/tag/release%2F0.5.6)と[AllTheLeaks配布ファイル](https://www.curseforge.com/minecraft/mc-mods/alltheleaks/files/9188826)を採用する。Clockworkは共通機能の更新であるため、変更前のワールド・MOD・設定を一括退避する。

TFMG 1.0.2fと配布版TFMG Optimizer、JEI 15.20.0.129の5000ミリ秒の遅延通知を維持する。PowerGridは配布jarとWindowsランタイムDLL 3本、Epic Fightは通常のコスメ同期無効設定を使用する。

## 起動時69記録の処置

対象は更新前の認証済み接続テストで、接続開始前に記録されたERROR/FATAL 69件である。69個の独立したゲーム不具合という意味ではない。

| 記録数 | 原因 | 処置 |
| --- | --- | --- |
| 35 | 不正・空モデルと制作途中の出力 | 実際に使う形状・参照先を修復し、未使用出力を個別除外する |
| 6 | XKDecoの存在しないwardrobeテンプレート | 未登録YAML 6個のみ除外する。クラスと完成済みブロックを維持する |
| 5 | EFIS Compatの未登録アニメーション出力 | クラスから参照されない出力5個のみ除外する |
| 4 | PNG拡張子で保存されたBlockbench JSON | 誤った制作データのみ除外する |
| 3 | TrustManagerがWindowsの取得元を読めない | 配布版ApexCore/Commonalityを同一バージョンの外部jarとして配置し、照合済み取得元メタデータを復元する。信頼判定クラスは変更しない |
| 3 | AllTheLeaksの旧連携 | 配布版1.1.4へ更新する |
| 2 | ケーキのテクスチャ規約の差 | 元の形状・UVを維持し、生成器が要求する名前を補う |
| 2 | 翻訳説明書のJSON構文 | 本文とリンクを保持して修復する |
| 1 | 音楽ディスクのアニメーションと静的パレットの寸法差 | GUIの元モデル・アニメーションを保持し、抽出用に先頭フレームを別参照する |
| 1 | IAF Patcherの不正なShadow remap | animatorフィールドの注釈1個を修正する。配布版1.2にも問題が残る |
| 1 | Lightspeedの破損キャッシュ | 当該ファイルだけを退避して再生成する |
| 1 | BetterNetherの旧Patchouli形式 | use_resource_packをtrueにする。元の配布物は仮のヘッダーだけで、ページは存在しない |
| 1 | BetterCombatのフォルダーに混入したアイテムモデル | 誤配置ファイルのみ除外する |
| 1 | CorgiLibの告知URLの301応答 | 作者の現行HTTPS取得先へ接続し、リダイレクトを処理する。404/410は告知なし、その他の失敗はERRORとする |
| 3 | Embeddium・Moonlightのサポート注意書きと、BCLibが除外済みのAIR入力スタック | 詳細とスタックを保持してWARNへ分類する。互換性問題の解消とは数えない |

リソースパックは29件の置換と30件の個別除外である。[対象と理由](../infra/forge-client-resource-fixes.json)を記録する。全体のERRORを抑制するログフィルターは使用しない。

## 残す独自修正

クラス修正を含むクライアントjarは6本である。BCLibのレジストリ参照、Supplementariesの袋16色、IAF Patcherの注釈、CorgiLibの告知通信に動作上の修正を残す。BCLibには診断変更を追加し、EmbeddiumとMoonlightには診断変更だけを加える。[変更前後のハッシュとクラス一覧](../infra/forge-client-compatibility.json)、[今回の変換ソース](../tools/forge-client-startup/PatchStartup.java)を記録する。サーバーにこれらのクライアント向けクラス修正を適用しない。

Lightspeed 2.0.5+hotfixは大量の新規リソースパスエラーを発生させたため採用しない。XKDeco 0.9.8、Kiwi 11.10.3、CorgiLib 4.0.3.6、Epic Fight 20.14.17、Mushroom Quest・Serene Shrubbery等の新しい配布版も該当資源・実装の代替にならず、不要な更新を避ける。BCLib・BetterEnd・BetterNetherの大きな更新も従来のリモートクライアント相当のハンマー参照問題を解消せず、見送る。

## 検証と復旧

2026-10-23の隔離クライアント実行では、対象69記録が接続前の起動段階で再発しなかった。既存の機能13項目、切断後のタイトル復帰と10秒のtick、モデル7種類、ケーキの生成済みモデル21個を確認した。検証agentはゲームAPIを呼ぶだけで、MODクラスを書き換えない。

新規ローカルワールド作成後には別のERROR 11件がある。既存データ・設定に起因する8件とJEIの5秒通知3件であり、対象69記録には含まれない。全実行期間のERRORが0件という判定ではない。BetterNetherの仮説明書のカテゴリ・ページ数は0であり、内容のある説明書が読めたという検証ではない。前回の認証済みマルチプレイ成功は旧構成の結果であり、今回の証拠として流用しない。

本番更新は稼働状態保存・RCON正常終了・ボット停止・新規全体バックアップ・配布物照合・更新・ボット起動の順序を使う。旧MODを個別退避し、起動・MOD集合・RCONの検証後に確定する。失敗時はJSON修復を戻した後にMOD更新を戻してボット経由で起動する。ワールド復旧が必要な場合は直前の全体退避またはNotionバックアップを使う。

ZIPはWindowsの取得元ストリームを保持しないため、展開後にPrepare-Client.ps1を実行する。MODと配布元のハッシュ照合後に取得元を再設定する方式であり、TrustManagerを無効化しない。
