# Expert Session — Frozen Case Registry（S0確定版）

**確定日**: 2026-10-02
**確定方法**: DB上のケース名fuzzy searchを信頼せず、Freeze関連artifact（`docs/expert-ai-validation/r1-blind-cases/raw/*.json`、`docs/expert-ai-validation/r1-human-check-cases/raw/*.json`、`docs/session1-hormuz-toll-case-pack/`、`docs/session2-us-china-semicon-case-pack/`）に埋め込まれた`request_id`/`response_id`、またはquery_textのSHA256フィンガープリント照合のみを根拠とする。

この表に無い`response_id`をGold/Calibrationとして扱わない。新規生成・類似ケースでの代替は行わない。

## Gold Cases

| case_key | title | response_id | 根拠 | 備考 |
|---|---|---|---|---|
| gold_hormuz | Hormuz（Blockade, Session3） | `23a1eebb-72fe-42e8-aba7-c6af1b200799` | `docs/hormuz-blockade-case-pack/HORMUZ_BLOCKADE_SESSION3_*` | Session1 Toll Case（`852b7278-14d7-4e54-8de5-909c2ac6ca1a`）は比較用として保持、Session Registryには不採用 |
| gold_us_china_semicon | US–China Semiconductor/AI | `eed932e3-42e1-4ea1-947a-8a684d59ad2f` | `docs/session2-us-china-semicon-case-pack/US_CHINA_AI_SEMICON_SESSION2_BLIND_INPUT.md`とSHA256完全一致確認済み（request_id `d09cdceb-743c-4a7c-a46c-bc7e1d6c02a0`） | 同一入力の別run（request `a18b00d4...` / response `2bd701be-9ca3-457d-ba33-f586f8655743`、2026-09-25実行）は不採用として記録のみ |
| gold_nato | NATO | `cd1ed3a4-70b1-4001-9643-253eccbcd532` | `docs/expert-ai-validation/r1-blind-cases/raw/r1_blind_case_nato_burden_sharing_trump_macron_merz_result.json`（Chunk6 Case2） | request_id `69a699a9-ff46-4824-9c3f-19cd6fbac153` |
| gold_saudi_israel | Saudi–Israel | `914191ea-9fe6-4efd-9ab5-554428fe7305` | `docs/expert-ai-validation/r1-human-check-cases/raw/r1_human_check_saudi_us_israel_realignment_result.json`（Case2「サウジ・米国・イスラエルの関係再編」） | request_id `81e143db-3bbd-440a-8091-5d5e47a45cf6`。Netanyahu単体/MBS単体の旧「Case3/Case4」（response `526ad7ba...`/`c00c9a3e...`、2026-08-31）は別のActor Intelligence回帰ベンチマークであり不採用 |
| gold_tsmc | TSMC | `1ab3abe0-6a8b-435f-b54b-1cd79f845d71` | `docs/expert-ai-validation/r1-blind-cases/raw/r1_blind_case_tsmc_export_control_compliance_result.json`（Chunk6 Case3） | request_id `4de52ab6-61cf-426d-9ca5-653efb3e5334` |
| gold_uae_saudi_qatar | UAE–Saudi–Qatar | **UNRESOLVED（欠番）** | DB全件・docs全文・全case-packを捜索したが該当request/response/case-packが存在しない | 新規生成・類似ケース代替は禁止。発見され次第復帰。当面Gold 5 + Calibration 2でセッション実施 |

## Calibration Cases

| case_key | title | response_id | 根拠 |
|---|---|---|---|
| calib_takaichi | 高市政権 / 経済安全保障 | `ace4bb0a-b303-418b-a550-3617c7cfed86` | `docs/expert-ai-validation/r1-blind-cases/raw/r1_blind_case_takaichi_semiconductor_economic_security_result.json`（Chunk6 Case1）、request_id `2d2da1b2-2439-4f68-8042-1b5c5e06190f` |
| calib_scs | South China Sea | `304ee051-7a16-46a2-a317-ebc65862b5ce` | `docs/expert-ai-validation/r1-blind-cases/raw/r1_blind_case_taiwan_strait_scs_incident_media_divergence_result.json`（Chunk6 Case5、セカンド・トーマス礁v2）、request_id `cbebfd6a-f349-483c-9d60-9313b4bc1869` |

## 現状のv2表示可否（S1着手前の状態）

| case_key | IOS Analysis Session wrapping | v2表示（S1着手前） |
|---|---|---|
| gold_hormuz | あり（`147534c3-2f71-4649-8fd1-e5f86207acb2`） | v2（`/geo/ios/analysis/{sessionId}`） |
| gold_us_china_semicon | あり（`9bb7ea7b-468a-4073-b5f4-5ebc11f856d8`） | v2 |
| gold_nato | なし | 旧UI（`/geo/situation-presentation/{responseId}`）のみ |
| gold_saudi_israel | なし | 旧UIのみ |
| gold_tsmc | なし | 旧UIのみ |
| calib_takaichi | なし | 旧UIのみ |
| calib_scs | なし | 旧UIのみ |

→ Phase S1（direct response_id routeでのReading Presentation v2共通化）により、Session wrapping無しの5件も含め全件v2で表示可能にする。

## Phase S1実施結果（2026-10-02、完了）

`/geo/situation-presentation/{response_id}`（Gold Case確認用route）を、IOS Analysis Session route（`/geo/ios/analysis/{sessionId}`）と同じReading Presentation v2 renderer（`geo-ios-reading-presentation-v2.tsx`、新規共有モジュール）で描画するよう変更した。Expert AI再実行・Analysis Session新規生成・response_id置換は一切行っていない（バックエンドは無変更、既存`build_situation_presentation()`をそのまま使用）。

- gold_nato / gold_tsmc / gold_saudi_israel / calib_takaichi / calib_scsの5件全てで実ブラウザ確認済み: v2表示・5 Reading切替・Timeline/Evidence/Research/Scenario表示、バックエンド呼び出しはGET 1回のみ（LLM call 0 / DB write 0）。
- gold_hormuz（IOS Session route、Frozen側）で回帰確認済み: 表示・挙動に変化なし、GETのみ。
- 実装中に発見・修正した実バグ: `buildFallbackReactionChain`が、Session未ラップ応答（`main_scenario`/`alternative_scenarios`からの合成candidateで`title`/`key_actors`が無い場合）で、シナリオ文の主語が文字列"null"になっていた。Actor名が無い場合はそのchain自体を省略するよう修正（新しい文章は作文しない、既存の「無ければ省く」方針を踏襲）。
- 既知の未解消の表示課題（Do-Not-Touch範囲＝Evidence Bindingのため今回は修正せず、観察のみ記録）: TSMC/高市のEvidence要約テキストの一部に、元データ側の文言欠落・JSON残骸と見られる軽微な乱れ（「は、」から始まる文、末尾の「}]。」等）を確認。Presentation層のバグではない。
- Regression: TypeScript 0 error / ESLint 0 error（既存のreact-refresh警告のみ、新規ではない）/ production build成功。

## Phase S2-S3実施結果（2026-10-02、完了）

Expert Session Capture V1（`geo_expert_session_case_runs`/`geo_expert_session_evaluations`、[sql/2026-10-02_geo_expert_session_capture.sql](sql/2026-10-02_geo_expert_session_capture.sql)）を実装し、実DB上でGold 1ケース（NATO）・Calibration 2ケース（高市/South China Sea）のE2Eを完走した。Session Home（`/geo/expert-session`）とCase Flow（`/geo/expert-session/{caseKey}`）の2ルートを新設し、Reading表示はS1の`ReadingPresentationV2Report`をそのまま再利用（二重実装なし）。

- Blind Integrity: 実ブラウザのnetwork response監視で、Calibration Caseが`not_started`/`in_progress`/`locked`の全フェーズで`response_id`キー自体が応答に存在しないこと、`revealed`後にのみ出現することを確認（API単体テストに加えて実DB・実ブラウザでも確認済み）。
- Server-side Lock: Lock後のExpert First Input更新が403で拒否されることを実DBで確認。Lock前の必須3項目チェック（422）も確認。
- Identity Isolation: 2つの異なるcredential（別のexpert_identity）で同じGold Caseを開き、別々のrun（別run_id、互いの評価データが見えない・上書きされない）になることを実DBで確認。
- 実装中に発見・修正したバグ2件: ①`IdentityBox`のSSR/クライアント間hydration mismatch（localStorageをrender本体で直接読んでいたため）→useEffectでのマウント後補正に修正。②親Route（`geo.expert-session.tsx`）に`<Outlet/>`分岐が無く、Case詳細へURL遷移してもHome画面の中身が表示され続ける不具合（`geo.ios.analysis.tsx`と同じ既知パターン）→同じ対処を適用。
- Regression: バックエンド新規30件PASS、既存回帰2171件PASS、フロントエンドTypeScript/ESLint/production build全てクリーン。

**Verdict: EXPERT SESSION CAPTURE V1 READY**
