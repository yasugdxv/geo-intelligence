# Media Intelligence Build — Stop Gate 1 報告（M0〜M4完了）

依頼書Section39の指示通り、M0〜M4まで進めてここで一度停止する。以下14項目を報告する。

## 1. Existing Architecture

[00_media_intelligence_architecture.md](00_media_intelligence_architecture.md)参照。
最重要の発見: **per-caseのMedia/Information Reasoning（Observed/Hypothesis分離、
Source Attribution、Beneficiary Guard、Anonymous Source Confidence Cap等）は
Session2 Reasoning Upgradeで既に実装済み。** 今回新規に必要なのは「安定層」
（Media Source Registry / Media Profile / Historical Media Episode / Media Pattern
Candidate）のみ。既存`geo_source_registry`（DBテーブル）はtheme_id必須・型固定
カラムでMedia用には不向きと判断し、Actor Source Registryと同じくfile-baseで構築した
（DB migration不要）。

## 2. Media Universe

[01_media_universe.md](01_media_universe.md)参照。7地域区分・65媒体
（Global Core 12 / Middle East 13 / Russia-Ukraine 9 / China-Taiwan 9 / Korea 6 /
Europe 8 / Japan 8）。

## 3. Specialist / Alternative Universe

[02_specialist_alternative_universe.md](02_specialist_alternative_universe.md)参照。
12 Lensのうち、Lens1(Official)/Lens6(Shipping等一部)/Lens7(Sanctions)/Lens11(Academic)
は既存`geo_source_registry`・既存Actor Source Registryの再利用で対応し、新規登録は
約26件（Establishment Strategic Analysis 7・OSINT/Physical/Humanitarian Data 7・
Regional Local Expert 6等）。Lens10(Religious/Ethnic/Tribal/Non-state)は体系的ソース
なくGapとして明示。

## 4. Accessibility Audit

[03_source_accessibility_audit.md](03_source_accessibility_audit.md)参照。5並列調査
Agent + 前タスク既存知見を統合。**失敗には複数の異なる性質があることが判明**
（汎用WebFetch失敗=bot検知疑い／明確な403／503(WAF疑い)／DNS失敗・ECONNREFUSED
(ネットワーク遮断)／TLS証明書エラー／実際のHTTPペイウォール応答(402/451)／
JS-heavyで技術的に取得困難）。403/503は「人間のブラウザでは無料アクセス可能」な
場合が多いことをWebSearchで複数件裏取り済み（RAND, ReliefWeb, OpenCorporates,
Ukrainska Pravda等）。

## 5. Source Registry

[media_source_registry.json](media_source_registry.json) — 91件
（media 69 / specialist 22）、`crawlable=true` 35件・`false` 54件・未検証2件
（Taiwan United Daily News/Liberty Times）。

## 6. Region × Lens Coverage

[04_region_lens_coverage.md](04_region_lens_coverage.md)参照。

## 7. Local-language Coverage

中国語(Xinhua/人民日報等)・ロシア語(TASS等、ただしアクセス不可)・ペルシャ語
(IRNA/Tasnim/Fars)・アラビア語(Asharq Al-Awsat/Al Jazeera)・ヘブライ語(Haaretz/Ynet)・
ウクライナ語(Suspilne/Ukrainska Pravda、ただしアクセス不可)・韓国語(Yonhap等、
ただしアクセス不可)・日本語(NHK/日経等、多くはアクセス不可)を候補に含めた。
実際に直接WebFetchできた現地語ソースは限定的（Xinhua系・Global Times系が中心）。

## 8. Official / State-linked Coverage

Iran(IRNA/Tasnim/Fars/Press TV)・Russia(TASS/RIA/Kommersant/RBC)・China(Xinhua/
人民日報/Global Times/CGTN)・North Korea(KCNA/Rodong Sinmun)・Ukraine(Suspilne)を
Universeに含めたが、**直接WebFetchできたのはChina系のみ**。Iran/Russia/北朝鮮の
公式チャンネルはほぼ全滅（DNS遮断・TLS証明書エラー・接続拒否）— これらはMedia
Profile構築時にWebSearch裏取りまたは将来の代替アクセス経路（RSS/API個別契約）が
必須になる構造的制約として記録した。

## 9. Opposition / Exile Coverage

Meduza/Novaya Gazeta Europe(Russia)・Iran International(Iran)は到達可、
Kyiv Independent(Ukraine)も到達可。北朝鮮についてはkcna.kp自体は不可だが、
第三者運営のkcnawatch.orgミラーが到達可であることを確認し、代替アクセス経路として
登録した。

## 10. OSINT / Physical Coverage

Bellingcat・ACLED・UNHCR Data Portalは到達可。MarineTraffic(AIS)はJS-heavyで
タイトルのみ取得（ブロックではなく技術的制約、無料版でも基本機能は利用可とWebSearchで
裏取り）。Planet Labs(衛星画像)は公開情報が事例紹介レベルに限定され、生画像は有料契約
必須と確認。

## 11. Trade / Shipping / Sanctions Coverage

既存`geo_source_registry`のUN Comtrade/OECD TiVa/BIS Data/OFAC/EU Sanctionsを
そのまま再利用する方針。新規にMarineTraffic(AIS)・OpenCorporates(企業登記・実質的
支配者)を追加登録（OpenCorporatesはWebFetchでは403だが基本検索は無料と裏取り済み）。

## 12. Coverage Gap

1. Religious/Ethnic/Tribal/Non-state(Lens10): 体系的ソースなし、Pilot Case選定時に
   実際に必要になるか要確認。
2. 北朝鮮国内の独立系/現地専門家: 構造的に存在しない。
3. Counterparty/Third-country(Lens12): 固定Sourceでなく、Case実行時の運用ルールとして
   別途記録する必要あり（今回はUniverse登録なし、M9で運用ルール化）。
4. Taiwan United Daily News/Liberty Times: 未検証のまま残存。
5. 韓国4大紙・北朝鮮公式チャンネルの直接WebFetch不可 — RSS/API個別検証が今後の課題。

## 13. Pilot候補Case

既存`geo_cases`から、Actor Intelligence Buildで既に実データ構築が完了している
10 Actorのいずれかに関連し、かつ複数Lensでの検証に十分な情報環境の多様性を持つケースを
Pilot候補として提案する:
- **Zelenskyy関連（2025年2月オーバルオフィス衝突、または選挙延期論争）**: 米国(Global
  Core)・ウクライナ(domestic establishment、ただし直接アクセス困難)・ロシア(official
  state-linked、アクセス不可)・欧州(establishment)・専門家(Carnegie Russia Eurasia
  Center)・Actor strategic_communication_patternとの接続が既に実データで存在する。
- **高市早苗の台湾「存立危機事態」発言(2025年11月)**: 日本(domestic establishment、
  多くは直接アクセス困難)・中国(official state-linked、アクセス良好)・台湾(INDSR等
  含め比較的アクセス良好)・米国(Global Core)という情報環境の対比が明確。

いずれのCaseもユーザーの最終選定を仰ぐ。

## 14. Pilotで使うSource候補（依頼書Section33の目安に沿う）

Zelenskyy Caseを例にすると: Global media(Reuters/AP/BBC=アクセス不可のためWebSearch
併用) 2〜3、Regional media(Kyiv Independent到達可、Meduza到達可) 2〜4、
Official sources(Actor Registry既存のkremlin.ru系=不可、ukraine大統領府系) 1〜3、
Specialist(Carnegie Russia Eurasia Center到達可) 2〜4、Alternative/local
(Iran Internationalは無関係のため対象外、Meduza/Novaya Gazeta Europeで代替) 1〜3、
Physical/data(該当性low、必要ならACLED) 1〜3。

---

## ユーザー確認事項（Stop Gate 1、承認を得てからM5へ進む）

1. **上記Pilot候補2案のいずれか、または別の1 Caseを指定してほしい。**
2. **Stable Media Profile / Historical Media Episode / Media Pattern Candidateの
   永続化方式**（00章4節の案A: 既存`precedent`型を再利用しoptionalフィールド追加/
   案B: `media_profile`等の新knowledge_type追加という軽量CHECK制約migration）を
   どちらにするか、方針を決めてから先に進みたい。
3. 91件のSource RegistryのうちP0/P1優先度のもの・crawlable=false(特に403/503で
   人間ブラウザなら無料の可能性が高いもの)について、Gate2以降でRSS/API個別検証を
   追加で行うかどうか。
