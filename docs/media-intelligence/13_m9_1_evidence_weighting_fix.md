# M9.1 — Evidence Weighting Fix

D限定で発生した過信(`anonymous_sourced_hypothesis_confidence_capped`違反)への対応。
大規模なReasoning修正はせず、指示された4点を実装した。

## 1. 実装内容

### ① Evidenceの役割の区別(Prompt指示、Schema変更なし)
`geo_expert_schema.py`の`_ACTOR_MEDIA_CROSS_REFERENCE_NOTE`へ追記。historical_pattern
（Actor/Media双方の過去傾向）/current claim（今回の匿名情報源等による主張）/current
independent corroboration（別系統の実名情報源・公式行動・実際の行動・物的証拠・独立
文書等）の3つを明示的に区別し、**「Actor PatternとMedia Patternが同じ方向を指している
という理由だけでconfidenceを高くしない」ことを明文で禁止**した(ユーザー指摘の
「2つのPriorの一致は2つのCurrent Evidenceの一致ではない」という論点をそのまま反映)。

### ②③ Anonymous hypothesisのConfidenceルール強化・「一致したから確信度UP」の禁止
既存`corroboration_basis`/`corroboration_target`契約(confidence=高には
`corroboration_target=hypothesis_itself`が必須)をそのまま活用。新しいEnum値は
追加していない——既存契約がすでに「出来事の確認」と「意図の確認」を区別しており、
ユーザーが指摘した区別と同じ構造を持っていたため、Prompt強調のみで対応した。

### ④ 決定的Confidence Cap + confidence_adjustment_reason
`geo_expert_quality_guard.apply_confidence_weighting_fix(response)`を新設
(`scrub_fabricated_evidence_ids`と同じ「LLMを再呼び出さずresponseを直接書き換える」
方式、追加Azure Callなし)。`confidence=高`かつ既存Guard契約に違反するhypothesisを
その場で`confidence=中`へ引き下げ、`confidence_adjustment_reason`(ユーザー指定の
英文をそのまま採用)を記録する。`run_expert_request_staged`のマージ処理直後
(`scrub_fabricated_evidence_ids`の次)に組み込んだ。Schema
(`schemas/geo_expert_ai_response.schema.json`)へ`confidence_adjustment_reason`
(optional、LLMが書く必要はない)を追加。

合成データによるユニットテストで、意図通りの動作(違反行のみ引き下げ、他行は無変更)を
確認済み。

## 2. D-before / D-after 比較(同一Case・同一Prompt条件、Actor ON+Media ON)

| | before | after |
|---|---|---|
| total_tokens | 99,500 | 96,882(減少、増加なし) |
| calls | 2 | 2(CallBudget維持) |
| `anonymous_sourced_hypothesis_confidence_capped` | **fail** | **pass** |
| `media_hypothesis_evidence_grounded` | fail | pass |
| primary_key_point | 支援を交渉カードとして一時停止し得る構造 | 支援停止というレバレッジの行使(同趣旨、やや簡潔) |
| control_type | actor_controlled | actor_controlled(維持) |
| Putin alternative_interpretation | 「評判コストを損なう可能性」(status=weak) | 「西側結束は想定より強く、誇張して国内向け利用の可能性」(status=**plausible**、salience/confidenceも上昇) |

## 3. ユーザー指定6項目のチェック結果

1. **匿名Source仮説のConfidenceが適切に下がったか**: △**部分確認**。今回の再実行では
   `media_editorial_context`自体が空(モデルがMedia Hypothesisを1件も生成しなかった)
   だったため、実データ上でCapが発火する場面には遭遇しなかった(`confidence_
   adjustments: []`)。**Capの正常動作自体は独立した合成データテストで確認済み**
   だが、実運用データでの発火は今回未観測——これは正直に限界として報告する。
2. **Putinの評判コスト等の有益な統合洞察は残ったか**: ✓確認。残っただけでなく、
   confidence(低→中)・salience(medium→high)・alternative_interpretation_status
   (weak→plausible)がいずれも向上し、より具体的な代替解釈へ発展した。
3. **Key Point/Scenarioの改善を壊していないか**: ✓確認。同じテーマ(支援の
   レバレッジ化)・同じcontrol_type(actor_controlled)を維持。Scenarioの
   supporting_evidenceも他Case実例参照を維持。
4. **CallBudget 2のままか**: ✓確認(2 calls)。
5. **Token増加していないか**: ✓確認(99,500→96,882、減少)。
6. **Quality Guard全passか**: △**部分確認**。対象だった`anonymous_sourced_
   hypothesis_confidence_capped`は解消。ただし`fact_claims_have_source_ref`/
   `stakeholders_grounded_in_source_pack`は**全条件(A/B/C/D/D-after共通)で
   一貫して発生している既知の制約**(本テストがtheme_id=None・実Documentコーパス
   不在という簡易構成であることに起因、M9.1のスコープ外)であり、引き続き残っている。

## 4. 未解決の限界(正直な報告)

- 今回の再実行でMedia Hypothesis自体が0件になった(before=3件)。これがPrompt強化に
  よる過度な慎重化なのか、単なるLLMの実行毎のばらつきなのかは、この1回の再実行だけ
  では判別できない。ユーザー指示通り「Dのみ再実行」に留めたため、複数回再実行しての
  分散確認は行っていない。
- 決定的Confidence Capが実データ上で実際に発火する場面を、今回はまだ観測できていない
  （合成データでの動作確認のみ）。将来、実際に発火した実例が得られた場合は、
  Expert Review Packの補足教材としてそのまま活用できる（ユーザー指摘の通り、この
  「統合すると過信した」という失敗事例自体は捨てずに残す）。

## 5. 結論

対象だった過信の実例(`anonymous_sourced_hypothesis_confidence_capped`)は解消された。
Token/CallBudgetの悪化なし、有益な統合洞察(Putin評価)は維持・向上。M9.1はここで
Close扱いとし、次にExpert Review Packの作成へ進む。
