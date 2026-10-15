# Media Intelligence Build — M1 Media Universe（媒体選定）

前タスク（Actor Intelligence Build）でユーザーから提示された初期Universe仕様
（`docs/actor-intelligence/media-intelligence-future-scope.md`）をベースに、依頼書
Section3の7地域区分へ再整理・確定する。Source数自体はKPIにしない
（依頼書Section29/Section1「今回実装しない」参照）。

区分キー: `media_type` = `global_mainstream` / `domestic_establishment` /
`official_state_linked` / `independent` / `opposition_exile` / `regional_local`

## A. Global Core（12媒体）
Reuters / Associated Press / AFP / Bloomberg / Financial Times / Wall Street Journal /
New York Times / Washington Post / BBC / The Economist / CNN / Politico
— すべて`global_mainstream`。目的: 世界共通の基準Narrative・政策/金融コミュニティの
Framing・主要政府情報源との接触点。

## B. Middle East / Israel / Iran / Gulf（13媒体）
- Israel(`domestic_establishment`): Haaretz / The Times of Israel / The Jerusalem Post / Ynet
- Arab/Gulf(`domestic_establishment`〜`official_state_linked`): Al Jazeera / Al Arabiya /
  Asharq Al-Awsat / The National
- Iran(`official_state_linked`〜`opposition_exile`): IRNA / Tasnim / Fars / Press TV
  （国営・IRGC系）/ Iran International（国外反体制系、ロンドン拠点）
  — 2026年のIran指導部変化を踏まえ、体制内メディアと国外系を同一カテゴリで扱わない。

## C. Russia / Ukraine（9媒体）
- Russia official/establishment(`official_state_linked`): TASS / RIA Novosti / Kommersant / RBC
- Russia independent/exile(`opposition_exile`): Meduza / Novaya Gazeta Europe
- Ukraine(`domestic_establishment`): Ukrainska Pravda / Kyiv Independent / Suspilne /
  Interfax-Ukraine
  — 「Russia Media」を一括りにせず、国家系/商業establishment系/国外独立系/Ukraine側を分離。

## D. China / Taiwan / Hong Kong（9媒体）
- China official(`official_state_linked`): Xinhua / People's Daily / Global Times / CCTV・CGTN
- China business/semi-independent(`domestic_establishment`): Caixin
- HK/international gateway(`domestic_establishment`): South China Morning Post
- Taiwan(`domestic_establishment`): Central News Agency(CNA/Focus Taiwan) / Taipei Times /
  United Daily News / Liberty Times
  — Xinhua/People's Daily/Global Timesと Caixinを同一性質の媒体として扱わない
  （Global Timesは対外強硬シグナル発信、Xinhua/人民日報は最も統制された公式トーン）。

## E. Korean Peninsula（6媒体）
- North Korea(`official_state_linked`): KCNA / Rodong Sinmun（「報道」というより公式
  Signaling Channel）
- South Korea(`domestic_establishment`): Yonhap / Chosun Ilbo / JoongAng Ilbo / Hankyoreh
  （Chosun/JoongAngは保守系、Hankyorehは進歩系として政治的立場を分離）

## F. Europe / NATO（8媒体）
Le Monde / Le Figaro（仏） / Der Spiegel / Frankfurter Allgemeine Zeitung /
Süddeutsche Zeitung / Die Welt（独） / Politico Europe / Euractiv（EU/NATO政策コミュニティ）
— すべて`domestic_establishment`。Macron/Merz分析ではReuters/FTだけでなく仏・独国内の
政治文脈（黄色いベスト・年金改革・debt brake転換への世論等）を見る必要がある。

## G. Japan（8媒体）
NHK / 日本経済新聞 / 読売新聞 / 朝日新聞 / 毎日新聞 / 産経新聞 / 共同通信 / 時事通信
— すべて`domestic_establishment`（産経は保守系、朝日/毎日はリベラル系として政治的立場を
分離）。高市氏や日本の安全保障判断は海外紙だけでは国内政治上のConstraintを取り切れない。

## 合計
Global Core 12 + Middle East 13 + Russia/Ukraine 9 + China/Taiwan 9 + Korea 6 +
Europe 8 + Japan 8 = **65媒体**（依頼書Section29の目安40〜60をやや超えるが、
既存Actor Intelligence Buildの10 Actorとの整合を優先し許容範囲とする）。

## M3で優先的にアクセス確認が必要な媒体（前タスクで未検証）

前回のActor Source Registry構築（`docs/actor-intelligence/01_source_registry.md`）で
既にアクセス検証済みの媒体（Reuters/AP/BBC/NYT/WSJ/FT/Bloomberg/Politico Europe/
Euractiv/Der Spiegel/FAZ/Süddeutsche/Welt/Le Monde/Le Figaro=すべて直接fetch不可、
Meduza/Novaya Gazeta Europe/Kyiv Independent/Times of Israel/Jerusalem Post/
Asharq Al-Awsat/Xinhua/People's Daily/Global Times/CGTN/Caixin/SCMP/CNA(Focus Taiwan)/
Taipei Times=直接fetch可、TASS/RIA/Kommersant/RBC等`.ru`ドメイン全般=ネットワークレベル
ブロック)を除き、以下が未検証:

```
Middle East: Al Jazeera, Al Arabiya, The National, Haaretz, Ynet,
             IRNA, Tasnim, Fars, Press TV, Iran International
Russia/Ukraine: Ukrainska Pravda, Suspilne, Interfax-Ukraine
Korea: Yonhap, Chosun Ilbo, JoongAng Ilbo, Hankyoreh, KCNA, Rodong Sinmun
Japan: NHK, 日経, 読売, 朝日, 毎日, 産経, 共同通信, 時事通信
Global Core残り: CNN, The Economist, Politico(本体)
```
