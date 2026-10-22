"""Geopolitical Intelligence OS: Expert Review View（読み方UIの決定論的な表示構造）。

保存済みExpert AI応答から組み立てたPresentation（geo_ios_situation_presentation_service.
build_situation_presentationの出力）に、「専門家からレビューコメントをもらう」体験用の
`expert_review`を追加する純粋な表示変換層。新しいLLM呼び出し・新しい推論・新しい事実は
一切扱わない（Frozen Reasoningには触れない）。

この層が決めるもの（全て既存フィールドの再配置・短縮・ラベル付けのみ）:
- 視点ラベル（本線/人物から見る/関係性から見る/構造から見る/別の見方/逆方向から見る）
- Stakeholder Tag（そのReadingで鍵になるActor）
- 表面／裏側、ここまでの流れ、支える動き・合わない動き、崩れる条件、次に見るもの
- 見ているレイヤー（現在/推移/人物/関係/構造/専門知見）
- LLMが使えない・失敗した場合のFallback用タイトル/コメント（既存文章を短く整えただけ）

タイトルと2〜4文のExpert Review Commentの文章品質だけは、別層（geo_ios_reading_
presentation_transform_service.get_or_build_expert_review_comments、キャッシュ付きの
Presentation専用LLM Transformation）が担い、その結果をapply_commentsで上書きする。
"""
import re

from geo_ios_situation_presentation_service import (
    _HYPOTHESIS_TYPE_LABELS,
    _strip_embedded_technical_refs,
)

EXPERT_REVIEW_VERSION = "expert_review_v1"

# 内部用語・分析フレームワーク用語（UIとLLM出力へ漏らさない）。保存済み応答の自由記述には
# "hypothesis_registerのH0"のような内部表現がそのまま混じるため、表示・LLM入力の前に整える。
# 文末の括弧書き「（as_of=2026-09-25）」は括弧ごと除き、直前の句点は残す。文頭・文中の
# 「as_of=2026-09-03。」「as_of 2026-09-03、」は直後の句読点までを除く。
_AS_OF_PAREN_PATTERN = re.compile(r"\s*[（(]\s*as_of[^）)]*[）)]")
_AS_OF_PATTERN = re.compile(r"as_of\s*[=:：]?\s*\d{4}-\d{2}-\d{2}[^。、]*[。、]\s*")
_AS_OF_TRUNCATED_PATTERN = re.compile(r"[（(]?\s*as_o[^）)。]*$")
_AS_OF_LEFTOVER_PATTERN = re.compile(r"as_of\S*")
# 内部フィールド名（snake_case）の列挙「supporting_evidence_ids/weakening_evidence_relevance/…」
_SNAKE_CASE_LIST = re.compile(
    r"(?<![A-Za-z0-9_])[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?:/[a-z][a-z0-9]*(?:_[a-z0-9]+)+)*(?![A-Za-z0-9_])")
# 値全体がID（UUID・Taiwan_Gov等のエンティティ参照）の文字列
_WHOLE_ID_PATTERN = re.compile(
    r"^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+)$",
    re.IGNORECASE)
# 仮説ID（H0〜H9）だけを対象にする。H20/H200のような製品名（2桁以上）は消さない。
_HYP_PAREN_PATTERN = re.compile(r"(?<![A-Za-z0-9_])H\d(?!\d)(?:\s*[+＋]\s*H\d(?!\d))*[（(]([^）)]*)[）)]")
# 仮説IDに続く内部名（H1_instrumental_indirect 等）も一緒に除く。残ると「この見立て_instrumental_indirect」になる
_HYP_KEY = r"(?:_[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*)?"
_HYP_WORD_PATTERN = re.compile(rf"(?<![A-Za-z0-9_])H\d(?!\d){_HYP_KEY}(?:\s*[+＋]\s*H\d(?!\d){_HYP_KEY})*(?:仮説|シナリオ)?")
_SCENARIO_LABEL_PREFIX = re.compile(r"^(?:代替|主)シナリオ\d*\s*[：:]\s*")
_REGISTER_PATTERN = re.compile(r"hypothesis_register(?:の|における|内の|に)?")
_ANALYSIS_LABEL_PATTERN = re.compile(
    r"(?:\b[A-Z][A-Za-z\-]*(?:\s[A-Za-z\-]+){0,3}\s(?:Fit|Check|Invalidation)\s*[:：]\s*)")
_REPORT_PHRASE_PATTERNS = [
    re.compile(r"^提供資料(?:と既存知識)?の範囲では、?"),
    re.compile(r"^現局面の主シナリオは、?"),
]

# UI/LLM出力に出てはいけない語（検出したら出力を採用しない/タグから除外する）
FORBIDDEN_TERMS = [
    "hypothesis_register", "Evidence Fit", "Actor Model Fit", "Premise Invalidation",
    "Higher-order", "as_of", "CQAS", "Primary Reading", "Alternative Hypothesis",
    "Switch Condition", "document_id", "foundation_pack", "supporting_knowledge_ref",
    "scenario_candidate", "response_id",
]
_FORBIDDEN_REGEX = re.compile(r"\bH\d\b|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

_PAREN_PATTERN = re.compile(r"[（(][^）)]*[）)]")


# ---- Presentation Hygiene（ユーザー向け文章に残してはいけないもの、2026-10-04） -----------------
# 保存済み応答の自由記述には、分析フレームワークの内部ラベル（"Evidence Fit:"等）、英語の内部用語
# （"Current Evidence"等）、引用記法を除去した跡の壊れた断片（"、の抜粋タイトルが示すように"
# "また、で台湾総統が"）、ソースのファイルパスが混じる。製品名・政策名・略称（H200/GPU/BIS/
# MATCH/JCPOA/TSMC等）は意味のある固有名詞なので、一切変換しない（回帰テストで担保）。
_FRAMEWORK_LABEL_PATTERN = re.compile(
    r"(?:Decision Relevance|Evidence Fit|Actor Model Fit|Higher-order Objective Check|"
    r"Premise Invalidation|Constraint Check|Timing Fit|Means Check|Counterfactual Check|"
    r"Actor-intent(?: Check)?|Frame-breaking(?: Check)?)\s*[:：]\s*")
# 英語の内部用語→自然な日本語（単語として現れる場合のみ。大文字小文字は区別する）
_ENGLISH_TERM_REPLACEMENTS = [
    # \bは日本語（\w扱い）に隣接すると効かないため、ASCII英字だけを境界に見る
    (re.compile(r"(?<![A-Za-z])Current Evidence(?![A-Za-z])"), "現時点の根拠"),
    (re.compile(r"(?<![A-Za-z])Preference-Action Gap(?![A-Za-z])"), "志向と行動のずれ"),
    (re.compile(r"(?<![A-Za-z])Persistent Preference(?![A-Za-z])"), "持続的な志向"),
    (re.compile(r"(?<![A-Za-z])Actor Intelligence(?: Pack)?(?![A-Za-z])"), "人物・組織の分析"),
    (re.compile(r"(?<![A-Za-z])Key Stakeholders?(?![A-Za-z])"), "鍵となる人物・組織"),
    (re.compile(r"(?<![A-Za-z])Key Actors?(?![A-Za-z])"), "鍵となる人物・組織"),
    (re.compile(r"(?<![A-Za-z])Primary Key Point(?![A-Za-z])"), "今回の鍵"),
    (re.compile(r"(?<![A-Za-z])Key Point(?![A-Za-z])"), "今回の鍵"),
    (re.compile(r"(?<![A-Za-z])Observed Action(?![A-Za-z])"), "観測された行動"),
    (re.compile(r"(?<![A-Za-z])Gap(?![A-Za-z])"), "ずれ"),
    (re.compile(r"(?<![A-Za-z])Means\s*[/／:：]\s*"), ""),
    (re.compile(r"(?<![A-Za-z])insufficient(?![A-Za-z])"), "不十分"),
    (re.compile(r"(?<![A-Za-z])reason(?![A-Za-z])"), "理由"),  # 内部フィールド名が文中に残ったもの
    (re.compile(r"(?<![A-Za-z])Frame-breaking(?![A-Za-z])"), "前提を疑う"),
    (re.compile(r"(?<![A-Za-z])Surface(?![A-Za-z])"), "表面的な"),
    (re.compile(r"(?<![A-Za-z])Structural(?![A-Za-z])"), "構造面の"),
    (re.compile(r"(?<![A-Za-z])Evidence(?![A-Za-z])"), "根拠"),
]
_FILE_PATH_TOKEN = re.compile(
    r"(?<![\w/])[\w\-]+(?:/[\w\-\.]+)+\.(?:html?|docx?|pdf|md|json|txt|xlsx?|csv)\b", re.IGNORECASE)
# 引用記法を除去した跡の断片
# 読点/文頭の直後に「の抜粋…」が来る場合だけ（「記事の抜粋」等の正常な表現は対象外）
_DANGLING_CITATION_TITLE = re.compile(r"(?:(?<=[、,。])|^)\s*の抜粋(?:タイトル)?(?:が示すように|によれば|では|で)?")
_ORPHAN_PARTICLE_AFTER_COMMA = re.compile(r"([、,])([はがをにでの])(?=[一-龥ァ-ヴ「（A-Za-z])")
_ORPHAN_PARTICLE_AT_SENTENCE_START = re.compile(r"(^|[。！？]\s*)[はがをにでの][、,]?(?=[一-龥ァ-ヴ「（A-Za-z])")


def hygiene_text(text) -> str:
    """ユーザー向けに出す文章の最終整形（意味は変えず、内部表現・壊れた断片だけを除く）。"""
    if not isinstance(text, str):
        return ""
    # UUID・内部key・引用記法（既存のsanitizer）→as_of/内部フィールド名/仮説ID/分析ラベル→英語内部用語
    cleaned = _strip_embedded_technical_refs(text)
    # ファイルパス・"document_id: "の残骸は、snake_case一括除去より先に処理する（順序依存）
    cleaned = _FILE_PATH_TOKEN.sub("", cleaned)
    cleaned = re.sub(r"(?<![A-Za-z_])document(?:_id)?\s*[:：]\s*", "", cleaned)
    cleaned = _AS_OF_PAREN_PATTERN.sub("", cleaned)
    cleaned = _AS_OF_PATTERN.sub("", cleaned)
    cleaned = _AS_OF_TRUNCATED_PATTERN.sub("", cleaned)
    cleaned = _AS_OF_LEFTOVER_PATTERN.sub("", cleaned)
    cleaned = _REGISTER_PATTERN.sub("", cleaned)
    cleaned = _SNAKE_CASE_LIST.sub("", cleaned)
    cleaned = _HYP_PAREN_PATTERN.sub(r"\1", cleaned)
    cleaned = _HYP_WORD_PATTERN.sub("この見立て", cleaned)
    cleaned = _ANALYSIS_LABEL_PATTERN.sub("", cleaned)
    cleaned = _FILE_PATH_TOKEN.sub("", cleaned)
    # ID（UUID）だけが除去され、ラベルが残った跡（"document_id: " 等）
    cleaned = re.sub(r"(?<![A-Za-z_])document(?:_id)?\s*[:：]\s*", "", cleaned)
    cleaned = re.sub(r"、\s+", "、", cleaned)
    cleaned = _FRAMEWORK_LABEL_PATTERN.sub("", cleaned)
    for pattern, replacement in _ENGLISH_TERM_REPLACEMENTS:
        cleaned = pattern.sub(replacement, cleaned)
    cleaned = _DANGLING_CITATION_TITLE.sub("", cleaned)
    cleaned = _ORPHAN_PARTICLE_AFTER_COMMA.sub(r"\1", cleaned)
    cleaned = _ORPHAN_PARTICLE_AT_SENTENCE_START.sub(r"\1", cleaned)
    cleaned = re.sub(r"[{}\[\]]+", "", cleaned)  # JSON構造記号の断片
    cleaned = re.sub(r"[、,]{2,}", "、", cleaned)
    cleaned = re.sub(r"、\s*。", "。", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


# sanitize_presentation_in_placeが触らないキー（ID・列挙値・内部ラベル・調査依頼の原文）
_HYGIENE_SKIP_KEYS = {
    "evidence_id", "scenario_candidate_id", "relation", "claim_relevance", "source_type", "reading_role",
    "phase", "role", "claim_type", "key", "version", "presentation_version", "_source",
    "investigation_question", "date", "confidence", "discretion_mode", "exposure_source",
}


def sanitize_presentation_in_place(node, _key=None):
    """presentation（保存済み応答から組み立てた表示用コピー）内のユーザー向け文章へhygiene_textを
    適用する。保存済みの応答自体（Frozen）は書き換えない。ID・列挙値・調査依頼の原文は対象外。"""
    def _clean_value(s: str) -> str:
        # 値全体がID（UUID・Taiwan_Gov等のエンティティ参照）の場合は空にする（人名として表示しない）
        return "" if _WHOLE_ID_PATTERN.match(s.strip()) else hygiene_text(s)

    if isinstance(node, dict):
        for k, v in list(node.items()):
            if k in _HYGIENE_SKIP_KEYS:
                continue
            if isinstance(v, str):
                node[k] = _clean_value(v)
            else:
                sanitize_presentation_in_place(v, k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, str):
                node[i] = _clean_value(v)
            else:
                sanitize_presentation_in_place(v, _key)
    return node


def clean_text(text) -> str:
    """表示・LLM入力用の軽い整形。技術参照・内部用語・「提供資料の範囲では」等のAIレポート定型句を
    除去するだけ（要約・言い換え・補完はしない）。"""
    if not isinstance(text, str):
        return ""
    cleaned = hygiene_text(text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    for pattern in _REPORT_PHRASE_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    return cleaned.strip()


def has_forbidden_term(text: str) -> bool:
    if not isinstance(text, str):
        return False
    return any(t.lower() in text.lower() for t in FORBIDDEN_TERMS) or bool(_FORBIDDEN_REGEX.search(text))


def first_sentences(text, n: int = 1, max_chars: int = 120) -> str:
    """cleanした文章の先頭n文。max_charsを超える場合は読点で切って「…」を付ける。"""
    cleaned = clean_text(text)
    if not cleaned:
        return ""
    parts = [p.strip() for p in re.split(r"(?<=[。！？])", cleaned) if p.strip()]
    out = "".join(parts[:n])
    if len(out) > max_chars:
        cut = out[:max_chars]
        idx = max(cut.rfind("、"), cut.rfind("，"))
        if idx >= max_chars * 0.5:
            cut = cut[:idx]
        out = cut.rstrip("、，,。") + "…"
    return out


def short_actor_name(name) -> str:
    """「イラン政権（最高指導者＋IRGCを含む統合主体として暫定扱い）」→「イラン政権」。"""
    if not isinstance(name, str):
        return ""
    short = _PAREN_PATTERN.sub("", name).strip()
    return short or name.strip()


def _value(item):
    return item.get("value") if isinstance(item, dict) else item


# ---- 視点ラベル ---------------------------------------------------------------
# what_was_doubted（hypothesis_typeを人間向けに訳した既存の疑いの文）の書き出しから、
# このReadingがどの角度の読みかを決定論的に分類する。分類できない場合は「別の見方」とし、
# 「逆方向から見る」は保存済みデータ上、Actor像の前提自体を疑う読みにだけ付ける
# （UIのために逆張りを新しく作らない）。
_DOUBT_TO_PERSPECTIVE = {
    _HYPOTHESIS_TYPE_LABELS["actor_intent_alternative"]: ("人物から見る", "actor", ["actor"]),
    _HYPOTHESIS_TYPE_LABELS["structural_alternative"]: ("構造から見る", "structural", ["structural"]),
    _HYPOTHESIS_TYPE_LABELS["H2_counterparty_audience_shift"]: (
        "関係性から見る", "relationship", ["relationship", "negotiation"]),
    _HYPOTHESIS_TYPE_LABELS["H3_constraint_domestic_driver"]: (
        "構造から見る", "structural", ["domestic-politics", "structural"]),
    _HYPOTHESIS_TYPE_LABELS["actor_model_breaking"]: ("逆方向から見る", "contrarian", ["contrarian", "actor"]),
    _HYPOTHESIS_TYPE_LABELS["frame_breaking"]: ("別の見方", "alternative", ["contrarian"]),
}
PERSPECTIVE_TAG_LABELS = {
    "actor": "人物起点", "relationship": "関係性起点", "structural": "構造起点",
    "institutional": "制度面", "domestic-politics": "国内事情", "economic-interest": "経済的利害",
    "negotiation": "交渉カード", "contrarian": "別角度", "consensus": "王道", "trajectory": "推移起点",
}


def classify_perspective(card: dict) -> dict:
    if card.get("is_main") or card.get("reading_role") == "MAIN":
        return {"label": "本線", "key": "main", "tags": ["consensus"]}
    doubt = _value(card.get("what_was_doubted")) or ""
    for label_text, (label, key, tags) in _DOUBT_TO_PERSPECTIVE.items():
        if doubt.startswith(label_text):
            return {"label": label, "key": key, "tags": list(tags)}
    return {"label": "別の見方", "key": "alternative", "tags": []}


# ---- Stakeholder ----------------------------------------------------------------

def build_stakeholders(card: dict, actors: list, limit: int = 5, extra_text: str = "") -> list:
    """そのReadingを成立させている主要Actor。候補は既存のactor_roles/key_actors/response-level
    actorsだけ（保存済みデータに無い名前は作らない）。Primary判定のActorは常に含め、それ以外は
    そのReadingの本文（＋extra_text=LLMコメント等）に名前が現れる場合だけ含める——全Readingが
    同じ顔ぶれのタグになり、どの読みが誰を見ているのか区別できなくなるのを避けるため。"""
    candidates = []  # (full_name, role)
    roles = card.get("actor_roles") or []
    for r in sorted(roles, key=lambda r: 0 if r.get("role") == "primary" else 1):
        candidates.append((r.get("actor"), "primary" if r.get("role") == "primary" else "related"))
    for name in _value(card.get("key_actors")) or []:
        candidates.append((name, "related"))
    for a in actors or []:
        candidates.append((a.get("actor"), "related"))

    blob = " ".join(str(_value(card.get(k)) or "") for k in (
        "title", "description", "selection_rationale", "hypothesis_formed", "what_was_doubted",
        "why_this_matters")) + " " + (extra_text or "")
    seen, result = set(), []
    for name, role in candidates:
        short = short_actor_name(name)
        if not short or short in seen or has_forbidden_term(short):
            continue
        core = re.sub(r"(政権|政府|当局|ライン)$", "", short)
        mentioned = short in blob or (len(core) >= 2 and core in blob)
        if role == "primary" or mentioned:
            seen.add(short)
            result.append({"name": short, "role": role})
    return result[:limit]


# ---- 詳細パーツ -----------------------------------------------------------------

_PHASE_LABELS = {
    "trigger": "きっかけ・気になった点", "primary_action": "動き", "reaction": "相手・周辺の反応",
    "current": "いまの局面",
}


def build_flow(card: dict) -> list:
    flow = []
    for ph in card.get("situation_phases") or []:
        text = _value(ph.get("description"))
        short = first_sentences(text, n=1, max_chars=90)
        if not short:
            continue
        label = _PHASE_LABELS.get(ph.get("phase"), "")
        actor = short_actor_name(ph.get("actor")) if ph.get("actor") else None
        flow.append({"phase": ph.get("phase"), "label": label, "actor": actor,
                     "text": short, "full_text": clean_text(text)})
    return flow


def build_surface_behind(card: dict, stakeholders: list, actors: list) -> dict | None:
    """表面（観測された動き）と裏側（Actor→利害→行動→制約）。既存のobserved_action/wants/
    constraintをそのまま短縮して並べるだけ。情報が無いActor/項目は出さない。"""
    surface = None
    for ph in card.get("situation_phases") or []:
        if ph.get("phase") == "primary_action":
            surface = first_sentences(_value(ph.get("description")), n=1, max_chars=110)
            break
    if not surface:
        surface = first_sentences(_value(card.get("what_was_seen")), n=1, max_chars=110)

    names = [s["name"] for s in stakeholders]
    behind = []
    for a in actors or []:
        short = short_actor_name(a.get("actor"))
        if short not in names:
            continue
        row = {
            "actor": short,
            "interest": first_sentences(_value(a.get("wants")), 1, 100),
            "action": first_sentences(_value(a.get("current_move")), 1, 100),
            "constraint": first_sentences(_value(a.get("constraint")), 1, 100),
        }
        if any(row[k] for k in ("interest", "action", "constraint")):
            behind.append(row)
    if not surface and not behind:
        return None
    return {"surface": surface or None, "behind": behind}


_FILE_PATH_LIKE = re.compile(r"^[\w\-./\\]+\.(?:html?|pdf|json|md|txt|docx?|xlsx?|csv|eml|msg)$", re.IGNORECASE)


def looks_like_technical_ref(text) -> bool:
    """証拠要約の代わりにソースのファイルパス等がそのまま入っているケース（保存済みデータ側の癖）。
    UIには出さない（意味のある要約が無い項目は、作文で埋めず省略する）。"""
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    return bool(_FILE_PATH_LIKE.match(stripped)) or ("/" in stripped and " " not in stripped
                                                     and stripped.isascii() and len(stripped) > 12)


def _evidence_rows(items, fallback_texts):
    rows = []
    for it in items or []:
        summary = first_sentences(it.get("summary"), 1, 110)
        if not summary or looks_like_technical_ref(summary):
            continue
        meaning = first_sentences(it.get("rationale"), 2, 160) or None
        rows.append({"actor": short_actor_name(it.get("actor")) or None, "what": summary,
                     "meaning": meaning, "meaning_origin": "saved" if meaning else None,
                     "date": (it.get("date") or "")[:10] or None})
    if not rows:
        for t in fallback_texts or []:
            text = first_sentences(_value(t), 1, 130)
            if text and not looks_like_technical_ref(text):
                rows.append({"actor": None, "what": text, "meaning": None, "meaning_origin": None,
                             "date": None})
    return rows[:5]


def _observations_only(points):
    """supporting_points/weak_pointsの自由記述のうち、「この読みが強まる/弱まる条件」（将来の条件、
    alternative_scenarios[].strengthening_conditions/weakening_conditions由来）を除く。条件は観測された
    動きではないため「支える動き/合わない動き」に混ぜない（「崩れる条件」「次に見るもの」に既に出る）。"""
    return [p for p in (points or []) if "conditions" not in str((p or {}).get("_source") or "")]


def _clean_list(values, limit=4, max_chars=130):
    out = []
    for v in values or []:
        text = first_sentences(v, 1, max_chars)
        if text:
            out.append(text)
    return out[:limit]


def build_origin(card: dict, key_point: dict) -> str | None:
    """① この読みの起点。本線はInitial Anomaly、別の見方は疑いの文＋その理由の冒頭。"""
    if card.get("is_main") or card.get("reading_role") == "MAIN":
        anomaly = first_sentences(_value((key_point or {}).get("initial_anomaly")), 2, 200)
        if anomaly:
            return anomaly
    doubt = _value(card.get("what_was_doubted"))
    return first_sentences(doubt, 2, 200) or first_sentences(_value(card.get("what_was_seen")), 1, 160) or None


def fallback_headline_and_comment(card: dict) -> tuple[str, str]:
    """LLMが無い/失敗した場合の表示: 既存の文章を先頭文で短く出すだけ。"""
    title = card.get("title")
    description = _SCENARIO_LABEL_PREFIX.sub("", clean_text(_value(card.get("description")) or ""))
    headline = ""
    if isinstance(title, str) and title.strip() and not has_forbidden_term(title) and len(title) <= 80:
        headline = clean_text(title)
    if not headline:
        headline = first_sentences(description, 1, 70)
    rationale = _value(card.get("selection_rationale"))
    comment = first_sentences(description, 2, 220)
    if rationale and len(comment) < 120:
        comment = (comment + " " + first_sentences(rationale, 1, 120)).strip()
    return headline, comment


def build_card_review(card: dict, *, key_point: dict, actors: list, monitoring: list) -> dict:
    perspective = classify_perspective(card)
    stakeholders = build_stakeholders(card, actors)
    headline, comment = fallback_headline_and_comment(card)
    outlook = card.get("outlook") or {}
    is_main = bool(card.get("is_main") or card.get("reading_role") == "MAIN")
    watch = _clean_list(_value(outlook.get("if_correct")))
    if is_main:
        extra = [(_value(m) if not isinstance(m, dict) else m.get("text")) for m in monitoring or []]
        watch = (watch + [x for x in _clean_list(extra, 3) if x not in watch])[:5]
    contradicting = _evidence_rows(
        card.get("weakening_evidence"), _observations_only(card.get("weak_points")))
    return {
        "version": EXPERT_REVIEW_VERSION,
        "perspective": {"label": perspective["label"], "key": perspective["key"]},
        "perspective_tags": [{"key": t, "label": PERSPECTIVE_TAG_LABELS[t]}
                             for t in perspective["tags"] if t in PERSPECTIVE_TAG_LABELS],
        "stakeholders": stakeholders,
        "is_primary": is_main,
        "headline": headline,
        "comment": comment,
        "comment_origin": "fallback",
        "origin": build_origin(card, key_point),
        "surface_behind": build_surface_behind(card, stakeholders, actors),
        "flow": build_flow(card),
        "supporting": _evidence_rows(
            card.get("supporting_evidence"), _observations_only(card.get("supporting_points"))),
        "contradicting": contradicting,
        "break_conditions": _clean_list(_value(outlook.get("weakens_if"))),
        "watch": watch,
    }


# ---- Presentation全体 ---------------------------------------------------------------

def build_layers(presentation: dict, response_json: dict) -> list:
    cards = (presentation.get("reading_exposure") or []) + (presentation.get("interpretations") or [])
    profiles = (response_json or {}).get("key_actor_profiles") or []
    has_expert_knowledge = any(
        p.get("persistent_preference") or p.get("strategic_relationship_preference") for p in profiles)
    return [
        {"key": "current", "label": "現在の動き", "present": bool(cards)},
        {"key": "trajectory", "label": "これまでの流れ",
         "present": bool(presentation.get("trajectory")) or any(c.get("situation_phases") for c in cards)},
        {"key": "actor", "label": "人物・組織の思惑", "present": bool(presentation.get("actors"))},
        {"key": "relationship", "label": "関係性", "present": bool(presentation.get("relationships"))},
        {"key": "structure", "label": "制度・構造", "present": bool(presentation.get("structural_constraints"))},
        {"key": "expert_knowledge", "label": "専門家の知見", "present": has_expert_knowledge},
    ]


def build_key_actors(actors: list, limit: int = 4) -> list:
    out = []
    for a in actors or []:
        name = short_actor_name(a.get("actor"))
        if not name or has_forbidden_term(name):
            continue
        out.append({"name": name, "why": first_sentences(_value(a.get("why_it_matters")), 1, 90) or None})
    return out[:limit]


def display_cards(presentation: dict) -> list:
    """フロントが表示元にするカード一覧（reading_exposureがあればそちら、無ければinterpretations）。"""
    return presentation.get("reading_exposure") or presentation.get("interpretations") or []


def attach_expert_review(presentation: dict, response_json: dict | None) -> None:
    """presentationへ`expert_review`（全体）と各カードの`expert_review`を追加する（破壊的追加のみ、
    既存キーは変更しない）。例外は呼び出し元で握りつぶし、既存表示へFallbackさせる。"""
    response_json = response_json or {}
    actors = presentation.get("actors") or []
    key_point = presentation.get("key_point") or {}
    monitoring = presentation.get("monitoring_signals") or []
    seen_ids = set()
    for card in (presentation.get("reading_exposure") or []) + (presentation.get("interpretations") or []):
        if id(card) in seen_ids:
            continue
        seen_ids.add(id(card))
        card["expert_review"] = build_card_review(
            card, key_point=key_point, actors=actors, monitoring=monitoring)
    presentation["expert_review"] = {
        "version": EXPERT_REVIEW_VERSION,
        # 原文のone_line_assessment/initial_anomalyには「as_of=...」等の内部表記が混じるため、
        # 画面のトップ用に整えた文（内容は変えず、内部表記の除去と先頭数文への短縮のみ）。
        "point": first_sentences(_value(key_point.get("one_line_assessment")), 2, 260) or None,
        "anomaly": first_sentences(_value(key_point.get("initial_anomaly")), 2, 240) or None,
        "key_actors": build_key_actors(actors),
        "layers": build_layers(presentation, response_json),
    }


def apply_flow(presentation: dict, steps) -> bool:
    """LLM Transformationが作った「ここまでの流れ」（局面全体で共通、3〜6ステップ）を反映する。
    stepsが無い/空の場合は何もしない（各カードのDeterministic Fallbackの流れがそのまま使われる）。"""
    if not steps:
        return False
    presentation.setdefault("expert_review", {})["flow"] = {"steps": list(steps), "origin": "llm"}
    return True


def _apply_evidence_notes(review: dict, notes: list) -> None:
    """既存Evidence行に「この読みにどう効くか」を補う。保存済みの理由がある行は変更しない。
    Evidence本体（要約・ID・日付）は不変で、meaning/meaning_originだけを設定する。"""
    for n in notes or []:
        rows = review.get(n.get("side")) or []
        i = n.get("i")
        if isinstance(i, int) and 0 <= i < len(rows) and not rows[i].get("meaning"):
            rows[i]["meaning"] = n["note"]
            rows[i]["meaning_origin"] = "generated"


def apply_comments(cards: list, comments: list, actors: list | None = None) -> int:
    """LLM Transformation結果（cardsと同じ順のlist、失敗要素はNone）をカードへ反映する。
    反映できた件数を返す。タイトル・コメント・証拠メモを上書き/補完し、コメントに登場するActorが
    Stakeholder Tagに反映されるよう、Tag（と表面/裏側）だけ再計算する。それ以外の構造は触らない。"""
    applied = 0
    for card, comment in zip(cards, comments or []):
        review = card.get("expert_review")
        if not review or not comment:
            continue
        review["headline"] = comment["title"]
        review["comment"] = comment["comment"]
        review["comment_origin"] = "llm"
        _apply_evidence_notes(review, comment.get("evidence_notes"))
        stakeholders = build_stakeholders(
            card, actors or [], extra_text=f"{comment['title']} {comment['comment']}")
        review["stakeholders"] = stakeholders
        review["surface_behind"] = build_surface_behind(card, stakeholders, actors or [])
        applied += 1
    return applied
