import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_synthesis_service as synth  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

NARRATIVE_ROW = {
    "report_topic_label": "各国政府の受け止め", "narrative_name": "仲介・緊張緩和優先",
    "narrative_description": "対話・仲介を重視する姿勢",
    "comparison_config_json": json.dumps([{"id": "cmp1", "label": "対米姿勢比較"}], ensure_ascii=False),
    "visualization_config_json": json.dumps([{"id": "viz1", "type": "bar"}], ensure_ascii=False),
}

CLUSTERS = [{
    "cluster_id": "CL-1", "record_count": 2,
    "classification": {"result": {
        "dimensions": {
            "country_focus": {"value": "US", "apply_to_cluster": "true", "confidence": "High",
                               "rationale": "米国主体の報道"},
        },
        "record_level_exceptions": [],
    }},
}]
REPRESENTATIVES_BY_CLUSTER = {
    "CL-1": [
        {"record_id": "r1", "title": "Mediation push", "source_name": "Example News",
         "published_at": "2026-08-10", "text_for_review": "US officials push for mediation with Iran."},
        {"record_id": "r2", "title": "Follow up", "source_name": "Example Wire",
         "published_at": "2026-08-11", "text_for_review": "Follow-up coverage of the mediation effort."},
    ],
}


def _base_client(**extra_tables) -> FakeSupabaseClient:
    tables = {
        "geo_entities": [
            {"entity_id": "trump-id", "entity_type": "person", "canonical_name": "Donald Trump",
             "external_ids": {}, "is_demo": False},
        ],
        "geo_relationships": [],
        "geo_knowledge_items": [],
    }
    tables.update(extra_tables)
    return FakeSupabaseClient(tables)


def _mock_llm_client(response: dict) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(response)))],
    )
    return client


def _valid_item(basis="observed", record_ids=None, cluster_ids=None, expert_refs=None, counterevidence_ids=None):
    return {
        "provenance": {
            "evidence_basis": basis,
            "observed_record_ids": record_ids or [],
            "observed_cluster_ids": cluster_ids or [],
            "expert_context_refs": expert_refs or [],
            "counterevidence_record_ids": counterevidence_ids or [],
        },
        "confidence": "Medium",
    }


def _valid_draft():
    return {
        "central_assessment": {**_valid_item(record_ids=["r1"]), "summary": "mediation-focused framing dominates"},
        "findings": [{**_valid_item(record_ids=["r1"], cluster_ids=["CL-1"]),
                      "finding_id": "f1", "statement": "US pushes mediation", "why_it_matters": "signals de-escalation"}],
        "drivers": [{**_valid_item(record_ids=["r1"]), "driver": "diplomatic bandwidth", "mechanism": "..."}],
        "counterforces": [{**_valid_item(record_ids=["r2"]), "counterforce": "domestic hawks", "mechanism": "..."}],
        "implications": [{**_valid_item(record_ids=["r1"]), "implication": "watch for backchannel signals", "for_whom": "policy analysts"}],
        "watchpoints": [{**_valid_item(record_ids=["r1"]), "watchpoint": "official statement shift", "trigger_condition": "..."}],
        "continuity_assessment": {"state": "New", "rationale": "初回観測"},
        "assessment_delta": [],
        "watchpoint_updates": [],
    }


# ---------------------------------------------------------------------------
# Observed Evidence Block
# ---------------------------------------------------------------------------

def test_build_observed_evidence_block_extracts_known_ids():
    text, known_records, known_clusters, known_prior_records = synth.build_observed_evidence_block(
        NARRATIVE_ROW, CLUSTERS, REPRESENTATIVES_BY_CLUSTER)
    assert known_records == {"r1", "r2"}
    assert known_clusters == {"CL-1"}
    assert known_prior_records == set()
    assert "country_focus" in text
    assert "対米姿勢比較" in text


def test_build_observed_evidence_block_reflects_no_prior_continuity():
    text, _, _, _ = synth.build_observed_evidence_block(NARRATIVE_ROW, [], {})
    assert "初回観測" in text


def test_build_observed_evidence_block_renders_prior_continuity():
    prior_synthesis = {
        "created_at": "2026-08-24T00:00:00Z",
        "observed_record_ids": ["r1"],
        "final_draft": {
            "central_assessment": {"summary": "前回はStatement中心だった"},
            "continuity_assessment": {"state": "New", "rationale": "初回観測だった"},
            "watchpoints": [{"watchpoint": "公式声明の変化", "trigger_condition": "具体的な執行措置の発表"}],
        },
    }
    prior_continuity = synth.build_prior_continuity_context(prior_synthesis)
    text, _, _, known_prior_records = synth.build_observed_evidence_block(
        NARRATIVE_ROW, CLUSTERS, REPRESENTATIVES_BY_CLUSTER, prior_continuity=prior_continuity)
    assert "前回はStatement中心だった" in text
    assert "公式声明の変化" in text
    assert known_prior_records == {"r1"}


# ---------------------------------------------------------------------------
# Expert Context（entity-scoped Actor Intelligence + Media Intelligence）
# ---------------------------------------------------------------------------

def test_resolve_actor_entity_ids_by_name():
    client = _base_client()
    resolved = synth.resolve_actor_entity_ids_by_name(client, ["Donald Trump", "Unknown Person"])
    assert resolved == {"Donald Trump": "trump-id"}


def test_build_entity_scoped_actor_intelligence_pack_reads_profile():
    client = _base_client(geo_knowledge_items=[
        {"knowledge_item_id": "prof-1", "knowledge_type": "actor_profile", "status": "active",
         "retrieval_status": "available", "is_demo": False,
         "structured_content": {
             "entity_id": "trump-id",
             "core_profile": {"worldview": [{"statement": "America First", "information_class": "actor_claim",
                                              "confidence": "中"}]},
         }},
    ])
    pack = synth.build_entity_scoped_actor_intelligence_pack(client, {"Donald Trump": "trump-id"})
    assert len(pack["key_actors"]) == 1
    assert pack["key_actors"][0]["canonical_name"] == "Donald Trump"
    assert pack["key_actors"][0]["core_profile"]["worldview"][0]["statement"] == "America First"


def test_build_expert_context_block_empty_when_no_actor_names():
    client = _base_client()
    text, known_refs = synth.build_expert_context_block(client, [])
    assert isinstance(text, str)
    assert isinstance(known_refs, set)


def test_build_expert_context_block_includes_actor_text_when_resolved():
    client = _base_client(geo_knowledge_items=[
        {"knowledge_item_id": "prof-1", "knowledge_type": "actor_profile", "status": "active",
         "retrieval_status": "available", "is_demo": False,
         "structured_content": {"entity_id": "trump-id", "core_profile": {}}},
    ])
    text, known_refs = synth.build_expert_context_block(client, ["Donald Trump"])
    assert "Donald Trump" in text
    assert "trump-id" in known_refs


# ---------------------------------------------------------------------------
# LLM呼び出し（mock）
# ---------------------------------------------------------------------------

def test_run_synthesis_calls_llm_and_returns_draft():
    mock_client = _mock_llm_client(_valid_draft())
    result = synth.run_synthesis(mock_client, "gpt-4o", "observed text", "expert text")
    assert result["data"]["central_assessment"]["summary"]
    assert mock_client.chat.completions.create.called


def test_run_expert_review_calls_llm():
    review_response = {"review_verdict": "approved", "issues_found": [], "revised_draft": None}
    mock_client = _mock_llm_client(review_response)
    result = synth.run_expert_review(mock_client, "gpt-4o", _valid_draft(), "observed text")
    assert result["data"]["review_verdict"] == "approved"


# ---------------------------------------------------------------------------
# Anti-Backflow Guard
# ---------------------------------------------------------------------------

def test_validity_checks_pass_on_well_formed_draft():
    draft = _valid_draft()
    checks = synth.run_synthesis_validity_checks(draft, {"r1", "r2"}, {"CL-1"}, set())
    assert synth.synthesis_validity_status(checks) == "PASS"


def test_observed_expert_separation_violation_detected():
    draft = _valid_draft()
    # evidence_basis=observedなのにexpert_context_refsを持たせる（逆流）
    draft["findings"][0]["provenance"]["expert_context_refs"] = ["trump-id"]
    checks = synth.run_synthesis_validity_checks(draft, {"r1", "r2"}, {"CL-1"}, {"trump-id"})
    assert checks["observed_expert_context_separation"]["passed"] is False
    assert synth.synthesis_validity_status(checks) == "REVIEW REQUIRED"


def test_fabricated_observed_record_id_detected():
    draft = _valid_draft()
    draft["findings"][0]["provenance"]["observed_record_ids"] = ["r1", "r-does-not-exist"]
    checks = synth.run_synthesis_validity_checks(draft, {"r1", "r2"}, {"CL-1"}, set())
    assert checks["observed_ids_not_fabricated"]["passed"] is False


def test_fabricated_expert_context_ref_detected():
    draft = _valid_draft()
    draft["drivers"][0]["provenance"]["evidence_basis"] = "expert_context"
    draft["drivers"][0]["provenance"]["expert_context_refs"] = ["not-a-real-ref"]
    checks = synth.run_synthesis_validity_checks(draft, {"r1", "r2"}, {"CL-1"}, {"trump-id"})
    assert checks["expert_context_refs_not_fabricated"]["passed"] is False


def test_fabricated_counterevidence_record_id_detected():
    draft = _valid_draft()
    draft["findings"][0]["provenance"]["counterevidence_record_ids"] = ["r-does-not-exist"]
    checks = synth.run_synthesis_validity_checks(draft, {"r1", "r2"}, {"CL-1"}, set())
    assert checks["observed_ids_not_fabricated"]["passed"] is False


def test_valid_counterevidence_record_id_passes():
    draft = _valid_draft()
    draft["findings"][0]["provenance"]["counterevidence_record_ids"] = ["r2"]
    checks = synth.run_synthesis_validity_checks(draft, {"r1", "r2"}, {"CL-1"}, set())
    assert synth.synthesis_validity_status(checks) == "PASS"


# ---------------------------------------------------------------------------
# Human Review Gate（P0修正: needs_human_reviewは自動昇格のみブロックする）
# ---------------------------------------------------------------------------

def test_auto_promote_eligible_when_pass_and_approved():
    assert synth.synthesis_auto_promote_eligible("PASS", "approved") is True


def test_auto_promote_eligible_when_pass_and_revised():
    assert synth.synthesis_auto_promote_eligible("PASS", "revised") is True


def test_auto_promote_blocked_when_needs_human_review_even_if_guard_passes():
    # Anti-Backflow Guardが機械的にPASSでも、Expert Reviewがneeds_human_reviewと
    # 判定した場合は自動昇格しない（生成・保存自体は別途継続してよい）。
    assert synth.synthesis_auto_promote_eligible("PASS", "needs_human_review") is False


def test_auto_promote_blocked_when_guard_fails_regardless_of_verdict():
    assert synth.synthesis_auto_promote_eligible("REVIEW REQUIRED", "approved") is False


# ---------------------------------------------------------------------------
# Continuity: assessment_delta / watchpoint_updates
# ---------------------------------------------------------------------------

def test_assessment_delta_and_watchpoint_updates_pass_when_grounded_in_prior_records():
    draft = _valid_draft()
    # prior_record_ids（"p1"）は今回runのobserved側には無いが、Continuity比較の
    # 根拠としては正当な引用（呼び出し元がknown_observed_record_idsへ合流させる想定）
    draft["assessment_delta"] = [{
        **_valid_item(basis="observed", record_ids=["r1"]),
        "item": "verification stance", "change_type": "Intensified",
    }]
    draft["watchpoint_updates"] = [{
        **_valid_item(basis="observed", record_ids=["p1"]),
        "watchpoint": "official statement shift", "status": "Triggered",
    }]
    checks = synth.run_synthesis_validity_checks(draft, {"r1", "r2", "p1"}, {"CL-1"}, set())
    assert synth.synthesis_validity_status(checks) == "PASS"


def test_assessment_delta_fabricated_record_id_detected():
    draft = _valid_draft()
    draft["assessment_delta"] = [{
        **_valid_item(basis="observed", record_ids=["r-does-not-exist"]),
        "item": "verification stance", "change_type": "Intensified",
    }]
    checks = synth.run_synthesis_validity_checks(draft, {"r1", "r2"}, {"CL-1"}, set())
    assert checks["observed_ids_not_fabricated"]["passed"] is False


def test_watchpoint_updates_separation_violation_detected():
    draft = _valid_draft()
    draft["watchpoint_updates"] = [{
        **_valid_item(basis="observed", record_ids=["r1"], expert_refs=["actor-1"]),
        "watchpoint": "official statement shift", "status": "Not_observed",
    }]
    checks = synth.run_synthesis_validity_checks(draft, {"r1", "r2"}, {"CL-1"}, {"actor-1"})
    assert checks["observed_expert_context_separation"]["passed"] is False


def test_build_prior_continuity_context_extracts_expected_fields():
    prior_synthesis = {
        "created_at": "2026-08-24T00:00:00Z",
        "observed_record_ids": ["p1", "p2"],
        "final_draft": {
            "central_assessment": {"summary": "summary text"},
            "continuity_assessment": {"state": "New", "rationale": "rationale text"},
            "watchpoints": [{"watchpoint": "w1", "trigger_condition": "tc1"}],
        },
    }
    ctx = synth.build_prior_continuity_context(prior_synthesis)
    assert ctx["prior_central_assessment"] == "summary text"
    assert ctx["prior_continuity_state"] == "New"
    assert ctx["prior_watchpoints"] == [{"watchpoint": "w1", "trigger_condition": "tc1"}]
    assert ctx["prior_observed_record_ids"] == ["p1", "p2"]


def test_missing_evidence_for_observed_basis_detected():
    draft = _valid_draft()
    draft["implications"][0]["provenance"]["observed_record_ids"] = []
    draft["implications"][0]["provenance"]["observed_cluster_ids"] = []
    checks = synth.run_synthesis_validity_checks(draft, {"r1", "r2"}, {"CL-1"}, set())
    assert checks["provenance_evidence_present"]["passed"] is False


def test_expert_context_basis_requires_expert_refs():
    draft = _valid_draft()
    draft["watchpoints"][0]["provenance"]["evidence_basis"] = "expert_context"
    draft["watchpoints"][0]["provenance"]["expert_context_refs"] = []
    checks = synth.run_synthesis_validity_checks(draft, {"r1", "r2"}, {"CL-1"}, set())
    assert checks["provenance_evidence_present"]["passed"] is False


# ---------------------------------------------------------------------------
# HTML Rendering
# ---------------------------------------------------------------------------

def test_render_final_html_contains_sections_and_no_qa_leakage():
    html = synth.render_final_html(_valid_draft(), NARRATIVE_ROW)
    assert "<h1>" in html
    assert "US pushes mediation" in html
    assert "signals de-escalation" in html
    # QA/パイプライン内部事情（validity_status, cluster_id等）が本文に出ていないこと
    assert "validity_status" not in html
    assert "CL-1" not in html


def test_render_final_html_shows_what_changed_and_watchpoint_status():
    draft = _valid_draft()
    draft["assessment_delta"] = [{
        **_valid_item(record_ids=["r1"]), "item": "verification stance", "change_type": "Intensified",
    }]
    draft["watchpoint_updates"] = [{
        **_valid_item(record_ids=["r1"]), "watchpoint": "official statement shift", "status": "Triggered",
    }]
    html = synth.render_final_html(draft, NARRATIVE_ROW)
    assert "What Changed Since Last Assessment" in html
    assert "Intensified" in html
    assert "Watchpoint Status" in html
    assert "Triggered" in html
