# Tier1 Reasoning Upgrade — OFF/ON比較（1ケース、STATUS: INCONCLUSIVE — 重大な交絡を発見）

> **Provenance note**: このRunはevaluation run executed before persist=False was introduced;
> DB side effects (geo_cases.final_outputの上書き含む) were later identified and restored
> (M9 Pilotケースの`final_output`は実行前バックアップから復元済み)。詳細:
> `docs/expert-ai-validation/eguchi_db_write_incident_2026-09-06/`。本レポートの比較結果
> （INCONCLUSIVE判定）自体には影響しない。

作成日: 2026-09-06
対象: Eguchi Expert AI Final v1.0 Tier1（DirectionalPerception / perceived_path / event_role /
Candidate Generation Operators / Actor-Analyst Response Separation）

## 結論（先に書く）

**このRun対では、Tier1の効果を判定できない。** ON側の応答が、実際に尋ねた質問（2025年2月
Oval Officeでのゼレンスキー・トランプ会談決裂）から大きく逸脱し、Source Pack内の無関係な
Fixture文書（ロシア国防省の72時間人道的停戦・ドネツク州部隊ローテーション、以前のCase2 Putin
検証用に作成されたもの）の内容をほぼそのまま分析対象にしてしまった。OFF側は同じ入力（同一
document_ids、同一query_text、同一Source Pack）を受け取りながら正しく質問に回答しており、
入力（Source Pack/Case Context）はOFF/ONで完全に同一であることを確認済み。したがって、この
逸脱はTier1コード差そのものによる決定的な結果ではなく、**LLMのラン間分散（temperature=0.2）が、
出典と質問が食い違う紛らわしいプロンプトに対して不安定に反応した結果である可能性が高い**が、
Tier1が追加したPromptの分量（Candidate Generation Operators等）が、この不安定さを助長した
可能性も否定できない。n=1では切り分けられない。

## 実行条件（実施した内容の記録）

- Case: `M9 Pilot: Zelenskyy Oval Office Clash`（case_id: `4d6e7894-cd39-48d2-8f9e-1efbcf74b294`）。
  既存DBに実在するケースを使用、新規Fixtureは投入していない。
- Theme: `dae0a454-8fe3-4518-b3d7-ca4821eff078`（us_ukraine_alliance_relations）。
- query_text: このCaseの`original_question`をそのまま使用（2025年2月28日のOval Office会談決裂〜
  ジェッダ協議正常化までの経緯を問う、実史実に基づく質問）。
- profile=`deep`, mode=`case_analysis`。Trump/Putin/Zelenskyyの3者とも、このCaseのissueへ
  実際にentity_issues紐付け済みで、3者ともactive Actor Intelligence Profile（worldview/
  persistent_preference/red_lines）を保有していることを事前にDBで確認した。
- OFF = Tier1変更前のコード（`git stash`で一時的に該当12ファイルのみ復元、api_server.py等の
  無関係な既存作業ファイルには一切触れていない）。ON = Tier1実装後の現行コード。
- 実行方法: `geo_expert_engine.run_expert_request_staged`を直接呼び出し（**DBへの保存は行われない
  設計**——既存の凍結Baseline・active Knowledgeは無変更）。
- External Research: 環境変数`GEO_EXPERT_EXTERNAL_RESEARCH_ENABLED`は未設定のため既定で無効。
  両Runとも`external_research_enabled=False`をログで確認、Web検索callは0。
- 予算実績: OFF/ONとも Reasoning LLM calls = 2/2（Stage1+Stage2、Repairなし）、Research/Web-search
  calls = 0、Physical Model API Calls = 2（Reasoning分のみ）。両Runで完全に同一。
- 保護条件: 既存Baseline上書きなし、DB active Knowledge変更なし、Human Modelへの自動write-back
  なし、Tier2/Tier3には進んでいない。

## 発見した交絡: Source Packの主題不一致

このTheme配下には実クロールされたCurrent Evidence文書が存在せず（このリポジトリの既知の
制約、事前調査で確認済み）、`build_source_pack`が返す7件の文書はすべて、**以前Case2 Putin検証
用に投入された「ロシア国防省の72時間人道的停戦」Fixture**である。document_idsはOFF/ONで
完全一致（7件とも同一ID・同一順序）——**入力そのものはOFF/ONで同一**であることを直接確認した。

にもかかわらず:
- **OFF**: `claims`はこのFixture文書由来だが、`one_line_assessment`/`key_point_assessment`/
  `main_scenario`は正しく「トランプ政権がウクライナ支援を交渉カード化しているか」という
  実際の質問に答えている。`key_actor_profiles`にTrump/Zelenskyy/Putinの3者とも登場。
- **ON**: `one_line_assessment`/`key_point_assessment`/`main_scenario`/`decisive_power_and_leverage`
  が**ほぼ全面的に**Fixture文書の内容（ドネツク州の停戦・部隊ローテーション）そのものの分析に
  なってしまい、実際に尋ねた質問（Oval Office会談・支援停止・ジェッダ協議）への言及は
  断片的（ゼレンスキー7回、オーバルオフィス4回、ジェッダ4回の言及はあるが、Key Point・
  Main Scenario・Alternative Scenarios・decisive_power_and_leverageの実質的な内容は
  すべてFixtureの停戦シナリオ）。`key_actor_profiles`は**Putin/Zelenskyyの2者のみで、
  Trump（今回の質問の中心人物）が欠落**している。

## Tier1固有フィールドの生成状況（ON、この逸脱の影響下）

| フィールド | 結果 |
|---|---|
| `directional_perceptions` | **null（0件）**。Stage2で`directional_perception`モジュールは実行されたが、LLMは生成しなかった。 |
| `key_actor_profiles[].perceived_path` | 両Actorともnull。 |
| `key_actor_profiles[].event_role` | **生成された**。Putin: role_type=`means`（局所停戦は前線再編・部隊ローテーションのための手段、国際社会向けの「人道的配慮」シグナルとして流用）。Zelenskyy: role_type=`signal`（ロシアの停戦は信用できないという対欧米メッセージのシグナル）。 |
| `situation_actor_assessment`（`actor_expected_response`/`analyst_expected_response`含む） | **配列自体が空**。この局面のIssue Component評価自体が生成されなかった。 |

Event Roleが「means」「signal」という質的に妥当な区別で生成された点はTier1設計の意図と一致する
（Fixtureの停戦発表が目的そのものではなく手段/シグナルである、という読み——ただし対象が
今回の実質問ではなくFixture側の局面である点で価値は限定的）。一方、directional_perceptions・
perceived_path・situation_actor_assessmentは軒並み空で、この逸脱したRunではTier1の中核機能の
大部分が実質的に検証できていない。

## 品質ガード集計（この点はTier1コードの決定的な効果として確認できる）

| | OFF | ON |
|---|---|---|
| 総チェック数 | 43 | 45（+2、新規directional_perceptions系グラウンディングチェック） |
| Pass | 39 | 44 |
| Fail | 4 | 1 |
| Fail一覧 | supporting_knowledge_refs_grounded, key_point_not_selected_for_novelty, temporal_hedge_language_not_present, unsupported_actor_assumption_not_promoted | temporal_hedge_language_not_present |

`temporal_hedge_language_not_present`（時点混乱表現の検出、advisory・非repair）は両Runとも
Fail——これはTier1と無関係な既存の弱点で、変化なし。それ以外の3件（supporting_knowledge_refs_
grounded/key_point_not_selected_for_novelty/unsupported_actor_assumption_not_promoted）は
ONでは発生していない。ただしON側はそもそも本題から逸脱した分析であるため、この改善が
「Tier1が良い推論をさせた結果」なのか「Fixtureの停戦シナリオの方がEvidence Groundingしやすい
単純な話だったから」なのかは、この交絡下では判別できない。

## 8観点の比較表（ユーザー指定フォーマット）

上記の交絡により、大半の観点は`Cannot judge`とする。無理な判定はしない。

| 観点 | OFF | ON | 判定 |
|---|---|---|---|
| 1. Situation Frame | 「米国の対ウクライナ支援の交渉カード化」を正しく捉えた | 「ロシア軍の局地停戦・部隊ローテーション」という別局面を捉えた（Fixture由来、質問の局面ではない） | **Cannot judge**（ONが別問題に答えたため直接比較不可） |
| 2. Actor Big Flow（Goal→Path→Current State→Event Role） | Goal/Current Stateは充実（persistent_preference等）だがperceived_path/event_role専用フィールドは無し（旧Schema） | event_roleは生成（means/signal）、perceived_pathは空、situation_actor_assessment自体が空 | **Cannot judge**（部分的にしか比較材料がない） |
| 3. Directional Perception | フィールド自体が存在しない（旧Schema） | フィールドは存在するがnull（0件） | **Cannot judge**（ON側で一度も生成されず、機能検証に至らず） |
| 4. Candidate Set（Actor→Goal→Path→Event Role→Expected Response→Desired State） | hypothesis_registerはH0-H4を実質問に対して生成、動機の羅列ではなく交渉カード化ロジックで一貫 | hypothesis_registerはH0-H4をFixtureの停戦シナリオに対して生成、内部的には一貫しているが対象が違う | **Cannot judge** |
| 5. Candidate Ranking / Rejection | 4候補比較・rejected_candidatesの理由づけあり、通常の候補選定として妥当 | 3候補比較・妥当な理由づけあり、ただし対象がFixtureシナリオ | **Cannot judge** |
| 6. Actor Expected vs Analyst Predicted | 分離フィールド自体が存在しない（旧Schema、likely_reactionのみ） | situation_actor_assessment自体が空のため両フィールドとも未生成 | **Cannot judge**（この観点はどちらのRunでも実質的にテストできていない） |
| 7. Dominant Flow / Scenario | key_stakeholder_determination=multiple、main_scenarioは相互作用（ゼレンスキーのレッドライン・議会制約等）を経た帰結として記述、本人希望の単純投影ではない | key_stakeholder_determination=distributed、main_scenarioはFixture内の複数情報源の解釈対立として記述、質問の実質主体（Trump）が不在 | **Cannot judge** |
| 8. Speculative Noise | 4候補・5仮説、根拠の強さに応じてconfidence/statusを使い分けており過剰ではない | 3候補・5仮説、同様に過剰ではないが対象がずれている | **Same**（少なくとも「もっともらしいだけの仮説の量産」という意味でのノイズ増加は無かった） |

## A/B/C/D分類

- **A. Tier1によって初めて可能になった推論**: event_roleのmeans/signal区別は、旧SchemaのAction
  Substance一本化では出せなかった「圧力は目的か手段か」という構造化を実際に生成できており、
  この点はTier1が意図した効果が部分的に出たと言える（ただし対象がずれた局面での発火）。
- **B. 既存AIでも既にできていた推論**: 目的階層(persistent_preference→issue_objective)、
  Key Point候補比較、hypothesis_registerのH0-H4フレームワークは、OFF側で既に実質問に対して
  機能しており、Tier1固有の追加価値ではなくSession2 Reasoning Upgrade由来の既存機能。
- **C. 新機能を入れてもまだ弱い推論**: directional_perceptionsは実行環境で一度も生成されず
  （0件）、perceived_pathも同様。Candidate Generation Operators（Goal/Means等10種）の
  Prompt指示が実際に候補の質へどう効いたかは、この逸脱下では確認できていない。
- **D. 新たに生じたノイズ／副作用（最重要の懸念）**: ON側が実際の質問から逸脱し、Source Pack内の
  無関係なFixture文書の内容へ分析全体が引きずられた。Trump（質問の中心人物）が`key_actor_profiles`
  から欠落した。これがTier1のPrompt追加（システムプロンプトの分量増加）による注意の希薄化なのか、
  単純なLLMのラン間分散なのかは、この1サンプルでは判別不能。**これは看過できない懸念点であり、
  次のRunで再現するか確認する必要がある。**

## 推奨する次のステップ（判断待ち）

1. **同一条件でON側のみもう1回実行**し、今回の逸脱が再現するか（stochasticな単発の外れ値か、
   再現性のある問題か）を確認する。追加コストは小さい（Reasoning call 2回のみ、Research 0）。
2. または、**実際のCurrent Evidenceが存在する別ケース**（このDB内でTheme一致かつ実クロール文書が
   ある案件、例えばCase3 Netanyahu/Hormuz関連は実データが存在することを過去の検証で確認済み）に
   差し替えて再比較する。Trump-Putin限定ではなくなるが、Source Pack主題不一致という交絡自体を
   排除できる。
3. または、**このCase用に、実際の質問（Oval Office Clash）に即した最小限のCurrent Evidence
   Fixtureを新規投入**する（M10-A2 Case2の前例と同じ手法）。ただしこれは「新規DB Fixtureは
   投入しない」という当初条件からの逸脱であり、ユーザーの明示的な承認が必要。

いずれも実施していない。ここで停止し、判断を仰ぐ。
