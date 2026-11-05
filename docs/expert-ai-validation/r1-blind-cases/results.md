# Phase R1 Chunk6 — 新規5ブラインドケース 実施結果

実施日: 2026-09-03。全ケース `profile="deep"`（Media Intelligence・Hypothesis Register
モジュールも含むフル実行）、`GEO_EXPERT_ACTOR_INTELLIGENCE_ENABLED`/
`GEO_EXPERT_INSTITUTION_INTELLIGENCE_ENABLED`/`GEO_EXPERT_EXTERNAL_RESEARCH_ENABLED`
全て有効。質問文は`README.md`にfreeze済みのものと同一（Case5のみ、初版が抽象的すぎて
Evidence取得不能だったためv2へ具体化——差し替え自体は「正解」を埋め込むものではない）。

本ファイルはAIの出力結果の**要約**であり、「正解」を判定・追記するものではない。各Caseの
完全な生アウトプット（JSON全文、executive_summary/key_actor_profiles/main_scenario/
stakeholders/investigation_questions等、本ファイルでは省略した項目も含む）は
`raw/r1_blind_case_<code>_result.json`に保存済み。最重要Human Check（5項目）は本ファイルと
raw JSONの両方を参照しつつユーザー側で別途実施する。

---

## サマリ表

| # | Case | Primary Key Point | control_type | confidence / evidence_class | Research |
|---|---|---|---|---|---|
| 1 | 高市早苗 | 米国MATCH法など同盟側の対中半導体輸出管理枠組みが、日本の政策基本線を規定する | institutionally_constrained | 中 / current_indirect_evidence | sufficient（既存DBのみ、4件） |
| 2 | NATO(米仏独) | 米国のNATO関与の強弱（Trump再登場の可能性）が欧州側の負担分担の方向性を規定 | mixed | 中 / current_indirect_evidence | sufficient（既存DBのみ、2件） |
| 3 | TSMC/台湾 | 米国の対中半導体輸出規制枠組みそのもの | structural | 中 / current_indirect_evidence | sufficient（既存DBのみ、2件） |
| 4 | EU制裁 | EU理事会における制裁延長決定のコンセンサス要件 | institutionally_constrained | 中 / current_direct_evidence | partial（検索1回、1件追加） |
| 5 | 南シナ海(v2) | フィリピンがセカンド・トーマス礁への補給ミッションを継続できるかどうか | mixed | 中 / current_direct_evidence | sufficient（検索1回、2件追加） |

---

## Case 1 — 高市早苗（10人中1名、単独）

**質問**: 日本の高市早苗政権が進めている、対中・台湾関連の経済安全保障政策（半導体サプライ
チェーン、輸出管理、投資審査等）について、主要な意思決定主体、それぞれの目的、現在の力学、
今後の方向性を分析してください。

**候補Key Point（4件、control_type多様）**:
1. **[採用]** 米国MATCH法など同盟側の対中半導体輸出管理枠組みが日本の政策基本線を規定する（institutionally_constrained）
2. 日台中の半導体サプライチェーン構造が政策の現実的選択肢を制約する（structural）
3. 高市政権が対中強硬レトリックと産業育成・台湾連携を組み合わせる政治的方針（actor_controlled）
4. 官邸・経産省・安全保障官僚・産業界の分散した意思決定構造が運用強度を決める（distributed）

**選定理由**: MATCH法等の外部制度による制約が資料で直接示唆されており、高市政権のActor意図
（H1）はActor Intelligenceが不十分なため下位の説明と位置づけた。

**品質**: 38 pass / 1 fail（`stakeholders_grounded_in_source_pack`、既知の繰り返しパターン）。架空Evidence ID: 0件。
**Media Intelligence**: 未populated。**Key Actor**: 高市早苗（entity_id紐付け）のみ、スコープ正しく限定。

---

## Case 2 — Trump / Macron / Merz（10人中複数）

**質問**: NATOにおける防衛費負担配分と欧州の防衛自立をめぐる、米国・フランス・ドイツ間の
現在の局面について、主要な意思決定主体、それぞれの目的、現在の力学、今後の方向性を分析して
ください。

**候補Key Point（4件）**:
1. NATO防衛投資5%コミットメントが負担配分の中心軸になりつつある（institutionally_constrained）
2. **[採用]** 米国のNATO関与の強弱（Trump再登場の可能性）が欧州側の防衛自立議論を規定（actor_controlled）
3. 欧州防衛自立（EU防衛能力強化）構想の並行進展が負担分担の意味を変える（distributed）
4. **H4 Frame-breaking候補**: 「防衛費負担配分」という問い自体がNATOの本質的課題（政治的結束・核抑止・産業基盤）を単純化している可能性（structural）

**選定理由**: EPISの "Burden-Sharing or Strategic Retreat?" というフレーミングを根拠に、
米国の関与不確実性を中心軸とした。Frame-breaking候補も検討したが、Evidenceは不足として
補助的位置づけに留めた。

**品質**: 37 pass / 2 fail（`stakeholders_grounded_in_source_pack`、`supporting_knowledge_refs_grounded`）。架空Evidence ID: 0件。
**Key Actor**: Trump/Macron/Merz全員が登場。

---

## Case 3 — TSMC / 台湾貿易・産業当局（10人以外が中心）

**質問**: 台湾のTSMCおよび貿易・産業当局が、米国の対中半導体輸出規制の実務的な遵守・執行に
どう対応しているかという局面について、主要な意思決定主体、それぞれの目的、現在の力学、
今後の方向性を分析してください。

**候補Key Point（4件）**:
1. **[採用]** 米国の対中半導体輸出規制枠組みそのもの（structural）
2. 台湾政府の「非紅色サプライチェーン」志向（unknown）
3. TSMCのグローバル顧客構成・技術ロードマップに基づく企業戦略（unknown）
4. **H4(Frame-breaking)仮説**: 「輸出規制遵守」ではなくサプライチェーン再編・技術移転管理が主戦場である可能性（mixed）

**Media Intelligence（実データで機能）**: timewell.jp（想定）とjapan.storm.mg（想定）の
2媒体を比較。台湾政府と日本の「非紅色サプライチェーン」協力という政治的に強い言葉を見出しに
採用している点等を観測し、editorial framingの仮説をconfidence=低で記録（evidence_class
capping正しく適用）。

**品質**: 37 pass / 2 fail（`stakeholders_grounded_in_source_pack`、`actor_model_challenged`）。架空Evidence ID: 0件。
**Key Actor**: `Taiwan_Gov`/`TSMC`（entity未紐付け、LLMが自ら命名——想定通り）。

---

## Case 4 — EU対ロシア制裁パッケージの更新・延長プロセス

**質問**: EUの対ロシア制裁パッケージの更新・延長プロセスをめぐる現在の局面について、主要な
意思決定主体、それぞれの目的、現在の力学、今後の方向性を分析してください。
（質問文に「unanimity」「拒否権」等の語は含めていない）

**候補Key Point（4件）**:
1. **[採用]** EU理事会における制裁延長決定のコンセンサス要件（institutionally_constrained）
2. 対ロ抑止・ウクライナ支援を安全保障上の優先課題とする政治的コンセンサス（structural）
3. 加盟国間のエネルギー・産業負担の分配をめぐる内部調整（distributed）
4. EU制裁枠組みの法的・制度的自動延長メカニズム（H0 Surface）（institutionally_constrained）

**特筆**: 質問文に一切ヒントを与えていないにもかかわらず、「全会一致（コンセンサス）要件」を
自力で発見しPrimaryに選定。H0（自動延長というSurface解釈）も検討した上で、その自動性自体が
コンセンサス要件に支えられているとして区別している。

**Media Intelligence**: EU理事会公式サイトの発信意図（対ロ抑止コミットメントの明示）を
observed、加盟国個別政府・企業・市民の見解が本文に含まれない点をsource_selectionとして記録。

**品質**: 37 pass / 2 fail（`stakeholders_grounded_in_source_pack`、`actor_model_challenged`）。架空Evidence ID: 0件。Research: partial（Direct Evidence 1件のみ、目安2件に届かず）。

---

## Case 5 (v2) — 南シナ海セカンド・トーマス礁（仁愛礁）の中比対立

**質問（v2、具体化後）**: 南シナ海のセカンド・トーマス礁（仁愛礁）をめぐる、中国海警局と
フィリピンとの最近の対立事案について、主要な意思決定主体、それぞれの目的、現在の力学、
今後の方向性を分析してください。

**候補Key Point（4件）**:
1. **[採用]** フィリピンがセカンド・トーマス礁への補給ミッションを継続できるかどうか（mixed）
2. 中国が海警局を通じてどこまで物理的妨害・暴力をエスカレートさせるか（unknown）
3. 米比同盟・国際法（2016年仲裁判決等）を背景としたフィリピンの対中姿勢の持続性（structural）
4. **H4 Frame-breaking候補**: 拠点維持そのものより国内政治・世論向けの対中強硬アピールが主目的である可能性（distributed）

**Media Intelligence（本ケースの本来の狙い、実データで機能）**:
- **Joongang Ilbo（韓国メディア日本語版）**: 「こん棒暴行」「真実攻防」等の強い言葉で
  身体的暴力疑惑を強調するドラマティックな見出し。`narrative_deviation`として
  「単なる危険接近・放水報道より一段強いトーン」と記録。米国・米比同盟の反応が記事から
  欠落している点を`omitted_actor_analysis`として検出。
- **The Manila Times（フィリピン国内紙）**: フィリピン側のresupply mission遂行を前面に
  出す構成。中国側の詳細な主張・中国国内世論の反応が記載されていない点を欠落として検出。

**品質**: 36 pass / 3 fail（`stakeholders_grounded_in_source_pack`、
`media_omitted_actor_analysis_grounded`、`actor_model_challenged`）。架空Evidence ID: 0件。
Research: 0→2件、sufficient。

---

## 横断的な所見

- **Institution/structural constraint認識（問題④）**: Case4で質問文に一切ヒントを与えずに
  「EU理事会の全会一致要件」を自力発見——最も明確な改善の裏付け。
- **Frame-break候補の自発的生成（問題⑥）**: Case2・3・5で"H4"ラベル付きのFrame-breaking
  候補が自発的に出現（いずれも採用はされず、Evidence不足を理由に補助的位置づけへ——「大胆な
  仮説生成・保守的な確信度昇格」の設計通りの挙動）。
- **Media divergence検出（問題⑤の一部）**: Case3・4・5でMedia Intelligenceが実際に機能し、
  複数媒体の報道の違い・欠落主体を具体的に検出。特にCase5は本来の検証目的をそのまま達成。
- **`hypothesis_register`（H0-H4の専用格納先）は5件中0件で空**——Frame-break等の競合思考
  自体は`candidate_key_points`/`media_source_context.hypotheses`側で機能しているため、
  実害は限定的だが、設計上の本来の置き場所が機能していない点は要検討（P1候補）。
- **架空Evidence ID**: 5件全てで0件（Stop Gate5維持）。
- **既知の繰り返しfailure**: `stakeholders_grounded_in_source_pack`が5件全てで失敗（Source
  Pack本文への文字列一致チェックが、Actor/Institution Intelligence Pack由来の主体名を
  拾えない既存の設計ギャップ、Chunk1-5とは無関係）。

## Human Check用メモ

各Caseについて、最重要Human Check（5項目、ユーザー側で実施）の際は上記の候補Key Point・
選定理由・Media Intelligence所見を参照。本ファイル自体は「正解」を判定しないため、Human
Checkの結果は別途スコアカードとして記録すること。
