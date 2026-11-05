"""SSO（OIDC）有効時に、/api/ 配下のすべての要求に、有効なIDトークン（Bearer）を求めるゲート。

画面の静的ファイル（ログイン画面を含む）は、誰でも取得できる（データは含まない）。データを返すAPIを、ここで一括して守る。
公開するのは、正常性チェック（/api/health）と、ログインの前に必要なSSOの設定（sso-config）だけ。
CORSのプリフライト（OPTIONS）は、通す。SSOが無効なとき、またはEasy Authモードのときは、何もしない（Azureの認証が守る）。
純粋なASGIミドルウェア（応答を、バッファしない。NDJSONのストリーミングを壊さないため）。
"""
import anyio
from fastapi import HTTPException
from starlette.responses import JSONResponse

import geo_ios_identity as ident

PUBLIC_PATHS = ("/api/health", "/api/geo/ios/identities/sso-config")


def _must_authenticate(scope) -> bool:
    if scope["type"] != "http" or scope.get("method") == "OPTIONS":
        return False
    path = scope.get("path", "")
    if not path.startswith("/api/") or path in PUBLIC_PATHS:
        return False
    return ident.sso_enabled() and ident.sso_mode() == "oidc"


class SsoGate:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if _must_authenticate(scope):
            auth = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"authorization"), None)
            try:
                await anyio.to_thread.run_sync(ident.validate_bearer, auth)      # 署名鍵の取得が、イベントループを止めないように
            except HTTPException as exc:
                resp = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers={"WWW-Authenticate": "Bearer"})
                return await resp(scope, receive, send)
        await self.app(scope, receive, send)


def install(app) -> None:
    """CORSより内側（先に追加）に入れる。401の応答にも、CORSのヘッダーが付くように。"""
    app.add_middleware(SsoGate)
