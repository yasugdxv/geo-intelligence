# Media Intelligence — 初期Universe仕様（次タスク向け、今回は未実装）

2026-08-30、ユーザーより提示。10 Key Actors Historical Intelligence Buildの依頼（同日）
23章「今回はMedia Intelligenceを実装しない」の方針通り、**本ドキュメントは次タスク
（Media Intelligence構築）向けの仕様記録であり、今回のActor Intelligence Buildでは
実装しない**。Actor/Media Intelligenceを同時実装すると問題発生時に原因切り分けができなく
なることを避けるため。

唯一、今回のActor Intelligence Build内で収集してよいとされているのは、Actor側の
`strategic_communication_pattern`（どのMedia/Channelを利用する傾向があるか）の
Historical Evidenceのみ。本ドキュメントの分類・媒体リストは、その収集作業时に「このActorが
どのMedia Typeを使ったか」を判定するための参照分類として使う。

## Media Universe（約40〜50媒体、Case/Region filterで絞り込む前提）

「有名メディア一覧」ではなく、異なる情報環境・Actor Signalを比較できる媒体選定。

### 1. Global Core（全ケース共通の基準線、Mainstream / global policy narrative）
Reuters / Associated Press / AFP / Bloomberg / Financial Times / Wall Street Journal /
New York Times / Washington Post / BBC / The Economist / CNN / Politico

### 2. Middle East / Israel / Iran / Gulf
- Israel: Haaretz / The Times of Israel / The Jerusalem Post / Ynet
- Arab/Gulf: Al Jazeera / Al Arabiya / Asharq Al-Awsat / The National
- Iran: IRNA / Tasnim / Fars / Press TV / Iran International
  （政府公式／IRGC寄り／国外反体制系を意図的に混在。2026年のIran指導部変化により、
  Ali Khamenei期と現在をTemporalに分けて扱う必要がある）

### 3. Russia / Ukraine
- Russia official/establishment: TASS / RIA Novosti / Kommersant / RBC
- Russia independent/exile: Meduza / Novaya Gazeta Europe
- Ukraine: Ukrainska Pravda / Kyiv Independent / Suspilne / Interfax-Ukraine
  （「Russia Media」と一括りにせず、国家系／商業系／国外独立系／Ukraine側を分離する）

### 4. China / Taiwan / Hong Kong
- China official: Xinhua / People's Daily / Global Times / CCTV・CGTN
- China business/semi-independent: Caixin
- Hong Kong/international gateway: South China Morning Post
- Taiwan: Central News Agency (CNA) / Taipei Times / United Daily News / Liberty Times
  （Xinhua/People's Daily/Global Times/Caixinを同じ性質の媒体として扱わないことが重要）

### 5. Korean Peninsula
- North Korea: KCNA / Rodong Sinmun（「報道」というより公式Signaling Channelとして重要）
- South Korea: Yonhap / Chosun Ilbo / JoongAng Ilbo / Hankyoreh

### 6. Europe / NATO
Le Monde / Le Figaro / Der Spiegel / Frankfurter Allgemeine Zeitung / Süddeutsche Zeitung /
Die Welt / Politico Europe / Euractiv
（Macron/Merzの分析ではReuters/FTだけでなく仏・独国内の政治文脈を見る必要がある）

### 7. Japan
NHK / 日本経済新聞 / 読売新聞 / 朝日新聞 / 毎日新聞 / 産経新聞 / 共同通信 / 時事通信
（高市氏や日本の安全保障判断は海外紙だけでは国内政治上のConstraintを取り切れない）

## Media Type分類（4種）

| Type | 役割 |
|---|---|
| Global mainstream | 世界で共有されている基準Narrative |
| Domestic establishment | 当該国の政策・Elite側Narrative |
| Official / state-linked | Actor自身のSignalingを見る |
| Independent / opposition / exile | 主流と異なるSignalや内部変化の探索 |

## 絞り込みパイプライン（次タスクでの想定運用）

```
Media Universe 約50
  ↓ Case / Region filter
Relevant Media 8〜15
  ↓ 重要記事 / Signal
5〜10
  ↓
Deep Media Intelligence 2〜5
```

## 最終的な接続像（次タスクのゴール）

Actor 10名 × Media Universeという総当たりではなく、以下の一直線の連鎖にする:

```
Actor
  ↓ 過去のDecision Episodes
Decision / Negotiation / Communication Pattern
  ↓ どのMedia / Channelをどう使ってきたか
          ×
        Media Intelligence
  ↓
今回の発言・行動・報道
  ↓
Situation Interpretation
```

## 今回のActor Intelligence Buildへの反映範囲

- `strategic_communication_pattern.typical_channels[]`へ、上記Media Type分類（global_
  mainstream/domestic_establishment/official_state_linked/independent_opposition_exile）
  を参考にした記述を許容する（Schema自体は自由記述文字列配列のため変更不要）。
- `strategic_communication_pattern.information_channel_relationship_ids[]`は、将来Media
  Intelligenceタスクでsource_registryが実装された際に、実在するsource_idを参照できるよう
  設計上の空き枠として残す（今回は空のままでよい）。
- Media Universe・Source Registry自体の実装、記事取得、媒体別Signal分析は本タスクの
  スコープ外。
