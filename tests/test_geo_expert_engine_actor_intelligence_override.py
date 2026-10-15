"""Actor-first化 Step2（2026-09-25、ユーザー指示Option 3）: Expert AI Coreへ追加した
operation-scoped override（actor_intelligence_enabled: bool | None = None）のテスト。

対象: run_expert_request / run_expert_request_staged / run_expert_request_with_mode。
global env flag（GEO_EXPERT_ACTOR_INTELLIGENCE_ENABLED）自体は変更していないため、
override=None（既定）の全既存呼び出し元（Consultation/RefineDesign/Draft生成・編集・
レビュー等）が一切影響を受けないことも確認する。
"""
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_expert_engine as engine  # noqa: E402


# ---- _resolve_actor_intelligence_enabled: override解決の純粋関数としての挙動 ----

def test_override_none_falls_back_to_global_flag_off(monkeypatch):
    monkeypatch.setattr(engine, "actor_intelligence_enabled", lambda: False)
    assert engine._resolve_actor_intelligence_enabled(None) is False


def test_override_none_falls_back_to_global_flag_on(monkeypatch):
    monkeypatch.setattr(engine, "actor_intelligence_enabled", lambda: True)
    assert engine._resolve_actor_intelligence_enabled(None) is True


def test_override_true_wins_even_when_global_flag_off(monkeypatch):
    monkeypatch.setattr(engine, "actor_intelligence_enabled", lambda: False)
    assert engine._resolve_actor_intelligence_enabled(True) is True


def test_override_false_wins_even_when_global_flag_on(monkeypatch):
    monkeypatch.setattr(engine, "actor_intelligence_enabled", lambda: True)
    assert engine._resolve_actor_intelligence_enabled(False) is False


# ---- 3エントリポイントのシグネチャに、後方互換なデフォルトNoneで追加されていること ----

@pytest.mark.parametrize("func_name", [
    "run_expert_request", "run_expert_request_staged", "run_expert_request_with_mode",
])
def test_entry_points_have_actor_intelligence_enabled_override_defaulting_to_none(func_name):
    func = getattr(engine, func_name)
    sig = inspect.signature(func)
    assert "actor_intelligence_enabled" in sig.parameters
    assert sig.parameters["actor_intelligence_enabled"].default is None


# ---- run_expert_request_with_modeが実際にoverrideを下流へ橋渡しすること ----

def test_with_mode_threads_override_to_monolithic_branch(monkeypatch):
    captured = {}
    monkeypatch.setattr(engine, "resolve_execution_mode", lambda execution_mode: "monolithic")
    monkeypatch.setattr(engine, "run_expert_request",
                         lambda *a, **kw: captured.update(kw) or {"ok": True})

    engine.run_expert_request_with_mode(
        client=object(), azure_client=object(), model="gpt-test", mode="case_analysis",
        theme_id=None, query_text="q", actor_intelligence_enabled=True)

    assert captured["actor_intelligence_enabled"] is True


def test_with_mode_threads_override_to_staged_branch(monkeypatch):
    captured = {}
    monkeypatch.setattr(engine, "resolve_execution_mode", lambda execution_mode: "staged")
    monkeypatch.setattr(engine, "run_expert_request_staged",
                         lambda *a, **kw: captured.update(kw) or {"ok": True})

    engine.run_expert_request_with_mode(
        client=object(), azure_client=object(), model="gpt-test", mode="case_analysis",
        theme_id=None, query_text="q", actor_intelligence_enabled=True)

    assert captured["actor_intelligence_enabled"] is True


def test_with_mode_default_override_is_none_for_both_branches(monkeypatch):
    """既存呼び出し元（Consultation/RefineDesign/Draft等）がoverrideを渡さない場合、
    Noneのまま下流へ渡り、geo_expert_engine側のglobal env flagに委ねる既存挙動を維持する。"""
    captured = {}
    monkeypatch.setattr(engine, "resolve_execution_mode", lambda execution_mode: "monolithic")
    monkeypatch.setattr(engine, "run_expert_request",
                         lambda *a, **kw: captured.update(kw) or {"ok": True})

    engine.run_expert_request_with_mode(
        client=object(), azure_client=object(), model="gpt-test", mode="research_design",
        theme_id=None, query_text="q")

    assert captured["actor_intelligence_enabled"] is None


# ---- build_foundation_context_statusのtelemetryがoverride解決後の実効値と整合すること ----

def _status_kwargs(**overrides):
    base = dict(
        mode="case_analysis", profile="deep", source_pack={"text": "", "document_ids": [], "event_ids": []},
        foundation_context_text="", foundation_context_diagnostics=None, case_id=None,
        foundation_pack=None, structural_text="", actor_intelligence_pack=None,
        actor_intelligence_text="", runtime_actor_candidates=None, media_intelligence_pack=None,
        media_intelligence_text="",
    )
    base.update(overrides)
    return base


def test_foundation_context_status_reports_not_configured_when_effective_enabled_is_false(monkeypatch):
    monkeypatch.setattr(engine, "actor_intelligence_enabled", lambda: True)  # global ONでも
    status = engine.build_foundation_context_status(
        **_status_kwargs(actor_intelligence_effective_enabled=False))  # override実効値OFFが優先
    assert status["actor_intelligence"]["status"] == engine._FC_STATUS_NOT_CONFIGURED


def test_foundation_context_status_reports_ready_when_effective_enabled_is_true(monkeypatch):
    monkeypatch.setattr(engine, "actor_intelligence_enabled", lambda: False)  # global OFFでも
    status = engine.build_foundation_context_status(
        **_status_kwargs(actor_intelligence_effective_enabled=True,  # override実効値ONが優先
                          actor_intelligence_text="Actorについての情報"))
    assert status["actor_intelligence"]["status"] == engine._FC_STATUS_READY


def test_foundation_context_status_falls_back_to_global_flag_when_effective_enabled_omitted(monkeypatch):
    """呼び出し元がactor_intelligence_effective_enabledを省略した場合（未更新の呼び出し元が
    将来現れても）、既存のglobal env flag直接参照にフォールバックする後方互換性。"""
    monkeypatch.setattr(engine, "actor_intelligence_enabled", lambda: False)
    status = engine.build_foundation_context_status(**_status_kwargs())
    assert status["actor_intelligence"]["status"] == engine._FC_STATUS_NOT_CONFIGURED
