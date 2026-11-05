# Phase M10-A追試: Case2 Putin Media Increment Test — 報告

## 実行条件

A = `docs/expert-ai-validation/original_after_v2/raw/original_after_v2_case2_putin_result.json`
（既存の正式After結果を再利用、再実行していない）。

B = 本テストで新規実行。Media Intelligence Pack snapshot（Azure呼び出し前に確認）:
**8件のmedia_pattern + 1件のmedia_profile（Global Times、中国の対外強硬シグナル
テスト媒体）**。期待していたKyiv Independent/Meduza/Novaya Gazeta Europe/ISW
（ウクライナ戦争に直接関連する4媒体）は、Pack構築時のChar Budget/Top-K制約により
今回のPackには含まれなかった（`media_pack_snapshot_precheck.json`参照）。
Stage2 completion_tokensは2,042（Case5で見られた異常な低下は再現せず、正常範囲）。

## 結果サマリ

| 項目 | A（Media無し） | B（Media有り） |
|---|---|---|
| Primary Key Point | NATOのウクライナ加盟・安全保障上の位置づけ | NATOの旧ソ連圏（特にウクライナ）への拡大をどこまで許容するかという安全保障上の一線 |
| control_type | distributed | mixed |
| confidence | 低 | 低 |
| evidence_class | current_indirect_evidence | structural_reference |
| **media_source_context** | null | **null（Media Intelligence ONでも生成されず、Case5と同一症状）** |
| audience_analysis | []（0件） | []（0件） |
| situation_actor_assessment | 3件 | **0件（AよりむしろMedia無しの時より減少）** |
| hypothesis_register | H0-H4の5件、パターンはAと同型 | H0-H4の5件、内容はAとほぼ同型（NATO拡大阻止フレームを維持） |
| calls / tokens | 2 / 96,275 | 2 / 95,480（ほぼ同水準） |

## ユーザー指定Stop Gate（Case2）での判定

| 通過条件 | 判定 |
|---|---|
| Mediaデータが実際にretrieveされる | △ Pack自体は注入されたが（precheck確認済み）、Stage1/2の出力側で`media_source_context`が一度も生成されない |
| Media固有のFrameまたはEvidence Conflictが最低1つ | **未達**（0件） |
| Primary/Alternative hypothesisのどれかがMediaによって強化・弱化・再定義される | **未達**（H0-H4の骨格・内容ともAとほぼ同一） |
| unsupported confidence = 0 | ○ 達成（confidenceはAと同じ低のまま） |
| Historical Media Pattern ≠ Current Evidenceが維持 | ○ 該当なし（Media由来Evidenceが生成されていないため混同しようがない） |
| CallBudget ≤3 | ○ 達成（2/2） |
| regression維持 | ○ Quality Guard failed_countはAと同水準 |

**総合判定: Stop Gate未達。** Case5に続き2ケース目でも同じ症状
（`media_source_context`が一貫してnull）が再現した。

## 解釈: データ不足ではなく「接続」の問題である可能性が高い

Case2では、Case5と異なり**トピック的に妥当なMedia Profile（Global Times、
中露関係の文脈で意味を持ちうる）と8件の一般的な行動パターンが実際に注入された**
にもかかわらず、結果は変わらなかった。これはユーザーが事前に示した分岐点に
該当する:

> 「Media Contextは十分入ったのにReasoningが全く変わらないなら、そこで止めます。
> その場合はデータ不足ではなく、Retrieval → Media Interpretation → Hypothesis
> Competitionの接続設計が弱い可能性が高いです。」

**最も有力な仮説**: `media_context`モジュールが機能するには、Media Intelligence
Pack（Behavioral Pattern・Outlet Profileという「事前知識」）だけでなく、
**Source Pack内に実際に「解釈対象となる当該事案の報道記事」が存在する必要がある**。
今回のCase2のSource Pack（document_ids 10件、event_ids 15件）の中身は本調査では
未確認だが、Case2のinitial_anomaly（Aの結果）が繰り返し「提供されたEvent群は
台湾海峡・中国海警・米沿岸警備隊に関するものであり、ウクライナ戦争やプーチンに
直接関係する一次Evidenceが含まれていない」と述べていることから、**この
Foundation/Case Contextの実データ自体がウクライナ戦争関連の記事を欠いている
可能性が高い**——Media Intelligence Packは「型」を提供するだけで、「型を
当てはめる具体的な記事」がSource Pack側に無ければ、`media_context`は
生成しようがない。

**もう一点の懸念**: `situation_actor_assessment`がA(3件)→B(0件)へ**減少**して
いる。Media Intelligence Pack追加によりPrompt全体が長くなった影響で、Stage2が
他モジュールへ割く出力を圧迫した可能性がある（Stage2 completion_tokens自体は
A:3,824相当→B:2,042と、Case2のA実行時点の値と比べても減少傾向）。これは
Media Intelligence導入が既存モジュールの出力を意図せず圧迫するという、
別の角度のリスクとして記録しておく。

## 結論・推奨

Case5・Case2の2ケース連続でStop Gate未達となったため、**M10-B（5ケース横断）
には進まない**。ユーザーが示した通り、次は以下いずれかの診断を先に行うべき:

1. **Source Pack実データの確認**: Case2のdocument_ids 10件が実際にウクライナ・
   プーチン関連の記事を含むか、あるいはCase5同様に無関係な記事（台湾海峡等）に
   終始しているかを直接確認する。
2. **`media_context`モジュールのPrompt指示の確認**: `geo_expert_schema.py`内で
   このモジュールがMedia Intelligence Pack単独でも(Source Pack記事が薄くても)
   何らかの分析を試みるよう指示されているか、それとも記事の存在を前提とした
   設計になっているかを確認する。
3. **Stage2の出力配分**: Media Intelligence Pack追加がsituation_actor_assessment等
   既存モジュールの出力を圧迫していないか、token配分の実態を確認する。

これらはコード変更を伴わない調査段階であり、Freeze条件を破らずに実施できる。
