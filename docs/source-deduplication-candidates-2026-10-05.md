# Source重複候補（Source Deduplication 次タスク用）— 2026-10-05

**性質**: 読み取り専用の分析結果。**merge / delete は一切していない**。
**対象**: `geo_source_registry` 108件（[TEST]を除く）。うち 76件は Expert AI External Research による自動登録（name＝domain名、notes「Expert AI External Researchにより自動登録」）。

## 1. 同一domainを持つ重複候補（4グループ）
`geo_source_domains` で同じdomainが複数Sourceに存在 → Article自動紐付けが曖昧（`source_id=NULL`のまま）になる原因。

| domain | Source A（推奨canonical） | Source B | 根拠（A/B） | canonicalの理由 |
|---|---|---|---|---|
| aljazeera.com | **Al Jazeera**（fdb74d1a、人手登録 2026-08-21） | www.aljazeera.com（7e00f3d6、自動登録 2026-09-03） | theme関連 1/1、Evidence文書 1/1、紐付け記事 0/0 | 人手登録を優先。名称が人に分かりやすい |
| cnbc.com | **CNBC**（9bae0c67、人手） | www.cnbc.com（aa882b18、自動） | theme 1/1、Evidence 2/2、記事 0/0 | 同上 |
| commonslibrary.parliament.uk | **UK House of Commons Library**（f9e704ca、人手） | commonslibrary.parliament.uk（994a9d29、自動） | theme 1/1、Evidence 1/1、記事 0/0 | 同上 |
| eur-lex.europa.eu | eur-lex.europa.eu（996869ca、自動 2026-09-03） | eur-lex.europa.eu（17383d8c、自動 2026-09-04） | theme 1/1、Evidence 1/1、記事 0/0 | 両方自動登録の完全重複。古い方を残し、人手で名称（EUR-Lex等）を付与するのが妥当 |

- 紐付け記事数は全て0（曖昧なためbackfillされていない）。canonicalを決めてAlias（`geo_source_domains`）を統合すれば、以降の記事は自動紐付けされる。
- どちらのSourceにも **Evidence文書（geo_documents.source_id）とSource↔Themeリンクが1件ずつ**あり、merge時は参照の付け替え（source_id UPDATE）が必要。

## 2. 名称が重複するもの（domain重複ではない）
- `internal analyst report` が **5件**（内部資料のSource）。domainを持たないため記事紐付けには影響しないが、Library上で区別できない。用途別に命名するか1件へ統合するか要判断。

## 3. 構造的な品質課題
- **自動登録Sourceが76/108件**（name＝domain名、media_type・region・description等なし）。Library検索・おすすめの精度が低い。
- 原因: `geo_expert_research_service.get_or_create_domain_source` が Research 時に **テーマ単位でdomain名のSourceを自動作成**する（既存挙動）。Source Library導入後は、人手登録SourceをSource Master（`theme_id`なし）として優先し、自動登録は既存domainにマッチしたらそちらを再利用する形への見直しが望ましい。

## 4. 次タスク（Source Deduplication）で決めること
1. canonical選定ルール（人手登録優先／関連数）の承認
2. merge手順: 参照付け替え（geo_documents / geo_source_theme_links / geo_source_domains / Source Set member / geo_ingestion_runs等）→ 重複側をarchive（soft delete）
3. 自動登録ロジックの見直し（get_or_create_domain_sourceのdomain照合化）
4. 76件の自動登録Sourceへのmedia_type/region付与（LLM Tag提案の活用）
