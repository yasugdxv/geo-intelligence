# Geopolitical Intelligence OS — Phase I-B0: Retrospective Proxy Evaluation 報告

日付: 2026-09-16
対象: Hungaryケース(1件)の実施結果、およびBC Strategyケースのas-of再現可能性監査。

## 0. 位置づけ(繰り返し明記)

**これはPhase I-Bの正式完了ではない。** 「AIがCQAS/Scenario/Designを生成し、それを
実専門家がレビューした」ケースではなく、「T0テーマだけを入力に現在のIOSで新規生成し、
生成完了後に初めて既存の江口さん実資料と突き合わせた」もの。`proxy / directional
evidence`として扱う。CQAS/Prompt/Expert AIロジックは一切変更していない。

## 1. Hungaryケース — 実施結果

**T0入力**(客観事実のみ、江口さんの独自フレーミング言語は使用せず):
> 2026年4月12日、ハンガリー議会選挙が実施された。野党ティサ党(中道右派)が得票率54%・
> 138議席(定数の3分の2超)を獲得する歴史的勝利を収め、与党フィデス党(オルバン首相、
> 16年間政権を維持)は得票率38%・55議席で大敗し、オルバン首相は即日敗北を宣言した。
> この政権交代の地政学的含意を分析してください。

生成結果: CQAS候補3件(全件Top表示)→ Rank1選択 → Analysis Design v1生成。
(`docs/geopolitical-ios-phase-i-b-evidence/phase_ib0_hungary_result.json`)

### 評価結果(江口さん実資料との突き合わせ、生成完了後に開示)

| 分類 | 論点 | 根拠 |
|---|---|---|
| **validated_dimension** | EU意思決定の目詰まり解消(オルバン拒否権消滅→対ロ制裁・ウクライナ支援の合意形成円滑化) | AI候補1(選択済)が同趣旨を「漸進的な統一性向上」として記述。ただし「拒否権」という具体的メカニズムへの言及はない |
| **generation_miss_candidate** | **Trump/MAGA連動**(オルバン退陣が欧州右派ポピュリストのTrump/MAGA陣営との関係再考の契機になる、という江口さん独自の最も価値ある追加視点) | AI候補3件・Design全てにTrump/MAGA/ヴァンスが一切登場しない。ランキング上の見落としではなく、**生成段階での完全な欠落** |
| **generation_miss_candidate** | **Putin個人への言及**(プーチンがEU最大の支持者を失う) | 「対ロシア政策の方向性」という大きな括りではAI候補に存在するが、Putin個人を名指ししたアクター単位の主張としては欠落 |
| **expert_added_dimension** | 「ポピュリズムの終焉ではない」という留保(反現職投票であり、イデオロギー的巻き返しではない。他国の右派・極右政権は継続中) | AI候補3件はいずれも「どこまで転換するか」の軸(漸進/現状維持/急進)で構成されており、この種の留保・ニュアンスは存在しない |
| **ambiguous** | 国内要因(経済停滞・汚職)を結果の原因として扱うか | AIのDesignは原因を断定せず`knowledge_gaps`として正直に「支持基盤の構成」を未知扱いしている(Evidence Disciplineとして適切だが、CQAS候補は元来forward-lookingであり、Eguchi資料の retrospective causal narrativeとの単純比較にならない) |

### Report Design評価(§7)

- **investigation_question**: AIは入力テーマをそのまま転記しており、江口さんの実践(即座に
  「なぜ重要か」を断定的に再フレーミングする冒頭段落を追加する)に相当する出力が無い。
- **key_actors**: AI=`[ティサ党指導部, EU中核]`(2件) vs 江口さん実資料=`[オルバン,
  マジャル, プーチン, Trump/MAGA/ヴァンス, EU]`(5件)。**AIのActor選定は明確に狭い**
  ——退陣するオルバン本人、プーチン、Trump/MAGA陣営が丸ごと欠落している。

## 2. BC Strategyケース — `pending_as_of_reconstruction`

### 除外理由(確定)

DB(`geo_documents`)に、BC StrategyケースのT0(2026-02-19)より後の日付(2026年5月・
8月付)の実際のイラン関連文書が複数存在することを実データで確認した。既知のバグ
(`build_source_pack`のUnscoped ILIKE検索に日付フィルタが無い、2026-09-16発見済み)
により、これらがT0生成時に無条件で混入するリスクが具体的に存在する。データセットの
integrityを優先し、今回は実行しなかった。

### as-of再現可能性の監査結果

| 確認事項 | 結果 |
|---|---|
| 2026-02-19以前のKnowledgeだけを取得可能か | **本番パイプラインでは不可**(`build_source_pack`本体に日付フィルタなし)。`geo_expert_engine.py`には別の限定的な仕組み(`_run_expert_context_v1_3_retrieval`のas_of_dateパラメータ)が存在するが、これは実験的なV1.3 Trajectory専用経路のみに適用され、主要なSource Pack構築(問題の発生源)には効かない(既知メモリで確認済み) |
| created_atではなく実際のpublication date/valid_from等でas-of filteringできるか | `geo_events`(`event_date`/`occurred_at`)、`geo_documents`(`published_at`)、`geo_knowledge_items`(`valid_from`/`valid_to`/`as_of_date`/`version`/`supersedes_id`)は、いずれも適切な実時刻フィールドを持っており、技術的には可能 |
| Actor/Media Intelligence/precedent等にも未来情報が混ざらないか | 未検証(範囲外の詳細確認が必要)。ただしこれらのPack Builderも全て`geo_db_client.SupabaseClient.select()`経由でDBアクセスする既存の一貫した設計パターンに従っているため、下記の対策で同時にカバーできる可能性が高い |
| current postureのような現在値がT0へ逆流しないか | **部分的に不可避のギャップあり**。`geo_entities`は`active_from`/`active_to`(存在期間)のみを持ち、属性(名称・ステータス等)自体の履歴バージョニングが無い——エンティティの「現在の」属性がT0時点でどうだったかは、変更履歴が無ければ再現不能。国・組織の識別情報は比較的安定なので実害は小さいと推定するが、完全な保証はできない |

### 安全な再現方法(提案、未実装)

**本番Expert AIコードは一切変更せず**、Evaluation Harness側で`geo_db_client.
SupabaseClient`と同じインターフェースを持つ**読み取り専用のDate-Filtering Proxy
Client**を作り、`geo_ios_expert_ai_adapter.py`以下の関数群へこのProxy Clientを
差し替えて渡す、という設計が技術的に成立する(`_relevant_rows_by_keywords`等、
全てのDB参照が`client.select(...)`という単一インターフェースを経由しているため)。
このProxyの`.select()`が、テーブルごとに適切な日付列で`as_of`以下という条件を
`params`へ注入してから本物のクライアントへ委譲する。geo_knowledge_itemsについては
単純な`as_of_date<=T0`ではなく、**`created_at<=T0`(その時点でシステムに存在したか)
を主たる判定軸にすべき**(`as_of_date`は内容が指す時期であり、システムへの投入時期
とは別軸のため)、という設計上の注意点も確認した。

これは新規コンポーネントの実装が必要なため、今回は設計のみ提示し実装は行っていない。
実装するかどうかはユーザー判断を仰ぐ。

## 3. 次のステップ

- I-B0はHungary 1件のみで一旦区切る(2件目としてBC Strategyを追加するかは、上記の
  Proxy Client実装可否のユーザー判断待ち)。
- 次にProspective Expert Validation(Phase I-B1)用のevaluation packageを作成する
  (Theme → CQAS全候補 → Top5 → User selection → Analysis Design → DraftをFreeze
  し、次回江口さんレビュー時にそのまま使える形式)。
- Phase I-B1が完了するまで、Expert AI/CQASの内容品質は引き続き`expert_unverified`
  のまま維持する。
