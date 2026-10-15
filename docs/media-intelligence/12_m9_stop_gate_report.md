# M9 — Actor × Media Intelligence Integration Pilot: Stop Gate報告

指示された20項目を報告する。詳細な根拠は[10_m9_actor_media_integration_architecture.md](10_m9_actor_media_integration_architecture.md)
と[11_m9_actor_media_abcd_comparison.md](11_m9_actor_media_abcd_comparison.md)を参照。
**Expert Review Packへ進むかはこの報告を受けてユーザーが判断する。全10 Actor×全Media
統合・定期自動更新・大量Case rerun・Pattern自動active化のいずれへも進んでいない。**

## 1. Pilot Case
M9 Pilot: Zelenskyy Oval Office Clash(2025-02-28〜03-11)。新規`geo_cases`
(`4d6e7894-cd39-48d2-8f9e-1efbcf74b294`)、`geo_issues`(`us_ukraine_alliance_
relations`)、`geo_themes`(`ukraine_conflict`)を作成しEntity 3件をリンク。

## 2. 利用Actor
Zelenskyy / Putin / Trump(既存entity、10 Actor Build時に作成済み)。

## 3. Actor Intelligence coverage
本番既定(status=active)ではZelenskyyは該当レコードなし、Putin/Trumpは骨格のみ
(persistent_preference/decision_patterns/strategic_communication_pattern全て空、
confidence=判断不能)。実際に使えた豊富な内容(persistent_preference・decision_
patterns 3件・strategic_communication_pattern)は全てstatus=candidate(10 Actor
Buildの成果)。Test-mode Candidate Snapshot(`profile_lookup_statuses=("active",
"candidate")`)により統合テストでのみ参照。詳細は10章の表を参照。

## 4. 利用Media Intelligence
Pilot1(Zelenskyy)の全25件(Episode15+Pattern5+Profile5)をstatus=candidate込みで
参照(`media_intelligence_statuses=("active","candidate")`)。

## 5. A/B/C/D response IDs
生JSON: `docs/media-intelligence/m9_raw_responses/m9_{A_base,B_actor_only,
C_media_only,D_integrated}_result.json`(各response内のrequest/response行に
実DB非保存の識別情報を含む、DBには保存されていない比較実験専用関数の結果)。

## 6. CallBudget
全4条件とも2 calls(Stage1+Stage2)、CallBudget上限3を下回る。追加Azure Callなし。

## 7. Token比較
A=90,942 / B=91,408 / C=97,375 / D=99,500 total tokens。D(統合)はC比+2,125、
A比+8,558のみで、統合による極端な肥大化は発生しなかった。詳細表は11章1節。

## 8. Rubric比較
12項目×4条件、合計 A=13/24, B=20/24, C=18/24, D=19/24。詳細根拠は11章2節。
**B(Actor Only)が単純合計では最高点だが、これはD唯一の弱点(項目8 Evidence
Weighting=0点、9章参照)による**。

## 9. Actor-only contribution(A→B)
- Key Point selection rationaleにActor Decision Pattern/Persistent Preferenceが
  明示的な根拠として登場(A→Bで質的に変化)
- control_typeが`mixed`→`actor_controlled`へ、より具体的な機構理解に変化
- Situation Actor Assessmentの各行にActor由来の具体的根拠(「トランプのディール志向・
  レバレッジ重視という既存のActor Intelligence」等)が明示的に追加

## 10. Media-only contribution(A→C)
- `media_source_context`: A=完全にnull(0 outlets)→C=5 outlets、実質的な内容で充填
- Key Pointに「条件付き支援へのシフト」という、情報環境解釈を経由した具体的表現が追加
- ただし`media_hypothesis_evidence_grounded`という新規Quality Guard失敗が発生
  (実Documentコーパス不在という既知の制約、M8.5から継続)

## 11. Integrated contribution(B/C→D)
- **B・Cのいずれか単独では生成されなかった記述がDでのみ出現**: Putin行の
  「ロシアがこの事件を過度に利用すると、第三国からの信頼を損なう可能性」
  (Media Third-country Patternの視点がActor評価へ流入)、Scenario根拠への
  「トランプの過去の対イラン・ホルムズ海峡事案」という他Case実例引用
  (Actor Decision Patternの想起がMedia提示によって促された可能性)
- **ネガティブな統合効果も確認**: `anonymous_sourced_hypothesis_confidence_capped`
  というD固有の新規Quality Guard違反(9章参照)。統合は単純に「良くなる」だけでは
  ないことを示す誠実な結果。

## 12. Key Point差分
A「米国の対ウクライナ軍事支援・情報共有の継続可否」(mixed)→D「支援を交渉カードとして
一時停止し得る構造(条件付き性)」(actor_controlled)。**Primary Key Point自体の対象
(米国の支援方針)は一貫していたが、selection_rationale/hinge/control_structureの
解像度がA→Dで明確に向上した。**controlling_actorは明記されていない
(controlling_entity_ids=null、control_type=actor_controlledだが具体的entity_id
参照は4条件とも空——実装上のGap、20章参照)。

## 13. Actor Intent差分
B以降、Zelenskyyのgenuine_or_tacticalが一貫して"genuine"(Red Line維持)、Trumpが
一貫して"mixed"(取引的)という評価で安定。Dで唯一、Putinの評価に「ナラティブ利用の
評判コスト」という新しい次元が追加された(9章Case B)。

## 14. Media Interpretation差分
C: 5つの個別Outlet別Hypothesis(Global mainstream/Ukraine domestic/Russia state/
European continental/Third-country)。D: 3つのHypothesis(統合された単一エントリ、
Actor側の考慮に注意力を配分した結果と推定)に集約されたが、うち1件が過信の実例
(9章)。**Outlet数の多寡ではなく質を見るべき**という依頼書Section3の原則に沿い、
数の減少自体は問題視しない。

## 15. Decision Dynamics差分
A→Dで、シナリオのassumptions/supporting_evidenceがより具体的なActor Pattern引用
(過去の交渉スタイル)を伴うようになった。因果Chain(Actorの選好→行動→Media/Signal→
Scenario switch)はD scenario本文で明示的に言語化された
(「トランプ政権が支援継続と引き換えに...資源・停戦条件...を求める構図」)。

## 16. Scenario差分
Main Scenario自体の大枠(支援停止=交渉レバー)はB以降で安定。Dのみsupporting_evidenceに
Cross-Case実例(Hormuz)を含み、observable_indicatorsに「ロシア・第三国メディアが
『西側分裂』ナラティブをどの程度継続的に引用するか」というMedia Pattern由来の指標が
追加された。

## 17. Actor / Media conflict事例
11章6節参照。Trump行での「意図的交渉カード化」vs「純粋な技術的調整」という対立、
および同一Hypothesis内での「press煽情的framing」vs「投資報道の過大因果主張」という
Media Pattern同士の緊張。

## 18. Physical Evidenceによる修正事例
11章7節参照(部分的確認)。独立したOSINT/専門家"outlet"行としては現れなかったが、
hypothesis[0]のalternative_explanationにISW/ACLEDの慎重な留保の趣旨が反映された。

## 19. Quality Guard
新設3チェック(`_historical_pattern_not_asserted_as_certain_fact`/
`_cross_intelligence_contradiction_not_silently_dropped`/`_evidence_attribution_
not_conflated`)は4条件全てでpass。既存チェックのうち`anonymous_sourced_hypothesis_
confidence_capped`がD限定で新規失敗(5章で分析済み、Human Reviewでの是正が必要な
実例として記録)。`pytest tests/`: 391 passed/6 failed(既知の無関係failureのみ)。

## 20. Remaining Gaps

1. **Evidence Weighting劣化の実例が確認された**(9章): Actor+Media統合が匿名ソース
   由来Hypothesisの確信度を不当に底上げするリスクを、まさにQuality Guardが検出した。
   M9をさらに進める前に、このケースを人手でレビューし、`_ACTOR_MEDIA_CROSS_
   REFERENCE_NOTE`のプロンプト指示をさらに強化すべきか判断が必要。
2. **`key_point_assessment.controlling_entity_ids`が全条件でnull**: control_type=
   actor_controlledと判定していながら、具体的なentity_id参照が一度も行われていない
   ——既存`_key_point_control_type_consistency_check`は本テストでは違反として
   拾われていない(要確認、Gap)。
3. **`selected_actor_ids`のactor_ref命名規則がD条件でのみ`actor_`prefix無し**
   (`trump`/`zelenskyy`/`putin`)——内部的には自己整合的(key_actor_profiles側も
   同じ命名)だが、他条件との比較のためにドリフトの原因を特定すべきか要検討。
4. **Case-scoped Retrieval未実装**(M8.5から継続): 全件走査+char budget trimの
   ままで、Actor/Media双方のSource数が増えた際にスケールしない。
5. **Case E(Physical/Specialist Evidenceによる修正)は部分確認に留まる**:
   独立したOSINT/専門家Outlet行としての明示的な扱いが無い。専用のOSINT情報源
   Profile/Episodeを充実させた場合に、より明確な確認ができるかもしれない。
6. **本Pilotは1 Case・4回のみの実行**であり、統計的検証ではない。Rubric合計点も
   B>D(僅差)という結果を含め、これらの傾向が別Caseでも再現するかは未検証。
7. 46件のMedia Intelligence・複数のActor Intelligence Candidateは全て`candidate`
   のまま。Human Reviewの範囲・担当・時期の決定は依然として保留。
