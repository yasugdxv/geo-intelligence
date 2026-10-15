# M9 — Actor × Media Intelligence Integration: A/B/C/D 実Azure比較

同一Case（M9 Pilot: Zelenskyy Oval Office Clash）・同一query_text・同一model
（Global-AzureOpenAI-DEV-gpt-5.1）・同一mode(`case_analysis`)/profile(`deep`)で、
`GEO_EXPERT_ACTOR_INTELLIGENCE_ENABLED`/`GEO_EXPERT_MEDIA_INTELLIGENCE_ENABLED`の
組み合わせのみを変えて4回実行した。生JSONは`docs/media-intelligence/m9_raw_responses/`
に保存済み。

## 1. Token / Call / Timing

| | calls | prompt_tokens | completion_tokens | total_tokens | wall_clock |
|---|---|---|---|---|---|
| A_base | 2 | 73,017 | 17,925 | 90,942 | 148.2s |
| B_actor_only | 2 | 74,670 | 16,738 | 91,408 | 244.1s |
| C_media_only | 2 | 77,572 | 19,803 | 97,375 | 162.2s |
| D_integrated | 2 | 80,348 | 19,152 | 99,500 | 177.2s |

全条件CallBudget(<=3)内の2 callsで完了。D(統合)はC(Media単体)比+2,125 token、A比
+8,558 tokenに留まり、「Dだけ極端に肥大化」は発生しなかった。

## 2. Rubric比較(0/1/2、根拠付き)

| # | 項目 | A | B | C | D | 根拠 |
|---|---|---|---|---|---|---|
| 1 | Key Point capture | 1 | 2 | 2 | 2 | Aは`control_type=mixed`で曖昧、B/C/Dは`actor_controlled`で「支援の条件付き化」という具体的構造まで踏み込む |
| 2 | Key Point selection rationale | 1 | 2 | 2 | 2 | B以降はActor/Media Patternを名指しで根拠に使う |
| 3 | Key Actor identification | 2 | 2 | 2 | 2 | 全条件でTrump/Zelenskyy/Putinの3名、差なし |
| 4 | Actor real priority/preference | 1 | 2 | 1 | 2 | Bはpersistent_preference/decision_patternを明示引用、Cは根拠薄め(alternative_interpretation欠落2/4行) |
| 5 | Intrinsic vs instrumental区別 | 1 | 2 | 2 | 2 | Aは区別語彙はあるが理由付けが薄い |
| 6 | Communication/signaling解釈 | 0 | 1 | 0 | 2 | Actor Packが無いA/Cでは評価不能。Dのみtrial balloon仮説でChannel一貫性検討まで到達 |
| 7 | Media source interpretation | 0 | 0 | 2 | 2 | A/Bはmedia_source_context自体が空(0 outlets) |
| 8 | Evidence weighting | 1 | 1 | 1 | **0** | **Dのみ新規QG違反`anonymous_sourced_hypothesis_confidence_capped`が発生(4章参照)** |
| 9 | Alternative hypothesis有用性 | 2 | 2 | 1 | 2 | Cは4行中2行でalternative_interpretation=null |
| 10 | Decision dynamics | 1 | 2 | 2 | 2 | Dのみ他Case(Hormuz)からの実例引用まで到達 |
| 11 | Scenario derivation | 1 | 2 | 2 | 2 | B/C/DはsupportingにActor/Media知見を明示引用 |
| 12 | Over-interpretation control | 2 | 2 | 1 | 1 | C/Dで一部Hypothesisの裏付け不足(4章参照) |
| **合計/24** | | **13** | **20** | **18** | **19** |

**重要な留保**: 合計点はD>Cだが僅差であり、D最高得点ではない。項目8(Evidence
Weighting)でDが唯一0点を取った(過信の実例が発生)ことが響いている。これは
「統合すれば単純に良くなる」わけではないことを示す誠実な結果であり、そのまま報告する。

## 3. Official評価: A vs D(主比較)

- **primary_key_point**: A「米国の対ウクライナ軍事支援・情報共有の継続可否」(やや一般的)
  → D「米国が対ウクライナ軍事支援・情報共有を交渉カードとして一時停止し得る構造(支援の
  条件付き性)」(より具体的な因果メカニズムまで踏み込む)。**Primary Key Point自体は
  同じ出来事系列を指すが、selection rationaleとhinge/control structureの解像度が
  A→Dで明確に向上した**(control_type: mixed→actor_controlled)。
- **selected_actor_ids**: 変化なし(3名、Trump/Zelenskyy/Putin)。ただしDでは
  `actor_ref`表記が`actor_trump`→`trump`という異なる命名規則になった(4章の
  留意事項参照、内部的な不整合ではなく命名規則のドリフト)。
- **media_source_context**: A=null(0 outlets)→D=1件(ただし3つのHypothesisを統合した
  濃縮された内容)。**A単体では一切生成されなかったMedia解釈が、D統合条件で実質的な
  内容を持つに至った。**
- **Scenario**: A「支援停止が約1週間で終わり...」という一般的な根拠→D「トランプの
  過去の対イラン・ホルムズ海峡事案で...取引志向のパターンが存在すること」という
  **他Case(Hormuz Pilot)からの具体的な参照を伴う根拠**へ強化された。

## 4. 統合効果の具体例(依頼書Section17、最低1つの実データ確認)

### Case A: Actor単体では読めなかったMedia Signalが読めた例 ✓確認

B(Actor Only)の`media_source_context`は**完全に空**(0 outlets)。Actor Intelligenceが
あってもMedia Intelligenceが無いと、Expert AIは自発的に情報環境の解釈を行わない。
C/D(Media ON)で初めてmedia_source_contextが実質的な内容を持った。

### Case B: Media IntelligenceによってActor Intentの推定が修正された例 ✓確認

D(統合)のPutin行のalternative_interpretationにのみ、「ロシアがこの事件を過度に利用
すると、第三国からの信頼を損なう可能性」という記述が現れた。これはB(Actor Only)の
Putin行(「停戦シグナルは主に第三国向けのイメージ戦略」という一般的な代替解釈のみ)には
無く、C(Media Only)にもない、**D固有の新規記述**である。Media Intelligence
（第三国は自国ナラティブ補強に対立を利用するというPattern、および評判コストという
視点）がActor側の解釈（ロシアの持久戦略）へ新しい次元を加えた例と判断できる。

### Case C: Actor/Media両方が同じ方向を示しConfidenceが上がった例 △部分的に確認

Key Pointの「支援の条件付き化」という解釈自体はB/C/D全てで方向性が一致するが、
`confidence`ラベル自体は全条件"中"のまま変化していない。ただし`supporting_evidence`の
質・量はA(3件、一般論中心)→D(4件、Actor Persistent Preference+他Case実例を含む)へ
明確に厚みを増しており、**カテゴリカルなConfidence値の上昇ではなく、根拠の厚み
(evidence depth)という形での確信度向上**として観測された。単純な「上がった/上がって
いない」の二値では捉えきれない、より現実的な結果として報告する。

### Case D: Actor/Mediaが矛盾しAlternative Hypothesisが生成された例 ✓確認

D統合条件のTrump行(「対ウクライナ支援の規模・条件」、instrumental_value=
negotiating_card)のalternative_interpretationに「支援停止が純粋な技術的調整である
可能性」が現れた。Actor Pattern(トランプの取引志向・レバレッジ活用という持続的傾向)が
示唆する「意図的な交渉カード化」という解釈に対し、より平凡な代替説明(技術的調整)を
明示的に対置している。

### Case E: Physical/Specialist Evidenceによって両方のNarrativeが弱められた例 △部分的に確認

独立したOSINT/軍事専門家の"outlet"行としては現れなかったが、D統合条件の
hypothesis[0]のalternative_explanationに「投資調査報道・一般ニュースが、軍事専門家・
OSINTが留保する因果関係(支援停止と戦況の直接因果)を過大に主張している可能性もある」
という記述が現れ、Pilot1で確立した`media_pattern_media_overclaim_vs_specialist_hedge`
の趣旨が明示的に反映されている。独立したEntryとしてではなく、既存Hypothesisの
alternative_explanation内に埋め込まれる形での反映であり、**完全な形での確認ではなく
部分的な確認**として報告する。

## 5. Evidence Weighting上の重要な発見(過信の実例、正直に報告)

D統合条件でのみ、新規Quality Guard違反`anonymous_sourced_hypothesis_confidence_capped`
が発生した。該当箇所:

```json
{
  "status": "supported", "confidence": "高",
  "hypothesis_text": "単一の匿名当局者に依拠した『口論の具体内容』『支援停止の真因』に
    関するスクープは、当事者側の意図的リーク（trial balloon）である可能性があるが、
    独立した裏取りがない限り慎重に扱うべきである。",
  "corroboration_basis": "behavioral_corroboration",
  "corroboration_target": "underlying_event_only"
}
```

`confidence=高`でありながら`corroboration_target=underlying_event_only`
(出来事の発生は確認できたが、意図(trial balloon)自体は確認できていない)であり、
既存Guard契約(`corroboration_target=hypothesis_itself`が必須)に違反する。C
(Media Only)では同種のHypothesisが生成されていない(またはconfidenceが低く抑えられて
いた)ため、**Actor Intelligenceを追加したことで、Media発のHypothesisに対する確信度が
（不当に）底上げされた可能性がある**——Actor側の「トランプは取引志向でレバレッジを
好んで使う」という持続的傾向が、Media側の「匿名スクープはtrial balloonかもしれない」
という仮説と結びつき、両者が"整合しているように見える"ことで見かけ上の説得力が
増し、Evidence Discipline（出来事の確認と意図の確認の峻別）が緩んだと考えられる。
**これはQuality Guardが正しく検出した、Section20が事前に警告していたリスクの実例
である**。修復(repair)は本テストでは実行されなかった(このチェックは非repair対象の
ため、人手レビューでの是正が必要)。

## 6. Actor / Media Conflict事例

4章Case Dの通り、Trump行で「意図的な交渉カード化」(Actor Pattern由来)と「純粋な技術的
調整」(代替解釈)が対置された。また、hypothesis[0]で「同盟内の危機として煽る"press"の
framing」と「投資調査報道の過大な因果主張」という、Media Pattern同士の緊張関係も
明示された(既存Media Patternの`media_pattern_press_more_emotive_than_official_
statement`と`media_pattern_media_overclaim_vs_specialist_hedge`が同一Hypothesis内で
併記される形)。

## 7. Physical Evidenceによる修正事例

D統合条件のhypothesis[0]のalternative_explanationが、ISW/ACLEDの慎重な留保
(`media_pattern_media_overclaim_vs_specialist_hedge`)を明示的に参照し、一般報道の
因果主張を弱める方向に働いた（4章Case E参照、部分的確認）。

## 8. Quality Guard結果一覧

| | initial/remaining failed checks |
|---|---|
| A | fact_claims_have_source_ref, stakeholders_grounded_in_source_pack, investigation_question_answer_grounded |
| B | fact_claims_have_source_ref, stakeholders_grounded_in_source_pack |
| C | fact_claims_have_source_ref, stakeholders_grounded_in_source_pack, media_hypothesis_evidence_grounded |
| D | fact_claims_have_source_ref, stakeholders_grounded_in_source_pack, media_hypothesis_evidence_grounded, **anonymous_sourced_hypothesis_confidence_capped** |

`fact_claims_have_source_ref`/`stakeholders_grounded_in_source_pack`は4条件共通
(本テストがtheme_id=None・実Document無しの簡易構成であることに起因する既知の制約で
あり、M9固有の問題ではない)。`media_hypothesis_evidence_grounded`はMedia ON時に一貫して
発生(実記事コーパス不在という同じ制約、M8.5から継続する既知の課題)。
`anonymous_sourced_hypothesis_confidence_capped`は**D固有の新規発見**(5章)。
新設3チェック(`_historical_pattern_not_asserted_as_certain_fact`/
`_cross_intelligence_contradiction_not_silently_dropped`/`_evidence_attribution_
not_conflated`)はいずれも4条件で違反なし(pass)。
