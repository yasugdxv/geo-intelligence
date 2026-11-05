# M10-A2: Case2 Putin — Source Pack Retrieval P0修正 後のMedia Increment Test

**STATUS: `STOP_GATE_PASSED`**（2026-09-01）。ユーザー指定の7条件を全て満たした。

## 実施した修正・準備

### ① Source Pack Retrieval P0修正（本番コード、`geo_expert_engine.py`）

`build_source_pack`が`query_text`を一切使わず「DB全体で直近fetchされた10件」を無条件に
返していたバグ（`docs/expert-ai-validation/m10a_case2_media_increment/
media_activation_trace.md`で発見）を修正した。

- `_extract_query_keywords`: query_textからKatakana/Latin固有名詞を優先抽出、無ければ
  Kanji連続語（定型語ストップワード除外）へフォールバック。
- `_relevant_rows_by_keywords`: 既存`geo_search_service._partial_match_candidates`と
  同じILIKE部分一致パターンを踏襲（新規RPC・migrationは作らない）。
- `theme_id`指定時は追加filterとして効く。Keyword該当が無ければ空を返す
  （「最近fetchされた10件」への無条件フォールバックは廃止）。
- 新規テスト4件追加（Putin/Ukraine query→サステナ文書除外、EUDR query→Ukraine文書
  除外、theme_id追加filter、該当無しで空）。既存テスト2件（generic query_textが
  seed dataと無関係だったため）を実際に関連するquery_textへ修正。
- **Regression**: Expert AIスコープ170件超 + 新規4件、全Pass。プロジェクト全体でも
  6件の既知・無関係な失敗（`test_sustainability_article_selector.py`等、日付依存
  fixtureの陳腐化、本修正の前から存在）を除き全Pass。

### ② Case2用Current Evidence fixture（7件、複数Lens）

単一の局面（2026年8月28日、ロシア国防省がドネツク州で発表した72時間の一時停戦、
検証用に構成した架空だが内的整合性のある事象）を、7つの異なるLensから記述した
文書を`geo_documents`へ投入（`is_mock=true`で明示）:

| Lens | 主張の要旨 |
|---|---|
| ロシア国防省（公式） | 全戦線で完全に攻撃停止、人道目的 |
| ウクライナ大統領府（公式） | 前線再編のための時間稼ぎと評価、砲撃継続を主張 |
| Kyiv Independent | 現地取材で砲撃継続を確認、補給路再編の可能性を指摘 |
| Meduza（露独立系亡命） | 親ロシア派軍事ブロガーも部隊ローテーション目的と分析 |
| Novaya Gazeta Europe（露独立系亡命） | 捕虜数の食い違いを併記、真偽判断は回避 |
| ISW（西側専門家） | 局所的・限定的と評価、部隊ローテーション時期との一致を指摘 |
| Global Times（中国第三国） | ロシアの人道姿勢を歓迎、西側報道を批判 |

### ③ Retrieval Snapshot事前確認（Azure呼び出し前）

修正後の`build_source_pack`を直接呼び出し、7件全てが取得されることを確認
（1回目はKanji抽出のストップワード判定バグと、4文書に固有のAnchor Keyword
[プーチン/ウクライナ/戦争]が本文中に無かったことが原因で3件しか取得できず、
両方を修正後に7件全件取得を確認）。無関係な既存文書の混入は1件のみ
（紅海・ホルムズ海峡リスクレポートの「戦争保険料」という語への偶然一致、
Evidence Discipline上は許容範囲）。

## M10-A2実行結果（A=既存Original After v2 Case2、B=本テスト）

| 項目 | A（Retrieval修正前、Media無し） | B（Retrieval修正後、Media有り） |
|---|---|---|
| Primary Key Point | NATOのウクライナ加盟・安全保障上の位置づけ | **ロシアによる占領地の既成事実化（特にドネツク州など東部）をどこまで進められるか** |
| control_type | distributed | mixed |
| key_point confidence | 低 | **中**（current_indirect_evidence、実際の現行証拠に基づき格上げ） |
| media_source_context | null | **narrative_deviation・media_editorial_context両方生成** |
| situation_actor_assessment | 3件 | 3件（内容が具体的な現行事象ベースへ刷新） |
| audience_analysis | 0件 | **1件（Signal vs Substance分析、具体的）** |
| hypothesis_register H0 | retained | **rejected（Media Evidenceが理由）** |
| calls / tokens | 2 / 96,275 | 2 / 102,619（+6.7%） |

## ユーザー指定Stop Gate（7条件）判定

| 条件 | 判定 | 根拠 |
|---|---|---|
| Query-relevant Current Evidenceが実際に取得される | ✅ | Retrieval Snapshotで7/7確認済み、実行結果でも全件が`hypothesis_register`/`main_scenario.supporting_evidence`等で引用されている |
| 3種類以上のMedia LensがCurrent Evidenceに紐づく | ✅ | Kyiv Independent・ISW・Meduza・Global Times・ロシア国防省（公式）の5者が明示的に引用・比較されている |
| media_source_contextが生成される | ✅ | **初めて非nullで生成**（`narrative_deviation`でGlobal Times vs 西側/ISWの記述差を明示的に比較、`media_editorial_context`でObservable/Hypothesis分離・corroboration_basis/target・alternative_explanationが適切に記述） |
| Media固有のFrameまたはEvidence Conflict ≥1 | ✅ | H0「純粋な人道目的」がKyiv Independent/ISW/Meduzaを根拠に**明示的にrejected**。「表向きの人道停戦」対「実際は部隊ローテーション・戦力再編」という一次情報源間の対立が、単なる記述比較で終わらずHypothesis判定に反映されている |
| H0-H4の少なくとも1つがMediaによって強化・弱化・再定義される | ✅ | H0=rejected（弱化）、H1「占領地既成事実化＋戦力再編ツールとしての停戦」=retained（Media Evidenceで強化）、Primary Key Point自体もこの流れを受けて「NATO加盟」から「占領地既成事実化」へ再定義された |
| Historical Media PatternだけでConfidenceを上げない | ✅ | media_editorial_context内のGlobal Times意図仮説は`evidence_class=historical_media_pattern`により決定的補正でconfidence=低に維持（`confidence_adjustments`に明示） |
| Current/Historical混同 = 0 | ✅ | `actor_evidence_separations`が実際に1件発動（Wagner反乱の historical episode IDをsituation_actor_assessment[2]から除去）、豊富な現行証拠がある中でも過去/現在の混同防止が機能 |
| CallBudget ≤3 | ✅ | 全ケースcalls=2 |

**7条件全て達成。Stop Gate通過。**

## Source Pack文書レンダリング修正 + 再確認（2026-09-01、ユーザー指示による追加修正）

`build_source_pack`の文書一覧が`title`のみを表示しdocument_id/出典/公開日時/
extracted_text本文を一切渡していなかった問題（直後の章で詳述）を修正した:

- `[document_id: <uuid>]`をタイトルの前に明示し、「引用時はこのIDを使うこと、
  タイトル文字列を代わりに引用しない」と明記。
- `geo_source_registry`（Case2の7 Fixtureにsource_idを付与）から出典名を解決し併記。
- `published_at`を併記。
- `_excerpt_around_keywords`により、query keywordの初出位置を中心とした最大1600字の
  抜粋を併記（全文はそのまま渡さない、token予算対策）。

修正後にCase2 M10-A2を再実行した結果（`m10a2_case2_putin_variant_B_result.json`、
修正前は`_before_rendering_fix_result.json`として保存）:

| 項目 | 修正前 | 修正後 |
|---|---|---|
| `removed_fabricated_ids`件数 | 30件（ほぼ全てタイトル文字列としての誤引用） | **0件** |
| `key_point_assessment.supporting_evidence_ids` | 空（scrubで除去） | **実UUID4件**（ISW/Kyiv Independent/Meduza/ウクライナ公式） |
| `media_source_context.media_editorial_context[0].evidence_ids` | 空`[]` | **実UUID**（Global Times） |
| `media_source_context.media_editorial_context[0].document_id` | タイトル文字列 | **実document_id** |
| `hypothesis_register`のsupporting/contradicting_evidence_ids | 空またはタイトル文字列 | **全件、実UUIDで一貫して引用**（例: H0がISW/Kyiv Independent/Meduza/ウクライナ公式を支持根拠、ロシア公式発表とNovaya Gazetaを反証根拠として明示） |
| key_point confidence | 中 | 中（維持） |
| main_scenario confidence | 中 | 中（維持、evidence_class=current_indirect_evidenceで裏付け） |
| media_editorial_context内Historical Pattern起点confidence | 低（正しくcap） | 低（維持、`confidence_adjustments`で明示） |
| situation_actor_assessment件数 | 3件 | 2件（0への崩壊なし、正常範囲の変動） |
| CallBudget | 2/2 | 2/2 |

ユーザー確認項目6点、全て達成:
1. 本文excerptがPromptへ入る ✅（Retrieval Snapshot・実行結果両方で確認）
2. document_idがCurrent Evidenceとして引用される ✅（fabrication scrub 0件が直接の証拠）
3. media_source_contextが維持される ✅（実evidence_idsを伴い、むしろ質が向上）
4. H0/H1のsupport/rejectが本文内容に基づく ✅（H0はISW/Kyiv Independent/Meduza/
   ウクライナ公式を支持根拠、ロシア公式の「完全停止」主張を反証根拠として明示——
   公式claim対独立系観測という対立構造が正しくHypothesis評価に反映されている）
5. Confidence discipline維持 ✅（決定的補正が実際に発動、不当な上昇なし）
6. situation_actor_assessmentが不自然に0にならない ✅（2件、健全な変動範囲）

**Source Pack ⇄ Media Intelligence接続は、ここでFreezeとする**（ユーザー指示通り）。
以後の変更は行わない。

## 新たに発見した課題（Media Reasoning自体の欠陥ではないが、次のP0候補）

`removed_fabricated_ids`を確認したところ、LLMは多くの箇所で文書を**タイトル文字列**
（例:「【検証用Fixture】Kyiv Independent: 停戦下でも砲撃継続と現地報告」）で
引用しようとしていたが、これは実際の`document_id`（UUID）と一致しないため
`evidence_ids_not_fabricated`のscrub処理で除去されていた。原因は
`build_source_pack`の文書一覧レンダリングが**タイトルのみ**（`- {title}`）を
表示し、`document_id`もextracted_text本文も一切プロンプトへ渡していないため
——LLMには「引用可能なID」自体が最初から見えていない。

今回は本文の要点をタイトル自体に書き込んだ検証用Fixtureだったため実害は
限定的だった（`hypothesis_register`等の自由記述本文には出典が正しく明記されて
いる）が、`evidence_ids`のような構造化フィールドは軒並み空になっており
（`media_hypothesis_evidence_grounded`がこれを検出しFailしている）、実運用の
記事タイトルはここまで内容を語らないため、より深刻な影響が出る可能性が高い。
**「build_source_packの文書一覧へdocument_idを併記する」ことを次の改善候補として
記録する**（本タスクのスコープ外、コード変更は行っていない）。

## 結論

Case5・Case2(1回目)で「Media Reasoningが機能しない」ように見えた問題は、
Media Intelligence自体にもReasoning接続設計にも欠陥が無く、**Source Pack
Retrievalのquery_text未使用というP0バグにより、そもそもMedia Reasoningが
解釈すべき現行文書が一件も存在しなかったこと**が根本原因だった。この
バグを修正し、Query-relevant Current Evidenceを用意した状態で再試験した結果、
Media Intelligenceは意図した通りに機能し、Hypothesis Competitionへ実際に
因果的な影響を与えることを確認した。

M10-Bへ進む条件は満たされたと判断する。ただし、上記「タイトルのみレンダリング」
問題は、5ケース横断時に実データ（人工的に情報を書き込んでいない実際の記事）を
使う場合に影響が顕在化する可能性があるため、次段階着手前に一言申し添える。
