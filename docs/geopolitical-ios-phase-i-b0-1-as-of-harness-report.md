# Geopolitical Intelligence OS — Phase I-B0.1: As-Of Evaluation Harness 完了報告

日付: 2026-09-16

## 1. Proxy Architecture

`eval_harness/as_of_evaluation_client.py` に `AsOfEvaluationClient` を実装した。
本番`geo_db_client.SupabaseClient`と同じ`.select()`インターフェースを持つ
read-onlyプロキシで、production側(`geo_expert_engine.py`等)は一切変更していない
——`_relevant_rows_by_keywords`をはじめ全てのDBアクセスが`client.select(...)`という
単一インターフェース経由であることを確認済みのため、この差し替えだけで既存の
Retrieval/Source Pack構築ロジックすべてを変更なしに温存できた。

```
production code (unchanged)
    ↓ client.select(table, params)
AsOfEvaluationClient.select()
    ↓ table-specific temporal policy
    ↓ over-fetch → post-filter → re-apply caller's limit
real SupabaseClient.select()
    ↓
Postgres/PostgREST
```

write系(insert/update/delete/rpc)は既定で`WriteBlockedError`を送出する。ただし
実際のRetrospective Run(§10)では新規Analysis Session/Scenario Candidate行の
永続化自体は必要(これは「過去/未来の知識を読む」行為ではなく「今回の評価という
新しい事実を書く」行為であり、時間的漏洩リスクが無いため)、明示オプトイン
`allow_write_passthrough=True`でのみ書き込みを本物のクライアントへ委譲する設計とした。

## 2. Tables Covered

| カテゴリ | テーブル | ポリシー |
|---|---|---|
| 世界知識(時間フィルタ対象) | geo_documents | published_at<=T0 AND (fetched_at/created_at)<=T0、published_at欠落は除外 |
| | geo_events | start_at<=T0 AND created_at<=T0 |
| | geo_knowledge_items | created_at<=T0(主軸)、valid_from/valid_toでT0を挟む |
| | geo_relationships | geo_knowledge_itemsと同じ構造 |
| | geo_entities | active_from/active_to(存在期間)のみ、属性自体は不変扱い(下記§9参照) |
| | geo_observations | as_of<=T0 AND created_at<=T0 |
| | geo_evidence / geo_claim_evidence / geo_knowledge_item_claims / geo_source_registry / geo_indicator_definitions / geo_cases / geo_case_issues / geo_themes | created_at<=T0(汎用フォールバック) |
| Run自身の記録(フィルタ対象外) | geo_ios_analysis_sessions / geo_ios_scenario_candidates / geo_ios_analysis_designs / geo_expert_requests / geo_expert_responses | 常時許可(今回のRun自身が今日書いたデータであり、T0時点の世界知識ではないため) |
| 未定義 | 上記以外 | **Fail Closed**(0件を返す、audit reportへ`UNDEFINED_POLICY_FAIL_CLOSED`として記録) |

## 3. Temporal Policy by Table

上表参照。特記: `geo_knowledge_items`は単純な`as_of_date`ではなく`created_at`
（システムへの投入時点）を主軸にした——`as_of_date`は内容が指す時期であり、
「T0時点でシステムがその情報を持っていたか」とは別軸のため（ユーザー指示通り）。

## 4. Future Leakage Tests

`eval_harness/leakage_audit_bc_strategy.py`実施結果(`leakage_audit_bc_strategy_
result.json`):

- Proxy無し: Iran関連文書からT0(2026-02-19)より未来の文書が**9件**確認された
  (2026-05月付・2026-08月付を含む)
- Proxy有り: 同じクエリで未来文書の漏洩は**0件**
- **PASS**

実際のBC Strategy Retrospective Run自体でも、生成過程のaudit reportで
`geo_documents: excluded_future_count=8, excluded_unverified_count=5, included_count=0`
を記録し、漏洩防止が実際の生成中も機能したことを確認した。

## 5. Unsupported Historical Fields

| 項目 | 分類 | 理由 |
|---|---|---|
| `geo_entities.status` | temporal_unverified | ステータス自体の履歴バージョニングが無い。存在期間(active_from/active_to)のみ判定可能 |
| `geo_knowledge_items(actor_profile).current_posture` | safe(条件付き) | geo_knowledge_itemsのcreated_at<=T0ポリシーで基本的にはカバーされるが、「内容更新がsupersedes_idによる新版作成ではなく既存行のin-place更新で行われていないか」は未検証。残存する前提 |
| `geo_knowledge_items(media_profile)の"最新のメディア認識"相当フィールド` | temporal_unverified | 同上のメカニズム上の懸念に加え、Media Pack Builder自体の実装を今回個別追跡していない |

「影響は小さいだろう」という推測でのallowはしていない——上記はいずれも
明示的にtemporal_unverified/safe(条件付き)として記録し、Evaluation結果の
limitationsに残した。

## 6. BC Strategy Execution Result

Leakage Audit PASS後、本番`geo_ios_analysis_session_service`/
`geo_ios_expert_ai_adapter`関数を直接呼び出し（HTTPを経由しない、Production
コード無変更）、`AsOfEvaluationClient(as_of_date="2026-02-19")`を差し替えて実行。

結果: CQAS候補4件生成 → Top5(4件)表示 → Rank1選択 → Analysis Design v1生成。
**特筆すべき点**: `geo_documents`が0件しかヒットせず(8件が未来として除外、5件が
published_at欠落で除外)、`geo_events`も0件——ほぼ無根拠に近い状態での生成となった。

## 7. BC Strategy CQAS Evaluation

| 分類 | 論点 |
|---|---|
| validated_dimension | 攻撃オプション二分法(限定攻撃 vs 体制転換)、米中間選挙との接続、台湾抑止への波及 — いずれもトピックレベルでは捕捉 |
| generation_miss_candidate | **全ての固有アクター名が完全に欠落**(`key_actors=[]`、江口さん実資料は10名)。空母打撃部隊配備という具体的タイミング指標(江口さん独自の追加視点)も欠落。「侮辱されると激発するトランプ」という行動パターンも欠落 |
| ambiguous | 日本(高市)投資規模ミスマッチという論点——T0テーマに明示的に含めたにもかかわらずAI出力に一切現れず |

**重要な留保**: 検索結果が実質ゼロだったため、これらのmissが「CQAS推論の限界」なのか
「単に検索できる材料が無かったため正しく具体化を避けた(Evidence Discipline上は適切な
挙動)」なのか、切り分けが困難。各判定のconfidenceを個別に記録し(LOW〜MEDIUM中心)、
一般化した性能主張はしていない。

## 8. Hungaryとのcross-case findings

| パターン | Hungary | BC Strategy | 判定 |
|---|---|---|---|
| Actor選定の狭さ | 2 vs 5(実専門家) | 0 vs 10(実専門家) | **recurring**(2/2) |
| 専門家の最も独自性の高い視点が生成段階で完全欠落 | Trump/MAGA連動 | 空母配備タイミング指標 | **recurring**(2/2) |
| investigation_questionが入力テーマの転記のまま(再フレーミング無し) | 該当 | 該当 | **recurring**(2/2) |
| 検索結果の枯渇 | 該当なし(相応の内容が生成された) | 該当(ほぼゼロ) | one-off(BC Strategy固有) |
| 「ポピュリズムの終焉ではない」という専門家独自のニュアンス | 該当 | 該当なし | case-specific(Hungary固有) |

n=2の直行的知見であり、**これだけを根拠にCQAS/Prompt/Rankingは一切変更していない**。

## 9. Limitations

- サンプル数2、統計的一般化は不可(Small Sample Warning)。
- BC Strategyの評価はHungaryより低い確信度(情報枯渇の影響を排除できないため)。
- `geo_entities.status`等、履歴再現不能なフィールドが一部残存(§5参照)。
- Actor/Media Intelligence Pack Builderの全実装経路を個別追跡はしていない(共通
  `client.select()`インターフェース経由という設計パターンから、カバーされている
  蓋然性は高いと判断したが、100%の確認はしていない)。
- Hungaryケースについては、Corpus Audit自体(Explore agentの報告)が生成前に
  Eguchi実資料の一部内容を私自身に開示してしまっており、完全なBlindingではない
  （T0テーマ文は客観的事実のみで構成し、専門家の独自フレーミング言語を意図的に
  避けることで影響を最小化した、という限定的な緩和に留まる）。

## 10. Regression Artifact Location

`docs/geopolitical-ios-phase-i-b-evidence/phase_ib0_regression_artifact.json`
——両ケースの完全なCQAS全候補・Top5・Design・評価ラベル・limitationsを自己完結的に
保存(現在のDB状態に依存しない。実際、Phase I-Aのpilot用report_projectが本セッション
中に消えるという事象が既に発生しており、この教訓を反映した)。

個別ファイル:
- `docs/geopolitical-ios-phase-i-b-evidence/phase_ib0_hungary_result.json` / `_evaluation.json`
- `eval_harness/phase_ib0_1_bc_strategy_result.json` / `_evaluation.json`
- `eval_harness/leakage_audit_bc_strategy_result.json`
- `eval_harness/as_of_evaluation_client.py`(Harness本体、将来のRegression Evalでも再利用可能)

## 11. I-B1 Evaluation Package

仕様書: `docs/geopolitical-ios-phase-i-b1-expert-validation-package-spec.md`。
まだ実データでのFreezeは実施していない(実専門家レビューの機会が確保され、新規
テーマでTheme→CQAS→Top5→Design→Draft→AI Reviewの全段階を通した時点で着手する)。

## 12. Recommendation Before Phase J

1. **CQAS/Rankingのチューニングはまだ推奨しない**——n=2のRetrospective Proxy
   Evidenceのみでは根拠として弱く、かつBC Strategyの結果は情報枯渇の影響と
   切り分けられていない。
2. Phase Jへ進む前に、**Actor選定の狭さ**(2/2ケースで再現)について、最低限
   もう1〜2ケースの独立した検証(理想は実Phase I-B1、次善はas-of Harnessを
   使った追加Retrospectiveケース)を推奨する。
3. `geo_entities`の属性履歴バージョニング欠如は、Harness自体の限界としてだけ
   でなく、本番システム自体の設計課題としても記録価値がある(別Issue化を推奨、
   今回は変更していない)。
4. 実専門家レビューの機会確保が、Phase I-B正式完了への唯一の道筋であることに
   変わりはない——Retrospective Proxyは補助的Evidenceの域を出ない。
