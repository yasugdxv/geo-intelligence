# Actor Retrieval Pipeline監査 — Original Afterでの「劣化」の原因究明

## 結論（先に）

**本番コードのバグではない。DBデータも壊れていない。原因は、Original Afterの
Benchmark Runnerスクリプトが`actor_profile_retrieval_status`を明示的に指定せず、
関数既定値`"available"`のまま`run_expert_request_staged`を呼び出していたこと。**
Neutral v3のRunnerは（現存しないため直接確認はできないが）候補データを含む
テストモード条件で実行されていたと推定され、Original Afterのみ条件が本番相当に
戻ってしまっていた。

修正は**Benchmark Runner側のみ**でよく、`geo_actor_intelligence_service.py`
等の本番コードは一切変更しない。本番運用の既定値`retrieval_status="available"`は
維持する。

---

## 1. DB状態の直接確認

`geo_knowledge_items`の`knowledge_type=actor_profile`全16件、`knowledge_type=precedent`
全53件を直接クエリした（2026-09-01実施）。

- **precedent（Historical Episode）53件は全件、例外なく`status=candidate`・
  `retrieval_status=restricted`**。`available`版は1件も存在しない。
- **actor_profile 16件の内訳**:
  - Trump/Putin/Xi/Kim Jong Unの4名は、2種類のレコードを持つ:
    - 旧: `status=active`・`retrieval_status=available`（2026-08-28作成、version=3）
    - 新: `status=candidate`・`retrieval_status=restricted`（2026-08-30作成、version=1）
  - Netanyahu/MBS/Zelenskyy/Macron/Merz/高市早苗の6名は、
    **`status=candidate`・`retrieval_status=restricted`の1件のみ**（2026-08-30作成）。
    `active`/`available`版が最初から存在しない。

いずれのレコードも`updated_at`が2026-08-30〜31時点で止まっており、
**Original After実行日(2026-09-01)にDBが変化した形跡は無い**。

## 2. Entity Resolutionの確認

`geo_entities`でMohammed bin Salmanを検索すると、`entity_id=
c47c90e6-eda1-4f56-9c3e-2b3206fde945`、`canonical_name="Mohammed bin Salman"`、
`external_ids.actor_intelligence_id="mohammed_bin_salman"`が正しく登録されている
ことを確認した。Original Afterで`actor_ref`が生UUID（`c47c90e6-...`）のまま出力
された事象は、Entity解決そのものの失敗ではなく、**Actor Intelligence Pack自体が
空だったため、LLMが人間可読な名前と紐付けるための材料を持てなかった**ことの
下流症状と判断する。

## 3. 決定的再現テスト（Azure呼び出し無し、ローカルのみ）

`geo_actor_intelligence_service.get_actor_profile_item_by_entity_id` /
`_relevant_precedents_for_entity`を、`retrieval_status="available"`
（Original Afterの実際の実行条件）と`retrieval_status=None`（制限なし）の
両方で、MBS/Netanyahu/Putinの3名について直接呼び出して比較した。

| Actor | `retrieval_status="available"` | `retrieval_status=None` |
|---|---|---|
| MBS | profile=**見つからず**、precedent=**0件** | profile=1件(candidate/restricted)、precedent=**2件** |
| Netanyahu | profile=**見つからず**、precedent=**0件** | profile=1件(candidate/restricted)、precedent=**2件** |
| Putin | profile=1件(active/available、見つかる)、precedent=**0件** | profile=同じ1件、precedent=**2件** |

Putinの`retrieval_status=None`時のprecedent IDは`c7582597-f5fe-4bf9-a521-
b9cbac5e7dd8`・`d0ef206d-d8d0-4697-8781-bc310387fc31`——これは**Neutral v3の
Putin結果に実際に出現したhistorical_episode_idsと完全一致**する。これにより、
「Neutral v3は候補データを含むテストモード条件で実行されていた」という推定が
裏付けられた。

生データ: `diagnosis_mbs_netanyahu_putin.json`（本ディレクトリ）。

## 4. なぜ「劣化」に見えたか（症状の再分類）

| 症状 | 原因 |
|---|---|
| historical_episode_ids: 2→0（全Actor共通） | precedent全53件が`retrieval_status=restricted`のみで、Runnerの`retrieval_status="available"`縛りに掛からなくなった |
| situation_actor_assessment: 4-5件→0件 | 同上（Actor Intelligence Packが痩せた結果、Stage2がAssessment生成の材料を失った可能性が高い。Stage2ロジック自体は今回検証対象外） |
| Netanyahu/MBSがprofile_source=none_available | 両名の`actor_profile`が`candidate/restricted`の1件のみで、`available`版が最初から存在しない |
| MBSの`actor_ref`が生UUID | 上記の下流症状（LLMに名前紐付けの材料が無かった） |

**「DBデータが昨日と今日で変わった」「Entity Resolutionが壊れた」という当初の
懸念はいずれも該当しなかった**。原因はRunner側の1パラメータのみ。

## 5. 対応方針

- 本番コード（`geo_actor_intelligence_service.py`含む）は**無変更**。本番の既定値
  `retrieval_status="available"`もそのまま維持する。
- Benchmark RunnerのみNeutral v3と同一の検証用条件
  （`actor_profile_retrieval_status=None`、`actor_profile_lookup_statuses=
  ("active","candidate")`）に固定し、実行直後にRetrieval Snapshot
  （本レポート3章の3 Actor分の値と一致するか）をログ出力してから
  Original 5-case Afterを再実行する。
- 初回Original After（`docs/expert-ai-validation/original_after/`）は
  `INVALID_FOR_REASONING_COMPARISON / RUNNER_CONFIGURATION_ERROR`として保持し、
  正式Before/After比較からは除外する。ただしEvidence Discipline系の汎化・
  unsupported confidence=0・Control Structure補正・CallBudget遵守は参考結果として
  有効のまま扱う（これらはActor Intelligenceの充足度に依存しない観測のため）。
