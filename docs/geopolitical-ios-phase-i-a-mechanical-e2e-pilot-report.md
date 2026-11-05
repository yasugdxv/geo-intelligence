# Geopolitical Intelligence OS — Phase I-A: Mechanical E2E Pilot 完了報告

日付: 2026-09-16
対象: Phase B〜Hで構築したIOS（Theme → Scenario → Design → Report → Review →
Approval → Publish → Learning Candidate）の実環境E2E検証。

## 0. スコープの大前提（ユーザー指示）

このPilotは、専門家AIの回答内容そのものの妥当性を判定するものではない。
Claudeは実務専門家ではないため、Reviewer/Approver/Knowledge Steward操作は
**運用上の代行（operational proxy）としてのみ**実施した。したがって結果は
明確に3層に分ける。

| レイヤー | 状態 |
|---|---|
| Workflow / E2E（配線・状態遷移・冪等性） | **検証済み** |
| Data integrity / Provenance | **検証済み** |
| Review / Approval / Learning Candidate の仕組み（メカニズム） | **検証済み** |
| Expert AI / CQAS の**内容**品質（generation/ranking/selection miss等） | **未検証（expert_unverified）** |

内容品質評価はPhase I-B（実専門家レビューまたは既存Expert Review corpusを
使った retrospective validation）で別途行う。今回の結果をCQASチューニングや
Expert AIプロンプト変更の根拠に使ってはならない。

## 1. 実施したPilot Case（3件、Controlled Replayではなく実DB・実LLM呼び出し）

| Case | テーマ | human_visibility | agent_sharing_policy | 特徴 |
|---|---|---|---|---|
| A | 米国の対中半導体・AI輸出規制 | company | geopolitics_only | Actor-heavy |
| B | EUDR サプライチェーン・地政学的影響 | department | allowed_cross_domain | Cross-domain |
| C | イラン・ホルムズ海峡情勢 | private | none | Fast-moving/critical |

3件とも以下を実際に完走した（`analysis_session → scenarios(CQAS) →
design v1 → design v2(refine) → report_project → review round1(要修正) →
review round2(承認) → approval → publish(active) → extract-learning`）。

補助Pilot:
- **Intelligence Need Pilot**: 未知テーマ（モルディブ深海採掘）で知識ギャップを
  意図的に発生させ、捏造なし・gap記録・同一問い合わせのdedup（`merged_into_existing:
  true`）を実データで確認。
- **Cross-Agent / Visibility Pilot**: 実publish済みの3件に対し
  `can_human_access`/`can_agent_access`を直接実行し、3種類の可視性設定通りに
  ゲートされることを確認。

## 2. 発見事項の分類（P0/P1/P2/Backlog、その場修正はしない方針）

### P0（今回は0件）
即時修正が必要な項目はなかった。

### インフラ/運用ギャップ（発見時点でP0扱い、ユーザー対応により解消済み）
- **Phase Eマイグレーション未適用**: `2026-09-16b_geo_ios_knowledge_publication.sql`
  が3回の「実行した」報告にもかかわらずDBに反映されておらず、publishが
  `42703 undefined_column`で失敗。最終的に再実行で解消（原因は特定できず、
  ユーザー側の実行環境の問題と推定）。コード側の欠陥ではない。

### P1（Governance Skillsで新規発見、要対応・release-gate BLOCK要因）
1. **SEC-1**: `POST /api/geo/ios/approval/in-app` / `/approval/upload` が
   `approver`を完全自由記述で受け付け、role check が一切ない。Learning
   Candidate承認やKnowledge publishには存在する`role`パラメータ検証が、
   最も重要な関門である承認ステップにだけ無い、という内部不整合。
2. **SEC-2**: 既存の`geo_role_guard.require_role`自体が自己申告・未認証
   （既知の仕様、今回のPilotで初めて本番相当パイプライン全体を通して
   露呈した）。
3. **SEC-3**: `geo_intelligence.api_keys`が本環境で未設定のため、Phase Fの
   認証済みCallerIdentity経路（なりすまし対策の本体）は今回のPilotでは
   実際には運動しておらず、既存ユニットテスト（16件）のみでカバーされている。

### P2（Backlog、優先度中）
- **REL-1**: `extract-learning`の初回LLM呼び出しが2/3ケースで一過性の500
  エラーを出し、即時リトライで成功（冪等性キーにより安全に再試行可能）。
  outbound Azure OpenAI呼び出しへのリトライ処理が無い。
- **REL-2**: 長時間LLM呼び出し中にサーバープロセスが外部要因で再起動され、
  接続がリセットされる事象を観測（DBは無事、データ損失なし）。クライアント側
  タイムアウト/再開設計の運用ノート。
- **TOK-2**: `build_learning_source_package`が使わない`scenario_candidates`
  全件・design全履歴をDBから取得している（LLMには送っていないため、コスト
  影響はゼロ、DB往復のみの軽微な無駄）。

### Backlog（構造的、対応時期未定）
- **リポジトリ全体が一度もコミットされていない**: `git diff <base>`による
  厳密なscope検証ができない構造的問題（今回はmtime+diff内容の手動照合で
  代替検証）。今回のIOS作業自体はスコープ違反なしと確認できたが、今後の
  governance auditのために定期的なコミット運用が望ましい。
- **`human_visibility="department"/"geopolitics_team"`が実質「認証済み人間
  なら誰でも」**という簡略化（コード内に既に明記済みの既知の制約、今回実データ
  で再確認）。

## 3. Governance Skills 実行結果（`world_monitor_dashboard`から本プロジェクトへ移植、v0.2）

移植先: `.claude/skills/agent-{development-integrity,security-review,evaluator,
token-optimizer,release-gate}/`。実行順序: development-integrity →
security-review → evaluator → token-optimizer → release-gate。
証跡: `.agent-governance/{integrity,security,eval,token,release-gate}-report.json`。

| Skill | 結果概要 |
|---|---|
| development-integrity | scope_status=partial。真のbase revisionが存在しない構造的問題を発見。ただしIOS作業自体のscope violationは無しと確認（diff内容にIOS関連語ゼロ、Report Studio側ファイルは自チームの別作業と確認）。 |
| security-review | P1×3件（上記SEC-1/2/3）、P2×2件（reliability findings）。P0は0件。 |
| evaluator | 6 eval cases、task_success=1.0、safety=1.0、**authorization_integrity=0.3**（SEC-1起因）、near_miss_rate=0.5。grounding/final_response_qualityは意図的に未採点（expert_unverified方針）。 |
| token-optimizer | 実測latency記録（generate_scenarios 87.4s、refine_design 86.6-95.5s、extract_learning 12.7-42.2s）。軽微なDB overfetch1件、waste的な問題は無し。 |
| release-gate | **Code/Merge Gate: BLOCK**（default policy: 統合性ガード失敗 + P1×3 + near_miss_rate超過 + grounding指標欠落）。 |

### release-gateのBLOCKの読み方（機械的判定 vs 実質的な解釈）

release-gateのdefault policyは汎用的なもので、今回意図的に採点しなかった
`grounding`指標の欠落もBLOCK要因に含めている。これは**設計上の欠陥ではなく
Phase I-A/I-Bの意図的な分離**によるものであり、実質的なBLOCK要因は次の3つ:

1. SEC-1（承認ゲートのroleチェック欠落）— 実際のコード修正が必要
2. development-integrity の base revision guard 失敗 — リポジトリ運用の構造的課題
3. near_miss_rate 0.5 — 上記SEC-1が3/3ケースで再現したことの反映（1つの根本原因）

## 4. Release Readiness 判断

| Gate | 状態 |
|---|---|
| Code/Merge Gate（機械的） | BLOCK（上記3要因） |
| Production Readiness Gate | NOT READY（`geo_intelligence.api_keys`未設定、SEC-1未対応） |

**総合判定: Internal Pilot Ready（社内限定・Claude代行運用の範囲でのみ）。
Controlled Production Pilot Ready ではない。**

Controlled Production Pilotへ進むための必須条件（P1の解消）:
1. SEC-1: 承認エンドポイントに`approver_role`検証を追加
2. SEC-3: `geo_intelligence.api_keys`を実際の外部呼び出し元（Sustainability/
   ERM等）向けに設定し、認証済みCallerIdentity経路を実際に1回通す
3. （中期）SEC-2: `geo_role_guard`を本物の認証基盤へ置き換える計画

## 5. 次のステップ

- 上記P1×3件は本報告確定後、ユーザーの指示に基づき着手する（Pilot中は
  その場修正しない方針を貫いた）。
- Phase I-B（Expert Validation Pilot）は別タスクとして、実専門家レビュー
  データが用意された時点で開始する。
- 今回の3ケースのevaluation_record（A/B/C）と`.agent-governance/*.json`
  一式を、Phase I-Bの入力および将来のrelease-gate再実行の基礎として保持する。
