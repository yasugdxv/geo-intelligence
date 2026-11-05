# Session3設計 — 江口さんインタビュー（Phase②）

作成日: 2026-09-06 / 更新: 2026-09-06（ユーザーフィードバックにより4点調整）
前提: Phase①完了（`docs/expert-ai-validation/eguchi_tier1_directional_functional_test/report.md`
のCloseout参照）。AIは既にActor Big Flow・DirectionalPerception・Actor期待/Analyst予測分離・
Candidate Generation→Scenario接続を実地で生成できることを確認済み。

## 0. Session3の目的（一つに絞る）

**Candidate Ranking / Rejection Logic と Frame選択の根拠を、江口さん本人から構造化して
抽出する。それ以外は聞かない。**

やらないこと（Phase①で答えが出ているため聞く必要がない）:
- 「どういう観点で局面を見るか」「ステークホルダーをどう考えるか」という一般論
- Frame・Actor Flow・候補それ自体をゼロから作らせる練習（AIも一定水準で作れることを確認済み）
- DirectionalPerceptionやEvent Role等の概念そのものの説明・すり合わせ（設計文書側で既に定義済み）

聞くべきこと（AIがまだ持っていないもの）:
1. 複数の候補（Frame／Key Point／Hypothesis／Scenario）を前にしたとき、何を根拠に**順位を
   つけるか**
2. 何を見て**棄却するか**（棄却の閾値・決め手）
3. どんな**違和感**があればFrame自体を疑い、切り替えるか
4. その判断の元になっている情報は、Source Pack・Case Context・Actor Intelligenceに
   **書かれているものか、それとも江口さん個人の経験・非公開情報・実務感覚か**（これがAIとの
   本質的なギャップの正体）

## 1. 使うケース

新規Fixtureは作らない。既存の凍結・保存済み成果物をそのまま使う（実行し直さない、
AIの出力を汚染しない）。

### 主候補: Case5 Kim（`docs/expert-ai-validation/original_after_v2/scorecard.md`他）

理由: 「核・ミサイル=体制保証を無批判にPrimary維持」という**構造的弱点がNeutral・Original v1・
v2の3回連続で再現された唯一のケース**（`geo_expert_ai_depth_grounding_roadmap.md`メモリ参照）。
AIが同じ場所で3回とも同じ見立てに落ち着くという事実自体が、「AIには見えていない／棄却できない
何か」が存在する強い証拠であり、Session3で最も価値の高い題材。

### 副候補1: Case1 Trump-Xi（本Phaseで最も深く検証済み）

理由: Tier1機能（DirectionalPerception/perceived_path/event_role/Actor期待vs Analyst予測）が
実際に発火した唯一のケースであり、AI自身の`hypothesis_register`（H0-H4）・
`directional_perceptions`・`main_scenario`が既に手元にある。**AIが出した具体的な候補セットを
使って「これのどこにどう反応するか」を聞ける**、最も準備コストの低いケース。

### 副候補2: Case3 Netanyahu（構造要因 vs Actor要因の優先順位で既知の揺れがあるケース）

理由: `case3_document_relevance_classification.md`等で、構造的制約（Hormuz/Iran情勢）が
Netanyahu個人の分析を「乗っ取る」か「従属統合されるか」で過去に揺れが観測されたケース。
「構造要因をどこまで前面に出すか」という順位付けの実例として使える。今回は予備（時間不足なら
次回に回す）。

### ケース構成: Case5（主軸）＋Case1（Contrast、時間が許せばではなく必須）

Case5だけで終えると、抽出したRanking/Rejection Logicが「北朝鮮についての江口さん固有の知識」
なのか「他の地政学ケースにも転用できる判断方法」なのか判別できない。そのため
**Session3全体の20〜30%を必ずCase1 Trump-Xiの短時間Contrastに充てる**（詳細分析はしない、
Kimと同じ問いを繰り返し「同じ判断の仕方をしているか」だけを見る）。**Case5で抽出した
Ranking/Rejection Logicの内容はCase1のセッションが終わるまで江口さんへ提示しない**
（Case1側の回答が誘導されないようにするため）。

- 前半70〜80%: Case5 Kim（Blind→Frame固定→Candidates/Ranking/Rejection→Reveal→
  差分→Rejecting Cue/Re-entry condition→判断根拠の出所）
- 後半20〜30%: Case1 Trump-Xi（同じ流れを短く繰り返す、Kimの抽出結果は見せない）

## 2. AI出力の見せ方（ShadowBox形式）

**盲検（Blind）→ 江口さんの独立判断 → 開示（Reveal）→ 差分への反応、の順で必ず進める。**
先にAI候補を見せると、江口さんの判断がAIにアンカリングされ、抽出したい「AIには無い判断軸」が
埋もれてしまう。

### Step A（Blind）
江口さんへ渡すのは**AIと同じ入力**（Source Pack本文・Case Context・Actor Intelligence Pack
の内容、Foundation Pack）のみ。AIの出力（Frame・候補・順位・Scenario）は一切見せない。

### Step B（江口さんの独立判断）

**①Frameを他と切り離して先に固定記録する。** 「この局面は何の話か」への回答は、候補の順位付け
とは別レコードとして最初に確定させる（後述5章の`frame_record`）。理由: AIが「北朝鮮の核抑止・
体制保証の問題」とFrameしている一方、江口さんが「米国との取引条件を作る話」あるいは
「中国・ロシアも含む交渉ポジション形成の話」と捉えていた場合、それはCandidate Ranking以前の
**Frame Selectionでの分岐**であり、両者を混同すると「候補順位が違う理由」を誤ってRanking
Logicの問題として処理してしまう。Session3では常に「Frameが違うのか、Frameは同じで
Candidate順位が違うだけなのか」を最初に切り分ける。

以下を、江口さん自身の言葉で出してもらう（形式は自由記述でよいが、後段の構造化のために
最低限この単位で聞く）:
1. この局面は何の話か（Frame、①として単独固定）
2. 考えられる解釈・シナリオ候補（複数、思いつく限り、①のFrameの下で）
3. その中でどれを本命(Primary)とするか、次点は何か、順位付け
4. 棄却する候補があれば、どれを・なぜ棄却するか
5. 「これが観測されたら自分の見立ては間違っている」と言える兆候（Disconfirming Signal）

### Step C（Reveal）
AIが実際に出した内容（`hypothesis_register`・`candidate_key_points`・`main_scenario`・
`rejected_candidates`・`selection_rationale`）をそのまま提示する。**AIの出力を「正解」として
提示しない**——あくまで比較対象として渡す。

### Step D（差分への反応、ここが本体）
Step BとCの**差分だけ**を材料に質問する。一致した部分は深掘りしない（時間の無駄になる）。
まず「①Frameが違うのか」「②Frameは同じでCandidate順位・棄却が違うのか」を最初に判定してから、
該当する方だけ深掘りする。

**棄却・順位変更の「決定的Cue」を必ず一段掘る（今回最重視）**。「何位です」で終わらせず、
以下の連鎖を毎回聞く:

```text
Candidate（対象の候補）
  ↓
Rejecting Cue（どの一点を見た時点で、主仮説ではないと思ったか）
  ↓
Reason（なぜその一点が決め手になるのか）
  ↓
Counterfactual condition（その一点が無かったら、この仮説を残すか）
  ↓
Re-entry condition（何が観測されたら、この仮説を1位に戻すか）
```

質問の型:
- 「どの一点を見た時点で、この読みは主仮説ではないと思いましたか」
- 「その一点がなければ、この仮説を残しますか」
- 「何が観測されたら、逆にこの仮説を1位に戻しますか」
- 「AIはこの候補を`unresolved`のまま残していますが、あなたは棄却していますね。決め手は何ですか」
- 「あなたが本命に選んだこの候補を、AIは候補にすら挙げていません。これはAIが何を見落として
  いるからだと思いますか」
- 「AIのこの順位付けの根拠(`selection_rationale`)を読んで、どこに違和感がありますか」
- 「この判断の根拠は、渡した資料のどこかに書いてありましたか。それとも、資料には無い情報や
  過去の知見から来ていますか」（←最重要の問い、下記の詳細な内訳で深掘りする）
  - 資料に書いてある(current_material)
  - 以前から知っていた公開情報を思い出した(prior_public_knowledge)
  - 複数の公開情報を組み合わせて意味づけした(logical_inference)
  - 他の地政学ケースで見たパターンを当てはめた(cross_case_pattern)
  - 業界・実務経験からの一般則(professional_experience)
  - 「普通ならこうなる」という期待値とのズレを感じただけ(intuition_or_pattern_recognition)
  - 非公開情報を知っている(private_knowledge)

## 3. 質問順序・時間配分（全体をKim70-80% / Trump-Xi 20-30%に配分）

### Case5 Kim（前半、全体の70〜80%、目安70〜90分）

1. （5分）オリエンテーション: 「今回は局面の読み方一般ではなく、複数の解釈候補をどう順位付け・
   棄却するかに絞って聞きます」と明示し、期待値をずらさない。
2. （15分）Step A/B: 資料読み込み＋独立判断。**まずFrameだけを固定**してから、候補・順位・
   棄却理由・Disconfirming Signalへ進む。
3. （5分）Step C: AI出力の開示
4. （35-45分）Step D: 差分への反応（本体、最も時間を割く）。①Frame一致か分岐かを先に判定→
   ②Candidate差分のRejecting Cue連鎖（Candidate→Rejecting Cue→Reason→Counterfactual→
   Re-entry）→③判断根拠の出所（judgment_basisの内訳）
5. （10分）Frame切替の閾値についての追加質問（下記4章）
6. （5分）まとめ、Case1への橋渡し

### Case1 Trump-Xi（後半、全体の20〜30%、目安20〜30分、深掘りしない）

**Case5で抽出したRanking/Rejection Logicの内容はここでは一切提示しない。** 同じ問い
（Frame→独立判断→Reveal→差分の要点のみ）を短く繰り返し、「Case5と同じ判断の仕方を
しているか」だけを見る。Rejecting Cueの連鎖まで深掘りする必要はなく、Frameが一致するか・
判断根拠の出所の傾向がCase5と同系統か、の2点確認で十分。

## 4. Frame切替の閾値を聞く追加設問（各ケース共通で必ず入れる）

Step Dの後、以下を明示的に聞く:
- 「今回のFrame・本命候補が仮に正しいとして、次にどんな一つの事実が出てきたら、あなたは
  それを疑い始めますか」
- 「過去の実務で、最初のFrameを覆した経験はありますか。その時、何が決め手でしたか」
  （一般論ではなく、具体的な実例を求める）
- 江口さんの回答に「〜のパターンは見たことがある」「〜の場合は普通〜しない」という発言が
  出たら、その場で「それは今回の資料のどこにも書かれていない情報ですか」と確認する。

## 5. 記録・構造化するもの

### 5.1 Frameレコード（Candidateとは別の単位、ケースごとに1件）

Step Bの①で候補順位付けより前に確定させる。Candidateレコードより上位の単位として扱う。

```yaml
case_id: ""
eguchi_frame_freetext: ""            # 「この局面は何の話か」の生の発言
ai_frame_freetext: ""                 # AI側のprimary_key_point/one_line_assessment（Reveal後に転記）
frame_match: ""                       # same | different | partial
frame_divergence_note: ""             # frame_match=different/partialの場合、何が違うかの要約
```

### 5.2 Candidateレコード（Frame／Key Point／Hypothesis／Scenario、いずれも本質は同じ
「候補」として扱う）

既存の`03_candidate_operators.json`/`05_contract_mapping_reference.json`の語彙と揃える
（新しい概念を極力増やさない、今回`decision_trigger`等の少数のみ追加）:

```yaml
candidate_id: ""
frame_id: ""                       # 5.1のFrameレコードに紐付け
source: "eguchi | ai"               # どちらが出した候補か
eguchi_rank: null                    # 江口さんの順位（1が最有力）、江口さんが挙げなかった候補はnull
eguchi_status: ""                     # retained | rejected | unresolved | not_considered
ai_status: ""                         # 対応するAI側のstatus（あれば）

# --- ここから今回追加: 棄却・順位変更の決定的Cueの連鎖 ---
decision_trigger: ""                  # そのCandidateの順位を上げた／下げた／棄却した
                                       #   決定的なCueは何だったか（生の発言、最重要フィールド）
decision_trigger_reason: ""           # なぜそのCueが決め手になるのか
counterfactual_condition: ""          # そのCueが無かったら、この仮説を残すか（回答＋根拠）
re_entry_condition: ""                # 何が観測されたら、この仮説を1位に戻すか
# --- ここまで ---

rejection_reason_freetext: ""         # 江口さんの生の発言（decision_triggerと重複してよい）
rejection_reason_category: ""         # 後付けで分類: evidence_contradiction / implausible_actor_model /
                                       #   requires_unsupported_assumption / known_pattern_mismatch /
                                       #   structural_constraint_dominates / other
judgment_basis: ""                    # 内訳を広く取る（privateだけに絞らない）:
                                       #   current_material | prior_public_knowledge | cross_case_pattern |
                                       #   professional_experience | private_knowledge | logical_inference |
                                       #   intuition_or_pattern_recognition | other
judgment_basis_note: ""               # judgment_basisの内容の詳細（具体的実例・組み合わせた情報源等）
divergence_type: ""                    # ai_missed_this_candidate / ai_kept_what_eguchi_rejected /
                                       #   ai_rejected_what_eguchi_kept / ranking_order_differs /
                                       #   no_divergence
provenance_status: ""                  # このレコードの各フィールドが、江口さんの発言そのものか
                                       #   (expert_stated)、その場でファシリテーターが解釈・要約した
                                       #   ものか(interviewer_inferred)、後日レコードを本人に見せて
                                       #   「はい、その理解で合っています」と確認を得たものか
                                       #   (expert_confirmed_reconstruction)を区別する。
                                       #   Session3直後はexpert_stated/interviewer_inferredのみで
                                       #   よく、confirmed化は事後工程（6章）で行う。
```

### 5.3 Frame切替閾値（ケースごとに1件、Candidateとは別）
```yaml
frame_switch_trigger_freetext: ""
frame_switch_trigger_is_in_materials: null   # true/false/partial
past_real_example_freetext: ""                # 過去実務での具体例（あれば）
```

## 6. Session3後のExpert AIへの反映方針

**その場でAIへ反映しない。** Session3自体はデータ収集であり、反映は別工程。

0. **反映判断の前に、記録を3種類へ仕分ける（最初にやること）**:
   - `expert_stated`: 江口さんが実際に口にした言葉そのもの（逐語・要約可だが本人の表現を保つ）。
   - `interviewer_inferred`: ファシリテーターがその場で解釈・要約したもの（本人の確認を
     まだ得ていない）。
   - `expert_confirmed_reconstruction`: `interviewer_inferred`の項目を後日本人へ見せ、
     「その理解で合っている」と確認を得たもの。
   Prompt強化・Human Model・Ranking Logic・Evidence discriminationのどこへ反映するかは、
   **`expert_stated`または`expert_confirmed_reconstruction`のみを根拠とし、確認前の
   `interviewer_inferred`のまま実装へ流さない**（誤解釈をそのままAIへ焼き付けるリスクを防ぐ）。
1. 上記構造化データを`docs/expert-ai-validation/session3/`配下へケースごとに保存（生の発言録
   もセットで残す、要約だけにしない）。
2. **反映候補の抽出は`judgment_basis`を限定しない**——`private_knowledge`だけに絞ると、
   「複数の公開情報を組み合わせる」「他ケースのパターンを当てはめる」「業界経験則」
   「期待値とのズレの感覚」といった、AIに教えれば再現できる可能性がある判断軸まで
   取りこぼす。`divergence_type`が実質的な項目（ai_missed_this_candidate等）を持つ
   Candidateは`judgment_basis`の値に関わらず全て抽出し、その上で以下のように扱いを分ける:
   - `current_material`/`logical_inference`: 既に渡した資料内で導出可能——AIのPrompt指示
     強化（推論の深さ・組み合わせ方の明示）で再現を狙える候補。
   - `prior_public_knowledge`/`cross_case_pattern`/`professional_experience`: 一般化できる
     ものは既存の`geo_knowledge_service`のCandidate/Review経路に乗せる——Session3の発言を
     直接Active Knowledgeへ書き込まない（既存原則「人間レビューを経るまで正式Knowledgeと
     して扱わない」を維持）。
   - `private_knowledge`: 非公開情報そのものであり、一般化・Knowledge化する場合は
     取扱い制限（M130等）の確認が必要——安易に反映しない。
   - `intuition_or_pattern_recognition`: 言語化しきれない感覚。無理に構造化せず、
     繰り返し出てくるようなら次のSession4候補として保留する。
3. `rejection_reason_category`の分布を見て、既存Operator（03_candidate_operators.json）や
   既存Guard（`geo_expert_quality_guard.py`）で表現できるものと、新しい概念が要るものを分ける。
   新しい概念が要ると分かった場合でも、この場では実装しない——次のPhase（Tier2以降）で
   改めて設計する。
4. `decision_trigger`/`counterfactual_condition`/`re_entry_condition`の3点セットを
   Candidateごとに並べると、「何を見たら順位が動くか」というRanking/Rejection Engineの
   ルールベースの原案になる。これをそのままコード化はしないが、次フェーズの設計インプットと
   して保存する。
5. Frame切替閾値の抽出結果は、`_KEY_POINT_NOTE`等の既存Promptノートへの追記候補として
   検討する（新しいReasoning ModuleやLLM callは追加しない、という既存方針を維持）。

## 7. 実施前チェックリスト

- [ ] Case5・Case1双方の資料一式（Source Pack本文・Case Context・Actor Intelligence Pack）を
      当時のRun時点のスナップショットのまま準備できているか（後日情報を混ぜない）
- [ ] AI出力（`hypothesis_register`・`candidate_key_points`・`main_scenario`・
      `rejected_candidates`）をStep Cまで江口さんに見せない運用を徹底できる体制か
- [ ] Case5で抽出したRanking/Rejection Logic・Frameの内容を、Case1のセッションが終わるまで
      一切提示しない運用を徹底できるか
- [ ] Frameレコード（5.1）をCandidate順位付けより先に固定する進行を徹底できるか
- [ ] 記録担当（構造化データ入力）とファシリテーター（質問進行）を分けられるか
- [ ] 「これはAIの答え合わせではない」ことを冒頭で明示できているか
- [ ] `decision_trigger`→`counterfactual_condition`→`re_entry_condition`の3連問いを、
      各棄却・順位変更のたびに漏らさず聞く体制（チェックリスト形式のメモ用紙等）を用意したか
