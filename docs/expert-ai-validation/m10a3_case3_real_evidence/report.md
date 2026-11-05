# M10-A3: Case3 Netanyahu — 実クロールCurrent Evidence + 最小限Media Profile追加

**STATUS: シナリオA（Current Evidence Layerの価値を確認・確定）+ シナリオB
（Media固有の分析はProfile追加後も不発、危険な逸脱=シナリオCは2回とも無し）**

Profile追加前後で判定は変わらず。ただし追加前は「Profile不在が原因」という
仮説が成立し得たが、追加後もmedia_source_contextが生成されなかったことで、
**原因はProfile不在ではなく、入力記事群自体が「同一事象を巡る鋭い対立」という
構造を持たないことにある可能性が高いと判明した**（詳細は末尾「追試」章）。

## 実施条件

コード・DBへの新規変更は一切行わず、既存の9件のIran関連実記事（CNBC/Bloomberg/
South China Morning Post/TIME/Gulf International Forum/NPR/Al Jazeera、いずれも
実際にクロールされた記事で本セッションが作成したものではない）がそのままSource
Packへ入る状態でCase3を1回実行した。既存5 Media Profile（Kyiv Independent/Meduza/
Novaya Gazeta Europe/ISW/Global Times）はいずれもIran特化ではないため、これは
自然に「Media Profileなし」条件を再現している。

## ユーザー確認項目7点の判定

| 項目 | 判定 | 根拠 |
|---|---|---|
| 9件の記事本文・document_idがSource Packに正しく入る | ✅ | Retrieval Snapshot・実行結果とも9件確認 |
| media_source_contextがどう扱われるか | **生成されず（null）** | Media/Information Reasoningが不発——後述の考察参照 |
| Iran側の実際の報道によってNetanyahu中心の元フレームが修正されるか | ✅（修正ではなく**適切な統合**） | Primary Key Pointは「ネタニヤフのICC逮捕状・国際司法プロセス不承認と国内政権維持」のまま維持しつつ、Iran/Hormuz要因を`situation_actor_assessment`の一項目として明示的に統合（後述） |
| 新しいKey Point/Alternativeとして出るか | ✅ | candidate_key_pointsに「ホルムズ海峡の封鎖・管理枠組みをめぐる米・イラン・オマーン・中国の力学」「IRGCによるイラン国家運営の実質的掌握」が明示的候補として出現（後者はTIME記事「Mojtaba Khamenei's Iran and the Politics of Succession」由来と考えられる）——ただしいずれも明確な理由でPrimaryからは除外 |
| H0-H4のどれかがCurrent Evidenceで強化・弱化・再定義される | ✅ | H1（政権維持・法的リスク回避が最上位目的）がretained、`situation_actor_assessment[2]`（対イラン・ホルムズ危機への姿勢）が実document_id「d163ef87-...」（CNBC記事）を引用して構造的制約として明示的に組み込まれている |
| Media Profileなしの媒体についてSource属性を勝手に断定していないか | ✅（安全側） | media_source_context自体が生成されなかったため、断定も一切発生していない |
| Confidence discipline維持 | ✅ | key_point/main_scenario/situation_actor_assessment[0]/key_actor_profiles[0]全て決定的補正でconfidence=低へcap、`removed_fabricated_ids=[]`（Evidence ID引用は全て正しいUUID） |

## 前回（v1/v2、Actor Intelligence不足時）との重要な違い

以前（Netanyahu/MBSのActor Intelligenceがnone_availableだった頃）は、Evidence
不足のあまりPrimary Key Point自体が「イランのホルムズ海峡封鎖能力」へ**丸ごと
差し替わって**しまい、依頼された問い（ネタニヤフの政策）から実質的に逸脱していた。

**今回はActor Intelligenceが正常（Netanyahu candidate profile、pattern×2件、
episode×2件）かつCurrent Evidence（Iran関連9件）も同時に存在する状態**で実行した
結果、Hormuz/Iran要因は**Primaryを乗っ取るのではなく、Netanyahu中心の分析の中に
構造的制約として正しく位置づけられた**（`situation_actor_assessment`の1項目、
`primary_binding_constraint: "米国の対イラン戦争方針。"`等）。これはCurrent
Evidence Layer単体の価値というより、**Actor IntelligenceとCurrent Evidenceの
両方が揃って初めて、依頼された問いの中心を保ったまま外部要因を正しく従属的に
統合できる**ことを示す、より説得力のある実例である。

## なぜmedia_source_contextが生成されなかったか（考察、未検証）

以下は考察であり、コード調査による確定的な結論ではない:

- 9件の記事はいずれも典型的な国際ニュースワイヤー的見出し・構成（CNBC/Bloomberg/
  NPR等）であり、`_MEDIA_REASONING_NOTE`が想定する「観測すべき編集判断の癖」
  （感情的framing、匿名情報源への依存等）が、Source Pack本文の抜粋（1600字予算）
  内で目立つ形では出現しなかった可能性がある。
- Media Intelligence Pack自体（8 pattern・1 profile=Global Times）がこれらの
  Outletと明確に結びつかず、LLMが「参照すべき先例が無い」と判断して
  `media_editorial_context`の生成自体を見送った可能性がある。
- M10-A2（Case2）は「同一事象を巡る複数Outletの明確な対立」という、Media
  Reasoningが機能しやすい強いシグナル（人道停戦 vs 部隊ローテーション）を
  意図的にFixtureへ組み込んでいた。今回の9件は事象こそ関連するが、各記事が
  「別々の日に別々の角度（後継問題/Hormuz/中国の抜け道等）を報じている」ため、
  「単一の事象について複数Outletが対立する記述をしている」という明確な比較軸が
  無かった可能性がある。

## 追試: 最小限Media Profile追加後の再実行（2026-09-02）

ユーザー指示によりAl Jazeera・South China Morning Post・Gulf International Forumの
3媒体（いずれも実際の9記事の実出典、`geo_source_registry`で既に確認済み）に
最小限のMedia Profile（`geo_knowledge_items`、knowledge_type=media_profile、
bias labelを避け「何を知りやすく・誰にアクセスしやすく・どのAudience向けに・
どのInstitutional Positionから報じるか」のみ記述）を追加し、Case3を再実行した。

**副次的に発見した実装ギャップ**: `geo_media_intelligence_service.
MEDIA_PACK_CHAR_BUDGET`が6000文字だったが、既存8 Media Pattern単体で実測
約7,500文字あり、Profileが1件しかPackに入らない状態だった（Profile 8件単体でも
約7,350文字）。Stop Gate「3 Profileのうち最低2つが対応」を検証可能にするため、
12000文字へ引き上げた（本番コード変更、Expert AIスコープ197件全Pass確認済み、
専用テストは元々存在せず新規テストの追加もしていない）。修正後、8 Pattern+8
Profile全てがPackへ収まることを確認。

**再実行結果**: Media Intelligence Packが実際に3新規Profileを含む形で
Promptへ入っていたことを、`_format_media_intelligence_block`の実出力（5,003字、
Al Jazeera/SCMP/Gulf International Forumの記述を含む）を直接再構築して確認した
上で、Case3を再実行した。それでも**`media_source_context`は再びnull**だった
（Stage2 completion_tokens=5,621と健全、token不足による打ち切りではない）。

これは今回のCase3固有の特徴による可能性が高いと考える（確定的な結論ではない）:
Case2 Fixtureは「同一の具体的事象（72時間停戦）について、公式発表『完全停止』
対独立系観測『砲撃継続』という鋭い対立」を意図的に作り込んでいた。一方、
今回のCase3実記事9件は、確認した限り相互に明確に矛盾する記述を持たず
（「イラン・オマーン間の交渉が進展」「米国船籍を禁止する合意内容」等はむしろ
時系列的に整合する報道であり、同一の具体的事実についての対立主張ではない）、
後継問題・Hormuz外交・中国の迂回策という**異なる側面を報じる複数記事**という
構造だった。**Media/Information Reasoning（`media_context`モジュール）は、
単なる複数Outlet・トピック関連性だけでは起動せず、同一の具体的事実についての
Outlet間の明確な対立という強いシグナルを必要としている可能性がある**——これは
Profile不在（今回否定された）でもConnection設計の欠陥（Priority Bで一度は
疑ったが、Case2で機能実証済みのため考えにくい）でもなく、**入力側の記事群の
構造的性質**に起因する可能性が高い。

## 結論・次の判断

危険な逸脱（Scenario C: Profile無しでの断定）は発生しなかった。Current
Evidence Layer自体の価値（Scenario A）は明確に確認できた。Media固有の分析は
不発（Scenario B）——ただしこれは「データはあるが设計が壊れている」ではなく、
「Media Profileが無い実際の媒体について、無理に踏み込まず沈黙した」という
Evidence Discipline的には健全な結果である。

ユーザー提示の方針に従い、次の選択肢は:
1. **このままCase2+Case3の2ケースでM10-Bへ進む**（Case3はCurrent Evidence Layerの
   価値のみを評価対象とし、Media固有の価値は「今回は無し」として記録する）。
2. **Case3の実際の7 Outlet（CNBC/Bloomberg/South China Morning Post/TIME/Gulf
   International Forum/NPR/Al Jazeera）のうち2〜3件に最小限のMedia Profileを
   追加し、Current Evidence + Media Profileの効果を再試験する**（これらは全て
   実在の著名メディアであり、実運用に近い形でのMedia Intelligence拡張になる）。

これらのOutletは本セッションが作成したものではなく、既存のクロールデータに
実在する（`geo_source_registry`に既に登録済み）ため、選択肢2は新規Fixture作成
ではなく既存データへのProfile追加のみで完結する。
