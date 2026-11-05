# Session 3 Expert Review — 実施ガイド

## 1. 実施順序(厳守)

1. `01_zelenskyy_blind_review_input.md`を専門家へ提示し、Q1〜Q17へ回答してもらう
   (Part A、Blind Expert Assessment)。**この時点でAIの分析は一切見せない。**
   回答は自由記述とし、Agree/Disagree等の評価尺度は使わない(1-1節参照)。
2. Part Aの回答を保存・確定させる(後から書き換えない)。
3. `02_zelenskyy_ai_review_pack.md`を提示する(Part B)。
4. `03_expert_review_form.md`へ、AI分析各項目についてのRating・Correction・
   Reason・Evidenceを記入してもらう。単に「AIのここが違う」で終わらせず、
   1-2節の5つの深掘り観点を必ず聞く。
5. Part A/Part Bの回答を`04_expert_ai_gap_schema.json`形式で構造化保存する。

**専門家の実際の回答が得られる前に、AIをExpert Judgmentの代用にしない。** 本ガイド
作成時点でPart A/Bとも実施されておらず、Expert Judgmentは1件も確定していない。

### 1-1. Part A進行の原則(自由記述、Anchoring防止)

Q1〜Q17は思考を漏れなく引き出すためのプロンプトに過ぎない。評価尺度
(Agree/Partial/Disagree/Missing/Over-interpreted)はPart Bでのみ使う。望ましい
進行順序は、専門家自身の言葉で以下の流れを自然に語ってもらうこと:

```
専門家自身のKey Point
  ↓
Key Players
  ↓
Actorの目的・Priority
  ↓
重要Evidence
  ↓
Scenario
```

`01_zelenskyy_blind_review_input.md`の資料選定自体、AIの分析が重視した項目を
基準にしていない(立場ごとのバランス優先)。専門家に「なぜこの資料を選んだのか」
と聞かれた場合は、この選定方針をそのまま開示してよい。

### 1-2. Part B進行の原則(「なぜ違うか」を掘る)

Part Bでは、AI分析とExpertの差分を記録するだけで終わらせない。各差分について
最低限以下を掘り下げる:

- なぜそのKey Pointを選んだのか(AIとは違う場合、AIの選定根拠のどこに納得
  できないか)
- どのEvidenceを他より重く見たのか(AIが軽視した/重視しすぎたものはどれか)
- どのActorの発言は額面通り取らなかったのか(Stated vs Real Preferenceの
  乖離をどう判定したか)
- どのMedia Signalは無視したのか(AIが取り上げた仮説のうち、専門家なら
  そもそも検討にすら値しないと考えるものはどれか)
- 何が起きれば自分の見方を変えるのか(Disconfirming signal/switch condition
  の専門家版)

**今回のSession3で最も重要なのは、AIの点数をつけることではなく、専門家との差を
Gap分類(5節)まで分解すること**である。

## 2. Provenance(Golden Case昇格可能な形で保存する)

各回答に必ず付与する:

```
case_id: "zelenskyy_oval_office_clash_2025"
reviewer: (実施時に記入、匿名化が必要な場合はreviewer_role等で代替可)
review_source: "session3_expert_review_pack"
review_date: (実施日)
review_version: "v1"
reviewed_material_version: "frozen_2026-08-31"(00章のfingerprint群を参照)
blind_review: true(Part A)/false(Part B)
```

これらが揃って初めて、既存のGolden Case採用条件(Session2 Reasoning Upgrade計画
「Phase0」で定義済み: reviewer/review_source/review_date/relevant_excerpt or
structured_judgmentが追跡可能であること)を満たし、Golden Caseへ昇格できる。

## 3. Part A/Part B分離の理由(Anchoring防止の徹底)

Part Aで専門家自身の判断を先に確定させないと、Part Bで提示されるAIの具体的な
Key Point・Actor評価・Scenarioが専門家の思考を誘導し、「AIと同じ意見だった」のか
「AIに影響されて同じ意見になった」のかが区別できなくなる。この区別自体が
Session3の核心的な価値(AIの独立した推論能力の検証)であるため、順序は必ず守る。

## 4. Session後のRubric評価(依頼書26章)

Part A/Bの回答が揃った後に初めて、以下の公式Rubric評価を行う
(Session2 Reasoning Upgrade計画で定義した8項目 + M9 12項目Rubricのいずれかを
流用可、または本Caseに合わせて再定義):

```
Key Point capture / Key Point selection rationale / Key Actor identification /
Actor real priority・preference / Instrumental vs intrinsic interpretation /
Evidence grounding / Alternative hypothesis usefulness / Over-interpretation control
```

これが**初めて実施可能になる「本物の」Golden Case評価**である
(これまでのSession2 E2E検証は「Golden Case Expert Judgmentデータ0件」により
PROVISIONAL PASSに留まっていた)。

## 5. Gap Origin → チューニング方針の振り分け表(依頼書21・27章)

Expert Reviewで見つかった差分は、**まずgap_originで分類してから**チューニング
方針を決定する。専門家コメントをそのままPromptへ追加しない。

| gap_origin | 意味 | 対応方針 |
|---|---|---|
| `reasoning_gap` | Key Pointの置き方・Scenario導出等、Reasoning Loop自体の課題 | Thinking OS tuning(`geo_expert_schema.py`のMODE_GUIDANCE等) |
| `actor_intelligence_gap` | 特定ActorのProfile・Decision Pattern・Persistent Preferenceの不足・誤り | Actor Intelligence tuning(該当Actorのstructured_content拡充、Human Review経由) |
| `media_intelligence_gap` | Media Profile・Episode・Patternの不足・誤り | Media Intelligence tuning(該当Sourceの拡充、Human Review経由) |
| `foundation_gap` | Foundation Pack(一般的な地政学的構造知識)の不足 | Foundation expansion |
| `source_gap` | 特定Sourceへのアクセス不足・Registry未収録 | Source Registry拡充(`media_source_registry.json`) |
| `evidence_weighting_gap` | Confidence判定・Corroboration基準等、重み付けロジックの課題 | Evidence Weighting tuning(Quality Guard・M9.1型の決定的補正) |
| `implementation_bug` | Schema契約違反・Grounding Check漏れ等の実装上の不具合 | 該当コードの修正(バグ修正、チューニングではない) |
| `expert_tacit_knowledge` | 専門家固有の暗黙知(データからは自明に導けない経験則) | **単一Caseのみでの反映は避ける。複数Case・複数専門家での再現を確認してから
  tuning_candidate=trueとする**(過学習防止) |

## 6. 推測でPhaseを進めない

Gap分類の結果、Thinking OS/Actor Intelligence/Media Intelligence/Evidence
Weighting/Foundationのどこを優先するかは、**実際のGap分布(件数・重大度)を見てから
決定する**。事前に「今回はActor Intelligenceを強化するはずだ」等と決め打ちしない。

## 7. 次Case(高市・台湾)について

第2 Review Caseとして高市・台湾関連Caseを使用する余地を残すが、**Zelenskyy Case
のPart A/B/Gap分類が一通り完了してから**着手を判断する。同時並行で複数Case用の
Blind Input/AI Review Packを大量生成しない。

## 8. 本Stop Gateで確定していないこと

- 専門家の実際の氏名・所属・回答内容(Part A/Bとも未実施)
- Rubric評価の実際のスコア(Session未実施のため算出不可)
- Gap分類の実際の件数・分布(同上)
- 高市・台湾Caseへの着手可否
