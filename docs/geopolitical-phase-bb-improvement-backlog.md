# Phase B-B 改善バックログ（Cross-Department Inquiry Receiver、B-B0〜B-B6完了時点）

Phase B-B（Geopolitics Department Intelligence AI: Cross-Department Inquiry Receiver、
UC2・UC8接続）はB-B0（監査）〜B-B6（A/B E2E）まで完了し、**Technical Integration Complete**
と位置づける。ただし今回のE2Eは以下の意味での「Real HTTP Integration E2E」であり、
「Production E2E」ではない点を明記する:

```text
A real application code
 → real HTTP
 → real FastAPI routing
 → real request/response schema
 → real auth
 → real retry/fail-open
 → Fake DB
 → Mock LLM
```

DB（Supabase）とLLM（Azure OpenAI）はFake/Mockのため、設計・契約・分岐ロジック自体は
検証済みだが、実データ・実LLM品質の検証はまだ行っていない。将来実環境が揃った際は、
Real Supabase + Real Azure OpenAI + Real HTTP A→BでのProduction Smokeを1回通せば十分で、
Phase B-Bの設計自体（resolution_mode分岐・Provenance分離・Fail-open）を再検証する必要は
ない（B-B6実施時点でのレビュー結論）。

## B-B6 E2Eで確認できた「機能していた」点（次フェーズでも維持すべき設計）

- existing_only / existing_plus_expert / expert_only の3経路が、Dimension別Coverage判定
  （B-B3）から意図通り分岐した（単なるHTTP 200ではなく、分岐ロジック自体を実HTTPで確認）
- Expert AI呼び出し失敗時、Aは特別なエラーハンドリングなしに通常成功レスポンスとして
  受信できた（Fail-openがB側で完結しており、Aの呼び出し側コードに変更を要求しない）
- Provenanceで既存Knowledge（active_knowledge/live_evidence等）とFresh Expert Analysis
  （実`expert_response_id`付き）が明確に分離されていた
- `expert_requested_dimensions`（依頼した対象、失敗時も保持）と`expert_analyzed_dimensions`
  （成立した場合のみ非空）の区別が、Expert失敗ケースで正しく機能した（捏造なし）

## バックログ

| category | problem | evidence_case | impact | recommended_action | priority | status |
|---|---|---|---|---|---|---|
| Confidence Cap設計 | 現行ルール「missing_dimensions（gis._ALL_CATEGORIES全12種）のうちExpert未解決分が1件でもあればhigh confidence不可」は、今回の質問に無関係なdimension（例: EUDRの政治的背景を聞かれているのにinternal_report/scenario_hypothesisがmissing）でもconfidenceを下げてしまう。Knowledge Coverage（Bが持つ全dimensionの充足度）とAnswer Confidence（今回のClaimsがEvidenceでどこまで支持されるか）を混同している | Case1（B-B6 E2E、EUDR政治的背景。existing_only×knowledge_sufficiency=sufficientでもconfidence="medium"止まり） | 中（安全側エラーであり誤った回答を過大評価してはいないが、正しい回答を恒常的に過小評価する可能性。UC2/UC8運用開始後、high confidenceがほぼ出ない設計になっていないか要観察） | `required_dimensions_for_this_query`（今回の質問に必要なdimensionのみ）を明示的に特定し、`unresolved_required_dimensions`（＝required∩unresolved）のみをConfidence Capの判定材料にする設計へ変更する。`missing_dimensions`（Knowledge Coverage全体）とは分離して保持する。変更前に、実際のUC2/UC8運用で10〜20ケース程度「highが何%出るか」「mediumのうち本来highでよかったものが何%か」「unresolvedだったdimensionは本当に質問に必要だったか」を観測してから着手する | **P1**（今は変更しない。安全側エラーのためP0ではない） | 未対応（設計課題として記録のみ） |

## 次フェーズの評価軸（Cross-domain Quality / Operational Calibration）

Phase B-BまででIntegration Architecture（接続できるか）は確認済み。次に見るべきは
「呼ぶ価値があるか」——A単独 vs A+B Existing Knowledge vs A+B Expert AI で、
Sustainability担当者の回答が実際に良くなるか。具体的な確認観点:

- Bを呼ばなくてよい質問でちゃんと呼ばない
- Bを呼ぶべき質問では不足論点を正しく特定する
- Bから返った地政学情報をそのまま貼らず、AがSustainability文脈へ再解釈できる
- Bが失敗・不足してもAの回答品質を壊さない
- 出典を見れば「Aの知識」「B既存知識」「B Fresh Expert Analysis」の違いが人に分かる

ここまで通って初めて「Department Expert AI Networkとして価値がある」と言える
（Phase B-B完了時点でのレビュー結論）。

### 観点別の着手状況（2026-09-04時点）

実Azure OpenAI/実Supabaseが未設定のため、LLMの実際の判断品質（detect_geo_need/
judge_geo_intelligence_sufficiency/最終Chat回答の再解釈度）はまだ検証できない。
コード監査・修正のみで対応できる範囲は着手済み:

| 観点 | 状態 | 対応内容 |
|---|---|---|
| 出典を見れば「Aの知識」「B既存知識」「B Fresh Expert Analysis」の違いが分かる | ✅対応済み(2026-09-04) | `world_monitor_dashboard/sustainability_chat_geo_service.py`の`build_cross_domain_context_block`（Chat system promptへ実際に載る文言）とProvenance（`_to_provenance`の`resolutionMode`/`reviewStatus`）が、B側`department_intelligence.resolution_mode`/`review_status`を初めて参照するよう修正。以前は`department_intelligence`をパースするだけで一切使っていなかった（B-B6完了時点の未消化状態）。テスト4件追加、既存286件は無変更で全Pass |
| Bが失敗・不足してもAの回答品質を壊さない | ✅B-B6 E2E Case3で実証済み | Expert AI例外時もB→Aは通常成功レスポンス。Aの既存コードは無改修で対応できていた |
| Bを呼ばなくてよい質問でちゃんと呼ばない | ⏸未着手（実LLM待ち） | `detect_geo_need`のLLM判定次第。実Azure OpenAI接続後に下記ケースセットで検証 |
| Bを呼ぶべき質問では不足論点を正しく特定する | ⏸未着手（実LLM待ち） | A側`judge_geo_intelligence_sufficiency`とB側dimension_assessment（B-B3で単体検証済み）の両方が絡む。実LLM接続後に下記ケースセットで検証 |
| Bから返った地政学情報をそのまま貼らず、Sustainability文脈へ再解釈できる | ⏸未着手（実LLM待ち） | Prompt側の指示文（「他部門側の文章をそのまま転記しないでください」）は既に存在することをコード監査で確認済み。実際にモデルが従うかは実LLM待ち |

### 実LLM接続後に流す評価ケースセット（案、15件）

Confidence Cap P1課題（バックログ表参照）の「10〜20ケース観測」と兼用する。各ケースで
記録する項目: `geo_needed`判定の妥当性、`resolution_mode`、`confidence`（"high"の出現率）、
`unresolved_dimensions`の内容が本当に今回の質問に必要だったか、最終Chat回答が地政学情報を
再解釈できているか（転記していないか）。

**カテゴリA: Bを呼ぶ必要が無い（geo_needed=falseを期待、5件）**
1. 「当社の水使用量削減目標の進捗を教えて」
2. 「容器包装のリサイクル率向上策は？」
3. 「Scope3排出量の算定方法を教えて」
4. 「サプライヤー行動規範の改定ポイントは？」
5. 「食品ロス削減の社内目標は？」

**カテゴリB: 既存Geo Knowledgeで十分（resolution_mode=existing_onlyを期待、5件）**
6. 「EUDRの政治的背景を簡潔に教えて」
7. 「イランの水資源政策の統治構造は？」
8. 「メキシコの地方分権と資源規制の関係は？」
9. 「台湾情勢の歴史的経緯を教えて」
10. 「OFAC制裁の基本的な仕組みは？」

**カテゴリC: Expert AIによる新規分析が必要（resolution_mode=existing_plus_expert/expert_only
を期待、5件。"今" "現在" "最新" 等、freshness_requirement="high"を誘発する表現を含む）**
11. 「EUDR延期は今どうなっている？」
12. 「メキシコのWater issueは今後どう政治化・規制化しそうか？」
13. 「イランはホルムズ海峡の通航に現在どう対応しているか？」
14. 「台湾情勢は直近の半導体供給網にどう影響しているか？」
15. 「コンゴ民主共和国の紛争鉱物規制は最近どう動いているか？」
