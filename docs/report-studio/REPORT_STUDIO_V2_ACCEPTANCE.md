# Report Studio v2 — Browser E2E Product Acceptance（Phase 5）

実施日: 2026-09-13
実施方法: 実ブラウザ（Playwright経由でUI操作を自動化、ただし操作内容はユーザーが手動で行う場合と同一の画面遷移・クリック・入力のみ。DB/APIの直接操作は状態確認・検証目的でのみ使用し、機能実行そのものはすべてUI経由）

## 1. Tested Scenario

- Scenario A: Application AI Full Flow（New Project → Design → Collect → Analyze → Compose → Review → Edit → Continue → Next Run）
- Scenario B: ChatGPT Fallback（Prompt1/Prompt2 Export → Import → Use as Current → Stale化 → Report Edit）
- Scenario C: Version Isolation（Design v1/v2それぞれにpinされたRunの独立性）
- Scenario D: Continuity（Next Run作成時のPrevious Continuity Snapshot固定）
- Scenario E: Report Edit Chain（Edit1→Edit2の親子関係、Canonical不変、Revert、composition_result再利用）
- Scenario F: Storage（大容量/小容量Artifactのルーティング）
- UX Acceptance（各StepのUI表現・内部用語露出）
- Legacy Regression（Iranプロジェクトの旧Collection/Evidenceタブ）

## 2. Environment

- Backend: `api_server.py`（`API_PORT=8001`、ローカルuvicorn）
- Frontend: `eco-digest-spark`（vite dev server、port 8080）
- DB: 共有Supabase（本番と同一インスタンス、テスト用Project/Runを新規作成して隔離）
- ブラウザ: Playwright + 事前キャッシュ済みChromium (`chromium-1228`)

## 3. Project / Run

| 対象 | ID | 備考 |
|---|---|---|
| Project | `73e0b5b8-3255-4d34-beb1-87a309fa5045`（project_code=`e2e_phase5`） | 本Phaseで新規作成 |
| Design v1 | version 1 | `primary_graph_signals`にDesign ChatのLLMがSignal説明文を誤って入れた不正版（BLOCKER発見の経緯） |
| Design v2 | version 2 | `primary_graph_signals=["S1"]`, `common_primary_graph_start="2026-07-01"`に修正した正常版 |
| Run 1 | `8b40a43c-2693-4006-ac4f-0d1b7b9aa62b` | Design v1 pinned。Aggregation失敗の再現・BLOCKER確認用に使用、Preprocess以降は未完走のまま保持 |
| Run 2 | `70a47de6-72b3-4f5f-b0ff-d9ee24a13c4e` | Design v2 pinned。Scenario A/B/C/Eの主体。Preprocess→Prompt1→Aggregation→Prompt2→Report Editまで完走 |
| Run 3（Next Run） | `4f409992-9f4b-4043-aa9d-7f4b3d615d02` | Run 2をbaselineに`Continue`から作成。Scenario D確認用 |

投入CSV: 実際のBabel Streetエクスポート形式CSVから15レコードを抽出したサンプル（`S1_...csv`、ファイル名でSignal=S1として検出）。

## 4. PASS / FAIL 総括

| Scenario | 結果 |
|---|---|
| A. Application AI Full Flow | **PASS**（BLOCKER 3件修正後） |
| B. ChatGPT Fallback | **PASS** |
| C. Version Isolation | **PASS** |
| D. Continuity | **PASS** |
| E. Report Edit Chain | **PASS**（MAJOR 1件修正後） |
| F. Storage | **PASS**（実データは全件小容量、大容量ルーティングはPhase 3の実機検証結果を引用） |
| UX Acceptance | **PASS**（Minor Issue 3件を記録、完走は阻害しない） |
| Legacy Regression | **PASS**（Iranプロジェクトの旧Collection/Evidenceタブで回帰なし） |

## 5. 修正したBlocker/Major

### BLOCKER

1. **新規ProjectでDesign Stepに到達できない**
   - 症状: RunがまだRunが1つも無い新規Projectを開くと、「Runを作成するか選択してください」という空状態しか表示されず、Design Chatへ辿り着けなかった。
   - 原因: `geo-report-pipeline-panel.tsx`のワークフロー全体（旧`ReportWorkflow`コンポーネント）が`runId`必須の設計になっており、Design Stepも含めてRun選択後にしか描画されなかった。
   - 修正: `GeoReportPipelinePanel`を再構成し、Design StepのみRun非依存で常時アクセス可能にした。Collect以降のStepのみRun選択を要求する。

2. **Narrative MasterのAI生成結果がDraftへ保存されない**
   - 症状: Design画面の「Signal別に生成」→「AIで生成」を押しても、生成された行がテーブルに反映されず、Publishしても空のまま。
   - 原因: Report Templateには`POST .../report-template/save`がある一方、Narrative Masterには対応する保存エンドポイントが存在せず、生成結果（`useGenerateNarrativeMasterForSignal`の戻り値）を保存する手段が無かった。
   - 修正: バックエンドに`POST /projects/{project_id}/assets/narrative-master/save`を追加（既存の`assets.save_narrative_master_draft`をAPIとして公開するのみ、新規ロジックの追加なし）。フロントで生成結果を既存Draft行とマージ（同一signal_idの既存行を置換）して保存するよう配線した。

3. **Aggregation失敗時にExecutionが完了も失敗もせず`running`のまま停止する**
   - 症状: CSVアップロード後の一括実行（Preprocess→Prompt1→Aggregation→Prompt2）が、Aggregation段階で`primary_graph_signals`/`common_primary_graph_start`が不正だと、画面が「処理中」のまま進行も失敗表示もされずに固まる。
   - 原因: `geo_report_prompt2_orchestration_service.run_aggregation`内の一部バリデーションエラー（`primary_graph_signals`/`common_primary_graph_start`未設定）がExecutionの`status`を`failed`に更新せずに例外をraiseしていた（同関数内の別のバリデーションは更新していた、非対称な実装）。
   - 修正: 関数全体を`try/except`で包み、どの段階で例外が起きても必ず`status='failed'`に更新するようにした。
   - 併せて根本原因として、Design ChatのLLMが`primary_graph_signals`にSignal名の説明文（例:「S1: 消費税引き上げに対する生活者の受け止め」）を書いてしまい、Pythonの完全一致フィルタが機能しなくなっていたことも発見。Design Chatのsystem promptへ「`primary_graph_signals`にはsignal_idのみを含める」という制約を明記して修正した。

### MAJOR

4. **Prompt1 Canonical切替時にReport Edit連鎖がStale化されない**
   - 症状: ChatGPT Import結果をPrompt1 Canonicalとして採用すると、Aggregation/Prompt2はStaleになるが、そこから派生したReport Edit（Edit1→Edit2）は`completed`のまま残り、Review画面がstale警告なしに「現在の版」として古い分析結果に基づく編集内容を表示し続けていた。
   - 原因: `geo_report_execution_canonical_service.propagate_staleness`の`changed_stage == "prompt1"`分岐が、Aggregation/Prompt2までしかStale化しておらず、Report Edit連鎖への伝播が未実装のまま残っていた（コード内のdocstringに「まだ実装されていない」と明記されていた既知のギャップ）。
   - 修正: `_find_source_prompt2_execution_id`ヘルパーを新設し（既存の`prompt2`分岐のロジックを共通化）、`prompt1`分岐でもStale化したPrompt2から派生したReport Edit連鎖を辿ってStale化するようにした。
   - 併せてUI側の可視化不足も修正: `ReportEditPane`の編集履歴に各エントリのStaleバッジを追加し、`ReviewStepPanel`にCurrent ReportがStaleな場合の警告バナーを追加した（バックエンドは元々`status`を返していたが、フロントが利用していなかった）。

## 6. 残Minor Issues（記録のみ、今回は未修正）

- 新規プロジェクト作成後、一覧ページに留まったままで詳細ページへ自動遷移しない（作成した行を一覧からクリックする必要がある）。
- Report Edit Chatで「短くして」等の指示を出しても、LLMの解釈により期待したほど文章量が削減されないことがある（Report Edit機能自体の不具合ではなくLLM応答品質のばらつき）。
- Run作成直後、Run一覧のバッジがPreprocess開始前の一瞬の間も"running"と表示される（実処理はまだ何も走っていない）。

## 7. Version Isolation結果

- Design v1 pinned Run（Run 1）と Design v2 pinned Run（Run 2/3）の`design_version_id`は最後まで異なる値のまま。
- v2 Publish後もRun 1の`design_version_id`は書き換わらず、v1のまま固定。
- Narrative Master/Report Template資産は、v1→v2間で内容変更が無かったため同一asset_idのまま（Atomic Publishの「変更の無いAssetは再版しない」仕様通り）。

## 8. Continuity結果

- Run 3（Next Run、Run 2をbaselineに作成）の`baseline_prompt2_execution_id`/`previous_continuity_artifact_id`は作成時点のRun 2のCanonical Prompt2実行に固定。
- その後Run 2のCanonical Prompt2/Reportを複数回切り替えた（Revert、ChatGPT Import採用、Report Edit適用）が、Run 3の上記2フィールドは一切変化しなかった。
- Continue画面の表示（Design Version/Narrative Master/Report Template/Baseline Run/Previous Continuityの有無）は、DBのRun行の値と一致していることを確認。

## 9. Storage結果

- 本Phaseで生成した全Artifact（prompt1_input/output、aggregation_output、composition_result、final_report等）は全て1MB未満のため、設計通りcontent_text（gzip圧縮）経路を通過し、`storage_path`は使用されなかった。
- 大容量（閾値超過）ルーティングの実機確認はPhase 3で別途実施済み（1.3MB相当のペイロードで`storage_path`へのアップロード・sha256整合性検証・削除までを実Supabase Storageに対して確認）。本Phaseはこの結果を踏襲し、追加の大容量テストは行っていない。
- 既存のgzip圧縮content_text artifactの読み込み（後方互換）は、本Phase中に読み出した全既存Runのartifactが問題なく復号できていることで間接的に確認。

## 10. Application AI E2E

Design Chat（Signal/Query一括生成）→ Narrative Master AI生成 → Report Template AI生成 → Validate → Atomic Publish → Query Copy/Download → New Run（CSV Upload）→ Preprocess/Prompt1/Aggregation/Prompt2一括実行 → Final Report表示 → Report Edit（2回連鎖）→ Revert → Continue → Next Run作成まで、すべてブラウザ操作のみで完走。Prompt1/Prompt2/Execution/Canonical等の内部語彙をユーザーが理解する必要はなかった。

## 11. ChatGPT Fallback E2E

Prompt1 Package Export → （擬似）ChatGPT結果Import → Use as Current → 下流Aggregation/Prompt2/Report Editの連鎖Stale化を確認 → Prompt2 Package Export → （擬似）ChatGPT結果Import → Use as Current Report → Report Edit Chat（初回のみHTML→composition_result正規化が実行されたことをArtifactで確認）→ Edit適用まで完走。Application AI結果とChatGPT結果は常に別Executionとして保持され、Canonicalは常に一意だった。

## 12. Report Edit E2E

Prompt2 → Edit1 → Edit2の連鎖で、Edit2.parent_execution_id=Edit1、canonical_prompt2_execution_idは終始不変、canonical_report_execution_idのみEdit2へ移動することを確認。Edit履歴から元のPrompt2実行へのRevert（Use as Current）も正常動作。ChatGPT Import由来のReportでは初回Edit時のみHTML正規化が走り、以降はcomposition_result（構造化JSON）を再利用する設計通りの動作を確認。

## 13. Legacy Regression

Iranプロジェクト（`hormuz_calibration`）のLegacy Collection/Evidenceタブを開き、既存Run履歴・クラスタ一覧が本Phaseの変更後も問題なく表示されることを確認。Legacyタブの内部ロジックには一切変更を加えていない（表示ラベルへの"(Legacy)"付与のみ）。

## 14. Final Go / No-Go

### 判定: **CONDITIONAL GO**

- Core Flow（New Project → Design → Collect → Analyze → Compose → Review → Continue → Next Run）はブラウザ操作のみで完走する。
- Canonical / Version Isolation / Continuity / Storage / Report Editの整合性に関わるBLOCKER 3件・MAJOR 1件は本Phase中にすべて修正し、再検証済み。
- 残っているのはMinor Issue（6章参照）のみで、いずれもE2E完走を阻害しない表示・品質面の課題。

