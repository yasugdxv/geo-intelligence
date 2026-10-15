# Media Intelligence Build — M8.5 完了報告(Stop Gate)

指示された4段階(Quality Guard拡張→Formatter/Retrieval→Zelenskyy OFF/ON実Azure比較→
高市/台湾Caseでのcross-case validation)をすべて完了した。**M9(Actor×Media統合)には
まだ進んでいない。**

## 1. Quality Guard拡張

`geo_expert_quality_guard.py`へ、`media_profile`/`media_episode`/`media_pattern`の
structured_content自体を直接検証する新規エントリポイント`validate_media_knowledge_item()`
を追加(既存の`response_json`向けチェックとは別関数、ロジックは流用)。

- `_media_kb_observed_free_of_inference_language`: observable_framingへの意図言及語混入検出
- `_media_kb_hypothesis_corroboration_consistency`: confidence=高×Hypothesis記載時の
  質的裏付け要求(既存`_anonymous_sourced_hypothesis_confidence_capped`と同じ契約)
- `_media_kb_pattern_evidence_grounded` / `_media_kb_pattern_exception_has_rationale`:
  media_pattern向け
- `_media_kb_profile_has_limitations_documented`: media_profile向け

**実データ全46件(Pilot1: 25件+Pilot2: 21件)に実際に走らせ、3件の実在する問題を検出・
修正した**(いずれもconfidence=高×根拠不十分なHypothesis、`中`へ是正)。最終的に
46件/46件がpass。既存テストスイートへの影響なし(391 passed維持)。

## 2. Media Intelligence Formatter / Retrieval

新規`geo_media_intelligence_service.py`(`build_media_intelligence_pack`)+
`geo_expert_engine._format_media_intelligence_block`を実装。`geo_actor_intelligence_
service`/`_format_actor_intelligence_block`と対称的な構造。

- Feature Flag`GEO_EXPERT_MEDIA_INTELLIGENCE_ENABLED`(既定OFF)、Actor Intelligence
  Flagとは独立。
- 既定`statuses=("active",)`のみ(本番相当、Human Review未了のCandidateを実運用へ
  混入させない)。比較実験時のみ呼び出し元が明示的に`("active","candidate")`へ上書き
  可能(Session2 Golden Case A/B/C比較と同じ考え方)。
- Case-scoped Retrieval(Actor Intelligenceの`select_key_actors`相当)はまだ実装せず、
  全件走査+char budget trimという簡易実装に留めた(M9以降の課題として明記)。

## 3. Zelenskyy OFF/ON 実Azure比較

詳細: [08_zelenskyy_off_on_comparison.md](08_zelenskyy_off_on_comparison.md)。

`run_expert_request_staged`(DB非保存の既存比較実験専用関数)を実Azure呼び出しで
2回実行(OFF/ON、Actor Intelligence Flagは両方とも既定OFFのまま単一変数比較)。

**測定可能な差分を確認**:
- `media_source_context`: OFF=完全にnull → ON=注入したMedia Pattern Candidatesの
  内容を直接反映した実質的な内容で充填(かつ「想定」ラベルで適切にConfidence管理)
- Key Point解釈フレーム: OFF「米国内政治」→ ON「支援の条件付き交渉カード化」という
  軸のシフト
- Key Stakeholder: OFF 2名→ON 3名(Putin追加、ロシア側情報環境の提示による)
- Token増分: +7,478(Pack自体は約2,990文字)

## 4. 高市/台湾Caseでのcross-case validation

### 4-1. 実施内容

4並列調査Agent(日本国内メディア・公式/中国公式・国営メディア/台湾反応/Global
mainstream・WSJ報道検証)による実地調査に基づき、17件のHistorical Media Episode+
4件のMedia Pattern Candidateを新規作成(`media_intelligence_takaichi_pilot2_
2026-08-31`)。

### 4-2. Lens Coverage(ユーザー指定: Japan/China/Taiwan間の国内・国外Narrative差、
Official Signaling、Local-language Media)

| Lens | Source数 | 主要発見 |
|---|---|---|
| Japan official | 1(kantei.go.jp) | Kihara官房長官による実名の明確な否定「そのような事実はない」 |
| Japan domestic(保守〜リベラル) | 5(Sankei/Mainichi/Tokyo Shimbun/Japan Times/Nikkei) | 世論調査数値の使い分け・手続き批判・国際framingへの同調等、政治的立場に沿った明確な分岐 |
| China official | 1(fmprc) | 林剣→毛寧→郭嘉昆と続く5段階のEscalation Ladder |
| China state media(3層) | 3(Xinhua/People's Daily/Global Times) | 法的抑制(Xinhua)/道徳的糾弾(People's Daily)/対決的signal-testing(Global Times)という明確な3層構造 |
| Taiwan official | 1(MOFA等) | 高市発言への直接評価を意図的に避ける抑制的姿勢 |
| Taiwan domestic media | 2(Focus Taiwan/Taipei Times) | 台湾を「対立の当事者ではなく巻き込まれた対象」として位置づけ |
| Taiwan specialist | 1(INDSR) | 分析を「日本の物語」として構成、台湾自身の利害分析は最小限 |
| Global mainstream | 2(WSJ/Reuters系束) | WSJ単独の匿名情報源スクープ、Bloomberg/APはFlashpoint型 |

### 4-3. Cross-case Pattern(最重要の検証結果)

**5つ全てのPilot1由来Patternのうち、少なくとも2つが独立した無関係のCaseで再現・拡張
されることを確認した**:

1. **`media_pattern_anonymous_backchannel_claim_meets_denial`**(新規、Pilot1の
   `mep_time_overclaim`とPilot2の`mep2_wsj_anonymous_claim_named_denial`を**同一
   Patternのevidence_episode_idsとして直接横断的に引用**): 単一匿名情報源の外交・
   軍事スクープが独立した裏取りなしに公式否定/専門家の留保に直面するという構図が、
   戦況(Ukraine)と外交電話(Japan)という全く異なる領域で再現された。
2. **`media_pattern_state_media_multi_tier_escalation_register`**: Pilot1では
   Global Times中心の単一Episodeによる初期観測だったが、Pilot2でMFA/People's
   Daily/Global Times/Xinhuaの4媒体を独立に検証したことで、4層構造として確証度が
   向上した。既存Actor Intelligence Build(Xi Jinping strategic_communication_
   pattern)の知見とも整合。
3. **`media_pattern_implicated_subject_avoids_self_interested_reframing`**
   (**pattern_role=exception、`default_pattern_ref`にPilot1の`media_pattern_
   third_country_self_interested_reframing`のpattern_idを直接指定**): 台湾は
   Pilot1の「第三国は対立を自国のナラティブ補強に利用する」という既定Patternとは
   逆に、自らが対立の「話題の主体」である場合はむしろ当事者性を薄める抑制的行動を
   取ることを発見。**初のtrue cross-case exception pattern。**

Pilot2固有の新規Pattern(`media_pattern_domestic_press_polarization_along_
orientation`)も1件作成。

## 5. DB状態サマリー

| | Pilot1(Zelenskyy) | Pilot2(Takaichi) | 合計 |
|---|---|---|---|
| Media Episode | 15 | 17 | 32 |
| Media Pattern | 5 | 4 | 9 |
| Media Profile | 5 | 0 | 5 |
| **合計** | 25 | 21 | **46** |

全件`status="candidate"`のまま、`activate=True`は未使用。Quality Guard 46/46 pass。

## 6. 回帰確認

`pytest tests/`: 391 passed / 6 failed(既知の無関係failureのみ、新規リグレッション
なし)。今回のスコープ(Quality Guard拡張・Formatter/Retrieval実装)による既存機能への
影響なし。

## 7. M9(Actor×Media統合)着手前に残る課題

1. **Case-scoped Retrieval未実装**: 現状は全件走査+char budget trimのみ。Source数が
   増えた場合にスケールしない(46件時点では実用上問題ないが、Actor Intelligence相当の
   `select_key_actors`型ロジックが必要)。
2. **`media_hypothesis_evidence_grounded`とMedia Intelligence Packの相性**: OFF/ON
   比較で、Pack由来のHypothesisが実Documentを伴わないためEvidence Grounding
   チェックに失敗した。実運用(実記事コーパスあり)でこの相性がどうなるかは未検証。
3. **Media Profileの効果検証はまだ間接的**: Global Times解釈で効果があったのは
   既存Actor Intelligence(Xi Jinping)の知見であり、Media Profile自体(n=1〜2)の
   単独効果はまだ十分に切り分けられていない。
4. Actor `strategic_communication_pattern.information_channel_relationship_ids[]`
   への実際の接続はまだ行っていない。
5. 46件すべて`candidate`のまま。Human Reviewの範囲・担当・時期の決定。
6. Pilot3(3件目のCase)での追加のcross-case確認が望ましいか、それとも2 Caseの
   再現で十分と判断しM9へ進むか、方針判断が必要。
