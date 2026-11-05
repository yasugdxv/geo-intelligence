# READING_EXPOSURE_STEP2_INTEGRATION_V1 — Freeze Manifest

**Freeze日**: 2026-09-24
**Freeze対象**:
- `geo_ios_analysis_session_service.py`（`get_or_build_reading_exposure`等、今回分のみ）
- `geo_ios_expert_ai_adapter.py`（`build_design_from_candidate`のバグ修正、今回分＋下記の混在分）
- `geo_ios_situation_presentation_api.py`（`reading_exposure`配線・Backward Compatibility fallback、今回分のみ）
- `geo_ios_situation_presentation_service.py`（`build_reading_exposure`等、今回分＋下記の混在分）
- `tests/test_geo_ios_reading_exposure_wiring.py`（新規、10件）
- `tests/test_geo_ios_phase_c_expert_ai_adapter.py`（既存assertion 1件を修正後の正しい挙動に合わせて更新。加えて下記の混在分）
- `eco-digest-spark/src/components/geo/geo-ios-situation-analysis-view.tsx`
- `eco-digest-spark/src/lib/geoIosSituationPresentationApi.ts`
- `eco-digest-spark/src/lib/i18n.tsx`（Reading Exposure関連の表示文言）

以降、本Freeze対象ファイルへの追加チューニングは、新しい要件が生じた場合にV2として別途設計する。

前提: `READING_EXPOSURE_LAYER_V1`（commit `e7c5558caf6aeeece5ca5a04a6759ec9e0b1c62e`、`geo_ios_reading_exposure_composer.py`のselection logic）は無変更のまま依存する。

### 重要な注記: このコミットに混在する範囲外の未コミット変更について

Freeze作業時のgit調査で、以下2ファイルには**今回のStep2配線作業だけでなく、2026-09-23付の別の未コミット作業（「Reading Presentation Layer v1」——「気づく→見る→疑う→仮説→読む」ストーリー化、Evidence Binding再構成、`_short_title`改善など）も混在している**ことが判明した。

- `geo_ios_expert_ai_adapter.py`
- `geo_ios_situation_presentation_service.py`
- `tests/test_geo_ios_phase_c_expert_ai_adapter.py`（`_short_title`関連テスト・Reading Portfolio関連テストを含む）

今回のStep2配線コード（`_build_exploratory_interpretation`等）は、この2026-09-23分の関数（`_HYPOTHESIS_TYPE_LABELS`・`_build_one_interpretation`等）を直接再利用しており、**git hunk単位での分離は依存関係上できない**（分離すると今回のコードが依存先を失う）ことを確認済み。

ユーザー承認（2026-09-24）に基づき、**hunk単位の手作業分離は行わず、テスト済みのworking tree内容をそのまま本コミットへ含める**方針とした。したがって本Freezeのcommitには、2026-09-23分の「Reading Presentation Layer v1」関連の未コミット変更も実質的に含まれる。今回のReading Exposure Step2 Integration作業に無関係な、その他327件の未コミット変更（別の作業ストリームによるもの）は、この方針に関わらず引き続き明示的に除外し、コミットに含めていない。

---

## 背景・目的

`READING_EXPOSURE_LAYER_V1`のFreeze後、Step2「読み方提案」API/UIへ実際に配線する作業。既存のReport Studio / IOS Step2表示（`InterpretationCard`、`interpretations`）はS Rank#1-3のみを見せていたが、Composerが計算するFRONTIER（Reading4）・EXPLORATORY（Reading5）を人間が実際に選択でき、その選択がStep3 Draft生成まで正しく引き継がれることを、実DB・実Workflowで確認した。

---

## この配線で修正した重大バグ

`geo_ios_expert_ai_adapter.build_design_from_candidate()` は、修正前は選択された`scenario_candidate`の内容を一切読まず、常に`response_json["main_scenario"]`からDesign fieldsを組み立てていた。つまり**Step2でどのReadingを選んでも、Step3 Draftの内容は常にS Rank#1（main_scenario）のままになる**という、この配線タスクの受け入れ条件（「選択したReadingがDraftへ反映される」）そのものを満たせない状態だった。

新関数`_map_selected_candidate_to_design_fields()`が、選択された`scenario_candidate.content.raw`から直接Design fieldsを組み立てるよう修正した。FRONTIER/EXPLORATORY選択時は、weakening evidence・不足しているEvidence・元のstatus（retained/unresolved/rejected、内部enumは翻訳済み文言のみ）を`assumptions`へ折り込み、探索的な読みを確定事実として渡さないようにした。`_map_response_to_design_fields`自体は`refine_design`（Chat改訂）が引き続き使うため無変更。

---

## Mechanical E2E（実DB・実Workflow、2026-09-24実施）

実Supabase上の既存Analysis Session「Blind Retrospective Diagnostic: HORMUZ_BLOCKADE_SESSION3」（`analysis_session_id=52ca2dd4-5636-4525-b281-25ddbb7827a0`、実Expert AI応答済み、hypothesis_register 5件・Reading Portfolio 3件を含む）に対し、新しいLLM呼び出しを一切行わずに以下を確認した（`geo_ios_analysis_session_service.select_scenario_candidate`/`geo_ios_expert_ai_adapter.build_design_from_candidate`/`geo_ios_situation_presentation_api.get_situation_presentation`を実clientで直接呼び出し）。

| 確認項目 | 結果 |
|---|---|
| MAIN選択 → reload → MAIN Draftになる | PASS（Design v7、main_scenario自身の内容） |
| FRONTIER選択 → reload → FRONTIER Draftになる | PASS（Design v8、H2_counterparty_audience_shift、status=unresolved由来） |
| EXPLORATORY選択 → reload → EXPLORATORY Draftになる | PASS（Design v9、Reading Portfolio `alternative_causal_structure`由来） |
| 4/5選択時にweakening/missing evidence/uncertaintyがDraft contextに保持される | PASS（FRONTIERは不足Evidence注記、EXPLORATORYは弱める情報3件＋切替条件が`assumptions`に反映） |
| DB上で`selected_scenario_id`と`selected=true`が一貫する | PASS（3回の選択いずれも単一行のみ`selected=true`、`session.selected_scenario_id`と一致） |
| 実際のDraft内容が3パターンで異なる | PASS（`hypotheses`/`assumptions`/`known_evidence`いずれも3パターンで相互に不一致） |
| 内部status文字列(retained/unresolved/rejected)がそのまま出ない | PASS（FRONTIERのassumptionsは「Sの中でも結論が出ていない、未解決の読み」、EXPLORATORYは「Sが本線として選ばなかった、探索的な読みである」という翻訳済み文言のみ） |

実行スクリプトは一時ファイルとして作成・実行し、恒久ファイルとしてリポジトリには追加していない（実DBに対する一度限りの検証用途のため）。実行結果ログの要旨は本Manifestに記載の通り。

---

## Freeze条件（すべて確認済み）

| 項目 | 結果 |
|---|---|
| Expert AI Core / CQAS / S ranking / Reading Portfolio生成 / Reading Exposure Composerのselection logic | unchanged（`READING_EXPOSURE_LAYER_V1`のまま） |
| Top1-3の内容・順序 | unchanged（既存`_build_one_interpretation`をそのまま再利用） |
| 新しいDB table / migration | 無し（既存`geo_ios_scenario_candidates.content` jsonbへ追加metadataを埋め込むのみ） |
| 選択の永続化 | 既存`geo_ios_scenario_candidates.selected` / `geo_ios_analysis_sessions.selected_scenario_id` / `select_scenario_candidate()`をそのまま再利用 |
| Draftへの引継ぎ | 選択されたcandidate自身の内容から組み立て（重大バグ修正済み、上記参照） |
| Backward Compatibility | Reading Exposure Composer失敗時は空配列＋audit log記録、既存`interpretations`表示・フロントのフォールバック共に確認済み |
| バックエンドregression | **1953 PASS**（新規10件含む、regressionゼロ。既存1件のassertionは旧バグに依存していたため修正） |
| フロントエンドtype check | `tsc --noEmit` エラーゼロ |
| Mechanical E2E（実DB・実Workflow） | 上記表の通り全項目PASS |

---

## 関連ファイル

- `geo_ios_reading_exposure_composer.py` / `docs/reading-exposure-layer-v1-freeze-manifest.md` — 前提となるFreeze済みSelection Logic（本Freezeでは無変更）
- `geo_ios_analysis_session_service.py` — `get_or_build_reading_exposure`（Composer呼び出し・べき等な永続化）
- `geo_ios_expert_ai_adapter.py` — `_map_selected_candidate_to_design_fields`（Draft mappingバグ修正）
- `geo_ios_situation_presentation_service.py` — `build_reading_exposure`（Step2表示への整形・status自然文翻訳）
- `geo_ios_situation_presentation_api.py` — API配線・Backward Compatibility fallback
- `eco-digest-spark/src/components/geo/geo-ios-situation-analysis-view.tsx` — Step2 UI（役割タグ・探索的インジケーター・「なぜ本線ではない？」・選択必須化）
- `tests/test_geo_ios_reading_exposure_wiring.py` — 配線側の単体テスト

## 未着手（本Freezeのスコープ外）

- Report Studio Step2の運用評価・ユーザーテストへの移行（次フェーズ）
- 江口さんセッション設計: 過去5ケースで現在性能を見せる → 新規2ケースでBlind Test → 実際の5案UIを触ってもらう → 選択結果確認 → Learning Workbenchへ還流
