# データモデル設計（Phase 0時点、SQL未作成）

指示書5章のエンティティ一覧を、既存`sql/`ディレクトリの命名規約（`YYYY-MM-DD_説明.sql`、素のSQL・Alembic不使用）を踏襲した新規マイグレーションファイル群として整理する。
**このドキュメントは設計のみであり、Phase 0では実SQLファイルを作成しない。** Phase 1着手時に、縦切り対象（テーマseed／文書アップロード／Event抽出／Evidence／MIL-01のObservation／audit log）に必要な最小テーブルから順にSQLファイル化する。

命名規約: `sql/YYYY-MM-DD_geo_<領域>.sql`（例: `sql/2026-08-XX_geo_core.sql`）。既存プロジェクトの`sql/2026-08-02_goal_categories.sql`と同じ「1トピック1ファイル、ユーザーがSupabase上で手動実行」運用を継続する。

---

## 5.1 モニタリング基盤

来歴（指示書6章のリネージをそのまま踏襲）:

```text
Source → Fetch Run → Document/Document Version → Event/Event Version → Evidence → Observation → Assessment → Scenario Assessment → Alert → Approval/Action
```

| テーブル | 役割 | 主な関係 |
|---|---|---|
| `geo_themes` | 地政学テーマの設定データ（台湾有事、将来の米中関係・中東・関税制裁・南シナ海等）。台湾有事を専用ロジックとしてハードコードせず、本テーブルの1レコードとして表現する | 全エンティティのルート |
| `geo_source_registry` | 監視対象ソース（公式サイト・報道・SNS等）の登録。source tier（一次/二次/三次/四次）を持つ | `geo_themes`に紐づく |
| `geo_source_fetch_runs` | 取得実行のログ（成功/失敗、HTTP status含む） | `geo_source_registry`に紐づく |
| `geo_documents` | 取得した文書。URL/canonical URL/公開日時/取得日時/言語/content hash/MIME type/原本保存先/parser versionを保持 | `geo_source_fetch_runs`に紐づく |
| `geo_document_versions` | 同一URLの版差分 | `geo_documents`に紐づく |
| `geo_events` | Event Candidate。actor/action/target/location/start_at/end_at/quantities/event_type/confidence/unknown_fieldsを保持。`verified`フラグを持つ | `geo_documents`（Evidence経由）に紐づく |
| `geo_event_versions` | 複数文書が同一事象を示す場合の統合履歴（Event乱立を防ぐ） | `geo_events`に紐づく |
| `geo_evidence` | 主張単位の証拠。claim text/quote/document_id/page-section/primary_flag/source tier/corroboration count/confidence/verification status/contradictionsを保持 | `geo_events`・`geo_documents`に紐づく |
| `geo_indicator_definitions` | 指標定義（MIL-01等22〜23指標、`mvp_enabled`フラグ） | `geo_themes`に紐づく |
| `geo_indicator_rule_versions` | 指標の計算ルールのversion管理 | `geo_indicator_definitions`に紐づく |
| `geo_observations` | 指標の実測値。raw value/aggregation window/baseline/deviation/persistence/novelty/data quality/source freshness/confidence、使用した`indicator_rule_version_id`を保持。値不足時は`insufficient_data`を返す（0として扱わない） | `geo_indicator_rule_versions`・`geo_evidence`に紐づく |
| `geo_assessments` | Observationを踏まえた評価 | `geo_observations`に紐づく |
| `geo_scenarios` | シナリオ定義（グレーゾーン圧力／検疫・法執行型封鎖／軍事封鎖／限定攻撃／侵攻準備・侵攻／経済・サイバー強圧、の6種を台湾テーマのseedとして投入） | `geo_themes`に紐づく |
| `geo_scenario_conditions` | required/supporting/missing/contradicting/mitigating conditions | `geo_scenarios`に紐づく |
| `geo_scenario_assessments` | シナリオごとのAI評価と人の承認状態 | `geo_scenarios`・`geo_assessments`に紐づく |
| `geo_alerts` | アラート。level/title/what changed/why it matters/affected scenario/business relevance/evidence links/confidence/counterevidence/recommended next checks/proposed action/approval status/recipients/sent_at/correction・retraction historyを保持 | `geo_scenario_assessments`に紐づく |
| `geo_alert_approvals` | Level 2以上の承認履歴（改変不可の形で保存） | `geo_alerts`に紐づく |
| `geo_actions` | アラートに紐づく対応アクション・担当者・期限・status | `geo_alerts`に紐づく |
| `geo_audit_logs` | 誰がどの資料を参照・操作したかの監査ログ | 全エンティティ横断 |

## 5.2 専門家AI・内部レポート

来歴:

```text
Expert Request → Source Pack → Prompt Version + Knowledge Versions + Model → Structured Expert Response → Quality Evaluation → Human Edit/Expert Feedback
```

```text
Internal Report → Report Version → Report Claims → External Evidence/Internal Report/Event → Review Status → Validity/Supersedes
```

| テーブル | 役割 |
|---|---|
| `geo_knowledge_items` / `geo_knowledge_versions` | 専門家AIのKnowledge資産（System Policy/Thinking OS/Stakeholder Force Model/Hypothesis & Evidence Protocol/Review OS/Output Templates/Case Library/Business Impact Model/Analyst Development）。ファイル名・version・effective date・approved_by・statusを保持 |
| `geo_internal_reports` / `geo_internal_report_versions` | 内部レポート本体・版。title/department/owner/created_at/as_of/topic/region/time horizon/report type/status/confidentiality/allowed departments/valid_until等 |
| `geo_report_claims` | レポートを分解したFact/Official Claim/Observation/Assessment/Assumption/Scenario/Impact/Recommendation/Uncertainty。Factは外部Evidenceへリンクできない限り「社内主張」であり外部確認済み事実へ自動昇格しない |
| `geo_expert_requests` / `geo_expert_responses` | 専門家AIへのリクエストと構造化レスポンス（7モード、15段階Reasoning Pipelineの入出力） |
| `geo_source_packs` | Expert Requestに紐づく根拠資料の束（イベント・レポート・文書） |
| `geo_quality_evaluations` | Quality Guardの評価結果（指示書14.6の9項目） |
| `geo_expert_feedback` | 人による編集・コメント・承認・12項目AI評価（指示書20.4） |
| `geo_prompt_versions` | Promptのversion管理（コード中に散在させない） |

## 5.3 将来拡張用（Phase 0ではテーブルのみ設計、機能実装しない）

`geo_stakeholders` / `geo_relationships` / `geo_hypotheses` / `geo_company_exposures` / `geo_impact_assessments` / `geo_agent_registry` / `geo_intelligence_objects`

これらはPhase1〜4では作成せず、必要になった時点（Phase3以降のcompany_exposures連携等）でスキーマを確定しマイグレーションを追加する。

---

## Phase 1縦切りで最初にSQL化するテーブル（想定、次ラウンドで確定）

`geo_themes` / `geo_source_registry` / `geo_documents` / `geo_document_versions` / `geo_events` / `geo_event_versions` / `geo_evidence` / `geo_indicator_definitions` / `geo_indicator_rule_versions` / `geo_observations` / `geo_audit_logs`

Executive Overviewへの最小表示に必要な範囲に絞り、Assessment/Scenario/Alert系はPhase2以降に回す。
