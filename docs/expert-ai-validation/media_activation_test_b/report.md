# Media Activation Test — Condition B: 明確な事実矛盾。無しでも起動するか

**STATUS: `PASS`。仮説「Media IntelligenceはFact Conflict Detectorに過ぎないのでは」
は否定された。**

## 目的

Case2（Condition A: 同一事象を巡る明確な事実矛盾）ではMedia Reasoningが機能し、
Case3の9記事（Condition C: 関連するが別々の論点、Media Profile追加後も不発）では
機能しなかった。この2点だけでは「明確な矛盾が無いと発火しない」のか「Case3固有の
他の要因（後述）」なのかを切り分けられないため、**Condition B（同一局面だが事実
矛盾は無く、Framing/Emphasisだけが異なる）を単独で検証**した。

## 実施条件

新規実装・コード変更は行わず、Case3の実記事9件のうち、**同一局面（ホルムズ海峡・
イラン-オマーン航行枠組み交渉、2026年8月上旬）を扱いながら、事実として矛盾しない
3記事**を選定した:

| Outlet | 強調点（Framing） |
|---|---|
| South China Morning Post | 中国の外交仲介（王毅外相の要請、「実利的中立」） |
| NPR | 中国の経済的迂回（原油備蓄・輸入調整による市場安定化） |
| Bloomberg | イランの交渉要求（米国船籍の恒久的禁止、投資家向けリスク） |

これら3件は互いに事実として矛盾しない（同じ局面の異なる側面を報じている）。
`geo_expert_engine.build_source_pack`を一時的にmonkeypatchし、Source Packを
この3文書に確実に固定した（本番コードは変更していない、プロセス内のテスト時
差し替えのみ）。`case_id`は指定せず（Actor Intelligenceを絡めない、純粋な
Media Reasoning単体のテストとするため）、Media Intelligence Pack（8 Profile、
SCMP含む）はON。

## 結果: `media_source_context`が生成された（3記事全てに対応）

- **narrative_deviation**: Bloombergの「恒久的禁止」という強い表現を、他媒体の
  より慎重な表現と比較した場合の市場リスク認識への影響として検出。
- **media_editorial_context**（3件、各記事のdocument_idと対応）:
  - SCMP: 「実利的中立」framingが中国を安定志向の仲介者として描く可能性
    （`alternative_explanation`で「単に外交部発言の要約に過ぎない可能性」も明記）
  - NPR: 軍事面ではなく経済・市場面を強調する編集判断の可能性
  - Bloomberg: 「恒久的禁止」という強い表現が投資家向けリスク警告として機能する
    可能性
- 全てのObservable（observed）は意図を語らない事実記述、Hypothesisは
  category=editorial_framing/source_signaling等で明確に分離。
  `corroboration_basis`/`corroboration_target`/`alternative_explanation`も
  適切に記述されている。
- Historical Media Pattern起点のconfidenceは全件「低」に決定的補正済み
  （`confidence_adjustment_reason`に明記）。
- `removed_fabricated_ids: []`——Evidence引用は全て正しいdocument_id。

## 結論: 仮説確認

**「事実矛盾が無くても、Framing/Emphasisの違いだけでMedia Reasoningは起動する」**
ことが確認された。現在のMedia Intelligence設計は単なるFact Conflict Detectorでは
なく、狙っていたInformation Environment Analysis（何を強調したか・誰の視点で
語られているか・どんなAudience向けか）として機能する能力を持つ。

## ではCase3の9記事はなぜ不発だったのか（新しい仮説）

Condition Bがこれだけ明確にPassしたことで、Case3の不発は「矛盾が無いから」では
説明できなくなった。有力な代替説明:

**query_textの主題（ネタニヤフ）と、Source Pack文書の主題（イラン・ホルムズ）が
一致していなかった。** 本テストでは、query_text自体が「ホルムズ海峡における
イラン・オマーン間の航行枠組み交渉」と、Source Pack文書と同じ主題を直接尋ねて
いた。一方Case3のquery_textは「ネタニヤフのIran・Gaza政策」であり、Source Pack
文書（イラン最高指導者後継問題・ホルムズ危機）はイラン側の話題であって、
ネタニヤフ自身の話題ではない。Stage2はNetanyahuのActor Assessment・
situation_actor_assessment等、複数のモジュールを同時に処理する必要があり、
「主題が一致しない付随的な文書群」に対するMedia Reasoningの優先度が相対的に
下がった可能性がある。

これはConnection設計の欠陥ではなく、**Reasoning Budget配分・関連性判断**の
問題であり、次に検証する価値がある（本タスクのスコープ外、次の課題として記録）。

## M10-Bへの示唆

- Case2（Condition A、Fixture）: Media Reasoning機能を確認済み。
- Case3（Condition C、実データ、query主題とdocument主題が不一致）: Current
  Evidence Layerの価値は確認済みだが、Media固有の価値は今回のquery設計では
  検出できなかった——これは「このケースでMediaに価値が無い」という結論では
  なく、「query主題とdocument主題の不一致」という交絡因子込みの結果として
  正直に記録する。
- 本Activation Test（Condition B、実データのサブセット）: Media Reasoningが
  矛盾無しでも機能することを独立して証明——Media Intelligence自体の能力は
  実証済みとして良い。

M10-Bは、この3つの結果セット（Case2=Condition A、Case3=Condition C+query主題
不一致、本テスト=Condition B）を踏まえた上で進めることを推奨する。
