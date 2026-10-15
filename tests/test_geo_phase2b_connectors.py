"""地政学ナレッジ＆インテリジェンスDB Phase2B: 追加Connector（IMF/UN Comtrade/WTO/Manifesto Project）
のテスト。外部APIは呼び出さない（requests.getをモックする）。
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import config_utils  # noqa: E402
import geo_connector_base as base  # noqa: E402
import geo_connector_imf  # noqa: E402
import geo_connector_manifesto  # noqa: E402
import geo_connector_un_comtrade  # noqa: E402
import geo_connector_wto  # noqa: E402
import geo_entity_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _stub_config_with_credential(monkeypatch, credential_key: str, env_var: str):
    """Connectorはconfig_utils.load_config()を関数内でローカルimportして呼ぶため、
    実環境のconfig.jsonの内容に依存しないよう、connector_credentials参照ごとスタブする。"""
    stub_config = {"connector_credentials": {credential_key: {"env_var": env_var}}}
    monkeypatch.setattr(config_utils, "load_config", lambda: stub_config)


# ── Source Registry 3ゲート分離 ─────────────────────────────────────────

def test_check_automation_allowed_three_independent_gates():
    base.check_automation_allowed({"name": "x", "automation_allowed": True,
                                    "connector_enabled": True, "credential_configured": True})

    with pytest.raises(base.ConnectorDisabledError):
        base.check_automation_allowed({"name": "x", "automation_allowed": False,
                                        "connector_enabled": True, "credential_configured": True})
    with pytest.raises(base.ConnectorDisabledError):
        base.check_automation_allowed({"name": "x", "automation_allowed": True,
                                        "connector_enabled": False, "credential_configured": True})
    with pytest.raises(base.ConnectorDisabledError):
        base.check_automation_allowed({"name": "x", "automation_allowed": True,
                                        "connector_enabled": True, "credential_configured": False})


def test_resolve_credential_reads_env_var_not_config(monkeypatch):
    monkeypatch.delenv("GEO_TEST_API_KEY", raising=False)
    config = {"connector_credentials": {"test": {"env_var": "GEO_TEST_API_KEY"}}}
    assert base.resolve_credential(config, "test") is None

    monkeypatch.setenv("GEO_TEST_API_KEY", "secret-value")
    assert base.resolve_credential(config, "test") == "secret-value"
    # config自体には実キー値を一切保持していないこと
    assert "secret-value" not in json.dumps(config)


def test_sync_credential_status_does_not_touch_automation_allowed(monkeypatch):
    client = FakeSupabaseClient(tables={"geo_source_registry": [{
        "source_id": "src-1", "automation_allowed": True, "connector_enabled": True,
        "credential_configured": True,
    }]})
    monkeypatch.delenv("GEO_TEST_API_KEY", raising=False)
    config = {"connector_credentials": {"test": {"env_var": "GEO_TEST_API_KEY"}}}

    configured = base.sync_credential_status(client, source_id="src-1", config=config, credential_key="test")

    assert configured is False
    row = client.tables["geo_source_registry"][0]
    assert row["credential_configured"] is False
    assert row["automation_allowed"] is True  # 一切変更されない


# ── IMF Connector（無認証） ─────────────────────────────────────────────

def _imf_payload(indicator_code: str, country_code: str, values: dict) -> bytes:
    return json.dumps({"values": {indicator_code: {country_code: values}}}).encode("utf-8")


def test_imf_connector_creates_metric_and_dual_evidence_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "RAW_SNAPSHOT_ROOT", tmp_path)
    client = FakeSupabaseClient(tables={"geo_themes": [{"theme_id": "theme-1"}]})

    def fake_get(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        if "NGDP_RPCH" in url:
            resp.content = _imf_payload("NGDP_RPCH", "JPN", {"2024": 1.2})
        else:
            resp.content = _imf_payload("X", "JPN", {})
        resp.json.return_value = json.loads(resp.content)
        return resp

    monkeypatch.setattr(geo_connector_imf.requests, "get", fake_get)
    result = geo_connector_imf.run_imf_ingestion(
        client, theme_id="theme-1", entity_id="entity-jp", country_code="JPN", is_demo=True)

    assert result["status"] in ("completed", "completed_with_warnings")
    assert len(result["claim_ids"]) == 1
    evidence = client.select("geo_claim_evidence", {"select": "*", "claim_id": f"eq.{result['claim_ids'][0]}"})
    targets = {"document_chunk" if e.get("document_chunk_id") else "metric_observation" for e in evidence}
    assert targets == {"document_chunk", "metric_observation"}


# ── UN Comtrade Connector（Credential-required） ───────────────────────

def test_un_comtrade_connector_blocked_without_credential(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "RAW_SNAPSHOT_ROOT", tmp_path)
    monkeypatch.delenv("GEO_UN_COMTRADE_API_KEY", raising=False)
    client = FakeSupabaseClient(tables={"geo_themes": [{"theme_id": "theme-1"}]})

    with pytest.raises(base.ConnectorDisabledError):
        geo_connector_un_comtrade.run_un_comtrade_ingestion(
            client, theme_id="theme-1", reporter_entity_id="entity-jp", is_demo=True)


def test_un_comtrade_connector_runs_with_credential(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "RAW_SNAPSHOT_ROOT", tmp_path)
    monkeypatch.setenv("GEO_UN_COMTRADE_API_KEY", "test-key")
    _stub_config_with_credential(monkeypatch, "un_comtrade", "GEO_UN_COMTRADE_API_KEY")
    client = FakeSupabaseClient(tables={"geo_themes": [{"theme_id": "theme-1"}]})

    def fake_get(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.content = json.dumps({"data": [{"primaryValue": 12345.0}]}).encode("utf-8")
        resp.json.return_value = json.loads(resp.content)
        return resp

    monkeypatch.setattr(geo_connector_un_comtrade.requests, "get", fake_get)
    result = geo_connector_un_comtrade.run_un_comtrade_ingestion(
        client, theme_id="theme-1", reporter_entity_id="entity-jp", partner_entity_id="entity-world", is_demo=True)

    assert result["status"] in ("completed", "completed_with_warnings")
    assert len(result["claim_ids"]) >= 1
    obs = client.select("geo_metric_observations", {"select": "*", "entity_id": "eq.entity-jp"})
    assert any(o.get("counterpart_entity_id") == "entity-world" for o in obs)


# ── WTO Connector（Credential-required） ───────────────────────────────

def test_wto_connector_blocked_without_credential(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "RAW_SNAPSHOT_ROOT", tmp_path)
    monkeypatch.delenv("GEO_WTO_API_KEY", raising=False)
    client = FakeSupabaseClient(tables={"geo_themes": [{"theme_id": "theme-1"}]})

    with pytest.raises(base.ConnectorDisabledError):
        geo_connector_wto.run_wto_ingestion(client, theme_id="theme-1", entity_id="entity-jp", is_demo=True)


def test_wto_connector_runs_with_credential(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "RAW_SNAPSHOT_ROOT", tmp_path)
    monkeypatch.setenv("GEO_WTO_API_KEY", "test-key")
    _stub_config_with_credential(monkeypatch, "wto", "GEO_WTO_API_KEY")
    client = FakeSupabaseClient(tables={"geo_themes": [{"theme_id": "theme-1"}]})

    def fake_get(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.content = json.dumps({"Dataset": [{"Value": 999.0}]}).encode("utf-8")
        resp.json.return_value = json.loads(resp.content)
        return resp

    monkeypatch.setattr(geo_connector_wto.requests, "get", fake_get)
    result = geo_connector_wto.run_wto_ingestion(client, theme_id="theme-1", entity_id="entity-jp", is_demo=True)

    assert result["status"] in ("completed", "completed_with_warnings")
    # config/geo_wto_indicators_seed.yamlの1 indicator分、periods全件(3件)ぶんClaimが作られる
    assert len(result["claim_ids"]) == 3


# ── Manifesto Project Connector（Credential-required、Main Datasetのみ） ─

def test_manifesto_connector_blocked_without_credential(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "RAW_SNAPSHOT_ROOT", tmp_path)
    monkeypatch.delenv("GEO_MANIFESTO_API_KEY", raising=False)
    client = FakeSupabaseClient(tables={"geo_themes": [{"theme_id": "theme-1"}]})

    with pytest.raises(base.ConnectorDisabledError):
        geo_connector_manifesto.run_manifesto_ingestion(client, theme_id="theme-1", is_demo=True)


def test_manifesto_connector_creates_match_candidate_and_metric_when_resolved(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "RAW_SNAPSHOT_ROOT", tmp_path)
    monkeypatch.setenv("GEO_MANIFESTO_API_KEY", "test-key")
    _stub_config_with_credential(monkeypatch, "manifesto_project", "GEO_MANIFESTO_API_KEY")
    client = FakeSupabaseClient(tables={"geo_themes": [{"theme_id": "theme-1"}]})
    party_entity = geo_entity_service.create_entity(
        client, entity_type="political_party", canonical_name="テスト政党", actor_id="t", is_demo=True)

    def fake_get(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.content = json.dumps([
            {"partyname": "テスト政党", "edate": "2024-10-01", "rile": 12.5},
        ]).encode("utf-8")
        resp.json.return_value = json.loads(resp.content)
        return resp

    monkeypatch.setattr(geo_connector_manifesto.requests, "get", fake_get)
    result = geo_connector_manifesto.run_manifesto_ingestion(client, theme_id="theme-1", is_demo=True)

    # config/geo_manifesto_seed.yamlの2カ国(Japan, Taiwan)ぶん、同じ政党行を処理する
    assert len(result["match_candidate_ids"]) == 2
    assert len(result["claim_ids"]) == 2
    obs = client.select("geo_metric_observations", {"select": "*", "entity_id": f"eq.{party_entity['entity_id']}"})
    assert len(obs) == 1  # 同一期間・同一値のため2回目はupsertで重複しない
    assert obs[0]["observed_value"] == 12.5


def test_manifesto_connector_does_not_fetch_full_corpus_text():
    """今回のスコープはMain Datasetのみであり、Corpus全文取得関数を一切持たないことの確認
    （geo_connector_manifesto.pyにtexts_and_annotations的な関数が存在しないことのガード）。"""
    assert not hasattr(geo_connector_manifesto, "fetch_corpus")
    assert not hasattr(geo_connector_manifesto, "fetch_texts_and_annotations")
