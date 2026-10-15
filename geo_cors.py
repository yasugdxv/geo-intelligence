"""CORS（別のサイトから API を呼べる許可）の設定。

本番では、画面（フロント）とAPI（バック）が別々の Web App（別のアドレス）になるため、バックは
「フロントのアドレスからの呼び出し」を明示的に許可する必要がある（サステナアプリと同じ構成）。

  - 既定: ローカル開発の許可先（Viteの 5173 / 8080〜8083）のみ。
  - 環境変数 CORS_ALLOWED_ORIGINS（カンマ区切り）に、フロントのアドレスを指定すると、それも許可する。
        例: https://app-jp1-sjp-bn-geo-frontend-001.azurewebsites.net
  - "*"（全て許可）は受け付けない（認証つきの通信を、どのサイトからでも許してしまうため）。
"""
from __future__ import annotations

import os

_DEV_ORIGINS = [
    "http://127.0.0.1:5173", "http://localhost:5173",
    "http://127.0.0.1:8080", "http://localhost:8080",
    "http://127.0.0.1:8081", "http://localhost:8081",
    "http://127.0.0.1:8082", "http://localhost:8082",
    "http://127.0.0.1:8083", "http://localhost:8083",
]


def allowed_origins(env_value: str | None = None) -> list[str]:
    raw = os.environ.get("CORS_ALLOWED_ORIGINS", "") if env_value is None else env_value
    extra = []
    for item in raw.split(","):
        origin = item.strip().rstrip("/")
        if origin and origin != "*" and origin.startswith(("http://", "https://")) and origin not in _DEV_ORIGINS + extra:
            extra.append(origin)
    return _DEV_ORIGINS + extra
