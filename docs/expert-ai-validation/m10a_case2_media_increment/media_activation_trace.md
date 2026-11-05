# Media Activation Trace — Case2 Putin（新規Azure呼び出し無し、既存結果+コード監査のみ）

## Priority A: Current Evidenceはそもそもあったか（結論: 無かった）

Case2の`source_pack.document_ids`（10件）を`geo_documents`に直接クエリした結果:

| document_id | title | source_id |
|---|---|---|
| b47f3ca0-... | EUDR施行直前：森林破壊規制が原料調達と産地外交をどう変えるか | 03a3f7b0-... |
| 3326dcdc-... | CBAM本格運用：炭素価格が新しい通商摩擦の軸になる | 03a3f7b0-... |
| 62010532-... | EU包装規則（PPWR）適用開始：循環経済が素材安全保障政策になる | 03a3f7b0-... |
| 50a2a155-... | 強制労働規制の地政学化：EUの製品禁止と米国の関税措置が供給網を二重に圧迫 | 03a3f7b0-... |
| 609a1837-... | EUサステナ規制の「競争力シフト」：CSRD/CSDDD簡素化 | 03a3f7b0-... |
| eba1d367-... | 紅海・ホルムズ海峡リスク：物流安全保障とScope 3が衝突する | 03a3f7b0-... |
| 36b1602d-... | 米国のパリ協定再離脱後：企業の気候戦略 | 03a3f7b0-... |
| 9181293d-... | メキシコ水政策2026 | 03a3f7b0-... |
| e2fed736-... | 西アフリカ・ココア政治経済 | 03a3f7b0-... |
| 69e8064f-... | 米国の「過剰生産能力」Section 301 | 03a3f7b0-... |

**この10件は、本セッションで数時間前（2026-09-01 05:36〜05:40 UTC）にユーザーが
テスト用に用意したサステナビリティ関連レポート10本そのものである。**
Putin/Ukraine/NATO/Russia/ceasefireに関連する文書は**0件**。Global Times
（Media Profile）と結び付けられるcurrent documentも当然0件。

### 根本原因（コード確認済み）

`geo_expert_engine.build_source_pack(client, theme_id, query_text, ...)`
（L65-144）を確認した結果:

- `geo_documents`の取得は`order=fetched_at.desc, limit=10`——**`query_text`は
  一切フィルタ条件に使われていない**。`theme_id`が指定された場合のみ
  `theme_id=eq.<id>`が絞り込みに追加されるが、**今回の5ケース全てが
  `run_expert_request_staged(..., theme_id=None, ...)`で呼ばれている**ため、
  このフィルタは常に無効。
- 結果として`build_source_pack`は「DB全体で直近fetchされた文書上位10件」を
  無条件に返す。これはCase固有の`case_id`経由で別途構築される
  `build_case_context`/`build_foundation_pack`（Actor Intelligence・Evidence・
  Entityの実データはこちら経由）とは**別の情報源**であり、Media Reasoning
  （`media_context`モジュール）が実際に解釈する「直近文書」はこの
  `source_pack.documents`のみを指す。
- `query_text`は`user_prompt = f"{source_pack['text']}\n\n【依頼内容】\n{query_text}"`
  という形で、**Source Pack本文の後に「依頼内容」として付け足されるだけ**で、
  文書検索には一切関与しない。

**検証**: `ukraine_conflict`テーマ（`geo_themes`に実在）に`theme_id`を明示的に
絞ってgeo_documents/geo_eventsを直接クエリしたところ、**両方とも0件**だった。
つまり、たとえ`theme_id=ukraine_conflict`を指定していたとしても、Source Packは
空になるだけで、今回のような無関係文書の混入は防げたが、Ukraine関連の
Current Evidenceが得られたわけではない。**このDBには、そもそもウクライナ紛争に
関する crawled 現行文書・Eventが一件も存在しない。**

## 一枚表

| Trace | 結果 |
|---|---|
| Current Source Pack documents | 10 |
| Putin/Ukraine/NATO/Russia relevant current docs | **0** |
| identifiable media sources (Global Times等と対応する記事) | 0 |
| Media Profile matched current docs | 0 |
| Historical Media Patterns retrieved | 8 |
| Media Pack injected | Yes |
| Media reasoning instruction present（`_MEDIA_REASONING_NOTE`） | Yes（Observable/Hypothesis分離等を規定、ただし「本文」の存在を前提とする設計） |
| Media reasoning mandatory | **No**（`media_editorial_context`が出力Schemaに含まれる場合のみ意味を持つ、という条件付き記述であり、記事本文が無い場合に無理に生成させる指示は無い） |
| media_source_context generation condition met | **No**（解釈対象となる現行記事が0件のため） |
| media hypothesis generated | 0 |
| media evidence used to support/refute H0-H4 | 0 |
| Stage2 prompt tokens A/B | 43,739 / 44,886 |
| Stage2 completion tokens A/B | 5,154 / 2,042 |
| situation_actor_assessment A/B | 3 / 0 |
| ukraine_conflictテーマのdocuments/events件数 | 0 / 0（DB自体に存在しない） |

## 分岐判定（ユーザー提示の基準に基づく）

**該当: 「Current relevant media documents = 0」**
→ M10-AはまだMedia Reasoningテストになっていない。Current Media Evidenceを
用意して再試験する必要がある。

Priority B（Retrieval→Media Interpretation→Hypothesis Competitionの接続設計）は
今回**「失敗」と断定できない**。`geo_expert_schema.py`の`_MEDIA_REASONING_NOTE`を
確認した限り、モジュールの設計自体は「本文（記事内容）が無ければ何も書かない」
という、Evidence Discipline思想と整合的な保守的設計になっている——これは
「接続が壊れている」のではなく「材料が無いので正しく沈黙している」状態であり、
むしろ望ましい振る舞いである。

## Priority 3（Output圧迫）について: 参考情報のみ、結論保留

Stage2 completion_tokensがA(5,154)→B(2,042)へ約60%減少し、
`situation_actor_assessment`もA(3件)→B(0件)へ減少した。Stage2 prompt_tokensの
増加は+1,147（+2.6%）と軽微で、`_MAX_COMPLETION_TOKENS`（既存20,000）に対しても
十分な余裕があるため、単純なtoken上限による物理的な圧迫とは考えにくい。
Media Intelligence Pack追加によりLLMが出力配分を変化させた可能性はあるが、
**Priority Aが未解決（Current Evidence自体が無い）状態でこの現象を追っても
Media Intelligence起因なのか、Case2固有のLLM出力揺らぎなのかを切り分けられない**
ため、今回は結論を保留する。Current Evidenceを用意した再試験の際に、この現象が
再現するかどうかを併せて確認する。
