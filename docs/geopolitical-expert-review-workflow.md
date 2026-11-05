# 地政学ナレッジ＆インテリジェンスDB — Expert Review運用手順（Phase 2B-2）

対象読者: Expert Review機能を実際に使うReviewer（expert_reviewer/knowledge_steward）。
関連: `docs/geopolitical-internal-expert-learning.md`（データモデル・抽出ロジックの詳細）

## 1. 原則

Expert Reviewは「この修正内容は正しいか」を判定する場ではない。**「この専門家修正から、このReasoning Ruleを社内専門家AIの再利用Knowledgeとして残す価値があるか」を判断する場**である。したがって:

- `Approve = true`のような真偽承認の表現は使わない（Promote/Keep Case Only/Reject/Reinforceという操作名を使う）
- Promoteしても「事実として確定した」わけではない。あくまで「他案件でも参照する価値がある分析知として蓄積した」という意味
- Keep Case OnlyやRejectは「間違っている」という意味ではない。「この案件限りで十分」「一般化するには早い」という判断でよい

## 2. 操作手順（Case詳細画面 → Expert Reviewセクション）

### 2.1 Review Session開始

1. 対象CaseでAI Draft（Expert Engineの出力、または過去のレポート下書き）とExpert Final（実際に配布・提出した最終版）を用意する
2. `theme_id`・Draft本文・Final本文を入力し「Review Session開始」を押す
3. 内部的に両文書が`geo_documents`として保存される（表示・編集画面としてはCase画面のまま完結する）

### 2.2 Review Comment（任意）

修正した理由を明示的に残したい場合、「Review Comment」欄に記入して「Note追加」を押す。**この欄に書いた理由は、Delta Extraction時にLLMの推測より優先して反映される**。複数件追加してもよい。理由が特に無い変更については書かなくてよい（Delta Extraction自体はNoteが0件でも動く）。

### 2.3 Delta Extraction実行

「Delta Extraction実行」を押すと、AI DraftとExpert Finalの差分から、分析上意味のある変更（change）が抽出される。文言・語調のみの修正は候補として出てこない（それでよい、意図した挙動）。

各Changeには以下が表示される:
- `change_type`（judgment_change, stakeholder_added等）
- 「再利用可能な思考方法」（reusable_reasoning）か「本件・時点固有の判断」（case_specific_assessment）かの分類
- `reasoning_summary`（何がどう変わったかの要約）

同じSessionで再度「Delta Extraction実行」を押しても、既に抽出済みなら重複して作られない（安全に何度でも押してよい）。

### 2.4 各Changeへの操作

| 操作 | 意味 | 結果 |
|---|---|---|
| Promote | 他案件でも再利用する価値がある | Knowledge Item（`reasoning_rule`/`mechanism`/`precedent`/`case_finding`）がactive化され、次回以降のCaseで自動的に参照される |
| Reinforce | 既存のReasoning Ruleを、この案件でも再確認した | 新規Knowledge Itemは作らず、既存itemの確認回数（support_count）が+1される。既存itemのknowledge_item_idを入力する必要がある |
| Keep Case Only | この案件限りの判断で、一般化はしない | 何も昇格しない。Change自体の記録は残る |
| Reject | 再利用価値が無い | 何も昇格しない |

いずれの操作も一度実行すると取り消せない（disposition='pending'の間だけ操作可能。二重実行は自動的に防止される）。迷う場合はKeep Case Onlyを選び、後で別Sessionから改めてReinforceする方が安全。

## 3. Retrieval Governance（参照可否の管理、正誤判定ではない）

Knowledge Item・Claim・Sourceそれぞれに「AIが参照してよいか」（`available`/`restricted`/`deprecated`/`archived`）を設定する操作がある。これは**内容が正しいかどうかの判定ではない**。ライセンス上の理由、Sourceの信頼性が疑わしくなった、等の運用上の理由で使う。

- Source単位: `PATCH /api/geo/knowledge/sources/{source_id}/reference-availability` — このSource由来のClaim全体の既定方針を変える（Steward操作）
- Knowledge Item個別: `PATCH /api/geo/knowledge/items/{knowledge_item_id}/retrieval-status`
- Claim個別: `PATCH /api/geo/knowledge/claims/{claim_id}/retrieval-status`

Knowledge Itemの`status`（lifecycle: candidate/in_review/active等）と`retrieval_status`は別軸なので、片方を変えても他方には影響しない。例えば「内容は正しいが、このClaimは社外秘扱いにしたい」という場合は、`status`はそのままに`retrieval_status`だけ`restricted`にする。

## 4. よくある質問

**Q. Promoteしたら二度と直せないのか？**
A. 昇格したKnowledge Itemは通常の`review_knowledge_item`（既存の状態遷移）で`deprecated`/`archived`にできる。Retrieval Governance（`retrieval_status`個別変更）で参照だけ止めることもできる。

**Q. 同じ理由で何度もPromoteしてしまったら？**
A. 2回目以降は既にPromote済み（disposition='promoted'）のため操作が拒否される。似た内容の別Changeがある場合は、新規Promoteではなく既存Knowledge ItemへのReinforceを選ぶ。

**Q. Reviewer Noteに書いた理由がChangeのreasoning_summaryに反映されない**
A. LLMがそのNoteをどのChangeに対応する理由か判断できなかった可能性がある。Noteの文言をより具体的に（どの変更についての理由か分かるように）書くと改善する。
