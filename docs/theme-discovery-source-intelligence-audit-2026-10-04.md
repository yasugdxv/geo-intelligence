# Theme Discovery / Source Intelligence 基盤 — 現状調査・設計Audit

**実施日**: 2026-10-04
**性質**: 読み取り専用調査（コード変更・DB書き込み・migration・新規実装は一切行っていない）
**調査方法**: 3領域（①Source DB/Crawler/Scheduler、②Theme Discoveryパイプライン/差別化、③フロントエンドUI）を独立調査エージェントが並行監査。DB実データはread-onlyクエリで確認。

---

## 1. Executive Summary

**現状を一言で**: 「テーマ探索」が目指す機能（重要だがまだ報道されていないテーマの発見・優先順位付け）に相当するパイプライン（Source→GDELT→Episode Cluster→Theme Candidate→Expert AI Handoff）は**既に実装・Freeze済み**（Phase 2G-1/2G-2、`docs/theme-discovery-v1-freeze-manifest.md`、2026-10-01 Freeze）。しかし、その入力データであるGDELT収集パイプラインは**26日間停止**しており、常駐Schedulerは意図的に未導入（設計判断として文書化済み）。また「ニッチだが重要」を検出するスコアリング層は**存在しない**（単独Clusterは記事3件以上or明確なトレンドが無いと候補化されない設計）。

今回の構想は、**新規構築ではなく「止まっている既存パイプラインの再稼働＋スコアリング層の追加」**が本筋である可能性が高い。

---

## 2. Current Source Architecture

中核テーブルは`geo_source_registry`（`sql/2026-08-02_geo_core_schema.sql:55-81`、以降4回ALTER）。108件登録済み。

| 項目 | 有無 |
|---|---|
| source name / URL | ○ |
| source_type（official_site/news/sns/commercial_data/other） | ○ |
| source_tier（一次〜四次）/ trust_tier（A1〜E） | ○ |
| country/region | △ `geographic_scope`（自由記述text、構造化されたcountry/regionコードではない） |
| language | ○ `languages`(text[]) |
| topic/domain | △ `thematic_scope`（自由記述） |
| crawl frequency / last fetched | ○ `update_cadence` / `last_successful_ingestion_at` |
| credentials | ○ `credential_configured` / `credential_reference` |
| access restriction | ○ `reference_availability`（Human Review無しでExpert AIが参照してよいか） |
| **誰が登録したか（added_by相当）** | **× 存在しない** |
| metadata（汎用jsonb） | × 個別列で表現、汎用metadata列なし |

**「誰が登録したか」を追う列が無いことが最大のギャップ**。`source_owner`は発行元組織名（例: "World Bank"）であり、社内の担当者/部署を指すものではない。

関連して、台湾/Theater Entity監視向けの**103媒体の詳細メタデータ（国/言語/lens/crawlable有無等）を持つSource一覧が`docs/media-intelligence/media_source_registry.json`というJSONファイルのみで存在し、DB化されていない**。これは最も即戦力に近い未活用資産。

Sustainability側からの流用は、DBトリガー関数`set_updated_at()`1つのみ（`sql/2026-08-01_geo_shared_infra.sql`）。クロールコード自体は本リポジトリに存在せず、Source DB設計はGeo側で独自に行われている。

---

## 3. Current Crawl / Ingestion Architecture

| Collector | 取得方法 | 頻度(設計) | 現状 |
|---|---|---|---|
| World Bank / OFAC / IMF | 無認証REST/CSV | daily〜quarterly | automation_allowed=true、but最終成功45〜60日前 |
| UN Comtrade / WTO / Manifesto | Credential必要 | 未指定 | **APIキー未取得で実行不可** |
| GDELT DOC 2.0（News） | REST query単位 | daily想定 | 4 query有効、**最終実行2026-09-08（26日停止）** |
| GDELT raw Event/Mentions/GKG | HTTPバッチ | daily想定 | 同上、停止中 |
| 台湾公式サイト×6＋TSMC IR（`geo_official_crawler.py`） | HTMLスクレイプ+LLM抽出 | スクリプト手動実行 | コード健在、実行証跡は未確認 |
| CSV/Excel/JSON手動Import | 手動アップロード | オンデマンド | 有効（設計上手動専用） |

Source→Raw→Normalization→Dedup→Theme Candidateの実パイプライン（実コード基準）:

```
Source Registry → GDELT Connector → geo_external_signals（normalize_url/compute_dedup_key）
  → geo_external_events / geo_actor_episode_candidates
  → geo_episode_clusters（actor×quad_class×event_root_code、14日window）
  → geo_episode_cluster_links（Situation Link、evidence overlap重み付け）
  → geo_theme_candidates（sync_theme_candidates）
  → geo_theme_candidate_handoff_service → geo_ios_analysis_sessions（Expert AI Handoff）
```

**実装済みDedup/Normalization**: URL正規化、dedup_key（canonical_url→normalized_url→domain+titleのSHA256）、日付正規化（seendate vs 真の公開日時を区別）。
**未実装**: content hashによる本文重複判定、タイトルfuzzy類似度（完全一致のみ）、真のcanonical URL解決（リダイレクト解決なし）、クロスドメインsyndication検出、意味的（embedding）重複判定。

---

## 4. Current Scheduler

**本当に自動実行されているものはゼロ**。`.github/workflows/`なし、APScheduler/Celery/cron/pg_cron/Edge Function導入なし（全文検索0件）。

これは不具合ではなく**明示的な設計判断**：`docs/geopolitical-ingestion-operations.md:1-5`に「常駐Schedulerは導入しない。手動トリガー＋外部スケジューラ（Windows Task Scheduler等）からの定期呼び出しを前提とする」と明記。外部スケジューラが実際に設定されているかはリポジトリの管理対象外で、確認できない。

`geo_official_crawler.py`も`if __name__=="__main__"`のみで、常駐実行の仕組みは無い。

---

## 5. Current Data Health（実データ確認済み、2026-10-04時点）

| 指標 | 値 |
|---|---|
| Source登録数 | 108件（うち58件はベンチマーク検証用、実運用は約29件） |
| automation_allowed=true | 3件のみ |
| 直近7日間で成功したSource | **0件** |
| 一度も成功していないSource | 105/108件 |
| `geo_raw_snapshots`（Immutable原本） | **0件** |
| GDELT記事（`geo_external_signals`）直近7日新規 | **0件**（最新created_at=2026-09-08、26日停止） |
| `geo_ingestion_runs`直近7日 | 0件 |

**結論**: Source登録と実収集は一致していない。Theme DiscoveryはGDELTにのみ実質依存しており、その入力が26日前から止まっている。

---

## 6. Sustainability / Taiwan-China由来資産

- **Sustainability由来**: DBトリガー関数1つのみ。クロールコード自体の移植は無く、再利用可能な実装資産としては実質無い。
- **台湾/Theater Entity由来**: `geo_official_crawler.py`（6公式サイト+TSMC IR、スクレイプ+LLM抽出+指標計算）は**REUSE WITH ADAPTER**候補。`media_source_registry.json`（103媒体、未DB化）は**最優先で活用すべき資産**。
- GDELT External Signal層は設計としてTheme Discoveryの土台そのものだが、運用停止中でデータが古い。

---

## 7. Current Theme Discovery（Source→Event→Theme）

Theme Candidate生成は2経路：
- **(a) 複数Cluster横断**（Situation Link由来）: 記事数の閾値なし。evidence overlapの質で発火。
- **(b) 単独Cluster**: `member_count>=3`件、または明確なescalating/de-escalatingトレンドが無いと候補化**されない**（`_is_strong_single_cluster_signal`、`geo_theme_candidate_service.py`）。

**Theme Candidateに優先順位スコアは一切無い**（importance/relevance/novelty/momentum/confidence、すべて列として存在しない）。`candidate_type`/`change_type`/`business_relevance`はいずれも分類ラベルであり、ランキングではないと設計コメントに明記されている。`signal_count`は証拠件数のカウンタのみ。

Expert AI Handoffは`geo_theme_candidate_handoff_service.py`が担い、冪等性・Atomicityとも実装済み。Expert AI Core自体はimportされておらず、責務分離（Theme Discovery=「何を見るべきか」、Expert AI=「どう読むべきか」）が設計として維持されている。

**Freeze済みV1の変更禁止コア**（`docs/theme-discovery-v1-freeze-manifest.md` §9）: fingerprint設計、Human refinement protection、Handoff mapping/idempotencyなど。特にfingerprintは`change_type`を意図的に除外（LLM分類の非決定性対策）——スコアリング層を追加する場合、既存列への追記ではなく別レイヤー（新列/別テーブル）として設計するのが安全。

---

## 8. Current Differentiation from Generic LLM

| 観点 | 評価 |
|---|---|
| Historical accumulation | **強み**（実装済み、日次データをDB保持） |
| Organization-specific sources | **弱み**（enum上はrss/media_cloud等を許容するが実装はGDELTのみ） |
| Change detection | **部分的な強み**（trajectory算出はあるが、Theme Candidate層に数値伝播しない） |
| Cross-source signal | **強み**（Situation Linkで複数Episode横断接続、ただしHuman Accept前提） |
| Source provenance | **強み**（Theme Candidate→Cluster→Signal/Eventまで実際に遡及確認済み） |
| Persistent monitoring | **構造上は強み**（再sync時にevidence自動更新）だが自動定期実行なし |
| Company-specific relevance | **弱み**（固定8タグのみ、部署文脈の重み付けなし） |

---

## 9. Gap Analysis

| Capability | Current | Target | Gap | Reuse | Priority |
|---|---|---|---|---|---|
| Source Registry | 108件、構造化済みだが登録者不明・103媒体JSON未統合 | 担当者/部署トレース可能、全Source統合 | 中 | REUSE WITH ADAPTER | 中 |
| Personal Source Sharing | 無し | 担当者が登録→部署共有 | 大 | GAP | 中 |
| Daily Crawl | コードはあるが26日停止、常駐Scheduler無し | 安定稼働 | **大・最優先** | REUSE（再稼働） | **最高** |
| Crawl Health可視化 | Ingestion Run履歴はあるがUI無し | Source DB画面でActive/Last fetched/Error表示 | 中 | REUSE WITH ADAPTER | 高 |
| Historical Accumulation | 実装済み | 維持 | 小 | REUSE AS-IS | — |
| Theme Candidate生成 | 実装・Freeze済み | 維持・拡張 | 小 | REUSE AS-IS | — |
| Priority/Scoring | **存在しない** | Importance/Novelty/Attention Gap等 | **大** | GAP | **最高** |
| Niche Detection | 単独Clusterは記事3件以上必須＝ニッチ排除的 | 報道量と独立した重要度判定 | **大** | GAP（ゲート改修要） | 高 |
| Natural Language Lens | 無し（既存チャットは個別Candidate相談のみ） | 探索方針を自然言語で指定・保存 | 大 | GAP（UIシェルはREUSE WITH ADAPTER） | 中 |
| Lens Save/History | 無し | 保存・履歴・部署共有 | 大 | GAP | 低（Lens本体の後） |
| Preset | 無し | 定型観点の用意 | 中 | GAP | 低 |
| Automation/Notification | 無し | 将来の自動実行・通知 | 大 | GAP（将来） | 低（今回スコープ外） |
| Expert AI Handoff | 実装済み | 維持 | 小 | REUSE AS-IS | — |

---

## 10. Target Architecture（推奨全体構造）

既存パイプラインの流れ自体は変更せず、**2つの層を追加**する：

```
[既存] Source Registry → Collector → Raw → Dedup → Event/Signal → Episode Cluster → Situation Link → Theme Candidate → Expert AI Handoff
                                                                                              ↑
[追加①] Scoring Layer（新テーブル、既存Theme Candidateへの外部キー参照のみ、fingerprint等は無改修）
         - Importance / Business Relevance / Novelty / Attention Gap / Source Quality / Cross-source Confirmation

[追加②] Exploration Lens Layer（新テーブル群）
         - Lens定義（自然言語instruction→構造化パラメータ）
         - Lens保存・履歴
         - Preset（Lens templateとして）
```

Collector/Scheduler層は「新規実装」ではなく「既存コードの再稼働＋外部スケジューラの実在確認」が先決。

---

## 11. Source DB Target Design

**Data**: `geo_source_registry`に`registered_by`（担当者ID）・`shared_scope`（private/department/company）列を追加。`media_source_registry.json`（103媒体）を同テーブルへ統合インポート（1回限りのread-only→insert作業、crawler新設ではない）。

**UI**: 既存`geo.sources-documents.tsx`はConnector実行コンソールとして維持しつつ、別途「Source一覧・追加・編集」画面を新設（Source名/Type/Region/Language/Owner/Shared/Active/Crawl Frequency/Last Fetched/Health/Article count）。新規URL登録は即crawler対象にせず、**REGISTERED→VALIDATED→ACTIVE**のGateを設ける（任意URLの即時クロールは安全性・技術的検証の観点で避けるべき、というユーザー自身の懸念と一致）。

---

## 12. Theme Discovery Target Design（Scoring / Niche Detection）

既存の`_is_strong_single_cluster_signal`ゲート（member_count>=3 or トレンド判定）は**ニッチ検出を阻害する直接的なボトルネック**。改修案：

- Importance（地政学的インパクト、Expert AI的な軽量判定 or ルールベース）
- Business Relevance（既存`business_relevance`タグを維持、数値重み付けを追加）
- Novelty（過去Theme Candidateとの差分）
- **Attention Gap**（Source Tier/専門媒体限定カバレッジ vs mainstream報道量の比） — 「ニッチ」を記事数の少なさだけで定義しない。専門媒体3誌で動き始めているが主要メディア0件、のようなパターンを明示的に拾う
- Cross-source Confirmation（独立Source数）
- Early Signal（直近加速度）

これらは**新しいLLM Prompt変更ではなく**、既存のEpisode Cluster/Situation Linkが持つ実データ（member_count、trajectory、evidence source多様性）から導出可能な部分が多い——まず機械的スコアとして設計し、LLM判定は最小限にとどめるのが安全（Freeze済みロジックへの影響を避けるため）。

---

## 13. Natural Language Lens Design

既存`GeoIosConsultationThread`（`geo-ios-consultation-view.tsx`）のUIシェル（turn pending/completed/failedポーリング、バブルUI）がそのまま流用可能。ただし応答形式が異なる——既存チャットは「1件のCandidateへの修正提案」を返すのに対し、Lensは「複数の新規Candidate候補」または「既存一覧へのフィルタ条件」を返す必要があり、**新しいmode＋レスポンス型の追加**が必要。

自然言語instructionを以下へ構造化：region / horizon / actor / topic / business relevance / novelty preference / mainstream attention ceiling / source preference / time window。

---

## 14. Saved Lens / Preset / History 設計案

新規テーブル（例）: `geo_theme_exploration_lenses`（lens_name, natural_language_instruction, structured_params jsonb, created_by, shared_scope, last_run_at）、`geo_theme_exploration_runs`（lens_id, run_at, candidate_ids_found）。Presetは、このLensテーブルへの`is_preset=true`の初期データ投入として実現可能（専用の別機構は不要）。

---

## 15. Future Automation / Notification（今回は設計のみ）

Saved Lensに`schedule`/`last_run`/`next_run`/`notification_threshold`（new only / meaningful change only）列を持たせておけば、将来、既存Scheduler整備（Phase 11参照）と組み合わせて自動実行→通知へ発展可能。今回は列の受け皿設計のみ。

---

## 16. UI Proposal

- **Home**: 既存`geo.theme-discovery.tsx`のCandidateカード形式は**REUSE AS-IS**（Title/Actor/Region/根拠件数/最終更新は既に表示済み）。Scoring追加後、カードに優先度バッジを追加。
- **Exploration Box**: 一覧上部に自然言語入力＋Preset。既存チャットUIシェルを流用。
- **Saved Lens/History**: 新規セクション。
- **Theme Detail**: 既存`CandidateDetail`（Evidence/Coverage/Handoffボタン）は充実しておりREUSE AS-IS。

---

## 17. Source DB UI設計（詳細）

Phase 11参照。最低限の表示項目と新規Source追加フォームのフィールド案は本文9章の通り。

---

## 18. Reuse vs New（総括）

**REUSE AS-IS**: Theme Discovery一覧UI、Candidate Detail、Evidence/Coverage表示、Expert AI Handoff、Dedup/Normalizationロジック、Historical Accumulation基盤。

**REUSE WITH ADAPTER**: `geo_official_crawler.py`（台湾監視）、`geo.news-signals.tsx`（収集状況モニタ部品として）、`GeoIosConsultationThread`（Lens UIシェルとして）、Wall-bounce Chat（新規Candidate提案への拡張）。

**GAP（新規）**: Scoring Layer、Niche/Attention Gap判定、Natural Language Lens構造化、Lens保存/履歴/Preset、Source DB管理UI（登録者/共有範囲）、Crawl Health可視化、常駐Scheduler（または外部スケジューラの実在確認・復旧）。

---

## 19. Implementation Phases（提案）

- **Phase A（最優先・最小）**: GDELT収集停止の原因究明＋再稼働確認。外部スケジューラの実在有無を確認し、無ければ最小限の定期実行手段を用意。
- **Phase B**: Source DB UI整理（登録者/共有範囲列の追加、103媒体JSON統合、Crawl Health表示）。
- **Phase C**: Theme Scoring改善（Importance/Novelty/Attention Gap、既存データからの機械的算出を優先）。
- **Phase D**: Natural Language Lens（構造化パラメータ化、既存チャットUIシェルの拡張）。
- **Phase E**: Saved Lens/History/Preset。
- **Phase F**: Automation/Notification（将来）。

Audit結果を踏まえ、**Phase A（データが動いていなければ何を作っても無意味）を実質的な前提条件**として最優先に置くことを推奨する。

---

## 20. Risks

- GDELT収集停止の原因が技術的障害なのか、単に誰も実行していないだけなのか切り分けが必要（切り分けずにScoring層だけ作っても空のデータの上に機能を積むことになる）。
- `_is_strong_single_cluster_signal`ゲートの緩和は、Freeze済みTheme Candidate生成ロジックの「性質」を変えるため、Freeze Manifestの変更範囲・再Freeze手続きが必要になる可能性がある。
- 新規URL登録を即クロール対象にする設計は、対象サイトのrobots.txt/利用規約・社内情報セキュリティポリシーとの整合確認が必要（Gate設計で緩和はするが、ゼロにはならない）。
- 103媒体JSONのDB統合は1回限りの作業だが、重複（既存108件との名寄せ）の設計が必要。

---

## Recommendation

次に着手すべきは **Phase A**: GDELT収集パイプラインが26日間停止している根本原因の確認（外部スケジューラが実在するか、最後に人間が実行したのはいつか）。これが解決しない限り、Scoring層やLensを作っても「1ヶ月前で止まったデータ」の上で動くことになり、「まだ報道されていないテーマの発見」という目的に対して価値を出せない。

---

## 最終的に答えてほしい問い（回答）

**1. 現在、本当に毎日何を収集しているのか？**
→ **何も自動収集されていない**。常駐Schedulerは意図的に未導入（文書化済み設計判断）。直近7日間のIngestion Runは0件。GDELT由来データの最新作成日は2026-09-08（26日前）。

**2. 登録されているSourceと、実際にCrawlerが動いているSourceは一致しているか？**
→ **一致していない**。108件中105件が一度も自動取得に成功していない。`automation_allowed=true`はわずか3件（World Bank/OFAC/IMF）で、かつその3件も最終成功は45〜60日前。

**3. Sustainabilityや台湾・中国モニタリング由来のSource DB/Crawlerは、どこまで再利用可能か？**
→ Sustainability由来はDBトリガー関数1つのみで実質的な再利用資産は無い。台湾監視由来の`geo_official_crawler.py`（6公式サイト+TSMC IR）はロジックとして再利用可能（REUSE WITH ADAPTER）。最も価値が高いのは`media_source_registry.json`（103媒体、詳細メタデータ保有）だが、現状DB化されておらず未統合。

**4. 今のTheme Discoveryは一般的なChatGPT/Perplexityと比べて、何が既に強いのか？**
→ Historical accumulation（日次データの実蓄積）、Source provenance（Theme Candidateから元Evidenceへの遡及確認済み）、Cross-source signal（複数Episode横断の接続検出）の3点は実装として既に強い。一方、Organization-specific sources・Company-specific relevanceは弱い。

**5. 「重要だがニッチ・まだ報道されていないTheme」を発見するには、現在何が足りないのか？**
→ 2点。(a) スコアリング層が全く存在しない（Importance/Novelty/Attention Gap等、ゼロから設計が必要）。(b) 現行の単独Cluster候補化ゲート（記事3件以上必須）がニッチ検出と直接矛盾しており、設計変更（Freeze Manifestの範囲確認込み）が必要。

**6. Natural Languageで「探し方」を指定し、保存・再利用するには何が必要か？**
→ UIシェル（`GeoIosConsultationThread`パターン）は流用可能だが、(a) 自然言語instructionを構造化パラメータへ変換する新ロジック、(b) 複数Candidate提案を返す新しい応答形式、(c) Lens保存用の新規テーブル、の3点が新規に必要。

**7. 最小の追加開発で、マネージャーに価値を実感してもらえるTheme Discovery V2はどこまで作れるか？**
→ Phase A（収集再稼働）さえ解決すれば、既存のTheme Discovery一覧・Candidate Detail・Expert AI HandoffはそのままREUSE AS-ISで「動くデモ」になる。そこに最小のScoring（Attention Gap＝専門媒体カバレッジ vs mainstream報道量の比、のような機械的指標1〜2個）を追加するだけで、「ニッチだが重要」という価値提案を具体的に見せられる。Natural Language Lensは次段階でよい。
