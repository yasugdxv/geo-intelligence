# Session3 進行台本＋質問票＋記録シート（そのまま持ち込み用）

作成日: 2026-09-06
設計の根拠・詳細な理由付けは`docs/expert-ai-eguchi-session3-design.md`を参照。本文書は
当日そのまま使う実務用（台本を読み上げる・質問票を見ながら進める・記録シートに書き込む）。

**当日絶対に守る2点（design.mdより再掲）**
- Blind段階ではAIの候補・表現・Operator名を一切見せない。
- Reveal後も一致部分は長く確認せず、差分・棄却・順位変更の理由に時間を使う。

---

# パート1: 進行台本

## 1-0. オリエンテーション（読み上げ用、そのまま口頭で）

> 「今日お聞きしたいのは、局面の読み方全般ではありません。AIはすでに、目的や達成経路を
> Actorに沿って組み立てたり、相手の反応を予想したりするところまではできるようになりました。
> 今日聞きたいのはその先——**複数の解釈候補がある時に、江口さんが何を根拠に順位をつけ、
> 何を見て捨て、どこで『これは違う』と判断するか**です。AIの答え合わせをする場ではありません。
> まず資料だけをお渡しして、江口さんご自身の見立てを伺い、そのあとAIが実際に出した内容を
> お見せして、違いがあるところだけ深掘りさせてください。」

## 1-1. Case5 Kim（前半、目安70〜90分）

1. **資料を渡す**（Source Pack本文・Case Context・Actor Intelligence Pack。AI出力は同封しない）
   > 「まずこの資料だけを読んで、思ったことを話しながら進めてください。」
2. **Frameを先に確定させる**（候補の話に進む前に、必ずここで一度止める）
   > 「候補の話に入る前に一つだけ——この局面は、一言でいうと何の話だと思いますか。」
   → 記録シートFrame欄へ記入。
3. **候補・順位・棄却・Disconfirming Signalを聞く**（質問票2章を使用）
4. **AI出力を開示**（`hypothesis_register`・`candidate_key_points`・`main_scenario`・
   `rejected_candidates`・`selection_rationale`を印刷 or 画面提示）
   > 「ここからはAIが実際に出した内容です。正解として見せているわけではありません。
   > 違うところだけ、一緒に見ていきたいです。」
5. **Frame一致/不一致をまず判定**
   > 「先ほど江口さんが仰っていた局面の捉え方と、AIのこれ、同じだと思いますか、違うと
   > 思いますか。」
6. **差分だけをRejecting Cue連鎖で深掘り**（質問票3章を使用、本体）
7. **Frame切替閾値の追加質問**（質問票4章を使用）
8. **まとめ**
   > 「ありがとうございました。次はTrump-Xiの件で、同じ聞き方を短く繰り返させてください。」

## 1-2. Case1 Trump-Xi（後半、目安20〜30分、深掘りしない）

**Case5で伺った内容・AIの出力は一切ここでは出さない。**

1. 資料を渡す（同様にAI出力は同封しない）
2. Frameを先に確定させる（1-1の②と同じ聞き方）
3. 候補・順位・棄却を手短に聞く（質問票2章、簡略版でよい）
4. AI出力を開示
5. Frame一致/不一致を判定、差分があれば手短にRejecting Cueの1段目（`decision_trigger`）
   だけ聞く（`counterfactual_condition`/`re_entry_condition`まで深追いしなくてよい）
6. クロージング
   > 「今日はありがとうございました。今回聞かせていただいた判断の仕方は、今後のAIの
   > 設計に反映を検討させていただきます。反映する前には、こちらでの解釈が正しいか、
   > 改めて確認のご連絡をさせていただくことがあります。」

---

# パート2: 質問票

## 2章: Blind時の独立判断（Case5・Case1共通）

1. この局面は、一言でいうと何の話だと思いますか。（Frame、単独で先に確定）
2. 考えられる解釈・シナリオ候補を、思いつく限り挙げてください。
3. その中でどれが本命ですか。次点は何ですか。
4. 明らかに違うと思う、あるいは弱いと思う候補はありますか。それはどれですか。
5. 何が観測されたら、今の見立て（本命）が間違っていると分かりますか。

## 3章: Reveal後の差分深掘り（Rejecting Cue連鎖、本体）

まず:
- 「先ほどの局面の捉え方と、AIのこれ、同じですか、違いますか。」（Frame一致判定）

Frameが同じ場合、候補・順位の差分ごとに以下を**順番通りに**聞く:

1. 「AIはこの候補を『◯◯』としていますが、江口さんは『△△』でした。どちらかの候補について
   聞かせてください——**どの一点を見た時点で、この読みは違うと思いましたか。**」
   → 記録シート`decision_trigger`
2. 「なぜその一点が決め手になるのですか。」
   → `decision_trigger_reason`
3. 「その一点がなかったとしたら、この仮説を残しますか。」
   → `counterfactual_condition`
4. 「では逆に、何が観測されたら、この仮説をもう一度1位に戻しますか。」
   → `re_entry_condition`
5. 「今の判断の根拠は、お渡しした資料のどこかに書いてありましたか。それとも——」
   （選択肢を提示しながら聞く、記録シートのチェックボックスに対応）
   - 「資料にそのまま書いてありましたか」→ current_material
   - 「前から知っていた公開の情報を思い出しましたか」→ prior_public_knowledge
   - 「複数の情報を組み合わせて意味づけしましたか」→ logical_inference
   - 「他の地政学のケースで見たパターンを当てはめましたか」→ cross_case_pattern
   - 「業界・実務の経験則ですか」→ professional_experience
   - 「『普通ならこうなる』という感覚のズレですか」→ intuition_or_pattern_recognition
   - 「一般には公開されていない情報をご存知だからですか」→ private_knowledge

Frameが違う場合:
- 「AIは『◯◯』という話として捉えていますが、江口さんは『△△』という話だと感じています。
  何を見て、そもそもこの局面をそう捉えましたか。」
- 「AIの捉え方（◯◯）で読むと、何が説明できなくなりますか。」

## 4章: Frame切替閾値（Case5のみ、時間があればCase1でも）

1. 「今回のFrame・本命候補が仮に正しいとして、次にどんな一つの事実が出てきたら、それを
   疑い始めますか。」
2. 「過去の実務で、最初のFrame・見立てを覆した経験はありますか。その時、何が決め手でしたか。」
   （一般論ではなく具体的な実例を求める）
3. （上の回答に「〜のパターンは見たことがある」等が出た場合）「それは今回の資料のどこかに
   書かれている情報ですか。」

---

# パート3: 記録シート（ケースごとに1部ずつ使用）

## 3-0. ヘッダー

```text
ケース名: [ Case5 Kim | Case1 Trump-Xi ]
実施日:
所要時間:
記録担当:
```

## 3-1. Frameレコード

| 項目 | 内容 |
|---|---|
| 江口さんのFrame（生の発言） | |
| AIのFrame（`primary_key_point`/`one_line_assessment`、Reveal後に転記） | |
| 一致判定（same / different / partial） | |
| 違いの要約（different/partialの場合） | |

## 3-2. Candidateレコード（差分があったものだけ、必要な行数を複製して使用）

| 項目 | 内容 |
|---|---|
| Candidate（何についてか） | |
| 出所（eguchi / ai） | |
| 江口さんの順位・status（retained/rejected/unresolved） | |
| AI側のstatus | |
| **decision_trigger**（どの一点を見て判断したか） | |
| decision_trigger_reason（なぜ決め手か） | |
| counterfactual_condition（その一点が無ければ残すか） | |
| re_entry_condition（何が観測されたら1位に戻るか） | |
| judgment_basis（下記から1つ以上に◯） | □current_material □prior_public_knowledge □cross_case_pattern □professional_experience □private_knowledge □logical_inference □intuition_or_pattern_recognition □other |
| judgment_basis_note（具体的な内容） | |
| divergence_type | □ai_missed_this_candidate □ai_kept_what_eguchi_rejected □ai_rejected_what_eguchi_kept □ranking_order_differs □no_divergence |
| provenance_status（記入時点でチェック） | □expert_stated（本人の言葉そのまま） □interviewer_inferred（その場での解釈・要約） |

## 3-3. Frame切替閾値

| 項目 | 内容 |
|---|---|
| Frame切替のトリガー（生の発言） | |
| それは資料内の情報か（true/false/partial） | |
| 過去実務での具体例 | |

---

# パート4: Session3後・事後確認の進め方（実施担当向けメモ）

1. 記録シートの`provenance_status=interviewer_inferred`の項目だけを抜き出す。
2. 抜き出した項目を要約し、江口さん本人へ「このような理解で合っていますか」と確認を取る
   （メール・短時間の再確認ミーティング等、形式は問わない）。
3. 確認が取れた項目は`provenance_status=expert_confirmed_reconstruction`へ更新する。
4. `expert_stated`または`expert_confirmed_reconstruction`のみを、design.md 6章の反映方針
   （judgment_basis別の仕分け）にかける。`interviewer_inferred`のまま実装へ流さない。
