# Article Discovery Policy（SOURCE_INTELLIGENCE_V1_1 で固定）

## 1. 動作順序

Article Discoveryは次の順で動作する。

```
Article
  → related article retrieval（関連記事の検索）
  → change / issue clustering（「何が変わっているか」でまとめる）
  → Theme Candidate proposal（テーマ候補の提案）
```

- **1記事1Themeにはしない**（複数記事を根拠にまとめる。検証: 2記事以上、かつ2ドメイン以上または3記事以上）。
- Themeは単なるイベント名ではなく、**「何が変わっているか」「継続して見る価値があるか」**を表現する。
- 候補は自動確定せず、**proposal / pending（提案・未確定）**として扱う。正式Themeへの自動昇格はしない。

## 2. Evidence Policy

- すべての候補に根拠Articleを追跡可能にする。最低限: **title / source / published date / original URL**。
- **材料不足の場合は候補を生成しない**（「該当する新しいテーマ候補は見つかりませんでした」）。
- **LLMだけでThemeを生成しない**。LLMに渡すのは検索で絞った記事のみで、LLMが返す根拠IDは実在する記事IDに限る（実在しないIDの候補は破棄）。

## 3. Duplicate Policy

既存の Theme Candidate / Monitoring Theme / Theme と重複する場合は、新しい候補を乱造せず
**「既存テーマに新しい動きがあります」**として扱う（根拠記事の重なり50%以上、またはタイトル類似度0.6以上）。
却下済み（DISMISSED）の候補と同じ根拠の提案は再作成しない。

## 4. Source Restriction

Source Selectorで選択されている場合、**選択Source（Source Set含む）の記事だけ**を検索・根拠に使う。選択外Sourceの記事は根拠に使わない。
未選択の場合は全登録Source＋既存記事すべてが対象。

## 5. 期間・性能

- 既定は直近30日（入力文で変更可、上限180日）。`published_at` に下限を付けて検索（`created_at` は索引なし）。
- LLMに渡すのは関連度上位40件（同一domain最大6件）。同一入力は10分キャッシュ。
- AI（Azure OpenAI）が使えない時は、エラーを表示する（「結果なし」と誤解させない）。

## 6. UI Policy

V1_1では戻さない: プリセット / 旧5フィルター / 分類タブ / ブラックボックスな注目度バッジ。
基本UX: 自然言語で探索 → AI提案 → キーワード絞り込み → 新しい順/古い順 → Monitoring / Research / Expert AI。
削除は論理削除（DISMISSED）。

## 7. Candidate origin

DBスキーマ変更なし。`geo_theme_candidates.generation_model` の接頭辞から判定する:
`article_discovery/` = 記事から発見、`monitoring/` = モニタリングから発見、`manual/` = 手動、それ以外 = Situationから発見。
`proposal_state` は origin が Situation 以外で status が NEW の時 `pending`。
