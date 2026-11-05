# 10 Key Actors — Phase B Source Registry（進行中）

2026-08-30。詳細データは`actor_source_registry.json`（正本）。本ファイルはCoverage Matrixと
実施状況のサマリ。

## 設計判断: DB migrationなし、ファイルベース管理

既存`geo_source_registry`テーブルは`theme_id`必須(FK)・単純な`source_tier`(一次/二次/三次/四次)
の4値enum・`source_type`(official_site/news/sns/commercial_data/other)という、Theme単位の
監視ソース登録用途の簡易schemaであり、今回必要な`actor_refs[]`/`content_types[]`/
`historical_start_year`/`crawlable`/`access_method`等のリッチな構造とは合致しない。依頼書
自体も保存先をファイル（`.md`+`.json`）として明示していたため、**新規テーブル追加・DB
migrationは行わず、`docs/actor-intelligence/actor_source_registry.json`をファイルベースの
正本とする**。ここでの`source_id`はこのファイル内限定のローカル識別子であり、DB上のFK制約は
持たない。

## 実施状況（2026-08-30、全10名完了）

| Actor | Source件数 | 主なTier1 | 主なアクセス障害 |
|---|---|---|---|
| Donald Trump | 20件 | whitehouse.gov/trumpwhitehouse.archives.gov(NARA)/UN/NATO/UCSB APP | Tier3西側大手（FT/WSJ/NYT/WaPo/Bloomberg）Paywall |
| Xi Jinping | 20件 | gov.cn/MFA記者会見/Xinhua/SCIO | 全人代(NPC)英語サイトTLS失敗、Reuters遮断 |
| Vladimir Putin | 18件 | kremlin.ru(存在確認済み、直接アクセス不可) | **kremlin.ru含む全.ruドメインが本環境からECONNREFUSED（制裁関連の可能性）** |
| Kim Jong Un | 14件 | KCNA Watch（KCNA/Rodong公式は不安定、ミラー経由が実用解） | KCNA公式サイト自体が到達不能（既知の不安定性） |
| Volodymyr Zelenskyy | 15件 | president.gov.ua（存在確認済み、直接アクセス不可） | president.gov.ua/Rada.gov.uaがECONNRESET |
| Benjamin Netanyahu | 15件 | PMO公式サイト（存在確認済み、直接アクセス不可） | gov.il系が403（Google経由到達は可能） |
| Mohammed bin Salman | 14件 | MOFA Saudi/GCC事務局（検証済み）、SPA/Vision2030/PIF（403） | Vision2030/PIF/Al Arabiyaが403（Cloudflare等Bot対策） |
| Emmanuel Macron | 15件 | elysee.fr/diplomatie.gouv.fr（検証済み、robots.txt寛容） | consilium.europa.eu(EU理事会)が403 |
| Friedrich Merz | 16件 | bundesregierung.de/bundeskanzler.de/auswaertiges-amt.de（検証済み） | cdu.deがECONNREFUSED、独主要紙は軒並みツール側ブロック |
| 高市早苗 | 18件 | kantei.go.jp（検証済み）、国会会議録検索（JS SPA） | mofa.go.jpが403、japantimes.co.jpが402、jimin.jpがTLS証明書エラー |

**合計165件**（Tier1: 約35件、Tier2: 約20件、Tier3: 約95件、Tier4: 10件）。

## 横断的に判明した重要事項（全Actor共通の構造的制約）

1. **主要西側通信社・大手紙はツールレベルで軒並みブロックされる**: Reuters/AP/BBC/NYT/WSJ/Financial
   Times/Bloomberg/Politico (Europe)/Euractiv/Der Spiegel/FAZ/Le Monde/Le Figaroは、ほぼ全Actorの
   調査において"Claude Code is unable to fetch from ..."という**ツール側の一律拒否**に遭遇した
   （サイト自体が落ちているのではなく、この環境のWebFetchツールが主要ニュース発行元ドメインを
   一括ブロックしている可能性が高い）。一方でMeduza/Novaya Gazeta Europe/Kyiv Independent等の
   独立系・亡命系メディアは問題なくfetchできており、単純な「大手だから遮断」ではなく特定ドメイン
   リストによるブロックと推測される。**本番運用でTier3西側メディアを継続的に取り込むには、
   RSS/ライセンスAPI等の別経路が必須**（このツールでの直接クロールに依存できない）。
2. **ロシア公式(.ru)ドメインは本環境からネットワークレベルで完全遮断**（kremlin.ru/mid.ru/tass.ru/
   ria.ru/kommersant.ru/rbc.ru全てECONNREFUSED）。EUのTASS配信制限方針等、制裁関連のネットワーク
   制約の可能性がある。Putinの一次資料は「存在は検索で確認できるが直接取得はできない」状態。
3. **各国政府公式サイトのアクセス可否はまちまち**: 米国(whitehouse.gov)・中国(gov.cn/MFA/Xinhua)・
   フランス(elysee.fr/diplomatie.gouv.fr)・ドイツ(bundesregierung.de)・日本(kantei.go.jp)は
   直接WebFetchで検証成功。イスラエル(gov.il)・サウジ(SPA/Vision2030/PIF)・ウクライナ
   (president.gov.ua)・EU理事会(consilium.europa.eu)はBot対策(403)またはネットワークエラー
   (ECONNRESET)で直接検証できず、検索経由での存在確認のみ。
4. **JS-heavy SPA型サイトはWebFetchで内容が取得できない**: 全人代(中国)、国会会議録検索システム
   (日本)がこのパターンに該当。手動ブラウザまたは別クローラーが必要。

## Donald Trump — 主な発見事項

- **Tier 1最重要**: `trumpwhitehouse.archives.gov`（NARA、第一期2017-2021の完全凍結アーカイブ）
  と現行`whitehouse.gov`（第二期2025-）が分離。The American Presidency Project (UCSB)は
  188,437件を横断索引、Trumpは「1st Term」「2nd Term」の別entryで管理されている。
- Truth Social投稿もUCSBのAPP経由で日付別permalink化されており、直接プラットフォームより
  安定して取得可能。
- NATO公式Text archiveは本人発言と独立した制度的合意の一次記録として高価値。
- Tier3はReuters/AP/BBCが概ねオープン、FT/WSJ/NYT/WaPo/Bloombergは有料壁あり
  （WaPoのFact Checkerデータベースは「発言と事実の乖離」検証に特に有用）。

## Xi Jinping — 主な発見事項

- **Tier1**: gov.cn／MFA記者会見（`/eng/xw/fyrbt/lxjzh/`、推測URLは404だったため要注意）／
  Xinhua（演説全文の一次配信元）／SCIO（UNGA演説等の全文アーカイブ）。
- **Tier3の性質の違いを明確に区別**（依頼書の指摘通り、これらを同一視しない）:
  - Xinhua/人民日報＝最も統制された公式トーン
  - Global Times＝民族主義的・対決的、対台湾強硬シグナルのテスト媒体として機能
  - Caixin＝経済・財政面で相対的に独立した分析視点（部分Paywall）
  - SCMP＝香港拠点、相対的に西側寄りの報道（混在Paywall）
  - CNA/Taipei Times＝台湾側の直接カウンターパート視点
- **アクセス障害**: 全人代(NPC)英語サイトはTLS handshake failureで2回とも取得失敗（geo-block
  ではなくlegacy TLS設定の可能性、別クライアントでの再確認が必要）。Reuters.comは2025年以降の
  AIクローラーブロックポリシーによりWebFetch直接失敗（検索スニペット経由のみ利用可）。

## 他8名の主な発見事項（詳細は`actor_source_registry.json`参照）

- **Vladimir Putin**: kremlin.ru（2000年以降の英語逐語記録、存在・URLパターンは検索で確認したが
  本環境からは直接アクセス不能）。Meduza/Novaya Gazeta Europe（亡命系、検証済み）が実質的に
  最も信頼できる独立系Tier3。
- **Kim Jong Un**: 北朝鮮公式（KCNA/Rodong）は運用上不安定。KCNA Watch（NK Newsによる
  ミラー、150万件超のアーカイブ、1996年まで遡及）が実用上の代替一次情報源として最重要。
- **Volodymyr Zelenskyy**: president.gov.ua（存在確認済みだがECONNRESET）。Kyiv Independent
  （2021年設立、検証済み、無料・英語ネイティブ）が最も信頼できるTier3。
- **Benjamin Netanyahu**: PMO公式（403）。Times of Israel/Jerusalem Post/Haaretzで意図的に
  政治的立場の異なる3紙を検証済み。
- **Mohammed bin Salman**: MOFA/GCC事務局（検証済み）。Al Arabiya/Asharq Al-Awsat/Arab News
  はいずれも国営・王室系（独立系ではない）と明記。Al Jazeeraが唯一の非サウジ系カウンターポイント。
- **Emmanuel Macron**: elysee.fr（robots.txt寛容、8種のsitemap、2024年ソルボンヌ演説全文
  検証済み）。Le Monde（中道左派）/Le Figaro（中道右派）を意図的に分離。
- **Friedrich Merz**: 2025年5月就任のため本人任期のアーカイブは浅い。Bundestag DIP
  （JSON/XML API、1949年まで遡及）が首相就任前(1994-2009, 2021-2025)の議員歴を辿る鍵。
- **高市早苗**: kantei.go.jp検証済み。国会会議録検索システムは1993年の初当選まで遡れるが
  JS SPAのため自動クロール不可。読売/朝日/毎日/産経で政治的立場の異なる論調を意図的に分離。

## Donald Trump — 主な発見事項

- **Tier 1最重要**: `trumpwhitehouse.archives.gov`（NARA、第一期2017-2021の完全凍結アーカイブ）
  と現行`whitehouse.gov`（第二期2025-）が分離。The American Presidency Project (UCSB)は
  188,437件を横断索引、Trumpは「1st Term」「2nd Term」の別entryで管理されている。
- Truth Social投稿もUCSBのAPP経由で日付別permalink化されており、直接プラットフォームより
  安定して取得可能。
- NATO公式Text archiveは本人発言と独立した制度的合意の一次記録として高価値。
- Tier3はReuters/AP/BBCが概ねオープン、FT/WSJ/NYT/WaPo/Bloombergは有料壁あり
  （WaPoのFact Checkerデータベースは「発言と事実の乖離」検証に特に有用）。

## Xi Jinping — 主な発見事項

- **Tier1**: gov.cn／MFA記者会見（`/eng/xw/fyrbt/lxjzh/`、推測URLは404だったため要注意）／
  Xinhua（演説全文の一次配信元）／SCIO（UNGA演説等の全文アーカイブ）。
- **Tier3の性質の違いを明確に区別**（依頼書の指摘通り、これらを同一視しない）:
  - Xinhua/人民日報＝最も統制された公式トーン
  - Global Times＝民族主義的・対決的、対台湾強硬シグナルのテスト媒体として機能
  - Caixin＝経済・財政面で相対的に独立した分析視点（部分Paywall）
  - SCMP＝香港拠点、相対的に西側寄りの報道（混在Paywall）
  - CNA/Taipei Times＝台湾側の直接カウンターパート視点
- **アクセス障害**: 全人代(NPC)英語サイトはTLS handshake failureで2回とも取得失敗（geo-block
  ではなくlegacy TLS設定の可能性、別クライアントでの再確認が必要）。Reuters.comは2025年以降の
  AIクローラーブロックポリシーによりWebFetch直接失敗（検索スニペット経由のみ利用可）。

## 次のアクション

レート制限解除後、残り8名（Putin/Kim/Zelenskyy/Netanyahu/MBS/Macron/Merz/高市早苗）の
Source Universe調査を再実行する。
