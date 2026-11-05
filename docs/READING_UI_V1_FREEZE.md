# READING_UI_V1 Freeze Manifest

| 項目 | 内容 |
|---|---|
| Freeze名 | **READING_UI_V1** |
| Freeze日時 | 2026-10-05 09:07 (JST, +0900) |
| 承認 | 最終Presentation Quality Pass結果を承認（ユーザー指示、2026-10-05） |
| Snapshot | `freeze/READING_UI_V1/`（本Manifestの複製は `freeze/READING_UI_V1/MANIFEST.md`） |

## 目的

Expert AIの読み方を「AIレポート」ではなく「専門家レビュー」として提示するUIの固定版。
インテリジェンス推進部マネージャーによる、Expert AI Reading体験の価値評価用。

推論コア・Frozen Response自体は既存Freezeを維持しており、**本Freezeの対象外**。
本Freezeはその上のPresentation層（表示変換・UI・キャッシュ）のみ。

## Freeze対象（機能）

Expert Review Reading Card / Expert Review Writing Transformation / Perspective Label /
Stakeholder Tag / 表面・裏側 / ここまでの流れ / Evidence Meaning / Weakening Evidence /
Switch Condition（崩れる条件）/ Watchpoints / Layer表示 / Presentation Sanitization /
Expert Review Cache / 新UI・旧UI fallback制御

## 対象ファイル（プロジェクト直下 `projects/geopolitical_monitor_dashboard/` からの相対パス）

SHA256は `freeze/READING_UI_V1/src/` 配下の複製と、Freeze時点の原本の両方で一致を確認済み。

### Backend
| ファイル | 区分 | SHA256 |
|---|---|---|
| `geo_ios_expert_review_view.py` | 新規 | `dfce95008f1b7240c34a06965446409b93e8fd64af3f381bedfe74929043029c` |
| `geo_ios_reading_presentation_transform_service.py` | 変更（末尾に追記） | `57f038482f791f5bdaad769d50a0bdeacf09fe135fc5dc9c776a8426f2d31905` |
| `geo_ios_situation_presentation_api.py` | 変更 | `bda97037354f22a0dca32d7af12518fc5df9549cafffa1c35c3c3fb2da8a868c` |
| `scripts/generate_expert_review_cache.py` | 新規 | `b6715b8609bfbb3c75db7ddbeb4bc798b8545730effb50dddb441c91019da2f2` |

### Frontend
| ファイル | 区分 | SHA256 |
|---|---|---|
| `eco-digest-spark/src/components/geo/geo-ios-reading-presentation-v2.tsx` | 変更（末尾に`ExpertReviewReport`追加） | `490128428654fc7cca5b3f26691eefd0e5c1be8fbe0df74e7c1ce6bef8d46cc2` |
| `eco-digest-spark/src/components/geo/geo-ios-situation-analysis-view.tsx` | 変更 | `400c6e64aa21993dcaa687e992a6c488643f52a1e1296ec670095dbbe5b1b71f` |
| `eco-digest-spark/src/lib/geoIosSituationPresentationApi.ts` | 変更 | `175040ac3ee7b757ff5c5365de05768bb9815fb523fb44b5fffa373ec716e049` |
| `eco-digest-spark/src/routes/geo.situation-presentation.$responseId.tsx` | 変更（response_idは`?debug=1`時のみ表示） | `d0f19efaf725fa493ddf189d0a3418a89c111ec3130a67c83bf41642cb012c14` |

### Test
| ファイル | 区分 | SHA256 |
|---|---|---|
| `tests/conftest.py` | 新規（Expert Review LLMを既定で無効化、cacheをtmp_pathへ） | `ecc83b8e6e083349f35885af59246c00917b99d2a7edb9308443c2f5509754e1` |
| `tests/test_geo_ios_expert_review.py` | 新規 | `87c0bf9bad3030fb2cc75432dbd65b3ad442b0a5f32e4bd273ea64046d4ca836` |
| `tests/test_geo_ios_expert_review_quality_pass.py` | 新規 | `2b19ab246d744b42e29cb1d33d7ce18b10cc70b1d06d98d989a7c79cd3802591` |
| `tests/test_geo_ios_situation_presentation_api.py` | 変更（旧LLM経路のFallbackテスト調整＋1件追加） | `9160731868e254d86019b73df43083de9e5d65c7ce73055848b65c36167515e7` |

### 依存（今回未変更・参照用、`freeze/READING_UI_V1/dependencies/`に複製）
| ファイル | SHA256 |
|---|---|
| `geo_ios_situation_presentation_service.py` | `380814c5dc9295f2dcdf70a0d8c51682b70bbd0840a5c7d49cd7e4f50c66cfef` |
| `geo_ios_analysis_session_service.py` | `e0339083886c983f50e2286ae66b5ce550829b5ff2a2da4f8d91c7ebdff4a21e` |

### Writing Guide / Prompt（`freeze/READING_UI_V1/`配下）
| ファイル | SHA256 |
|---|---|
| `docs/READING_UI_V1_WRITING_GUIDE.md`（原本は `docs/READING_UI_V1_WRITING_GUIDE.md`） | `a5ad3c9995e4696725f6fae6aafff75e8e20679433ef1e36767eb43c16fe8d26` |
| `prompt/expert_review_system_prompt.txt`（System Prompt全文） | `e64f3e2ebea1ac078aec17ef0e5443ec47289418422417845f7d10aa61197246` |
| `prompt/expert_review_output_schema.json`（出力JSON Schema） | `292263af958dd5a65fcd8a4b352ddaf4a89ba334d6f914fb1d23e69d0f031284` |
| `prompt/versions.json`（各version・禁止表現リスト・再試行上限） | `576e98e79a308b655b53b364ba14fa98ee7db8c6dcbebe1e2ca604ef1c9e2b90` |

`freeze/READING_UI_V1/SHA256SUMS.txt`（25ファイル分、SHA256 `5adc9c7c1103479ba4b50ff73d2ac0ab9f4849d018dd1b2d8f6607aba20e9b07`）に
snapshot全体のハッシュがある。

## Version

| 項目 | 値 |
|---|---|
| Presentation Version（既存、変更なし） | `situation_analysis_v1` |
| Expert Review View Version | `expert_review_v1` |
| **Expert Review Prompt Version** | `expert_review_comment_v4_flow_evidence` |
| **Cache Version**（ファイル名・指紋に含まれる） | 同上（Prompt Versionと一体） |
| Writing Guide Version | `READING_UI_V1_WRITING_GUIDE`（素材収集zip 約25通から抽出、2026-10-04作成。要点はSystem Promptに反映済み） |
| 出力検証の再試行上限 | 2回（検証NGの読み／流れのみ再生成） |

## Cache Key仕様

- 保存先: `expert_review_cache/`（環境変数 `GEO_EXPERT_REVIEW_CACHE_DIR` で変更可）
- ファイル名: `{EXPERT_REVIEW_PROMPT_VERSION}__{fingerprint}.json`
- fingerprint: `sha256( JSON{ prompt_version, inputs } )`（`sort_keys=True`, `ensure_ascii=False`）
  - inputs＝保存済み応答から組み立てた、局面・最初の違和感・局面の力学・Actor情報・関係・各Readingの要旨/証拠材料
  - 同じ保存済み応答なら同じキー → 同じ出力（画面を開くたびに生成しない）
  - Writing Guide/Promptを更新したら **必ず `EXPERT_REVIEW_PROMPT_VERSION` を上げる**（別キーになり古いキャッシュは自動で無効）
- 内容: `fingerprint / prompt_version / created_at / meta / flow(局面共通の流れ) / readings[title, comment, pattern, evidence_notes]`
- GET（画面表示）はキャッシュのみ参照しLLMを呼ばない。未作成の場合のみ `comment_status=pending` とし、
  フロントがPOST `/expert-review` で一度だけ生成する（Gold 4件は事前生成済みのため発生しない）

## Gold確認ケースと事前生成Cache

| ケース | response_id | カード数 | LLMコメント | 流れ | Cacheファイル（`expert_review_cache/`） |
|---|---|---|---|---|---|
| Hormuz | `23a1eebb-72fe-42e8-aba7-c6af1b200799` | 5 | 5/5 | 5 steps | `…__8ef40f69…json` ほか下記参照 |
| US-China Semiconductor / AI | `eed932e3-42e1-4ea1-947a-8a684d59ad2f` | 5 | 5/5 | 4 steps | |
| Saudi-Israel | `914191ea-9fe6-4efd-9ab5-554428fe7305` | 3 | 3/3 | 4 steps | |
| TSMC | `1ab3abe0-6a8b-435f-b54b-1cd79f845d71` | 3 | 3/3 | 5 steps | |

現行Cache（Freeze時点・4件。同じ内容を `freeze/READING_UI_V1/gold_cache/` にも保存）:

| ファイル名（`expert_review_comment_v4_flow_evidence__` に続く指紋） | SHA256 |
|---|---|
| `258ce4302fe2d29434d67bcd4de254c12522b6ac6f879fce2ee69d4f0f4ef511` | `12e1b9975c788824d485c1dbfe6116ee40031ae1e0f405e608781efc55b48949` |
| `7fc7201b1b06c8dc8fc6352312bd1d8353b303d5b5de7d788ea1fd468ddb728e` | `ac68365969354d3d952e56b6010375b4e61bd51793f3004b9ef5fae6df52caee` |
| `8ef40f698894542942ab1102c7d8f9c90c89c167203f200b15b3e635138d3926` | `70a480ab07bb10d5f845603dc74ccd9df51978b602f68e060325e508da81713e` |
| `e502ed540fce0fb6640efc66580b02edbf9fdc77a1628914fd25596018ec76f5` | `8d5b133b04e16031091d2d675cedee5024c398df4bbd87882802c00c49e97f3a` |

Gold 4件のGET結果（presentation JSONのSHA256、`verification/gold_baseline_before_freeze.json`）:

| ケース | SHA256 |
|---|---|
| Hormuz | `561e30e69180b18584ce7f402886c70be6a9cbda113f451a8c0cdcc8133e90e1` |
| US-China Semiconductor / AI | `c896253cc46f39bef2855595d6a8b4c7b9e7762b24b0dd211f9585437387bb0e` |
| Saudi-Israel | `7a79c4cfc653ab4ab8400871ec3670dbd8bf62512b224765e0b2e09f3c21cfe3` |
| TSMC | `ad4fc060ed3601aca9283c2b07e16c848ca190b36d442feaf8972ae72d00b5d6` |

旧キャッシュ（`expert_review_cache_old_versions/` 17件、`expert_review_cache_v1_discarded/` 1件）は
Snapshot・Manifest作成後に削除済み。削除前後でGold 4件のGET結果ハッシュは**完全一致**を確認。

## Validation（Freeze時点）

| 項目 | 結果 |
|---|---|
| Python tests | **2428 PASS**（`pytest tests`、既存＋今回追加分） |
| Frontend typecheck（`tsc --noEmit`） | **PASS**（エラー0） |
| Browser Gold 4（Playwright、全カード展開） | **PASS**（JSエラー0、内部表現の漏れ0、画面表示時のLLM生成リクエスト0） |
| Snapshot整合性（`sha256sum -c SHA256SUMS.txt`） | 25/25 OK |

## Regression確認

- Primary / Alternative の順序: **変更なし**（Gold 4件、Before/After比較）
- Evidence ID: **変更なし**（支持・弱化とも、Gold 4件）
- Frozen Response: **変更なし**（読み取りのみ。Expert AI Core・保存済み応答は書き換えない）
- 同じresponse_idの繰り返しGET: **決定論的**（2回取得のJSONが完全一致、Gold 4件）
- UUID／ファイルパス／内部用語の漏れ: **なし**（画面全文の総点検＋実ブラウザで確認。製品名・略称（H200/GPU/BIS/MATCH/JCPOA/TSMC等）は保持する回帰テストあり）
- キャッシュ済みGET時のLLM呼び出し: **0回**（`verification/freeze_check.py` でLLM呼び出しを禁止して4件取得、`llm_calls_during_GET=0`）

再検証手順（プロジェクト直下で）:

```
python freeze/READING_UI_V1/verification/freeze_check.py out.json   # Gold 4件のハッシュ・LLM呼び出し数
cd freeze/READING_UI_V1 && sha256sum -c SHA256SUMS.txt               # snapshot整合性
# 原本とsnapshotの一致: 各 src/<パス> を原本と cmp する
```

## LLM呼び出し構成

- Expert Review Transformation: 1回の呼び出しで `title・comment・evidence_notes・局面共通flow` を同時生成。
  検証NGの読み／流れのみ修正指示付きで最大2回再試行。Gold 4件の初回生成は各1〜3回（最終生成は各2回）。キャッシュ後は0回。
- Sessionページの旧「6段落Presentation Transformation」は、新UI（`expert_review`が付く場合）では**実行しない**（LLM経路を1本化）。
  新UI用ViewのBuild失敗時のみ、旧UI Fallbackとして従来どおり使われる。REVIEW MODEは従来表示のまま（対象外）。

## Known Limitations

- Hormuz Primary（本線）には保存済みの構造化Evidenceがなく、「この読みを支える動き」は表示されない（データ制約）。
- 「ここまでの流れ」の品質は、保存済み素材（`dynamics`等）の粒度に依存する。Gold 4件にはTrajectory / Foundation Contextが含まれていない。
- 代替Readingの「強まる／崩れる条件」は観測された証拠ではないため証拠欄には出さず、「崩れる条件」「次に見るもの」にのみ出す。
- Cacheは現在file based。環境により永続性が異なる（Dockerでは事前生成分をimageへ同梱するか、`GEO_EXPERT_REVIEW_CACHE_DIR` を永続ボリュームへ向ける）。
- **DB persistence（response_id + presentation version をキーにしたDB永続化）はFuture Work。**
- 原文に由来する英語の一般語（例: `Taiwan`）等、固有名詞以外の内部表現は除去しているが、新しい表記ゆれは今後も出うる。

## Freeze後の変更禁止（マネージャー評価が終わるまで）

以下は原則変更しない。評価中に気になった点はその場で修正せず、`docs/READING_UI_V1_FEEDBACK.md` に記録する。
評価終了後にまとめて READING_UI_V1_1 または次版として検討する。

- Expert Review Prompt / Writing Guide
- Reading Card構造 / Layer構造
- Evidence Meaning生成 / Trajectory Transformation
- Sanitization
- Cache Version（`EXPERT_REVIEW_PROMPT_VERSION`）
