# Gate 3/4 完了報告 — 10 Actor Historical Intelligence Build 全件展開

## 1. 状態サマリー

Gate3(Trumpパイロット)完了報告後、ユーザー承認(「次へ」)を受けて残り9 Actorへ
同一手法(実WebFetch/WebSearch調査→PRECEDENT_SCHEMA拡張によるDecision Episode
DB登録→cross-episode Pattern抽出→ACTOR_PROFILE_SCHEMA候補作成)を展開完了。

**全新規レコードは`status="candidate"`のみ。`activate=True`は一切使用していない。
Human Review(Gate6)を経るまで`active`化はされない。**

## 2. Decision Episode登録件数(knowledge_type="precedent")

| Actor | 件数 |
|---|---|
| Donald Trump | 6 |
| Vladimir Putin | 6 |
| Xi Jinping | 6 |
| Kim Jong Un | 5 |
| Volodymyr Zelenskyy | 5 |
| Benjamin Netanyahu | 5 |
| Mohammed bin Salman | 5 |
| Emmanuel Macron | 5 |
| Friedrich Merz | 4 |
| 高市早苗 | 5 |
| **合計** | **52** |

各episode_idと`knowledge_item_id`の対応は`{actor}_episode_ids.json`(scratchpad)に保持。

## 3. Actor Profile候補(knowledge_type="actor_profile")

既存active profileを`supersedes_id`で指定して候補作成(4件、既存プロファイルは
まだactiveのまま維持):
- Trump: Gate3内で作成済み(前回報告分)
- Putin `f39bf3e5-059a-4119-9fc5-9c10d86462cf`
- Xi Jinping `3434a15c-b8c2-47be-887e-6ba3afa6a732`
- Kim Jong Un `0488ff39-f7bc-47ee-802f-15a7e604841d`

新規Entity・新規候補(supersedes_idなし、6件):
- Zelenskyy `93e8433c-aad0-426e-abdb-56efdebfa0fe`
- Netanyahu `fe79618d-e6b2-4f9c-bd55-a88786c619d2`
- MBS `91d88ab7-207d-41a6-8b64-ea2de9cbb304`
- Macron `145ccc25-6861-4ccc-ae4b-263b09513668`
- Merz `cf95ca35-e6dc-4117-a0d8-07f1ebd518b2`
- Takaichi `abeffdf4-96e5-40e6-a9e7-79ca1b46c677`

全プロファイルに`persistent_preference`(単数、実サービス形状準拠)・
`decision_patterns[]`(pattern_role="default"/"exception"を各Actor最低1件ずつ含む、
矛盾・反転の保持を徹底)・`strategic_communication_pattern`を格納。

## 4. 途中で発覚した問題と対処

- Zelenskyyの5 Episodeは、当初bashヒアドキュメントで作成した際に印字上は成功して
  見えたが実際にはDBへ書き込まれていなかった(Putin作成時に発生した同種の
  ヒアドキュメント破損と同じ原因と推定)。DBを`created_by`で再クエリしたところ
  0件と判明したため、`Write`ツールで台本を再構成し実行、5件とも新規に正しく
  作成されたことを確認済み(上記IDが最終的な実DB値)。

## 5. 非回帰確認

`pytest tests/` — **391 passed / 6 failed**。失敗6件はすべて本タスク開始前から
存在する無関係の既知failure(`test_geo_official_crawler.py`1件、
`test_sustainability_article_selector.py`5件、別プロジェクトworld_monitor_dashboard
由来)であり、本タスクによる新規リグレッションはなし。

## 6. 残タスク・ユーザー確認事項

1. **Human Review(Gate6)**: 上記10件のactor_profile候補・52件のprecedent候補
   すべてが`candidate`のまま。どの範囲を誰がいつレビューするか、ユーザーの
   意向を確認したい。
2. Source Registry(`actor_source_registry.json`)の一部ソース(特に`.ru`ドメイン全般、
   イスラエル/サウジ/ウクライナ/EU理事会公式サイトの直接fetch)がアクセス不可のまま
   残っている。WebSearchスニペット経由で代替した箇所は各Episodeの`sources`/
   `information_class`欄に明記済み。
3. Media Intelligence(Section23で今回スコープ外と明記済み)は未着手のまま。
4. 全10 Actorの`actor_profile`候補が実際にExpert AIのActor Intelligence Blockへ
   反映されるのはHuman Review後の`active`化以降(現状は候補のままなので反映されない)。
