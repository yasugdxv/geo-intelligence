# Gate 3 Pilot: Donald Trump — 完全なHistorical Intelligence構築結果

2026-08-30。10 Key Actorsのうち、既存4名(entity/core_profile保有済み)から最もTier1アクセスが
充実しているDonald TrumpをGate 3のPilotとして選定した。**依頼書22章の指示通り、ここで一度停止し
結果を報告する。ユーザー確認前に残り9名への大量展開は行わない。**

## 実施内容

1. **Decision Episode調査（実WebSearch/WebFetch）**: 6件のEpisode候補（Paris協定離脱/イラン核
   合意離脱/米朝首脳会談(シンガポール・ハノイ)/対中301条関税/NATO防衛費圧力/2020年選挙異議申立て
   ＋1月6日）について、実際にWebFetch/WebSearchで調査を実施。`trumpwhitehouse.archives.gov`の
   直接fetchで検証できたもの（パリ協定離脱演説全文等）と、検索スニペット経由でのみ確認できた
   もの（NATO/北朝鮮関連は直接fetch失敗）を明確に区別して記録。
2. **Decision Episode構造化**: 拡張済み`PRECEDENT_SCHEMA`（knowledge_type=precedent、新規テーブル
   なし）へ6件をマッピングし、`geo_knowledge_service.create_knowledge_item`でDBへ**candidate**
   として登録（`activate=True`は使用していない）。
3. **Episode横断のPattern Candidate導出**: 6 Episodeを比較し、以下を導出:
   - `persistent_preference`: 「相手の対米依存の有無で圧力戦術の帰結が変わる」という
     単一Episodeからは導けない横断的パターン
   - `decision_patterns[]`3件: (a)相手依存型の圧力先行パターン(default)、(b)低拘束力枠組みからの
     一方的離脱パターン(default)、(c)脅しと実行の水準に乖離があるパターン(exception、(a)への
     例外条件として明示)
   - `strategic_communication_pattern`: SNSでの先制的な過激発言→正式な政策行動という
     チャネル使い分けパターン
4. **既存active profileへの反映**: 既存active `actor_profile`(`6fdacbcf-...`)を直接上書きせず、
   `supersedes_id`で紐づけた新規**candidate**を作成（`65bbd686-f627-4b9b-b1c6-15fa33820dbe`）。
   Human Reviewを経ない自動active化は行っていない。

## 作成物（すべてstatus=candidate、実DB）

| knowledge_item_id | 種別 | タイトル |
|---|---|---|
| d9de59f3-c00e-47c1-a0cf-4f7c8f923eb6 | precedent | パリ協定離脱表明とその反復(2017, 2025) |
| 22319c64-ed4f-4623-bcf3-3c7232d56cc1 | precedent | イラン核合意(JCPOA)離脱と第二期での直接軍事攻撃への転換 |
| cad8cfef-729d-4a59-9590-53dc4d196bdb | precedent | 米朝首脳会談: シンガポール(2018)の融和とハノイ(2019)の交渉決裂 |
| 31f53d2e-c521-44b1-b962-c5b9d7e3889c | precedent | 対中通商301条関税と第二期の大幅エスカレーション・休戦 |
| 3072929e-a953-4062-9f09-5d0f2211e02e | precedent | NATO同盟国への防衛費増額圧力キャンペーン |
| 887cb52b-ce3b-4caf-abc2-ebd996454173 | precedent | 2020年大統領選挙結果への異議申し立てと1月6日議事堂事件 |
| 65bbd686-f627-4b9b-b1c6-15fa33820dbe | actor_profile(candidate, supersedes既存active) | Donald Trump（Pattern統合版） |

## 品質確認（依頼書Gate3チェック項目）

- **Biography要約になっていないか**: なっていない。各Episodeは「何を言ったか→何をしたか→
  何を得たか→何を諦めたか→何が制約だったか」で分離されている（例: NATO Episodeでは
  「脅しの水準」と「実際の行動(正式離脱手続き不開始)」を明確に分離）。
- **EpisodeからPattern抽出を本当にできているか**: できている。単一Episodeからパターンを
  即断せず、複数Episode比較で導出した（例: `persistent_preference`はEpisode1・2・5の
  3件比較から「相手の対米依存の有無」という条件変数を導出）。
- **Evidenceは十分か**: Tier1直接fetch検証(パリ協定演説全文)からWebSearchスニペットのみ
  (NATO・北朝鮮関連)まで、確認レベルの差を各Episodeの`sources`/`confidence`に明記した。
  `confidence`は高1件・中5件（機械的に全件「高」にしていない）。
- **反例を拾えているか**: 拾えている。`decision_patterns`の3件目(`trump_pattern_threat_
  action_gap`)は明示的にexception_conditionsとして「NATOでは脅しのみで正式離脱せず、
  パリ協定・イラン核合意では実際に離脱を完遂した」という一貫しないデータを保持している
  （矛盾を「一貫した人物像」に無理に合わせていない）。
- **Token/Cost**: 本Gate3はAzure LLM呼び出しをゼロ回で実施した（Episode調査はWebSearch/
  WebFetchのみ、DB登録は決定的Python処理）。Azure呼び出しはExpert AI実行時（Actor
  Intelligence Pack経由の読み込み）にのみ発生する設計であり、本Historical Intelligence
  構築自体はAzureコストを伴わない。

## 既存テストへの非回帰

`tests/`全体384件成功（既知の無関係6件を除く）。PRECEDENT_SCHEMA拡張・DB書き込みとも
既存機能に影響を与えていない。

## Gate 3完了・次の判断

以下の判断はユーザー確認待ち:
1. 残り9名（Putin/Xi Jinping/Kim Jong Un/Zelenskyy/Netanyahu/MBS/Macron/Merz/高市早苗）への
   同様の展開を進めてよいか
2. Trump Pilotの結果（Episode構造・Pattern導出の質）に修正が必要か
3. Human Review（Gate 6相当、Candidate→Active化）を誰がいつ実施するか
