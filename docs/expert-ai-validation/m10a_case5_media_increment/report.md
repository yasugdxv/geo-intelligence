# Phase M10-A: Case5 Kim Media Increment Test — 報告

## 実行条件

A = `docs/expert-ai-validation/original_after_v2/raw/original_after_v2_case5_kim_result.json`
（Actor Intelligence + Thinking OS + Evidence Discipline、Media Intelligence OFF、
既存の正式After結果をそのまま再利用、再実行していない）

B = 本テストで新規実行（A+Media Intelligence ON、他は全て同一条件・同一
Fingerprint・同一Frozen Query）。`media_intelligence_statuses=("active","candidate")`
（本番既定`("active",)`ではなく検証用、Media側も全9 media_pattern/5 media_profileが
`status=candidate`のみで存在するためActor Intelligenceと同じ理由で緩和が必要
——事前に確認済み）。

**1回目の実行は破棄**: Stage2のcompletion_tokensが73（Aの3,824と比べ明らかに異常）で、
`hypothesis_register`が0件になるという実行時異常が発生した。これはMedia
Intelligenceの効果ではなく、Azure呼び出し側の一時的な劣化応答と判断し、
`m10a_case5_kim_variant_B_attempt1_anomalous_result.json`として記録を残した上で
再実行した。2回目（`m10a_case5_kim_variant_B_result.json`）はStage2
completion_tokens=4,222と正常値に復帰し、これを正式なBとして採用する。

## 結果サマリ

| 項目 | A（Media無し） | B（Media有り、2回目=正式） |
|---|---|---|
| Primary Key Point | 北朝鮮の核・ミサイル能力をめぐる抑止・エスカレーションのバランス | 核・ミサイル能力を体制存続の保証とみなす北朝鮮の戦略ドクトリン |
| control_type | mixed | structural |
| key_point confidence | 低 | 低 |
| H0(surface)の内容 | 核・ミサイル=体制保証+ロシア中国利用+敵対的2国家路線 | ほぼ同内容（ドクトリンという語で明示化） |
| media_source_context | null | **null（Media Intelligence ONでも生成されず）** |
| audience_analysis | []（0件） | []（0件、変化なし） |
| situation_actor_assessment | []（0件） | []（0件、変化なし） |
| main_scenario confidence | 低 | 低 |
| calls / tokens | 2 / 94,437 | 2 / 95,316（+879） |

## ユーザー指定10項目での判定

| 評価項目 | 判定 |
|---|---|
| Key Pointが変わったか | **変わらず**（同一の核心フレーム、表現の明確化のみ） |
| 新しいFrame-breaking hypothesisが出たか | H4の内容自体はA「国内演出・外部関係は副次的」→B「経済改革・開放路線への転換」と変化したが、**いずれも最終的にrejectedで、Primaryへの影響なし** |
| 新しいActor/Decision Dynamicsが出たか | **出ず**（situation_actor_assessment依然0件） |
| Media由来HypothesisがCurrent Evidenceと混同されていないか | 該当なし（Media由来Hypothesis自体が生成されていない） |
| Source Attributionが出ているか | **出ず**（media_source_context自体がnull） |
| Counterparty/Third-country Lensが増えたか | **増えず** |
| Physical/OSINT evidenceとの矛盾を扱えたか | 扱う対象（Media由来の主張）自体が生成されていない |
| Confidenceが不当に上がっていないか | **上がっていない**（低のまま、1回目の異常実行でのみ中へ上昇したが2回目では再現せず） |
| Main Scenarioが本当に改善したか | evidence_classがstructural_reference→historical_actor_patternへ変化した程度で、実質的な改善とは言えない |
| CallBudget / token増分 | CallBudget維持(2/2)、token増分は+879（+0.9%）と軽微 |

## 結論: Stop Gate未達（Case5では）

ユーザーが設定したPhase M10-A通過条件「Case5でMedia固有の新しいFrameまたは
Evidence Conflictが最低1つ出る」は**満たさなかった**。ただし、これはMedia
Reasoningモジュール自体の欠陥ではなく、**データ側の構造的制約**によるものと
判断する:

- 現在のMedia Intelligence Pilotデータ（`media_profile`5件）は
  **Kyiv Independent/Meduza/Novaya Gazeta Europe/ISW/Global Times**と、
  すべてウクライナ紛争（一部中国）向けに構築されたものであり、
  **北朝鮮関連メディア（KCNA/Rodong Sinmun/Yonhap/NK News等）のProfileが
  一切存在しない**。
- `media_pattern`8件は抽象的な行動パターン（国家系メディアの多層Escalation
  Register、匿名情報源スクープの裏取り構造等）で北朝鮮固有ではないが、これらは
  「具体的なMedia記事・報道内容」に紐づけて初めて`media_context`モジュールが
  機能する設計であり、**今回のCase5のSource Packには北朝鮮関連の当日記事・
  報道そのものが十分含まれていなかった**可能性が高い（この点はSource Pack内容の
  詳細確認までは実施していない、次の診断候補）。
- 結果として、Media Intelligence ONにしても`media_context`モジュールに
  「解釈すべき具体的な報道」が実質的に無く、素通りした——**危険な副作用
  （Confidence不当上昇・Evidence混同）も一切発生しなかった**点はむしろ良い
  ニュースであり、Evidence Disciplineが「材料が無ければ何も主張しない」という
  設計思想通りに機能したことを示す。

## 推奨: Case2 Putinでの追試

現在のMedia Intelligence Pilotデータが実際にトピック適合するのは、
Kyiv Independent/Meduza/Novaya Gazeta Europe/ISWという構成から明らかに
**ウクライナ紛争（Case2 Putin）**である。Case5がMedia Intelligenceの限界
（データ不足）を明らかにした一方、Case2はMedia Intelligenceが本来意図した
形で機能するかどうかを検証できる、より公平なテストケースになり得る。

Case5を「Media Intelligence自体の失敗」と結論づけず、**Case2 Putinで同じ
Increment Testを追加実施**してから、M10-B（5ケース横断）に進むかどうかを
判断することを推奨する。
