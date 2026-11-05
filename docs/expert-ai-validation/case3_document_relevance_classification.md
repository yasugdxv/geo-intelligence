# Case3 Netanyahu — Retrieved Document Relevance Classification（Azure呼び出し無し）

## 目的

Case3で`media_source_context`が（Profile追加前後とも）nullだった理由が、
「Media Activationの発火条件の欠陥」ではなく「query主題（ネタニヤフ）とdocument
主題（イラン）の不一致」であるという仮説を、実際に取得された9文書を1件ずつ読んで
検証する。Azure呼び出しは行わない。

Decision Point: 「ネタニヤフの現在のIran・Gaza政策——目的・制約・意思決定力学・
今後の方向性」

## 分類基準

- **Direct**: Netanyahu/Israelの意思決定・Iran/Gaza政策そのものを扱う記事
- **Structural**: Netanyahuの意思決定環境を規定する構造要因（Iran内部政治・
  Hormuz情勢・US-Iran関係等）を扱うが、Netanyahu/Israelの決定そのものではない
- **Peripheral**: テーマ周辺だが、当該Decision Pointへの規定力が弱い

## 分類結果

| document_id | Outlet | 内容要旨 | 分類 | 理由 |
|---|---|---|---|---|
| d163ef87 | CNBC | Trump「対話せず、Hormuz封鎖継続」表明 | Structural | 米国の対イラン方針はイスラエルの同盟依存・安全保障環境を左右するが、ネタニヤフ自身の決定ではない |
| 6b3e15d8 | Bloomberg | イラン、オマーンとの交渉で米国船籍恒久禁止を要求 | Structural | イラン-オマーン間の交渉、イスラエルへの直接言及なし |
| 3d3c3c09 | SCMP | 中国・王毅がイランに航行の自由を要請 | **Peripheral** | 中国-イラン間の第三国外交、ネタニヤフの意思決定環境への直接的規定力は弱い |
| a9208a53 | NPR | 中国が封鎖下で原油調達を迂回・備蓄調整 | **Peripheral** | 世界原油市場・イラン経済の耐久力に関わるが、当該Decision Pointからは距離がある |
| f1a5db81 | TIME | ハメネイ師暗殺、モジュタバが後継指導者に選出 | Structural | イラン最高指導部の交代・空白は、イスラエルの対イラン脅威評価・抑止計算に直結する重要な構造要因 |
| fc30753d | TIME | IRGCによる「静かな構造的クーデター」、実質的権力掌握 | Structural | イラン側の実質的な意思決定構造そのものを説明する重要な構造要因 |
| 0140af1e | Gulf International Forum | モジュタバのIRGC内人脈分析 | Structural | 同上、イラン後継体制の性質を説明 |
| c8278119 | Al Jazeera | MoU後、イランが商船攻撃を再開（カタールLNG船含む） | Structural | 地域全体の安全保障環境・緊張水準の指標 |
| e522f821 | NPR | イラン・オマーン合意が米・**イスラエル**船籍を禁止と主張、米国は拒否 | Structural | 9件中最もIsrael近接（名指しで対象）だが、報じているのは「イランの行動」であり「ネタニヤフの決定」そのものではないためStructural止まり |

**集計**: Direct = **0** / Structural = **7** / Peripheral = **2**

## 実際のReasoning出力との整合性確認

M10-A3（Profile追加前後とも）の`situation_actor_assessment`は、これらの文書を
「対イラン・ホルムズ危機への姿勢」という1項目にまとめ、
`primary_binding_constraint: "米国の対イラン戦争方針。"`、
`issue_component_description: "米・イラン戦争・ホルムズ封鎖に対する外交・情報戦・
安全保障ポジション"`として、**構造的制約として正しく従属統合**していた。これは
本分類（Direct=0、Structural多数）と完全に整合する——モデルは9文書をNetanyahu
自身についての一次証拠として扱わず、正しく背景制約として位置づけていた。

## 結論

「query主題（ネタニヤフ）とdocument主題（イラン）の不一致」仮説は確定した。
`media_source_context=null`は、Media Activationの発火条件の欠陥ではなく、
**そもそもこのケースにMedia Reasoningが直接介入すべき対象（Direct文書）が
1件も存在しなかった**ことの正しい帰結である。

Media Activation Test（Condition B、`docs/expert-ai-validation/
media_activation_test_b/`）で証明した通り、Direct文書（同一Decision Pointに
ついて複数Outletが異なるFramingで報じている状態）が与えられれば、Media
Reasoningは正しく起動する。今回のCase3にはそのDirect文書が無かっただけである。

## Evaluability分類の更新（4区分）

| Case | 分類 |
|---|---|
| Case1 (Trump-Xi) | `MEDIA_NOT_EVALUABLE_DATA_GAP` |
| Case2 (Putin, Fixture) | `MEDIA_EVALUABLE_DIRECT` |
| Case3 (Netanyahu, 実データ) | `CURRENT_EVIDENCE_EVALUABLE` + `MEDIA_EVALUABLE_STRUCTURAL_ONLY` |
| Case4 (MBS) | `MEDIA_NOT_EVALUABLE_DATA_GAP` |
| Case5 (Kim) | `MEDIA_NOT_EVALUABLE_DATA_GAP` |

Media Activation設計（Prompt/Thinking Flow）はここでFreeze。M10-Bへ進む条件は
満たされた。Case2とCase3は同じ意味で評価しない——Case2はMedia Intelligenceの
直接Increment評価対象、Case3はCurrent Evidence + Structural Media Contextの
Increment評価対象（media_source_context=nullはFail扱いにしない）。
