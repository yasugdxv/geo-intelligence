# Focus Selection / Attention Prioritization — OFF/ON比較（Case1 Trump-Xi）

作成日: 2026-09-06
対象: Focus Selection Layer（key_point_assessment.focus_selection、Phase①のActor Big Flow/
DirectionalPerception/Candidate Generation Operators/actor_expected_response vs
analyst_expected_responseを壊さず追加した最小差分）。

## 実行条件

- Case: `Expert AI Benchmark: Trump-Xi US-China Semiconductor & AI Controls`
  (case_id: `61447d94-e5cc-48f9-9352-31df72af9628`)。原質問をそのまま使用（Evaluation Input
  Gate通過済み、実文書3件directly_relevant）。
- Theme: `05064de8-48f8-484a-8243-f8c837c5a735`。
- profile=`deep`, mode=`case_analysis`, `persist=False`（DB write 0件を確認済みの既存機構）。
- OFF/ONの切替方法: schema定義(`key_point_assessment.focus_selection`)とprompt note
  (`_FOCUS_SELECTION_NOTE`)のみを一時的に無効化してOFFを実行→復元してONを実行
  （git stashではなく直接のファイル一時退避・復元。Tier1の既存差分と混在させないための
  精密な切替）。復元後、オフラインテスト640件全てpassを確認してからON実行。
- Reasoning LLM calls = 2/2（Stage1+Stage2、Repairなし、両Run同一）、Research calls = 0。
  document_ids・Case Context・Actor Intelligence Packは完全同一（同一theme/case_id、
  同一query_text）。
- 既存Baseline上書きなし、新規Fixture投入なし、External Research OFF（環境変数未設定）。

## 品質ガード集計

| | OFF | ON |
|---|---|---|
| 総チェック数 | 48 | 48（focus_selection系3チェックは両Runに存在、OFFはfield不在のため自明にpass） |
| Pass | 44 | 45 |
| Fail | 4 | 3 |
| Fail一覧 | supporting_knowledge_refs_grounded, temporal_hedge_language_not_present, key_point_controllers_covered, unsupported_actor_assumption_not_promoted | temporal_hedge_language_not_present, key_point_controllers_covered, unsupported_actor_assumption_not_promoted |

`supporting_knowledge_refs_grounded`（BIS institution_intelligence参照の既知の再発バグ、
本Phase①の別Runでも複数回観測済み）がONでは発生しなかった。ただし1サンプルであり、
Focus Selectionが直接この既知バグを修正したと断定はしない。

## 10観点の評価

| 観点 | OFF | ON | 判定 |
|---|---|---|---|
| **Question Fidelity** | 一貫して米中半導体・AI輸出管理を分析 | 同左 | **Same（両方良好）** |
| **Primary Focus** | 「MATCH法などによる制度的固定化」という**状態**を記述 | 「立法化＋域外適用が**どこまで進むか**」という**未決の決定点**を記述 | **Improved**：ONは静的な状態描写ではなく、成立/不成立で分岐する決定点として論点を立てている |
| **Why Now** | フィールド自体が無い（focus_selection=null） | 「MATCH法の成立可否がdecision_powerの構造を変え、成立/不成立でdiscriminating_hingeとして分岐が生じる」ことを明示 | **Working**（新規に生成、フィールドが機能した） |
| **Focus Trigger** | 該当なし | `decision_power`/`premise_or_constraint_change`/`discriminating_hinge`の3個（11種のうち選択的に3個、指示通り2-3個以内） | **Working** |
| **Downstream Decision Sensitivity** | selection_rationaleに「蝶番として説明力が高い」と書くのみ、何がどう変わるかは不明示 | 「規制強度・同盟国要求水準・日本企業の対中ビジネス許容範囲・コンプライアンス体制規模」まで具体的に接続 | **Improved** |
| **Alternative valid frames retained** | 棄却理由が「蝶番としては弱い」「主要な制約条件とは言い難い」とやや否定的な断定調 | 棄却理由が「Frameとして重要だが...主シナリオの説明枠組みとして採用しつつ、Primary KeyPointはその中で最も決定的な論点に置く」という**Not-primary-now**の枠組みで記述。H1(Actor-intent)はOFFでは`rejected`、ONでは`unresolved`——より断定を避ける方向 | **Improved** |
| **Actor Big Flowへの接続** | `perceived_path`/`event_role`が**3名全員(BIS/Trump/Xi)とも空欄** | `perceived_path`/`event_role`が**3名全員に生成**（Trump: bargaining_card、Xi: constraint、BIS: constraint、いずれも質的に妥当） | **Improved（最も顕著な差）** |
| **Candidate quality** | 4候補、記述的（状態の列挙） | 4候補、うち1つはH4(Frame-breaking)を明示的に候補化、決定点志向の書き方 | **Improved（軽微）** |
| **Main Scenarioへの影響** | 網羅的だが焦点の置き所が明示されない記述 | MATCH法の成立可否という分岐点を軸に、より簡潔に構成 | **Same〜Improved（軽微）** |
| **Speculative Noise** | 5仮説、過剰な量産なし | 5仮説、過剰な量産なし。むしろH1/H4の扱いがより慎重（unresolved/retained） | **Same（ONがやや良い）** |

## 最も重要な発見: Actor Big Flowへの波及効果

Focus Selection自体はkey_point_assessment内の追加であり、key_actor_profiles[].perceived_path/
event_role（Tier1）とはスキーマ上直接の依存関係が無い。にもかかわらず、**OFFでは3名全員が
未生成（空欄）だったperceived_path/event_roleが、ONでは3名全員に実質的な内容で生成された**。

Focus Selectionのprompt note末尾に明記した「Stage2/Candidate GenerationはこのPrimary Focus
を参照すること。候補はActor→Goal→Perceived Path→Event Role→...として生成し」という指示が、
Focus Selection自体の生成だけでなく、既存のActor Big Flowフィールドの想起・記入も後押しした
可能性がある。ただし**n=1のサンプルであり、この効果がFocus Selection追加による系統的なものか、
単なるRun間分散かは今回の1回の比較だけでは断定できない**。次回以降、複数サンプルでの再現性
確認が望ましい（今回は実施していない、追加コスト要）。

## Focus Selectionが本当に働いた具体例

ON側のfocus_selectionフィールド全文:
```json
{
  "trigger_types": ["decision_power", "premise_or_constraint_change", "discriminating_hinge"],
  "why_now": "MATCH法などの立法化は、米側の裁量ベースの規制から、法的拘束力の強い枠組みへの
    『前提条件の変化』をもたらし、誰がどこまで規制強度を決められるかというdecision_powerの
    構造を変える可能性がある。また、MATCH法が包括的に成立する場合と、骨抜き・不成立の場合
    では、次に起こる企業への影響・同盟国への圧力が大きく分岐するため、discriminating_hinge
    としても意味がある。",
  "decision_sensitive_outcome": "この論点の判断が変われば、米側の規制強度・同盟国への要求
    水準・日本企業の対中ビジネスの許容範囲・必要なコンプライアンス体制の規模など、今後の
    シナリオの中心部分が変わる。",
  "discriminating_observation": "MATCH法の議会審議・採決の行方（成立・不成立・大幅修正）と、
    成立した場合の具体的な条文内容（対象技術・域外適用範囲・例外規定）が、各シナリオを
    分岐させる観測点となる。"
}
```
これは「最もEvidenceが多いから」「質問文に近いから」ではなく、「この論点の判断が変われば
Scenarioの中心部分が変わる」という設計意図通りのdecision-sensitivityに基づく選定になっている。

## Session3でまだHuman Expertに聞く必要が残るGap

- Focus Triggerの**選び方自体**（今回は3個選んだが、なぜこの3個で他の8個ではないか）の
  判断基準はAIの記述からは読み取れるが、これが専門家の実際の判断と一致するかは未検証。
  Session3のCandidate Ranking/Rejection Logic抽出（`docs/expert-ai-eguchi-session3-design.md`）
  と統合的に検証すべき項目。
- `discriminating_hinge`のdiscriminating_observationが「議会審議の行方」という妥当だが
  一般的な観測点に留まっており、江口さんならではのより具体的な観測点（Session3のRejecting
  Cue連鎖で聞く`decision_trigger`に相当）との比較が今後の価値になる。
- Actor Big Flowへの波及効果（上記「最も重要な発見」）が系統的なものか偶然かは、Session3
  ではなく追加のOFF/ON再現性確認（実LLM）で検証すべき別軸の課題。

## Regressions

なし。オフラインテスト640件（既存626+新規14: schema2件+guard12件）全てpass。
既存Tier1機能（DirectionalPerception/perceived_path/event_role/actor_expected_response/
analyst_expected_response/Candidate Generation Operators）のGuard・schemaには変更を
加えていない。

## 変更ファイル・schema差分・prompt差分・guard差分（サマリ）

- `schemas/geo_expert_ai_response.schema.json`: `key_point_assessment.properties`へ
  `focus_selection`（optional object、trigger_types/why_now/decision_sensitive_outcome/
  discriminating_observation）を追加。既存requiredリストは変更なし（後方互換）。
- `geo_expert_schema.py`: `_FOCUS_SELECTION_NOTE`を新設し`MODE_GUIDANCE`ループへ
  `_KEY_POINT_NOTE`の直後に追加。新規Stage2モジュール・新規LLM callは追加していない
  （key_point_assessmentはStage1所有のため既存の1回の呼び出しへ相乗り）。
- `geo_expert_quality_guard.py`: `_focus_selection_why_now_present_check`（trigger_types
  設定時にwhy_now/decision_sensitive_outcomeが空でないことを検証）、
  `_focus_selection_trigger_types_within_enum_check`（enum外の値を検出）、
  `_focus_selection_not_conflated_with_anomaly_conclusion_check`（anomaly系triggerで
  断定的表現を検出）の3件を追加、`run_quality_guard`へ配線。いずれも追加LLM呼び出しなし。
- テスト: `tests/test_geo_expert_staged_execution.py`へschema検証2件、
  `tests/test_geo_expert_quality_guard.py`へguard検証12件（うちA-Fの6件は設計文書の
  意味テスト項目に対応）を追加。
