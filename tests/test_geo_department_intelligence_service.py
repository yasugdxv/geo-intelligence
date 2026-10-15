"""Phase B-B1〜B-B5: Cross-Department Inquiry Receiver（受信オーケストレーター〜Response/
Provenance統合）のテスト。

geo_intelligence_service.process_requestが、source_department指定のuser_questionだけを
geo_department_intelligence_service.handle_department_inquiryへ分岐すること、既存の
user_question/weekly_monitoring契約を壊さないこと、Dimension別Coverage判定（B-B3）と
それに基づく選択的Expert AI呼び出し（B-B4: missing/conflicting分だけExpert AIへ）、
resolution_modeとknowledge_sufficiencyの分離・expert_requested/analyzed_dimensionsの区別・
Confidence Cap・Provenance（B-B5）を確認する。"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_intelligence_service as service  # noqa: E402
import geo_department_intelligence_service as dept_service  # noqa: E402
from geo_intelligence_auth import CallerIdentity  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402
from tests.test_geo_intelligence_service_phase2 import WELL_FORMED_EXPERT_RESPONSE, _mock_llm_client  # noqa: E402


def _department_request(**overrides) -> dict:
    request = {
        "requester_app": "sustainability_intelligence",
        "request_type": "user_question",
        "question": "メキシコのWater issueの政治化・規制化可能性を見たい",
        "country_region": ["Mexico"],
        "themes": ["water"],
        "source_department": "sustainability",
        "source_workflow": "chat",
        "analysis_requested": True,
    }
    request.update(overrides)
    return request


def _seeded_client() -> FakeSupabaseClient:
    return FakeSupabaseClient(tables={
        "geo_knowledge_items": [{
            "knowledge_item_id": "ki-1", "knowledge_type": "country_foundation",
            "title": "メキシコ(Mexico): 水資源政策の基礎情報", "summary": "水資源(water)規制の概要",
            "status": "active", "retrieval_status": "available", "is_demo": False,
            "confidence": "中", "updated_at": "2026-08-01T00:00:00+00:00",
            "agent_sharing_policy": "allowed_cross_domain",  # Phase E: 既存Cross-Domainテストの意図（既存Knowledgeで応答可能）を保つため明示
        }],
        "geo_claims": [{
            "claim_id": "cl-1", "claim_text": "メキシコ(Mexico)では水資源(water)の地方分権が進んでいる",
            "information_class": "observed_fact", "retrieval_status": "available",
            "is_demo": False,
        }],
    })


# ---- 分岐: source_department指定時のみdepartment_intelligence経路へ ----

def test_source_department_routes_to_department_service():
    client = _seeded_client()
    response = service.process_request(client, _department_request())

    assert response["department_intelligence"] is not None
    assert response["department_intelligence"]["source_department"] == "sustainability"
    assert response["department_intelligence"]["source_workflow"] == "chat"


def test_without_source_department_existing_behavior_unaffected():
    """既存user_question呼び出し元（source_department未指定）はdepartment_intelligenceが
    Noneのまま、既存フィールドのみのResponseを受け取る（後方互換の中核）。"""
    client = _seeded_client()
    response = service.process_request(client, _department_request(source_department=None))

    # _process_user_question自体は無変更のため、キー自体が存在しない（FastAPI層の
    # response_model=GeoIntelligenceResponseがデフォルト値Noneを補う）。
    assert response.get("department_intelligence") is None
    assert response["request_id"]
    assert response["knowledge_sufficiency"] in ("sufficient", "partial", "insufficient")


# ---- department_intelligence metadataの内容 ----

def test_existing_knowledge_only_no_expert_ai_without_azure_client():
    client = _seeded_client()
    response = service.process_request(client, _department_request())  # azure_client未指定

    di = response["department_intelligence"]
    assert di["used_expert_ai"] is False
    assert di["resolution_mode"] == "existing_only"
    assert di["used_existing_intelligence"] is True
    assert di["covered_dimensions"]
    assert response["expert_ai_called"] is False


def test_no_existing_intelligence_reports_all_dimensions_missing():
    client = FakeSupabaseClient()  # Knowledge一切なし
    response = service.process_request(client, _department_request())

    di = response["department_intelligence"]
    assert di["used_existing_intelligence"] is False
    assert di["resolution_mode"] == "existing_only"
    assert di["covered_dimensions"] == []
    assert set(di["missing_dimensions"]) == (
        set(dept_service.gis._ALL_CATEGORIES) | set(dept_service.DEPARTMENT_ONLY_DIMENSIONS))
    assert di["key_actor_intelligence"] == []


# ---- Phase B-B2: Actor Intelligence / Geo Reports拡張 ----

def test_actor_intelligence_excluded_from_cross_domain_pack_even_when_profile_exists():
    """Phase F §8-10 Intelligence Pack Audit: build_entity_scoped_actor_intelligence_packは
    要約形状（key_actors[]）へ変換する過程でagent_sharing_policyを引き継がないため、安全に
    判定できない——Cross-Domain（department_intelligence、本テストの経路）では、Active
    Actor Profileが実在してもactor_intelligenceパック自体を丸ごと除外する（推測でallowしない、
    §9）。旧: test_actor_intelligence_used_when_relevant_profile_exists（Phase E以前はpackが
    そのままCross-Domainへ出ていた挙動）。"""
    client = FakeSupabaseClient(tables={
        "geo_entities": [{
            "entity_id": "ent-mexico", "entity_type": "country",
            "canonical_name": "Mexico", "external_ids": {}, "is_demo": False,
        }],
        "geo_knowledge_items": [{
            "knowledge_item_id": "ai-1", "knowledge_type": "actor_profile", "status": "active",
            "retrieval_status": "available", "is_demo": False, "title": "Mexico Government",
            "updated_at": "2026-08-01T00:00:00+00:00", "agent_sharing_policy": "allowed_cross_domain",
            "structured_content": {
                "entity_id": "ent-mexico", "core_profile": None, "decision_patterns": [],
                "current_posture": {"as_of": "2026-08-01", "current_priorities": ["水資源規制強化"],
                                    "evidence_ids": []},
                "issue_positions": [], "persistent_preference": None,
                "strategic_communication_pattern": None,
            },
        }],
    })
    response = service.process_request(client, _department_request())

    di = response["department_intelligence"]
    assert "actor_intelligence" not in di["covered_dimensions"]
    assert di["key_actor_intelligence"] == []


def test_actor_intelligence_not_claimed_when_entity_has_no_profile():
    """Entityは解決できてもActor Profileが存在しない場合、捏造せずmissing扱いのままにする。"""
    client = FakeSupabaseClient(tables={
        "geo_entities": [{
            "entity_id": "ent-mexico", "entity_type": "country",
            "canonical_name": "Mexico", "external_ids": {}, "is_demo": False,
        }],
    })
    response = service.process_request(client, _department_request())

    di = response["department_intelligence"]
    assert "actor_intelligence" not in di["covered_dimensions"]
    assert "actor_intelligence" in di["missing_dimensions"]
    assert di["key_actor_intelligence"] == []


def test_geo_reports_denied_by_default_to_cross_domain_agents():
    """Phase 2E: geo_reportsの既定agent_sharing_policy（未設定時のフォールバック値
    "geopolitics_only"）は、他Domain Agentからのdepartment_intelligence問い合わせでは
    常にdenyになる（unknown/未共有設定→deny、§11/§13/§26）。Phase 2E以前は
    governance列自体が無いためsearch_reports呼び出し自体を無効化していたが（旧:
    test_geo_reports_never_surfaced_to_cross_domain_agents）、Phase 2Eでgeo_reportsへ
    human_visibility/agent_sharing_policy/allowed_domainsが追加されたことに伴い、
    実際にsearch_reportsを呼んだ上でfilter_agent_accessible_reportsにより除外される
    形へ変わった——観測される結果（geo_reportsがCross-Domainへ出ない）自体は同じ。"""
    client = FakeSupabaseClient(tables={
        "geo_reports": [{
            "report_id": "rep-1", "document_id": "doc-1", "author": "internal",
            "report_type": "assessment", "is_demo": False, "uploaded_at": "2026-08-01T00:00:00+00:00",
        }],
        "geo_documents": [{
            "document_id": "doc-1", "title": "Mexico water resource regulation report",
            "theme_id": None, "published_at": "2026-08-01T00:00:00+00:00", "is_demo": False,
        }],
    })
    response = service.process_request(client, _department_request())

    di = response["department_intelligence"]
    assert "geo_reports" not in di["covered_dimensions"]
    assert "rep-1" not in di["selected_source_ids"]


def test_geo_reports_surfaced_when_explicitly_shared_with_matching_domain():
    """Phase 2E: agent_sharing_policy="custom"+allowed_domainsに認証済みcaller_domainが
    含まれる場合のみ、Cross-Domain Agentへgeo_reportsが渡る（推測でallowしない、明示設定が
    必須）。caller_domainは認証済みCallerIdentity由来のみを信頼する（§7 requester_app
    自己申告は使わない、既存retrieve_knowledgeと同じ方針）。"""
    client = FakeSupabaseClient(tables={
        "geo_reports": [{
            "report_id": "rep-1", "document_id": "doc-1", "author": "internal",
            "report_type": "assessment", "is_demo": False, "uploaded_at": "2026-08-01T00:00:00+00:00",
            "agent_sharing_policy": "custom", "allowed_domains": ["sustainability"],
        }],
        "geo_documents": [{
            "document_id": "doc-1", "title": "Mexico water resource regulation report",
            "theme_id": None, "published_at": "2026-08-01T00:00:00+00:00", "is_demo": False,
        }],
    })
    caller_identity = CallerIdentity(
        key="test-key", caller_app="sustainability_intelligence", caller_domain="sustainability",
        authenticated=True)
    response = service.process_request(client, _department_request(), caller_identity=caller_identity)

    di = response["department_intelligence"]
    assert "geo_reports" in di["covered_dimensions"]
    assert "rep-1" in di["selected_source_ids"]


def test_geo_reports_denied_when_caller_domain_not_in_allowed_domains():
    """同じcustom policyでも、認証済みcaller_domainがallowed_domainsに含まれなければdeny
    （fail-closed、他domainへの誤爆共有を防ぐ）。"""
    client = FakeSupabaseClient(tables={
        "geo_reports": [{
            "report_id": "rep-1", "document_id": "doc-1", "author": "internal",
            "report_type": "assessment", "is_demo": False, "uploaded_at": "2026-08-01T00:00:00+00:00",
            "agent_sharing_policy": "custom", "allowed_domains": ["sustainability"],
        }],
        "geo_documents": [{
            "document_id": "doc-1", "title": "Mexico water resource regulation report",
            "theme_id": None, "published_at": "2026-08-01T00:00:00+00:00", "is_demo": False,
        }],
    })
    caller_identity = CallerIdentity(
        key="test-key", caller_app="erm_intelligence", caller_domain="erm", authenticated=True)
    response = service.process_request(client, _department_request(), caller_identity=caller_identity)

    di = response["department_intelligence"]
    assert "geo_reports" not in di["covered_dimensions"]
    assert "rep-1" not in di["selected_source_ids"]


def test_geo_reports_search_skipped_when_no_country_region_or_themes():
    """P0-1: country_region/themesが両方とも無い場合、search_reports自体を呼ばない
    （質問文全体でのILIKE検索は本番実測で「該当0件のまま2秒超」だったため廃止）。"""
    client = FakeSupabaseClient()

    def _raise_if_called(*args, **kwargs):
        raise AssertionError("country_region/themesが無いのにsearch_reportsが呼ばれた")

    original = dept_service.geo_report_service.search_reports
    dept_service.geo_report_service.search_reports = _raise_if_called
    try:
        response = service.process_request(
            client, _department_request(country_region=[], themes=[]))
    finally:
        dept_service.geo_report_service.search_reports = original

    di = response["department_intelligence"]
    assert "geo_reports" in di["missing_dimensions"]


def test_selected_source_ids_match_returned_references():
    """selected_source_idsは実在するReferenceのIDのみで構成される（捏造しない）。"""
    client = _seeded_client()
    response = service.process_request(client, _department_request())

    di = response["department_intelligence"]
    reference_ids = {r["reference_id"] for r in response["references"]}
    assert set(di["selected_source_ids"]) == reference_ids
    assert reference_ids  # このfixtureでは実際にKnowledgeがヒットする


# ---- Phase B-B3: Dimension別Sufficiency判定（Coverage/Freshness/Conflict） ----

def test_high_freshness_requirement_demotes_stale_claim_to_missing():
    """freshness_requirement="high"時のみ、古いClaim（observed_fact）はcoveredからmissingへ
    降格する（item9: Current/Latestが重要な問いでのみFreshnessを厳格に見る）。"""
    client = FakeSupabaseClient(tables={
        "geo_claims": [{
            "claim_id": "cl-1", "claim_text": "メキシコ(Mexico)では水資源(water)の地方分権が進んでいる",
            "information_class": "observed_fact", "retrieval_status": "available",
            "is_demo": False, "valid_from": "2020-01-01T00:00:00+00:00",
        }],
    })
    response = service.process_request(client, _department_request(freshness_requirement="high"))

    di = response["department_intelligence"]
    assert "observed_fact" not in di["covered_dimensions"]
    assert "observed_fact" in di["missing_dimensions"]
    assert di["freshness_sufficient"] is False
    dim = next(d for d in di["dimension_assessment"] if d["dimension"] == "observed_fact")
    assert dim["freshness"] == "stale"
    assert dim["source_ids"] == []  # 降格時はselected_source_idsへ混入させない


def test_normal_freshness_requirement_does_not_discard_old_claim():
    """item9: freshness_requirement未指定（通常時）は古いClaimでも機械的にmissing扱いしない。"""
    client = FakeSupabaseClient(tables={
        "geo_claims": [{
            "claim_id": "cl-1", "claim_text": "メキシコ(Mexico)では水資源(water)の地方分権が進んでいる",
            "information_class": "observed_fact", "retrieval_status": "available",
            "is_demo": False, "valid_from": "2020-01-01T00:00:00+00:00",
        }],
    })
    response = service.process_request(client, _department_request())

    di = response["department_intelligence"]
    assert "observed_fact" in di["covered_dimensions"]
    assert di["freshness_sufficient"] is True
    assert "cl-1" in di["selected_source_ids"]


def test_agnostic_dimension_freshness_never_blocks_coverage():
    """Foundation/Precedent等は構造的知識としてfreshness_requirement="high"でも
    coveredのまま（Actor core belief/Historical precedent相当、一律stale扱いにしない）。"""
    client = _seeded_client()
    response = service.process_request(client, _department_request(freshness_requirement="high"))

    di = response["department_intelligence"]
    assert "foundation" in di["covered_dimensions"]
    dim = next(d for d in di["dimension_assessment"] if d["dimension"] == "foundation")
    assert dim["freshness"] == "not_applicable"


def test_contested_knowledge_marks_dimension_conflicting_not_covered():
    """既存Knowledge同士が矛盾する場合、無理に1つへ平均化せずconflicting_dimensionsへ分離する
    （item19）。"""
    client = FakeSupabaseClient(tables={
        "geo_entities": [{
            "entity_id": "ent-mexico", "entity_type": "country",
            "canonical_name": "Mexico", "external_ids": {}, "is_demo": False,
        }],
        "geo_entity_issues": [{"entity_id": "ent-mexico", "issue_id": "issue-water"}],
        "geo_knowledge_item_issues": [
            {"knowledge_item_id": "ki-active", "issue_id": "issue-water"},
            {"knowledge_item_id": "ki-contested", "issue_id": "issue-water"},
        ],
        "geo_knowledge_items": [
            {"knowledge_item_id": "ki-active", "knowledge_type": "country_foundation",
             "status": "active", "retrieval_status": "available", "is_demo": False,
             "title": "Mexico政治体制", "updated_at": "2026-08-01T00:00:00+00:00",
             "agent_sharing_policy": "allowed_cross_domain"},
            {"knowledge_item_id": "ki-contested", "knowledge_type": "country_foundation",
             "status": "contested", "retrieval_status": "available", "is_demo": False,
             "title": "Mexico政治体制（対立見解）", "updated_at": "2026-08-01T00:00:00+00:00",
             "agent_sharing_policy": "allowed_cross_domain"},
        ],
    })
    response = service.process_request(client, _department_request())

    di = response["department_intelligence"]
    assert "foundation" in di["conflicting_dimensions"]
    assert "foundation" not in di["covered_dimensions"]
    assert "foundation" not in di["missing_dimensions"]
    assert di["used_existing_intelligence"] is True  # Conflictも「既存Intelligenceが見つかった」扱い


# ---- Contract Regression: 既存weekly_monitoring/request_payload保存は影響を受けない ----

def test_weekly_monitoring_unaffected_by_department_dispatch():
    client = FakeSupabaseClient()
    response = service.process_request(client, {
        "requester_app": "sustainability_intelligence",
        "request_type": "weekly_monitoring",
        "period_start": "2026-08-01",
        "period_end": "2026-08-31",
        "sustainability_themes": ["water"],
        "priority_geographies": ["Mexico"],
    })

    assert "department_intelligence" not in response or response.get("department_intelligence") is None
    assert response["status"] in ("completed", "partial", "failed")


def test_department_request_persists_request_payload_with_source_department():
    client = _seeded_client()
    response = service.process_request(client, _department_request())

    saved = client.tables["geo_intelligence_requests"]
    assert saved[0]["request_payload"]["source_department"] == "sustainability"
    assert saved[0]["request_id"] == response["request_id"]


# ---- Phase B-B4: Selective Expert Invocation ----

def _dim(dimension: str, status: str, freshness: str = "not_applicable", source_ids: list = None) -> dict:
    return {"dimension": dimension, "status": status, "freshness": freshness, "source_ids": source_ids or []}


def test_determine_resolution_plan_four_branches():
    covered = _dim("foundation", "covered")
    missing = _dim("geo_reports", "missing")
    conflicting = _dim("precedent", "conflicting")
    normalized = {"analysis_requested": True}
    azure_client = object()

    assert dept_service.determine_resolution_plan([covered], normalized, azure_client) == "existing_only"
    assert dept_service.determine_resolution_plan(
        [covered, missing], normalized, azure_client) == "existing_plus_expert"
    assert dept_service.determine_resolution_plan(
        [covered, conflicting], normalized, azure_client) == "existing_plus_expert"
    assert dept_service.determine_resolution_plan([missing, conflicting], normalized, azure_client) == "expert_only"
    # ガード条件: analysis_requested=FalseまたはazureClient未指定なら常にexisting_only
    assert dept_service.determine_resolution_plan(
        [missing], {"analysis_requested": False}, azure_client) == "existing_only"
    assert dept_service.determine_resolution_plan([missing], normalized, None) == "existing_only"


def test_build_missing_dimension_request_separates_covered_missing_conflicting():
    normalized = {"question": "Q", "context": None}
    retrieval = {
        "foundation": [{"title": "既存assessment: Mexico政治体制"}],
        "contested": [{"knowledge_type": "country_foundation", "title": "対立見解: Mexico政治体制（対立）"}],
    }
    dimension_assessment = [
        _dim("foundation", "conflicting"),
        _dim("geo_reports", "missing"),
        _dim("stakeholder", "covered"),
    ]
    text = dept_service.build_missing_dimension_request(
        normalized, retrieval, dimension_assessment, "existing_plus_expert")

    assert "Already covered" in text
    assert "Need analysis" in text
    assert "Need resolution" in text
    assert "既存assessment: Mexico政治体制" in text
    assert "対立見解: Mexico政治体制（対立）" in text
    assert "重大な誤り" in text  # Covered判定へのChallengeを許容する指示文


def test_build_missing_dimension_request_expert_only_has_no_coverage_sections():
    normalized = {"question": "Q", "context": None}
    text = dept_service.build_missing_dimension_request(normalized, {}, [], "expert_only")
    assert "Already covered" not in text
    assert "質問全体について新規に分析してください" in text


def _partial_coverage_client() -> FakeSupabaseClient:
    """foundation/stakeholderのみcovered、他は全てmissing（geo_reports/actor_intelligence含む）
    になるfixture。existing_plus_expertのE2Eテスト用。"""
    return FakeSupabaseClient(tables={
        "geo_entities": [{"entity_id": "ent-mexico", "entity_type": "country",
                          "canonical_name": "Mexico", "external_ids": {}, "is_demo": False}],
        "geo_entity_issues": [{"entity_id": "ent-mexico", "issue_id": "issue-water"}],
        "geo_knowledge_item_issues": [{"knowledge_item_id": "ki-foundation", "issue_id": "issue-water"}],
        "geo_knowledge_items": [{
            "knowledge_item_id": "ki-foundation", "knowledge_type": "country_foundation",
            "status": "active", "retrieval_status": "available", "is_demo": False,
            "title": "Mexico政治体制", "updated_at": "2026-08-01T00:00:00+00:00",
            "agent_sharing_policy": "allowed_cross_domain",
        }],
    })


def test_existing_plus_expert_calls_expert_with_coverage_context(monkeypatch):
    """Phase B-C以降、通常経路は同期Expert AIを既定で呼ばないため、Regression用にこの
    テストだけ明示的に有効化する（sync_expert_enabled、既存existing_plus_expert直接呼び出し
    経路自体は削除しない、item17）。"""
    monkeypatch.setenv("GEO_EXPERT_EXECUTION_MODE", "monolithic")
    monkeypatch.setenv("GEO_DEPARTMENT_SYNC_EXPERT_ENABLED", "true")
    client = _partial_coverage_client()
    azure_client = _mock_llm_client()

    response = service.process_request(client, _department_request(),
                                       azure_client=azure_client, model="gpt-4o")

    di = response["department_intelligence"]
    assert di["resolution_mode"] == "existing_plus_expert"
    assert response["expert_ai_called"] is True
    assert di["expert_response_id"]
    assert "foundation" not in di["expert_analyzed_dimensions"]  # coveredなので再分析対象外
    assert "geo_reports" in di["expert_analyzed_dimensions"]  # missingなので分析対象

    messages = json.dumps(azure_client.chat.completions.create.call_args[1]["messages"], ensure_ascii=False)
    assert "Already covered" in messages
    assert "Need analysis" in messages


def test_expert_call_failure_falls_back_to_existing_without_failing_whole_response(monkeypatch):
    """Expert AI呼び出し失敗時、既存Retrieval/Dimension評価の結果を破棄せず、既存Intelligence
    の範囲までの回答をstatus="failed"にせず返す（B-B4: fail-open）。同期Expert AIを明示的に
    有効化した場合のRegressionとして残す（Phase B-C既定は非同期化、別テストで検証）。"""
    monkeypatch.setenv("GEO_DEPARTMENT_SYNC_EXPERT_ENABLED", "true")
    client = _partial_coverage_client()
    azure_client = MagicMock()
    azure_client.chat.completions.create.side_effect = RuntimeError("azure timeout")

    response = service.process_request(client, _department_request(),
                                       azure_client=azure_client, model="gpt-4o")

    assert response["status"] != "failed"
    assert response["expert_ai_called"] is False
    di = response["department_intelligence"]
    assert di["resolution_mode"] == "existing_only"
    assert di["expert_call_error"] is not None
    # 2026-09-18修正: geo_llm_common._call_llm_structured_onceの手動JSONフォールバックが
    # 例外を正規化するようになったため、Azure呼び出し自体の失敗はGeoLLMErrorとして
    # 上位へ渡る（生のRuntimeError等がそのまま漏れなくなった）。元の例外種別・メッセージは
    # GeoLLMErrorのメッセージ文字列の中に保持される。
    assert di["expert_call_error"]["code"] == "GeoLLMError"
    assert "RuntimeError" in di["expert_call_error"]["message"]
    assert "azure timeout" in di["expert_call_error"]["message"]
    assert "未解決" in response["geo_assessment"]
    # 既存Foundation Referenceは破棄されずそのまま残る
    assert any(r["reference_id"] == "ki-foundation" for r in response["references"])
    # B-B5: 「分析しようとした(requested)」と「分析できた(analyzed)」を混同しない
    assert di["expert_requested_dimensions"]  # 依頼自体は行われたので非空のまま保持
    assert di["expert_analyzed_dimensions"] == []  # 成立していないので空
    assert di["expert_response_id"] is None


# ---- Phase B-B5: Response / Provenance統合 ----

def _high_confidence_partial_coverage_client() -> FakeSupabaseClient:
    """foundation/precedent/stakeholder/relationshipが揃いgis.assess_confidenceが"high"を
    返す想定だが、geo_reports/actor_intelligence/observed_fact等は未取得のままにし、
    missing_dimensionsが残るfixture（Confidence Cap検証用）。"""
    return FakeSupabaseClient(tables={
        "geo_entities": [
            {"entity_id": "ent-mexico", "entity_type": "country", "canonical_name": "Mexico", "is_demo": False},
            {"entity_id": "ent-water-authority", "entity_type": "organization",
             "canonical_name": "water", "is_demo": False},
        ],
        "geo_entity_issues": [
            {"entity_id": "ent-mexico", "issue_id": "issue-water"},
            {"entity_id": "ent-water-authority", "issue_id": "issue-water"},
        ],
        "geo_relationships": [{
            "relationship_id": "rel-1", "from_entity_id": "ent-mexico", "to_entity_id": "ent-water-authority",
            "relationship_type": "regulatory_oversight", "relationship_domain": "regulation",
            "description": "規制監督関係", "is_demo": False,
        }],
        "geo_knowledge_item_issues": [
            {"knowledge_item_id": "ki-foundation", "issue_id": "issue-water"},
            {"knowledge_item_id": "ki-precedent", "issue_id": "issue-water"},
        ],
        "geo_knowledge_items": [
            {"knowledge_item_id": "ki-foundation", "knowledge_type": "country_foundation",
             "status": "active", "retrieval_status": "available", "is_demo": False,
             "title": "Mexico政治体制", "updated_at": "2026-08-01T00:00:00+00:00",
             "agent_sharing_policy": "allowed_cross_domain"},
            {"knowledge_item_id": "ki-precedent", "knowledge_type": "precedent",
             "status": "active", "retrieval_status": "available", "is_demo": False,
             "title": "過去の水資源規制事例", "updated_at": "2026-08-01T00:00:00+00:00",
             "agent_sharing_policy": "allowed_cross_domain"},
        ],
    })


def test_confidence_capped_when_unresolved_dimensions_remain_without_expert_call():
    """item3: resolution_mode="existing_only"（Expert未呼び出し）でもmissing_dimensionsが
    残っていれば、既存gis.assess_confidenceが返す"high"をそのまま採用しない。"""
    client = _high_confidence_partial_coverage_client()
    response = service.process_request(client, _department_request())  # azure_client未指定

    di = response["department_intelligence"]
    assert di["resolution_mode"] == "existing_only"
    assert di["unresolved_dimensions"]
    assert response["confidence"] == "medium"
    assert di["confidence"] == "medium"  # top-levelとdepartment_intelligenceで矛盾しない


def test_confidence_not_capped_when_expert_resolves_all_missing_dimensions(monkeypatch):
    """既存Coverage+Expert AIで全てのmissing_dimensionsが解消された場合はCapしない
    （Boostはしないが、解消済みなら引き下げもしない）。同期Expert AIを明示的に有効化した
    場合のRegression（Phase B-C既定は非同期化）。"""
    monkeypatch.setenv("GEO_EXPERT_EXECUTION_MODE", "monolithic")
    monkeypatch.setenv("GEO_DEPARTMENT_SYNC_EXPERT_ENABLED", "true")
    client = _high_confidence_partial_coverage_client()
    azure_client = _mock_llm_client()

    response = service.process_request(client, _department_request(),
                                       azure_client=azure_client, model="gpt-4o")

    di = response["department_intelligence"]
    assert di["resolution_mode"] == "existing_plus_expert"
    assert di["unresolved_dimensions"] == []
    assert response["confidence"] == "high"
    assert di["confidence"] == "high"


def test_resolution_mode_and_knowledge_sufficiency_are_independent_fields():
    """item1: resolution_mode（How answered）とknowledge_sufficiency（How sufficient）を
    混同しない。department_intelligence.knowledge_sufficiencyはtop-levelと同じ値を持つ。"""
    client = _high_confidence_partial_coverage_client()
    response = service.process_request(client, _department_request())

    di = response["department_intelligence"]
    assert di["resolution_mode"] == "existing_only"
    assert di["knowledge_sufficiency"] == response["knowledge_sufficiency"]


def test_provenance_lists_existing_sources_and_expert_response(monkeypatch):
    """同期Expert AIを明示的に有効化した場合のRegression（Phase B-C既定は非同期化）。"""
    monkeypatch.setenv("GEO_EXPERT_EXECUTION_MODE", "monolithic")
    monkeypatch.setenv("GEO_DEPARTMENT_SYNC_EXPERT_ENABLED", "true")
    client = _partial_coverage_client()
    azure_client = _mock_llm_client()

    response = service.process_request(client, _department_request(),
                                       azure_client=azure_client, model="gpt-4o")

    di = response["department_intelligence"]
    provenance = di["provenance"]
    foundation_entry = next(p for p in provenance if p["source_id"] == "ki-foundation")
    assert foundation_entry["dimension"] == "foundation"
    assert foundation_entry["origin"] == "active_knowledge"
    expert_entry = next(p for p in provenance if p["origin"] == "fresh_expert_analysis")
    assert expert_entry["source_id"] == di["expert_response_id"]
