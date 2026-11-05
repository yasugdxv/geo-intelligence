# Phase 3-1 改善バックログ（Phase 3-0 Pilotで観測されたGap）

`docs/geopolitical-phase3-pilot-results.md`のPilot実施結果から抽出。category/problem/
evidence_case/impact/recommended_action/priorityで整理し、**複数Caseで共通して観測された
もの、またはimpactが大きいもの**をTop2-3として優先する（Plan11章）。

| category | problem | evidence_case | impact | recommended_action | priority | status |
|---|---|---|---|---|---|---|
| Retrieval配線 | `build_foundation_pack`（Entity/Relationship/Asset/Dependency/既存Leverage Card）がExpert Engine呼び出し経路（`run_expert_request`）に一切接続されていなかった | Case1 | 高（Stakeholder粒度・Decisive Power分析・Asset/Leverage識別に実測可能な影響） | ✅対応済み: `geo_expert_engine._format_structural_context`で最小限接続。Baseline比較で改善を確認（`geopolitical-phase3-pilot-results.md`参照） | **Top1** | **解消済み(2026-08-21)** |
| Provenance紐付け | 実質支配評価等のExternal Assessment Claimが、作成時に`geo_claim_issues`への紐付けを忘れると、Case Contextから静かに欠落する（エラーにならない） | Case1 | 中（今回は事前の監査で発見・是正できたが、気づかれなければ静かなGapのまま） | candidate/Claim作成時に「Issue紐付け漏れ」を検知するチェックの追加を検討（`geo_candidate_extraction_service`等） | Top2 | 個別事象は解消済み(2026-08-21)、恒久チェックは未実装 |
| Retrieval Scope | `build_source_pack`がCase→Issueのスコープを無視し、Theme全体の直近document titleを無差別に返す。Case1実行でCase2/3関連文書タイトルの混入と、タイトルのみからのAIによる内容類推（本文未開示のまま観測claim化）を実際に確認した | Case1（Baseline実行で観測） | 中〜高（Case分離設計の効果をsource_pack側で無効化している） | `build_source_pack`にissue_ids引数を追加し、Case1の対象Issueに紐づくDocumentのみへスコープを絞る改修を検討 | Top3 | 保留（Phase3-1判断材料） |
| Quality Guard | `stakeholders_grounded_in_source_pack`チェックが、AIがstakeholder名を説明的表記（「イラン政権（最高指導者陣営を含む）」等）にするたびに誤検知する。文字列完全一致ではなくEntity名の部分一致・正規化比較にすべき | Case1/2/3（3件全て） | 高（Quality Guardの実効的な監視能力を損なう。実際の無根拠stakeholder登場を見逃すリスクにもつながる） | チェックロジックをEntity名の部分一致・エイリアス比較に変更、またはgeo_entitiesのcanonical_name/name_enとの照合に切替 | Top4 | 未対応 |
| LLM出力品質 | `fact_claims_have_source_ref`不合格: 一般的背景知識（米海軍のプレゼンス等）を無出典のobservation/factとして述べる | Case2（Case1のBaselineでも発生、Integration Fix後は解消） | 中（内容自体は妥当だが、Provenance原則に反する） | プロンプト指示に「Source Packに無い一般的背景知識はfact/observationではなくhypothesis/inferenceとして明示する」旨を追加 | Top5 | 未対応（Case1では構造データ追加により副次的に改善したが恒久対策ではない） |
| Knowledge Item整備 | Seed投入時点のKnowledge Item candidate 3件が、当初出典未紐付けの記述（IRGC実質支配評価、UAEパイプライン迂回能力等）を含んでいた。人間レビュー前の自己点検ステップが無かった | country_foundation/leverage_card | 中（今回は人間の詳細レビューで発覚・是正できたが、レビューが無ければ無根拠記述がactive化するリスクがあった） | candidate作成時に「全フィールドがgeo_claimsへ紐付いているか」の自動チェックを`geo_candidate_extraction_service`等に追加することを検討 | Top6 | 未対応 |
| Reuse Test | Case横断のReasoning再利用が本Pilotでは検証不能（Expert Review Loop未実施のため） | Case1-3共通 | 低（Pilotのスコープの問題であり、基盤側の欠陥ではない） | Phase 3-0のExpert Review Loop実施後に別途評価する。今回は「未検証」として記録するに留め、Phase 3-1の判断材料には含めない | 保留 | 未実施 |
| executive_summary | Case3のAI Draftで`executive_summary`の3フィールドが全てnullだった（他の2件は埋まっている） | Case3 | 低〜中（case_analysisモードでは必須運用ではないが、原因不明のばらつき） | 複数回実行して再現性を確認し、プロンプト側の指示不足かモデルの偶発的な出力揺れかを切り分ける | 要追加観測 | 未対応 |

## Pilotで確認できた「機能していた」点（次フェーズでも維持すべき設計）

- External Reference Model: Observed Fact/Actor Claim/External Assessmentの分離がCase
  Contextに正しく反映され、AI Draftのclaim_typeとも整合していた
- Phase3-0で追加したstakeholder拡張項目（position/leverage/ability_to_move_or_block/
  decisive_power_and_leverage/contradicting_evidence）は3Case全てで具体的に埋まっており、
  MODE_GUIDANCEへの指示追加が有効に機能した
- Case-specific Issueを3つ独立させた設計により、各CaseのSource Packが人為的に混ざらず、
  それぞれの分析範囲に即した内容になっていた

## pgvector/Embedding等の要否判断

Plan11章の基準（Retrieval Gapが複数件観測された場合のみ次フェーズ候補）に照らすと、
本Pilotでは**FTSによる取得失敗（Retrieval Gap）は観測されていない**（Case Context構築・
Source Pack構築とも正常に機能）。したがって現時点でpgvector/Embeddingの追加実装は
見送りを継続する。
