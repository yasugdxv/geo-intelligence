# Tier1 Reasoning Upgrade — OFF/ON比較（Case1 Trump-Xi、Gate通過ケース）

> **Provenance note**: このRunはevaluation run executed before persist=False was introduced;
> DB side effects (geo_cases.final_outputの上書き含む) were later identified and restored
> where recoverable. 詳細: `docs/expert-ai-validation/eguchi_db_write_incident_2026-09-06/`。
> 本レポートの比較結果自体の妥当性には影響しない（`run_expert_request_staged`はRepairまで
> 含めてOFF/ON同一に実行されており、副作用はCase1のDB行への書込みのみで、LLM実行内容や
> 比較結果には影響しない）。

作成日: 2026-09-06
対象: Eguchi Expert AI Final v1.0 Tier1（DirectionalPerception / perceived_path / event_role /
Candidate Generation Operators / Actor-Analyst Response Separation）
前回のM9比較（`docs/expert-ai-validation/eguchi_tier1_off_on_m9_zelenskyy/`）はSource Pack主題
不一致によりINCONCLUSIVEだったため、Evaluation Input Gateを新設して再実施した。

## Evaluation Input Gate 事前監査（実施記録）

Netanyahu(Case3)/MBS(Case4)を先に調査したが、いずれもKey ActorのProfileが`status=candidate,
retrieval_status=restricted`（本番既定フィルタでは取得不可、明示的上書きが必要）で条件4に
弱点があった。**Case1（Trump-Xi US-China Semiconductor & AI Controls）を採用**——TrumpとXi
Jinping双方が`status=active`のActor Intelligence Profileを保有しており、明示的上書き不要。

| Gate条件 | 判定 | 根拠 |
|---|---|---|
| 1. Current Evidenceが実資料 | △ | 実文書(is_mock=false)だが、この5-caseベンチマーク共有テーマ(67件)は全件`extracted_text`がタイトルの丸写しのみ（本文取得なし、既知の構造的制約、Case3/4も同様）。ただし本件は主題との一致度が高くEvidence Dominanceリスクは低い |
| 2. Fixtureが主要Evidenceを占めない | ✅ | 3件とも実文書、Fixtureなし |
| 3. 直接関連Evidenceが複数件 | ✅ | 原質問（無改変）で3件、いずれも半導体輸出管理の実務に直接関連 |
| 4. Key ActorがGroundされている | ✅ | Trump/Xiともactive Profile。BIS(制度)もEntity登録済み |
| 5. OFF/ONでCase input完全一致 | ✅ | git stashで自分のTier1差分12ファイルのみ一時退避→OFF実行→復元→ON実行。document_ids完全一致を確認 |
| 6. External ResearchはOFF | ✅ | 環境変数未設定、両Runで`external_research_enabled=False`確認 |

事前Evidence分類（3件、原質問時点）:

| 文書 | 分類 |
|---|---|
| 中国・台湾・日本の半導体輸出2026（TIMEWELL） | directly_relevant |
| 半導体（METI/経済産業省） | directly_relevant |
| 国問研戦略コメント（2026-17）MATCH法と経済安全保障 | directly_relevant |

## 実行条件

- Case: `Expert AI Benchmark: Trump-Xi US-China Semiconductor & AI Controls`
  (case_id: `61447d94-e5cc-48f9-9352-31df72af9628`)。原質問をそのまま使用。
- Theme: `05064de8-48f8-484a-8243-f8c837c5a735`（Expert AI検証用ベンチマーク共有テーマ）。
- profile=`deep`, mode=`case_analysis`。`run_expert_request_staged`使用
  （**当時はpersist=Falseオプションが存在せず、実際にはDBへ書込みが発生していた**——
  `docs/expert-ai-validation/eguchi_db_write_incident_2026-09-06/`参照、事後に復元済み）。
- 予算実績: Reasoning LLM calls = 3/3（Stage1+Stage2+Repair、両Run共通）、Research/Web-search
  calls = 0、Physical Model API Calls = 3。OFF/ONで完全に同一のRepair発火パターン
  （`key_stakeholder_v12_fields_present`失敗による）。
- document_ids: OFF/ONで完全一致（3件とも同一）——入力の同一性を確認済み。
- 保護条件: 既存Baseline上書きなし、DB active Knowledge変更なし、write-backなし、Tier2/Tier3
  未着手。

## 品質ガード集計

| | OFF | ON |
|---|---|---|
| 総チェック数 | 43 | 45（+2、directional_perceptions系グラウンディングチェック） |
| Pass（Repair後） | 42 | 45 |
| Fail（Repair後） | 1（`actor_intelligence_provenance_present`） | 0 |

## 9観点の比較（Question Fidelity / Key Actor Retention / Evidence Dominanceを追加）

このケースはGateを通過しただけあり、**両Runとも実際の質問（米中半導体・AI規制）に一貫して
回答しており、M9のような主題逸脱は発生しなかった**。

| 観点 | OFF | ON | 判定 |
|---|---|---|---|
| **Question Fidelity** | 一貫して米中半導体輸出管理を分析 | 一貫して米中半導体輸出管理を分析 | **Same（両方良好）** |
| **Key Actor Retention** | Repairが米連邦議会・BIS・中国政府/党指導部の3主体を新規生成 | Repairが**米連邦議会1主体のみ**新規生成 | **Worse（訂正済み、下記Repair分析参照）**：両RunともStage1 raw時点でkey_actor_profilesは0件（Trump/Xiも含め一度も存在せず）。3件/1件はRepairが完全新規生成した結果であり、「groundedなActorが脱落した」のではない。ON側はOFFより2件多い失敗チェック（`fact_claims_have_source_ref`/`supporting_knowledge_refs_grounded`）を同じ1回のRepair呼び出しで解決する必要があり、生成量が細った可能性が高い。詳細: `docs/expert-ai-validation/eguchi_db_write_incident_2026-09-06/`配下のRepair分析参照 |
| **Evidence Dominance** | 3件とも主題に沿った形で引用、乗っ取りなし | 同左 | **Same** |
| 1. Situation Frame | 「輸出管理の法制化・恒久化がどこまで進むか」を制度要因中心に把握 | 「対中先端半導体・AI輸出管理の制度化」を制度・市場構造の相互拘束として把握 | **Same**（本質的に同じFrameへ到達、記述の精緻さはON がやや上） |
| 2. Actor Big Flow（Goal→Perceived Path→Event Role） | フィールド自体が無く（旧Schema）、Goal相当の記述のみ | **米連邦議会についてperceived_path（BISへ権限付与し政権交代耐性のある枠組みを作ろうとしている、という因果的信念）とevent_role（role_type=institutionalization）を実際に生成** | **Improved**（Tier1が意図した「つながった読み」を実際に1主体分生成できた） |
| 3. Directional Perception | フィールド自体が存在しない | フィールドは存在するが**null**（0件）——key_actor_profilesに残った主体がCongress単独のため、A→Bのペア自体が成立しなかった | **Cannot judge**（機能不全ではなく、対象ペアが存在しない状況でGrounding Guardが正しく空を維持した=安全側の正しい挙動） |
| 4. Candidate Set（hypothesis_register H0-H4） | H0/H2 retained、H1/H3 rejected、H4 retained。動機の羅列ではなく「制度化 vs 個人・政権要因 vs 構造要因 vs フレーム過大」の一貫した対立軸 | H0/H2 retained、H1/H4 rejected、**H3はrejectedではなくunresolved**（「今後の方向性は政権・議会・産業界の分裂により予測不能に近い」と留保） | **Improved（軽微）**：ONの方が不確実性の扱いにやや慎重（Evidence不足時に断定を避けるAnti-overthinking原則に近い） |
| 5. Candidate Ranking / Rejection | 4候補比較、rejected理由の説明は明確 | 同様に明確、対象主体が減った分シンプル | **Same** |
| 6. Actor Expected vs Analyst Predicted | フィールド自体が存在しない | `situation_actor_assessment`配列自体が空のため未生成 | **Cannot judge**（この局面ではIssue Component評価自体が生成されず、両Runとも検証material無し） |
| 7. Dominant Flow / Scenario | key_stakeholder_assessment.determination等を通じ、制度的制約下の帰結として記述、本人希望の単純投影ではない | 同様、市場構造・同盟協調との相互拘束として記述 | **Same** |
| 8. Speculative Noise | 5仮説、過剰な量産なし | 5仮説、過剰な量産なし | **Same** |

## A/B/C/D分類

- **A. Tier1によって初めて可能になった推論**: `perceived_path`/`event_role`によって、
  米連邦議会という制度アクターについて「なぜ法制化するのか（Goal）→BISへの権限付与という
  経路（Perceived Path）→今回の措置の位置づけ（institutionalization、Event Role）」という
  Session2以前のSchemaでは表現できなかった因果連鎖を実際に生成できた。これはTier1が意図した
  効果の直接的な実例である。
- **B. 既存AIでも既にできていた推論**: Key Point候補比較、H0-H4のCompeting Hypotheses、
  制度要因 vs 個人要因の対立軸自体は、OFF側で既に高品質に機能していた（Session2 Reasoning
  Upgrade由来）。
- **C. 新機能を入れてもまだ弱い推論**: DirectionalPerceptionは今回も0件——ただしこれは
  「Trump/Xiという本来の主役がKey Actorから脱落した」という別の問題が先にあり、
  DirectionalPerception自体の欠陥かどうかはこのRunだけでは判断できない。Actor Expected/
  Analyst Predicted分離も同様に、situation_actor_assessment自体が空だったため未検証。
- **D. 新たに生じたノイズ／副作用**: 当初「Repairが既存Actorを削除した」と疑ったが、
  Repair分析（後述）により**両RunともStage1 raw時点でActorは1件も存在せず、最終的な
  Actor群は全てRepairの新規生成物**であることが判明し、この仮説は否定された。実際の
  副作用は「ON側はOFFより2件多い失敗チェックを同じ1回のRepair呼び出しで解決する必要が
  あり、生成されたActor数がOFFの3件よりON1件と少なくなった」という、Repairの一発勝負
  設計における生成量の希薄化。バグではないが、Tier1追加分の複雑性がRepair入力の
  失敗チェック数を増やす方向に作用した可能性は残る（n=1、要追加検証）。

## 総括

前回のM9とは異なり、**Question Fidelityの逸脱は無く、Tier1の効果を部分的に正しく観測できた**。
最大の成果は`perceived_path`/`event_role`が実際に意味のある因果連鎖を生成したこと。
Key Actor Retentionの差（3件 vs 1件）は追加のRepair分析により「groundedなActorの脱落」では
なく「両RunともStage1 raw時点でActor0件、Repairが新規生成する量がON側で少なかった」ことが
判明した——Repairの決定的なバグは見つからなかった。DirectionalPerceptionとActor/Analyst分離は、
今回のケースでも実質的な検証material（複数の人物Actor、Issue Component評価）が揃わず、
依然として実地検証できていない。むしろCase1自体が「distributed、個別Key Actorなし」という
制度論フレームに終始しやすく、人物間相互作用の検証には構造的に不向きだった可能性が高い。

## Repair分析の詳細

`docs/expert-ai-validation/eguchi_db_write_incident_2026-09-06/repair_analysis_case1.md`参照。

## 次のステップ（判断待ち、実施していない）

1. Repairに明確な不具合は見つからなかったため、**ON単独再実行による再現性確認の優先度は
   下がった**（ユーザー方針: 明確な不具合が無い場合のみ再実行を検討）。
2. DirectionalPerception/Actor-Analyst分離を実地検証するには、**複数の人物Actor
   （Trump/Xi等）がKey Actorとして最後まで残る質問設計**が必要——「TrumpとXiはそれぞれ
   相手をどう動かそうとしているか」のような、人物間の相互作用そのものが回答に不可欠な
   ケースへ進むのが妥当。
