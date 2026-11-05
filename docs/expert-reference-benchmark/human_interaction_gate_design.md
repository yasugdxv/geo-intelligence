# Human Interaction Analytical Gate — 設計案(Phase F)

**状態**: 実装済み(Schema/Prompt/Guard)、pytest 698件通過。ただし§改訂で述べる通り
「必須Gate」から「Human Lens / Quality Signal」へ設計変更済み。実データRunは未実施。

## 改訂(Phase F改訂: Human Interaction Gate → Human Lens / Quality Signal)

本ドキュメント本文(§1-8)は当初の「必須Gate」設計を記した通りの内容として残すが、
**実装は以下の通り修正されている**——本文を読む際はこの改訂を前提とすること。

**変更の理由**: 当初のPhase Fは「Human Interactionを通らなければ先へ進ませない」
という、推論の正しさを強制する設計に寄りすぎていた。しかし本来の目的は「人間関係を
正しく復元すること」ではなく、**不確実な事実から、最も確からしく・説明力があり・
経営が備えやすい読み筋(Storyline)を作ること**である。Human Interactionはこの
Storylineを組み立てるための推奨レンズの一つであり、それ自体を目的化してはならない。

**変更点**:
1. `discretion_assessment`/`human_interaction_coverage`/`hypothesis_register[].
   interaction_basis`のSchema自体は維持する——AIがどの程度「人で読んだか」を
   観測するQuality Signalとして有用なため。
2. `human_interaction_gate_present`/`human_interaction_status_consistency`/
   `hypothesis_interaction_basis_grounded`の3件は、Repair対象から**Advisory**へ
   格下げした（`geo_expert_quality_guard.MODE_V12_CHECK_NAMES`から削除）。
   `hypothesis_interaction_basis_missing`は当初からAdvisoryのまま変更なし。
3. Prompt側(`_DISCRETION_MODE_NOTE`/`_HUMAN_INTERACTION_COVERAGE_NOTE`/
   `_COMPETING_HYPOTHESES_NOTE`/`_DEEP_REASONING_SEQUENCE_NOTE`)を全面的に
   「Human Interactionが薄い・書ききれないことを理由にHypothesis/Storyline生成
   自体を止めたり簡略化したりしないこと」という趣旨で書き換えた。
4. `hypothesis_register`のRanking観点を5→7に拡張し、(6)Decision Relevance
   （経営の意思決定にとっての重要性）と(7)Narrative Salience（人間模様として
   語られたときの理解・記憶・意思決定準備への寄与、ただしEvidenceを曲げることは
   禁止）を追加した。(1)〜(3)が拮抗する場合にのみ(6)(7)を優先順位の決め手とする。
5. `main_scenario.derived_from_hypothesis_id`が指す仮説を「Primary Storyline」
   （現時点のEvidenceで最も筋が良い読み筋、唯一の正解ではなく新情報で変わり得る）
   として再定義し、他のretained/unresolved仮説をAlternative Storyline/Wildcardと
   して位置づけた。`business_impact`/`monitoring_indicators`を、それぞれ
   Executive Implication/Watchとして選定したPrimary Storylineと整合させる旨を
   明記した。
6. `hypothesis_register`のSchema自体（`hypothesis_id`/`status`(retained/rejected/
   unresolved)等のフィールド名）は変更していない——Storyline語彙はPrompt上の
   解釈層として追加し、既存の参照整合性Guard・Report Studio等の既存コンシューマへの
   影響を避けた。

## 0. 問題の再確認(当初設計の記録、上記改訂を前提に読むこと)

Phase Eの順序修正(Key Actors→directional_perceptions→situation_actor_assessment→
key_point_assessment→hypothesis_register)を実データ(イラン)へ適用した後も、
`directional_perceptions`/`situation_actor_assessment`は`null`のままだったが、
`hypothesis_register`はH0〜H4の5件を問題なく生成できた。これは、Schema/Promptの
**登場順序**を直しても、`hypothesis_register`が`directional_perceptions`/
`situation_actor_assessment`の中身に**依存せず独立に生成できてしまう**という
構造が残っていることを示す——つまり「先に書かせる」だけでは、Human Interactionを
迂回してEvent/Actor/event_roleから直接Hypothesisへショートカットする経路を
閉じられない。今回の設計は、この経路自体を構造的に塞ぐことが目的。

## 1. 全体設計方針

1. Human Interactionを**optional enrichment(あれば良い付加情報)から、
   Human-discretion-dominantなケースにおけるanalytical gate(通過するには
   明示的な自己申告が必要な関門)へ格上げする**。
2. Evidence不足を理由にnullで素通りすることを禁止する代わりに、
   「検討した上で見つからなかった」ことを**明示的な状態値として申告させる**
   （怠けたnullと、検討済みの空、を区別する）。
3. `event_role`／`directional_perceptions`／`situation_actor_assessment`の
   責務を明文化し、`event_role`がHuman Interactionの代替として扱われることを
   Prompt上で明示的に禁止する。
4. `hypothesis_register`の各仮説に、Actor-pair参照による**構造的な接続点**を
   持たせ、Human Interactionデータを経由しないHypothesis生成をGuardで検出する。
5. 新規フィールドは最小限にし、`identified_knowledge_gaps`/`investigation_questions`
   等の既存フィールドを可能な限り再利用する。

## 2. Schema変更案

### 2.1 新規: `human_interaction_coverage`(ケース単位のgate、1オブジェクト)

Stage1の新規モジュール要素として、`situation_actor_assessment`/
`directional_perception`モジュールが発火するprofile×mode(現状deep×
theme_design/case_analysisのみ)でのみ出現する、**nullを許さない**必須オブジェクト。

```
human_interaction_coverage: {
  discretion_mode: "HUMAN_DISCRETION_DOMINANT" | "STRUCTURE_DOMINANT" | "MIXED",
  mode_rationale: string,                      // なぜこの局面をこう分類したか
  interaction_status: "GROUNDED" | "PARTIAL_EVIDENCE"
                     | "INSUFFICIENT_EVIDENCE" | "NOT_APPLICABLE_STRUCTURE_FIRST",
  status_rationale: string,                    // 必須。この状態を選んだ理由
}
```

* `discretion_mode`は、ユーザーが以前提示したReasoning Frameworkの
  「STRUCTURE-FIRST EXCEPTION」の判定そのもの——物理的制約・法制度・技術的不可能性・
  資源制約が支配的かどうかを、Key Actor特定の直後(directional_perceptions/
  situation_actor_assessmentの生成を試みる**前**)に判定させる、軽量な事前スクリーニング。
* `interaction_status`は、`directional_perceptions`/`situation_actor_assessment`の
  実際の生成を試みた**後**に、その結果を自己申告する形で書かせる(Schema上の位置は
  `directional_perceptions`/`situation_actor_assessment`の直後、`key_point_assessment`
  の直前)。これにより「null」は「何も申告しない」ことを意味しなくなり、必ず4状態の
  いずれかを明示しなければ通過できない。
* `interaction_status=INSUFFICIENT_EVIDENCE`または`PARTIAL_EVIDENCE`の場合、
  対応する欠落理由は新規フィールドを作らず、既存の`identified_knowledge_gaps`へ
  「どのActorペアの、どんな認識/期待データが欠けているか」を具体的に1件以上
  追加することを必須にする(Guardでクロスチェック、2.4章参照)。
* `interaction_status=NOT_APPLICABLE_STRUCTURE_FIRST`は、`discretion_mode=
  STRUCTURE_DOMINANT`の場合のみ選択可能とする(整合性はGuardで検証)。

**なぜ`key_point_assessment.control_type`を流用しないか**: `control_type`
(actor_controlled/institutionally_constrained/structural/distributed/mixed/unknown)は
概念的には`discretion_mode`と重なるが、Phase Eの順序修正により
`key_point_assessment`はdirectional_perceptions/situation_actor_assessmentより
**後**に生成される設計へ変わった。ゲート判定は Human Interactionを試みる**前**に
必要なため、`control_type`をそのまま前倒しで使うことはできない。`key_point_assessment`
自体を分割して`control_type`部分だけ前出しする案も検討したが、既存Schemaの構造を
大きく崩す(過度なリファクタ)ため採用しない。かわりに軽量な`discretion_mode`を
新設し、`control_type`とは「粗いスクリーニング」対「精緻な分類」という役割分担にする
(両者が食い違う場合はそれ自体が興味深い所見になり得るため、無理に一致させる
Guardは設けない)。

### 2.2 新規: `hypothesis_register[].interaction_basis`(仮説単位の構造的接続)

各仮説に、根拠とするActor Interactionを構造的に参照させる。

```
hypothesis_register[].interaction_basis: {
  basis_type: "DIRECTIONAL_PERCEPTION" | "SITUATION_ACTOR_ASSESSMENT"
            | "EVENT_ROLE" | "EVIDENCE_GAP" | "NOT_APPLICABLE",
  actor_ref: string | null,
  target_actor_ref: string | null,
  gap_note: string | null,   // basis_type=EVIDENCE_GAPの場合、何が欠けているか
}
```

* `basis_type=DIRECTIONAL_PERCEPTION`/`SITUATION_ACTOR_ASSESSMENT`を選んだ場合、
  `actor_ref`/`target_actor_ref`は、それぞれ`directional_perceptions[].observer_actor_ref/
  target_actor_ref`または`situation_actor_assessment[].actor_ref`(+それに対応する
  `target_actor_ref`)と**実際に一致する組**でなければならない(Guardで参照整合性を
  検証、2.4章のReferential Integrity Check)。
* `basis_type=EVENT_ROLE`は許容するが、`event_role`は行動の機能分類であり
  認識・期待の代替ではないため、H1(instrumental_indirect)/H2(counterparty_audience_
  shift)/H4(frame_breaking)——いずれも「相手をどう読むか」に直接依存するタイプ——では
  `basis_type=EVENT_ROLE`単独の仮説は**Guardの警告対象**とする(2.4章)。
* `basis_type=EVIDENCE_GAP`は、`human_interaction_coverage.interaction_status`が
  `PARTIAL_EVIDENCE`/`INSUFFICIENT_EVIDENCE`の場合にのみ使える、正直な「この仮説は
  相互作用データの不在自体を論点にしている」という申告。
* H3(constraint_domestic_driver)は、対象がしばしば「国内Audience」等、Actor
  Profile化されていない主体であるため、`basis_type=NOT_APPLICABLE`を認める
  (ただし`gap_note`で何が対象かを説明させる)。H0(straightforward)は表向きの説明
  そのものであるため、`interaction_basis`は任意(nullable)のままでよい。

### 2.3 責務分離の再確認(Prompt反映事項、Schema自体は変更不要)

既存の3フィールドの役割を、Prompt Note内で以下のように明確化する
(Schemaの型・必須性は変更しない、Note文言のみ追加):

| フィールド | 責務 | 明示的に禁止する誤用 |
|---|---|---|
| `key_actor_profiles[].event_role` | **観測された行動の機能分類**(この一手が交渉カードか・シグナルか等) | これを埋めたことをもって、Directional Perception/Situation Actor Assessmentの検討を済ませたとみなさないこと |
| `directional_perceptions` | **AがBについて持つ主観的認識**(Bの客観的実態の記述ではない) | event_roleのrole_type/explanationをそのまま転記しないこと(行動の機能とAの認識は別軸) |
| `situation_actor_assessment` | **Aが期待するBの反応(actor_expected_response)とAnalystが独自に予測するBの反応(analyst_expected_response)** | 両者を`likely_reaction`的に同一視しないこと(既存の`_ACTOR_ANALYST_RESPONSE_SEPARATION_NOTE`と同じ原則をここでも再掲) |

## 3. Prompt変更案

* `_DEEP_REASONING_SEQUENCE_NOTE`(Phase Eで既に22段階化済み)へ、新設の
  Discretion Mode判定・Interaction Status自己申告のステップを追加する
  (例: (4)Key Actorsの直後に(4b)Discretion Mode判定、(7)Situation Actor
  Assessmentの直後に(7b)Interaction Status自己申告)。
* 新規Note案(`_HUMAN_INTERACTION_GATE_NOTE`、仮称): 「Human-discretion-
  dominantな局面では、Directional Perception/Situation Actor Assessmentの
  検討を省略してはならない。根拠が見つからない場合も、空配列のまま無言で
  通過することは禁止であり、必ずhuman_interaction_coverage.interaction_status
  へGROUNDED/PARTIAL_EVIDENCE/INSUFFICIENT_EVIDENCE/NOT_APPLICABLE_STRUCTURE_
  FIRSTのいずれかを明示し、status_rationaleで理由を説明すること。」
* 既存の`_COMPETING_HYPOTHESES_NOTE`/`_CANDIDATE_GENERATION_OPERATORS_NOTE`へ、
  「H1/H2/H4は、対応するinteraction_basisがdirectional_perceptions/
  situation_actor_assessmentの実在レコードを参照できる場合、それを優先して使うこと。
  event_role単独のみを根拠にこれらの仮説を生成しないこと」という一文を追加する。

## 4. Guard変更案(`geo_expert_quality_guard.py`)

1. **`human_interaction_gate_present`**: `human_interaction_coverage`が
   非null、かつ`interaction_status`が4値のいずれか、`status_rationale`が
   非空であることを検証。Human-discretion-dominant判定のprofile×modeで
   欠落していれば、既存のRepair(1回だけの再生成)対象とする候補。
2. **`human_interaction_status_consistency`**: 申告と実データの整合性を検証。
   - `interaction_status in {GROUNDED, PARTIAL_EVIDENCE}` なのに
     `directional_perceptions`と`situation_actor_assessment`が両方とも
     空/null → 矛盾として検出(「グラウンデッドと言いながら中身がない」を防ぐ)。
   - `interaction_status == NOT_APPLICABLE_STRUCTURE_FIRST` なのに
     `discretion_mode != STRUCTURE_DOMINANT` → 矛盾として検出。
   - `interaction_status in {PARTIAL_EVIDENCE, INSUFFICIENT_EVIDENCE}` の場合、
     `identified_knowledge_gaps`に対応するギャップ記述が最低1件あるか確認
     (完全な意味比較はせず、既存の`hypothesis_pruning_present`と同様の
     存在チェックに留める)。
3. **`hypothesis_interaction_basis_missing`(核心のショートカット検出)**:
   `discretion_mode == HUMAN_DISCRETION_DOMINANT`かつ`interaction_status in
   {GROUNDED, PARTIAL_EVIDENCE}`の場合、hypothesis_registerのうちtype∈
   {H1_instrumental_indirect, H2_counterparty_audience_shift, H4_frame_breaking}
   の各itemについて、`interaction_basis.basis_type`が`EVENT_ROLE`または
   `NOT_APPLICABLE`のみだと**advisory警告**(将来的にrepair対象へ格上げ検討)。
4. **Referential Integrity Check**: `interaction_basis.actor_ref`/
   `target_actor_ref`が指定されている場合、それが実際に`directional_perceptions`
   または`situation_actor_assessment`の該当レコードに存在する組み合わせかを
   機械的に検証(既存の`known_ids`パターン——hypothesis_registerの
   `hypothesis_id`参照整合性チェックと同じ実装方針を流用)。

**Repair対象にするかAdvisory止まりにするか**は未確定——1と2(自己申告の
存在・整合性)はRepair対象(既存の1回だけの修復生成の枠内)に格上げしても
Azure呼び出し回数は増えない。3(ショートカット検出)は、初回導入時点では
Advisory(既存の`initial_failed_checks_detail`へ記録するのみ)から始め、
実データでの発火頻度を見てからRepair昇格を検討する方が安全——Phase D-3/D-4bで
deep_scenario/situation_actor_assessmentを導入した際と同じ「まずdeep限定・
advisoryから」という導入作法に倣う。

## 5. Primary Question/hypothesis_registerの導出ルール変更案

`_KEY_POINT_NOTE`へ以下を追加: Human-discretion-dominantな局面では、
primary_key_point/selection_rationaleは、(a)特定のActor→Counterparty
interaction(directional_perceptions/situation_actor_assessmentの実在
レコード)、または(b)human_interaction_coverage.interaction_statusが
PARTIAL_EVIDENCE/INSUFFICIENT_EVIDENCEの場合はその欠落自体、のいずれかを
根拠として明示すること。Event/Claim/Actor Profileの表層記述だけから
Primary Questionを立てないこと。

## 6. 既存フィールド再利用 vs 新規フィールドのトレードオフ

| 目的 | 新規フィールド案 | 既存フィールド再利用案 | 採用方針 |
|---|---|---|---|
| Evidence不足の理由記述 | `evidence_gap_description`(専用フィールド) | `identified_knowledge_gaps`(既存) | **再利用**(重複source of truthを避ける) |
| 調査すべき問いの記録 | 専用フィールド | `investigation_questions`(既存、situation_actor_assessmentモジュール内で既に合成済み) | **再利用**(ただし既存taxonomyがこの用途に十分か実装前に要確認) |
| Gate状態そのものの申告 | `human_interaction_coverage`(新規) | — | **新規**(既存フィールドに相当するものがなく、これが今回の核) |
| 仮説とActor Interactionの接続 | `hypothesis_register[].interaction_basis`(新規) | `hypothesis_register[].reason`(自由記述、既存) | **新規フィールドを追加**(自由記述のreasonだけでは、Guardが機械的に参照整合性を検証できないため。ただしreason自体は削除・変更しない) |

## 7. 後方互換性・実装コストへの影響

* `directional_perceptions`/`situation_actor_assessment`自体の型・既存
  必須性は変更しない——既存コンシューマ(Report Studio等)への影響なし。
* `human_interaction_coverage`/`hypothesis_register[].interaction_basis`は
  純粋な追加フィールド。deep×theme_design/case_analysisのみで出現し、
  quick/standard/他modeには一切影響しない(既存のモジュール発火表・Call数を
  変更しない)。
* 影響を受ける既存pytest: `test_geo_expert_staged_execution.py`の
  `hypothesis_register`関連fixture、`test_geo_expert_quality_guard.py`・
  `test_geo_expert_depth_grounding_upgrade.py`のGuardテストは、新規必須
  フィールドの追加に伴いfixtureの更新が必要になる(実装時に対応、今回は
  未着手)。
* Repair対象に格上げする場合、`geo_llm_common`側のRepair実行フロー自体は
  変更不要(既存の「1回だけの修復生成」の対象チェックリストへ追加するのみ)。

## 8. 未解決の設計判断(実装前にユーザー確認が必要な点)

1. `discretion_mode`の判定を、Human Interaction生成の**前**に置く設計で
   問題ないか(2.1章の理由により`control_type`の前倒し流用は避けた)。
2. `hypothesis_interaction_basis_missing`(4.3)を、導入初期からAdvisoryのみに
   するか、最初からRepair対象にするか。
3. H3(constraint_domestic_driver)を`interaction_basis`必須対象から除外する
   判断(国内Audience等、Actor Profile化されない対象を想定)でよいか。
4. `investigation_questions`の既存taxonomyが、Evidence Gap記述の再利用先として
   実際に十分か(実装前に`_INVESTIGATION_QUESTION_NOTE`の詳細を再確認する必要あり)。
