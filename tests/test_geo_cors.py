"""CORS許可先の環境変数設定と、デプロイ用ワークフロー・起動スクリプトのテスト。"""
import json
from pathlib import Path

import pytest
import yaml

import geo_cors

ROOT = Path(__file__).resolve().parent.parent


def test_default_origins_are_local_development_only(monkeypatch):
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    origins = geo_cors.allowed_origins()
    assert "http://localhost:5173" in origins and all(o.startswith("http://127.0.0.1") or o.startswith("http://localhost") for o in origins)


def test_environment_adds_frontend_origin_and_rejects_wildcard_and_invalid():
    env = " https://app-front.azurewebsites.net/ ,*, ftp://x, javascript:alert(1),, https://app-front.azurewebsites.net,http://localhost:5173"
    origins = geo_cors.allowed_origins(env)
    assert origins.count("https://app-front.azurewebsites.net") == 1          # 末尾の/は除き、重複は1つに
    assert "*" not in origins and not any(o.startswith(("ftp", "javascript")) for o in origins)
    assert origins.count("http://localhost:5173") == 1                         # 既定と重複しても1つ


def test_api_server_uses_env_origin_and_private_network(monkeypatch):
    """api_server のCORS設定が、環境変数の許可先と allow_private_network を実際に使っている。"""
    text = (ROOT / "api_server.py").read_text(encoding="utf-8")
    assert "allow_origins=geo_cors.allowed_origins()" in text and "allow_private_network=True" in text


def test_entrypoint_syncs_config_every_start(tmp_path):
    import docker_entrypoint as de
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"old": True}), encoding="utf-8")
    msg = de._sync_config_file({"azure": {"endpoint": "e"}}, path)
    assert json.loads(path.read_text(encoding="utf-8")) == {"azure": {"endpoint": "e"}} and "上書き" in msg      # 古い設定を残さない
    path.write_text(json.dumps({"manual": True}), encoding="utf-8")
    msg = de._sync_config_file({}, path)                                                                       # 環境変数が無いローカルでは、手動の設定を使う
    assert json.loads(path.read_text(encoding="utf-8")) == {"manual": True}


@pytest.mark.parametrize("name", ["deploy-azure.yml", "deploy-azure-frontend.yml"])
def test_workflows_are_valid_manual_only_and_use_publish_profile_secrets(name):
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))
    triggers = wf.get("on") or wf.get(True)                                  # PyYAMLは on を True として読む
    assert list(triggers) == ["workflow_dispatch"]                           # pushでは動かない（意図しない本番反映の防止）
    text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
    assert "secrets.azure_" in text and "SCM_DO_BUILD_DURING_DEPLOYMENT:" not in text      # Azure側ビルドを有効にする設定を入れていない
    assert "azure/login" not in text                                         # サステナアプリと同じ、発行プロファイル方式
    deploy = wf["jobs"]["deploy"]
    assert deploy["needs"] == "build"


def test_backend_workflow_installs_dependencies_in_the_job_not_on_azure():
    text = (ROOT / ".github" / "workflows" / "deploy-azure.yml").read_text(encoding="utf-8")
    assert "python -m venv antenv" in text and "pip install -r requirements.txt" in text and "python-version: \"3.11\"" in text
    assert "--no-frontend" in text
