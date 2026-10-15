"""地政学インテリジェンス基盤 Phase B-B: Cross-Department Inquiry Receiver（UC2・UC8接続）。

他部門のDepartment Intelligence AI（今回はSustainability Department Intelligence AI）から
request_type="user_question"で受けたRequestを、「Geopolitics Department Intelligence AI」
として処理する。既存のgeo_intelligence_service._process_user_question（無変更）とは並行する
別経路であり、geo_intelligence_service.process_requestはnormalized["source_department"]が
指定されている場合のみ本モジュールへ分岐する（未指定の既存呼び出し元は影響を受けない）。

役割分担（設計原則）:
    Geopolitics Subject Expert AI（geo_expert_engine）: 地政学専門分析そのものを深く考える。
    Geopolitics Department Intelligence AI（本モジュール）: 既存Geo Intelligence/Knowledgeを
        まず検索・判定し、Expert AIは「使う側」として呼び出すオーケストレーション層。

新しいRetrieval/Sufficiency/Expert呼び出しロジックは実装しない。既存の
geo_intelligence_service（retrieve_knowledge/assess_sufficiency/assess_confidence/
generate_analysis/build_references/_finalize_response）をそのまま呼び出し、Department
Intelligence向けのoptional metadata（department_intelligence）を追加で組み立てるだけに
留める（geo_intelligence_schema.GeoIntelligenceResponseの既存フィールドは一切変更しない）。

Phase B-B1のスコープ（受信・オーケストレーション層）:
    - source_department指定時のみ本モジュールへ分岐する受信口を追加。
    - resolution_mode/covered_dimensions/missing_dimensions/freshness_sufficientは、既存の
      assess_sufficiency（1軸のsufficient/partial/insufficient）とretrieval結果から導出する
      粗い粒度に留めた。

Phase B-B2（本実装、追加分）のスコープ: 既存Geo Intelligence Retrievalの拡張。
    - geo_intelligence_service.retrieve_knowledge（Foundation/Precedent/Claim/Relationship/
      Internal Report等）に加え、Department Inquiryでは以下も既存関数をそのまま呼んで取得する
      （新しい検索エンジンは作らない、item5）:
        * Actor Intelligence: geo_report_synthesis_service.build_entity_scoped_actor_
          intelligence_pack（Case非依存のentity_id起点版、既存build_actor_intelligence_pack
          はcase_id前提のため外部Requestには使えない。resolve_entities_and_issuesで解決済みの
          stakeholder Entityをそのまま渡す）
        * Geo Reports: geo_report_service.search_reports（既存Intelligence Library検索、
          item27の「将来API公開可能な境界」の一部として今回から利用）
    - Media Intelligence（geo_media_intelligence_service.build_media_intelligence_pack）も
      取得はするが、この関数自体が「質問非依存の全Active Pattern/Profile」を返す設計
      （Expert AI向けの常時Context）のため、今回のcovered_dimensions/missing_dimensions
      （＝今回の質問に対して関連Knowledgeが見つかったか）には含めない。参考情報として
      retrieval["media_intelligence"]に保持するのみ（B-B5 Response Synthesis等で使う候補）。
Phase B-B3（本実装、追加分）のスコープ: Dimension別Sufficiency判定（_assess_dimensions）。
    - 「Retrieved（取得できた）」を無条件に「Covered（今回の質問をカバーできた）」扱いに
      しない。Dimensionごとに Coverage（実際に使える情報か）/ Freshness（新しさが必要な
      dimensionかどうかで判定基準を変える）/ Conflict（Contested Knowledgeとの食い違い）を
      個別に評価する（_assess_dimensions）。
    - Freshnessは一律判定しない: foundation/precedent/internal_expert_knowledge/stakeholder/
      relationship/internal_report/scenario_hypothesisは構造的・永続的な情報として鮮度を
      問わない（FRESHNESS_AGNOSTIC_DIMENSIONS、Actor core belief・Historical precedent相当）。
      observed_fact/actor_claim/external_assessment/actor_intelligence/geo_reportsは
      新しさが問われうるdimensionとして、実際に取得した個々の項目のas_of（Actor
      Intelligenceはbuild_entity_scoped_actor_intelligence_pack側で既に計算済みの
      current_posture.freshnessをそのまま再利用、重複計算しない）で判定する。
      freshness_requirement="high"の場合のみ、Stale判定がそのdimensionをmissingへ降格させる
      （通常時は「古いが存在する」ことをcoveredのまま許容し、機械的に切り捨てない。item9）。
    - Conflict: 既存retrieve_knowledgeが取得済みのcontested（Sufficiency/Referenceには
      使わない参考情報として保持されていたバケット）にfoundation/precedent相当の対抗
      Knowledgeが存在する場合、そのdimensionをcovered/missingどちらにもせずconflicting
      として分離する（無理に1つへ平均化しない、item19）。
    - Media Intelligenceはこのdimension評価に含めない（B-B2の判断を継続、質問非依存の
      常時Contextであり「今回の質問をCoverした」と主張すると過大評価になるため）。
    - 出力はcovered_dimensions/missing_dimensions/conflicting_dimensions/selected_source_ids/
      freshness_sufficientの5項目に加え、根拠を追跡できるdimension_assessment（dimension別の
      status/freshness/source_idsの明細）を追加する。これがPhase B-B4の
      「不足dimensionだけをExpert AIへ渡す」選択的呼び出しの入力になる。
Phase B-B4（本実装、追加分）のスコープ: Selective Expert Invocation（選択的Expert AI呼び出し）。
    - 既存gis.generate_analysis/should_call_expert_ai（1軸のsufficient/partial/insufficient
      基準）はDepartment Inquiryではもう使わない。代わりにdetermine_resolution_planが
      dimension_assessment（B-B3）からresolution_modeを決定する:
        missing=0 かつ conflicting=0        → existing_only（Expert AI呼び出しなし）
        covered=0（既存Coverageがほぼ無い）    → expert_only（質問全体を新規分析）
        それ以外（missing>0 または conflicting>0、かつcoveredが1件以上）
                                             → existing_plus_expert（不足/矛盾分のみ）
      Expert AIへ渡す対象はmissing_dimensionsだけでなくconflicting_dimensionsも含める
      （Conflictは「既存情報があるから回答可能」ではなく、判断を追加で必要としている状態）。
    - 既存Expert AI Contract（geo_expert_engine.run_expert_request_with_mode）自体は無変更。
      「不足dimensionのみ」を構造化Requestとして渡す新Contractは作らず（無理なEngine改修は
      しない、item10）、build_missing_dimension_requestが既存questionフィールド（自然文）へ
      Already covered（既知の前提として再分析不要）/ Need analysis（既存Coverageが不足） /
      Need resolution（既存Knowledge同士が矛盾、対抗Knowledgeのtitleを明示）を追記する形で
      合成する。Covered部分は「再分析するな」と完全に封じず、Expertが重大な誤り・陳腐化を
      発見した場合は指摘してよい旨を明記する（Covered判定へのChallengeを許容）。
    - Expert AI呼び出し自体が失敗した場合、既存Retrieval/Dimension評価の結果は破棄しない
      （item: 部分的にでも回答できるならB-B5へ渡す）。gis._digest_analysisへフォールバックし、
      geo_assessmentへ「Expert AI呼び出しに失敗したため、既存Geo Intelligenceの範囲までの
      回答です。以下は未解決です: ...」を付記する。department_intelligence.expert_call_error
      にエラー内容を残す（Outer try/exceptによるstatus="failed"への全体降格はしない）。
    - department_intelligenceへexisting_source_ids（=selected_source_idsと同値、Phase B-B5
      向けの明示的なAlias）・expert_response_id・expert_analyzed_dimensionsを追加し、
      Existing起源とFresh（Expert AI）起源の境界をPhase B-B5が追跡できるようにする。
Phase B-B5（本実装、追加分）のスコープ: Response / Provenance統合。
    - How answered（resolution_mode） ≠ How sufficient（knowledge_sufficiency）を明示的に分離
      する。resolution_mode="existing_only"は「Expert AIを使わなかった処理経路」を意味する
      だけであり、「既存情報だけで十分だった」ことを意味しない（azure_client未指定・
      analysis_requested=False等でもexisting_onlyになるため）。department_intelligenceへ
      knowledge_sufficiency（既存gis.assess_sufficiencyの値、top-levelのknowledge_sufficiency
      と同じ）を明示的に複製し、両者を別々に読める形にする。
    - expert_requested_dimensions（Expert AIへ分析を依頼した対象、失敗時も保持）と
      expert_analyzed_dimensions（実際にExpert Responseが成立した場合のみ、成功時に限って
      requestedと同じ内容を複製）を分離する。expert_response_id/expert_analyzed_dimensionsは
      Expert呼び出しが実際に成功した場合のみ非空にする（「分析しようとした」と「分析できた」
      を混同しない）。
    - unresolved_dimensions = (missing_dimensions ∪ conflicting_dimensions) のうち
      expert_analyzed_dimensionsに含まれないもの（＝既存Intelligenceでも新規Expert分析でも
      最終的に埋まらなかった論点）。Confidence Capの判定にはこちらを使う（raw
      missing_dimensions/conflicting_dimensionsは既存Intelligence単体のB-B3時点の
      スナップショットとして保持したまま変更しない、Existing/Fresh境界の保持）。
    - Confidence: 既存gis.assess_confidence（Retrieval内容のみに基づく）の値を基礎としつつ、
      unresolved_dimensionsが1件以上、またはexpert_call_errorがある場合は"high"を"medium"へ
      Cap（決して引き上げない、_cap_confidence）。この値をtop-level response["confidence"]と
      department_intelligence["confidence"]の両方へ使う（矛盾した値を返さない）。
    - provenance: covered/conflictingなdimensionのsource_idと、成立した場合のexpert_response_id
      を、spec由来のOrigin分類（existing_intelligence/active_knowledge/actor_intelligence/
      live_evidence/fresh_expert_analysis）付きで列挙する（_build_provenance、item16）。
      存在しない情報は載せない。
"""
import os
import time
from datetime import datetime, timezone

from config_utils import load_config

import geo_freshness_service
import geo_intelligence_service as gis
import geo_knowledge_access_policy
import geo_knowledge_gap_service
import geo_media_intelligence_service
import geo_report_service
import geo_report_synthesis_service

_config = load_config()


def sync_expert_enabled() -> bool:
    """Phase B-C: 通常のCross-Department Inquiryからは同期Expert AI呼び出しを既定OFFにする
    （geo_expert_engineは1回数分〜規模の深い調査エンジンであり、HTTP同期応答に置く設計ではない
    ことが実本番検証で判明したため）。既定Falseで、既存existing_plus_expert/expert_only直接
    呼び出し経路はRegression・将来の管理者/内部ユースケース向けにコード自体は残し、明示的に
    有効化した場合のみ従来通り同期で呼ぶ。"""
    cfg = ((_config.get("geo_intelligence") or {}).get("department_intelligence") or {})
    if "sync_expert_enabled" in cfg:
        return bool(cfg["sync_expert_enabled"])
    return os.environ.get("GEO_DEPARTMENT_SYNC_EXPERT_ENABLED", "").strip().lower() in ("1", "true", "yes")

# assess_sufficiency/_missing_categoriesが扱う既存カテゴリ（gis._ALL_CATEGORIES）に、
# Department Inquiry専用で追加取得するdimensionを加えたもの。Intelligence Need記録
# （geo_knowledge_gap_service.record_external_request_gap）へは渡さない
# （既存gap記録の対象カテゴリはgis._ALL_CATEGORIESのまま変更しない）。
DEPARTMENT_ONLY_DIMENSIONS = ("actor_intelligence", "geo_reports")
ALL_DIMENSIONS = tuple(gis._ALL_CATEGORIES) + DEPARTMENT_ONLY_DIMENSIONS
_REPORT_SEARCH_LIMIT = 5

# build_missing_dimension_request向けの表示ラベル（Expert AIへの指示文を簡潔にするため、
# 個々のItem本文ではなくdimension名の日本語ラベルのみを渡す）。
DIMENSION_LABELS_JA = {
    "foundation": "制度・構造的基礎情報", "precedent": "先例",
    "internal_expert_knowledge": "内部専門家によるナレッジ", "observed_fact": "確認済み事実",
    "actor_claim": "アクターの発言・主張", "external_assessment": "外部評価",
    "stakeholder": "関係者(Stakeholder)", "relationship": "関係性",
    "internal_report": "内部レポート", "scenario_hypothesis": "シナリオ仮説",
    "actor_intelligence": "Actor Intelligence(Core Profile/Current Posture等)",
    "geo_reports": "既存Geo Report",
}

# Provenance向けOrigin分類（spec item16の語彙をそのまま使う）。dimensionごとに固定の対応。
ORIGIN_BY_DIMENSION = {
    "foundation": "active_knowledge", "precedent": "active_knowledge",
    "internal_expert_knowledge": "active_knowledge", "observed_fact": "live_evidence",
    "actor_claim": "live_evidence", "external_assessment": "live_evidence",
    "stakeholder": "active_knowledge", "relationship": "active_knowledge",
    "internal_report": "existing_intelligence", "scenario_hypothesis": "active_knowledge",
    "actor_intelligence": "actor_intelligence", "geo_reports": "existing_intelligence",
}

_CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
_CONFIDENCE_CAP_WHEN_UNRESOLVED = "medium"


# Dimensionの性質: 古くても使える構造的・永続的な情報（Actor core belief/Historical
# precedent相当）と、新しさが問われうる情報を分ける（一律のFreshness判定をしない、item9）。
FRESHNESS_AGNOSTIC_DIMENSIONS = frozenset({
    "foundation", "precedent", "internal_expert_knowledge", "stakeholder", "relationship",
    "internal_report", "scenario_hypothesis",
})
# gis._ALL_CATEGORIES + DEPARTMENT_ONLY_DIMENSIONSのうち、上記以外が「新しさが問われうる」
# dimension（observed_fact/actor_claim/external_assessment/actor_intelligence/geo_reports）。

_RECENT_DAYS_HIGH = 90   # freshness_requirement="high"時の許容鮮度ウィンドウ
_RECENT_DAYS_NORMAL = 365  # 通常時（古い情報も許容するが、あまりに古いものはstale扱い）

_DIMENSION_ID_FIELDS = {
    "foundation": "knowledge_item_id", "precedent": "knowledge_item_id",
    "observed_fact": "claim_id", "actor_claim": "claim_id", "external_assessment": "claim_id",
    "stakeholder": "entity_id", "internal_report": "document_id", "scenario_hypothesis": "hypothesis_id",
}


def _dimension_source_ids(dim: str, items: list) -> list:
    """dimensionごとに参照可能なID field名が異なる（build_references/_record_retrieval_runと
    同じ対応関係）ため、ここでのみ吸収する。"""
    if dim == "internal_expert_knowledge":
        return [i.get("knowledge_item_id") or i.get("claim_id") for i in items
                if i.get("knowledge_item_id") or i.get("claim_id")]
    if dim == "relationship":
        id_fields = {"relationship": "relationship_id", "asset": "asset_id", "dependency": "dependency_id"}
        return [i[id_fields[i["kind"]]] for i in items if i.get("kind") in id_fields and i.get(id_fields[i["kind"]])]
    field = _DIMENSION_ID_FIELDS.get(dim)
    return [i[field] for i in items if field and i.get(field)]


def _is_recent(date_value, *, window_days: int) -> bool | None:
    """None=日付情報が無く判定不能（unknown、不利にも有利にも倒さない）。"""
    parsed = geo_freshness_service._parse_date(date_value)
    if parsed is None:
        return None
    return (datetime.now(timezone.utc).date() - parsed).days <= window_days


def _claim_dimension_freshness(items: list, freshness_requirement: str | None) -> str:
    """observed_fact/actor_claim/external_assessment向け。Claim行にはKnowledge Item側の
    as_of_date/stale_after_daysが無いため、既存classify_knowledge_freshnessは流用せず、
    valid_from/created_atの新しさで軽量に判定する。"""
    window = _RECENT_DAYS_HIGH if freshness_requirement == "high" else _RECENT_DAYS_NORMAL
    results = [_is_recent(c.get("valid_from") or c.get("created_at"), window_days=window) for c in items]
    if any(r is True for r in results):
        return "fresh"
    if all(r is None for r in results):
        return "unknown"
    return "stale"


def _actor_intelligence_freshness(actor_intelligence: dict) -> str:
    """current_postureを持たないKey Actorのみ（core_profile/persistent_preference等の
    永続的情報のみ）ならnot_applicable（Actor core belief相当、古くても使える）。
    current_postureを持つKey Actorがいる場合は、build_entity_scoped_actor_intelligence_pack
    が既に計算済みのfreshness（geo_actor_intelligence_service.compute_freshness）をそのまま
    使う（重複計算しない）。"""
    postures = [ka.get("current_posture") for ka in (actor_intelligence.get("key_actors") or [])
                if ka.get("current_posture")]
    if not postures:
        return "not_applicable"
    statuses = [(p.get("freshness") or {}).get("status") for p in postures]
    if any(s in ("current", "aging") for s in statuses):
        return "fresh"
    if all(s is None for s in statuses):
        return "unknown"
    return "stale"


def _geo_reports_freshness(geo_reports: list, freshness_requirement: str | None) -> str:
    window = _RECENT_DAYS_HIGH if freshness_requirement == "high" else _RECENT_DAYS_NORMAL
    results = [_is_recent(r.get("uploaded_at"), window_days=window) for r in geo_reports]
    if any(r is True for r in results):
        return "fresh"
    if all(r is None for r in results):
        return "unknown"
    return "stale"


def _assess_dimensions(retrieval: dict, freshness_requirement: str | None) -> list:
    """Dimensionごとに Relevance(=Retrieved)ではなくCoverage(=実際に使えるか)/Freshness/
    Conflictを個別評価する。戻り値は
    [{"dimension", "status": "covered"|"missing"|"conflicting", "freshness", "source_ids"}]。
    3つのstatusは互いに排他（1つのdimensionが同時にcoveredとmissingにはならない）。"""
    contested_present = bool(retrieval.get("contested"))
    assessment = []

    for dim in gis._ALL_CATEGORIES:
        items = retrieval.get(dim) or []
        if not items:
            assessment.append({"dimension": dim, "status": "missing", "freshness": "not_applicable",
                               "source_ids": []})
            continue
        # contestedはfoundation/precedent由来のKnowledge Itemのみ（geo_retrieval_service.
        # get_contested_knowledgeが同じissue_ids単位で返す対抗Knowledge）と対応させる。
        # Claim/Relationship等はstatus軸が別のため、ここでは対象にしない。
        if contested_present and dim in ("foundation", "precedent"):
            assessment.append({"dimension": dim, "status": "conflicting", "freshness": "not_applicable",
                               "source_ids": _dimension_source_ids(dim, items)})
            continue
        if dim in FRESHNESS_AGNOSTIC_DIMENSIONS:
            assessment.append({"dimension": dim, "status": "covered", "freshness": "not_applicable",
                               "source_ids": _dimension_source_ids(dim, items)})
            continue
        freshness = _claim_dimension_freshness(items, freshness_requirement)
        demoted = freshness == "stale" and freshness_requirement == "high"
        status = "missing" if demoted else "covered"
        assessment.append({"dimension": dim, "status": status, "freshness": freshness,
                           "source_ids": [] if demoted else _dimension_source_ids(dim, items)})

    actor_intelligence = retrieval.get("actor_intelligence") or {}
    if _has_actionable_actor_intelligence(actor_intelligence):
        freshness = _actor_intelligence_freshness(actor_intelligence)
        demoted = freshness == "stale" and freshness_requirement == "high"
        source_ids = [ka["entity_id"] for ka in actor_intelligence.get("key_actors") or []
                      if (ka.get("profile_source") or "none_available") != "none_available"]
        assessment.append({"dimension": "actor_intelligence", "status": "missing" if demoted else "covered",
                           "freshness": freshness, "source_ids": [] if demoted else source_ids})
    else:
        assessment.append({"dimension": "actor_intelligence", "status": "missing",
                           "freshness": "not_applicable", "source_ids": []})

    geo_reports = retrieval.get("geo_reports") or []
    if geo_reports:
        freshness = _geo_reports_freshness(geo_reports, freshness_requirement)
        demoted = freshness == "stale" and freshness_requirement == "high"
        source_ids = [r["report_id"] for r in geo_reports if r.get("report_id")]
        assessment.append({"dimension": "geo_reports", "status": "missing" if demoted else "covered",
                           "freshness": freshness, "source_ids": [] if demoted else source_ids})
    else:
        assessment.append({"dimension": "geo_reports", "status": "missing", "freshness": "not_applicable",
                           "source_ids": []})

    return assessment


def _conflict_titles(dim: str, retrieval: dict) -> tuple:
    """conflicting判定されたdimensionについて、既存assessment側と対抗Knowledge側の実在する
    titleのみを返す（捏造しない）。foundation/precedentのみがconflicting対象（_assess_dimensions
    参照）なので、この2種類のみ扱う。"""
    active_items = retrieval.get(dim) or []
    contested_types = gis.FOUNDATION_KNOWLEDGE_TYPES if dim == "foundation" else ("precedent",)
    contested_items = [c for c in (retrieval.get("contested") or []) if c.get("knowledge_type") in contested_types]
    active_titles = [i.get("title") for i in active_items if i.get("title")]
    contested_titles = [c.get("title") for c in contested_items if c.get("title")]
    return active_titles, contested_titles


def determine_resolution_plan(dimension_assessment: list, normalized: dict, azure_client) -> str:
    """B-B4の分岐（item: missing/conflictの有無とcovered有無で4パターン）。
    missing=0かつconflicting=0        → existing_only（Expert AI呼び出しなし）
    covered=0（既存Coverageがほぼ無い）  → expert_only（質問全体を新規分析）
    それ以外                          → existing_plus_expert（不足/矛盾分のみ）
    既存should_call_expert_aiと同じガード（analysis_requested/azure_client有無）を踏襲する。"""
    if not normalized["analysis_requested"] or azure_client is None:
        return "existing_only"
    missing = [d for d in dimension_assessment if d["status"] == "missing"]
    conflicting = [d for d in dimension_assessment if d["status"] == "conflicting"]
    covered = [d for d in dimension_assessment if d["status"] == "covered"]
    if not missing and not conflicting:
        return "existing_only"
    if not covered:
        return "expert_only"
    return "existing_plus_expert"


def build_missing_dimension_request(normalized: dict, retrieval: dict, dimension_assessment: list,
                                     resolution_mode: str) -> str:
    """既存Expert AI Contract（question文字列1本）を変更せず、その範囲でDepartment Inquiry
    向けの指示を合成する（無理なEngine改修はしない、item10）。Covered部分は「既知の前提として
    再分析不要」と伝えつつ、その前提に重大な誤り・陳腐化があれば指摘してよいことも明記する
    （Covered判定を完全に封じない）。"""
    query_text = normalized["question"]
    if normalized.get("context"):
        query_text = f"{query_text}\n\n{normalized['context']}"

    if resolution_mode == "expert_only":
        return query_text + (
            "\n\n[Geopolitics Department Intelligence AI]\n既存Geo Intelligence DBに関連情報が"
            "ほとんど見つからなかったため、上記の質問全体について新規に分析してください。")

    covered = [d for d in dimension_assessment if d["status"] == "covered"]
    missing = [d for d in dimension_assessment if d["status"] == "missing"]
    conflicting = [d for d in dimension_assessment if d["status"] == "conflicting"]

    lines = ["[Geopolitics Department Intelligence AI: 既存Intelligence Coverage]"]
    if covered:
        labels = "、".join(DIMENSION_LABELS_JA.get(d["dimension"], d["dimension"]) for d in covered)
        lines.append(f"Already covered（既存Geo Intelligenceで確認済み、再分析は不要）: {labels}")
    if missing:
        labels = "、".join(DIMENSION_LABELS_JA.get(d["dimension"], d["dimension"]) for d in missing)
        lines.append(f"Need analysis（既存Geo Intelligenceが不足している論点、重点的に分析してください）: {labels}")
    for d in conflicting:
        active_titles, contested_titles = _conflict_titles(d["dimension"], retrieval)
        label = DIMENSION_LABELS_JA.get(d["dimension"], d["dimension"])
        lines.append(f"Need resolution（既存Knowledge同士が矛盾、解消してください）: {label} — "
                     f"既存assessment: {'; '.join(active_titles) or '(不明)'} / "
                     f"対立する見解: {'; '.join(contested_titles) or '(不明)'}")
    lines.append("指示: 「Already covered」は既知の前提として活用してよく、再分析は不要です。"
                 "ただし、その前提に重大な誤り・陳腐化があると判断した場合は、Assessment内で"
                 "明確に指摘してください（既存前提の破棄・修正提案を歓迎します）。"
                 "「Need analysis」「Need resolution」に該当する論点を中心に分析してください。")
    return query_text + "\n\n" + "\n".join(lines)


def run_expert_for_missing_dimensions(client, azure_client, model: str, normalized: dict, retrieval: dict,
                                       dimension_assessment: list, resolution_mode: str) -> dict:
    """既存gis._run_expert_ai_and_shape（geo_expert_engine.run_expert_request_with_modeの
    Geo Intelligence Response整形ラッパー、_call_expert_aiと共通）をそのまま呼ぶ。新しい
    Expert呼び出し経路は作らない。query_textだけをbuild_missing_dimension_requestで合成する。"""
    mode = gis.EXPERT_MODE_BY_REQUEST_TYPE.get(normalized["request_type"], "case_analysis")
    theme_id = gis.resolve_single_theme_id(client, retrieval["issue_ids"])
    query_text = build_missing_dimension_request(normalized, retrieval, dimension_assessment, resolution_mode)
    return gis._run_expert_ai_and_shape(client, azure_client, model, mode, theme_id, query_text)


def _report_search_terms(normalized: dict) -> list:
    """search_reportsはgeo_reports.author/report_type/geo_documents.titleへのILIKE検索
    （geo_search_service._partial_match_candidates、`ilike.*pattern*`）のため、質問文
    全体（自然文の長い1文）をそのまま渡すとタイトルとの部分一致がほぼ成立せず、実運用で
    実測した通り「該当0件のまま実Supabase往復コストだけ発生する」（P0-1、本番実測で
    2.27秒/0件を確認）。country_region/themesの短い語（既存gis._fts_search_termsと同じ
    考え方）を個別に検索する方が、既存FTS検索と一貫した挙動になり、無駄な全文検索を避けられる。
    新しい検索基盤・query decomposition機構は作らない。"""
    return [t for t in [*normalized["country_region"], *normalized["themes"]] if t]


def retrieve_department_intelligence(client, normalized: dict, retrieval: dict) -> dict:
    """gis.retrieve_knowledgeの結果へ、Department Inquiry向けにActor Intelligence/Geo Reports/
    Media Intelligenceを追加する（既存関数の呼び出しのみ、新しい検索ロジックは実装しない）。
    resolve_entities_and_issuesが既に解決済みのstakeholder Entityをそのまま使う
    （country_region/themesの再解決はしない）。

    Phase F §8-10 Intelligence Pack Audit: build_entity_scoped_actor_intelligence_pack /
    build_media_intelligence_packは、元のgeo_knowledge_items（agent_sharing_policy列を持つ）を
    Case/Report非依存の要約形状（key_actors[]/media_patterns[]等）へ変換する過程でこの列を
    引き継がない。要約後の形状からagent_sharing_policyを安全に再判定する手段が無いため、
    このAdapter層で新たに引き当てるくらいなら、Cross-Agent（本関数は常にCross-Domain呼び出し）
    向けにはこの2 packを丸ごと除外する（§9-10: 推測でallowしない。Expert AI自身の内部利用
    ではこれらのBuilder関数は無変更で従来どおり使われる、本関数の外側の話）。"""
    actor_intelligence = {"key_actors": [], "known_ids": set(), "actor_intelligence_status": "partial"}
    media_intelligence = {"media_patterns": [], "media_profiles": [], "statuses_included": [], "known_ids": set()}

    # Phase 2E: geo_reportsへhuman_visibility/agent_sharing_policy/allowed_domains列が追加され
    # たため（sql/2026-09-28b_geo_report_knowledge_access_policy.sql）、旧P0-1で無効化していた
    # search_reports呼び出しを、governance付きで復活させる。本関数は常にCross-Domain呼び出し
    # （is_internal_caller=False固定）——caller_domainはprocess_request側で認証済みの
    # normalized["_authenticated_caller_domain"]のみを信頼する（requester_app自己申告は
    # 使わない、既存retrieve_knowledgeと同じ方針）。
    matched_reports = []
    seen_report_ids = set()
    for term in _report_search_terms(normalized):
        for report in geo_report_service.search_reports(client, term, include_demo=False):
            if report["report_id"] not in seen_report_ids:
                seen_report_ids.add(report["report_id"])
                matched_reports.append(report)
    geo_reports = geo_knowledge_access_policy.filter_agent_accessible_reports(
        matched_reports, is_internal_caller=False,
        caller_domain=normalized.get("_authenticated_caller_domain"))

    return {**retrieval, "actor_intelligence": actor_intelligence,
            "media_intelligence": media_intelligence, "geo_reports": geo_reports}


def _has_actionable_actor_intelligence(actor_intelligence: dict) -> bool:
    """Entityが解決できただけ（profile_source="none_available"）ではCoverage扱いにしない。
    実際にActor Intelligence Profile/Precedent/Postureのいずれかを持つKey Actorが
    1件以上ある場合のみ「今回の質問にActor Intelligenceが使えた」とみなす。"""
    return any((ka.get("profile_source") or "none_available") != "none_available"
               for ka in (actor_intelligence.get("key_actors") or []))


def _key_actor_intelligence_summary(actor_intelligence: dict) -> list:
    """A側（他部門）が参照しやすいよう、Actor Intelligence Packから存在するフィールドのみを
    抜き出した軽量サマリー。取得できていない項目はNoneのまま（捏造しない）。"""
    summary = []
    for ka in (actor_intelligence.get("key_actors") or []):
        if (ka.get("profile_source") or "none_available") == "none_available":
            continue
        current_posture = ka.get("current_posture") or {}
        summary.append({
            "entity_id": ka.get("entity_id"),
            "canonical_name": ka.get("canonical_name"),
            "actor_intelligence_status": ka.get("actor_intelligence_status"),
            "current_posture_priorities": current_posture.get("current_priorities"),
            "current_posture_as_of": current_posture.get("as_of"),
        })
    return summary


def _build_provenance(dimension_assessment: list, expert_response_id: str | None) -> list:
    """既存Knowledge/Claim/Actor Intelligence/Expert Responseのみを引用する（存在しない情報は
    載せない、item16）。Origin分類はspec item16の語彙（existing_intelligence/active_knowledge/
    actor_intelligence/live_evidence/fresh_expert_analysis）をそのまま使う。"""
    provenance = []
    for d in dimension_assessment:
        if d["status"] not in ("covered", "conflicting"):
            continue
        origin = ORIGIN_BY_DIMENSION.get(d["dimension"], "existing_intelligence")
        for source_id in d["source_ids"]:
            provenance.append({"source_id": source_id, "dimension": d["dimension"], "origin": origin})
    if expert_response_id:
        provenance.append({"source_id": expert_response_id, "dimension": None, "origin": "fresh_expert_analysis"})
    return provenance


def _cap_confidence(confidence: str, unresolved_dimensions: list, expert_call_error: dict | None) -> str:
    """unresolved_dimensions（既存Intelligenceでも新規Expert分析でも埋まらなかった論点）が
    1件以上、またはExpert AI呼び出し自体が失敗した場合、confidenceを"high"のままにしない
    （完全回答相当の確信度を主張しない）。既存gis.assess_confidenceの値を引き上げることは
    しない（Capのみ、Boostしない）。"""
    if not unresolved_dimensions and expert_call_error is None:
        return confidence
    if _CONFIDENCE_ORDER.get(confidence, 0) > _CONFIDENCE_ORDER[_CONFIDENCE_CAP_WHEN_UNRESOLVED]:
        return _CONFIDENCE_CAP_WHEN_UNRESOLVED
    return confidence


def build_department_intelligence_metadata(normalized: dict, retrieval: dict, dimension_assessment: list,
                                            sufficiency: str, confidence: str, analysis: dict, completed_at: str, *,
                                            resolution_mode: str, expert_requested_dimensions: list,
                                            expert_analyzed_dimensions: list,
                                            expert_call_error: dict | None,
                                            research_status: str = "none") -> dict:
    """GeoIntelligenceResponseへ追加するoptional department_intelligence blockを組み立てる。
    resolution_mode/expert_requested_dimensions/expert_analyzed_dimensions/confidence（既に
    _cap_confidence適用済みの値）はhandle_department_inquiry側で決定済みの値をそのまま受け取る
    （ここで再判定しない）。resolution_mode（How answered）とsufficiency（How sufficient）は
    別軸として両方保持する（B-B5、混同しない）。

    Phase B-C: research_status（none/requested/in_progress/completed/failed、
    geo_knowledge_gap_service.research_status_for_gapが導出）は「Expert Researchという
    非同期の営みがどこまで進んだか」を示す。resolution_mode="existing_only"（同期Expert AIは
    使っていない）であることと矛盾しない——research_status="completed"でも、そのGapがまだ
    resolvedとは限らない（Human ReviewでKnowledgeがactiveへ昇格するまでGapは未解決のまま、
    Research完了とGap解決は別概念）。"""
    covered_dimensions = [d["dimension"] for d in dimension_assessment if d["status"] == "covered"]
    missing_dimensions = [d["dimension"] for d in dimension_assessment if d["status"] == "missing"]
    conflicting_dimensions = [d["dimension"] for d in dimension_assessment if d["status"] == "conflicting"]
    used_expert_ai = bool(analysis.get("expert_ai_called"))
    used_existing_intelligence = bool(covered_dimensions or conflicting_dimensions)
    unresolved_dimensions = [d for d in (missing_dimensions + conflicting_dimensions)
                             if d not in expert_analyzed_dimensions]

    existing_source_ids = []
    for d in dimension_assessment:
        if d["status"] in ("covered", "conflicting"):
            existing_source_ids += d["source_ids"]
    seen = set()
    existing_source_ids = [s for s in existing_source_ids if s and not (s in seen or seen.add(s))]

    selected_source_ids = [r["reference_id"] for r in (analysis.get("references") or [])] + existing_source_ids
    seen = set()
    selected_source_ids = [s for s in selected_source_ids if s and not (s in seen or seen.add(s))]

    # freshness_requirement="high"の場合のみFreshness不足を厳格に評価する（item9: 通常時は
    # 古いKnowledgeを機械的に不足扱いしない）。
    freshness_sufficient = (
        not any(d["freshness"] == "stale" for d in dimension_assessment)
        if normalized.get("freshness_requirement") == "high" else True
    )

    actor_intelligence = retrieval.get("actor_intelligence") or {}
    expert_response_id = analysis.get("expert_response_id")
    return {
        "source_department": normalized.get("source_department"),
        "source_workflow": normalized.get("source_workflow"),
        "resolution_mode": resolution_mode,
        "knowledge_sufficiency": sufficiency,
        "used_existing_intelligence": used_existing_intelligence,
        "used_expert_ai": used_expert_ai,
        "selected_source_ids": selected_source_ids,
        "existing_source_ids": existing_source_ids,
        "expert_requested_dimensions": expert_requested_dimensions,
        "expert_analyzed_dimensions": expert_analyzed_dimensions,
        "expert_response_id": expert_response_id,
        "expert_call_error": expert_call_error,
        "covered_dimensions": covered_dimensions,
        "missing_dimensions": missing_dimensions,
        "conflicting_dimensions": conflicting_dimensions,
        "unresolved_dimensions": unresolved_dimensions,
        "research_status": research_status,
        "freshness_sufficient": freshness_sufficient,
        "review_status": "ai_only" if used_expert_ai else "existing_intelligence",
        "as_of": completed_at,
        "confidence": confidence,
        "key_actor_intelligence": _key_actor_intelligence_summary(actor_intelligence),
        "dimension_assessment": dimension_assessment,
        "provenance": _build_provenance(dimension_assessment, expert_response_id),
        "reasoning": (f"resolution_mode={resolution_mode}（How answered）, "
                     f"knowledge_sufficiency={sufficiency}（How sufficient）, "
                     f"covered={len(covered_dimensions)}件, missing={len(missing_dimensions)}件, "
                     f"conflicting={len(conflicting_dimensions)}件, "
                     f"unresolved={len(unresolved_dimensions)}件"),
    }


def handle_department_inquiry(client, normalized: dict, *, actor_id: str,
                               azure_client=None, model: str = None) -> dict:
    """Department Inquiry（request_type="user_question" かつ source_department指定）の
    エントリポイント。既存_process_user_questionと同じFailure Isolation方針（Retrieval等の
    内部例外は外部へ伝播させずstatus="failed"のResponseへ変換する）を踏襲する。ただしB-B4:
    Expert AI呼び出しの失敗だけは全体をfailedにせず、既存Retrieval/Dimension評価の結果を
    活かしたまま既存Intelligenceの範囲までの回答を返す（部分的にでも回答できるなら返す）。"""
    request_id = normalized["request_id"]
    completed_at = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    try:
        retrieval = gis.retrieve_knowledge(client, normalized)
        retrieval = retrieve_department_intelligence(client, normalized, retrieval)
        # knowledge_sufficiency（既存Contractの必須field）とIntelligence Need記録は、既存
        # user_question同様1軸のassess_sufficiencyのまま維持する（B-B4はExpert AI呼び出し
        # 可否の判定だけをdimension_assessment/resolution_modeへ置き換える）。
        sufficiency = gis.assess_sufficiency(retrieval, normalized)
        confidence = gis.assess_confidence(retrieval, sufficiency)
        dimension_assessment = _assess_dimensions(retrieval, normalized.get("freshness_requirement"))
        resolution_mode = determine_resolution_plan(dimension_assessment, normalized, azure_client)

        # expert_requested_dimensions（依頼した対象、失敗しても保持）とexpert_analyzed_dimensions
        # （実際にExpert Responseが成立した場合のみ非空）を分離する（B-B5、item2:
        # 「分析しようとした」と「分析できた」を混同しない）。
        expert_requested_dimensions = []
        expert_analyzed_dimensions = []
        expert_call_error = None
        analysis = None
        if resolution_mode == "expert_only":
            expert_requested_dimensions = list(ALL_DIMENSIONS)
        elif resolution_mode == "existing_plus_expert":
            expert_requested_dimensions = [d["dimension"] for d in dimension_assessment
                                           if d["status"] in ("missing", "conflicting")]

        research_needed = resolution_mode in ("existing_plus_expert", "expert_only")
        if research_needed and sync_expert_enabled():
            try:
                analysis = run_expert_for_missing_dimensions(
                    client, azure_client, model, normalized, retrieval, dimension_assessment, resolution_mode)
                analysis["expert_ai_called"] = True
                expert_analyzed_dimensions = expert_requested_dimensions  # 成功時のみrequestedを複製
            except Exception as expert_exc:  # noqa: BLE001 — Expert失敗でも既存情報は破棄しない
                expert_call_error = {"code": type(expert_exc).__name__, "message": str(expert_exc)}
                analysis = None

        # Phase B-C: Knowledge Gap記録はExpert AI呼び出し可否の判定より前に確定させる
        # （geo_assessmentへ「Research Requestとして記録した」と書く前に、実際に記録できたかを
        # 確認するため。sufficiencyが1軸判定なので、まれにresearch_needed=Trueでもsufficient
        # 判定でGapが作られないケースがあり、その場合は「記録した」と書かない）。
        missing_categories = gis._missing_categories(retrieval)
        knowledge_gap = None
        research_status = "none"
        if sufficiency != "sufficient":
            gap_result = gis.geo_knowledge_gap_service.record_external_request_gap(
                client, request_id=request_id, normalized=normalized, sufficiency=sufficiency,
                missing_categories=missing_categories, actor_id=actor_id)
            if gap_result:
                knowledge_gap = {**gap_result, "missing_categories": missing_categories,
                                 "note": "AIはKnowledge不足を推測で補完せず、地政学担当者向けの"
                                         "Intelligence Needとして記録した。"}
                # Phase B-C: このGapが後段でgeo_research_service.run_research_requestにより
                # 非同期Researchされる想定。department_intelligence.research_statusは
                # Gapの現在状態から導出する（新規カラム無し、geo_knowledge_gap_service参照）。
                research_status = geo_knowledge_gap_service.research_status_for_gap(
                    client, gap_result["gap_id"])
            else:
                knowledge_gap = {"exists": False, "gap_id": None, "description": None,
                                  "missing_categories": missing_categories,
                                  "note": "回答品質上重要な不足ではないと判定したため、"
                                          "Intelligence Needの登録は行っていない。"}

        if analysis is None:
            analysis = gis._digest_analysis(retrieval)
            analysis["expert_ai_called"] = False
            if expert_call_error is not None:
                # 同期Expert AI呼び出しを試みたが失敗した場合（sync_expert_enabled=True時のみ
                # 到達しうる経路）。既存Intelligenceの範囲までの回答である旨を明記する。
                unresolved = "、".join(DIMENSION_LABELS_JA.get(d, d) for d in expert_requested_dimensions) or "(なし)"
                note = ("\n\n[Geopolitics Department Intelligence AI] Expert AI呼び出しに失敗したため、"
                       f"既存Geo Intelligenceの範囲までの回答です。以下は未解決です: {unresolved}")
                analysis["geo_assessment"] = (analysis.get("geo_assessment") or "") + note
            elif research_needed and knowledge_gap and knowledge_gap.get("exists"):
                # Phase B-C既定経路: 同期Expert AIをそもそも呼んでいない（エラーではない）。
                # 既存Knowledgeで答えられる範囲を返し、不足分はKnowledge Gapとして実際に記録
                # できた場合のみ「記録した」旨を書く（記録できていないのに書かない）。Expert
                # Researchは別途非同期（geo_research_service.run_research_request）で実行する。
                unresolved = "、".join(DIMENSION_LABELS_JA.get(d, d) for d in expert_requested_dimensions) or "(なし)"
                note = ("\n\n[Geopolitics Department Intelligence AI] 既存Geo Intelligenceの範囲までの"
                       f"回答です。以下は追加のResearch Requestとして記録しました（非同期で調査予定）: "
                       f"{unresolved}")
                analysis["geo_assessment"] = (analysis.get("geo_assessment") or "") + note
            # Expert AIを実際に使っていない場合、resolution_modeもexisting_only（_digest_analysis
            # が返す既存Intelligenceの範囲のみの回答）へ統一する（sync失敗時・sync既定OFF時とも
            # 共通。expert_requested_dimensionsは「依頼した」証跡としてそのまま保持し、
            # expert_analyzed_dimensionsのみ空のまま＝成立していない）。
            resolution_mode = "existing_only"

        analysis["references"] = gis.build_references(client, retrieval)

        # Confidence Cap（B-B5、item3）: unresolved_dimensions（missing/conflictingのうち
        # Expert分析でも埋まらなかったもの）が残る、またはExpert呼び出し自体が失敗した場合、
        # 既存gis.assess_confidenceの値をそのまま使わず"high"を"medium"へCapする
        # （引き上げはしない）。unresolved_dimensionsの実体はbuild_department_intelligence_
        # metadata内でも計算するため、ここでは同じロジックを先取りして使う。
        unresolved_for_confidence = [d["dimension"] for d in dimension_assessment
                                     if d["status"] in ("missing", "conflicting")
                                     and d["dimension"] not in expert_analyzed_dimensions]
        confidence = _cap_confidence(confidence, unresolved_for_confidence, expert_call_error)
        analysis["confidence"] = confidence

        status = "completed" if normalized["analysis_requested"] else "partial"
        retrieval_run_id = retrieval["retrieval_run_id"]
        department_intelligence = build_department_intelligence_metadata(
            normalized, retrieval, dimension_assessment, sufficiency, confidence, analysis, completed_at,
            resolution_mode=resolution_mode, expert_requested_dimensions=expert_requested_dimensions,
            expert_analyzed_dimensions=expert_analyzed_dimensions, expert_call_error=expert_call_error,
            research_status=research_status)
        error = None
    except Exception as e:  # noqa: BLE001 — Failure Isolation: A側がfail-openできるよう不明瞭な500を返さない
        status = "failed"
        sufficiency = "insufficient"
        confidence = "low"
        retrieval_run_id = None
        knowledge_gap = {"exists": False, "gap_id": None, "description": None,
                          "missing_categories": list(gis._ALL_CATEGORIES),
                          "note": "内部処理エラーのためKnowledgeを取得できませんでした"
                                  "（Intelligence Needの登録は行っていない）。"}
        error = {"code": type(e).__name__, "message": str(e)}
        analysis = {
            "geo_assessment": f"Geopolitics Department Intelligence AI内部処理でエラーが発生しました: "
                               f"{type(e).__name__}: {e}",
            "key_stakeholders": [], "political_dynamics": None, "outlook": None,
            "cross_domain_implications": None, "references": [], "expert_response_id": None,
            "expert_ai_called": False,
        }
        department_intelligence = {
            "source_department": normalized.get("source_department"),
            "source_workflow": normalized.get("source_workflow"),
            "resolution_mode": "unavailable",
            "knowledge_sufficiency": sufficiency,
            "used_existing_intelligence": False,
            "used_expert_ai": False,
            "selected_source_ids": [],
            "existing_source_ids": [],
            "expert_requested_dimensions": [],
            "expert_analyzed_dimensions": [],
            "expert_response_id": None,
            "expert_call_error": None,
            "covered_dimensions": [],
            "missing_dimensions": list(ALL_DIMENSIONS),
            "conflicting_dimensions": [],
            "unresolved_dimensions": list(ALL_DIMENSIONS),
            "research_status": "none",
            "freshness_sufficient": False,
            "review_status": "unavailable",
            "as_of": completed_at,
            "confidence": confidence,
            "key_actor_intelligence": [],
            "dimension_assessment": [],
            "provenance": [],
            "reasoning": "内部処理エラーのためDimension評価を実行できませんでした。",
        }

    response = {
        "request_id": request_id,
        "status": status,
        "geo_assessment": analysis.get("geo_assessment"),
        "key_stakeholders": analysis.get("key_stakeholders") or [],
        "political_dynamics": analysis.get("political_dynamics"),
        "outlook": analysis.get("outlook"),
        "cross_domain_implications": analysis.get("cross_domain_implications"),
        "references": analysis.get("references") or [],
        "confidence": confidence,
        "knowledge_sufficiency": sufficiency,
        "knowledge_gap": knowledge_gap,
        "expert_ai_called": bool(analysis.get("expert_ai_called")),
        "error": error,
        "completed_at": completed_at,
        "department_intelligence": department_intelligence,
    }
    return gis._finalize_response(client, request_id=request_id, response=response,
                                  retrieval_run_id=retrieval_run_id,
                                  expert_response_id=analysis.get("expert_response_id"),
                                  actor_id=actor_id, started=started)
