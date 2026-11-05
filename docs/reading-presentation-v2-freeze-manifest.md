# READING_PRESENTATION_V2 — Freeze Manifest

**Freeze日**: 2026-10-02
**Freeze対象**:
- `geo_ios_situation_presentation_service.py`（Presentation層関数の追加分のみ——既存§1-11の関数は無変更）
- `eco-digest-spark/src/components/geo/geo-ios-situation-analysis-view.tsx`（全面再構成）
- `eco-digest-spark/src/lib/geoIosSituationPresentationApi.ts`（型追加）
- `eco-digest-spark/src/lib/i18n.tsx`（`geo.ios.situation.report.*`キー追加）
- `tests/test_geo_ios_situation_presentation_service.py`（新規テスト5件）

以降、この設計（5 Reading一覧→単一縦型レポートCard、Actor-first Primary/Peripheral分類、Situation Phases Timeline）のチューニングは行わない。新しい要件（Closest Alternative表示等）が生じた場合はv2.1として別途設計する。

---

## 背景：なぜ作られたか

Phase「5つの読み方案UI再設計」（2026-10-01）でActor-first Presentation（Primary/Peripheral Actor分類、Situation Phases）を追加したが、個々のReadingが独立した折りたたみCard群のまま残っていたため、「1本のレポートとして読める」という最終形には至っていなかった。ユーザーから、縦型レポート参考HTML（v7）をもとにした全面再設計の指示があり、既存Expert AI Core／schema／CQAS／Reading生成ロジックを一切変更せず、Presentation層のみで実現した。

## 設計原則（v2で確定した責務分離）

- **Expert AI Core**: 無変更。`geo_expert_engine.py`・`geo_expert_schema.py`・CQAS・Reading生成（main_scenario/alternative_scenarios/hypothesis_register）には一切触れていない。
- **Presentation層**: 既存の保存済みResponseを、新規LLM callなしで縦型レポートへ再構成するだけ。Actor分類（`build_actor_roles`）・状況推移（`build_situation_phases`）は2026-10-01に実装済みのものをそのまま再利用し、今回はラベル・配置のみ変更。
- **新規追加**: Evidence itemへのactor帰属（`_forced_primary_actor_name`を共有）と、既存だが未使用だった`response.business_impact`をScenario Impact Boxとして表示する`build_business_impact()`。どちらも既存fieldの決定論的な再構成であり、新しい事実・新しいActor・新しい関係性は生成していない。

## 7 Section構成（固定）

```
Header（番号＋Reading Tag＋Title＋採用ボタン）
1. 読み方詳細
2. 主要なステークホルダーと、その思惑（Primary Actor 1件=カード1枚、委員会等の架空sub-actorは作らない）
3. 局面に至るまでの状況推移（Timeline、ラベルをActorの判断軸に再設計）
4. 踏まえて、この読み方が有力になるポイント（連続パラグラフの論証、末尾にWeakening Evidenceを統合）
5. それを裏付ける動き（Actor＋日付のメタ行つきEvidence）
6. 追加調査案（最大3件）
7. この読み方で見えるシナリオ（reaction_chainの連続文＋MAIN限定のbusiness_impact）
```

各Sectionは中身が無ければ見出しごと省略する（空見出しバグを目視監査中に発見・修正済み）。

## Gold Case検証（実ブラウザ・実データ、3ケース）

- Russia Sanctions（1 Primary Actorのケース、0 Primary/全unclassifiedのケースの両方を確認）
- US-China Semiconductor（2 Primary Stakeholderの個別カード表示を確認）
- Hormuz（歴史的precedent・制度的記憶がActor以外のSectionにも分散表示されることを確認）

## Known Limitation（v2.1候補、ブロッカーではない）

- Closest Alternative（「このReadingと最も競合するのはReading 0Xである」）は未実装。内部データ（`closest_alternative`フラグ）自体は保持済みのため、Expert Sessionでの要望次第でv2.1として追加可能。
- Evidence headlineは既存`summary`（claim文）をそのまま使うため、やや長め。
- Actor Roleが`unclassified`な場合、見た目は`peripheral`と区別しない（安全側の仕様、意図通り）。

## Regression

- Backend full regression: 2140 passed, 1 failed（`test_resolve_reference_date_with_none_case_id_skips_query_and_falls_back_to_today`、2026-09-30から記録済みのUTC日付境界flaky test、今回と無関係）
- Frontend: TypeScript／ESLint／production buildすべてPASS

---

## Expert Session用 運用メモ（2026-10-02時点）

次フェーズ（Expert Session：実際の専門家によるReadingの評価・修正観察）に備え、以下のdev server状態を**維持したまま**にしている。

| コンポーネント | 起動コマンド | ポート | 状態 |
|---|---|---|---|
| Backend API | `python -m uvicorn api_server:app --port 8001`（プロジェクトルートで実行） | `127.0.0.1:8001` | 起動中 |
| Frontend | `npm run dev`（`eco-digest-spark/`で実行、Vite dev server） | `127.0.0.1:8080` | 起動中（他プロセスでポート使用中の場合8081/8082等へ自動繰り上がる場合あり） |

**確認用URL**: `http://localhost:8080/geo/ios/analysis/{sessionId}` の「2 読み方提案」タブ。

- この2プロセスは**Reading Presentation V2 Freeze状態のまま**動いている（上記Freeze対象ファイル以降、コード変更は行っていない）。
- 再起動すると上記ポート割り当てが変わる可能性があるため、Expert Session実施前に`curl http://127.0.0.1:8001/api/geo/theme-candidates`等で生存確認してから使うこと。
- 停止する場合は該当プロセスをkillするだけでよい（DBへの書き込みは伴わない）。

次にやるのは追加実装ではなく、**Expert Sessionのケース導線と評価項目の最終確認**。
