"""Moderation-error retry/failover policy (noabot).

Aliyun MaaS / DashScope rejects whole requests with
`data_inspection_failed`, and live probes showed the verdict is partly
stochastic (identical slices passed and failed across requests).  These
tests pin the resulting policy: SSE payload parsing keeps structured
metadata, the code is retryable despite transport hints, and it may fail
over — while genuine content_filter/refusal semantics stay unchanged.
"""

from __future__ import annotations

from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.providers.fallback_provider import FallbackProvider

_SSE_BODY = (
    'data: {{"error":{{"code":"data_inspection_failed","param":null,'
    '"message":"Input text data may contain inappropriate content.",'
    '"type":"{type}"}},"id":"chatcmpl-x"}}\n\n'
)


def _moderation_response(**overrides: object) -> LLMResponse:
    base: dict = {
        "content": _SSE_BODY.format(type="data_inspection_failed").replace("\n", " "),
        "finish_reason": "error",
        "error_status_code": 400,
        "error_code": "data_inspection_failed",
        "error_type": "data_inspection_failed",
    }
    base.update(overrides)
    return LLMResponse(**base)  # type: ignore[arg-type]


def test_sse_prefixed_payload_yields_structured_code() -> None:
    payload = _SSE_BODY.format(type="data_inspection_failed")
    error_type, code = LLMProvider._extract_error_type_code(payload)
    assert code == "data_inspection_failed"
    assert error_type == "data_inspection_failed"


def test_plain_json_payload_still_parses() -> None:
    error_type, code = LLMProvider._extract_error_type_code(
        '{"error":{"code":"rate_limit_exceeded","type":"rate_limit_error"}}'
    )
    assert code == "rate_limit_exceeded"
    assert error_type == "rate_limit_error"


def test_moderation_response_is_transient_despite_no_retry_hint() -> None:
    # x-should-retry: false must not veto the retry — the verdict is stochastic.
    assert LLMProvider.is_transient_response(
        _moderation_response(error_should_retry=False)
    ) is True


def test_moderation_content_marker_covers_missing_code() -> None:
    resp = _moderation_response(error_code=None, error_type=None, error_kind="invalid_request")
    assert LLMProvider.is_transient_response(resp) is True


def test_moderation_response_allows_failover() -> None:
    assert FallbackProvider._should_fallback(_moderation_response()) is True


def test_genuine_content_filter_semantics_unchanged() -> None:
    resp = LLMResponse(
        content="No provider error text here.",
        finish_reason="content_filter",
        error_kind="content_filter",
    )
    assert FallbackProvider._should_fallback(resp) is False
    assert LLMProvider.is_transient_response(resp) is False


def test_ordinary_400_invalid_request_not_retried() -> None:
    resp = LLMResponse(
        content='Error: {"error":{"code":"invalid_parameter","type":"invalid_request_error"}}',
        finish_reason="error",
        error_status_code=400,
        error_code="invalid_parameter",
        error_kind="invalid_request",
    )
    assert LLMProvider.is_transient_response(resp) is False
    assert FallbackProvider._should_fallback(resp) is False
