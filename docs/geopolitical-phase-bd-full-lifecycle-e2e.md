# Phase B-D P0: Full Lifecycle E2E（本番実データ）実施結果と設計確認

## 実施内容（Real/Mock明記）

DB=実Supabase、LLM=実Azure OpenAI、質問は実用的なCross-domain質問1件
（「EUDR延期は今どうなっている？」、`source_department=sustainability`）。

| ステップ | 結果 |
|---|---|
| Migration本番適用 | 完了。preflight（origin分布・FK型・件数）確認済み、適用後も件数/分布一致（121/24/160件） |
| ①Real Department Inquiry（初回） | 24.5秒で応答。Expert AI同期呼び出しなし（`expert_ai_called=false`）。Gap生成（`merged_into_existing=false`） |
| 重複問い合わせ（同一質問を再送） | Gap総数25件のまま増殖なし。既存gapへ`merged_into_existing=true`、`request_count`が1→2に増加 |
| ②`run_research_request`（実Azure OpenAI） | 194.2秒で完了。実`expert_response_id`・candidate `knowledge_item_id`生成 |
| ③Retrieval非混入確認 | Human Review前の再問い合わせで、生成されたcandidateが`provenance`/`selected_source_ids`に一切含まれないことを確認 |
| ④Human Review | **却下（Reject）**。理由は下記Evidence Provenance監査参照 |
| ⑤Reopen | `reopen_research_gap_after_rejection`を実データに対して実行、`gap.status=open`・`resolution_expert_response_id=None`へ正常に復帰することを確認（P0修正の実データ検証も兼ねる） |

Approve pathでのGap resolved→次回reuse確認は、却下判断のため今回は実施していない
（内容自体の却下であり、Approve pathのコード自体は`test_full_lifecycle_gap_research_candidate_review_reuse`で決定論的に検証済み）。

## 重要な発見: Evidence Provenance監査

Human Review判断の前に、candidateが何を根拠に生成されたかを監査した（ユーザー指摘により実施）。

**判明した事実**:
- このExpert AIエンジン自体には、通常のLLM補完呼び出しにtool-calling/web検索は無い
  （`geo_llm_common.call_llm_structured`は`tools=`無しの単純な構造化出力呼び出し）
- ただし**staged実行モードには別途`geo_expert_research_service.py`による実Web検索機構が存在する**
  （Azure OpenAI Responses APIの`web_search_preview`ツール、`GEO_EXPERT_EXTERNAL_RESEARCH_ENABLED`で有効化）。
  今回のRun中に実際に動作し、実在するURL（Rainforest Alliance公式・欧州委員会公式・idh.org等）を
  10件取得・引用していた（捏造IDは無し、`claims[].source_ref`は全て実Source Pack内のID）
- **ただしこの機構は意図的にタイトル+URLのみを保存し、本文は取得しない設計**
  （`qualify_and_persist_citations`、`extracted_text = title`をそのまま保存。既存Phase3
  バックログ記載の「タイトルのみからのAIによる内容類推」問題と同種）
- **新規発見のバグ**: キーワードマッチング（`_relevant_rows_by_keywords`）が日本語（カタカナ/漢字）
  のみstopword除去し、英語トークンを無フィルタで通すため、Department Inquiry側の指示文英単語
  （"Need **analysis**（既存Geo Intelligenceが不足している論点..."）が、無関係な過去テストの
  スタブ文書（"An **Analysis** of Greenland's Draft Constitutions..."）とILIKE一致し、
  Source Packに紛れ込んだ（10件中1件が無関係な混入）

**却下理由**: 引用元URL自体は実在するが、詳細な主張内容（例:「報告義務の具体的な内容・
プロセスを解説している」等）が本文未検証のタイトル推測に基づく可能性があること、および
無関係な文書が1件混入していたことから、Promoteするには根拠が不十分と判断。

## 設計理解の確認（ユーザーとの合意事項）

- **Geopolitics Department Intelligence AI（Intelligence AI、Department Inquiry経路）**:
  既存Geo Knowledge/Reportからわかる範囲のみで回答する。Web検索・捏造は一切しない。
  判断できない場合は正直にその旨を回答し、Knowledge Gap（Needs）として記録する。
  この境界は今回の実データE2Eで実証済み（`sync_expert_enabled`既定OFF）。
- **Geopolitics Subject Expert AI（Report Studio・非同期Research Request等の深い調査用途）**:
  External Web Researchを行うのは設計として妥当・意図通り。ただしその成果はcandidateのまま
  Human Reviewを経るまで正式なKnowledgeとして扱われず、Sustainability側には流れない。

## バックログ（新規発見分）

| category | problem | evidence_case | impact | recommended_action | priority | status |
|---|---|---|---|---|---|---|
| Keyword Matching | `_relevant_rows_by_keywords`が日本語のみstopword除去し英語トークンを無フィルタで通すため、Department Inquiry等が生成する指示文中の一般英単語（"analysis"等）が無関係な過去Runのスタブ文書とILIKE一致し、Source Packへ混入する | 本番Full Lifecycle E2E（EUDR質問でGreenland文書が混入） | 中（Expert AIの分析対象が汚染され、無関係な引用が紛れ込む。Quality Guardも`sources_and_citations`を検証しないため素通りする） | 英語トークン向けのstopword除去（一般語のブロックリスト、または最低文字数+一般語辞書によるフィルタ）を`_KATAKANA_LATIN_KEYWORD_PATTERN`周りに追加検討。Department Inquiry側の指示文生成（`build_missing_dimension_request`）でも、一般語になりやすい表現を避ける余地がないか確認 | P1 | 未対応（発見のみ） |
| Evidence Grounding | `run_external_research`（Web検索）が取得するcitationは「タイトル+URLのみ、本文未取得」の設計。Expert AIはタイトルのみから詳細な内容を推測して記述しうる（本文との突き合わせ検証は無い）。既存Phase3バックログの「タイトルのみからのAIによる内容類推」と同種の問題がWeb検索経由でも再現することを確認 | 本番Full Lifecycle E2E（EUDR関連10件中、コンプライアンス期限等の具体的主張がタイトルの範囲を超えて詳細化されていた） | 中（Human Reviewで人間が気づけば防げるが、機械的な検知手段が無い） | 将来的に、Web検索citationについても本文取得（少なくとも要約）を行うか、Quality Guardに「claims/sources_and_citationsの内容がsource_pack内のtitleの範囲を超えていないか」を検査するチェックを追加することを検討。既存Phase3バックログ項目と統合管理 | P1（既存Phase3項目と重複、優先度は既存判断に準拠） | 未対応（発見のみ、既知の設計トレードオフ） |

## Acceptance確認状況（10項目）

1. 通常Department Inquiryで同期Expert call = 0 ✅（実測、24.5秒応答でExpert AI不使用）
2. HTTP回答がExpert AI完了を待たず返る ✅（同上、3.5分待ちが解消）
3. Real geo_knowledge_gapが1件生成される ✅
4. 重複問い合わせでGapが増殖しない ✅（実測、25件のまま、request_count増加のみ）
5. `run_research_request`でReal Azure Expert AIが実行される ✅（194.2秒、実Web検索も動作）
6. 実在するexpert_response_idがGapに紐づく ✅
7. candidate生成時点では通常Retrievalに混ざらない ✅（実測確認済み）
8. Human Review前はGapがresolvedにならない ✅（`status=researching`のまま維持を確認）
9. 人間承認→active後にのみGap resolved — 今回はReject判断のため未実施（コードパスはUnit Testで検証済み）
10. 再問い合わせでは新Knowledgeを使い、Expert call = 0 — 今回はReject判断のため未実施

Reject/Reopenパスは実データでも正常動作を確認（P0修正の実データ検証を兼ねる）。
