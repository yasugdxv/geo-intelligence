"""geo_llm_common.call_llm_structured / _call_llm_structured_once: 例外正規化の回帰テスト。

2026-09-18発見: 以前は手動JSON抽出フォールバック（②）・Schema訂正再試行（③）のAzure呼び出しが
try/exceptで保護されておらず、httpx.ReadTimeout/openai.APITimeoutError等が未処理のまま
呼び出し元（run_expert_request_staged）へ伝播し、Production pathをクラッシュさせていた
（Actor Intelligence実データ投入でCall1の生成量が増え、大型SchemaのAzure呼び出しが180秒の
client timeoutへ近づいたことで初めて実機で露出した）。

このテストは、①②③のどの段で何が起きても、最終的な失敗は必ずGeoLLMError（生のhttpx/openai
例外ではなく）として上がることを保証する。また、「GeoLLMError化＝call_llm_structured内で
自動リトライされる」という誤解を防ぐため、429(RateLimitError)のみが実際にリトライされ、
timeout等はリトライされない（呼び出し元が個別に判断する）ことも確認する。
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import openai
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_llm_common as llm  # noqa: E402

SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
_DUMMY_REQUEST = httpx.Request("POST", "https://example.invalid/chat/completions")


def _resp(content: str, usage=None):
    m = MagicMock(choices=[MagicMock(message=MagicMock(content=content))])
    m.usage = usage
    return m


def _timeout_error() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=_DUMMY_REQUEST)


def _rate_limit_error() -> openai.RateLimitError:
    response = httpx.Response(429, request=_DUMMY_REQUEST)
    return openai.RateLimitError("rate limited", response=response, body=None)


def _valid_json() -> str:
    return '{"x": "ok"}'


def _invalid_json() -> str:
    return '{"x": 123}'  # xはstring必須、int違反でjsonschema.ValidationErrorになる


# ---- A: structured output成功 ----

def test_a_structured_output_success_returns_immediately():
    client = MagicMock()
    client.chat.completions.create.return_value = _resp(_valid_json())

    result = llm.call_llm_structured(client, "model", "sys", "user", SCHEMA, "s", max_attempts=3, strict=True)

    assert result["mode"] == "structured_output"
    assert result["data"] == {"x": "ok"}
    assert client.chat.completions.create.call_count == 1
    assert len(result["attempts"]) == 1
    assert result["attempts"][0]["outcome"] == "success"


# ---- B: structured output失敗（Schema検証エラー） → fallback成功 ----

def test_b_structured_output_schema_invalid_falls_back_to_manual_json_success():
    client = MagicMock()
    client.chat.completions.create.side_effect = [_resp(_invalid_json()), _resp(_valid_json())]

    result = llm.call_llm_structured(client, "model", "sys", "user", SCHEMA, "s", max_attempts=3, strict=True)

    assert result["mode"] == "manual_json"
    assert result["data"] == {"x": "ok"}
    assert client.chat.completions.create.call_count == 2
    assert result["attempts"][0]["outcome"] == "validation_failed"
    assert result["attempts"][0]["exception_type"] == "ValidationError"
    assert result["attempts"][1]["outcome"] == "success"


# ---- C: structured outputがtimeout → fallback成功 ----

def test_c_structured_output_timeout_falls_back_to_manual_json_success():
    client = MagicMock()
    client.chat.completions.create.side_effect = [_timeout_error(), _resp(_valid_json())]

    result = llm.call_llm_structured(client, "model", "sys", "user", SCHEMA, "s", max_attempts=3, strict=True)

    assert result["mode"] == "manual_json"
    assert result["data"] == {"x": "ok"}
    assert client.chat.completions.create.call_count == 2
    assert result["attempts"][0]["outcome"] == "error"
    assert result["attempts"][0]["exception_type"] == "APITimeoutError"
    assert result["attempts"][1]["outcome"] == "success"


# ---- D: structured output失敗 → fallbackもtimeout → raw例外ではなくGeoLLMError ----

def test_d_fallback_timeout_raises_geo_llm_error_not_raw_timeout():
    client = MagicMock()
    client.chat.completions.create.side_effect = [_resp(_invalid_json()), _timeout_error()]

    with pytest.raises(llm.GeoLLMError) as excinfo:
        llm.call_llm_structured(client, "model", "sys", "user", SCHEMA, "s", max_attempts=3, strict=True)

    assert excinfo.value.exception_type == "APITimeoutError"
    assert client.chat.completions.create.call_count == 2  # ③(訂正再試行)は前回の出力が無いため試みない
    assert excinfo.value.attempts[-1]["outcome"] == "error"
    assert excinfo.value.attempts[-1]["exception_type"] == "APITimeoutError"


def test_d_structured_timeout_then_fallback_timeout_raises_geo_llm_error():
    """①structured outputが直接timeout（Schema検証エラーではなく）→②fallbackもtimeout、
    という組み合わせでも同様にGeoLLMErrorへ正規化されることを確認する。"""
    client = MagicMock()
    client.chat.completions.create.side_effect = [_timeout_error(), _timeout_error()]

    with pytest.raises(llm.GeoLLMError) as excinfo:
        llm.call_llm_structured(client, "model", "sys", "user", SCHEMA, "s", max_attempts=3, strict=True)

    assert excinfo.value.exception_type == "APITimeoutError"
    assert client.chat.completions.create.call_count == 2


# ---- E: timeoutのretry回数が設計上限を超えない ----

def test_e_timeout_does_not_trigger_unbounded_retries_at_max_attempts_1():
    """max_attempts=1では①のみを試し、timeoutでも即座にGeoLLMErrorとして確定する
    （フォールバック段へ進まない、Stage1初回呼び出しの実際の設定と同じ）。"""
    client = MagicMock()
    client.chat.completions.create.side_effect = [_timeout_error()] * 5

    with pytest.raises(llm.GeoLLMError):
        llm.call_llm_structured(client, "model", "sys", "user", SCHEMA, "s", max_attempts=1, strict=True)

    assert client.chat.completions.create.call_count == 1


def test_e_timeout_is_not_retried_by_the_rate_limit_backoff_wrapper(monkeypatch):
    """call_llm_structuredの外側ループは429(RateLimitError)のみをバックオフリトライする。
    timeoutで発生したGeoLLMErrorは、このループによって自動的に再試行されない
    （「GeoLLMError化＝自動リトライされる」ではないことの直接確認）。"""
    monkeypatch.setattr(llm.time, "sleep", lambda *_a, **_k: None)
    client = MagicMock()
    client.chat.completions.create.side_effect = [_timeout_error()] * 20

    with pytest.raises(llm.GeoLLMError):
        llm.call_llm_structured(client, "model", "sys", "user", SCHEMA, "s", max_attempts=1, strict=True)

    # 429用のバックオフループ(_RATE_LIMIT_MAX_RETRIES+1回試行)には乗らないため、
    # 呼び出しは1回のみで終わる（20回分のside_effectを使い切らない）。
    assert client.chat.completions.create.call_count == 1


def test_rate_limit_error_is_still_retried_by_the_backoff_wrapper(monkeypatch):
    """既存動作の非破壊確認: 429は引き続きcall_llm_structuredのバックオフで自動リトライされる
    （timeoutとは異なる取り扱いのまま）。"""
    monkeypatch.setattr(llm.time, "sleep", lambda *_a, **_k: None)
    client = MagicMock()
    client.chat.completions.create.side_effect = [_rate_limit_error(), _resp(_valid_json())]

    result = llm.call_llm_structured(client, "model", "sys", "user", SCHEMA, "s", max_attempts=3, strict=True)

    assert result["mode"] == "structured_output"
    assert result["rate_limit_retries"] == 1
    assert client.chat.completions.create.call_count == 2


# ---- F: non-timeoutエラーの既存挙動を壊さない ----

def test_f_schema_correction_retry_still_succeeds_as_before():
    """①②とも失敗（Schema検証エラー）→③訂正再試行が成功、という既存の3段構成が
    今回の変更後も壊れていないことを確認する。"""
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        _resp(_invalid_json()), _resp(_invalid_json()), _resp(_valid_json()),
    ]

    result = llm.call_llm_structured(client, "model", "sys", "user", SCHEMA, "s", max_attempts=3, strict=True)

    assert result["mode"] == "manual_json_retry"
    assert result["data"] == {"x": "ok"}
    assert client.chat.completions.create.call_count == 3


def test_f_all_three_attempts_fail_schema_validation_raises_geo_llm_error():
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        _resp(_invalid_json()), _resp(_invalid_json()), _resp(_invalid_json()),
    ]

    with pytest.raises(llm.GeoLLMError) as excinfo:
        llm.call_llm_structured(client, "model", "sys", "user", SCHEMA, "s", max_attempts=3, strict=True)

    assert excinfo.value.exception_type == "ValidationError"
    assert client.chat.completions.create.call_count == 3


# ---- call-level timeout override ----

def test_timeout_override_is_forwarded_to_the_azure_call():
    client = MagicMock()
    client.chat.completions.create.return_value = _resp(_valid_json())

    llm.call_llm_structured(client, "model", "sys", "user", SCHEMA, "s",
                             max_attempts=1, strict=True, timeout=300.0)

    _, kwargs = client.chat.completions.create.call_args
    assert kwargs["timeout"] == 300.0


def test_no_timeout_override_omits_timeout_kwarg():
    client = MagicMock()
    client.chat.completions.create.return_value = _resp(_valid_json())

    llm.call_llm_structured(client, "model", "sys", "user", SCHEMA, "s", max_attempts=1, strict=True)

    _, kwargs = client.chat.completions.create.call_args
    assert "timeout" not in kwargs
