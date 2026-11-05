# -*- coding: utf-8 -*-
"""Foundation 段階3: 先例・メディア系ナレッジを「事実と解釈を分けて」AI評価する（評価のみ・DBへ書かない）。

先例（precedent）やメディア系（media_episode/media_pattern/media_profile）は、出来事の事実と、その分析（動機・
失敗要因・選択肢・評価）の両方を含む。事実だけの基準で評価すると、分析部分が全て「出典で確認できない」になり、
どれも承認されない。そこで既存の解釈用の評価（事実は出典で確認し、解釈は事実と矛盾しなければ通す。分析は
「AIの見立て」として扱う）を、これらの種類にも使う。既存サービス(geo_knowledge_ai_review_service)は変更せず、
この実行の間だけ INTERPRETIVE_TYPES を広げる。承認条件（事実3件以上かつ60%以上を出典で確認、事実の誤りが
再確認で確定したら不可、異なるドメインの出典2つ以上、独立に複数回評価して過半数）は既存のまま。

使い方: python scripts/foundation_knowledge_review_interpretive.py --types precedent --limit 60 [--out-name NAME]
結果: evaluation/KNOWLEDGE_AI_REVIEW_FOUNDATION_<NAME>.json / .md（反映は geo_knowledge_ai_review.py --apply <json>）
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import geo_knowledge_ai_review as rv  # noqa: E402
import geo_knowledge_ai_review_service as svc  # noqa: E402

EXTRA = ("precedent", "media_episode", "media_pattern", "media_profile")
svc.INTERPRETIVE_TYPES = tuple(svc.INTERPRETIVE_TYPES) + tuple(t for t in EXTRA if t not in svc.INTERPRETIVE_TYPES)

name = "INTERPRETIVE"
if "--out-name" in sys.argv:
    i = sys.argv.index("--out-name")
    name = sys.argv[i + 1]
    del sys.argv[i:i + 2]
# --titles-file <json>: 評価対象のタイトル一覧（JSON配列）。長いタイトルをコマンドラインに並べずに済ませる
if "--titles-file" in sys.argv:
    import json as _json
    i = sys.argv.index("--titles-file")
    titles = _json.loads(Path(sys.argv[i + 1]).read_text(encoding="utf-8"))
    del sys.argv[i:i + 2]
    sys.argv += ["--titles"] + titles
rv.OUT_JSON = ROOT / "evaluation" / f"KNOWLEDGE_AI_REVIEW_FOUNDATION_{name}.json"
rv.OUT_MD = ROOT / "evaluation" / f"KNOWLEDGE_AI_REVIEW_FOUNDATION_{name}.md"

if __name__ == "__main__":
    rv.main()
