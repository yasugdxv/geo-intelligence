"""テスト共通設定: Expert Review Comment用のPresentation LLM Transformationを既定で無効にする
（既存テストが意図せず実LLM・実キャッシュファイルへ触れないようにするため）。LLMを使うテストは
個別にfakeを注入し、cacheディレクトリはtmp_pathへ向ける。"""
import pytest


@pytest.fixture(autouse=True)
def _disable_expert_review_llm(monkeypatch, tmp_path):
    try:
        import geo_ios_situation_presentation_api as api
    except Exception:  # noqa: BLE001 — API importに失敗する環境でも他のテストを巻き込まない
        api = None
    if api is not None:
        monkeypatch.setattr(api, "EXPERT_REVIEW_LLM_ENABLED", False)
    monkeypatch.setenv("GEO_EXPERT_REVIEW_CACHE_DIR", str(tmp_path / "expert_review_cache"))


@pytest.fixture(autouse=True)
def _disable_report_contract_validation_by_default(monkeypatch):
    """既存の書き出しテストは、Report Type(report_kind)等を持たない簡易なRunでZIPを組み立てる。
    ZIP生成前のReport Contract検証（2026-10-05、geo_report_profile_registry）は、検証専用のテスト
    （test_geo_report_report_contract.py）が個別にTrueへ戻して確認する。"""
    try:
        import geo_report_prompt_export_service as export_svc
    except Exception:  # noqa: BLE001
        return
    monkeypatch.setattr(export_svc, "REPORT_CONTRACT_VALIDATION_ENABLED", False)


@pytest.fixture(autouse=True)
def _isolated_gdelt_state(tmp_path, monkeypatch):
    """GDELT DOCのレート制御・排他はローカルファイル(logs/)に状態を持つ（geo_gdelt_rate_limit）。
    テストが本番用の状態（429クールダウン等）を書き換えたり、逆に影響されたりしないよう、
    各テストで状態ディレクトリを一時ディレクトリへ切り替える。"""
    monkeypatch.setenv("GEO_GDELT_STATE_DIR", str(tmp_path / "gdelt_state"))
