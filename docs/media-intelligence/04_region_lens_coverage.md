# Media Intelligence Build — M4準備 Region × Lens Coverage Matrix（ドラフト）

M3(Source Accessibility Audit)の並列調査結果が揃い次第、reachable列を確定する。
現時点ではM1/M2のUniverse選定に基づくCoverage設計を先に固める。

## Region × Lens Coverage

| Region | Global | Official | Domestic Establishment | Independent | Local Language | Opposition/Exile | Specialist | Physical/Data |
|---|---|---|---|---|---|---|---|---|
| Global共通 | Reuters/AP/AFP/Bloomberg/FT/WSJ/NYT/WaPo/BBC/Economist/CNN/Politico | UN Security Council, NATO | — | — | 英語中心 | — | CSIS/RAND/IISS/Chatham House/Carnegie/Brookings/RUSI | ACLED/UNHCR/OCHA/OpenCorporates |
| Middle East(Israel/Iran/Gulf) | ✓(上記) | 各国政府公式(Actor Registry既存) | Haaretz/Times of Israel/Jerusalem Post/Ynet/Al Jazeera/Al Arabiya/Asharq Al-Awsat/The National | — | ペルシャ語(IRNA/Tasnim/Fars原文)、アラビア語(Asharq Al-Awsat原文) | Iran International | INSS/IranSource/Gulf Research Center | MarineTraffic(ホルムズ海峡) |
| Russia/Ukraine | ✓ | kremlin.ru等(ネットワーク遮断済み確認) | TASS/RIA/Kommersant/RBC(`.ru`遮断)/Ukrainska Pravda/Suspilne/Interfax-Ukraine | — | ロシア語(TASS等原文、ただしアクセス不可)、ウクライナ語 | Meduza/Novaya Gazeta Europe | Carnegie Russia Eurasia Center | ACLED(ウクライナ紛争データ) |
| China/Taiwan/HK | ✓ | 情報不足(NPC等JS-heavy SPAで前回fetch失敗確認済み) | Xinhua/人民日報/Global Times/CCTV・CGTN/Caixin/SCMP/CNA/Taipei Times | — | 中国語(Xinhua/人民日報原文) | — | INDSR(台湾) | — |
| Korean Peninsula | ✓ | 情報不足(kantei.go.jp型の公式サイトに相当する北朝鮮側は未検証) | Yonhap/Chosun Ilbo/JoongAng Ilbo/Hankyoreh | — | 韓国語 | — | Sejong Institute/Asan Institute | — |
| 北朝鮮 | — | KCNA/Rodong Sinmun(公式Signaling Channelとして扱う、Media区分と別軸) | — | — | 朝鮮語 | — | — | — |
| Europe/NATO | ✓ | elysee.fr/diplomatie.gouv.fr/bundesregierung.de/bundeskanzler.de(Actor Registry既存確認済み) | Le Monde/Le Figaro/Der Spiegel/FAZ/Süddeutsche/Welt/Politico Europe/Euractiv | — | フランス語/ドイツ語原文 | — | Chatham House/RUSI(Global共通と重複) | — |
| Japan | ✓ | kantei.go.jp(Actor Registry既存確認済み) | NHK/日経/読売/朝日/毎日/産経/共同/時事 | — | 日本語原文 | — | — | — |

## 既知のCoverage Gap（依頼書Section16 lens_gap原則）

1. **Religious/Ethnic/Tribal/Non-state(Lens10)**: 体系的な情報源が確保できておらず、
   M2で明示した通り事前登録を見送っている。Pilot Case選定時にこのGapが問題になるかを
   確認する。
2. **北朝鮮国内の独立系/現地専門家(Lens3)**: 事実上存在しない。KCNA/Rodong Sinmunの
   公式Signalingのみでカバーせざるを得ない構造的Gap。
3. **Counterparty/Third-country(Lens12)**: 固定Source一覧ではなくRetrieval Ruleとして
   Case実行時に都度検索する運用のため、事前のCoverage Matrixには現れない
   （M9実装時に明示的な運用ルールとして記録する）。
4. **Physical/Data系のMiddle East以外への展開**: 現状MarineTraffic(ホルムズ海峡)・
   ACLED(ウクライナ)以外は地域別の紐付けが弱い。Pilot Caseの地域に応じて個別追加を検討。
