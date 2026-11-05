# 地政学ナレッジ＆インテリジェンスDB — External Reference Model（Phase 2B-2）

対象読者: 外部情報（Think Tank分析・政府発表・報道分析・OSINT等）の扱いを理解したい開発者・Reviewer。
承認済みPlan: `C:\Users\269811\.claude\plans\pure-drifting-cosmos.md`

## 1. 基本方針

地政学の外部情報は「正しいと確定できる」性質のものではない。Think Tank・政府・報道等の分析を、人間が1件ずつApprove/Rejectして「正しいKnowledge」に昇格させるフローは採用しない。代わりに、「誰が・いつ・どのような立場から・何を事実として述べたか／何を評価・推定しているか」を保持し、そのままExpert AIが参照できるようにする。

**重要**: retrievable（AIが参照可能）であることは、true/verified/correctを意味しない。

## 2. Retrieval Status × Epistemic Type（二軸モデル）

### 2.1 Epistemic Type（＝既存`information_class`列、Phase1から存在）

新軸ではなく、既存の`geo_knowledge_items`/`geo_claims`共通列`information_class`をそのまま使う。Phase2B-2で1値追加した。

| 値 | 意味 | 表示名 |
|---|---|---|
| `observed_fact` | 公式統計、選挙結果、制裁発動、条約締結等 | 観測・記録された事実情報 |
| `actor_claim` | 政府・政党・軍・企業の発言（事実性は保証しない） | Actor Claim |
| `external_assessment` | 研究機関・専門家・報道機関の解釈 | External Assessment |
| `internal_hypothesis` | 専門家AI・担当者が構築した仮説（承認済みの基礎事実として扱わない） | Internal Hypothesis |
| `internal_expert_assessment`（Phase2B-2追加） | 社内専門家レビューから抽出・Promoteされた分析知 | Internal Expert Assessment |

`actor_claim`は「Official Claim」と同義。DB定義の値をそのまま表示名にも使う（リネームはしない）。

### 2.2 Retrieval Status（Phase2B-2で新設、`geo_knowledge_items`・`geo_claims`共通）

`available` / `restricted` / `deprecated` / `archived`。「AIが参照してよいか」だけを表す軸で、内容の正誤判定ではない。

**`geo_knowledge_items`は`status`（lifecycle: candidate/in_review/active/contested/rejected/superseded/deprecated/archived）と完全に独立した二軸として運用する**。一軸へ統合しない。

- `status`の初回遷移（`candidate`/`in_review`から抜ける瞬間）のみ、`retrieval_status`を下表で自動初期化する（`geo_knowledge_service.RETRIEVAL_STATUS_INITIAL_MAP`）
- それ以降の`status`遷移（例: `active→deprecated`）は`retrieval_status`に一切触れない
- 個別に`retrieval_status`を変更したい場合は`geo_knowledge_service.set_retrieval_status()`を明示的に呼ぶ（Human Approvalではなく運用操作。`geo_knowledge_reviews`は使わず`geo_audit.log_audit`にのみ記録する）

| 初回遷移時の`status` | 初期`retrieval_status` |
|---|---|
| active | available |
| contested | available |
| rejected | archived |
| superseded / deprecated | deprecated |
| archived | archived |

`geo_claims`は元々`status`列が無い（Phase1から人間承認なしで使われる設計）ため、`retrieval_status`のみのシンプルな軸。作成時の決定ロジックは3章参照。個別変更は`geo_claim_service.set_retrieval_status()`。

### 2.3 検索・Retrievalは常に両方をAND条件で使う

`status`と`retrieval_status`は独立して両方フィルタする。一軸へ統合しない。

- 通常のReusable Knowledge: `status='active' AND retrieval_status='available'`（`geo_retrieval_service.get_foundation_knowledge`/`get_reusable_knowledge`）
- Contested Knowledge: `status='contested' AND retrieval_status='available'`（`get_contested_knowledge`。**引き続き別関数・別セクション**として取得し、Reusableへ混ぜない）
- Candidate Knowledge（レビューキュー）: `status in (candidate, in_review)`のみ（`retrieval_status`によるフィルタは行わない。レビュー待ち一覧は全件見える必要がある）
- `geo_search_service.search_knowledge_items`/`search_claims`も同様に、既存の`status`引数はそのまま維持し、`retrieval_status`を追加のAND条件として付与するのみ（既存デフォルト挙動は変わらない）

## 3. Source Reference Policy（Claim個別のHuman Reviewを要求しない仕組み）

Claimを自動`available`にするかどうかは、Claim個別のHuman Reviewではなく、**Source側に一度だけ設定する参照方針**（`geo_source_registry.reference_availability`）で決める。既存の`automation_allowed`/`content_storage_allowed`と同じ「Steward/管理者がSource単位で1回設定する」パターン。

### 3.1 Claim作成時の`retrieval_status`決定ロジック（`geo_claim_service.create_claim`）

`origin_type`（Claimがどこから導出されたか: event/observation/document/knowledge_item/case/manual）と`source_id`（情報の出所となる外部Source）は別概念として分離する。**origin_typeだけでInternal/Externalを判定しない**（event/observationも証拠(`geo_evidence`)経由で外部Sourceに辿り着けるため）。

1. `source_id`の解決を試みる（`document`→`geo_documents.source_id`、`event`→`geo_evidence`経由、`observation`→`geo_observations.evidence_id`経由。`knowledge_item`/`manual`/`case`は解決を試みない）
2. `source_id`が解決できた場合: origin_typeに関係なく`geo_source_registry.reference_availability`をそのままコピーする
3. `source_id`が解決できず、`origin_type in (manual, case)`（＝定義上必ずSourceを持たない、明示的にInternal生成と判定できるorigin）の場合のみ: `available`
4. それ以外（event/observation等でSource未解決）: 列のDB default`restricted`のまま（安全側）

### 3.2 Source方針変更時の一括再同期

`geo_connector_base.set_reference_availability(client, source_id=..., reference_availability=..., actor_id=...)`は、Source単位で方針を設定し、既存Claimのretrieval_statusも再同期する。**対象は現在`available`/`restricted`のClaimだけ**（`deprecated`/`archived`は個別の除外理由があるため、Source側の方針転換だけで復活させない）。

### 3.3 個別Claimの上書き

Sourceの方針が`available`でも、特定の1件だけ`restricted`にする、といった個別判断には`geo_claim_service.set_retrieval_status()`を使う。Source単位の方針とは独立に上書きできる。

## 4. 複数見解の共存

同じIssueについて、Source Aの仮説・Source Bの仮説・政府の主張・Internal Expertの仮説が存在しても、1つに自動統合しない。Contestedは「解消対象」ではなく「複数の見方が存在する状態」として保持する（既存の`get_contested_knowledge`の設計をClaimにも敷衍）。

Issueベースで複数の`external_assessment`/`actor_claim`Claimを取得するには`geo_retrieval_service.get_reference_claims_for_issue(client, issue_ids, information_classes=..., retrieval_status="available")`を使う（`geo_claim_issues`経由、`geo_knowledge_item_issues`と同一パターン）。

## 5. Expert Engineでの扱い

`geo_expert_engine.build_case_context`が、Issueに紐づくReference Claimを`information_class`で3セクション（観測・記録された事実情報／Actor Claims／External Assessments）に振り分けて出力する。External Assessmentsの各行にはSource名・perspectiveを併記する。詳細: `docs/geopolitical-expert-engine-integration.md`。

外部機関のAssessmentや政府のClaimは、その主体の見方・主張として扱い、AIが確定事実へ変換しないことをSystem Promptで明示する。

## 6. 既存データへの互換性

- 既存`geo_claims`/`geo_knowledge_items`の列は削除しない。additiveのみ
- backfill: 既存Knowledge Itemは`status`から`retrieval_status`を導出（2.2章の表と同一）。既存Claimは`source_id`を解決できるものはSource Policyへ揃え、できないものは`origin_type`がmanual/caseの場合のみavailable、それ以外はrestricted
- `origin`列（Knowledge Itemの由来）は既存行に対して推定せずNULLのまま（危険な自動推定はしない）
