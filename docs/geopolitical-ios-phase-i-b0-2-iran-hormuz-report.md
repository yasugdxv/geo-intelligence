# Geopolitical Intelligence OS — Phase I-B0.2: Additional Retrospective (Iran/Hormuz) 完了報告

日付: 2026-09-16

## 0. 位置づけ

Phase I-B1(正式Expert Validation)ではない。引き続きretrospective proxy /
directional evidence。CQAS/Prompt/Expert AIロジックは無変更。Phase Jへは
自動的に進まない(§14参照)。

## 1. Evaluation Harness Commit Hash

- `99bc9dd` = system-under-test baseline(CQAS/Expert AI/Report Studio含む)
- `649625d` = evaluation-harness baseline(Snapshot機構・Taxonomy等)
- 本Phase(I-B0.2)の成果物はまだ未commit(本報告後、確定依頼を待つ)

## 2. Iran T0 Rationale

推奨通りT0=2026-05-14(M086)を採用。ただしT0入力自体にはM086を一切使わず、
**M086より7週間早い3/25時点のR006→R002(Pollack/Vatanka、MEI面談の外部専門家
分析、Tier1 Observed Answer Angle)**と、基本的な状況設定のみでテーマ文を構成した。
M086の結論・棄却理由・独自指標・framingはいずれも使用していない。イスラマバード
MoU(6月、T0より未来)についても、DB内に実在する未来文書であることを確認した上で
テーマ文に含めなかった。

## 3. Leakage Audit

`eval_harness/leakage_audit_iran.py`実施(Iran/Hormuz/Khamenei/Tehranの4キーワード、
geo_documents・geo_events・geo_knowledge_items全てを対象)。**PASS**。

Proxy無しでは2026年7-8月付の実在文書(Islamabad MoU関連、米中経由の迂回輸送、
Trump「交渉せず、封鎖継続」等)が多数ヒットしたが、Proxy有りでは全カテゴリで
漏洩0件。

## 4. Evidence Availability

`geo_documents`: included=0, excluded_future=19, excluded_unverified=11。
`geo_events`: included=0, excluded_future=1。**BC Strategyと同様の証拠枯渇**。
DB内のIran/Hormuz関連文書は2026-05-15付が最古(T0の1日後)で、T0=2026-05-14を
含むいかなる2〜5月台のT0を選んでも、このDBでは同様の枯渇が起きたと考えられる
(このDatasetの構造的特徴であり、T0選択の巧拙の問題ではない)。

## 5. CQAS Candidate Coverage

3候補生成(全件Top5扱い、5件未満のため)。Rank1(選択済み)=中間路線
(強圧キャンペーン→交渉収れん)、Rank2=管理されたディエスカレーション、
Rank3=長期広域エスカレーション。

## 6. Top5 Coverage / Selection-Rejection Comparison(★最重要発見)

| 実専門家(M086) | AI候補との対応 | 分類 |
|---|---|---|
| **採用**: 戦争再開の可能性が高い | AIのRank3(代替案)に相当 | **ranked_top5だが、AI自身の選択(Rank1)とは異なる** |
| **棄却**: 段階的交渉(理由: トランプの面子維持困難) | AIのRank2として生成されているが、棄却理由(心理的要因)への言及なし | 仮説自体は生成、棄却ロジックは欠落 |
| **棄却**: 合意なき膠着(理由: 米経済・中間選挙コスト、イランの湾岸攻撃拡大リスク) | 対応する単独候補なし。湾岸攻撃拡大要素はRank3内に部分的に含まれる | not_generated(独立仮説としては) |

**最重要発見**: AI自身が選んだ主シナリオ(Rank1)は、実専門家の実際の結論とは
異なる。実専門家の結論はAIの代替案(Rank3)に近い。これは「候補セットの中に
正解が含まれていたか」ではなく「**AI自身のランキング・選定ロジックが実専門家の
判断と食い違った**」という、これまでのHungary/BC Strategyでは検出できなかった
種類の発見(Selection Logic材料を持つケースでしか検証できない)。

## 7. Actor Comparison

| 実専門家 | AI | 分類 |
|---|---|---|
| トランプ | 「米国政権（大統領・国家安全保障チーム）」 | implicitly_covered |
| ハメネイ師 | 「イラン指導部（最高指導者・革命防衛隊）」 | implicitly_covered |
| **ネタニヤフ** | **「イスラエル政権（ネタニヤフ首相と安全保障閣僚）」** | **explicitly_generated** — 3ケース中初めて個人名が具体的に生成された |
| 習近平/中国 | 欠落 | not_applicable(今回のT0テーマが意図的にこの主題系列を含めなかったため、公正な欠落ではない) |
| ガリバフ国会議長 | 欠落 | missing |

## 8. Indicator Comparison

| 実専門家の指標 | AI | 評価 |
|---|---|---|
| 「イランは減産に耐えられない」説(専門家多数が否定) | 原油・LNG価格の一般的モニタリング | **B(意味的に近い、ただし特定の論争自体は捕捉せず)** |
| 米中間選挙の経済・政治コスト | 該当なし | **D(完全欠落)** |
| イランの湾岸攻撃拡大リスク | Rank3候補内に部分的に存在 | **B(意味的に近い、独立した観測点としては未昇格)** |

## 9. Expert Selection/Rejection Comparison

§6に統合済み。

## 10. H1-H4 Support Status(+新規H5)

- H1(Actor Selection): **inconclusive**(部分改善と継続的一般化が混在)
- H2(Indicator Reasoning): **supports**
- H3(Counter-Hypothesis/Nuance): **supports**(新しい形: 棄却理由の欠落として再確認)
- H4(Investigation Question Reframing): **supports**(3/3ケース)
- **H5(新規)｜Main Scenario Ranking Divergence**: AI自身の主シナリオ選定が実専門家の
  結論と食い違う。confidence: preliminary / n=1。Phase I-B1で最優先に検証すべき。

## 11. Cross-case Findings by Case Family

**Hungary = 独立ケース(Family A)**。**BC Strategy + Iran = 関連Case Family(Family B、
同一のイラン・エスカレーション局面)**——この2つを独立した再現とはみなさない。

| パターン | Family A(Hungary) | Family B(BC Strategy/Iran) | Cross-family再現 |
|---|---|---|---|
| Actor選定の狭さ(H1) | あり | 混在(BC Strategy深刻、Iranは部分改善) | 2/2 familyだが内部一貫性は弱い |
| 専門家独自視点の生成欠落(H2/H3) | あり(Trump/MAGA連動) | あり(空母指標、面子ロジック) | **2/2 family、最も確からしい** |
| Investigation Question転記(H4) | あり | あり | **2/2 family、3/3ケース、最も再現性が高い** |
| Main Scenario Ranking Divergence(H5) | 評価不能(Selection Logic材料なし) | Iranのみで観測 | n=1、pattern未満 |

## 12. Limitations

- BC StrategyとIranはn=2独立ケースではなくn=2 case family中1つの内部2ケース
  である点を過大評価しないこと(ユーザー指示通り明記)。
- Iranも証拠枯渇(BC Strategyと同型)、Confidence上限あり。
- T0入力のBlinding完全性は技術的に保証されていない(私自身が事前にCorpus Auditで
  M086を含む資料を読んでいるため)——M086の結論・棄却理由・指標・framingを
  意図的に使わないという方法でのみ緩和。

## 13. Regression Artifact

`docs/geopolitical-ios-phase-i-b-evidence/phase_ib0_regression_artifact.json`
へIranケースを追加済み(Hungary/BC Strategyと同一自己完結形式、`case_families`
情報も追加)。個別ファイル: `eval_harness/phase_ib0_2_iran_result.json` /
`_evaluation.json`、`eval_harness/leakage_audit_iran_result.json`。

## 14. Phase J Readiness Assessment

**Phase Jへはまだ進まない。** 満たすべき条件(実Expert Review、または異なる
Case Familyでの追加独立Evidence)はいずれも未達。特にH5(Main Scenario Ranking
Divergence)は、n=1かつ最も実務的インパクトが大きい可能性のある発見であり、
Phase I-B1での最優先確認事項として記録する。

## 15. I-B1 Readiness

Production-ready状態を維持。実データでのFreezeは未実施のまま
(`eval_harness/evaluation_snapshot.py`のスモークテストのみ済み)。
実Expert Session確保時、新規テーマで即座に開始可能。
