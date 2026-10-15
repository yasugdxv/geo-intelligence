# Media Intelligence Build — M8.5 Zelenskyy OFF/ON 実Azure比較

## 1. 実行条件

`geo_expert_engine.run_expert_request_staged`(DB非保存、比較実験専用の既存関数)を、
`mode="case_analysis"`, `profile="deep"`(media_context Stage2モジュール発火条件)、
`theme_id=None`, `case_id=None`で実行。query_textにZelenskyy Oval Office衝突〜
ジェッダ会談までの状況とEvaluation依頼を記載。

- **OFF**: `GEO_EXPERT_MEDIA_INTELLIGENCE_ENABLED`未設定(既定OFF)。Media Intelligence
  Pack注入なし。
- **ON**: 同Flag有効化、`media_intelligence_statuses=("active","candidate")`を明示指定
  (Human Review未了のCandidateを本番へ混入させないための既定`("active",)`を、この
  比較実験に限り明示的に上書き。Actor Intelligence Flagは両条件とも既定OFFのまま変更せず、
  単一変数比較を徹底)。

## 2. Token/Call

| | calls | prompt_tokens | completion_tokens | total_tokens |
|---|---|---|---|---|
| OFF | 2 | 69,237 | 14,442 | 83,679 |
| ON | 2 | 73,019 | 18,138 | 91,157 |

Media Intelligence Pack注入による増分: prompt +3,782 token、completion +3,696 token、
合計+7,478 token(注入Pack自体は整形後約2,990文字)。CallBudget(<=3)は両条件とも
2回で収まっている。

## 3. 最重要の実質的差分

### 3-1. media_source_context: null → 実質的な内容で充填

**OFF**: `media_source_context`は**完全にnull**。profile=deep・mode=case_analysisで
`media_context`モジュール自体は発火条件を満たしていたにもかかわらず、Media Intelligence
Packという「参照材料」が無い状態ではExpert AIは自発的にMedia/Information Reasoningを
展開しなかった。

**ON**: `media_source_context`が完全に充填され、しかも**注入したMedia Pattern
Candidatesの内容を直接反映**している:
- Kyiv Independent想定の観測: 「同一国pressが自国政府公式声明より感情的framingを用いる
  傾向」→ `media_pattern_press_more_emotive_than_official_statement`をそのまま適用。
- ロシア国外亡命系メディアの観測: Novaya Gazeta Europeの編集判断の違いに言及
  → `media_pattern_exile_media_kremlin_reaction_inclusion_diverges`をそのまま適用。
- ISWの観測: 「方法論的に独立したOSINT・紛争データに基づき、因果関係を過大に主張する
  一般報道との差別化」、`corroboration_basis: documentary_corroboration`・
  `corroboration_target: hypothesis_itself` → `media_pattern_media_overclaim_vs_
  specialist_hedge`をほぼそのまま適用。

**重要な留保**: Expert AIは各Outletを「Kyiv Independent（想定）」「米欧投資調査系
メディア（想定）」のように明記し、`caveat`欄に「具体的記事IDは未提示のためStage1での
Media Profileに基づく一般傾向としてのみ参照」と記載した。**実際の生記事・Documentを
与えていないため、Expert AIはPatternからの一般化であることを正しく自己申告しており、
確定した観測であるかのように偽装していない。** これはHallucinationではなく適切な
Confidence/Provenance管理であり、Media Intelligence Packが「参照可能な安定知識」として
機能した証拠と言える。

### 3-2. Key Point Assessmentの解釈フレームが変化

| | OFF | ON |
|---|---|---|
| primary_key_point | 米国の対ウクライナ軍事支援・情報共有継続をめぐる**米国内政治**(選挙・負担感) | 米国の支援を**「条件付き交渉カード」か「同盟コミットメント」か**という方針の性質 |
| control_type | mixed | actor_controlled |

OFFは「なぜ米国は支援を止めたり再開したりするのか」を国内政治要因(選挙・世論・財政)
で説明する一方、ONは同じ事実系列を「支援自体が交渉材料化されているか」という
Leverage/取引的な軸で説明している。**これはMedia Intelligence Pack(情報環境・
Framingの差異という視点)を与えたことで、Expert AIが局面全体を見る軸そのものを
シフトさせた可能性を示唆する**(単なる文章量の増加ではなく解釈の枠組みの変化)。

### 3-3. Key Stakeholder選定にPutinが追加

- OFF: `selected_actor_ids: ['zelenskyy', 'trump']`(2名)
- ON: `selected_actor_ids: ['actor_zelenskyy', 'actor_trump', 'actor_putin']`(3名)

Media Intelligence Packがロシア側(Kremlin公式・Meduza・Novaya Gazeta Europe)の
情報環境を明示的に提示したことで、ロシアの反応自体が局面解釈に不可欠な要素として
認識され、Putinが鍵となるActorとして追加された。

## 4. Quality Guard結果の差分

| | OFF | ON |
|---|---|---|
| completeness_status | incomplete_after_repair | incomplete_after_repair |
| initial_failed_checks | fact_claims_have_source_ref, stakeholders_grounded_in_source_pack, key_point_control_type_consistency, **situation_actor_assessment_grounded** | fact_claims_have_source_ref, stakeholders_grounded_in_source_pack, key_point_control_type_consistency, **media_hypothesis_evidence_grounded** |

ONで新たに失敗した`media_hypothesis_evidence_grounded`は、注入したMedia Patternに
基づく仮説の`supporting_evidence_ids`が(実記事Documentを与えていないため)空である
ことに起因する、**本テストの設計上避けられない制約**(query_textのみでtheme_id/実
Documentコーパスを与えていないため)。Media Intelligence Pack自体の欠陥ではなく、
今回の簡易テスト条件の限界として記録する。

## 5. 結論

**Stable Media Intelligence(Media Pattern Candidates + Media Profiles)は、実際に
Case Interpretationへ測定可能な影響を与えることを実Azure呼び出しで確認した**:
1. Media/Information Reasoningフィールド自体の生成(null→充填)
2. Key Point解釈の枠組みのシフト
3. Key Actor選定への追加(Putin)
4. 適切なConfidence/Provenance管理を保ったまま(捏造ではなく明示的な「一般化」表示)

ただし1 Run限りの単発比較であり、統計的検証ではない。またmedia_hypothesis_evidence_
grounded失敗は実Documentコーパス不在という本テスト固有の制約であり、実運用(Case+
実記事あり)での挙動は別途検証が必要。
