"""画面の静的配信（Azure App Service用）のテスト。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import geo_frontend_static as fs


@pytest.fixture
def dist(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / fs.SHELL_NAME).write_text("<html>SHELL</html>", encoding="utf-8")
    (d / "assets" / "app-abc.js").write_text("console.log(1)", encoding="utf-8")
    (d / "favicon.ico").write_bytes(b"ico")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")        # 配信元の外にあるファイル
    return d


def make_app(dist):
    app = FastAPI()

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    @app.get("/docs-like")
    def docs_like():
        return {"early": True}

    assert fs.mount_frontend(app, dist) is True        # ルーターの登録の後に呼ぶ
    return TestClient(app)


def test_root_and_spa_deep_links_return_the_shell(dist):
    c = make_app(dist)
    for path in ("/", "/geo/intelligence-ai", "/geo/external-network/contacts/abc"):
        r = c.get(path)
        assert r.status_code == 200 and "SHELL" in r.text and r.headers["content-type"].startswith("text/html"), path
        assert r.headers["cache-control"] == "no-cache"


def test_assets_served_with_long_cache_and_missing_files_are_404_not_html(dist):
    c = make_app(dist)
    r = c.get("/assets/app-abc.js")
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"] and r.headers["x-content-type-options"] == "nosniff"
    assert c.get("/favicon.ico").status_code == 200
    assert c.get("/assets/missing.js").status_code == 404          # 無い .js の代わりにHTMLを返すと、画面が壊れて原因が分かりにくい
    assert c.get("/missing.png").status_code == 404


def test_api_routes_are_never_shadowed_and_unknown_api_is_404(dist):
    c = make_app(dist)
    assert c.get("/api/ping").json() == {"ok": True}
    assert c.get("/docs-like").json() == {"early": True}            # 先に登録済みのルートが優先される
    r = c.get("/api/does-not-exist")
    assert r.status_code == 404 and "SHELL" not in r.text
    assert c.get("/api").status_code == 404


def test_cannot_escape_the_dist_directory(dist):
    c = make_app(dist)
    for evil in ("/..%2Fsecret.txt", "/%2e%2e/secret.txt", "/assets/..%2F..%2Fsecret.txt"):
        r = c.get(evil)
        assert "secret" not in r.text, evil


def test_no_dist_means_api_only(tmp_path, monkeypatch):
    monkeypatch.delenv("GEO_FRONTEND_DIST", raising=False)
    app = FastAPI()
    assert fs.mount_frontend(app, tmp_path / "nothing") is False or fs.find_dist() is not None
    # 配信元が見つからないときは、ルートを登録しない（ローカル開発のAPI専用の動作）
    empty = FastAPI()
    monkeypatch.setattr(fs, "find_dist", lambda explicit=None: None)
    assert fs.mount_frontend(empty) is False and [r.path for r in empty.routes if r.path == "/{path:path}"] == []


def test_dist_is_found_via_environment_variable(dist, monkeypatch):
    monkeypatch.setenv("GEO_FRONTEND_DIST", str(dist))
    assert fs.find_dist() == dist.resolve()
