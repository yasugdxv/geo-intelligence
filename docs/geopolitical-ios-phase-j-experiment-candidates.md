# Phase J Experiment Candidates(実験候補の整理、コード変更ではない)

作成日: 2026-09-16。Phase I-B0/I-B0.1/I-B0.2/I-B0.3(Hungary/BC Strategy/Iran/
US-China、4ケース・3 Case Family)の結果から整理した**実験候補**。Phase Jの
本格実装開始条件(Phase I-B1実Expert Validation、または異なるCase Familyでの
追加独立Evidence)を満たすまで、production logicへは一切反映しない。

---

## 1. Actor Expansion

- **observed evidence**: Hungary(2v5)、BC Strategy(0v10)、Iran(部分改善、
  ネタニヤフのみ個人名生成)、US-China(Xiが一度も個人名で登場しない)。
- **case families**: A/B/C全て、ただし深刻度は一貫しない(BC Strategyのみ突出して
  深刻、Iranは改善傾向)。
- **confidence**: preliminary(3/3 familyで何らかの形は見られるが、内部一貫性が
  弱く、単一のメカニズムと断定できない)。
- **proposed evaluation**: Phase I-B1で、T0入力に直接名前が含まれない
  「間接的だが重要な主体」(退場者・第三国・カウンターパート首脳)をどれだけ
  自力で補完できるかを専用に測る評価ケースを設計する。
- **expected failure mode**: T0入力に明示的に名前が無い主体(特に相手国首脳・
  間接的第三国)を、Key Actorとして拾えない。

## 2. Indicator / Observable Signal Reasoning

- **observed evidence**: BC Strategyで具体的軍事指標(空母配備)が欠落、Iranで
  「生産減少への耐性」という特定の論争軸が一般的な価格モニタリングに薄まった。
- **case families**: B(BC Strategy/Iran)のみ、n=2(ただし同一Family内)。
- **confidence**: preliminary / 実質n=1 family。
- **proposed evaluation**: T0入力に具体的な軍事・制度的トリガー候補(部隊配備、
  法的手続きの発動日等)が実際に存在するケースで、AIのwatch_pointsがその
  具体性のレベルまで降りられるかを測る。
- **expected failure mode**: トピックレベルの一般的モニタリング軸は生成できるが、
  特定の部隊・装備・手続きといった固有名詞レベルの指標を独自に特定できない。

## 3. Absence / Anomaly Detection

- **observed evidence**: US-Chinaで、T0入力自体に構造的な手がかり(一部メンバー
  リストという「欠落の余地がある」形式)が含まれていたにもかかわらず、3候補
  全てが完全に「不在」を無視した(D. missing、confidence HIGH——他の発見と異なり
  証拠枯渇の影響を受けにくい、最もクリーンな発見)。
- **case families**: C(US-China)のみ、n=1。
- **confidence**: preliminary、ただし**根拠の質は他の発見より高い**
  (証拠枯渇というノイズ要因を排除できているため)。
- **proposed evaluation**: 「意図的に一部だけを開示した情報」を複数パターン
  (人物リスト、日程、声明文の言及範囲等)でT0入力とし、欠落自体への言及有無を
  機械的に判定する専用ミニベンチマークを作る。
- **expected failure mode**: 与えられた情報の中だけで解釈を完結させ、「何が
  言及されていないか」という否定形のシグナルへ推論が及ばない。

## 4. Counter-Hypothesis / Rejection Logic

- **observed evidence**: Hungary("ポピュリズムの終焉ではない"という留保の欠落)、
  Iran(棄却仮説自体は生成されたが、トランプの面子維持困難という棄却理由が欠落)。
  一方US-Chinaでは3つの質的に異なる仮説を生成できており、**多仮説生成そのものは
  弱くない**。
- **case families**: A(Hungary)・B(Iran)で弱さ、C(US-China)で強さ——**単純な
  「Counter-Hypothesis生成が弱い」という一般化は誤り**。
- **confidence**: preliminary、ただし**弱いのは多仮説生成ではなく、
  「なぜその仮説を退けるか」という棄却理由(特にActorの心理・体面等の
  行動論理に基づく棄却)の組み立てである**、という、より狭い仮説へ修正すべき。
- **proposed evaluation**: 生成された複数候補について、各候補への「採用/棄却
  理由」を明示的に求めるプロンプト構造でのA/Bを、実Expert Reviewで比較する
  (production変更ではなく評価のみ)。
- **expected failure mode**: 複数の解釈軸は作れるが、各解釈を退ける理由付け
  (特にActor固有の心理的・政治的制約に基づく理由)が浅い、または存在しない。

## 5. Scenario Ranking / Evidence Weighting

- **observed evidence**: Iranで、AI自身が選んだ主シナリオ(Rank1)が実専門家の
  実際の結論と異なり、実専門家の結論はAIの代替案(Rank3)に近かった(H5、
  H5a-d分解済み)。
- **case families**: B(Iran)のみ、n=1(Selection Logic材料を持つ唯一のケース)。
- **confidence**: preliminary、**証拠枯渇下での観測のため上限あり**
  (`possible ranking/selection weakness under sparse evidence`として記録、
  「ranking logicの欠陥」と断定しない)。
- **proposed evaluation**: Phase I-B1で最優先。実専門家が複数候補から選定する
  実際のSelection Logicと、AI自身の`selection_rationale`ロジックを直接比較する
  専用ケースを設計する。証拠が十分なケースでも同じ乖離が起きるかを確認する
  ことが必須(現状は証拠枯渇下でのn=1のみ)。
- **expected failure mode**: 複数の妥当なシナリオが生成されていても、どれを
  「主」として選ぶかの基準が、実専門家の重視する要因(Actor行動論理、
  構造的制約の強さ等)と食い違う。

## 6. Investigation Question Reframing

- **observed evidence**: 4ケース全て(Hungary/BC Strategy/Iran/US-China)で、
  `investigation_question`が入力テーマの転記のまま。
- **case families**: A・B・C全て(3/3 family、4/4ケース)。
- **confidence**: **preliminary/n=4だが、本Phase I-B0シリーズ内で唯一
  3 Case Family全てを横断して再現された発見**——最も確からしいシグナル。
- **proposed evaluation**: 最優先の実験候補。入力テーマをそのまま
  `investigation_question`へ複製する現在の挙動と、実専門家の実践(即座に
  「なぜ重要か」を断定的に再フレーミングする一文を加える)とのGapを、
  Phase I-B1のReframing Evaluation(A〜Dスケール、今回定義済み)で正式測定する。
- **expected failure mode**: 入力された問いをそのまま「調査すべき問い」として
  受け取り、分析対象として独自に再定義・再フレーミングするステップを経ない。

---

## 総括: Phase Jで最優先すべき2項目

1. **Investigation Question Reframing(§6)** — 3 Case Family全てで再現された
   唯一の発見、確度が最も高い。
2. **Scenario Ranking / Evidence Weighting(§5、H5)** — n=1だが実務的インパクトが
   最も大きい可能性がある発見(候補生成自体は機能しているが、選定ロジックが
   実専門家と食い違う)。

いずれも、**Phase I-B1実Expert Validationでの正式検証を経るまで、production
logicへは反映しない。**
