"""
OpenAI / Azure OpenAI クライアント生成（共通）

以前は run.py（~5000行のダッシュボード本体）に定義されており、
make_openai_client を使うためだけの軽量スクリプト（article_analyzer.py、
sustainability_article_selector.py 等）まで run.py 全体をimportして
起動コストが増えていた。依存を最小限（httpx, openai, config_utils）に
した本モジュールへ切り出す。
"""
import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from config_utils import find_azure_cfg


# 2026-09-13実機確認: プロキシ経由のhttpx.Clientにtimeout未指定だと、応答が返らない
# まま接続がEstablished状態で残り続けるハングが発生しうる(3時間以上CPU使用率ほぼ0のまま
# 停止しない事例を確認、geo_llm_common側の429リトライ以前にリクエスト自体が返ってこない
# ケースには効かない)。read/write/poolを180秒、connectを30秒に制限し、ハング時は例外を
# 発生させて既存のリトライ処理(geo_llm_common.call_llm_structured等)に委ねる。
_HTTP_CLIENT_TIMEOUT = httpx.Timeout(180.0, connect=30.0)


def _is_private_host(host: str) -> bool:
    """ホスト名が社内(プライベート)IPに解決されるか。Private Endpoint はプロキシを通すと拒否される。"""
    try:
        return ipaddress.ip_address(socket.gethostbyname(host)).is_private
    except Exception:
        return False


def _normalize_azure_endpoint(endpoint: str) -> str:
    """Foundry画面のURL(/openai/v1/ 付き)が貼られても、ホスト名までに丸める。"""
    u = urlparse(endpoint)
    return f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else endpoint


def make_openai_client(config: dict):
    """Azure OpenAI を優先し、なければ OpenAI を使う。(client, model) を返す"""
    proxy_cfg = config.get("proxy", {})
    ssl_verify = config.get("ssl", {}).get("verify", True)
    http_client = None
    if proxy_cfg.get("enabled") and proxy_cfg.get("http_proxy"):
        http_client = httpx.Client(proxy=proxy_cfg["http_proxy"], verify=ssl_verify, timeout=_HTTP_CLIENT_TIMEOUT)

    # ── Azure OpenAI（優先） ──
    az = find_azure_cfg(config)
    az_endpoint = (az.get("endpoint") or "").strip()
    az_key      = (az.get("api_key")  or "").strip()
    if az_endpoint and az_key:
        from openai import AzureOpenAI
        az_endpoint = _normalize_azure_endpoint(az_endpoint)
        # 社内IP(Private Endpoint)宛てはプロキシを通さず直接つなぐ
        if _is_private_host(urlparse(az_endpoint).netloc):
            http_client = httpx.Client(trust_env=False, verify=ssl_verify, timeout=_HTTP_CLIENT_TIMEOUT)
        dep = (az.get("deployment") or az.get("default_deployment") or "gpt-4o").strip()
        kwargs: dict = {
            "azure_endpoint": az_endpoint,
            "api_key":        az_key,
            "api_version":    (az.get("api_version") or "2024-05-01-preview").strip(),
        }
        if http_client:
            kwargs["http_client"] = http_client
        return AzureOpenAI(**kwargs), dep

    # ── OpenAI フォールバック ──
    key = config.get("api_keys", {}).get("openai", "")
    if key:
        from openai import OpenAI
        kwargs = {"api_key": key}
        if http_client:
            kwargs["http_client"] = http_client
        return OpenAI(**kwargs), "gpt-4o"

    return None, None
